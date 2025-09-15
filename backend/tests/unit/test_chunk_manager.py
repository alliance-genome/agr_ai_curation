"""
Unit tests for Chunk Manager Library
Following TDD-RED phase - these tests should FAIL initially
Tests cover semantic chunking with layout preservation
"""

import pytest
from typing import List, Dict, Optional
from unittest.mock import Mock, patch, MagicMock
import hashlib

# These imports will fail initially (TDD-RED)
from lib.chunk_manager import (
    ChunkManager,
    ChunkResult,
    Chunk,
    ChunkingStrategy,
    LayoutBlock,
    ChunkBoundary,
    SemanticChunker,
    ChunkManagerError,
    InvalidDocumentError,
)
from lib.pdf_processor import ExtractionResult, PageContent


class TestChunkManager:
    """Test suite for Chunk Manager with semantic boundaries"""

    @pytest.fixture
    def manager(self):
        """Create a ChunkManager instance"""
        return ChunkManager()

    @pytest.fixture
    def sample_extraction_result(self):
        """Create a sample ExtractionResult for testing"""
        pages = [
            PageContent(
                page_number=1,
                text="Introduction\nThis is the first paragraph of the introduction. It contains important information about the study.",
                layout_blocks=[
                    {
                        "type": "header",
                        "text": "Introduction",
                        "bbox": {"x1": 0, "y1": 0, "x2": 100, "y2": 20},
                    },
                    {
                        "type": "paragraph",
                        "text": "This is the first paragraph...",
                        "bbox": {"x1": 0, "y1": 30, "x2": 100, "y2": 80},
                    },
                ],
            ),
            PageContent(
                page_number=2,
                text="Methods\nWe used the following methods in our research. First, we collected data from various sources.",
                layout_blocks=[
                    {
                        "type": "header",
                        "text": "Methods",
                        "bbox": {"x1": 0, "y1": 0, "x2": 100, "y2": 20},
                    },
                    {
                        "type": "paragraph",
                        "text": "We used the following methods...",
                        "bbox": {"x1": 0, "y1": 30, "x2": 100, "y2": 80},
                    },
                ],
            ),
            PageContent(
                page_number=3,
                text="Results are shown in Table 1.\nTable 1: Experimental Results\n[TABLE DATA]\nFigure 1: Graph showing trends",
                layout_blocks=[
                    {
                        "type": "paragraph",
                        "text": "Results are shown in Table 1.",
                        "bbox": {"x1": 0, "y1": 0, "x2": 100, "y2": 20},
                    },
                    {
                        "type": "table_caption",
                        "text": "Table 1: Experimental Results",
                        "bbox": {"x1": 0, "y1": 30, "x2": 100, "y2": 40},
                    },
                    {
                        "type": "table",
                        "text": "[TABLE DATA]",
                        "bbox": {"x1": 0, "y1": 50, "x2": 100, "y2": 100},
                    },
                    {
                        "type": "figure_caption",
                        "text": "Figure 1: Graph showing trends",
                        "bbox": {"x1": 0, "y1": 110, "x2": 100, "y2": 120},
                    },
                ],
            ),
        ]

        return ExtractionResult(
            pdf_path="/path/to/test.pdf",
            pages=pages,
            page_count=3,
            full_text="".join([p.text for p in pages]),
            extraction_time_ms=100.0,
            file_size_bytes=1024,
            metadata={"title": "Test Document"},
            tables=[],
            figures=[],
        )

    # ==================== BASIC CHUNKING TESTS ====================

    def test_chunk_with_default_settings(self, manager, sample_extraction_result):
        """Test basic chunking with default settings"""
        result = manager.chunk(extraction_result=sample_extraction_result)

        assert isinstance(result, ChunkResult)
        assert result.chunks is not None
        assert len(result.chunks) > 0
        assert result.total_chunks == len(result.chunks)
        assert result.chunking_strategy == ChunkingStrategy.SEMANTIC

    def test_chunk_with_size_and_overlap(self, manager, sample_extraction_result):
        """Test chunking with specific size and overlap"""
        result = manager.chunk(
            extraction_result=sample_extraction_result, chunk_size=100, overlap=20
        )

        for i, chunk in enumerate(result.chunks):
            assert chunk.token_count <= 100
            if i > 0:
                # Check overlap exists
                prev_chunk = result.chunks[i - 1]
                assert chunk.char_start < prev_chunk.char_end

    def test_preserve_layout_information(self, manager, sample_extraction_result):
        """Test that layout information is preserved in chunks"""
        result = manager.chunk(
            extraction_result=sample_extraction_result, preserve_layout=True
        )

        for chunk in result.chunks:
            assert chunk.layout_blocks is not None
            assert len(chunk.layout_blocks) > 0
            for block in chunk.layout_blocks:
                assert "type" in block
                assert "bbox" in block

    def test_mark_references_section(self, manager, sample_extraction_result):
        """Test that references section is marked appropriately"""
        # Add references section to test data
        ref_page = PageContent(
            page_number=4,
            text="References\n1. Smith et al. (2020)\n2. Jones et al. (2021)",
            layout_blocks=[
                {
                    "type": "header",
                    "text": "References",
                    "bbox": {"x1": 0, "y1": 0, "x2": 100, "y2": 20},
                },
                {
                    "type": "paragraph",
                    "text": "1. Smith et al...",
                    "bbox": {"x1": 0, "y1": 30, "x2": 100, "y2": 50},
                },
            ],
        )
        sample_extraction_result.pages.append(ref_page)
        # Update full_text to include the new page
        sample_extraction_result.full_text += ref_page.text

        result = manager.chunk(
            extraction_result=sample_extraction_result,
            chunk_size=50,  # Small chunks to ensure References gets its own chunk
            overlap=10,
            mark_references=True,
        )

        # Debug: print what chunks were created
        print(f"Total chunks: {len(result.chunks)}")
        for i, chunk in enumerate(result.chunks):
            print(
                f"Chunk {i}: is_reference={chunk.is_reference}, text_preview={chunk.text[:50]}"
            )

        # Find chunks containing references
        ref_chunks = [c for c in result.chunks if c.is_reference]
        assert len(ref_chunks) > 0
        for chunk in ref_chunks:
            assert chunk.is_reference is True

    def test_group_captions_with_content(self, manager, sample_extraction_result):
        """Test that captions are grouped with their associated content"""
        result = manager.chunk(
            extraction_result=sample_extraction_result, group_captions=True
        )

        # Find chunks containing captions
        for chunk in result.chunks:
            if "Table 1:" in chunk.text or "Figure 1:" in chunk.text:
                assert chunk.is_caption or chunk.contains_caption
                # Caption should not be split from its content
                if chunk.is_caption:
                    assert (
                        "[TABLE DATA]" in chunk.text
                        or "Graph showing trends" in chunk.text
                    )

    # ==================== SEMANTIC BOUNDARY TESTS ====================

    def test_semantic_boundaries_respect_sections(
        self, manager, sample_extraction_result
    ):
        """Test that semantic boundaries respect section headers"""
        result = manager.chunk(
            extraction_result=sample_extraction_result,
            preserve_layout=True,
            semantic_boundaries=True,
        )

        # Chunks should not split sections inappropriately
        for chunk in result.chunks:
            # If chunk contains a header, it should be at the beginning
            if any(
                block.get("type") == "header" for block in chunk.layout_blocks or []
            ):
                header_block = next(
                    b for b in chunk.layout_blocks if b.get("type") == "header"
                )
                assert chunk.text.startswith(header_block["text"])

    def test_semantic_boundaries_preserve_paragraphs(
        self, manager, sample_extraction_result
    ):
        """Test that semantic boundaries try to preserve paragraph integrity"""
        result = manager.chunk(
            extraction_result=sample_extraction_result,
            chunk_size=50,  # Small size to force splitting
            overlap=10,  # Small overlap for small chunks
            semantic_boundaries=True,
        )

        # Check that chunks preferably end at sentence boundaries
        for chunk in result.chunks:
            text = chunk.text.strip()
            if text and not chunk.is_reference:
                # Should preferably end with sentence-ending punctuation
                # (unless it's the last chunk or size constraint forces split)
                if chunk.chunk_index < result.total_chunks - 1:
                    assert text[-1] in ".!?\n" or len(text) >= 45  # Close to size limit

    def test_section_path_preservation(self, manager, sample_extraction_result):
        """Test that section paths are preserved in chunks"""
        result = manager.chunk(
            extraction_result=sample_extraction_result, preserve_layout=True
        )

        for chunk in result.chunks:
            assert chunk.section_path is not None
            # Section path should reflect document structure
            if "Introduction" in chunk.text:
                assert "Introduction" in chunk.section_path
            elif "Methods" in chunk.text:
                assert "Methods" in chunk.section_path
            elif "Results" in chunk.text:
                assert "Results" in chunk.section_path

    # ==================== CHUNK PROPERTIES TESTS ====================

    def test_chunk_hash_generation(self, manager, sample_extraction_result):
        """Test that each chunk has a unique hash"""
        result = manager.chunk(sample_extraction_result)

        hashes = [chunk.chunk_hash for chunk in result.chunks]
        assert len(hashes) == len(set(hashes))  # All hashes should be unique

        for chunk in result.chunks:
            assert chunk.chunk_hash is not None
            assert len(chunk.chunk_hash) == 32  # MD5 hash length

    def test_chunk_metadata(self, manager, sample_extraction_result):
        """Test that chunks contain proper metadata"""
        result = manager.chunk(sample_extraction_result)

        for chunk in result.chunks:
            assert chunk.chunk_index >= 0
            assert chunk.page_start > 0
            assert chunk.page_end >= chunk.page_start
            assert chunk.char_start >= 0
            assert chunk.char_end > chunk.char_start
            assert chunk.pdf_id is not None

    def test_chunk_token_counting(self, manager, sample_extraction_result):
        """Test that token counts are calculated correctly"""
        result = manager.chunk(
            extraction_result=sample_extraction_result, chunk_size=100
        )

        for chunk in result.chunks:
            assert chunk.token_count is not None
            assert chunk.token_count > 0
            assert chunk.token_count <= 100

    # ==================== ANALYSIS TESTS ====================

    def test_analyze_chunk_quality(self, manager, sample_extraction_result):
        """Test chunk quality analysis"""
        result = manager.chunk(sample_extraction_result)

        analysis = manager.analyze(
            chunk_result=result, show_boundaries=True, token_counts=True
        )

        assert analysis is not None
        assert "total_chunks" in analysis
        assert "avg_chunk_size" in analysis
        assert "min_chunk_size" in analysis
        assert "max_chunk_size" in analysis
        assert "chunk_boundaries" in analysis

        if analysis.get("chunk_boundaries"):
            for boundary in analysis["chunk_boundaries"]:
                assert "chunk_index" in boundary
                assert "start_text" in boundary
                assert "end_text" in boundary

    def test_analyze_token_distribution(self, manager, sample_extraction_result):
        """Test token distribution analysis"""
        result = manager.chunk(sample_extraction_result)

        analysis = manager.analyze(chunk_result=result, token_counts=True)

        assert "token_distribution" in analysis
        distribution = analysis["token_distribution"]
        assert "mean" in distribution
        assert "median" in distribution
        assert "std_dev" in distribution
        assert "percentiles" in distribution

    # ==================== STRATEGY TESTS ====================

    def test_fixed_size_chunking_strategy(self, manager, sample_extraction_result):
        """Test fixed-size chunking strategy"""
        result = manager.chunk(
            extraction_result=sample_extraction_result,
            strategy=ChunkingStrategy.FIXED_SIZE,
            chunk_size=100,
        )

        assert result.chunking_strategy == ChunkingStrategy.FIXED_SIZE
        for chunk in result.chunks[:-1]:  # Except last chunk
            # Should be close to target size
            assert 80 <= chunk.token_count <= 100

    def test_sentence_based_chunking_strategy(self, manager, sample_extraction_result):
        """Test sentence-based chunking strategy"""
        result = manager.chunk(
            extraction_result=sample_extraction_result,
            strategy=ChunkingStrategy.SENTENCE_BASED,
            chunk_size=100,
        )

        assert result.chunking_strategy == ChunkingStrategy.SENTENCE_BASED
        for chunk in result.chunks:
            # Should end at sentence boundaries
            text = chunk.text.strip()
            if text and chunk.chunk_index < result.total_chunks - 1:
                assert text[-1] in ".!?"

    def test_paragraph_based_chunking_strategy(self, manager, sample_extraction_result):
        """Test paragraph-based chunking strategy"""
        result = manager.chunk(
            extraction_result=sample_extraction_result,
            strategy=ChunkingStrategy.PARAGRAPH_BASED,
        )

        assert result.chunking_strategy == ChunkingStrategy.PARAGRAPH_BASED
        # Each chunk should roughly correspond to paragraphs

    # ==================== ERROR HANDLING TESTS ====================

    def test_empty_document_handling(self, manager):
        """Test handling of empty documents"""
        empty_result = ExtractionResult(
            pdf_path="/path/to/empty.pdf",
            pages=[],
            page_count=0,
            full_text="",
            extraction_time_ms=10.0,
            file_size_bytes=0,
            metadata={},
            tables=[],
            figures=[],
        )

        with pytest.raises(InvalidDocumentError) as exc_info:
            manager.chunk(empty_result)

        assert "empty document" in str(exc_info.value).lower()

    def test_invalid_chunk_size(self, manager, sample_extraction_result):
        """Test handling of invalid chunk sizes"""
        with pytest.raises(ValueError) as exc_info:
            manager.chunk(extraction_result=sample_extraction_result, chunk_size=0)

        assert "chunk_size must be positive" in str(exc_info.value).lower()

    def test_invalid_overlap_size(self, manager, sample_extraction_result):
        """Test handling of invalid overlap sizes"""
        with pytest.raises(ValueError) as exc_info:
            manager.chunk(
                extraction_result=sample_extraction_result,
                chunk_size=100,
                overlap=150,  # Overlap larger than chunk size
            )

        assert "overlap must be smaller than chunk_size" in str(exc_info.value).lower()

    # ==================== INTEGRATION TESTS ====================

    def test_full_chunking_pipeline(self, manager, sample_extraction_result):
        """Test complete chunking pipeline with all features"""
        result = manager.chunk(
            extraction_result=sample_extraction_result,
            chunk_size=100,
            overlap=20,
            preserve_layout=True,
            mark_references=True,
            group_captions=True,
            semantic_boundaries=True,
        )

        assert result is not None
        assert result.total_chunks > 0
        assert result.processing_time_ms > 0

        # Verify all features are applied
        has_layout = any(c.layout_blocks for c in result.chunks)
        has_references = any(c.is_reference for c in result.chunks)
        has_captions = any(c.is_caption or c.contains_caption for c in result.chunks)

        assert has_layout
        # These assertions depend on content
        if "References" in sample_extraction_result.full_text:
            assert has_references
        if "Table 1:" in sample_extraction_result.full_text:
            assert has_captions

    def test_deterministic_chunking(self, manager, sample_extraction_result):
        """Test that chunking is deterministic"""
        result1 = manager.chunk(
            extraction_result=sample_extraction_result, chunk_size=100, overlap=20
        )

        result2 = manager.chunk(
            extraction_result=sample_extraction_result, chunk_size=100, overlap=20
        )

        assert len(result1.chunks) == len(result2.chunks)
        for c1, c2 in zip(result1.chunks, result2.chunks):
            assert c1.text == c2.text
            assert c1.chunk_hash == c2.chunk_hash


