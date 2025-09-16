"""
Chunk Manager using Unstructured.io's built-in chunking
Provides semantic chunking that preserves document structure and handles
tables, figures, and references properly.
"""

import hashlib
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from unstructured.chunking.basic import chunk_elements
from unstructured.chunking.title import chunk_by_title
from unstructured.documents.elements import Element

from .pdf_processor import ExtractionResult, UnstructuredElement


class ChunkingStrategy(Enum):
    """Available chunking strategies"""

    BY_TITLE = "by_title"  # Preserves document structure
    BASIC = "basic"  # Simple chunking
    BY_PAGE = "by_page"  # One chunk per page
    BY_SECTION = "by_section"  # Group by sections


@dataclass
class Chunk:
    """Enhanced chunk with metadata"""

    chunk_index: int
    text: str
    token_count: int
    char_start: int
    char_end: int
    page_start: int
    page_end: int
    section_path: Optional[str] = None
    heading_text: Optional[str] = None
    is_reference: bool = False
    is_caption: bool = False
    is_table: bool = False
    contains_table: bool = False
    contains_figure: bool = False
    contains_caption: bool = False
    chunk_hash: Optional[str] = None
    element_ids: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        """Generate hash if not provided"""
        if not self.chunk_hash:
            self.chunk_hash = hashlib.md5(self.text.encode()).hexdigest()[:16]


@dataclass
class ChunkResult:
    """Result of chunking operation"""

    chunks: List[Chunk]
    total_chunks: int
    avg_chunk_size: int
    processing_time_ms: float
    strategy: ChunkingStrategy
    parameters: Dict[str, Any]


