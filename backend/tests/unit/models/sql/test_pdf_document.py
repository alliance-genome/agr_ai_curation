"""Tests for the paper (PDFDocument) and file (PDFDocumentFile) SQLAlchemy models."""
import pytest
from sqlalchemy import CheckConstraint, Index
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.orm import Mapped

from src.models.sql.pdf_document import (
    MissingMainFileError,
    MultipleMainFilesError,
    PDFDocument,
    PDFDocumentFile,
)


def _constraint(model, name: str) -> CheckConstraint:
    return next(
        element
        for element in model.__table__.constraints
        if isinstance(element, CheckConstraint) and element.name == name
    )


def _main_file(**overrides) -> PDFDocumentFile:
    values = dict(
        role="main",
        display_name="test.pdf",
        status="pending",
        tab_order=0,
        display_mode="local_pdf",
        file_path="/path/to/test.pdf",
        file_hash="abc123def456",
        file_size=1024,
        page_count=10,
    )
    values.update(overrides)
    return PDFDocumentFile(**values)


class TestPDFDocumentModel:
    """Tests for the paper model's attributes."""

    def test_model_has_title_attribute(self):
        """PDFDocument should have a title attribute for batch processing."""
        doc = PDFDocument(user_id=1, filename="test.pdf")

        assert hasattr(doc, "title")
        doc.title = "My Research Paper"
        assert doc.title == "My Research Paper"

    def test_title_defaults_to_none(self):
        doc = PDFDocument(user_id=1, filename="test.pdf")

        assert doc.title is None

    def test_owner_is_required_in_the_orm_contract(self):
        column = PDFDocument.__table__.c.user_id

        assert PDFDocument.__annotations__["user_id"] == Mapped[int]
        assert column.nullable is False
        assert column.index is True

    def test_per_file_columns_live_on_the_file_not_the_paper(self):
        paper_columns = set(PDFDocument.__table__.c.keys())
        file_columns = set(PDFDocumentFile.__table__.c.keys())
        moved = {
            "file_path", "file_hash", "file_size", "page_count", "pdfx_json_path",
            "processed_json_path", "hierarchy_metadata", "source_provider_source_file_id",
            "source_provider_converted_artifact_id", "source_provider_pdf_artifact_id",
            "source_md5", "source_converted_artifact_sha256", "source_file_class",
            "source_file_extension", "source_artifact_status", "source_import_status",
            "source_imported_at", "source_payload_path", "source_markdown_path",
            "source_access_scope", "source_access_group_ids",
        }

        assert not moved & paper_columns
        assert moved <= file_columns
        assert "viewer_mode" not in paper_columns | file_columns
        assert {"filename", "title", "upload_timestamp", "last_accessed", "document_kind",
                "next_chunk_index", "source_provider", "source_provider_reference_id",
                "source_provider_reference_curie", "source_external_ids"} <= paper_columns

    def test_document_kind_and_next_piece_number_constraints(self):
        kind = _constraint(PDFDocument, "ck_pdf_documents_document_kind")
        counter = _constraint(PDFDocument, "ck_pdf_documents_next_chunk_index")

        assert str(kind.sqltext) == "document_kind IN ('library', 'benchmark_frozen')"
        assert str(counter.sqltext) == "next_chunk_index IS NULL OR next_chunk_index >= 0"

    def test_paper_provenance_defaults_to_none(self):
        """Local PDF uploads should not require upstream source provenance."""
        doc = PDFDocument(user_id=1, filename="test.pdf")

        assert doc.source_provider is None
        assert doc.source_provider_reference_curie is None
        assert doc.source_external_ids is None

    def test_main_file_is_a_named_error_when_missing(self):
        doc = PDFDocument(user_id=1, filename="test.pdf")

        with pytest.raises(MissingMainFileError):
            doc.main_file

    def test_a_supplements_only_paper_has_no_main_file(self):
        doc = PDFDocument(user_id=1, filename="test.pdf")
        doc.files.append(
            _main_file(role="supplement", display_mode="text", page_count=None)
        )

        assert doc.main_file_or_none is None
        with pytest.raises(MissingMainFileError):
            doc.main_file

    def test_main_file_or_none_returns_the_main_file(self):
        doc = PDFDocument(user_id=1, filename="test.pdf")
        main = _main_file()
        doc.files.extend(
            [_main_file(role="supplement", display_mode="text", page_count=None), main]
        )

        assert doc.main_file_or_none is main
        assert doc.main_file is main

    def test_two_main_files_are_a_named_error(self):
        doc = PDFDocument(user_id=1, filename="test.pdf")
        doc.files.extend([_main_file(), _main_file()])

        with pytest.raises(MultipleMainFilesError):
            doc.main_file_or_none


class TestPDFDocumentFileModel:
    """Tests for the file model."""

    def test_file_size_constraint_only_requires_positive_size(self):
        assert str(_constraint(PDFDocumentFile, "ck_pdf_document_files_file_size").sqltext) == "file_size > 0"

    def test_page_count_exists_only_for_pdf_display_modes(self):
        constraint = _constraint(PDFDocumentFile, "ck_pdf_document_files_page_count")

        assert str(constraint.sqltext) == (
            "(display_mode = 'text' AND page_count IS NULL) "
            "OR (display_mode <> 'text' AND page_count IS NOT NULL AND page_count > 0)"
        )

    def test_chunk_range_constraint_ties_the_range_to_the_count(self):
        constraint = _constraint(PDFDocumentFile, "ck_pdf_document_files_chunk_range")

        assert "chunk_count = chunk_index_end - chunk_index_start + 1" in str(constraint.sqltext)

    def test_artifact_digest_constraint_compiles_for_postgres_and_sqlite(self):
        constraint = _constraint(PDFDocumentFile, "ck_pdf_document_files_converted_artifact_sha256")

        for dialect, operator in ((postgresql.dialect(), "~"), (sqlite.dialect(), "REGEXP")):
            ddl = str(constraint.sqltext.compile(dialect=dialect, compile_kwargs={"literal_binds": True}))
            assert "source_converted_artifact_sha256 IS NULL" in ddl
            assert f"source_converted_artifact_sha256 {operator} '^[0-9a-f]{{64}}$'" in ddl

    def test_one_main_file_per_paper_and_unique_main_hashes(self):
        indexes = {
            index.name: index
            for index in PDFDocumentFile.__table__.indexes
            if isinstance(index, Index)
        }
        for name in ("uq_pdf_document_files_one_main", "uq_pdf_document_files_main_file_hash"):
            assert indexes[name].unique is True
            assert str(indexes[name].dialect_options["postgresql"]["where"]) == "role = 'main'"

    def test_file_provenance_is_settable(self):
        """Provider imports persist artifact identifiers and the display mode on the file."""
        main_file = _main_file(
            display_mode="text",
            page_count=None,
            source_provider_converted_artifact_id="555",
            source_md5="md5abc123",
            source_access_scope="restricted",
            source_access_group_ids=["GROUP-A"],
        )

        assert main_file.source_provider_converted_artifact_id == "555"
        assert main_file.source_access_group_ids == ["GROUP-A"]
        assert main_file.display_mode == "text"
        assert main_file.chunk_index_start is None and main_file.text_bytes is None

    def test_main_file_is_the_one_file_with_the_main_role(self):
        doc = PDFDocument(user_id=1, filename="test.pdf")
        main_file = _main_file()
        doc.files.extend([_main_file(role="supplement", tab_order=1), main_file])

        assert doc.main_file is main_file
