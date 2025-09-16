"""
Unit tests for PDF Processor using Unstructured.io
Tests extraction, validation, and hashing with the new implementation
"""

import pytest
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

from lib.pdf_processor import (
    PDFProcessor,
    ExtractionResult,
    ValidationResult,
    HashResult,
    UnstructuredElement,
)


class TestPDFProcessorUnstructured:
    """Test suite for Unstructured PDF processor"""

    @pytest.fixture
    def processor(self):
        """Create PDF processor instance"""
        return PDFProcessor(default_strategy="fast")

    @pytest.fixture
    def mock_pdf_fs(self):
        """Mock filesystem interactions for synthetic PDF paths"""
        with patch("pathlib.Path.exists", return_value=True), patch(
            "pathlib.Path.stat"
        ) as mock_stat:
            mock_stat.return_value.st_size = 1024
            yield

    @pytest.fixture
    def mock_elements(self):
        """Create mock Unstructured elements"""
        elements = []

        # Create mock Title element
        title_elem = Mock()
        title_elem.category = "Title"
        title_elem.text = "Test Document Title"
        title_elem.id = "elem_1"
        title_elem.metadata = Mock()
        title_elem.metadata.page_number = 1
        title_elem.metadata.coordinates = {
            "x": 100,
            "y": 100,
            "width": 400,
            "height": 50,
        }
        title_elem.metadata.to_dict = Mock(
            return_value={
                "page_number": 1,
                "coordinates": {"x": 100, "y": 100, "width": 400, "height": 50},
            }
        )
        elements.append(title_elem)

        # Create mock NarrativeText element
        text_elem = Mock()
        text_elem.category = "NarrativeText"
        text_elem.text = "This is the main content of the document."
        text_elem.id = "elem_2"
        text_elem.metadata = Mock()
        text_elem.metadata.page_number = 1
        text_elem.metadata.to_dict = Mock(return_value={"page_number": 1})
        elements.append(text_elem)

        # Create mock Table element
        table_elem = Mock()
        table_elem.category = "Table"
        table_elem.text = "Col1\tCol2\nVal1\tVal2"
        table_elem.id = "elem_3"
        table_elem.metadata = Mock()
        table_elem.metadata.page_number = 2
        table_elem.metadata.text_as_html = (
            "<table><tr><td>Col1</td><td>Col2</td></tr></table>"
        )
        table_elem.metadata.to_dict = Mock(
            return_value={
                "page_number": 2,
                "coordinates": {"x1": 50, "y1": 100, "x2": 550, "y2": 200},
                "text_as_html": "<table><tr><td>Col1</td><td>Col2</td></tr></table>",
            }
        )
        elements.append(table_elem)

        # Create mock TableCaption element
        table_caption = Mock()
        table_caption.category = "TableCaption"
        table_caption.text = "Table 1: Test table caption"
        table_caption.id = "elem_4"
        table_caption.metadata = Mock()
        table_caption.metadata.page_number = 2
        table_caption.metadata.to_dict = Mock(return_value={"page_number": 2})
        elements.append(table_caption)

        # Create mock Image element that precedes figure caption
        image_elem = Mock()
        image_elem.category = "Image"
        image_elem.text = ""
        image_elem.id = "elem_5"
        image_elem.metadata = Mock()
        image_elem.metadata.page_number = 2
        image_elem.metadata.coordinates = {
            "x1": 75,
            "y1": 120,
            "x2": 525,
            "y2": 420,
        }
        image_elem.metadata.to_dict = Mock(
            return_value={
                "page_number": 2,
                "coordinates": {"x1": 75, "y1": 120, "x2": 525, "y2": 420},
            }
        )
        elements.append(image_elem)

        # Create mock FigureCaption element
        caption_elem = Mock()
        caption_elem.category = "FigureCaption"
        caption_elem.text = "Figure 1: Test figure caption"
        caption_elem.id = "elem_6"
        caption_elem.metadata = Mock()
        caption_elem.metadata.page_number = 2
        caption_elem.metadata.to_dict = Mock(return_value={"page_number": 2})
        elements.append(caption_elem)

        return elements

    @pytest.fixture
    def real_pdf_path(self):
        """Path to real test PDF"""
        return "tests/fixtures/test_paper.pdf"

    # ==================== EXTRACTION TESTS ====================

    def test_extract_basic(self, processor, real_pdf_path):
        """Test basic PDF extraction with real PDF"""
        result = processor.extract(real_pdf_path, strategy="fast")

        assert isinstance(result, ExtractionResult)
        assert result.processing_strategy == "fast"
        assert len(result.elements) > 10  # Real PDF has many elements
        assert result.page_count == 9  # test_paper.pdf has 9 pages
        assert len(result.full_text) > 1000  # Should have substantial content

        # Check for key content from the paper
        assert "Dnr1" in result.full_text or "neurodegeneration" in result.full_text

        # Check that elements have proper types
        element_types = {e.type for e in result.elements}
        assert len(element_types) > 1  # Should have multiple element types

    def test_extract_with_tables(self, processor, real_pdf_path):
        """Test extraction with table detection using real PDF"""
        result = processor.extract(real_pdf_path, extract_tables=True, strategy="fast")

        # Real scientific papers often have tables
        # Just check the structure is correct, actual count may vary
        assert isinstance(result.tables, list)
        if result.tables:  # If tables were detected
            assert "text" in result.tables[0]
            assert "page" in result.tables[0]

    def test_extract_with_figures(self, processor, real_pdf_path):
        """Test extraction with figure detection using real PDF"""
        result = processor.extract(real_pdf_path, extract_figures=True, strategy="fast")

        assert isinstance(result.figures, list)
        # Scientific papers typically have figures
        if result.figures:
            assert "caption" in result.figures[0]
            assert "page" in result.figures[0]

    @patch("lib.pdf_processor.partition_pdf")
    def test_extract_with_progress_callback(
        self, mock_partition, processor, mock_elements, mock_pdf_fs
    ):
        """Test extraction with progress callback"""
        mock_partition.return_value = mock_elements
        progress_calls = []

        def progress_callback(current, total, message):
            progress_calls.append((current, total, message))

        result = processor.extract("test.pdf", progress_callback=progress_callback)

        assert len(progress_calls) >= 2
        assert progress_calls[0][0] == 0  # Start at 0%
        assert progress_calls[-1][0] == 100  # End at 100%

    @patch("lib.pdf_processor.partition_pdf")
    def test_extract_different_strategies(
        self, mock_partition, processor, mock_elements, mock_pdf_fs
    ):
        """Test different extraction strategies"""
        mock_partition.return_value = mock_elements

        # Test hi_res strategy
        result = processor.extract("test.pdf", strategy="hi_res")
        assert result.processing_strategy == "hi_res"
        mock_partition.assert_called_with(
            filename="test.pdf",
            strategy="hi_res",
            infer_table_structure=True,
            include_page_breaks=True,
            extract_images_in_pdf=False,
            extract_forms=False,
            languages=["eng"],
        )

        # Test ocr_only strategy
        result = processor.extract("test.pdf", strategy="ocr_only")
        assert result.processing_strategy == "ocr_only"

    @patch("lib.pdf_processor.partition_pdf")
    def test_extract_content_hashing(
        self, mock_partition, processor, mock_elements, mock_pdf_fs
    ):
        """Test content hash generation"""
        mock_partition.return_value = mock_elements

        result = processor.extract("test.pdf")

        assert result.content_hash is not None
        assert result.content_hash_normalized is not None
        assert len(result.content_hash) == 32  # MD5 hash length
        assert (
            result.content_hash != result.content_hash_normalized
        )  # Different due to normalization

    @patch("lib.pdf_processor.partition_pdf")
    def test_extract_page_hashes(
        self, mock_partition, processor, mock_elements, mock_pdf_fs
    ):
        """Test per-page hash generation"""
        mock_partition.return_value = mock_elements

        result = processor.extract("test.pdf")

        assert len(result.page_hashes) == 2  # Two pages in mock data
        assert all(len(h) == 32 for h in result.page_hashes)

    @patch("lib.pdf_processor.partition_pdf")
    def test_extract_includes_table_metadata(
        self, mock_partition, processor, mock_elements, mock_pdf_fs
    ):
        """Tables should include caption, bbox, html, and element IDs"""
        mock_partition.return_value = mock_elements

        result = processor.extract("test.pdf", extract_tables=True)

        assert result.tables, "Expected table results in extraction"
        table = result.tables[0]
        assert table["text"] == "Col1\tCol2\nVal1\tVal2"
        assert table["page"] == 2
        assert table["element_id"] == "elem_3"
        assert table["bbox"] == {"x1": 50, "y1": 100, "x2": 550, "y2": 200}
        assert table["html"].startswith("<table")
        assert table["caption"] == "Table 1: Test table caption"

    @patch("lib.pdf_processor.partition_pdf")
    def test_extract_groups_figures_with_captions(
        self, mock_partition, processor, mock_elements, mock_pdf_fs
    ):
        """Figure results should group caption and image metadata together"""
        mock_partition.return_value = mock_elements

        result = processor.extract("test.pdf", extract_figures=True)

        assert result.figures, "Expected figure results in extraction"
        figure = result.figures[0]
        assert figure["caption"] == "Figure 1: Test figure caption"
        assert figure["page"] == 2
        assert figure["has_image"] is True
        assert figure["image_element_id"] == "elem_5"
        assert figure["bbox"] == {"x1": 75, "y1": 120, "x2": 525, "y2": 420}

    # ==================== VALIDATION TESTS ====================

    @patch("lib.pdf_processor.partition_pdf")
    def test_validate_valid_pdf(self, mock_partition, processor, mock_elements):
        """Test validation of valid PDF"""
        mock_partition.return_value = mock_elements

        with patch("pathlib.Path.exists", return_value=True):
            with patch("pathlib.Path.stat") as mock_stat:
                mock_stat.return_value.st_size = 1000000

                result = processor.validate("test.pdf")

                assert result.is_valid is True
                assert result.page_count >= 1
                assert result.has_text is True
                assert result.file_size_bytes == 1000000
                assert result.is_encrypted is False
                assert result.is_corrupted is False

    @patch("lib.pdf_processor.partition_pdf")
    def test_validate_scanned_pdf(self, mock_partition, processor):
        """Test detection of scanned PDF"""
        mock_partition.return_value = []  # No elements means scanned

        with patch("pathlib.Path.exists", return_value=True):
            with patch("pathlib.Path.stat") as mock_stat:
                mock_stat.return_value.st_size = 1000000

                result = processor.validate("test.pdf")

                assert result.is_scanned is True
                assert result.has_text is False

    def test_validate_nonexistent_pdf(self, processor):
        """Test validation of non-existent file"""
        result = processor.validate("nonexistent.pdf")

        assert result.is_valid is False
        assert result.error_message == "File not found"
        assert result.file_size_bytes == 0

    @patch("lib.pdf_processor.partition_pdf")
    def test_validate_corrupted_pdf(self, mock_partition, processor):
        """Test validation of corrupted PDF"""
        mock_partition.side_effect = Exception("PDF is corrupted")

        with patch("pathlib.Path.exists", return_value=True):
            with patch("pathlib.Path.stat") as mock_stat:
                mock_stat.return_value.st_size = 1000000

                result = processor.validate("test.pdf")

                assert result.is_valid is False
                assert result.is_corrupted is True
                assert "corrupted" in result.error_message

    # ==================== HASHING TESTS ====================

    @patch("lib.pdf_processor.partition_pdf")
    def test_hash_all_types(
        self, mock_partition, processor, mock_elements, mock_pdf_fs
    ):
        """Test all hash types"""
        mock_partition.return_value = mock_elements

        with patch("builtins.open", create=True) as mock_open:
            mock_open.return_value.__enter__.return_value.read.return_value = (
                b"file content"
            )

            result = processor.hash("test.pdf", normalized=True, per_page=True)

            assert isinstance(result, HashResult)
            assert len(result.file_hash) == 32
            assert len(result.content_hash) == 32
            assert len(result.content_hash_normalized) == 32
            assert len(result.page_hashes) == 2

    # ==================== HELPER METHOD TESTS ====================

    def test_normalize_text(self, processor):
        """Test text normalization"""
        text = "  This   is   a   TEST!   "
        normalized = processor._normalize_text(text)

        assert "  " not in normalized  # No double spaces
        assert normalized == normalized.lower()  # Lowercase
        assert "!" not in normalized  # Punctuation removed

    @patch("lib.pdf_processor.partition_pdf")
    def test_extract_tables_as_dataframes(
        self, mock_partition, processor, mock_elements, mock_pdf_fs
    ):
        """Test table extraction as DataFrames"""
        mock_partition.return_value = mock_elements

        result = processor.extract("test.pdf")
        tables = processor.extract_tables_as_dataframes(result.elements)

        assert len(tables) == 1
        assert tables[0]["text"] == "Col1\tCol2\nVal1\tVal2"
        assert tables[0]["page"] == 2

    @patch("lib.pdf_processor.partition_pdf")
    def test_extract_figures(
        self, mock_partition, processor, mock_elements, mock_pdf_fs
    ):
        """Test figure extraction"""
        mock_partition.return_value = mock_elements

        result = processor.extract("test.pdf")
        figures = processor.extract_figures(result.elements)

        assert len(figures) == 1
        assert "Figure 1" in figures[0]["caption"]

    @patch("lib.pdf_processor.partition_pdf")
    def test_build_document_structure(
        self, mock_partition, processor, mock_elements, mock_pdf_fs
    ):
        """Test document structure building"""
        mock_partition.return_value = mock_elements

        result = processor.extract("test.pdf")
        structure = processor.build_document_structure(result.elements)

        assert len(structure) >= 1
        assert structure[0]["title"] == "Test Document Title"
        assert structure[0]["level"] == 1

    # ==================== ERROR HANDLING TESTS ====================

    def test_extract_file_not_found(self, processor):
        """Test extraction with non-existent file"""
        with pytest.raises(FileNotFoundError):
            processor.extract("nonexistent.pdf")

    @patch("lib.pdf_processor.partition_pdf")
    def test_extract_processing_error(self, mock_partition, processor):
        """Test extraction error handling"""
        mock_partition.side_effect = Exception("Processing failed")

        with patch("pathlib.Path.exists", return_value=True):
            with patch("pathlib.Path.stat") as mock_stat:
                mock_stat.return_value.st_size = 1000000

                with pytest.raises(RuntimeError) as exc_info:
                    processor.extract("test.pdf")

                assert "Failed to extract PDF" in str(exc_info.value)
