"""
Weaviate document tools for OpenAI Agents SDK.

This module provides tools for:
- Hybrid search (semantic + keyword)
- Section listing (show available sections)
- Section reading (get full section content)
"""

import hashlib
import json
import logging
import os
from typing import Optional, List, TYPE_CHECKING, Any, Literal

from pydantic import BaseModel
from agents import function_tool
from agr_ai_curation_runtime.evidence_spans import (
    EVIDENCE_SPANIZER_VERSION,
    build_evidence_spans,
)
from agr_ai_curation_runtime.chunk_identity import resolve_chunk_identifier
from agr_ai_curation_runtime.tool_result_bounds import (
    TOOL_RESULT_BUDGET_UNMET,
    ToolResultBudgetError,
    clamp_page_limit,
    env_positive_int,
    fit_page,
    parse_offset,
    serialized_size,
    tool_result_max_bytes,
)
from agr_ai_curation_runtime.weaviate_chunks import (
    hybrid_search_chunks,
    get_chunk_by_id,
    get_chunk_neighbor_ids,
    get_chunks_by_parent_section,  # Uses LLM-resolved parentSection for accurate boundaries
    get_chunks_by_subsection,
)

if TYPE_CHECKING:
    from ..guardrails import ToolCallTracker

logger = logging.getLogger(__name__)

SearchMode = Literal["auto", "hybrid", "lexical", "hybrid_lexical_first"]


def _env_int(key: str, default: int, *, minimum: int = 0) -> int:
    """Read an int env var with a resilient fallback.

    This module runs inside the isolated package subprocess, which inherits the
    backend process environment (package_runner.py uses subprocess.run without an
    env= override). It cannot import backend config, so it reads os.environ
    directly using the SAME env var names the backend honors.
    """
    raw = os.getenv(key)
    if raw is None:
        return default
    try:
        return max(minimum, int(raw))
    except ValueError:
        logger.warning("Invalid int value for %s: %s, using default %s", key, raw, default)
        return default


def _env_bool(key: str, default: bool) -> bool:
    """Read the backend-shared boolean setting in the package subprocess."""

    raw = os.getenv(key)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    logger.warning("Invalid boolean value for %s: %s, using default %s", key, raw, default)
    return default


def _env_unit_float(key: str, default: float) -> float:
    """Read a finite float in [0, 1] in the package subprocess."""

    raw = os.getenv(key)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        logger.warning("Invalid float value for %s: %s, using default %s", key, raw, default)
        return default
    if value != value or not 0.0 <= value <= 1.0:
        logger.warning("%s=%s is outside [0, 1]; using default %s", key, value, default)
        return default
    return value


# Default cap on how many section/subsection chunks one read returns. Section reads
# previously returned every chunk unbounded (~50 chunks, the full section text twice),
# which could reach hundreds of thousands of characters in a single tool result. The
# default is surpassable via max_chunks, and the result reports total_chunk_count plus
# next_offset so the model can page through the rest.
# Env-configurable via SECTION_READ_MAX_CHUNKS (default 30).
_DEFAULT_SECTION_MAX_CHUNKS = _env_int("SECTION_READ_MAX_CHUNKS", 30, minimum=1)


def _section_read_page_max_chunks() -> int:
    """Largest section/subsection page (SECTION_READ_PAGE_MAX_CHUNKS, default 100).

    Same variable and default as the backend getter
    ``get_section_read_page_max_chunks``. Larger requests clamp and report the
    requested and effective values; the TOOL_RESULT_MAX_BYTES budget can end a
    page earlier.
    """
    return env_positive_int("SECTION_READ_PAGE_MAX_CHUNKS", 100)

# How much surrounding text to return around a text_contains match. Bounded so a
# matched passage gives the model enough context to decide whether to read the full
# chunk without pulling the entire section back.
# Env-configurable via SECTION_SNIPPET_RADIUS_CHARS (default 200).
_SECTION_SNIPPET_RADIUS = _env_int("SECTION_SNIPPET_RADIUS_CHARS", 200, minimum=0)

# These package-owned tools execute in an isolated subprocess and cannot import
# backend config. Read deployment settings directly from the inherited environment.
_SEARCH_INITIAL_LIMIT = min(
    100,
    _env_int("WEAVIATE_SEARCH_INITIAL_LIMIT", 50, minimum=1),
)
_SEARCH_HYBRID_ALPHA = _env_unit_float("WEAVIATE_SEARCH_HYBRID_ALPHA", 0.4)
_SEARCH_MMR_ENABLED = _env_bool("WEAVIATE_SEARCH_MMR_ENABLED", False)
_SEARCH_MMR_LAMBDA = _env_unit_float("WEAVIATE_SEARCH_MMR_LAMBDA", 0.5)

_SEARCH_MODE_TO_STRATEGY: dict[str, str] = {
    "auto": "hybrid",
    "hybrid": "hybrid",
    "lexical": "lexical",
    "hybrid_lexical_first": "hybrid_lexical_first",
}


def _strategy_for_search_mode(search_mode: str) -> str:
    try:
        return _SEARCH_MODE_TO_STRATEGY[search_mode]
    except KeyError as exc:
        allowed = ", ".join(_SEARCH_MODE_TO_STRATEGY)
        raise ValueError(
            f"Unsupported search_mode '{search_mode}'. Allowed values: {allowed}."
        ) from exc


