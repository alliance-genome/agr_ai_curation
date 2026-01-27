"""Tests for PDFDocument SQLAlchemy model."""
import pytest

from src.models.sql.pdf_document import PDFDocument


class TestPDFDocumentModel:
    """Tests for PDFDocument model attributes."""

    def test_model_has_title_attribute(self):
        """PDFDocument should have a title attribute for batch processing."""
        # The title field allows users to rename documents for batch clarity
        doc = PDFDocument(
            filename="test.pdf",
            file_path="/path/to/test.pdf",
            file_hash="abc123def456",
            file_size=1024,
            page_count=10,
        )

        # Should have title attribute
        assert hasattr(doc, "title")

        # Title should be settable
        doc.title = "My Research Paper"
        assert doc.title == "My Research Paper"

    def test_title_defaults_to_none(self):
        """Title should default to None (optional field)."""
        doc = PDFDocument(
            filename="test.pdf",
            file_path="/path/to/test.pdf",
            file_hash="abc123def456",
            file_size=1024,
            page_count=10,
        )

        # Title should be None by default (not set by user yet)
        assert doc.title is None
