"""SQLAlchemy models for batch processing.

Batches enable curators to run flows on multiple documents sequentially.
State is persisted after each document for crash recovery.
"""
import enum
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


class BatchStatus(str, enum.Enum):
    """Batch execution status."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class BatchDocumentStatus(str, enum.Enum):
    """Per-document processing status."""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class Batch(Base):
    """Batch processing job metadata.

    Tracks overall batch progress and status. Each batch belongs to a user
    and references a specific flow to execute on multiple documents.
    """
    __tablename__ = "batches"

    id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    # Soft reference to users table (no FK to match CurationFlow pattern)
    user_id: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="Owner user ID - references users(user_id)"
    )
    flow_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True), nullable=False,
        comment="Flow to execute - references curation_flows(id)"
    )
    status: Mapped[BatchStatus] = mapped_column(
        Enum(BatchStatus, values_callable=lambda obj: [e.value for e in obj]),
        nullable=False, default=BatchStatus.PENDING
    )
    total_documents: Mapped[int] = mapped_column(Integer, nullable=False)
    completed_documents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_documents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    documents: Mapped[list["BatchDocument"]] = relationship(
        "BatchDocument", back_populates="batch", cascade="all, delete-orphan",
        order_by="BatchDocument.position"
    )

    __table_args__ = (
        Index("idx_batches_user_id", "user_id"),
        Index("idx_batches_status", "status"),
    )


class BatchDocument(Base):
    """Per-document tracking within a batch.

    Tracks processing status, result file path, and error messages for
    each document in the batch. State is saved after each document completes.
    """
    __tablename__ = "batch_documents"

    id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    batch_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True), ForeignKey("batches.id", ondelete="CASCADE"), nullable=False
    )
    document_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True), nullable=False,
        comment="Reference to document in Weaviate PDFDocument collection"
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[BatchDocumentStatus] = mapped_column(
        Enum(BatchDocumentStatus, values_callable=lambda obj: [e.value for e in obj]),
        nullable=False, default=BatchDocumentStatus.PENDING
    )
    result_file_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    processing_time_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    batch: Mapped["Batch"] = relationship("Batch", back_populates="documents")

    __table_args__ = (
        Index("idx_batch_documents_batch_id", "batch_id"),
        Index("uq_batch_document", "batch_id", "document_id", unique=True),
    )