class ChunkHit(BaseModel):
    chunk_id: Optional[str]
    section_title: Optional[str]
    page_number: Optional[int]
    score: Optional[float]
    content: str
    doc_items: Optional[List[dict]] = None  # Bounding box data for PDF highlighting
    # Set when the hit's text did not fit the result budget; read it with read_chunk.
    content_withheld: Optional[bool] = None
    content_chars: Optional[int] = None


class ChunkSearchResult(BaseModel):
    summary: str
    hits: List[ChunkHit]
    error_code: Optional[str] = None
    result_bounds: Optional[dict] = None


class EvidenceSpanResult(BaseModel):
    span_id: str
    span_index: int
    span_type: str
    text: str
    char_start: int
    char_end: int
    page_number: Optional[int] = None
    section_title: Optional[str] = None
    spanizer_version: str = EVIDENCE_SPANIZER_VERSION


class ChunkReadContent(BaseModel):
    chunk_id: str
    chunk_index: Optional[int] = None
    chunk_number: Optional[int] = None
    previous_chunk_id: Optional[str] = None
    next_chunk_id: Optional[str] = None
    page_number: Optional[int] = None
    section_title: Optional[str] = None
    subsection: Optional[str] = None
    content: str
    evidence_spans: List[EvidenceSpanResult]
    doc_items: Optional[List[dict]] = None
    # Present only when the chunk is too large for one result: ``content`` is
    # then the exact slice covering the returned spans, and next_span_offset
    # continues the chunk.
    content_range: Optional[dict] = None
    span_page: Optional[dict] = None


class ChunkReadResult(BaseModel):
    summary: str
    chunk: Optional[ChunkReadContent]
    error_code: Optional[str] = None
    result_bounds: Optional[dict] = None


def _coerce_chunk_index(value: Any) -> Optional[int]:
    if value is None:
        return None
    return int(value)


def _read_chunk_metadata(chunk_id: str, raw_metadata: Any) -> dict:
    if raw_metadata is None:
        return {}
    if isinstance(raw_metadata, str):
        try:
            parsed = json.loads(raw_metadata)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Chunk '{chunk_id}' has malformed JSON metadata") from exc
        if not isinstance(parsed, dict):
            raise TypeError(f"Chunk '{chunk_id}' metadata JSON must decode to an object")
        return parsed
    if not isinstance(raw_metadata, dict):
        raise TypeError(f"Chunk '{chunk_id}' metadata must be an object or JSON object string")
    return raw_metadata


def _read_actual_chunk_id(requested_chunk_id: str, chunk: dict) -> str:
    raw_chunk_id = chunk.get("id")
    actual_chunk_id = str(raw_chunk_id or "").strip()
    if not actual_chunk_id:
        raise ValueError(
            f"Chunk lookup for '{requested_chunk_id}' returned no concrete backend chunk id"
        )
    return actual_chunk_id


def create_search_tool(document_id: str, user_id: str, tracker: Optional["ToolCallTracker"] = None):
    """
    Create a search tool bound to a specific document and user.

    Args:
        document_id: UUID of the document to search
        user_id: User ID for tenant isolation
        tracker: Optional ToolCallTracker to record when this tool is called

    Returns a function_tool that emits structured ChunkSearchResult objects.
    """

    @function_tool
    async def search_document(
        query: str,
        limit: int = 5,
        section_keywords: Optional[List[str]] = None,
        search_mode: SearchMode = "auto",
    ) -> ChunkSearchResult:
        """Discovery tool: search the loaded PDF for relevant chunks.

        Use returned chunk_id values with read_chunk for final evidence selection.
        Do not use search snippets as retained evidence.
        The default search_mode='auto' runs hybrid search (semantic similarity plus
        BM25 keyword matching), so it bridges paraphrases like "expressed in" vs
        "detected in" that pure keyword search would miss. Use search_mode='lexical'
        for exact gene symbols, IDs, strains, alleles, probes, reagents, genotype
        handles, and PMIDs/DOIs; use search_mode='hybrid_lexical_first' when broad
        hybrid search should retry with lexical-heavy matching. Results are reranked
        by a cross-encoder. MMR diversification is an optional operator-controlled
        stage and is disabled by default. Short queries (<=3 tokens) auto-boost
        lexical matching to avoid semantic drift. Pass
        section_keywords to scope the search to named sections (e.g. Results or
        figure legends) before retrieval runs.

        Hits are returned in rank order with their full text while the response
        fits its size budget; any hit whose text does not fit is still listed, with
        content_withheld and content_chars, so read it with read_chunk.

        Args:
            query: Search terms or natural-language retrieval query.
            limit: Maximum number of chunks to return, capped at 10.
            section_keywords: Optional section filters such as Methods or Results.
            search_mode: Retrieval mode: auto, hybrid, lexical, or hybrid_lexical_first.
        """
        # Record tool call if tracker is provided
        if tracker:
            tracker.record_call("search_document")

        limit = min(max(1, limit), 10)
        query_fingerprint = hashlib.sha256(query.encode("utf-8")).hexdigest()[:16]

        logger.info(
            "Searching document %s... query_fingerprint=%s, limit=%s, sections=%s, mode=%s",
            document_id[:8],
            query_fingerprint,
            limit,
            section_keywords,
            search_mode,
        )

        try:
            # Exact biomedical tokens need explicit lexical-heavy retrieval modes;
            # reranking/MMR must still run on full chunk content, not previews.
            strategy = _strategy_for_search_mode(search_mode)
            chunks = await hybrid_search_chunks(
                document_id=document_id,
                query=query,
                user_id=user_id,
                limit=limit,
                initial_limit=_SEARCH_INITIAL_LIMIT,
                alpha=_SEARCH_HYBRID_ALPHA,
                section_keywords=section_keywords,
                apply_mmr=_SEARCH_MMR_ENABLED,
                mmr_lambda=_SEARCH_MMR_LAMBDA,
                strategy=strategy,
            )

            if not chunks:
                logger.info("No chunks found for query_fingerprint=%s", query_fingerprint)
                return ChunkSearchResult(summary="No relevant content found.", hits=[])

            hits: List[ChunkHit] = []
            for chunk in chunks:
                metadata = _best_effort_metadata(chunk)
                section = metadata.get("section_title") or metadata.get("sectionTitle") or "Unknown Section"
                page = metadata.get("page_number") or metadata.get("pageNumber")
                score = chunk.get("score", 0.0)
                content = chunk.get("text") or chunk.get("content") or ""

                # Get doc_items for PDF highlighting (contains bounding boxes)
                doc_items = metadata.get("doc_items") or chunk.get("doc_items") or []

                hits.append(
                    ChunkHit(
                        chunk_id=resolve_chunk_identifier(chunk, metadata),
                        section_title=section,
                        page_number=page,
                        score=score,
                        content=content,
                        doc_items=doc_items if doc_items else None,
                    )
                )

            logger.debug("Returning %s structured chunks", len(hits))
            return _bounded_search_result(hits)

        except Exception as e:
            logger.error("Search error: %s", e, exc_info=True)
            return ChunkSearchResult(summary=f"Error searching document: {str(e)}", hits=[])

    return search_document