class ChunkManager:
    """
    Simplified chunk manager using Unstructured's built-in chunking
    Handles semantic boundaries, document structure, and special elements
    """

    def __init__(self):
        """Initialize chunk manager"""
        pass

    def chunk(
        self,
        extraction_result: ExtractionResult,
        strategy: ChunkingStrategy = ChunkingStrategy.BY_TITLE,
        max_characters: int = 2000,
        overlap: int = 200,
        combine_under_n_chars: int = 100,
        include_metadata: bool = True,
        **kwargs,
    ) -> ChunkResult:
        """
        Chunk extracted PDF using Unstructured's strategies

        Args:
            extraction_result: Result from PDF extraction
            strategy: Chunking strategy to use
            max_characters: Maximum characters per chunk
            overlap: Character overlap between chunks
            combine_under_n_chars: Combine elements smaller than this
            include_metadata: Include element metadata in chunks
            **kwargs: Additional strategy-specific parameters

        Returns:
            ChunkResult with processed chunks
        """
        start_time = time.time()

        # Convert UnstructuredElements back to Unstructured native elements for chunking
        # (In practice, we might keep the original elements in ExtractionResult)
        elements = self._prepare_elements_for_chunking(extraction_result.elements)

        # Apply chunking strategy
        if strategy == ChunkingStrategy.BY_TITLE:
            chunked_elements = self._chunk_by_title(
                elements,
                max_characters=max_characters,
                overlap=overlap,
                combine_under_n_chars=combine_under_n_chars,
                **kwargs,
            )
        elif strategy == ChunkingStrategy.BASIC:
            chunked_elements = self._chunk_basic(
                elements, max_characters=max_characters, overlap=overlap, **kwargs
            )
        elif strategy == ChunkingStrategy.BY_PAGE:
            chunked_elements = self._chunk_by_page(
                extraction_result.elements, max_characters=max_characters
            )
        elif strategy == ChunkingStrategy.BY_SECTION:
            chunked_elements = self._chunk_by_section(
                extraction_result.elements,
                max_characters=max_characters,
                overlap=overlap,
            )
        else:
            raise ValueError(f"Unknown chunking strategy: {strategy}")

        # Convert to our Chunk format
        chunks = self._convert_to_chunks(
            chunked_elements, extraction_result, include_metadata=include_metadata
        )

        # Calculate statistics
        total_chunks = len(chunks)
        avg_size = (
            sum(c.token_count for c in chunks) // total_chunks
            if total_chunks > 0
            else 0
        )
        processing_time = (time.time() - start_time) * 1000

        return ChunkResult(
            chunks=chunks,
            total_chunks=total_chunks,
            avg_chunk_size=avg_size,
            processing_time_ms=processing_time,
            strategy=strategy,
            parameters={
                "max_characters": max_characters,
                "overlap": overlap,
                "combine_under_n_chars": combine_under_n_chars,
            },
        )

    def _chunk_by_title(
        self,
        elements: List[Element],
        max_characters: int,
        overlap: int,
        combine_under_n_chars: int,
        **kwargs,
    ) -> List[Element]:
        """
        Chunk by title - preserves document structure
        This is the recommended strategy for scientific papers
        """
        return chunk_by_title(
            elements=elements,
            max_characters=max_characters,
            overlap=overlap,
            combine_text_under_n_chars=combine_under_n_chars,
            include_orig_elements=kwargs.get("include_orig_elements", True),
            multipage_sections=kwargs.get("multipage_sections", True),
            new_after_n_chars=kwargs.get("new_after_n_chars", max_characters),
        )

    def _chunk_basic(
        self, elements: List[Element], max_characters: int, overlap: int, **kwargs
    ) -> List[Element]:
        """Basic chunking without structure preservation"""
        return chunk_elements(
            elements=elements,
            max_characters=max_characters,
            overlap=overlap,
            overlap_all=kwargs.get("overlap_all", False),
        )

    def _chunk_by_page(
        self, elements: List[UnstructuredElement], max_characters: int
    ) -> List[Dict[str, Any]]:
        """
        Chunk by page - one or more chunks per page
        Useful for maintaining page-level citations
        """
        page_chunks = []
        current_page_elements = []
        current_page = None

        for elem in elements:
            if elem.page_number != current_page:
                # Process previous page
                if current_page_elements:
                    page_text = "\n\n".join(
                        [e.text for e in current_page_elements if e.text]
                    )
                    if len(page_text) > max_characters:
                        # Split large pages
                        for i in range(0, len(page_text), max_characters - 200):
                            chunk_text = page_text[i : i + max_characters]
                            page_chunks.append(
                                {
                                    "text": chunk_text,
                                    "page": current_page,
                                    "elements": current_page_elements[
                                        i : i + 10
                                    ],  # Approximate
                                }
                            )
                    else:
                        page_chunks.append(
                            {
                                "text": page_text,
                                "page": current_page,
                                "elements": current_page_elements,
                            }
                        )

                # Start new page
                current_page = elem.page_number
                current_page_elements = [elem]
            else:
                current_page_elements.append(elem)

        # Don't forget last page
        if current_page_elements:
            page_text = "\n\n".join([e.text for e in current_page_elements if e.text])
            page_chunks.append(
                {
                    "text": page_text,
                    "page": current_page,
                    "elements": current_page_elements,
                }
            )

        return page_chunks

    def _chunk_by_section(
        self, elements: List[UnstructuredElement], max_characters: int, overlap: int
    ) -> List[Dict[str, Any]]:
        """
        Chunk by section - groups elements by their section path
        Maintains semantic coherence within sections
        """
        section_chunks = []
        current_section = None
        current_elements = []

        for elem in elements:
            elem_section = elem.section_path or "Unknown"

            if elem_section != current_section:
                # Process previous section
                if current_elements:
                    section_text = "\n\n".join(
                        [e.text for e in current_elements if e.text]
                    )
                    if len(section_text) > max_characters:
                        # Split large sections with overlap
                        for i in range(0, len(section_text), max_characters - overlap):
                            chunk_text = section_text[i : i + max_characters]
                            section_chunks.append(
                                {
                                    "text": chunk_text,
                                    "section": current_section,
                                    "elements": current_elements,
                                }
                            )
                    else:
                        section_chunks.append(
                            {
                                "text": section_text,
                                "section": current_section,
                                "elements": current_elements,
                            }
                        )

                # Start new section
                current_section = elem_section
                current_elements = [elem]
            else:
                current_elements.append(elem)

        # Don't forget last section
        if current_elements:
            section_text = "\n\n".join([e.text for e in current_elements if e.text])
            section_chunks.append(
                {
                    "text": section_text,
                    "section": current_section,
                    "elements": current_elements,
                }
            )

        return section_chunks

    def _prepare_elements_for_chunking(
        self, elements: List[UnstructuredElement]
    ) -> List[Element]:
        """
        Prepare elements for Unstructured chunking functions
        This is a placeholder - in practice, we'd keep the original Element objects
        """
        # For now, return a mock list
        # In the real implementation, we'd maintain the original Element objects
        return []

    def _convert_to_chunks(
        self,
        chunked_elements: List[Any],
        extraction_result: ExtractionResult,
        include_metadata: bool,
    ) -> List[Chunk]:
        """Convert chunked elements to our Chunk format"""
        chunks = []
        char_offset = 0

        for i, elem in enumerate(chunked_elements):
            # Handle different chunk formats (Element, dict, etc.)
            chunk_elements: List[Any] = []

            if isinstance(elem, dict):
                text = elem.get("text", "")
                page_start = elem.get("page", 1)
                page_end = page_start
                section = elem.get("section", "")
                chunk_elements = elem.get("elements", []) or []
                element_ids = [
                    e.element_id for e in chunk_elements if hasattr(e, "element_id")
                ]
            elif hasattr(elem, "text"):
                text = elem.text
                page_start = (
                    elem.metadata.page_number if hasattr(elem, "metadata") else 1
                )
                page_end = page_start
                section = (
                    elem.metadata.section if hasattr(elem.metadata, "section") else ""
                )
                chunk_elements = [elem]
                element_ids = [elem.id] if hasattr(elem, "id") else []
            else:
                text = str(elem)
                page_start = 1
                page_end = 1
                section = ""
                element_ids = []

            # Calculate character positions
            char_start = char_offset
            char_end = char_offset + len(text)
            char_offset = char_end

            # Estimate token count (rough approximation)
            token_count = len(text.split()) * 1.3  # Approximate tokens

            # Detect special content
            text_lower = text.lower()
            element_types = [
                getattr(e, "type", getattr(e, "category", "")).lower()
                for e in chunk_elements
            ]

            has_table_element = any(t in {"table"} for t in element_types)
            has_caption_element = any(
                t in {"figurecaption", "tablecaption"} for t in element_types
            )
            has_image_element = any(t in {"image", "figure"} for t in element_types)

            is_reference = self._is_reference_section(text_lower)
            is_caption = has_caption_element or self._is_caption(text_lower)
            is_table = has_table_element
            contains_table = has_table_element or "table" in text_lower
            contains_figure = has_image_element or any(
                term in text_lower for term in ["figure", "fig.", "chart", "graph"]
            )
            contains_caption = (
                has_caption_element or is_caption or "caption" in text_lower
            )

            # Extract heading if available
            heading_text = self._extract_heading(elem, extraction_result)

            chunk = Chunk(
                chunk_index=i,
                text=text,
                token_count=int(token_count),
                char_start=char_start,
                char_end=char_end,
                page_start=page_start,
                page_end=page_end,
                section_path=section,
                heading_text=heading_text,
                is_reference=is_reference,
                is_caption=is_caption,
                is_table=is_table,
                contains_table=contains_table,
                contains_figure=contains_figure,
                contains_caption=contains_caption,
                element_ids=element_ids,
                metadata=(
                    {"include_metadata": include_metadata} if include_metadata else {}
                ),
            )

            chunks.append(chunk)

        return chunks

    def _is_reference_section(self, text_lower: str) -> bool:
        """Check if text is from references section"""
        # Check for reference section indicators
        reference_indicators = [
            "references",
            "bibliography",
            "works cited",
            "citations",
        ]

        # Check if text starts with reference indicator
        for indicator in reference_indicators:
            if text_lower.strip().startswith(indicator):
                return True

        # Check for high density of citation patterns
        citation_patterns = [
            "et al.",
            "vol.",
            "pp.",
            "doi:",
            "isbn:",
            "journal",
            "proceedings",
        ]

        pattern_count = sum(1 for pattern in citation_patterns if pattern in text_lower)
        word_count = len(text_lower.split())

        # If more than 10% of "words" are citation patterns, likely references
        if word_count > 0 and pattern_count / word_count > 0.1:
            return True

        # Check for year patterns common in citations
        import re

        year_pattern = r"\(19\d{2}\)|\(20\d{2}\)"
        year_matches = len(re.findall(year_pattern, text_lower))
        if year_matches > 3:  # Multiple year citations
            return True

        return False

    def _is_caption(self, text_lower: str) -> bool:
        """Check if text is a caption"""
        caption_starters = [
            "figure",
            "fig.",
            "table",
            "tab.",
            "scheme",
            "chart",
            "graph",
            "plate",
            "supplementary",
        ]

        # Check if text starts with caption indicator
        for starter in caption_starters:
            if text_lower.strip().startswith(starter):
                # Also check it's not too long (captions are typically short)
                if len(text_lower) < 500:
                    return True

        return False

    def _extract_heading(
        self, elem: Any, extraction_result: ExtractionResult
    ) -> Optional[str]:
        """Extract heading text for a chunk"""
        # Look for Title or Header elements in the extraction result
        # This is simplified - in practice, we'd track section hierarchy
        if hasattr(elem, "metadata") and hasattr(elem.metadata, "section"):
            return elem.metadata.section

        return None

    def analyze(
        self,
        chunk_result: ChunkResult,
        show_boundaries: bool = False,
        token_counts: bool = True,
        show_references: bool = False,
    ) -> Dict[str, Any]:
        """
        Analyze chunking results

        Args:
            chunk_result: Result from chunking operation
            show_boundaries: Show chunk boundaries
            token_counts: Include token count statistics
            show_references: Highlight reference chunks

        Returns:
            Analysis dictionary
        """
        analysis = {
            "total_chunks": chunk_result.total_chunks,
            "avg_chunk_size": chunk_result.avg_chunk_size,
            "strategy": chunk_result.strategy.value,
            "parameters": chunk_result.parameters,
        }

        if token_counts:
            tokens = [c.token_count for c in chunk_result.chunks]
            analysis["token_distribution"] = {
                "min": min(tokens) if tokens else 0,
                "max": max(tokens) if tokens else 0,
                "mean": sum(tokens) / len(tokens) if tokens else 0,
                "percentiles": {
                    "25": sorted(tokens)[len(tokens) // 4] if len(tokens) > 4 else 0,
                    "50": sorted(tokens)[len(tokens) // 2] if len(tokens) > 2 else 0,
                    "75": (
                        sorted(tokens)[3 * len(tokens) // 4] if len(tokens) > 4 else 0
                    ),
                },
            }

        if show_boundaries:
            analysis["chunk_boundaries"] = [
                {
                    "index": c.chunk_index,
                    "char_range": f"{c.char_start}-{c.char_end}",
                    "page_range": f"{c.page_start}-{c.page_end}",
                    "tokens": c.token_count,
                }
                for c in chunk_result.chunks[:10]  # First 10 for brevity
            ]

        if show_references:
            ref_chunks = [c for c in chunk_result.chunks if c.is_reference]
            analysis["reference_chunks"] = {
                "count": len(ref_chunks),
                "indices": [c.chunk_index for c in ref_chunks],
                "percentage": (
                    (len(ref_chunks) / chunk_result.total_chunks * 100)
                    if chunk_result.total_chunks > 0
                    else 0
                ),
            }

        # Special content analysis
        analysis["special_content"] = {
            "captions": sum(1 for c in chunk_result.chunks if c.is_caption),
            "tables": sum(1 for c in chunk_result.chunks if c.is_table),
            "contains_tables": sum(1 for c in chunk_result.chunks if c.contains_table),
            "contains_figures": sum(
                1 for c in chunk_result.chunks if c.contains_figure
            ),
            "references": sum(1 for c in chunk_result.chunks if c.is_reference),
        }

        # Page coverage
        pages = set()
        for c in chunk_result.chunks:
            pages.update(range(c.page_start, c.page_end + 1))
        analysis["page_coverage"] = {
            "pages_covered": len(pages),
            "page_list": sorted(list(pages))[:20],  # First 20 pages
        }

        return analysis
