"""SQLAlchemy models for papers (``pdf_documents``) and their files (``pdf_document_files``)."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.sql.database import Base


DOCUMENT_KIND_LIBRARY = "library"
DOCUMENT_KIND_BENCHMARK_FROZEN = "benchmark_frozen"
DOCUMENT_KINDS = (DOCUMENT_KIND_LIBRARY, DOCUMENT_KIND_BENCHMARK_FROZEN)

FILE_ROLE_MAIN = "main"
FILE_ROLE_SUPPLEMENT = "supplement"
FILE_ROLES = (FILE_ROLE_MAIN, FILE_ROLE_SUPPLEMENT)

FILE_STATUS_PENDING = "pending"
FILE_STATUS_READY = "ready"
FILE_STATUS_FAILED = "failed"
FILE_STATUSES = (FILE_STATUS_PENDING, FILE_STATUS_READY, FILE_STATUS_FAILED)

DISPLAY_MODE_LOCAL_PDF = "local_pdf"
DISPLAY_MODE_PROVIDER_PDF_PROXY = "provider_pdf_proxy"
DISPLAY_MODE_TEXT = "text"
DISPLAY_MODES = (DISPLAY_MODE_LOCAL_PDF, DISPLAY_MODE_PROVIDER_PDF_PROXY, DISPLAY_MODE_TEXT)


def _sql_in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(value) for value in values)})"


class MissingMainFileError(RuntimeError):
    """A paper has no main file row; nothing guesses one."""

    def __init__(self, document_id: object) -> None:
        super().__init__(f"Document {document_id} has no main file")
        self.document_id = document_id


class PDFDocument(Base):
    """One paper in the Library. Each of its files is a ``PDFDocumentFile`` row."""

    __tablename__ = "pdf_documents"

    id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    # The paper's name in the Library. The main file's display name is copied from it.
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    # User-defined title for batch processing (defaults to None, uses filename if not set)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    upload_timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    last_accessed: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    # Status tracking (Phase 3)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="pending")
    error_message: Mapped[str | None] = mapped_column(String, nullable=True)
    processing_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    processing_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # ``library`` papers are curator entries; ``benchmark_frozen`` papers are
    # runtime copies made by benchmark preparation and never shown in the Library.
    document_kind: Mapped[str] = mapped_column(
        String(32), nullable=False, default=DOCUMENT_KIND_LIBRARY,
    )
    # The next free piece number in the search index. A file's range is reserved
    # from it under a row lock and numbers are never reused. NULL only on papers
    # saved before the files table existed, until the one-time fill step measures them.
    # Every code path that creates a paper sets it to 0 explicitly.
    next_chunk_index: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # A curator's statement of which source paper this file is. Loaded only when
    # read, and kept in its own table, apart from where the file itself came from.
    reference_link: Mapped["DocumentReferenceLink | None"] = relationship(
        back_populates="document",
        uselist=False,
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    # Loaded with the paper: a paper has few files and most readers need its main file.
    files: Mapped[list["PDFDocumentFile"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="PDFDocumentFile.tab_order",
        lazy="selectin",
    )

    # Optional upstream provenance for provider-backed document imports. These
    # name the paper; per-file provenance lives on ``PDFDocumentFile``.
    source_provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_provider_reference_id: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    source_provider_reference_curie: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )
    source_external_ids: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Every supported PDF document has an authenticated owner. The database
    # foreign key and ON DELETE CASCADE were introduced by migration a7f8b9c0d1e2.
    user_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
        comment="Owner user ID - references users(user_id)",
    )

    __table_args__ = (
        CheckConstraint(
            _sql_in("document_kind", DOCUMENT_KINDS),
            name="ck_pdf_documents_document_kind",
        ),
        CheckConstraint(
            "next_chunk_index IS NULL OR next_chunk_index >= 0",
            name="ck_pdf_documents_next_chunk_index",
        ),
    )

    @property
    def main_file(self) -> "PDFDocumentFile":
        """The paper's main file. A paper without one is a named error."""

        main_files = [file_row for file_row in self.files if file_row.role == FILE_ROLE_MAIN]
        if len(main_files) != 1:
            raise MissingMainFileError(self.id)
        return main_files[0]

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return f"<PDFDocument(id={self.id}, filename='{self.filename}')>"