def _search_summary(hits: List[ChunkHit]) -> str:
    withheld = [hit.chunk_id or "" for hit in hits if hit.content_withheld]
    summary = f"Found {len(hits)} chunks"
    if withheld:
        summary += (
            f"; {len(withheld)} too long to include in full here "
            f"(content_withheld): {', '.join(withheld)}. Read them with read_chunk"
        )
    return summary


def _bounded_search_result(hits: List[ChunkHit]) -> ChunkSearchResult:
    """Keep every hit, with full text in rank order while the budget allows.

    Hits whose text does not fit stay listed as pointers (content withheld,
    char count kept), so no ranked result disappears from the model's view.
    """
    budget = tool_result_max_bytes()
    shown = [
        hit.model_copy(
            update={"content": "", "content_withheld": True, "content_chars": len(hit.content)}
        )
        for hit in hits
    ]

    def result() -> ChunkSearchResult:
        return ChunkSearchResult(summary=_search_summary(shown), hits=shown)

    measured = serialized_size(result())
    if measured > budget:
        return ChunkSearchResult(
            summary="The search results could not fit the tool result budget.",
            hits=[],
            error_code=TOOL_RESULT_BUDGET_UNMET,
            result_bounds={
                "measured_bytes": measured,
                "limit_bytes": budget,
                "setting": "TOOL_RESULT_MAX_BYTES",
            },
        )
    for index, hit in enumerate(hits):
        pointer = shown[index]
        shown[index] = hit
        if serialized_size(result()) > budget:
            shown[index] = pointer
    return result()


def _chunk_window(
    *,
    content: str,
    spans: List[EvidenceSpanResult],
    span_offset: int,
    render: Any,
    budget: int,
) -> Any:
    """Exact slice of an oversized chunk covering whole evidence spans.

    ``render(content_slice, content_range, span_page_spans, span_page)`` builds
    the complete result. Spans are added in order from ``span_offset`` while the
    result fits; the slice starts where the previous window ended so that every
    character of the chunk appears in exactly one window.
    """
    total = len(spans)
    if total == 0:
        # Windows are span-aligned; text without spans cannot be windowed.
        raise ToolResultBudgetError(measured=len(content), limit=budget)
    start_char = 0 if span_offset == 0 else spans[span_offset].char_start

    def build(page: List[EvidenceSpanResult], returned: int) -> Any:
        next_offset = span_offset + returned
        more = next_offset < total
        end_char = spans[next_offset].char_start if more else len(content)
        if returned == 0:
            end_char = start_char
        return render(
            content[start_char:end_char],
            {"start": start_char, "end": end_char, "total_chars": len(content)},
            page,
            {
                "span_offset": span_offset,
                "returned_spans": returned,
                "total_spans": total,
                "next_span_offset": next_offset if more else None,
                "complete": not more,
            },
        )

    result, returned = fit_page(
        spans,
        start=span_offset,
        limit=total - span_offset,
        render=build,
        budget=budget,
    )
    if returned == 0 and span_offset < total:
        raise ToolResultBudgetError(measured=serialized_size(result), limit=budget)
    return result


