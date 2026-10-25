"""Batch processing service layer."""
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, List
from uuid import UUID

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session, selectinload

from src.models.sql.batch import Batch, BatchDocument, BatchStatus, BatchDocumentStatus
from src.models.sql.curation_flow import CurationFlow
from src.models.sql.pdf_document import PDFDocument
from src.lib.curation_workspace.curation_prep_constants import CURATION_PREP_AGENT_ID
from src.lib.curation_workspace.models import (
    CurationExtractionResultRecord,
    CurationReviewSession,
)
from src.schemas.batch import BatchResponse, BatchDocumentResponse, BatchListResponse
from .result_files import canonical_result_files
from .summaries import BatchFilter, list_summaries
from src.lib.openai_agents.config import get_batch_metadata_chunk_size
from .status import (
    require_batch_status_transition,
)

logger = logging.getLogger(__name__)


class BatchService:
    """Service for batch processing operations."""

    def __init__(self, db: Session):
        self.db = db

    def list_batches(
        self,
        user_id: int,
        limit: int | None = None,
        cursor: str | None = None,
        status: BatchFilter | None = None,
    ) -> BatchListResponse:
        """Return bounded, column-only history summaries."""
        return list_summaries(self.db, user_id, limit, cursor, status)

    def active_totals(self, user_id: int) -> dict[str, int]:
        """Exact all-active totals, independent of displayed pages."""
        running, pending = self.db.execute(
            select(
                func.count(Batch.id),
                func.coalesce(
                    func.sum(
                        Batch.total_documents
                        - Batch.completed_documents
                        - Batch.failed_documents
                    ),
                    0,
                ),
            ).where(
                Batch.user_id == user_id,
                Batch.status.in_([BatchStatus.PENDING, BatchStatus.RUNNING]),
            )
        ).one()
        return {"running_count": running, "pending_documents": pending}

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
        active_group_ids: List[str],
    ) -> Batch:
        """Create a new batch with document records.

        Args:
            user_id: Owner user ID
            flow_id: Flow to execute
            document_ids: List of document UUIDs to process
            active_group_ids: Authenticated internal-group snapshot for delayed execution

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
            active_group_ids=list(active_group_ids),
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
            require_batch_status_transition(batch.status, status)
            batch.status = status
            if started_at:
                batch.started_at = started_at
            if completed_at:
                batch.completed_at = completed_at
            self.db.commit()
            logger.debug("Updated batch_id=%s status to %s", batch_id, status)

    def recompute_batch_counters(self, batch: Batch) -> None:
        """Derive progress counters from durable terminal document rows."""
        self.db.flush()
        counts = dict(self.db.execute(select(BatchDocument.status, func.count(BatchDocument.id))
            .where(BatchDocument.batch_id == batch.id).group_by(BatchDocument.status)).all())
        batch.total_documents = sum(counts.values())
        for status, name in ((BatchDocumentStatus.COMPLETED, "completed_documents"),
                             (BatchDocumentStatus.FAILED, "failed_documents"),
                             (BatchDocumentStatus.REVIEW_PENDING, "review_pending_documents"),
                             (BatchDocumentStatus.REVIEW_EXHAUSTED, "review_exhausted_documents"),
                             (BatchDocumentStatus.INTERRUPTED, "interrupted_documents")):
            setattr(batch, name, counts.get(status, 0))

    def is_batch_cancelled(self, batch_id: UUID) -> bool:
        """Check if batch has been cancelled."""
        batch = self.db.query(Batch).filter(Batch.id == batch_id).first()
        return batch is not None and batch.status == BatchStatus.CANCELLED

    def claim_recoverable_batch(
        self,
        batch_id: UUID,
        lease_owner: UUID,
        lease_seconds: int,
    ) -> Optional[Batch]:
        """Atomically claim pending or stale running work for one worker.

        A stale ``PROCESSING`` document is failed rather than re-executed because
        its external flow side effects may already have occurred. Terminal rows
        and their artifact/review references are never modified.
        """
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(seconds=lease_seconds)
        claimed_id = self.db.execute(
            update(Batch)
            .where(
                Batch.id == batch_id,
                or_(
                    Batch.status == BatchStatus.PENDING,
                    (
                        (Batch.status == BatchStatus.RUNNING)
                        & or_(
                            Batch.lease_expires_at.is_(None),
                            Batch.lease_expires_at <= now,
                        )
                    ),
                ),
            )
            .values(
                status=BatchStatus.RUNNING,
                started_at=func.coalesce(Batch.started_at, now),
                lease_owner=lease_owner,
                lease_expires_at=expires_at,
                lease_heartbeat_at=now,
            )
            .returning(Batch.id)
            .execution_options(synchronize_session=False)
        ).scalar_one_or_none()

        if claimed_id is None:
            self.db.rollback()
            return None

        batch = self.db.scalars(
            select(Batch)
            .where(Batch.id == claimed_id)
            .with_for_update()
            .options(selectinload(Batch.documents))
        ).first()
        assert batch is not None

        interrupted_at = datetime.now(timezone.utc)
        for document in batch.documents:
            if document.status == BatchDocumentStatus.PROCESSING:
                document.status = BatchDocumentStatus.INTERRUPTED
                document.error_message = (
                    "Worker lease expired during processing; document was not re-run"
                )
                document.processed_at = interrupted_at
        self.recompute_batch_counters(batch)
        self.db.commit()
        return self.db.scalars(
            select(Batch)
            .where(Batch.id == claimed_id)
            .options(selectinload(Batch.documents))
        ).one()

    def heartbeat_batch_lease(
        self,
        batch_id: UUID,
        lease_owner: UUID,
        lease_seconds: int,
    ) -> bool:
        """Extend a lease only while the caller remains the exclusive owner."""
        now = datetime.now(timezone.utc)
        renewed_id = self.db.execute(
            update(Batch)
            .where(
                Batch.id == batch_id,
                Batch.status == BatchStatus.RUNNING,
                Batch.lease_owner == lease_owner,
                Batch.lease_expires_at > now,
            )
            .values(
                lease_heartbeat_at=now,
                lease_expires_at=now + timedelta(seconds=lease_seconds),
            )
            .returning(Batch.id)
            .execution_options(synchronize_session=False)
        ).scalar_one_or_none()
        self.db.commit()
        return renewed_id is not None

    def list_recoverable_batch_ids(self) -> list[UUID]:
        """Return durable pending and expired-running work for startup dispatch."""
        now = datetime.now(timezone.utc)
        return list(
            self.db.scalars(
                select(Batch.id)
                .where(
                    or_(
                        Batch.status == BatchStatus.PENDING,
                        (
                            (Batch.status == BatchStatus.RUNNING)
                            & or_(
                                Batch.lease_expires_at.is_(None),
                                Batch.lease_expires_at <= now,
                            )
                        ),
                    )
                )
                .order_by(Batch.created_at)
            ).all()
        )

    def _interrupt_processing_documents(self, batch_id):
        """Cancellation atomically removes PROCESSING rows without erasing history."""
        self.db.execute(update(BatchDocument).where(BatchDocument.batch_id == batch_id,
            BatchDocument.status == BatchDocumentStatus.PROCESSING).values(
                status=BatchDocumentStatus.INTERRUPTED,
                error_message="Batch cancelled; saved extraction and review history are retained",
                processed_at=datetime.now(timezone.utc)))
        batch = self.db.get(Batch, batch_id)
        self.recompute_batch_counters(batch)

    def cancel_running_batch_for_lease(
        self,
        batch_id: UUID,
        lease_owner: UUID,
    ) -> bool:
        """Cancel a running batch only while the caller owns its live lease."""
        now = datetime.now(timezone.utc)
        cancelled_id = self.db.execute(
            update(Batch)
            .where(
                Batch.id == batch_id,
                Batch.status == BatchStatus.RUNNING,
                Batch.lease_owner == lease_owner,
                Batch.lease_expires_at > now,
            )
            .values(
                status=BatchStatus.CANCELLED,
                completed_at=now,
                lease_owner=None,
                lease_expires_at=None,
                lease_heartbeat_at=None,
            )
            .returning(Batch.id)
            .execution_options(synchronize_session=False)
        ).scalar_one_or_none()
        if cancelled_id is not None:
            self._interrupt_processing_documents(cancelled_id)
        self.db.commit()
        return cancelled_id is not None

    def complete_running_batch(self, batch_id: UUID, lease_owner: UUID) -> bool:
        """Complete a fully terminal batch only for its current lease owner."""
        now = datetime.now(timezone.utc)
        batch = self.db.scalars(
            select(Batch)
            .where(
                Batch.id == batch_id,
                Batch.status == BatchStatus.RUNNING,
                Batch.lease_owner == lease_owner,
                Batch.lease_expires_at > now,
            )
            .with_for_update()
        ).first()
        if batch is None:
            self.db.rollback()
            return False
        self.recompute_batch_counters(batch)
        if (batch.completed_documents + batch.failed_documents + batch.review_pending_documents
                + batch.review_exhausted_documents + batch.interrupted_documents) != batch.total_documents:
            self.db.commit()
            return False
        completed_id = self.db.execute(
            update(Batch)
            .where(
                Batch.id == batch_id,
                Batch.status == BatchStatus.RUNNING,
                Batch.lease_owner == lease_owner,
                Batch.lease_expires_at > now,
            )
            .values(
                status=BatchStatus.COMPLETED,
                completed_at=now,
                lease_owner=None,
                lease_expires_at=None,
                lease_heartbeat_at=None,
            )
            .returning(Batch.id)
            .execution_options(synchronize_session=False)
        ).scalar_one_or_none()
        self.db.commit()
        return completed_id is not None

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
        now = datetime.now(timezone.utc)
        cancelled_id = self.db.execute(
            update(Batch)
            .where(
                Batch.id == batch_id,
                Batch.user_id == user_id,
                Batch.status.in_((BatchStatus.PENDING, BatchStatus.RUNNING)),
            )
            .values(
                status=BatchStatus.CANCELLED,
                completed_at=now,
                lease_owner=None,
                lease_expires_at=None,
                lease_heartbeat_at=None,
            )
            .returning(Batch.id)
            .execution_options(synchronize_session=False)
        ).scalar_one_or_none()

        if cancelled_id is None:
            batch = self.get_batch(batch_id, user_id)
            if not batch:
                self.db.rollback()
                return None
            self.db.rollback()
            raise ValueError(
                f"Cannot cancel batch with status '{batch.status.value}'. "
                "Only PENDING or RUNNING batches can be cancelled."
            )

        self._interrupt_processing_documents(cancelled_id)
        self.db.commit()
        batch = self.get_batch(cancelled_id, user_id)

        logger.info("Batch cancelled: batch_id=%s", batch_id)
        return batch

    def get_flow_name(self, flow_id: UUID) -> Optional[str]:
        """Get flow name by ID."""
        return self.db.scalar(select(CurationFlow.name).where(CurationFlow.id == flow_id))

    def get_document_titles(self, document_ids: List[UUID]) -> dict[UUID, str]:
        """Look up document titles by IDs.

        Args:
            document_ids: List of document UUIDs to look up

        Returns:
            Dict mapping document_id to title (or filename if no title)
        """
        if not document_ids:
            return {}

        docs = []
        size = get_batch_metadata_chunk_size()
        for offset in range(0, len(document_ids), size):
            docs.extend(
                self.db.execute(
                    select(
                        PDFDocument.id, PDFDocument.title, PDFDocument.filename
                    ).where(PDFDocument.id.in_(document_ids[offset : offset + size]))
                ).all()
            )

        return {doc.id: doc.title or doc.filename for doc in docs}

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

        metadata = self.get_documents_handoff_metadata(batch, batch.documents)

        # Build document responses with titles
        document_responses = []
        for d in batch.documents:
            handoff_metadata = metadata[d.id]
            stored_output_status = getattr(d, "output_status", None)
            stored_output_branches = getattr(d, "output_branches", None)
            doc_dict = {
                "id": d.id,
                "document_id": d.document_id,
                "document_title": document_titles.get(d.document_id),
                "position": d.position,
                "status": d.status,
                "result_files": canonical_result_files(d),
                "output_status": (
                    stored_output_status
                    if stored_output_status in {"complete", "partial", "none", "failed"}
                    else None
                ),
                "output_branches": (
                    stored_output_branches if isinstance(stored_output_branches, list) else []
                ),
                "review_session_ids": getattr(d, "review_session_ids", None),
                **handoff_metadata,
                "review_details": d.review_details,
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
            review_pending_documents=batch.review_pending_documents,
            review_exhausted_documents=batch.review_exhausted_documents,
            interrupted_documents=batch.interrupted_documents,
            created_at=batch.created_at,
            started_at=batch.started_at,
            completed_at=batch.completed_at,
            documents=document_responses,
        )

    def get_documents_handoff_metadata(
        self,
        batch: Batch,
        documents: list[BatchDocument],
    ) -> dict[UUID, dict[str, Any]]:
        """Resolve persisted identities in chunks; assembly performs no SQL.

        These checks protect reference integrity, not scientific judgments.
        Callers must authorize the batch before enrichment.
        """
        metadata: dict[UUID, dict[str, Any]] = {
            d.id: {
                "adapter_keys": [],
                "extraction_result_ids": [],
                "extraction_result_refs": [],
                "flow_run_id": str(batch.id),
                "origin_session_id": None,
            }
            for d in documents
        }
        completed = [d for d in documents if d.status == BatchDocumentStatus.COMPLETED]
        session_ids_by_document = {}
        for document in completed:
            try:
                session_ids_by_document[document.id] = [
                    UUID(s) for s in (document.review_session_ids or [])
                ]
            except (TypeError, ValueError, AttributeError) as exc:
                raise ValueError(
                    f"Batch document {document.id} has an invalid authoritative review-session ID"
                ) from exc
        all_session_ids = list(
            dict.fromkeys(s for ids in session_ids_by_document.values() for s in ids)
        )
        size = get_batch_metadata_chunk_size()
        sessions_by_id = {}
        for offset in range(0, len(all_session_ids), size):
            sessions_by_id.update(
                {
                    row.id: row
                    for row in self.db.execute(
                        select(
                            CurationReviewSession.id, CurationReviewSession.adapter_key
                        ).where(
                            CurationReviewSession.id.in_(
                                all_session_ids[offset : offset + size]
                            )
                        )
                    ).all()
                }
            )
        for document in completed:
            ids = session_ids_by_document[document.id]
            missing = [str(i) for i in ids if i not in sessions_by_id]
            if missing:
                raise ValueError(
                    f"Batch document {document.id} references missing authoritative review sessions: {', '.join(missing)}"
                )
            metadata[document.id]["adapter_keys"] = [
                sessions_by_id[i].adapter_key for i in ids
            ]

        by_document = {d.document_id: d.id for d in completed}
        document_ids = list(by_document)
        record = CurationExtractionResultRecord
        for offset in range(0, len(document_ids), size):
            rows = self.db.execute(
                select(
                    record.id,
                    record.document_id,
                    record.adapter_key,
                    record.agent_key,
                    record.candidate_count,
                    record.trace_id,
                    record.origin_session_id,
                )
                .where(
                    record.document_id.in_(document_ids[offset : offset + size]),
                    record.flow_run_id == str(batch.id),
                    record.agent_key != CURATION_PREP_AGENT_ID,
                )
                .order_by(record.created_at.asc(), record.id.asc())
            ).all()
            for row in rows:
                entry = metadata[by_document[row.document_id]]
                entry["extraction_result_ids"].append(str(row.id))
                entry["extraction_result_refs"].append(
                    {
                        "result_ref": f"extraction-result:{row.id}",
                        "extraction_result_id": str(row.id),
                        "adapter_key": row.adapter_key,
                        "agent_key": row.agent_key,
                        "candidate_count": row.candidate_count,
                        "trace_id": row.trace_id,
                    }
                )
                if entry["origin_session_id"] is None and row.origin_session_id:
                    entry["origin_session_id"] = row.origin_session_id
        return metadata
