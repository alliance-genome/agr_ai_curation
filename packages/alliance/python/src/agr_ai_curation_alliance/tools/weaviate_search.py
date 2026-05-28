"""
Weaviate document tools for OpenAI Agents SDK.

This module provides tools for:
- Hybrid search (semantic + keyword)
- Section listing (show available sections)
- Section reading (get full section content)
"""

import json
import logging
from typing import Optional, List, TYPE_CHECKING, Any, Literal

from pydantic import BaseModel
from agents import function_tool
from agr_ai_curation_runtime.evidence_spans import (
    EVIDENCE_SPANIZER_VERSION,
    build_evidence_spans,
)
from agr_ai_curation_runtime.chunk_identity import resolve_chunk_identifier
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


class ChunkSearchResult(BaseModel):
    summary: str
    hits: List[ChunkHit]


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


class ChunkReadResult(BaseModel):
    summary: str
    chunk: Optional[ChunkReadContent]


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
        Use search_mode='lexical' for exact biomedical symbols, IDs, strains,
        alleles, probes, reagents, genotype handles, PMIDs/DOIs, and controlled
        tokens; use search_mode='hybrid_lexical_first' when broad hybrid search
        should retry with lexical-heavy matching if needed. The default 'auto'
        preserves hybrid search for broad conceptual queries.

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

        logger.info(
            "Searching document %s... query='%s...', limit=%s, sections=%s, mode=%s",
            document_id[:8],
            query[:50],
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
                section_keywords=section_keywords,
                apply_mmr=True,
                strategy=strategy,
            )

            if not chunks:
                logger.info("No chunks found for query: %s...", query[:50])
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

            summary = f"Found {len(hits)} chunks"
            logger.debug("Returning %s structured chunks", len(hits))
            return ChunkSearchResult(summary=summary, hits=hits)

        except Exception as e:
            logger.error("Search error: %s", e, exc_info=True)
            return ChunkSearchResult(summary=f"Error searching document: {str(e)}", hits=[])

    return search_document