class TestChunkResult:
    """Test the ChunkResult data model"""

    def test_chunk_result_serialization(self):
        """Test that ChunkResult can be serialized"""
        chunks = [
            Chunk(
                chunk_index=0,
                text="Sample chunk text",
                page_start=1,
                page_end=1,
                char_start=0,
                char_end=17,
                token_count=3,
                chunk_hash="abc123",
                section_path="Introduction",
                is_reference=False,
                is_caption=False,
                is_header=False,
            )
        ]

        result = ChunkResult(
            chunks=chunks,
            total_chunks=1,
            chunking_strategy=ChunkingStrategy.SEMANTIC,
            chunk_size=100,
            overlap=20,
            processing_time_ms=50.5,
        )

        # Should be able to convert to dict
        result_dict = result.to_dict()
        assert result_dict["total_chunks"] == 1
        assert result_dict["chunking_strategy"] == "SEMANTIC"
        assert len(result_dict["chunks"]) == 1

        # Should be able to convert to JSON
        import json

        result_json = result.to_json()
        assert isinstance(result_json, str)
        parsed = json.loads(result_json)
        assert parsed["total_chunks"] == 1


class TestCLIInterface:
    """Test the CLI interface for chunk_manager"""

    def test_cli_chunk_command(self):
        """Test CLI chunk command structure"""
        from lib.chunk_manager import cli

        # Should have chunk command
        assert hasattr(cli, "chunk")
        assert callable(cli.chunk)

    def test_cli_analyze_command(self):
        """Test CLI analyze command structure"""
        from lib.chunk_manager import cli

        # Should have analyze command
        assert hasattr(cli, "analyze")
        assert callable(cli.analyze)
