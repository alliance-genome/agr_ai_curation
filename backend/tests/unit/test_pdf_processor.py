"""
Unit tests for PDF Processor Library
Following TDD-RED phase - these tests should FAIL initially
Tests cover PyMuPDF extraction, validation, and hashing functionality
"""

import pytest
import hashlib
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
import json
from typing import Dict, List, Optional

# These imports will fail initially (TDD-RED)
from lib.pdf_processor import (
    PDFProcessor,
    ExtractionResult,
    PageContent,
    ExtractedTable,
    ExtractedFigure,
    PDFValidationResult,
    PDFHashResult,
    PDFProcessorError,
    UnsupportedFileError,
    CorruptedPDFError,
)


class TestPDFProcessor:
    """Test suite for PDF Processor using PyMuPDF"""

    @pytest.fixture
    def processor(self):
        """Create a PDF processor instance"""
        return PDFProcessor()

    @pytest.fixture
    def sample_pdf_path(self, tmp_path):
        """Create a temporary PDF file for testing"""
        pdf_file = tmp_path / "test_document.pdf"
        # Create a minimal valid PDF content
        pdf_content = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
        pdf_file.write_bytes(pdf_content)
        return str(pdf_file)

    @pytest.fixture
    def corrupted_pdf_path(self, tmp_path):
        """Create a corrupted PDF file for testing"""
        pdf_file = tmp_path / "corrupted.pdf"
        pdf_file.write_bytes(b"Not a valid PDF content")
        return str(pdf_file)

    # ==================== EXTRACTION TESTS ====================

    def test_extract_basic(self, processor, sample_pdf_path):
        """Test basic PDF extraction"""
        result = processor.extract(
            pdf_path=sample_pdf_path, extract_tables=False, extract_figures=False
        )

        assert isinstance(result, ExtractionResult)
        assert result.pdf_path == sample_pdf_path
        assert result.page_count > 0
        assert result.pages is not None
        assert len(result.pages) == result.page_count
        assert result.extraction_time_ms > 0
        assert result.file_size_bytes > 0

    def test_extract_with_text_content(self, processor, sample_pdf_path):
        """Test extraction returns text content"""
        result = processor.extract(pdf_path=sample_pdf_path)

        assert result.full_text is not None
        for page in result.pages:
            assert isinstance(page, PageContent)
            assert page.page_number > 0
            assert page.text is not None

    def test_extract_tables(self, processor, sample_pdf_path):
        """Test table extraction from PDF"""
        result = processor.extract(pdf_path=sample_pdf_path, extract_tables=True)

        assert result.tables is not None
        assert isinstance(result.tables, list)
        assert result.table_count >= 0

        for table in result.tables:
            assert isinstance(table, ExtractedTable)
            assert table.page_number > 0
            assert table.data is not None
            assert isinstance(table.headers, (list, type(None)))
            assert table.bbox is not None

    def test_extract_figures(self, processor, sample_pdf_path):
        """Test figure extraction from PDF"""
        result = processor.extract(pdf_path=sample_pdf_path, extract_figures=True)

        assert result.figures is not None
        assert isinstance(result.figures, list)
        assert result.figure_count >= 0

        for figure in result.figures:
            assert isinstance(figure, ExtractedFigure)
            assert figure.page_number > 0
            assert figure.figure_type in ["CHART", "DIAGRAM", "IMAGE", "PLOT", None]
            assert figure.bbox is not None

    def test_extract_with_layout_blocks(self, processor, sample_pdf_path):
        """Test that layout information is extracted"""
        result = processor.extract(pdf_path=sample_pdf_path, preserve_layout=True)

        for page in result.pages:
            assert page.layout_blocks is not None
            assert isinstance(page.layout_blocks, list)
            for block in page.layout_blocks:
                assert "bbox" in block
                assert "type" in block  # header, paragraph, caption, etc.
                assert "text" in block
                assert block["bbox"] is not None
                assert all(key in block["bbox"] for key in ["x1", "y1", "x2", "y2"])

    def test_extract_metadata(self, processor, sample_pdf_path):
        """Test PDF metadata extraction"""
        result = processor.extract(pdf_path=sample_pdf_path)

        assert result.metadata is not None
        # These fields may or may not be present, but should exist as keys
        expected_keys = [
            "title",
            "author",
            "subject",
            "keywords",
            "creator",
            "producer",
            "creation_date",
            "modification_date",
        ]
        for key in expected_keys:
            assert key in result.metadata  # Key exists even if value is None

    def test_extract_corrupted_pdf_raises_error(self, processor, corrupted_pdf_path):
        """Test that corrupted PDF raises appropriate error"""
        with pytest.raises(CorruptedPDFError) as exc_info:
            processor.extract(pdf_path=corrupted_pdf_path)

        assert (
            "corrupted" in str(exc_info.value).lower()
            or "invalid" in str(exc_info.value).lower()
        )

    def test_extract_nonexistent_file_raises_error(self, processor):
        """Test that nonexistent file raises appropriate error"""
        with pytest.raises(FileNotFoundError):
            processor.extract(pdf_path="/nonexistent/file.pdf")

    def test_extract_non_pdf_file_raises_error(self, processor, tmp_path):
        """Test that non-PDF file raises appropriate error"""
        txt_file = tmp_path / "not_a_pdf.txt"
        txt_file.write_text("This is not a PDF")

        with pytest.raises(UnsupportedFileError) as exc_info:
            processor.extract(pdf_path=str(txt_file))

        assert (
            "not a valid PDF" in str(exc_info.value).lower()
            or "unsupported" in str(exc_info.value).lower()
        )

    def test_extract_with_page_range(self, processor, sample_pdf_path):
        """Test extraction with specific page range"""
        result = processor.extract(pdf_path=sample_pdf_path, start_page=1, end_page=1)

        assert len(result.pages) == 1
        assert result.pages[0].page_number == 1

    # ==================== VALIDATION TESTS ====================

    def test_validate_pdf_structure(self, processor, sample_pdf_path):
        """Test PDF validation functionality"""
        result = processor.validate(pdf_path=sample_pdf_path)

        assert isinstance(result, PDFValidationResult)
        assert isinstance(result.is_valid, bool)
        assert result.page_count >= 0
        assert isinstance(result.has_text, bool)
        assert isinstance(result.has_images, bool)
        assert result.file_size_bytes > 0

    def test_validate_with_checks(self, processor, sample_pdf_path):
        """Test validation with various checks"""
        result = processor.validate(
            pdf_path=sample_pdf_path, check_corruption=True, check_encryption=True
        )

        assert result.is_encrypted is not None
        assert result.is_corrupted is not None
        assert result.issues is not None
        assert isinstance(result.issues, list)

    def test_validate_returns_json_format(self, processor, sample_pdf_path):
        """Test validation returns JSON format when requested"""
        result_json = processor.validate(pdf_path=sample_pdf_path, format="json")

        assert isinstance(result_json, str)
        parsed = json.loads(result_json)
        assert "is_valid" in parsed
        assert "page_count" in parsed
        assert "file_size_bytes" in parsed

    def test_validate_corrupted_pdf(self, processor, corrupted_pdf_path):
        """Test validation of corrupted PDF"""
        result = processor.validate(pdf_path=corrupted_pdf_path)

        assert result.is_valid is False
        assert result.is_corrupted is True
        assert len(result.issues) > 0

    # ==================== HASHING TESTS ====================

    def test_hash_pdf_content(self, processor, sample_pdf_path):
        """Test PDF content hashing"""
        result = processor.hash(
            pdf_path=sample_pdf_path, normalized=False, per_page=False
        )

        assert isinstance(result, PDFHashResult)
        assert result.file_hash is not None
        assert len(result.file_hash) == 32  # MD5 hash length
        assert result.content_hash is not None
        assert len(result.content_hash) == 32

    def test_hash_normalized_content(self, processor, sample_pdf_path):
        """Test normalized content hashing (whitespace/formatting agnostic)"""
        result = processor.hash(pdf_path=sample_pdf_path, normalized=True)

        assert result.content_hash_normalized is not None
        assert len(result.content_hash_normalized) == 32
        # Normalized hash should exist (may or may not differ from raw)
        assert result.content_hash is not None

    def test_hash_per_page(self, processor, sample_pdf_path):
        """Test per-page hashing for incremental updates"""
        result = processor.hash(pdf_path=sample_pdf_path, per_page=True)

        assert result.page_hashes is not None
        assert isinstance(result.page_hashes, list)
        assert len(result.page_hashes) > 0

        for page_hash in result.page_hashes:
            assert isinstance(page_hash, str)
            assert len(page_hash) == 32

    def test_hash_deterministic(self, processor, sample_pdf_path):
        """Test that hashing is deterministic"""
        result1 = processor.hash(pdf_path=sample_pdf_path, normalized=True)
        result2 = processor.hash(pdf_path=sample_pdf_path, normalized=True)

        assert result1.file_hash == result2.file_hash
        assert result1.content_hash == result2.content_hash
        if result1.content_hash_normalized and result2.content_hash_normalized:
            assert result1.content_hash_normalized == result2.content_hash_normalized

    # ==================== INTEGRATION TESTS ====================

    def test_full_extraction_pipeline(self, processor, sample_pdf_path):
        """Test complete extraction pipeline with all features"""
        result = processor.extract(
            pdf_path=sample_pdf_path,
            extract_tables=True,
            extract_figures=True,
            preserve_layout=True,
        )

        # Validate the complete result
        assert result.pdf_path == sample_pdf_path
        assert result.full_text is not None
        assert result.extraction_time_ms > 0
        assert result.metadata is not None
        assert result.pages is not None
        assert result.tables is not None
        assert result.figures is not None

    def test_extraction_with_progress_callback(self, processor, sample_pdf_path):
        """Test extraction with progress callback"""
        progress_updates = []

        def progress_callback(current_page: int, total_pages: int, status: str):
            progress_updates.append(
                {"current": current_page, "total": total_pages, "status": status}
            )

        result = processor.extract(
            pdf_path=sample_pdf_path, progress_callback=progress_callback
        )

        assert len(progress_updates) > 0
        assert any(u["status"] == "completed" for u in progress_updates)

    # ==================== ERROR HANDLING TESTS ====================

    def test_handle_encrypted_pdf(self, processor, tmp_path):
        """Test handling of encrypted PDFs"""
        # This is a placeholder - real encrypted PDF would be more complex
        encrypted_pdf = tmp_path / "encrypted.pdf"
        encrypted_pdf.write_bytes(b"%PDF-1.4\n%Encrypted")

        with pytest.raises(PDFProcessorError) as exc_info:
            processor.extract(pdf_path=str(encrypted_pdf))

        assert (
            "encrypted" in str(exc_info.value).lower()
            or "password" in str(exc_info.value).lower()
        )

    def test_handle_large_pdf_with_memory_limit(self, processor, tmp_path):
        """Test handling of large PDFs with memory constraints"""
        # Create a mock large PDF
        large_pdf = tmp_path / "large.pdf"
        large_pdf.write_bytes(b"%PDF-1.4\n" + b"0" * 100_000_000)  # 100MB

        with patch("lib.pdf_processor.MAX_FILE_SIZE_MB", 50):
            with pytest.raises(PDFProcessorError) as exc_info:
                processor.extract(pdf_path=str(large_pdf))

            assert (
                "too large" in str(exc_info.value).lower()
                or "size limit" in str(exc_info.value).lower()
            )

    def test_handle_empty_pdf(self, processor, tmp_path):
        """Test handling of empty PDFs"""
        empty_pdf = tmp_path / "empty.pdf"
        empty_pdf.write_bytes(b"%PDF-1.4\n%%EOF")

        result = processor.extract(pdf_path=str(empty_pdf))

        # Should handle gracefully
        assert result.page_count == 0 or result.full_text == ""
        assert result.pages is not None