def create_read_chunk_tool(document_id: str, user_id: str, tracker: Optional["ToolCallTracker"] = None):
    """
    Create a read_chunk tool bound to a specific document and user.

    Returns a function_tool that retrieves raw chunk content plus deterministic
    exact-text evidence spans for extraction evidence selection.
    """

    @function_tool
    async def read_chunk(chunk_id: str, span_offset: int = 0) -> ChunkReadResult:
        """Read one PDF chunk and return its full text plus selectable evidence_spans.

        This is the evidence-selection step: it returns the complete chunk text and
        deterministic evidence_spans, each carrying a span_id. For retained evidence,
        choose evidence_spans[].span_id values and pass them to
        record_evidence(span_ids=[...]); the backend copies the exact source text into
        verified_quote. Do not write evidence quote text yourself.

        A chunk too large for one response (for example a very long table) is
        returned in consecutive exact windows: chunk.content_range gives the
        character range shown, and chunk.span_page.next_span_offset continues with
        the following spans until span_page.complete is true.

        Args:
            chunk_id: Chunk identifier returned by search_document or section source chunks.
            span_offset: Only for a chunk returned in windows: the next_span_offset from the previous window.
        """
        if tracker:
            tracker.record_call("read_chunk")

        logger.info(
            "Reading chunk '%s' from document %s...",
            chunk_id,
            document_id[:8],
        )

        chunk = await get_chunk_by_id(
            chunk_id=chunk_id,
            user_id=user_id,
            document_id=document_id,
        )
        if not chunk:
            return ChunkReadResult(
                summary=f"No chunk found for chunk_id '{chunk_id}'.",
                chunk=None,
            )

        content = chunk.get("text")
        if not isinstance(content, str):
            raise ValueError(f"Chunk '{chunk_id}' is missing exact raw text content")

        metadata = _read_chunk_metadata(chunk_id, chunk.get("metadata"))

        actual_chunk_id = _read_actual_chunk_id(chunk_id, chunk)
        chunk_index = _coerce_chunk_index(chunk.get("chunk_index"))
        if chunk_index is None:
            chunk_index = _coerce_chunk_index(metadata.get("chunk_index"))
        page_number = chunk.get("page_number") or metadata.get("page_number")
        section_title = (
            chunk.get("section_title")
            or metadata.get("section_title")
            or metadata.get("sectionTitle")
        )

        neighbor_ids = await get_chunk_neighbor_ids(
            document_id=document_id,
            user_id=user_id,
            chunk_index=chunk_index,
        )
        spans = [
            EvidenceSpanResult(**span.to_dict())
            for span in build_evidence_spans(
                chunk_id=actual_chunk_id,
                chunk_text=content,
                page_number=page_number,
                section_title=section_title,
            )
        ]

        page_text = f" from page {page_number}" if page_number else ""

        def render(
            window: str,
            content_range: Optional[dict],
            window_spans: List[EvidenceSpanResult],
            span_page: Optional[dict],
        ) -> ChunkReadResult:
            window_note = ""
            if span_page is not None:
                window_note = (
                    f" This chunk is shown in windows: characters {content_range['start']}"
                    f"-{content_range['end']} of {content_range['total_chars']}."
                )
                if span_page["next_span_offset"] is not None:
                    window_note += (
                        " Continue with read_chunk(chunk_id, "
                        f"span_offset={span_page['next_span_offset']})."
                    )
            return ChunkReadResult(
                summary=(
                    f"Read chunk '{actual_chunk_id}'{page_text}.{window_note} "
                    "Select evidence_spans[].span_id for record_evidence."
                ),
                chunk=ChunkReadContent(
                    chunk_id=actual_chunk_id,
                    chunk_index=chunk_index,
                    chunk_number=chunk_index + 1 if chunk_index is not None else None,
                    previous_chunk_id=neighbor_ids.get("previous_chunk_id"),
                    next_chunk_id=neighbor_ids.get("next_chunk_id"),
                    page_number=page_number,
                    section_title=section_title,
                    subsection=chunk.get("subsection") or metadata.get("subsection"),
                    content=window,
                    evidence_spans=window_spans,
                    doc_items=chunk.get("doc_items") or metadata.get("doc_items") or None,
                    content_range=content_range,
                    span_page=span_page,
                ),
            )

        budget = tool_result_max_bytes()
        if span_offset in (0, None):
            whole = render(content, None, spans, None)
            if serialized_size(whole) <= budget:
                return whole
        try:
            window_start = parse_offset(span_offset, total=max(0, len(spans) - 1), name="span_offset")
            return _chunk_window(
                content=content,
                spans=spans,
                span_offset=window_start,
                render=render,
                budget=budget,
            )
        except ValueError as exc:
            return ChunkReadResult(
                summary=f"Invalid read_chunk request: {exc}",
                chunk=None,
                error_code="invalid_result_cursor",
            )
        except ToolResultBudgetError as exc:
            return ChunkReadResult(
                summary="The chunk could not fit the tool result budget, even one span at a time.",
                chunk=None,
                error_code=TOOL_RESULT_BUDGET_UNMET,
                result_bounds=_budget_unmet_bounds(exc),
            )

    return read_chunk


