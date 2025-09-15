"""
Chunk Manager Library
Handles semantic chunking of extracted PDF content with layout preservation
"""

import hashlib
import json
import time
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional, Any, Callable
from enum import Enum
import re


# ==================== Exceptions ====================


class ChunkManagerError(Exception):
    """Base exception for chunk manager errors"""

    pass


class InvalidDocumentError(ChunkManagerError):
    """Raised when document is invalid for chunking"""

    pass


# ==================== Enums ====================


class ChunkingStrategy(Enum):
    """Available chunking strategies"""

    SEMANTIC = "SEMANTIC"
    FIXED_SIZE = "FIXED_SIZE"
    SENTENCE_BASED = "SENTENCE_BASED"
    PARAGRAPH_BASED = "PARAGRAPH_BASED"


# ==================== Data Models ====================


@dataclass
class LayoutBlock:
    """Represents a layout block in a document"""

    type: str
    text: str
    bbox: Dict[str, float]


@dataclass
class ChunkBoundary:
    """Represents a chunk boundary"""

    chunk_index: int
    start_text: str
    end_text: str


@dataclass
class Chunk:
    """Represents a document chunk"""

    chunk_index: int
    text: str
    page_start: int
    page_end: int
    char_start: int
    char_end: int
    token_count: int
    chunk_hash: str
    pdf_id: Optional[str] = None
    section_path: Optional[str] = None
    layout_blocks: Optional[List[Dict[str, Any]]] = None
    is_reference: bool = False
    is_caption: bool = False
    contains_caption: bool = False
    is_header: bool = False


@dataclass
class ChunkResult:
    """Result of chunking operation"""

    chunks: List[Chunk]
    total_chunks: int
    chunking_strategy: ChunkingStrategy
    chunk_size: int
    overlap: int
    processing_time_ms: float

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {
            "chunks": [asdict(chunk) for chunk in self.chunks],
            "total_chunks": self.total_chunks,
            "chunking_strategy": self.chunking_strategy.value,
            "chunk_size": self.chunk_size,
            "overlap": self.overlap,
            "processing_time_ms": self.processing_time_ms,
        }

    def to_json(self) -> str:
        """Convert to JSON string"""
        return json.dumps(self.to_dict(), indent=2)


# ==================== Semantic Chunker ====================


class SemanticChunker:
    """Handles semantic chunking logic"""

    def __init__(self):
        self.sentence_endings = re.compile(r"[.!?]\s+")
        self.header_patterns = [
            re.compile(
                r"^(abstract|introduction|methods|results|discussion|conclusion|references)",
                re.IGNORECASE,
            ),
            re.compile(r"^\d+\.\s+\w+"),  # Numbered sections
            re.compile(r"^[A-Z][A-Z\s]+$"),  # All caps headers
        ]

    def find_sentence_boundary(self, text: str, target_pos: int) -> int:
        """Find the nearest sentence boundary to target position"""
        # If we're at the end of text, return it
        if target_pos >= len(text):
            return len(text)

        # Look for sentence ending near target
        search_start = max(0, target_pos - 50)
        search_end = min(len(text), target_pos + 100)

        # First, try to find a sentence boundary after target
        for i in range(target_pos, min(target_pos + 100, len(text))):
            if i > 0 and text[i - 1] in ".!?":
                # Check if followed by space or end of text
                if i >= len(text) or text[i].isspace():
                    # Skip whitespace after punctuation
                    while i < len(text) and text[i].isspace():
                        i += 1
                    return i

        # If no sentence boundary found, try to at least end at a word boundary
        # Look for the next space or newline
        for i in range(target_pos, min(target_pos + 50, len(text))):
            if text[i].isspace():
                return i

        # If still no good boundary, look backwards for a space
        for i in range(target_pos - 1, max(0, target_pos - 50), -1):
            if text[i].isspace():
                return i + 1

        # Last resort: return target position
        return target_pos

    def is_header(self, text: str) -> bool:
        """Check if text is likely a header"""
        text = text.strip()
        if not text:
            return False

        for pattern in self.header_patterns:
            if pattern.match(text):
                return True

        return False

    def find_paragraph_boundary(self, text: str, target_pos: int) -> int:
        """Find the nearest paragraph boundary to target position"""
        # Look for double newline near target
        search_start = max(0, target_pos - 100)
        search_end = min(len(text), target_pos + 100)

        # Find double newline
        for i in range(target_pos, search_end):
            if i < len(text) - 1 and text[i : i + 2] == "\n\n":
                return i + 2

        # Fall back to sentence boundary
        return self.find_sentence_boundary(text, target_pos)


