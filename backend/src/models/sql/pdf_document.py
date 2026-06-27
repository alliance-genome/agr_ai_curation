"""SQLAlchemy model for PDF documents metadata."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.lib.pdf_limits import MAX_PDF_FILE_SIZE_BYTES
from src.models.sql.database import Base


class PDFDocument(Base):
    """Minimal metadata required for serving PDFs to the viewer."""

    __tablename__ = "pdf_documents"

    id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    # User-defined title for batch processing (defaults to None, uses filename if not set)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    file_path: Mapped[str] = mapped_column(String(500), nullable=False, unique=True)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    page_count: Mapped[int] = mapped_column(Integer, nullable=False)
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

    # New fields for storing processed file versions
    pdfx_json_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    processed_json_path: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Hierarchy metadata from LLM-based section resolution
    # Stores: sections, top_level_sections, created_at, model_used, llm_raw_response
    hierarchy_metadata: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Optional upstream document-source provenance for ABC Literature imports.
    source_provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_provider_reference_id: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    source_provider_reference_curie: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )
    source_provider_source_file_id: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    source_provider_converted_artifact_id: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    source_provider_pdf_artifact_id: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    source_external_ids: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    source_md5: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_file_class: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_file_extension: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source_artifact_status: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    source_import_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_imported_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    source_payload_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    source_markdown_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    source_access_scope: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source_access_mods: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    viewer_mode: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # User ownership (T007: Alembic migration a7f8b9c0d1e2)
    # Foreign key to users table for multi-tenant data isolation (FR-012, FR-016)
    user_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,  # Nullable for backwards compatibility (set during upload)
        index=True,
        comment="Owner user ID - references users(user_id)"
    )

    __table_args__ = (
        CheckConstraint(
            f"file_size > 0 AND file_size <= {MAX_PDF_FILE_SIZE_BYTES}",
            name="ck_pdf_documents_file_size",
        ),
        CheckConstraint(
            "page_count > 0 AND page_count <= 50",
            name="ck_pdf_documents_page_count",
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return f"<PDFDocument(id={self.id}, filename='{self.filename}')>"