class TestExtractionResult:
    """Test the ExtractionResult data model"""

    def test_extraction_result_creation(self):
        """Test ExtractionResult creation and properties"""
        pages = [PageContent(page_number=1, text="Page 1 text", layout_blocks=[])]

        result = ExtractionResult(
            pdf_path="/path/to/file.pdf",
            page_count=1,
            pages=pages,
            full_text="Page 1 text",
            extraction_time_ms=100.5,
            file_size_bytes=1024,
            metadata={"title": "Test PDF"},
            tables=[],
            figures=[],
        )

        assert result.pdf_path == "/path/to/file.pdf"
        assert result.page_count == 1
        assert len(result.pages) == 1
        assert result.table_count == 0
        assert result.figure_count == 0

    def test_extraction_result_serialization(self):
        """Test that ExtractionResult can be serialized to dict/JSON"""
        result = ExtractionResult(
            pdf_path="/path/to/file.pdf",
            page_count=10,
            pages=[],
            full_text="Sample text",
            extraction_time_ms=100.5,
            file_size_bytes=1024,
            metadata={"title": "Test PDF"},
            tables=[],
            figures=[],
        )

        # Should be able to convert to dict
        result_dict = result.to_dict()
        assert result_dict["pdf_path"] == "/path/to/file.pdf"
        assert result_dict["page_count"] == 10
        assert result_dict["extraction_time_ms"] == 100.5

        # Should be able to convert to JSON
        result_json = result.to_json()
        assert isinstance(result_json, str)
        parsed = json.loads(result_json)
        assert parsed["pdf_path"] == "/path/to/file.pdf"


class TestCLIInterface:
    """Test the CLI interface for pdf_processor"""

    def test_cli_extract_command(self):
        """Test CLI extract command structure"""
        from lib.pdf_processor import cli

        # Should have extract command
        assert hasattr(cli, "extract")
        assert callable(cli.extract)

    def test_cli_validate_command(self):
        """Test CLI validate command structure"""
        from lib.pdf_processor import cli

        # Should have validate command
        assert hasattr(cli, "validate")
        assert callable(cli.validate)

    def test_cli_hash_command(self):
        """Test CLI hash command structure"""
        from lib.pdf_processor import cli

        # Should have hash command
        assert hasattr(cli, "hash")
        assert callable(cli.hash)
