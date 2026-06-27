"""Tests for PDFDocument SQLAlchemy model."""
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

    def test_model_uses_configured_file_size_constraint(self):
        constraint = next(
            element
            for element in PDFDocument.__table__.constraints
            if isinstance(element, CheckConstraint)
            and element.name == "ck_pdf_documents_file_size"
        )

        assert str(constraint.sqltext) == (
            f"file_size > 0 AND file_size <= {MAX_PDF_FILE_SIZE_BYTES}"
        )

    def test_literature_provenance_defaults_to_none(self):
        """Local PDF uploads should not require upstream source provenance."""
        doc = PDFDocument(
            filename="test.pdf",
            file_path="/path/to/test.pdf",
            file_hash="abc123def456",
            file_size=1024,
            page_count=10,
        )

        assert doc.source_provider is None
        assert doc.source_provider_reference_curie is None
        assert doc.source_provider_converted_artifact_id is None
        assert doc.source_external_ids is None
        assert doc.source_access_mods is None
        assert doc.viewer_mode is None

    def test_literature_provenance_is_settable(self):
        """ABC imports can persist artifact/source identifiers and viewer mode."""
        doc = PDFDocument(
            filename="AGRKB-101.md",
            file_path="/path/to/AGRKB-101.md",
            file_hash="md5abc123",
            file_size=1024,
            page_count=1,
            source_provider="abc_literature",
            source_provider_reference_curie="AGRKB:101",
            source_provider_converted_artifact_id="555",
            source_md5="md5abc123",
            source_external_ids={"pmid": "12345"},
            source_access_scope="mod",
            source_access_mods={"mods": ["FB"]},
            viewer_mode="text_only",
        )

        assert doc.source_provider == "abc_literature"
        assert doc.source_provider_reference_curie == "AGRKB:101"
        assert doc.source_provider_converted_artifact_id == "555"
        assert doc.source_external_ids == {"pmid": "12345"}
        assert doc.source_access_mods == {"mods": ["FB"]}
        assert doc.viewer_mode == "text_only"