class SectionChunkSource(BaseModel):
    # Lightweight per-chunk locator only. The full chunk text already lives in the
    # assembled section ``content`` (or ``snippet`` when text_contains is set), so it
    # must NOT be repeated here; duplicating it doubled the payload of every section
    # read. To read a single passage's raw text and its selectable evidence spans,
    # call read_chunk with this chunk_id.
    chunk_id: str
    chunk_index: Optional[int] = None
    page_number: Optional[int] = None
    section_title: Optional[str] = None
    subsection: Optional[str] = None
    char_count: int
    snippet: Optional[str] = None  # Only populated when text_contains matched this chunk
    # Set when this passage alone exceeds the result budget; its text is not in
    # ``content``. Read it with read_chunk.
    content_withheld: Optional[bool] = None


class SectionContent(BaseModel):
    section_title: str
    page_numbers: List[int]
    content: str
    chunk_count: int
    returned_chunk_count: int
    total_chunk_count: int
    offset: int
    next_offset: Optional[int] = None
    truncated: bool
    source_chunks: Optional[List[SectionChunkSource]] = None
    doc_items: Optional[List[dict]] = None  # Combined bounding boxes from all chunks
    page_ended_by: Optional[str] = None
    requested_max_chunks: Optional[int] = None
    effective_max_chunks: Optional[int] = None
    max_chunks_clamped: Optional[bool] = None
    budget_bytes: Optional[int] = None


class SectionReadResult(BaseModel):
    summary: str
    section: Optional[SectionContent]
    error_code: Optional[str] = None
    result_bounds: Optional[dict] = None


def _best_effort_metadata(chunk: dict) -> dict:
    metadata = chunk.get("metadata", {}) or {}
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except (json.JSONDecodeError, TypeError):
            return {}
    if not isinstance(metadata, dict):
        return {}
    return metadata


def _chunk_text(chunk: dict) -> str:
    return chunk.get("text") or chunk.get("content") or ""


def _chunk_page(chunk: dict, metadata: dict) -> Optional[int]:
    return (
        chunk.get("page_number")
        or chunk.get("pageNumber")
        or metadata.get("page_number")
        or metadata.get("pageNumber")
    )


def _chunk_section_title(chunk: dict, metadata: dict, fallback: Optional[str]) -> Optional[str]:
    return (
        chunk.get("section_title")
        or chunk.get("sectionTitle")
        or metadata.get("section_title")
        or metadata.get("sectionTitle")
        or fallback
    )


def _chunk_subsection(chunk: dict, metadata: dict, fallback: Optional[str]) -> Optional[str]:
    return (
        chunk.get("subsection")
        or metadata.get("subsection")
        or metadata.get("subSection")
        or fallback
    )


def _build_snippet(text: str, needle_lower: str) -> str:
    """Return a bounded excerpt of ``text`` around the first match of ``needle_lower``."""
    position = text.lower().find(needle_lower)
    if position < 0:
        return ""
    start = max(0, position - _SECTION_SNIPPET_RADIUS)
    end = min(len(text), position + len(needle_lower) + _SECTION_SNIPPET_RADIUS)
    excerpt = text[start:end]
    if start > 0:
        excerpt = "..." + excerpt
    if end < len(text):
        excerpt = excerpt + "..."
    return excerpt



def _section_entries(
    chunks: List[dict],
    *,
    start: int,
    needle: Optional[str],
    section_fallback: Optional[str],
    subsection_fallback: Optional[str],
) -> List[dict]:
    """Per-passage pieces of one section page, in order, before size fitting."""
    entries: List[dict] = []
    for index, chunk in enumerate(chunks, start=start):
        text = _chunk_text(chunk)
        metadata = _best_effort_metadata(chunk)
        page_number = _chunk_page(chunk, metadata)
        chunk_id = resolve_chunk_identifier(chunk, metadata)
        if not (chunk_id and text):
            entries.append({"page_number": page_number})
            continue
        snippet = _build_snippet(text, needle) if needle else None
        entries.append(
            {
                "page_number": page_number,
                # Assembled section text is the full chunk text when surveying, or
                # the bounded excerpt when filtering, never both the joined text
                # and a per-chunk copy of it.
                "part": snippet if snippet is not None else text,
                "source": SectionChunkSource(
                    chunk_id=chunk_id,
                    chunk_index=index,
                    page_number=page_number,
                    section_title=_chunk_section_title(chunk, metadata, section_fallback),
                    subsection=_chunk_subsection(chunk, metadata, subsection_fallback),
                    char_count=len(text),
                    snippet=snippet,
                ),
                "doc_items": metadata.get("doc_items") or chunk.get("doc_items") or [],
            }
        )
    return entries


def _withhold_section_entry(entry: dict, _index: int) -> dict:
    """Stand-in for one passage too large for a page: pointer only, no text."""
    if "source" not in entry:
        return entry
    return {
        **entry,
        "part": None,
        "source": entry["source"].model_copy(update={"content_withheld": True}),
    }