# ==================== Main ChunkManager Class ====================


class ChunkManager:
    """
    Manages document chunking with various strategies
    """

    def __init__(self):
        self.semantic_chunker = SemanticChunker()

    def chunk(
        self,
        extraction_result: Any,  # ExtractionResult from pdf_processor
        chunk_size: int = 512,
        overlap: int = 50,
        strategy: ChunkingStrategy = ChunkingStrategy.SEMANTIC,
        preserve_layout: bool = False,
        mark_references: bool = False,
        group_captions: bool = False,
        semantic_boundaries: bool = True,
    ) -> ChunkResult:
        """
        Chunk extracted PDF content

        Args:
            extraction_result: Result from PDF extraction
            chunk_size: Target chunk size in tokens
            overlap: Overlap between chunks in tokens
            strategy: Chunking strategy to use
            preserve_layout: Whether to preserve layout information
            mark_references: Whether to mark reference sections
            group_captions: Whether to group captions with content
            semantic_boundaries: Whether to respect semantic boundaries

        Returns:
            ChunkResult with chunks and metadata
        """
        start_time = time.time()

        # Validate input
        if not extraction_result or not extraction_result.full_text:
            raise InvalidDocumentError("Empty document cannot be chunked")

        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")

        if overlap < 0:
            raise ValueError("overlap must be non-negative")

        if overlap >= chunk_size:
            raise ValueError("overlap must be smaller than chunk_size")

        # Extract text and metadata
        full_text = extraction_result.full_text
        pages = extraction_result.pages
        pdf_path = extraction_result.pdf_path

        # Choose chunking strategy
        if strategy == ChunkingStrategy.SEMANTIC:
            chunks = self._semantic_chunk(
                full_text,
                pages,
                chunk_size,
                overlap,
                preserve_layout,
                mark_references,
                group_captions,
                semantic_boundaries,
            )
        elif strategy == ChunkingStrategy.FIXED_SIZE:
            chunks = self._fixed_size_chunk(full_text, pages, chunk_size, overlap)
        elif strategy == ChunkingStrategy.SENTENCE_BASED:
            chunks = self._sentence_based_chunk(full_text, pages, chunk_size, overlap)
        elif strategy == ChunkingStrategy.PARAGRAPH_BASED:
            chunks = self._paragraph_based_chunk(full_text, pages, chunk_size, overlap)
        else:
            raise ValueError(f"Unknown strategy: {strategy}")

        # Add metadata to chunks
        for i, chunk in enumerate(chunks):
            chunk.chunk_index = i
            chunk.pdf_id = hashlib.md5(pdf_path.encode()).hexdigest()[:16]
            chunk.chunk_hash = hashlib.md5(chunk.text.encode()).hexdigest()[:32]

        processing_time_ms = (time.time() - start_time) * 1000

        return ChunkResult(
            chunks=chunks,
            total_chunks=len(chunks),
            chunking_strategy=strategy,
            chunk_size=chunk_size,
            overlap=overlap,
            processing_time_ms=processing_time_ms,
        )

    def _semantic_chunk(
        self,
        full_text: str,
        pages: List[Any],
        chunk_size: int,
        overlap: int,
        preserve_layout: bool,
        mark_references: bool,
        group_captions: bool,
        semantic_boundaries: bool,
    ) -> List[Chunk]:
        """Perform semantic chunking"""
        chunks = []
        current_pos = 0

        while current_pos < len(full_text):
            # Calculate chunk end position
            target_end = min(
                current_pos + chunk_size * 4, len(full_text)
            )  # Approximate chars

            # Find semantic boundary if enabled
            if semantic_boundaries and target_end < len(full_text):
                # Check for header
                lookahead = full_text[
                    target_end : min(target_end + 100, len(full_text))
                ]
                if self.semantic_chunker.is_header(lookahead.split("\n")[0]):
                    # Don't split before a header
                    target_end = full_text.rfind("\n", current_pos, target_end)
                    if target_end == -1:
                        target_end = min(current_pos + chunk_size * 4, len(full_text))
                else:
                    # Find paragraph or sentence boundary
                    target_end = self.semantic_chunker.find_paragraph_boundary(
                        full_text, target_end
                    )

            # Extract chunk text
            chunk_text = full_text[current_pos:target_end]

            # Determine pages and positions
            page_start, page_end = self._find_page_range(pages, current_pos, target_end)

            # Extract layout blocks if needed
            layout_blocks = None
            if preserve_layout:
                layout_blocks = self._extract_layout_blocks_for_range(
                    pages, current_pos, target_end
                )

            # Check for references
            is_reference = False
            if mark_references:
                is_reference = self._is_reference_section(chunk_text)

            # Check for captions
            is_caption, contains_caption = self._check_captions(chunk_text)

            # Determine section path
            section_path = self._determine_section_path(chunk_text, pages, current_pos)

            # Create chunk
            chunk = Chunk(
                chunk_index=len(chunks),
                text=chunk_text,
                page_start=page_start,
                page_end=page_end,
                char_start=current_pos,
                char_end=target_end,
                token_count=self._estimate_tokens(chunk_text),
                chunk_hash="",  # Will be set later
                section_path=section_path,
                layout_blocks=layout_blocks,
                is_reference=is_reference,
                is_caption=is_caption,
                contains_caption=contains_caption,
                is_header=self.semantic_chunker.is_header(chunk_text[:100]),
            )

            chunks.append(chunk)

            # Move position with overlap
            current_pos = (
                target_end - overlap if target_end < len(full_text) else target_end
            )

        return chunks

    def _fixed_size_chunk(
        self, full_text: str, pages: List[Any], chunk_size: int, overlap: int
    ) -> List[Chunk]:
        """Perform fixed-size chunking"""
        chunks = []
        current_pos = 0
        char_size = chunk_size * 4  # Approximate characters per token

        while current_pos < len(full_text):
            chunk_end = min(current_pos + char_size, len(full_text))
            chunk_text = full_text[current_pos:chunk_end]

            page_start, page_end = self._find_page_range(pages, current_pos, chunk_end)

            chunk = Chunk(
                chunk_index=len(chunks),
                text=chunk_text,
                page_start=page_start,
                page_end=page_end,
                char_start=current_pos,
                char_end=chunk_end,
                token_count=self._estimate_tokens(chunk_text),
                chunk_hash="",
                section_path=None,
                layout_blocks=None,
                is_reference=False,
                is_caption=False,
                contains_caption=False,
                is_header=False,
            )

            chunks.append(chunk)
            current_pos = (
                chunk_end - (overlap * 4) if chunk_end < len(full_text) else chunk_end
            )

        return chunks

    def _sentence_based_chunk(
        self, full_text: str, pages: List[Any], chunk_size: int, overlap: int
    ) -> List[Chunk]:
        """Perform sentence-based chunking"""
        chunks = []
        current_pos = 0
        char_size = chunk_size * 4

        while current_pos < len(full_text):
            target_end = min(current_pos + char_size, len(full_text))

            # Find sentence boundary
            if target_end < len(full_text):
                chunk_end = self.semantic_chunker.find_sentence_boundary(
                    full_text, target_end
                )
            else:
                chunk_end = target_end

            chunk_text = full_text[current_pos:chunk_end]
            page_start, page_end = self._find_page_range(pages, current_pos, chunk_end)

            chunk = Chunk(
                chunk_index=len(chunks),
                text=chunk_text,
                page_start=page_start,
                page_end=page_end,
                char_start=current_pos,
                char_end=chunk_end,
                token_count=self._estimate_tokens(chunk_text),
                chunk_hash="",
                section_path=None,
                layout_blocks=None,
                is_reference=False,
                is_caption=False,
                contains_caption=False,
                is_header=False,
            )

            chunks.append(chunk)
            current_pos = (
                chunk_end - (overlap * 4) if chunk_end < len(full_text) else chunk_end
            )

        return chunks

    def _paragraph_based_chunk(
        self, full_text: str, pages: List[Any], chunk_size: int, overlap: int
    ) -> List[Chunk]:
        """Perform paragraph-based chunking"""
        # Split by double newlines (paragraphs)
        paragraphs = full_text.split("\n\n")
        chunks = []
        current_text = ""
        current_start = 0

        for para in paragraphs:
            para_with_sep = para + "\n\n"

            # Check if adding this paragraph exceeds chunk size
            if (
                self._estimate_tokens(current_text + para_with_sep) > chunk_size
                and current_text
            ):
                # Create chunk with current text
                chunk_end = current_start + len(current_text)
                page_start, page_end = self._find_page_range(
                    pages, current_start, chunk_end
                )

                chunk = Chunk(
                    chunk_index=len(chunks),
                    text=current_text.strip(),
                    page_start=page_start,
                    page_end=page_end,
                    char_start=current_start,
                    char_end=chunk_end,
                    token_count=self._estimate_tokens(current_text),
                    chunk_hash="",
                    section_path=None,
                    layout_blocks=None,
                    is_reference=False,
                    is_caption=False,
                    contains_caption=False,
                    is_header=False,
                )

                chunks.append(chunk)

                # Start new chunk
                current_start = chunk_end - (overlap * 4)
                current_text = para_with_sep
            else:
                # Add to current chunk
                current_text += para_with_sep

        # Add final chunk if there's remaining text
        if current_text.strip():
            chunk_end = current_start + len(current_text)
            page_start, page_end = self._find_page_range(
                pages, current_start, chunk_end
            )

            chunk = Chunk(
                chunk_index=len(chunks),
                text=current_text.strip(),
                page_start=page_start,
                page_end=page_end,
                char_start=current_start,
                char_end=chunk_end,
                token_count=self._estimate_tokens(current_text),
                chunk_hash="",
                section_path=None,
                layout_blocks=None,
                is_reference=False,
                is_caption=False,
                contains_caption=False,
                is_header=False,
            )

            chunks.append(chunk)

        return chunks

    def analyze(
        self,
        chunk_result: ChunkResult,
        show_boundaries: bool = False,
        token_counts: bool = False,
    ) -> Dict[str, Any]:
        """
        Analyze chunk quality and distribution

        Args:
            chunk_result: Result from chunking operation
            show_boundaries: Whether to show chunk boundaries
            token_counts: Whether to analyze token distribution

        Returns:
            Analysis dictionary
        """
        analysis = {
            "total_chunks": chunk_result.total_chunks,
            "chunking_strategy": chunk_result.chunking_strategy.value,
            "avg_chunk_size": 0,
            "min_chunk_size": 0,
            "max_chunk_size": 0,
        }

        if chunk_result.chunks:
            sizes = [chunk.token_count for chunk in chunk_result.chunks]
            analysis["avg_chunk_size"] = sum(sizes) / len(sizes)
            analysis["min_chunk_size"] = min(sizes)
            analysis["max_chunk_size"] = max(sizes)

        if show_boundaries:
            boundaries = []
            for chunk in chunk_result.chunks:
                boundaries.append(
                    {
                        "chunk_index": chunk.chunk_index,
                        "start_text": (
                            chunk.text[:50] + "..."
                            if len(chunk.text) > 50
                            else chunk.text
                        ),
                        "end_text": (
                            "..." + chunk.text[-50:]
                            if len(chunk.text) > 50
                            else chunk.text
                        ),
                    }
                )
            analysis["chunk_boundaries"] = boundaries

        if token_counts:
            import statistics

            sizes = [chunk.token_count for chunk in chunk_result.chunks]
            if sizes:
                analysis["token_distribution"] = {
                    "mean": statistics.mean(sizes),
                    "median": statistics.median(sizes),
                    "std_dev": statistics.stdev(sizes) if len(sizes) > 1 else 0,
                    "percentiles": {
                        "25": (
                            statistics.quantiles(sizes, n=4)[0]
                            if len(sizes) > 1
                            else sizes[0]
                        ),
                        "50": statistics.median(sizes),
                        "75": (
                            statistics.quantiles(sizes, n=4)[2]
                            if len(sizes) > 1
                            else sizes[0]
                        ),
                    },
                }

        return analysis

    # ==================== Helper Methods ====================

    def _estimate_tokens(self, text: str) -> int:
        """Estimate token count (roughly 4 chars per token)"""
        return max(1, len(text) // 4)

    def _find_page_range(
        self, pages: List[Any], char_start: int, char_end: int
    ) -> tuple[int, int]:
        """Find which pages a text range spans"""
        if not pages:
            return 1, 1

        current_pos = 0
        page_start = 1
        page_end = 1

        for page in pages:
            page_text = page.text if hasattr(page, "text") else str(page)
            page_len = len(page_text)

            if current_pos <= char_start < current_pos + page_len:
                page_start = page.page_number if hasattr(page, "page_number") else 1

            if current_pos < char_end <= current_pos + page_len:
                page_end = page.page_number if hasattr(page, "page_number") else 1
                break

            current_pos += page_len

        return page_start, page_end

    def _extract_layout_blocks_for_range(
        self, pages: List[Any], char_start: int, char_end: int
    ) -> List[Dict[str, Any]]:
        """Extract layout blocks for a character range"""
        blocks = []
        current_pos = 0

        for page in pages:
            page_text = page.text if hasattr(page, "text") else str(page)
            page_len = len(page_text)

            # Check if this page overlaps with our range
            if current_pos + page_len > char_start and current_pos < char_end:
                if hasattr(page, "layout_blocks") and page.layout_blocks:
                    for block in page.layout_blocks:
                        blocks.append(block)

            current_pos += page_len

            if current_pos >= char_end:
                break

        return blocks

    def _is_reference_section(self, text: str) -> bool:
        """Check if text is part of references section"""
        text_lower = text.lower()[:200]  # Check beginning

        references_indicators = [
            "references",
            "bibliography",
            "works cited",
            "literature cited",
            "citations",
        ]

        for indicator in references_indicators:
            if indicator in text_lower:
                return True

        # Check for citation patterns
        if re.search(r"\[\d+\]|\(\d{4}\)|\d+\.\s+\w+", text[:500]):
            if text.count("[") > 5 or text.count("(19") + text.count("(20") > 3:
                return True

        return False

    def _check_captions(self, text: str) -> tuple[bool, bool]:
        """Check if text is or contains a caption"""
        text_lower = text.lower()

        caption_patterns = [
            r"table\s+\d+[:.]",
            r"figure\s+\d+[:.]",
            r"fig\.\s+\d+",
            r"tab\.\s+\d+",
        ]

        is_caption = False
        contains_caption = False

        for pattern in caption_patterns:
            if re.search(pattern, text_lower[:100]):
                is_caption = True
            if re.search(pattern, text_lower):
                contains_caption = True

        return is_caption, contains_caption

    def _determine_section_path(
        self, chunk_text: str, pages: List[Any], char_pos: int
    ) -> str:
        """Determine the section path for a chunk"""
        # Look for section headers in chunk
        lines = chunk_text.split("\n")
        for line in lines[:5]:  # Check first few lines
            line = line.strip()
            if self.semantic_chunker.is_header(line):
                return line

        # Default to generic section
        if self._is_reference_section(chunk_text):
            return "References"

        return "Main Content"


# ==================== CLI Interface ====================


class cli:
    """CLI interface for chunk_manager"""

    @staticmethod
    def chunk(
        pdf_path: str,
        chunk_size: int = 512,
        overlap: int = 50,
        strategy: str = "semantic",
        output: Optional[str] = None,
    ):
        """
        Chunk a PDF document

        Args:
            pdf_path: Path to PDF file
            chunk_size: Target chunk size
            overlap: Overlap between chunks
            strategy: Chunking strategy
            output: Output file path (optional)
        """
        # This would import pdf_processor and perform chunking
        # Implementation would be added when integrating with pdf_processor
        print(f"Chunking {pdf_path} with strategy {strategy}")
        print(f"Chunk size: {chunk_size}, Overlap: {overlap}")
        if output:
            print(f"Output will be saved to: {output}")

    @staticmethod
    def analyze(chunks_file: str):
        """
        Analyze chunks from a file

        Args:
            chunks_file: Path to chunks JSON file
        """
        print(f"Analyzing chunks from {chunks_file}")
