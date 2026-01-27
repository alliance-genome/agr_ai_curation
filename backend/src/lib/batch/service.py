"""Batch processing service layer."""
import logging
from datetime import datetime, timezone
from typing import Optional, List
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from src.models.sql.batch import Batch, BatchDocument, BatchStatus, BatchDocumentStatus
from src.models.sql.curation_flow import CurationFlow
from src.models.sql.pdf_document import PDFDocument
from src.schemas.batch import BatchResponse, BatchDocumentResponse

logger = logging.getLogger(__name__)


class BatchService:
    """Service for batch processing operations."""

    def __init__(self, db: Session):
        self.db = db

    def list_batches(self, user_id: int) -> List[Batch]:
        """List all batches for a user, ordered by created_at desc."""
        logger.debug("Listing batches for user_id=%s", user_id)
        stmt = (
            select(Batch)
            .where(Batch.user_id == user_id)
            .order_by(Batch.created_at.desc())
            .options(selectinload(Batch.documents))
        )
        result = self.db.scalars(stmt).all()
        batches = list(result)
        logger.debug("Found %d batches for user_id=%s", len(batches), user_id)
        return batches

    def count_running_batches(self, user_id: int) -> int:
        """Count running/pending batches for a user.

        Lightweight query for nav badge without loading documents.
        """
        # CR-9: func import moved to module top level
        count = self.db.query(func.count(Batch.id)).filter(
            Batch.user_id == user_id,
            Batch.status.in_([BatchStatus.PENDING.value, BatchStatus.RUNNING.value])
        ).scalar()
        return count or 0

    def get_pending_documents_count(self, user_id: int) -> int:
        """Get count of documents remaining to process across all running batches.

        Calculates: sum(total_documents - completed_documents - failed_documents)
        for all PENDING or RUNNING batches owned by the user.

        Lightweight query for nav badge polling.
        """
        result = self.db.query(
            func.sum(
                Batch.total_documents - Batch.completed_documents - Batch.failed_documents
            )
        ).filter(
            Batch.user_id == user_id,
            Batch.status.in_([BatchStatus.PENDING.value, BatchStatus.RUNNING.value])
        ).scalar()
        return result or 0

    def get_batch(self, batch_id: UUID, user_id: int) -> Optional[Batch]:
        """Get a batch by ID, verifying ownership."""
        logger.debug("Getting batch_id=%s for user_id=%s", batch_id, user_id)
        stmt = (
            select(Batch)
            .where(Batch.id == batch_id, Batch.user_id == user_id)
            .options(selectinload(Batch.documents))
        )
        batch = self.db.scalars(stmt).first()
        if batch:
            logger.debug("Found batch_id=%s with status=%s", batch_id, batch.status)
        else:
            logger.debug("Batch not found: batch_id=%s, user_id=%s", batch_id, user_id)
        return batch

    def create_batch(
        self,
        user_id: int,
        flow_id: UUID,
        document_ids: List[UUID],
    ) -> Batch:
        """Create a new batch with document records.

        Args:
            user_id: Owner user ID
            flow_id: Flow to execute
            document_ids: List of document UUIDs to process

        Returns:
            Created batch with documents
        """
        logger.info(
            "Creating batch: user_id=%s, flow_id=%s, doc_count=%d",
            user_id, flow_id, len(document_ids)
        )

        # Create batch record
        batch = Batch(
            user_id=user_id,
            flow_id=flow_id,
            status=BatchStatus.PENDING,
            total_documents=len(document_ids),
            completed_documents=0,
            failed_documents=0,
        )
        self.db.add(batch)
        self.db.flush()  # Get batch.id for foreign key

        # Create batch document records
        for position, doc_id in enumerate(document_ids):
            batch_doc = BatchDocument(
                batch_id=batch.id,
                document_id=doc_id,
                position=position,
                status=BatchDocumentStatus.PENDING,
            )
            self.db.add(batch_doc)

        self.db.commit()
        self.db.refresh(batch)

        logger.info("Created batch_id=%s with %d documents", batch.id, len(document_ids))
        return batch

    def update_batch_status(
        self,
        batch_id: UUID,
        status: BatchStatus,
        started_at: Optional[datetime] = None,
        completed_at: Optional[datetime] = None,
    ) -> None:
        """Update batch status and timestamps."""
        batch = self.db.query(Batch).filter(Batch.id == batch_id).first()
        if batch:
            batch.status = status
            if started_at:
                batch.started_at = started_at
            if completed_at:
                batch.completed_at = completed_at
            self.db.commit()
            logger.debug("Updated batch_id=%s status to %s", batch_id, status)

    def update_document_status(
        self,
        batch_doc_id: UUID,
        status: BatchDocumentStatus,
        result_file_path: Optional[str] = None,
        error_message: Optional[str] = None,
        processing_time_ms: Optional[int] = None,
    ) -> None:
        """Update a batch document's status and results."""
        batch_doc = self.db.query(BatchDocument).filter(BatchDocument.id == batch_doc_id).first()
        if batch_doc:
            batch_doc.status = status
            if result_file_path:
                batch_doc.result_file_path = result_file_path
            if error_message:
                batch_doc.error_message = error_message
            if processing_time_ms is not None:
                batch_doc.processing_time_ms = processing_time_ms
            if status in (BatchDocumentStatus.COMPLETED, BatchDocumentStatus.FAILED):
                batch_doc.processed_at = datetime.now(timezone.utc)
            self.db.commit()

    def increment_batch_completed(self, batch_id: UUID) -> None:
        """Increment the completed document count."""
        batch = self.db.query(Batch).filter(Batch.id == batch_id).first()
        if batch:
            batch.completed_documents += 1
            self.db.commit()

    def increment_batch_failed(self, batch_id: UUID) -> None:
        """Increment the failed document count."""
        batch = self.db.query(Batch).filter(Batch.id == batch_id).first()
        if batch:
            batch.failed_documents += 1
            self.db.commit()

    def is_batch_cancelled(self, batch_id: UUID) -> bool:
        """Check if batch has been cancelled."""
        batch = self.db.query(Batch).filter(Batch.id == batch_id).first()
        return batch is not None and batch.status == BatchStatus.CANCELLED

    def cancel_batch(self, batch_id: UUID, user_id: int) -> Optional[Batch]:
        """Cancel a batch if it's in a cancellable state.

        Args:
            batch_id: UUID of the batch to cancel
            user_id: User ID (for ownership verification)

        Returns:
            Updated batch if cancellation successful, None if batch not found

        Raises:
            ValueError: If batch is not in a cancellable state
        """
        batch = self.get_batch(batch_id, user_id)
        if not batch:
            return None

        # Check if batch can be cancelled
        if batch.status not in (BatchStatus.PENDING, BatchStatus.RUNNING):
            raise ValueError(
                f"Cannot cancel batch with status '{batch.status.value}'. "
                "Only PENDING or RUNNING batches can be cancelled."
            )

        logger.info("Cancelling batch: batch_id=%s, current_status=%s", batch_id, batch.status)

        batch.status = BatchStatus.CANCELLED
        batch.completed_at = datetime.now(timezone.utc)
        self.db.commit()
        self.db.refresh(batch)

        logger.info("Batch cancelled: batch_id=%s", batch_id)
        return batch

    def get_flow_name(self, flow_id: UUID) -> Optional[str]:
        """Get flow name by ID."""
        flow = self.db.query(CurationFlow).filter(CurationFlow.id == flow_id).first()
        return flow.name if flow else None

    def get_document_titles(self, document_ids: List[UUID]) -> dict[UUID, str]:
        """Look up document titles by IDs.

        Args:
            document_ids: List of document UUIDs to look up

        Returns:
            Dict mapping document_id to title (or filename if no title)
        """
        if not document_ids:
            return {}

        docs = self.db.query(PDFDocument).filter(
            PDFDocument.id.in_(document_ids)
        ).all()

        return {
            doc.id: doc.title or doc.filename
            for doc in docs
        }

    def batch_to_response(self, batch: Batch, flow_name: Optional[str] = None) -> BatchResponse:
        """Convert Batch model to BatchResponse schema.

        Args:
            batch: Batch model instance
            flow_name: Optional flow name (if not provided, will be looked up)

        Returns:
            BatchResponse schema instance
        """
        if flow_name is None:
            flow_name = self.get_flow_name(batch.flow_id)

        # Look up document titles
        document_ids = [d.document_id for d in batch.documents]
        document_titles = self.get_document_titles(document_ids)

        # Build document responses with titles
        document_responses = []
        for d in batch.documents:
            doc_dict = {
                "id": d.id,
                "document_id": d.document_id,
                "document_title": document_titles.get(d.document_id),
                "position": d.position,
                "status": d.status,
                "result_file_path": d.result_file_path,
                "error_message": d.error_message,
                "processing_time_ms": d.processing_time_ms,
                "processed_at": d.processed_at,
            }
            document_responses.append(BatchDocumentResponse.model_validate(doc_dict))

        return BatchResponse(
            id=batch.id,
            flow_id=batch.flow_id,
            flow_name=flow_name,
            status=batch.status,
            total_documents=batch.total_documents,
            completed_documents=batch.completed_documents,
            failed_documents=batch.failed_documents,
            created_at=batch.created_at,
            started_at=batch.started_at,
            completed_at=batch.completed_at,
            documents=document_responses,
        )