def _assemble_section_entries(entries: List[dict]) -> dict:
    sources = [entry["source"] for entry in entries if "source" in entry]
    return {
        "content": "\n\n".join(
            entry["part"] for entry in entries if entry.get("part") is not None
        ),
        "page_numbers": sorted(
            {entry["page_number"] for entry in entries if entry.get("page_number")}
        ),
        "source_chunks": sources,
        "doc_items": [item for entry in entries for item in entry.get("doc_items") or []],
        "withheld": [source.chunk_id for source in sources if source.content_withheld],
    }


def _section_page_note(
    *,
    returned: int,
    total: int,
    start: int,
    ended_by: str,
    withheld: List[str],
) -> str:
    notes = []
    if ended_by != "end":
        reason = " (the response reached its size budget)" if ended_by == "size_budget" else ""
        notes.append(f" More remain{reason}; call again with offset={start + returned}.")
    if withheld:
        notes.append(
            " Passages too long to include here were withheld (content_withheld): "
            + ", ".join(withheld)
            + "; read each with read_chunk."
        )
    return "".join(notes)


def _bounded_section_read(
    selected: List[dict],
    *,
    max_chunks: Any,
    offset: Any,
    needle: Optional[str],
    section_fallback: Optional[str],
    subsection_fallback: Optional[str],
    render_result: Any,
) -> Any:
    """Serve one count- and size-bounded page of section passages.

    ``render_result(assembled, meta)`` builds the tool's result model for the
    passages on the page; the whole model is measured against the budget.
    Raises ValueError for malformed max_chunks/offset and ToolResultBudgetError
    when not even a pointer page fits.
    """
    total = len(selected)
    cap, limit_metadata = clamp_page_limit(
        max_chunks,
        default=_DEFAULT_SECTION_MAX_CHUNKS,
        maximum=_section_read_page_max_chunks(),
        name="max_chunks",
    )
    start = parse_offset(offset, total=total)
    budget = tool_result_max_bytes()
    entries = _section_entries(
        selected[start : start + cap],
        start=start,
        needle=needle,
        section_fallback=section_fallback,
        subsection_fallback=subsection_fallback,
    )
    aligned = [None] * start + entries

    def render(page: List[dict], returned: int) -> Any:
        next_offset = start + returned
        has_more = total > next_offset
        if not has_more:
            ended_by = "end"
        elif returned >= cap:
            ended_by = "limit"
        else:
            ended_by = "size_budget"
        return render_result(
            _assemble_section_entries(page),
            {
                "returned": returned,
                "total": total,
                "start": start,
                "next_offset": next_offset if has_more else None,
                "has_more": has_more,
                "ended_by": ended_by,
                "budget": budget,
                **limit_metadata,
            },
        )

    result, _ = fit_page(
        aligned,
        start=start,
        limit=cap,
        render=render,
        budget=budget,
        oversized=_withhold_section_entry,
    )
    return result


def _budget_unmet_bounds(exc: ToolResultBudgetError) -> dict:
    return {
        "measured_bytes": exc.measured,
        "limit_bytes": exc.limit,
        "setting": "TOOL_RESULT_MAX_BYTES",
    }


