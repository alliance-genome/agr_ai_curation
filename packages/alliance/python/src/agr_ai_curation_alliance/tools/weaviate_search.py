"""
Weaviate document tools for OpenAI Agents SDK.

This module provides tools for:
- Hybrid search (semantic + keyword)
- Chunk reading (one passage, numbered sentence by sentence)
- Section and subsection reading (every passage of a section, numbered the same way)
"""

import hashlib
import json
import logging
import os
from typing import Optional, List, TYPE_CHECKING, Any, Literal

from pydantic import BaseModel
from agr_ai_curation_runtime.function_tools import function_tool
from agr_ai_curation_runtime.document_errors import DocumentReadError
from agr_ai_curation_runtime.document_references import LocalIndex, scoped_document_tool, retain_issued_spans
from agr_ai_curation_runtime.evidence_spans import (
    EVIDENCE_SPANIZER_VERSION,
    build_evidence_spans,
)
from agr_ai_curation_runtime.chunk_identity import resolve_chunk_identifier
from agr_ai_curation_runtime.tool_result_bounds import parse_offset
from agr_ai_curation_runtime.weaviate_chunks import (
    count_document_chunks,
    hybrid_search_chunks,
    get_chunk_by_index,
    get_chunk_neighbor_ids,
    get_chunks_by_parent_section,  # Uses LLM-resolved parentSection for accurate boundaries
    get_chunks_by_subsection,
)

if TYPE_CHECKING:
    from ..guardrails import ToolCallTracker

logger = logging.getLogger(__name__)

SearchMode = Literal["auto", "hybrid", "lexical", "hybrid_lexical_first"]


def _raise_document_read_error(ctx: Any, error: Exception) -> str:
    # Raising from the SDK handler retains the original cause instead of returning
    # its error text as a successful tool result for the document adapter.
    if isinstance(error, DocumentReadError):
        raise error
    raise DocumentReadError(getattr(ctx, "tool_name", "document_read")) from error


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
    chunk_index: LocalIndex
    section_title: Optional[str]
    page_number: Optional[int]
    score: Optional[float]
    content: str
    doc_items: Optional[List[dict]] = None  # Bounding box data for PDF highlighting


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
    previous_chunk_index: Optional[int] = None
    next_chunk_index: Optional[int] = None
    page_number: Optional[int] = None
    section_title: Optional[str] = None
    subsection: Optional[str] = None
    content: str
    evidence_spans: List[EvidenceSpanResult]
    doc_items: Optional[List[dict]] = None
    # Present for an explicit span offset: ``content`` is the complete suffix
    # covering all remaining spans.
    content_range: Optional[dict] = None
    span_page: Optional[dict] = None


class ChunkReadResult(BaseModel):
    summary: str
    chunk: Optional[ChunkReadContent]
    error_code: Optional[str] = None
    result_bounds: Optional[dict] = None


