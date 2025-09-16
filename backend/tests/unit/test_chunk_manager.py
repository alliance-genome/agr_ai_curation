"""
Unit tests for Chunk Manager using Unstructured.io
Tests chunking strategies with the new implementation
"""

import pytest
from unittest.mock import Mock, patch, MagicMock

from lib.chunk_manager import ChunkManager, ChunkingStrategy, Chunk, ChunkResult
from lib.pdf_processor import ExtractionResult, UnstructuredElement


class TestChunkManagerUnstructured:
    """Test suite for Unstructured chunk manager"""

    @pytest.fixture
    def manager(self):
        """Create chunk manager instance"""
        return ChunkManager()

    @pytest.fixture
    def mock_extraction_result(self):
        """Create mock extraction result"""
        elements = []

        # Title element
        elem1 = UnstructuredElement(
            type="Title",
            text="Introduction to Neural Networks",
            metadata={"page_number": 1},
            element_id="elem_1",
            page_number=1,
            section_path="Introduction",
        )
        elements.append(elem1)

        # Narrative text
        elem2 = UnstructuredElement(
            type="NarrativeText",
            text="Neural networks are computational models inspired by biological neural networks. "
            * 20,
            metadata={"page_number": 1},
            element_id="elem_2",
            page_number=1,
            section_path="Introduction",
        )
        elements.append(elem2)

        # Another title
        elem3 = UnstructuredElement(
            type="Title",
            text="Methods",
            metadata={"page_number": 2},
            element_id="elem_3",
            page_number=2,
            section_path="Methods",
        )
        elements.append(elem3)

        # Methods text
        elem4 = UnstructuredElement(
            type="NarrativeText",
            text="We implemented a deep learning model using PyTorch. " * 30,
            metadata={"page_number": 2},
            element_id="elem_4",
            page_number=2,
            section_path="Methods",
        )
        elements.append(elem4)

        # Table
        elem5 = UnstructuredElement(
            type="Table",
            text="Model\tAccuracy\nCNN\t95%\nRNN\t92%",
            metadata={"page_number": 3, "text_as_html": "<table></table>"},
            element_id="elem_5",
            page_number=3,
            section_path="Results",
        )
        elements.append(elem5)

        # Figure caption
        elem6 = UnstructuredElement(
            type="FigureCaption",
            text="Figure 1: Model architecture diagram",
            metadata={"page_number": 3},
            element_id="elem_6",
            page_number=3,
            section_path="Results",
        )
        elements.append(elem6)

        # References
        elem7 = UnstructuredElement(
            type="Title",
            text="References",
            metadata={"page_number": 4},
            element_id="elem_7",
            page_number=4,
            section_path="References",
        )
        elements.append(elem7)

        elem8 = UnstructuredElement(
            type="NarrativeText",
            text="1. Smith et al. (2023). Deep Learning. Journal of AI. vol. 10, pp. 1-10.",
            metadata={"page_number": 4},
            element_id="elem_8",
            page_number=4,
            section_path="References",
        )
        elements.append(elem8)

        full_text = "\n\n".join([e.text for e in elements])

        return ExtractionResult(
            pdf_path="test.pdf",
            elements=elements,
            page_count=4,
            full_text=full_text,
            metadata={"title": "Neural Networks"},
            tables=[],
            figures=[],
            extraction_time_ms=100,
            file_size_bytes=100000,
            processing_strategy="fast",
        )

    # ==================== CHUNKING STRATEGY TESTS ====================

    @patch("lib.chunk_manager.chunk_by_title")
    def test_chunk_by_title_strategy(
        self, mock_chunk_fn, manager, mock_extraction_result
    ):
        """Test chunking by title strategy"""
        # Mock the Unstructured chunking function
        mock_chunk_fn.return_value = []

        result = manager.chunk(
            mock_extraction_result,
            strategy=ChunkingStrategy.BY_TITLE,
            max_characters=500,
            overlap=50,
        )

        assert isinstance(result, ChunkResult)
        assert result.strategy == ChunkingStrategy.BY_TITLE
        mock_chunk_fn.assert_called_once()

    @patch("lib.chunk_manager.chunk_elements")
    def test_chunk_basic_strategy(self, mock_chunk_fn, manager, mock_extraction_result):
        """Test basic chunking strategy"""
        mock_chunk_fn.return_value = []

        result = manager.chunk(
            mock_extraction_result, strategy=ChunkingStrategy.BASIC, max_characters=500
        )

        assert result.strategy == ChunkingStrategy.BASIC
        mock_chunk_fn.assert_called_once()

    def test_chunk_by_page_strategy(self, manager, mock_extraction_result):
        """Test chunking by page"""
        result = manager.chunk(
            mock_extraction_result,
            strategy=ChunkingStrategy.BY_PAGE,
            max_characters=1000,
        )

        assert result.strategy == ChunkingStrategy.BY_PAGE
        # Should have chunks for each page with content
        assert result.total_chunks >= 4

    def test_chunk_by_section_strategy(self, manager, mock_extraction_result):
        """Test chunking by section"""
        result = manager.chunk(
            mock_extraction_result,
            strategy=ChunkingStrategy.BY_SECTION,
            max_characters=500,
        )

        assert result.strategy == ChunkingStrategy.BY_SECTION
        # Should have chunks for each section
        assert result.total_chunks >= 3  # Introduction, Methods, Results, References

    # ==================== CHUNK PROPERTIES TESTS ====================

    def test_chunk_properties(self, manager, mock_extraction_result):
        """Test that chunks have correct properties"""
        result = manager.chunk(
            mock_extraction_result,
            strategy=ChunkingStrategy.BY_PAGE,
            max_characters=1000,
        )

        for chunk in result.chunks:
            assert isinstance(chunk, Chunk)
            assert chunk.chunk_index >= 0
            assert chunk.text
            assert chunk.token_count > 0
            assert chunk.char_start >= 0
            assert chunk.char_end > chunk.char_start
            assert chunk.page_start > 0
            assert chunk.page_end >= chunk.page_start
            assert chunk.chunk_hash is not None

    def test_chunk_special_content_detection(self, manager, mock_extraction_result):
        """Test detection of special content in chunks"""
        result = manager.chunk(
            mock_extraction_result, strategy=ChunkingStrategy.BY_PAGE
        )

        # Check for reference detection
        ref_chunks = [c for c in result.chunks if c.is_reference]
        assert len(ref_chunks) > 0, "Should detect reference chunks"

        # Check for caption detection
        caption_chunks = [c for c in result.chunks if c.contains_caption]
        assert len(caption_chunks) > 0, "Should detect caption chunks"

        # Check for table detection
        table_chunks = [c for c in result.chunks if c.contains_table]
        assert any(table_chunks), "Should detect table chunks"

    def test_chunk_size_limits(self, manager, mock_extraction_result):
        """Test that chunks respect size limits"""
        max_chars = 200
        result = manager.chunk(
            mock_extraction_result,
            strategy=ChunkingStrategy.BY_SECTION,
            max_characters=max_chars,
            overlap=20,
        )

        for chunk in result.chunks:
            # Allow some tolerance for word boundaries
            assert len(chunk.text) <= max_chars * 1.2

    # ==================== ANALYSIS TESTS ====================

    def test_analyze_basic(self, manager, mock_extraction_result):
        """Test basic chunk analysis"""
        chunk_result = manager.chunk(mock_extraction_result)
        analysis = manager.analyze(chunk_result)

        assert "total_chunks" in analysis
        assert "avg_chunk_size" in analysis
        assert "strategy" in analysis
        assert "parameters" in analysis

    def test_analyze_with_token_distribution(self, manager, mock_extraction_result):
        """Test analysis with token distribution"""
        chunk_result = manager.chunk(mock_extraction_result)
        analysis = manager.analyze(chunk_result, token_counts=True)

        assert "token_distribution" in analysis
        dist = analysis["token_distribution"]
        assert "min" in dist
        assert "max" in dist
        assert "mean" in dist
        assert "percentiles" in dist

    def test_analyze_with_boundaries(self, manager, mock_extraction_result):
        """Test analysis with chunk boundaries"""
        chunk_result = manager.chunk(mock_extraction_result)
        analysis = manager.analyze(chunk_result, show_boundaries=True)

        assert "chunk_boundaries" in analysis
        boundaries = analysis["chunk_boundaries"]
        assert len(boundaries) <= 10  # Limited to first 10
        if boundaries:
            assert "char_range" in boundaries[0]
            assert "page_range" in boundaries[0]

    def test_analyze_with_references(self, manager, mock_extraction_result):
        """Test analysis with reference highlighting"""
        chunk_result = manager.chunk(mock_extraction_result)
        analysis = manager.analyze(chunk_result, show_references=True)

        assert "reference_chunks" in analysis
        ref_info = analysis["reference_chunks"]
        assert "count" in ref_info
        assert "indices" in ref_info
        assert "percentage" in ref_info

    def test_analyze_special_content(self, manager, mock_extraction_result):
        """Test special content analysis"""
        chunk_result = manager.chunk(
            mock_extraction_result, strategy=ChunkingStrategy.BY_PAGE
        )
        analysis = manager.analyze(chunk_result)

        assert "special_content" in analysis
        special = analysis["special_content"]
        assert special["captions"] > 0
        assert special["tables"] > 0
        assert special["references"] > 0

    def test_chunk_marks_table_elements(self, manager, mock_extraction_result):
        """Tables should be flagged even when short"""
        chunk_result = manager.chunk(
            mock_extraction_result, strategy=ChunkingStrategy.BY_PAGE
        )

        table_chunk = next(
            chunk for chunk in chunk_result.chunks if "Model\tAccuracy" in chunk.text
        )

        assert table_chunk.contains_table is True
        assert table_chunk.is_table is True

    def test_analyze_page_coverage(self, manager, mock_extraction_result):
        """Test page coverage analysis"""
        chunk_result = manager.chunk(mock_extraction_result)
        analysis = manager.analyze(chunk_result)

        assert "page_coverage" in analysis
        coverage = analysis["page_coverage"]
        assert "pages_covered" in coverage
        assert "page_list" in coverage

    # ==================== HELPER METHOD TESTS ====================

    def test_is_reference_section(self, manager):
        """Test reference section detection"""
        ref_text = (
            "References\n1. Smith et al. (2023). Title. Journal. vol. 1, pp. 1-10."
        )
        assert manager._is_reference_section(ref_text.lower())

        non_ref_text = "This is regular text about neural networks."
        assert not manager._is_reference_section(non_ref_text.lower())

    def test_is_caption(self, manager):
        """Test caption detection"""
        fig_caption = "figure 1: this is a figure caption"
        assert manager._is_caption(fig_caption)

        table_caption = "table 2: results summary"
        assert manager._is_caption(table_caption)

        regular_text = "this is regular paragraph text"
        assert not manager._is_caption(regular_text)

    # ==================== PERFORMANCE TESTS ====================

    def test_chunking_performance(self, manager, mock_extraction_result):
        """Test that chunking completes quickly"""
        result = manager.chunk(
            mock_extraction_result,
            strategy=ChunkingStrategy.BY_TITLE,
            max_characters=500,
        )

        # Should complete in less than 100ms for small document
        assert result.processing_time_ms < 100

    # ==================== ERROR HANDLING TESTS ====================

    def test_invalid_strategy(self, manager, mock_extraction_result):
        """Test handling of invalid strategy"""
        with pytest.raises(ValueError) as exc_info:
            manager.chunk(
                mock_extraction_result, strategy="invalid_strategy"  # type: ignore
            )

    def test_empty_extraction_result(self, manager):
        """Test handling of empty extraction result"""
        empty_result = ExtractionResult(
            pdf_path="empty.pdf",
            elements=[],
            page_count=0,
            full_text="",
            metadata={},
            tables=[],
            figures=[],
            extraction_time_ms=0,
            file_size_bytes=0,
            processing_strategy="fast",
        )

        result = manager.chunk(empty_result)
        assert result.total_chunks == 0
        assert result.chunks == []