def create_read_chunk_tool(document_id: str, user_id: str, tracker: Optional["ToolCallTracker"] = None):
    """
    Create a read_chunk tool bound to a specific document and user.

    Returns a function_tool that retrieves raw chunk content plus deterministic
    exact-text evidence spans for extraction evidence selection.
    """

    @function_tool
    async def read_chunk(chunk_id: str) -> ChunkReadResult:
        """Read one PDF chunk and return selectable evidence_spans.

        For retained evidence, choose evidence_spans[].span_id values and pass
        them to record_evidence(span_ids=[...]). Do not write evidence quote text.

        Args:
            chunk_id: Chunk identifier returned by search_document or section source chunks.
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
        return ChunkReadResult(
            summary=(
                f"Read chunk '{actual_chunk_id}'{page_text}. "
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
                content=content,
                evidence_spans=spans,
                doc_items=chunk.get("doc_items") or metadata.get("doc_items") or None,
            ),
        )

    return read_chunk


class SectionChunkSource(BaseModel):
    chunk_id: str
    page_number: Optional[int] = None
    section_title: Optional[str] = None
    subsection: Optional[str] = None
    # Keep full source text here. Preview-only chunk text invites agents to copy
    # incomplete evidence instead of calling read_chunk for span IDs.
    content: str


class SectionContent(BaseModel):
    section_title: str
    page_numbers: List[int]
    content: str
    chunk_count: int
    source_chunks: Optional[List[SectionChunkSource]] = None
    doc_items: Optional[List[dict]] = None  # Combined bounding boxes from all chunks


class SectionReadResult(BaseModel):
    summary: str
    section: Optional[SectionContent]


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
    async def read_section(section_name: str) -> SectionReadResult:
        """Survey ALL content from a specific section of the document.

        Use this tool when you need to read an ENTIRE section at once, especially for:
        - Extracting complete lists (e.g., all strains in Methods)
        - Getting full tables or figures
        - Reading complete methodology details
        - Any case where you need comprehensive section content
        Use section.source_chunks[].chunk_id with read_chunk for final evidence
        selection; read_section content is survey context, not retained evidence.

        Args:
            section_name: The section title to read (e.g., "Materials and Methods", "Results")
                          Partial matching is supported - "Methods" will match "Materials and Methods"
        """
        # Record tool call if tracker is provided
        if tracker:
            tracker.record_call("read_section")

        logger.info(
            "Reading section '%s' from document %s...",
            section_name,
            document_id[:8],
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

            # Combine all chunk content and collect doc_items for highlighting
            content_parts = []
            page_numbers = set()
            actual_section_title = None
            all_doc_items = []
            source_chunks: List[SectionChunkSource] = []

            for chunk in chunks:
                # get_chunks_by_parent_section returns text, locator fields,
                # hierarchy fields, metadata, and optional doc_items.
                text = chunk.get("text") or chunk.get("content") or ""
                if text:
                    content_parts.append(text)

                metadata = _best_effort_metadata(chunk)
                page = (
                    chunk.get("page_number")
                    or chunk.get("pageNumber")
                    or metadata.get("page_number")
                    or metadata.get("pageNumber")
                )
                if page:
                    page_numbers.add(page)

                if not actual_section_title:
                    actual_section_title = (
                        chunk.get("section_title")
                        or chunk.get("sectionTitle")
                        or metadata.get("section_title")
                        or metadata.get("sectionTitle")
                        or section_name
                    )

                chunk_id = resolve_chunk_identifier(chunk, metadata)
                if chunk_id and text:
                    source_chunks.append(
                        SectionChunkSource(
                            chunk_id=chunk_id,
                            page_number=page,
                            section_title=chunk.get("section_title")
                            or chunk.get("sectionTitle")
                            or metadata.get("section_title")
                            or metadata.get("sectionTitle")
                            or actual_section_title,
                            subsection=chunk.get("subsection")
                            or metadata.get("subsection")
                            or metadata.get("subSection"),
                            content=text,
                        )
                    )

                chunk_doc_items = metadata.get("doc_items") or chunk.get("doc_items") or []
                if chunk_doc_items:
                    all_doc_items.extend(chunk_doc_items)

            full_content = "\n\n".join(content_parts)
            sorted_pages = sorted(page_numbers) if page_numbers else []
            resolved_section_title = actual_section_title or section_name

            logger.info(
                "Read %s chunks from section '%s', pages %s, %s doc_items",
                len(chunks),
                resolved_section_title,
                sorted_pages,
                len(all_doc_items),
            )

            return SectionReadResult(
                summary=(
                    f"Read {len(chunks)} chunks from '{resolved_section_title}'. "
                    "Use section.source_chunks[].chunk_id with read_chunk, then pass selected "
                    "evidence_spans[].span_id values to record_evidence."
                ),
                section=SectionContent(
                    section_title=resolved_section_title,
                    page_numbers=sorted_pages,
                    content=full_content,
                    chunk_count=len(chunks),
                    source_chunks=source_chunks if source_chunks else None,
                    doc_items=all_doc_items if all_doc_items else None,
                )
            )

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
    source_chunks: Optional[List[SectionChunkSource]] = None
    doc_items: Optional[List[dict]] = None


class SubsectionReadResult(BaseModel):
    summary: str
    subsection: Optional[SubsectionContent]


def create_read_subsection_tool(document_id: str, user_id: str, tracker: Optional["ToolCallTracker"] = None):
    """
    Create a read_subsection tool for precise subsection reading.

    Uses LLM-resolved hierarchy for accurate subsection boundaries.
    """

    @function_tool
    async def read_subsection(parent_section: str, subsection: str) -> SubsectionReadResult:
        """Survey content from a SPECIFIC SUBSECTION within a parent section.

        Use this for precise reading when you know the exact subsection you need.
        This respects the LLM-resolved document hierarchy for accurate boundaries.
        For retained evidence, call read_chunk on relevant chunks and select
        evidence_spans[].span_id values before record_evidence.

        Examples:
            - read_subsection("Methods", "Fly Strains")
            - read_subsection("Results", "Gene Expression Analysis")
            - read_subsection("Discussion", "Limitations")

        Args:
            parent_section: The top-level section (e.g., "Methods", "Results")
            subsection: The specific subsection name (e.g., "Fly Strains", "Cell Culture")
        """
        if tracker:
            tracker.record_call("read_subsection")

        logger.info(
            "Reading subsection '%s' in '%s' from document %s...",
            subsection,
            parent_section,
            document_id[:8],
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

            # Combine content and collect metadata
            content_parts = []
            page_numbers = set()
            all_doc_items = []
            source_chunks: List[SectionChunkSource] = []

            for chunk in chunks:
                text = chunk.get("text") or chunk.get("content") or ""
                if text:
                    content_parts.append(text)

                metadata = _best_effort_metadata(chunk)
                page = (
                    chunk.get("page_number")
                    or chunk.get("pageNumber")
                    or metadata.get("page_number")
                    or metadata.get("pageNumber")
                )
                if page:
                    page_numbers.add(page)

                chunk_id = resolve_chunk_identifier(chunk, metadata)
                if chunk_id and text:
                    source_chunks.append(
                        SectionChunkSource(
                            chunk_id=chunk_id,
                            page_number=page,
                            section_title=chunk.get("section_title")
                            or chunk.get("sectionTitle")
                            or metadata.get("section_title")
                            or metadata.get("sectionTitle")
                            or parent_section,
                            subsection=chunk.get("subsection")
                            or metadata.get("subsection")
                            or metadata.get("subSection")
                            or subsection,
                            content=text,
                        )
                    )

                doc_items = metadata.get("doc_items") or chunk.get("doc_items") or []
                if doc_items:
                    all_doc_items.extend(doc_items)

            full_content = "\n\n".join(content_parts)
            sorted_pages = sorted(page_numbers) if page_numbers else []

            logger.info(
                "Read %s chunks from subsection '%s', pages %s",
                len(chunks),
                subsection,
                sorted_pages,
            )

            return SubsectionReadResult(
                summary=(
                    f"Read {len(chunks)} chunks from '{parent_section} > {subsection}'. "
                    "Use subsection.source_chunks[].chunk_id with read_chunk for final evidence span selection."
                ),
                subsection=SubsectionContent(
                    parent_section=parent_section,
                    subsection=subsection,
                    page_numbers=sorted_pages,
                    content=full_content,
                    chunk_count=len(chunks),
                    source_chunks=source_chunks if source_chunks else None,
                    doc_items=all_doc_items if all_doc_items else None,
                )
            )

        except Exception as e:
            logger.error("Read subsection error: %s", e, exc_info=True)
            return SubsectionReadResult(
                summary=f"Error reading subsection: {str(e)}",
                subsection=None
            )

    return read_subsection
