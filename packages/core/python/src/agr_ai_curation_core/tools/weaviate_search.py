"""
Weaviate document tools for OpenAI Agents SDK.

This module provides tools for:
- Hybrid search (semantic + keyword)
- Section listing (show available sections)
- Section reading (get full section content)
"""

import json
import logging
from typing import Optional, List, TYPE_CHECKING

from pydantic import BaseModel
from agents import function_tool

from src.lib.weaviate_client.chunks import (
    hybrid_search_chunks,
    get_document_sections,
    get_chunks_by_parent_section,  # Uses LLM-resolved parentSection for accurate boundaries
    get_chunks_by_subsection,
)

if TYPE_CHECKING:
    from ..guardrails import ToolCallTracker

logger = logging.getLogger(__name__)


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
        section_keywords: Optional[List[str]] = None
    ) -> ChunkSearchResult:
        """Search the loaded PDF document for relevant content."""
        # Record tool call if tracker is provided
        if tracker:
            tracker.record_call("search_document")

        limit = min(max(1, limit), 10)

        logger.info(
            "Searching document %s... query='%s...', limit=%s, sections=%s",
            document_id[:8],
            query[:50],
            limit,
            section_keywords,
        )

        try:
            chunks = await hybrid_search_chunks(
                document_id=document_id,
                query=query,
                user_id=user_id,
                limit=limit,
                section_keywords=section_keywords,
                apply_mmr=True,
                strategy="hybrid"
            )

            if not chunks:
                logger.info("No chunks found for query: %s...", query[:50])
                return ChunkSearchResult(summary="No relevant content found.", hits=[])

            hits: List[ChunkHit] = []
            for chunk in chunks:
                metadata = chunk.get("metadata", {}) or {}
                section = metadata.get("section_title") or metadata.get("sectionTitle") or "Unknown Section"
                page = metadata.get("page_number") or metadata.get("pageNumber")
                score = chunk.get("score", 0.0)
                content = chunk.get("text") or chunk.get("content") or ""

                # Get doc_items for PDF highlighting (contains bounding boxes)
                doc_items = metadata.get("doc_items") or chunk.get("doc_items") or []

                max_content_length = 1500
                if len(content) > max_content_length:
                    content = content[:max_content_length] + "... [truncated]"

                hits.append(
                    ChunkHit(
                        chunk_id=metadata.get("chunk_id") or chunk.get("id"),
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


class SectionContent(BaseModel):
    section_title: str
    page_numbers: List[int]
    content: str
    chunk_count: int
    doc_items: Optional[List[dict]] = None  # Combined bounding boxes from all chunks


class SectionReadResult(BaseModel):
    summary: str
    section: Optional[SectionContent]


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
        """Read ALL content from a specific section of the document.

        Use this tool when you need to read an ENTIRE section at once, especially for:
        - Extracting complete lists (e.g., all strains in Methods)
        - Getting full tables or figures
        - Reading complete methodology details
        - Any case where you need comprehensive section content

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

            for chunk in chunks:
                # get_chunks_by_parent_section returns: text, chunk_index, section_title, parent_section, subsection, is_top_level, page_number, metadata, doc_items
                text = chunk.get("text") or chunk.get("content") or ""
                if text:
                    content_parts.append(text)

                # page_number is at top level, not in metadata
                page = chunk.get("page_number") or chunk.get("pageNumber")
                if page:
                    page_numbers.add(page)

                # section_title is at top level
                if not actual_section_title:
                    actual_section_title = chunk.get("section_title") or chunk.get("sectionTitle") or section_name

                # Collect doc_items for PDF highlighting
                metadata = chunk.get("metadata", {}) or {}
                # Handle case where metadata is JSON string from Weaviate
                if isinstance(metadata, str):
                    try:
                        metadata = json.loads(metadata)
                    except (json.JSONDecodeError, TypeError):
                        metadata = {}
                chunk_doc_items = metadata.get("doc_items") or chunk.get("doc_items") or []
                if chunk_doc_items:
                    all_doc_items.extend(chunk_doc_items)

            full_content = "\n\n".join(content_parts)
            sorted_pages = sorted(page_numbers) if page_numbers else []

            logger.info(
                "Read %s chunks from section '%s', pages %s, %s doc_items",
                len(chunks),
                actual_section_title,
                sorted_pages,
                len(all_doc_items),
            )

            return SectionReadResult(
                summary=f"Read {len(chunks)} chunks from '{actual_section_title}'",
                section=SectionContent(
                    section_title=actual_section_title,
                    page_numbers=sorted_pages,
                    content=full_content,
                    chunk_count=len(chunks),
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
        """Read content from a SPECIFIC SUBSECTION within a parent section.

        Use this for precise reading when you know the exact subsection you need.
        This respects the LLM-resolved document hierarchy for accurate boundaries.

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

            for chunk in chunks:
                text = chunk.get("text") or ""
                if text:
                    content_parts.append(text)

                page = chunk.get("page_number")
                if page:
                    page_numbers.add(page)

                doc_items = chunk.get("doc_items") or []
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
                summary=f"Read {len(chunks)} chunks from '{parent_section} > {subsection}'",
                subsection=SubsectionContent(
                    parent_section=parent_section,
                    subsection=subsection,
                    page_numbers=sorted_pages,
                    content=full_content,
                    chunk_count=len(chunks),
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
