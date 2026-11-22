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
from typing import Annotated, Optional, List, TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field
from agr_ai_curation_runtime.function_tools import function_tool
from agr_ai_curation_runtime.document_errors import DocumentReadError
from agr_ai_curation_runtime.document_references import (
    LocalIndex,
    current_readable_files,
    retain_issued_spans,
    scoped_document_tool,
)
from agr_ai_curation_runtime.evidence_spans import (
    EVIDENCE_SPANIZER_VERSION,
    build_evidence_spans,
    chunk_table_segments,
)
from agr_ai_curation_runtime.chunk_identity import resolve_chunk_identifier
from agr_ai_curation_runtime.tool_result_bounds import parse_offset
from agr_ai_curation_runtime.weaviate_chunks import (
    get_document_sections_hierarchical,
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


# read_chunk takes any integer so it can answer an index outside the document with
# the valid range; stored and selected chunk indexes stay LocalIndex (0 or more).
ChunkIndexRequest = Annotated[int, Field(strict=True)]


class TableRows(BaseModel):
    """Whole rows of one table held in a passage, with the table's header rows.

    Structural metadata written when the text was cut into passages. The model
    view shows it as an unnumbered label above the rows; it is never a span.
    """

    table_index: int
    char_start: int
    char_end: int
    first_data_row: Optional[int] = None
    last_data_row: Optional[int] = None
    total_data_rows: int
    header_row_count: int
    header_text: str
    separator_char_start: Optional[int] = None


def _table_rows(segments: List[dict]) -> Optional[List[TableRows]]:
    return [TableRows(**segment) for segment in segments] or None


class ChunkHit(BaseModel):
    chunk_id: Optional[str]
    chunk_index: LocalIndex
    section_title: Optional[str]
    page_number: Optional[int]
    score: Optional[float]
    content: str
    doc_items: Optional[List[dict]] = None  # Bounding box data for PDF highlighting
    tables: Optional[List[TableRows]] = None


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
    tables: Optional[List[TableRows]] = None
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
        """Discovery tool: search the paper's readable files for relevant chunks.

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
        model-context boundary handles compaction when context grows. A hit from a
        table also lists, under table_labels, the table's column names and which of
        its rows the hit holds.

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
            readable=current_readable_files(),
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
                    tables=_table_rows(chunk_table_segments(metadata)),
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
    chunk_id: str,
    text: str,
    page_number: Optional[int],
    section_title: Optional[str],
    table_segments: List[dict],
) -> List[EvidenceSpanResult]:
    """The one span builder every reading tool shares, so numbers mean the same everywhere."""
    return [
        EvidenceSpanResult(**span.to_dict())
        for span in build_evidence_spans(
            chunk_id=chunk_id,
            chunk_text=text,
            page_number=page_number,
            section_title=section_title,
            table_segments=table_segments,
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
    async def read_chunk(chunk_index: ChunkIndexRequest, span_offset: int = 0) -> ChunkReadResult:
        """Read one passage (chunk) of the paper, numbered sentence by sentence.

        The passage comes back as numbered lines. Each line starts with a
        sentence number in square brackets, for example "[3] Wingless expression
        was lost.", followed by that sentence exactly as it appears in the
        paper. When a passage numbers a table's rows one per line, one line
        without a number above them starts with "Column names of the" and
        shows the table's column names and which of its rows the passage holds;
        further column-name rows follow it, indented by four spaces. That label
        is for reading only and cannot be recorded as evidence. A sentence that
        spans several lines, such as a table row in a passage without that
        label, keeps its line breaks under one number, and its later lines are
        indented by four spaces.

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

        readable = current_readable_files()
        if readable.files and not readable.contains(chunk_index):
            # An index in no readable file (negative, past the end, or in a file that is
            # being added or was removed) is a request error the model can correct.
            return ChunkReadResult(
                summary=(
                    f"Invalid read_chunk request: chunk_index {chunk_index} does not exist; "
                    f"this document has chunks {readable.ranges_text()}."
                ),
                chunk=None,
                error_code="invalid_result_cursor",
            )
        chunk = None if chunk_index < 0 else await get_chunk_by_index(
            chunk_index=chunk_index,
            user_id=user_id,
            document_id=document_id,
            readable=readable,
        )
        if not chunk:
            # A missing chunk inside a readable file's range is a failed source read.
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
            readable=readable,
        )
        table_segments = chunk_table_segments(metadata)
        spans = _evidence_spans(actual_chunk_id, content, page_number, section_title, table_segments)

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
                    tables=_table_rows(table_segments),
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
    tables: Optional[List[TableRows]] = None