class PDFDocumentFile(Base):
    """One file of a paper: its main paper or one supplement."""

    __tablename__ = "pdf_document_files"

    id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    document_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        ForeignKey("pdf_documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    error_message: Mapped[str | None] = mapped_column(String, nullable=True)
    tab_order: Mapped[int] = mapped_column(Integer, nullable=False)
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    display_mode: Mapped[str] = mapped_column(String(32), nullable=False)

    file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    # Pages exist only for PDF display modes; a text file has none.
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pdfx_json_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    processed_json_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Hierarchy metadata from LLM-based section resolution
    # Stores: sections, top_level_sections, created_at, model_used, llm_raw_response
    hierarchy_metadata: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    source_provider_source_file_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_provider_converted_artifact_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True,
    )
    source_provider_pdf_artifact_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_md5: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # SHA256 of the exact downloaded converted artifact, before text processing.
    source_converted_artifact_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_file_class: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_file_extension: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source_artifact_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # The provider import's status; NULL on files without a provider import.
    source_import_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_imported_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    source_payload_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    source_markdown_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    source_access_scope: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source_access_group_ids: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    # This file's pieces are ``chunkIndex`` start..end (inclusive) of the paper
    # in the search index. ``text_bytes`` is the UTF-8 byte total of the text of
    # the elements that were cut into those pieces (see document_files.measure).
    chunk_index_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    chunk_index_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    chunk_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    text_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    document: Mapped[PDFDocument] = relationship(back_populates="files")

    __table_args__ = (
        CheckConstraint(_sql_in("role", FILE_ROLES), name="ck_pdf_document_files_role"),
        CheckConstraint(_sql_in("status", FILE_STATUSES), name="ck_pdf_document_files_status"),
        CheckConstraint(
            _sql_in("display_mode", DISPLAY_MODES), name="ck_pdf_document_files_display_mode",
        ),
        CheckConstraint("tab_order >= 0", name="ck_pdf_document_files_tab_order"),
        CheckConstraint("file_size > 0", name="ck_pdf_document_files_file_size"),
        CheckConstraint(
            f"(display_mode = '{DISPLAY_MODE_TEXT}' AND page_count IS NULL) "
            f"OR (display_mode <> '{DISPLAY_MODE_TEXT}' AND page_count IS NOT NULL AND page_count > 0)",
            name="ck_pdf_document_files_page_count",
        ),
        CheckConstraint(
            source_converted_artifact_sha256.is_(None)
            | source_converted_artifact_sha256.regexp_match("^[0-9a-f]{64}$"),
            name="ck_pdf_document_files_converted_artifact_sha256",
        ),
        CheckConstraint(
            "(chunk_index_start IS NULL AND chunk_index_end IS NULL) "
            "OR (chunk_index_start >= 0 AND chunk_index_end >= chunk_index_start "
            "AND chunk_count = chunk_index_end - chunk_index_start + 1)",
            name="ck_pdf_document_files_chunk_range",
        ),
        CheckConstraint(
            "chunk_count IS NULL OR chunk_count >= 0", name="ck_pdf_document_files_chunk_count",
        ),
        CheckConstraint(
            "text_bytes IS NULL OR text_bytes >= 0", name="ck_pdf_document_files_text_bytes",
        ),
        UniqueConstraint("file_path", name="uq_pdf_document_files_file_path"),
        UniqueConstraint("document_id", "tab_order", name="uq_pdf_document_files_tab_order"),
        UniqueConstraint(
            "document_id",
            "source_provider_source_file_id",
            name="uq_pdf_document_files_source_file",
        ),
        Index(
            "uq_pdf_document_files_one_main",
            "document_id",
            unique=True,
            postgresql_where=text(f"role = '{FILE_ROLE_MAIN}'"),
        ),
        Index(
            "uq_pdf_document_files_main_file_hash",
            "file_hash",
            unique=True,
            postgresql_where=text(f"role = '{FILE_ROLE_MAIN}'"),
        ),
        Index(
            "ix_pdf_document_files_source_md5",
            "source_md5",
            postgresql_where=text("source_md5 IS NOT NULL"),
        ),
        Index(
            "ix_pdf_document_files_converted_artifact",
            "source_provider_converted_artifact_id",
            postgresql_where=text("source_provider_converted_artifact_id IS NOT NULL"),
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return f"<PDFDocumentFile(id={self.id}, document_id={self.document_id}, role='{self.role}')>"


class DocumentReferenceLink(Base):
    """A curator's link from an uploaded document to its paper in the literature source."""

    __tablename__ = "pdf_document_reference_links"

    document_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        ForeignKey("pdf_documents.id", ondelete="CASCADE"),
        primary_key=True,
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    reference_curie: Mapped[str] = mapped_column(String(128), nullable=False)
    linked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    document: Mapped[PDFDocument] = relationship(back_populates="reference_link")

