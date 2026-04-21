"""Tests for PDFDocument SQLAlchemy model."""
import pytest
from sqlalchemy import CheckConstraint

from src.lib.pdf_limits import MAX_PDF_FILE_SIZE_BYTES
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

    def test_model_uses_100mb_file_size_constraint(self):
        constraint = next(
            element
            for element in PDFDocument.__table__.constraints
            if isinstance(element, CheckConstraint)
            and element.name == "ck_pdf_documents_file_size"
        )

        assert str(constraint.sqltext) == (
            f"file_size > 0 AND file_size <= {MAX_PDF_FILE_SIZE_BYTES}"
        )