def create_read_section_tool(document_id: str, user_id: str, tracker: Optional["ToolCallTracker"] = None):
    """
    Create a read_section tool bound to a specific document and user.

    Args:
        document_id: UUID of the document to search
        user_id: User ID for tenant isolation
        tracker: Optional ToolCallTracker to record when this tool is called

    Returns a function_tool that retrieves ALL content from a specific section.
    """

    @function_tool
    async def read_section(
        section_name: str,
        max_chunks: int = _DEFAULT_SECTION_MAX_CHUNKS,
        offset: int = 0,
        text_contains: Optional[str] = None,
    ) -> SectionReadResult:
        """Survey the text of the chunks in a named section of the document.

        Returns the chunks classified under the section via the LLM-resolved semantic
        hierarchy, not linear page order, so it gives complete coverage even when search
        would miss low-scoring passages. Reach for it for comprehensive reads, especially:
        - Extracting complete lists (e.g., all strains in Methods)
        - Getting full tables or figure legends (a rich source of expression evidence)
        - Reading complete methodology details
        - Any case where you need comprehensive section content

        A long section is returned one page of chunks at a time. The result reports
        total_chunk_count and, when more remain, next_offset; pass that next_offset back
        in to continue. A page also ends early when the response reaches its size
        budget, and max_chunks above the configured maximum is clamped (the section
        reports requested and effective values). A single passage too long for one
        response is listed with content_withheld; read it with read_chunk. If you only
        need the part of a long section that mentions a specific term, set text_contains
        to return just the matching passages and a short excerpt around each match
        instead of the whole section.

        section.source_chunks lists the passages on this page as lightweight pointers
        (chunk_id, location, size) without repeating their text. Read one passage in full
        with read_chunk using its chunk_id for final evidence selection; read_section
        content is survey context, not retained evidence.

        Args:
            section_name: The section title to read (e.g., "Materials and Methods", "Results")
                          Partial matching is supported - "Methods" will match "Materials and Methods"
            max_chunks: Most passages to return on this page. Defaults to a sensible cap;
                        raise it to pull more of a very long section at once.
            offset: How many passages to skip before this page, for stepping through a
                    long section. Use the next_offset from the previous page to continue.
            text_contains: Return only passages that contain this text (case-insensitive),
                           each with a short excerpt around the match, instead of the whole
                           section.
        """
        # Record tool call if tracker is provided
        if tracker:
            tracker.record_call("read_section")

        logger.info(
            "Reading section '%s' from document %s... (offset=%s, max_chunks=%s, filtered=%s)",
            section_name,
            document_id[:8],
            offset,
            max_chunks,
            bool(text_contains),
        )

        try:
            # Use hierarchy-aware function that filters by parentSection
            # This respects LLM-resolved section boundaries instead of reading forward by index
            chunks = await get_chunks_by_parent_section(
                document_id=document_id,
                parent_section=section_name,
                user_id=user_id
            )

            if not chunks:
                logger.info("No content found for section: %s", section_name)
                return SectionReadResult(
                    summary=f"No content found for section '{section_name}'.",
                    section=None
                )

            resolved_section_title = _chunk_section_title(
                chunks[0], _best_effort_metadata(chunks[0]), section_name
            )

            needle = text_contains.lower() if text_contains else None
            if needle:
                selected = [
                    chunk for chunk in chunks if needle in _chunk_text(chunk).lower()
                ]
            else:
                selected = chunks
            resolved_section_title = resolved_section_title or section_name
            filter_note = f" matching '{text_contains}'" if text_contains else ""

            def render_result(assembled: dict, meta: dict) -> SectionReadResult:
                note = _section_page_note(
                    returned=meta["returned"],
                    total=meta["total"],
                    start=meta["start"],
                    ended_by=meta["ended_by"],
                    withheld=assembled["withheld"],
                )
                return SectionReadResult(
                    summary=(
                        f"Read {meta['returned']} of {meta['total']} chunks{filter_note} from "
                        f"'{resolved_section_title}'.{note} "
                        "Use section.source_chunks[].chunk_id with read_chunk, then pass selected "
                        "evidence_spans[].span_id values to record_evidence."
                    ),
                    section=SectionContent(
                        section_title=resolved_section_title,
                        page_numbers=assembled["page_numbers"],
                        content=assembled["content"],
                        chunk_count=meta["returned"],
                        returned_chunk_count=meta["returned"],
                        total_chunk_count=meta["total"],
                        offset=meta["start"],
                        next_offset=meta["next_offset"],
                        truncated=meta["has_more"],
                        source_chunks=assembled["source_chunks"] or None,
                        doc_items=assembled["doc_items"] or None,
                        page_ended_by=meta["ended_by"],
                        requested_max_chunks=meta["requested_max_chunks"],
                        effective_max_chunks=meta["effective_max_chunks"],
                        max_chunks_clamped=meta["max_chunks_clamped"],
                        budget_bytes=meta["budget"],
                    ),
                )

            try:
                result = _bounded_section_read(
                    selected,
                    max_chunks=max_chunks,
                    offset=offset,
                    needle=needle,
                    section_fallback=resolved_section_title,
                    subsection_fallback=None,
                    render_result=render_result,
                )
            except ValueError as exc:
                return SectionReadResult(
                    summary=f"Invalid read_section request: {exc}",
                    section=None,
                    error_code="invalid_result_cursor",
                )
            except ToolResultBudgetError as exc:
                return SectionReadResult(
                    summary="The section page could not fit the tool result budget.",
                    section=None,
                    error_code=TOOL_RESULT_BUDGET_UNMET,
                    result_bounds=_budget_unmet_bounds(exc),
                )
            logger.info(
                "Read %s/%s chunks from section '%s', pages %s",
                result.section.returned_chunk_count,
                result.section.total_chunk_count,
                resolved_section_title,
                result.section.page_numbers,
            )
            return result

        except Exception as e:
            logger.error("Read section error: %s", e, exc_info=True)
            return SectionReadResult(
                summary=f"Error reading section: {str(e)}",
                section=None
            )

    return read_section


# =============================================================================
# NEW HIERARCHY-AWARE TOOLS
# =============================================================================

class SubsectionContent(BaseModel):
    parent_section: str
    subsection: str
    page_numbers: List[int]
    content: str
    chunk_count: int
    returned_chunk_count: int
    total_chunk_count: int
    offset: int
    next_offset: Optional[int] = None
    truncated: bool
    source_chunks: Optional[List[SectionChunkSource]] = None
    doc_items: Optional[List[dict]] = None
    page_ended_by: Optional[str] = None
    requested_max_chunks: Optional[int] = None
    effective_max_chunks: Optional[int] = None
    max_chunks_clamped: Optional[bool] = None
    budget_bytes: Optional[int] = None


class SubsectionReadResult(BaseModel):
    summary: str
    subsection: Optional[SubsectionContent]
    error_code: Optional[str] = None
    result_bounds: Optional[dict] = None