def _coerce_chunk_index(value: Any) -> Optional[int]:
    if type(value) is not int or value < 0:
        raise ValueError("Stored document chunk_index must be a non-negative integer")
    return value


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

    @function_tool(failure_error_function=_raise_document_read_error)
    async def search_document(
        query: str,
        limit: int = 5,
        section_keywords: Optional[List[str]] = None,
        search_mode: SearchMode = "auto",
    ) -> ChunkSearchResult:
        """Discovery tool: search the loaded PDF for relevant chunks.

        Use returned chunk_index values with read_chunk for final evidence selection.
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

        All selected hits are returned in rank order with full text. The shared
        model-context boundary handles compaction when context grows.

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

        # Exact biomedical tokens need explicit lexical-heavy retrieval modes;
        # reranking/MMR must still run on full chunk content, not previews.
        try:
            strategy = _strategy_for_search_mode(search_mode)
        except ValueError as exc:
            return ChunkSearchResult(summary=str(exc), hits=[])

        # Removed legacy error-summary fallback — canonical runtime failure reporting owns Weaviate outages after ALL-825.
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
                    chunk_index=_coerce_chunk_index(chunk.get("chunk_index")),
                    section_title=section,
                    page_number=page,
                    score=score,
                    content=content,
                    doc_items=doc_items if doc_items else None,
                )
            )

        logger.debug("Returning %s structured chunks", len(hits))
        return _search_result(hits)


    return scoped_document_tool(search_document, document_id=document_id, user_id=user_id)


def _search_summary(hits: List[ChunkHit]) -> str:
    return f"Found {len(hits)} chunks"


def _search_result(hits: List[ChunkHit]) -> ChunkSearchResult:
    """Return all selected ranked hits; shared context policy owns capacity."""
    return ChunkSearchResult(summary=_search_summary(hits), hits=hits)


def _evidence_spans(
    chunk_id: str, text: str, page_number: Optional[int], section_title: Optional[str],
) -> List[EvidenceSpanResult]:
    """The one span builder every reading tool shares, so numbers mean the same everywhere."""
    return [
        EvidenceSpanResult(**span.to_dict())
        for span in build_evidence_spans(
            chunk_id=chunk_id,
            chunk_text=text,
            page_number=page_number,
            section_title=section_title,
        )
    ]


def _issued_read(
    chunk: dict, spans: List[EvidenceSpanResult], shown: List[EvidenceSpanResult],
) -> tuple[dict, List[dict], List[int]]:
    """One displayed chunk for retain_issued_spans: identity, definitions, shown numbers."""
    return (
        {"id": chunk["id"], "chunk_index": chunk["chunk_index"], "text": chunk["text"]},
        [span.model_dump() for span in spans],
        [span.span_index for span in shown],
    )


def _chunk_window(
    *,
    content: str,
    spans: List[EvidenceSpanResult],
    span_offset: int,
    render: Any,
) -> Any:
    """Return all exact text and spans from an explicitly requested span offset."""
    total = len(spans)
    start_char = 0 if span_offset == 0 else spans[span_offset].char_start
    return render(
        content[start_char:],
        {"start": start_char, "end": len(content), "total_chars": len(content)},
        spans[span_offset:],
        {"span_offset": span_offset, "returned_spans": total - span_offset,
         "total_spans": total, "next_span_offset": None, "complete": True},
    )


def create_read_chunk_tool(document_id: str, user_id: str, tracker: Optional["ToolCallTracker"] = None):
    """
    Create a read_chunk tool bound to a specific document and user.

    Returns a function_tool that retrieves raw chunk content plus deterministic
    exact-text evidence spans for extraction evidence selection.
    """

    @function_tool(failure_error_function=_raise_document_read_error)
    async def read_chunk(chunk_index: LocalIndex, span_offset: int = 0) -> ChunkReadResult:
        """Read one passage (chunk) of the paper, numbered sentence by sentence.

        The passage comes back as numbered lines. Each line starts with a sentence
        number in square brackets, for example "[3] Wingless expression was lost.",
        followed by that sentence exactly as it appears in the paper. A sentence
        that spans several lines in the paper, such as a table row, keeps its line
        breaks under one number, and its later lines are indented by four spaces,
        so only a real sentence number ever starts a line.

        To keep a sentence as evidence, pass the passage's chunk_index and the
        sentence number (span_index) to record_evidence. The application copies the
        exact sentence from the paper; never type the quote yourself. The sentence
        numbers are the same ones read_section and read_subsection show for this
        passage, so a sentence seen through any of the three tools can be used.

        By default the whole passage is returned. span_offset starts the read at
        that sentence number and returns every sentence from there to the end of
        the passage.

        Args:
            chunk_index: The passage number, as shown by search_document, read_section or read_subsection.
            span_offset: Optional sentence number to start from; leave it out to read the whole passage.
        """
        if tracker:
            tracker.record_call("read_chunk")

        logger.info(
            "Reading chunk '%s' from document %s...",
            chunk_index,
            document_id[:8],
        )

        chunk = await get_chunk_by_index(
            chunk_index=chunk_index,
            user_id=user_id,
            document_id=document_id,
        )
        if not chunk:
            # An index past the document's last chunk is a request error the model
            # can correct; a missing chunk inside the range is a failed source read.
            chunk_count = await count_document_chunks(user_id=user_id, document_id=document_id)
            if chunk_count > 0 and chunk_index >= chunk_count:
                return ChunkReadResult(
                    summary=(
                        f"Invalid read_chunk request: chunk_index {chunk_index} does not exist; "
                        f"this document has chunks 0 to {chunk_count - 1}."
                    ),
                    chunk=None,
                    error_code="invalid_result_cursor",
                )
            raise DocumentReadError("read_chunk")

        chunk_id = _read_actual_chunk_id(str(chunk_index), chunk)
        content = chunk.get("text")
        if not isinstance(content, str):
            raise ValueError(f"Chunk '{chunk_id}' is missing exact raw text content")

        metadata = _read_chunk_metadata(chunk_id, chunk.get("metadata"))

        actual_chunk_id = chunk_id
        if _coerce_chunk_index(chunk.get("chunk_index")) != chunk_index:
            raise ValueError("Resolved chunk index differs from the selected source")
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
        spans = _evidence_spans(actual_chunk_id, content, page_number, section_title)

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
                    f" Showing sentences {span_page['span_offset']} to "
                    f"{span_page['total_spans'] - 1}, the end of the chunk."
                )
            return ChunkReadResult(
                summary=(
                    f"Read chunk {chunk_index}{page_text}.{window_note} "
                    "Select chunk_index/span_index pairs for record_evidence."
                ),
                chunk=ChunkReadContent(
                    chunk_id=actual_chunk_id,
                    chunk_index=chunk_index,
                    chunk_number=chunk_index + 1 if chunk_index is not None else None,
                    previous_chunk_id=neighbor_ids.get("previous_chunk_id"),
                    next_chunk_id=neighbor_ids.get("next_chunk_id"),
                    previous_chunk_index=chunk_index - 1 if neighbor_ids.get("previous_chunk_id") else None,
                    next_chunk_index=chunk_index + 1 if neighbor_ids.get("next_chunk_id") else None,
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

        if span_offset in (0, None):
            whole = render(content, None, spans, None)
            await retain_issued_spans([_issued_read(chunk, spans, spans)])
            return whole
        try:
            window_start = parse_offset(span_offset, total=max(0, len(spans) - 1), name="span_offset")
            window_result = _chunk_window(
                content=content,
                spans=spans,
                span_offset=window_start,
                render=render,
            )
        except ValueError as exc:
            return ChunkReadResult(
                summary=f"Invalid read_chunk request: {exc}",
                chunk=None,
                error_code="invalid_result_cursor",
            )

        await retain_issued_spans([_issued_read(chunk, spans, window_result.chunk.evidence_spans)])
        return window_result

    return scoped_document_tool(read_chunk, document_id=document_id, user_id=user_id)


class NumberedPassage(BaseModel):
    """One whole chunk of a section read, with the same spans read_chunk issues."""

    chunk_id: str
    chunk_index: int
    page_number: Optional[int] = None
    section: Optional[str] = None
    subsection: Optional[str] = None
    content: str
    evidence_spans: List[EvidenceSpanResult]
    doc_items: Optional[List[dict]] = None


class NumberedSection(BaseModel):
    """Every chunk the section request matched, or every matching one for text_contains."""

    requested_section: str
    requested_subsection: Optional[str] = None
    matched_sections: List[str]
    matched_subsections: Optional[List[str]] = None
    page_numbers: List[int]
    total_chunk_count: int
    text_contains: Optional[str] = None
    matched_chunk_count: Optional[int] = None
    passages: List[NumberedPassage]


class SectionReadResult(BaseModel):
    summary: str
    section: Optional[NumberedSection]


class SubsectionReadResult(BaseModel):
    summary: str
    subsection: Optional[NumberedSection]


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


def _chunk_section_title(chunk: dict, metadata: dict) -> Optional[str]:
    return (
        chunk.get("section_title")
        or chunk.get("sectionTitle")
        or metadata.get("section_title")
        or metadata.get("sectionTitle")
    )


def _chunk_subsection(chunk: dict, metadata: dict) -> Optional[str]:
    return (
        chunk.get("subsection")
        or metadata.get("subsection")
        or metadata.get("subSection")
    )


def _validate_section_chunks(chunks: List[dict]) -> None:
    """Reject incomplete stored rows before filtering can hide missing content."""
    for chunk in chunks:
        text = _chunk_text(chunk)
        chunk_id = resolve_chunk_identifier(chunk, _best_effort_metadata(chunk))
        if not chunk_id or not isinstance(text, str) or not text:
            raise RuntimeError("Stored section chunk is missing identity or text")
        try:
            _coerce_chunk_index(chunk.get("chunk_index"))
        except ValueError as exc:
            raise RuntimeError("Stored section chunk has invalid chunk_index") from exc


def _distinct(values: List[Optional[str]]) -> List[str]:
    return list(dict.fromkeys(value for value in values if value))


async def _numbered_section_read(
    chunks: List[dict],
    *,
    requested_section: str,
    requested_subsection: Optional[str],
    text_contains: Optional[str],
) -> NumberedSection:
    """Show every selected chunk whole and issue all of its spans in one transaction."""
    _validate_section_chunks(chunks)
    needle = text_contains.lower() if text_contains else None
    selected = [chunk for chunk in chunks if needle is None or needle in _chunk_text(chunk).lower()]
    passages: List[NumberedPassage] = []
    reads = []
    for chunk in selected:
        metadata = _best_effort_metadata(chunk)
        text = _chunk_text(chunk)
        chunk_id = resolve_chunk_identifier(chunk, metadata)
        chunk_index = _coerce_chunk_index(chunk.get("chunk_index"))
        page_number = _chunk_page(chunk, metadata)
        spans = _evidence_spans(chunk_id, text, page_number, _chunk_section_title(chunk, metadata))
        passages.append(NumberedPassage(
            chunk_id=chunk_id,
            chunk_index=chunk_index,
            page_number=page_number,
            section=chunk.get("parent_section"),
            subsection=_chunk_subsection(chunk, metadata),
            content=text,
            evidence_spans=spans,
            doc_items=metadata.get("doc_items") or chunk.get("doc_items") or None,
        ))
        reads.append(_issued_read({"id": chunk_id, "chunk_index": chunk_index, "text": text}, spans, spans))
    if reads:
        await retain_issued_spans(reads)
    return NumberedSection(
        requested_section=requested_section,
        requested_subsection=requested_subsection,
        matched_sections=_distinct([chunk.get("parent_section") for chunk in chunks]),
        matched_subsections=(
            _distinct([_chunk_subsection(chunk, _best_effort_metadata(chunk)) for chunk in chunks])
            if requested_subsection is not None else None
        ),
        page_numbers=sorted({passage.page_number for passage in passages if passage.page_number}),
        total_chunk_count=len(chunks),
        text_contains=text_contains,
        matched_chunk_count=len(selected) if text_contains else None,
        passages=passages,
    )


def _section_summary(section: NumberedSection, label: str) -> str:
    names = ", ".join(f"'{name}'" for name in section.matched_sections)
    if section.matched_subsections:
        subsections = ", ".join(f"'{name}'" for name in section.matched_subsections)
        names = f"{names}; subsections {subsections}" if names else f"subsections {subsections}"
    if section.text_contains:
        shown = (
            f"{section.matched_chunk_count} of the {section.total_chunk_count} chunks of {label} "
            f"contain '{section.text_contains}'; each matching chunk is shown whole."
        )
    else:
        shown = f"Read all {section.total_chunk_count} chunks of {label}."
    matched = f" Matched section names: {names}." if names else ""
    return (
        f"{shown}{matched} Each passage lists numbered sentences; "
        "pass its chunk_index and a sentence number (span_index) to record_evidence."
    )


def create_read_section_tool(document_id: str, user_id: str, tracker: Optional["ToolCallTracker"] = None):
    """
    Create a read_section tool bound to a specific document and user.

    Args:
        document_id: UUID of the document to search
        user_id: User ID for tenant isolation
        tracker: Optional ToolCallTracker to record when this tool is called

    Returns a function_tool that retrieves every chunk of a named section.
    """

    @function_tool(failure_error_function=_raise_document_read_error)
    async def read_section(
        section_name: str,
        text_contains: Optional[str] = None,
    ) -> SectionReadResult:
        """Read a whole section of the paper, numbered sentence by sentence.

        Returns every passage (chunk) the paper's outline places in the named
        section, in reading order, however long the section is. Use it whenever you
        need everything a section says: complete lists such as every strain in the
        Methods, full tables and figure legends, or a full methods description.

        Each passage shows its chunk_index, page and subsection, then its text as
        numbered lines. Each line starts with a sentence number in square brackets,
        for example "[2] Embryos were fixed in 4% formaldehyde.", followed by that
        sentence exactly as it appears in the paper. A sentence that spans several
        lines, such as a table row, keeps its line breaks under one number, and its
        later lines are indented by four spaces. To keep a sentence as evidence,
        pass the passage's chunk_index and the sentence number (span_index) to
        record_evidence; there is no need to read the passage again with read_chunk.

        The section name is matched as part of a name: "Methods" also matches
        "Materials and Methods" and "Supplementary Methods". The result lists every
        section name that matched, so check it when several sections share a word.

        Args:
            section_name: The section to read, as named in the paper's outline (e.g., "Materials and Methods", "Results").
            text_contains: Optional word or phrase (case does not matter). Only passages containing it are returned,
                           each one whole and numbered; the result says how many of the section's passages matched.
        """
        if tracker:
            tracker.record_call("read_section")

        logger.info(
            "Reading section '%s' from document %s... (filtered=%s)",
            section_name,
            document_id[:8],
            bool(text_contains),
        )

        # Hierarchy-aware retrieval: LLM-resolved parentSection, every matching chunk.
        chunks = await get_chunks_by_parent_section(
            document_id=document_id,
            parent_section=section_name,
            user_id=user_id,
        )

        if not chunks:
            logger.info("No content found for section: %s", section_name)
            return SectionReadResult(
                summary=f"No content found for section '{section_name}'.",
                section=None
            )

        section = await _numbered_section_read(
            chunks,
            requested_section=section_name,
            requested_subsection=None,
            text_contains=text_contains,
        )
        logger.info(
            "Read %s/%s chunks from section '%s', pages %s",
            len(section.passages),
            section.total_chunk_count,
            section_name,
            section.page_numbers,
        )
        return SectionReadResult(summary=_section_summary(section, f"'{section_name}'"), section=section)

    return scoped_document_tool(read_section, document_id=document_id, user_id=user_id)


def create_read_subsection_tool(document_id: str, user_id: str, tracker: Optional["ToolCallTracker"] = None):
    """
    Create a read_subsection tool for precise subsection reading.

    Uses LLM-resolved hierarchy for accurate subsection boundaries.
    """

    @function_tool(failure_error_function=_raise_document_read_error)
    async def read_subsection(
        parent_section: str,
        subsection: str,
        text_contains: Optional[str] = None,
    ) -> SubsectionReadResult:
        """Read a whole subsection of the paper, numbered sentence by sentence.

        Returns every passage (chunk) the paper's outline places in the named
        subsection of a section, in reading order, however long it is. Use it for a
        focused read of one part of a section, such as "Fly strains" in the Methods
        or one figure legend.

        Each passage shows its chunk_index, page and subsection, then its text as
        numbered lines. Each line starts with a sentence number in square brackets,
        for example "[0] Flies were raised at 25 C.", followed by that sentence
        exactly as it appears in the paper. A sentence that spans several lines,
        such as a table row, keeps its line breaks under one number, and its later
        lines are indented by four spaces. To keep a sentence as evidence, pass the
        passage's chunk_index and the sentence number (span_index) to
        record_evidence; there is no need to read the passage again with read_chunk.

        Both names are matched as part of a name, so "Strains" also matches
        "Fly strains and genetics". The result lists every section and subsection
        name that matched.

        Examples:
            - read_subsection("Methods", "Fly Strains")
            - read_subsection("Results", "Gene Expression Analysis")
            - read_subsection("Discussion", "Limitations")

        Args:
            parent_section: The top-level section (e.g., "Methods", "Results").
            subsection: The subsection within it (e.g., "Fly Strains", "Cell Culture").
            text_contains: Optional word or phrase (case does not matter). Only passages containing it are returned,
                           each one whole and numbered; the result says how many of the subsection's passages matched.
        """
        if tracker:
            tracker.record_call("read_subsection")

        logger.info(
            "Reading subsection '%s' in '%s' from document %s... (filtered=%s)",
            subsection,
            parent_section,
            document_id[:8],
            bool(text_contains),
        )

        chunks = await get_chunks_by_subsection(
            document_id=document_id,
            parent_section=parent_section,
            subsection=subsection,
            user_id=user_id,
        )

        if not chunks:
            return SubsectionReadResult(
                summary=f"No content found for subsection '{subsection}' in '{parent_section}'.",
                subsection=None
            )

        section = await _numbered_section_read(
            chunks,
            requested_section=parent_section,
            requested_subsection=subsection,
            text_contains=text_contains,
        )
        logger.info(
            "Read %s/%s chunks from subsection '%s', pages %s",
            len(section.passages),
            section.total_chunk_count,
            subsection,
            section.page_numbers,
        )
        return SubsectionReadResult(
            summary=_section_summary(section, f"'{parent_section} > {subsection}'"),
            subsection=section,
        )

    return scoped_document_tool(read_subsection, document_id=document_id, user_id=user_id)