class NumberedSection(BaseModel):
    """Every chunk of the one section read, or every matching one for text_contains."""

    requested_section: str
    requested_subsection: Optional[str] = None
    file: str
    page_numbers: List[int]
    total_chunk_count: int
    text_contains: Optional[str] = None
    matched_chunk_count: Optional[int] = None
    passages: List[NumberedPassage]


class SectionReadResult(BaseModel):
    summary: str
    section: Optional[NumberedSection]
    error_code: Optional[str] = None


class SubsectionReadResult(BaseModel):
    summary: str
    subsection: Optional[NumberedSection]
    error_code: Optional[str] = None


# A section request the model can correct: the run continues with the answer.
SECTION_NOT_FOUND = "section_not_found"
SECTION_AMBIGUOUS = "section_ambiguous"
UNKNOWN_FILE = "unknown_file"


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


async def _numbered_section_read(
    chunks: List[dict],
    *,
    requested_section: str,
    requested_subsection: Optional[str],
    file: str,
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
        table_segments = chunk_table_segments(metadata)
        spans = _evidence_spans(chunk_id, text, page_number, _chunk_section_title(chunk, metadata), table_segments)
        passages.append(NumberedPassage(
            chunk_id=chunk_id,
            chunk_index=chunk_index,
            page_number=page_number,
            section=chunk.get("parent_section"),
            subsection=_chunk_subsection(chunk, metadata),
            content=text,
            evidence_spans=spans,
            doc_items=metadata.get("doc_items") or chunk.get("doc_items") or None,
            tables=_table_rows(table_segments),
        ))
        reads.append(_issued_read({"id": chunk_id, "chunk_index": chunk_index, "text": text}, spans, spans))
    if reads:
        await retain_issued_spans(reads)
    return NumberedSection(
        requested_section=requested_section,
        requested_subsection=requested_subsection,
        file=file,
        page_numbers=sorted({passage.page_number for passage in passages if passage.page_number}),
        total_chunk_count=len(chunks),
        text_contains=text_contains,
        matched_chunk_count=len(selected) if text_contains else None,
        passages=passages,
    )


def _section_summary(section: NumberedSection, label: str) -> str:
    if section.text_contains:
        shown = (
            f"{section.matched_chunk_count} of the {section.total_chunk_count} chunks of {label} "
            f"contain '{section.text_contains}'; each matching chunk is shown whole."
        )
    else:
        shown = f"Read all {section.total_chunk_count} chunks of {label}."
    return (
        f"{shown} Each passage lists numbered sentences; "
        "pass its chunk_index and a sentence number (span_index) to record_evidence."
    )


def _quoted(names: List[str]) -> str:
    return ", ".join(f"'{name}'" for name in names)


def _scope_or_answer(readable: Any, file: Optional[str]) -> tuple[Any, Optional[str]]:
    """The files a section read may look in, or the unknown-file answer."""
    if file is None:
        return readable, None
    target = readable.by_label(file)
    if target is None:
        names = ", ".join(label for label, _ in readable.labelled())
        return None, f"There is no readable file named '{file}'. Readable files: {names}."
    return readable.only(target), None


def _files_holding(readable: Any, chunks: List[dict]) -> List[str]:
    """The labels of the readable files the chunks belong to, in tab order."""
    held = {readable.file_for(chunk.get("chunk_index")) for chunk in chunks}
    if None in held:
        raise RuntimeError("Section read returned a chunk outside the readable files")
    return [label for label, file in readable.labelled() if file in held]


async def _not_found_answer(
    document_id: str, user_id: str, scope: Any, *, section: str, subsection: Optional[str], file: Optional[str],
) -> str:
    """Every exact name the request could have used, file by file, as stored."""
    outline = await get_document_sections_hierarchical(document_id, user_id, readable=scope)
    where = f"file '{file}'" if file is not None else "the readable files"
    parts = []
    for listed in outline["files"]:
        if subsection is None:
            names = [entry["name"] for entry in listed["sections"]]
        else:
            names = [
                sub["name"] for entry in listed["sections"] if entry["name"] == section
                for sub in entry["subsections"]
            ]
        if names:
            parts.append(f"{listed['name']}: {_quoted(names)}")
    if subsection is None:
        missing = f"No section named '{section}' in {where}."
        listing = "Section names, exactly as stored"
    elif parts:
        missing = f"No subsection '{subsection}' in section '{section}' in {where}."
        listing = f"Subsection names of '{section}', exactly as stored"
    else:
        return await _not_found_answer(
            document_id, user_id, scope, section=section, subsection=None, file=file,
        )
    return f"{missing} {listing}: {'; '.join(parts) if parts else 'none'}."


async def _read_named_section(
    *,
    document_id: str,
    user_id: str,
    section: str,
    subsection: Optional[str],
    file: Optional[str],
    text_contains: Optional[str],
) -> tuple[Optional[NumberedSection], str, Optional[str]]:
    """Read one section (or subsection) of one readable file by its exact stored name.

    Returns (section, summary, error_code); the error answers let the model correct
    the request with the names it needs.
    """
    readable = current_readable_files()
    scope, unknown_file = _scope_or_answer(readable, file)
    if unknown_file is not None:
        return None, unknown_file, UNKNOWN_FILE
    if subsection is None:
        chunks = await get_chunks_by_parent_section(
            document_id=document_id,
            parent_section=section,
            user_id=user_id,
            readable=scope,
        )
    else:
        chunks = await get_chunks_by_subsection(
            document_id=document_id,
            parent_section=section,
            subsection=subsection,
            user_id=user_id,
            readable=scope,
        )
    # Incomplete stored rows fail as a source fault before anything else is decided.
    _validate_section_chunks(chunks)
    if not chunks:
        answer = await _not_found_answer(
            document_id, user_id, scope, section=section, subsection=subsection, file=file,
        )
        return None, answer, SECTION_NOT_FOUND
    holding = _files_holding(readable, chunks)
    if len(holding) > 1:
        kind = "Section" if subsection is None else f"Subsection '{subsection}' of section"
        return None, (
            f"{kind} '{section}' is in more than one file: {', '.join(holding)}. "
            "Call again with file set to one of these names."
        ), SECTION_AMBIGUOUS
    read = await _numbered_section_read(
        chunks,
        requested_section=section,
        requested_subsection=subsection,
        file=holding[0],
        text_contains=text_contains,
    )
    label = f"'{section}'" if subsection is None else f"'{section} > {subsection}'"
    return read, _section_summary(read, label), None


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
        file: Optional[str] = None,
        text_contains: Optional[str] = None,
    ) -> SectionReadResult:
        """Read a whole section of the paper, numbered sentence by sentence.

        Returns every passage (chunk) the paper's outline places in the named
        section, in reading order, however long the section is. Use it whenever you
        need everything a section says: complete lists such as every strain in the
        Methods, full tables and figure legends, or a full methods description.

        Each passage shows its chunk_index, page and subsection, then its text
        as numbered lines. Each line starts with a sentence number in
        square brackets, for example "[2] Embryos were fixed in 4%
        formaldehyde.", followed by that sentence exactly as it appears in the
        paper. When a passage numbers a table's rows one per line, one line
        without a number above them starts with "Column names of the" and
        shows the table's column names and which of its rows the passage holds;
        further column-name rows follow it, indented by four spaces. That label
        is for reading only and cannot be recorded as evidence. A sentence that
        spans several lines, such as a table row in a passage without that
        label, keeps its line breaks under one number, and its later lines are
        indented by four spaces. To keep a sentence as evidence, pass the passage's
        chunk_index and the sentence number (span_index) to record_evidence;
        there is no need to read the passage again with read_chunk.

        The section name must be one of the names in the paper's outline, written
        exactly as shown there. When the paper has several files and more than one
        has a section of that name, also give the file's name from the outline.

        Args:
            section_name: The section to read, exactly as named in the paper's outline (for example "Materials and Methods").
            file: The file the section is in, written exactly as in the outline. Needed only when several files have a
                  section of that name.
            text_contains: Optional word or phrase (case does not matter). Only passages containing it are returned,
                           each one whole and numbered; the result says how many of the section's passages matched.
        """
        if tracker:
            tracker.record_call("read_section")

        logger.info(
            "Reading section from document %s... (file given=%s, filtered=%s)",
            document_id[:8],
            file is not None,
            bool(text_contains),
        )

        section, summary, error_code = await _read_named_section(
            document_id=document_id,
            user_id=user_id,
            section=section_name,
            subsection=None,
            file=file,
            text_contains=text_contains,
        )
        if section is not None:
            logger.info(
                "Read %s/%s chunks from one section, pages %s",
                len(section.passages),
                section.total_chunk_count,
                section.page_numbers,
            )
        return SectionReadResult(summary=summary, section=section, error_code=error_code)

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
        file: Optional[str] = None,
        text_contains: Optional[str] = None,
    ) -> SubsectionReadResult:
        """Read a whole subsection of the paper, numbered sentence by sentence.

        Returns every passage (chunk) the paper's outline places in the named
        subsection of a section, in reading order, however long it is. Use it for a
        focused read of one part of a section, such as the fly strains in the Methods
        or one figure legend.

        Each passage shows its chunk_index, page and subsection, then its text
        as numbered lines. Each line starts with a sentence number in
        square brackets, for example "[0] Flies were raised at 25 C.", followed
        by that sentence exactly as it appears in the paper. When a passage
        numbers a table's rows one per line, one line without a number above
        them starts with "Column names of the" and shows the table's column names
        and which of its rows the passage holds; further column-name rows follow
        it, indented by four spaces. That label is for reading only and cannot
        be recorded as evidence. A sentence that spans several lines, such as a
        table row in a passage without that label, keeps its line breaks under
        one number, and its later lines are indented by four spaces. To keep a
        sentence as evidence, pass the passage's chunk_index and the sentence
        number (span_index) to record_evidence; there is
        no need to read the passage again with read_chunk.

        Both names must be written exactly as in the paper's outline. When the
        paper has several files and more than one has that subsection, also give
        the file's name from the outline.

        Example, with the names exactly as the outline shows them:
            - read_subsection("Materials and Methods", "Fly strains")

        Args:
            parent_section: The top-level section, exactly as named in the outline.
            subsection: The subsection within it, exactly as named in the outline.
            file: The file the subsection is in, written exactly as in the outline. Needed only when several files
                  have that subsection.
            text_contains: Optional word or phrase (case does not matter). Only passages containing it are returned,
                           each one whole and numbered; the result says how many of the subsection's passages matched.
        """
        if tracker:
            tracker.record_call("read_subsection")

        logger.info(
            "Reading subsection from document %s... (file given=%s, filtered=%s)",
            document_id[:8],
            file is not None,
            bool(text_contains),
        )

        section, summary, error_code = await _read_named_section(
            document_id=document_id,
            user_id=user_id,
            section=parent_section,
            subsection=subsection,
            file=file,
            text_contains=text_contains,
        )
        if section is not None:
            logger.info(
                "Read %s/%s chunks from one subsection, pages %s",
                len(section.passages),
                section.total_chunk_count,
                section.page_numbers,
            )
        return SubsectionReadResult(summary=summary, subsection=section, error_code=error_code)

    return scoped_document_tool(read_subsection, document_id=document_id, user_id=user_id)