def create_read_subsection_tool(document_id: str, user_id: str, tracker: Optional["ToolCallTracker"] = None):
    """
    Create a read_subsection tool for precise subsection reading.

    Uses LLM-resolved hierarchy for accurate subsection boundaries.
    """

    @function_tool
    async def read_subsection(
        parent_section: str,
        subsection: str,
        max_chunks: int = _DEFAULT_SECTION_MAX_CHUNKS,
        offset: int = 0,
        text_contains: Optional[str] = None,
    ) -> SubsectionReadResult:
        """Survey the text of the chunks in a SPECIFIC SUBSECTION of a parent section.

        Returns the chunks under the subsection via the LLM-resolved semantic hierarchy,
        not linear page order, for complete coverage when search may miss low-scoring
        passages. Use it for precise reads of a named subsection (figure legends are a
        rich source of expression evidence).

        A long subsection is returned one page of chunks at a time. The result reports
        total_chunk_count and, when more remain, next_offset; pass that next_offset back
        in to continue. A page also ends early when the response reaches its size
        budget, and max_chunks above the configured maximum is clamped. A single passage
        too long for one response is listed with content_withheld; read it with
        read_chunk. Set text_contains to return only passages that mention a specific
        term, each with a short excerpt around the match, instead of the whole subsection.

        subsection.source_chunks lists the passages on this page as lightweight pointers
        (chunk_id, location, size) without repeating their text. For retained evidence,
        call read_chunk on a relevant chunk_id and select evidence_spans[].span_id values
        before record_evidence.

        Examples:
            - read_subsection("Methods", "Fly Strains")
            - read_subsection("Results", "Gene Expression Analysis")
            - read_subsection("Discussion", "Limitations")

        Args:
            parent_section: The top-level section (e.g., "Methods", "Results")
            subsection: The specific subsection name (e.g., "Fly Strains", "Cell Culture")
            max_chunks: Most passages to return on this page. Defaults to a sensible cap;
                        raise it to pull more of a very long subsection at once.
            offset: How many passages to skip before this page. Use the next_offset from
                    the previous page to continue.
            text_contains: Return only passages that contain this text (case-insensitive),
                           each with a short excerpt around the match.
        """
        if tracker:
            tracker.record_call("read_subsection")

        logger.info(
            "Reading subsection '%s' in '%s' from document %s... (offset=%s, max_chunks=%s, filtered=%s)",
            subsection,
            parent_section,
            document_id[:8],
            offset,
            max_chunks,
            bool(text_contains),
        )

        try:
            chunks = await get_chunks_by_subsection(
                document_id=document_id,
                parent_section=parent_section,
                subsection=subsection,
                user_id=user_id
            )

            if not chunks:
                return SubsectionReadResult(
                    summary=f"No content found for subsection '{subsection}' in '{parent_section}'.",
                    subsection=None
                )

            needle = text_contains.lower() if text_contains else None
            if needle:
                selected = [
                    chunk for chunk in chunks if needle in _chunk_text(chunk).lower()
                ]
            else:
                selected = chunks
            filter_note = f" matching '{text_contains}'" if text_contains else ""

            def render_result(assembled: dict, meta: dict) -> SubsectionReadResult:
                note = _section_page_note(
                    returned=meta["returned"],
                    total=meta["total"],
                    start=meta["start"],
                    ended_by=meta["ended_by"],
                    withheld=assembled["withheld"],
                )
                return SubsectionReadResult(
                    summary=(
                        f"Read {meta['returned']} of {meta['total']} chunks{filter_note} from "
                        f"'{parent_section} > {subsection}'.{note} "
                        "Use subsection.source_chunks[].chunk_id with read_chunk for final evidence span selection."
                    ),
                    subsection=SubsectionContent(
                        parent_section=parent_section,
                        subsection=subsection,
                        page_numbers=assembled["page_numbers"],
                        content=assembled["content"],
                        chunk_count=meta["returned"],
                        returned_chunk_count=meta["returned"],
                        total_chunk_count=meta["total"],
                        offset=meta["start"],
                        next_offset=meta["next_offset"],
                        truncated=meta["has_more"],
                        source_chunks=assembled["source_chunks"] or None,
                        doc_items=assembled["doc_items"] or None,
                        page_ended_by=meta["ended_by"],
                        requested_max_chunks=meta["requested_max_chunks"],
                        effective_max_chunks=meta["effective_max_chunks"],
                        max_chunks_clamped=meta["max_chunks_clamped"],
                        budget_bytes=meta["budget"],
                    ),
                )

            try:
                result = _bounded_section_read(
                    selected,
                    max_chunks=max_chunks,
                    offset=offset,
                    needle=needle,
                    section_fallback=parent_section,
                    subsection_fallback=subsection,
                    render_result=render_result,
                )
            except ValueError as exc:
                return SubsectionReadResult(
                    summary=f"Invalid read_subsection request: {exc}",
                    subsection=None,
                    error_code="invalid_result_cursor",
                )
            except ToolResultBudgetError as exc:
                return SubsectionReadResult(
                    summary="The subsection page could not fit the tool result budget.",
                    subsection=None,
                    error_code=TOOL_RESULT_BUDGET_UNMET,
                    result_bounds=_budget_unmet_bounds(exc),
                )
            logger.info(
                "Read %s/%s chunks from subsection '%s', pages %s",
                result.subsection.returned_chunk_count,
                result.subsection.total_chunk_count,
                subsection,
                result.subsection.page_numbers,
            )
            return result

        except Exception as e:
            logger.error("Read subsection error: %s", e, exc_info=True)
            return SubsectionReadResult(
                summary=f"Error reading subsection: {str(e)}",
                subsection=None
            )

    return read_subsection
