"""Service helpers for durable PDF processing jobs."""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional
from uuid import UUID

from sqlalchemy import exists, func, or_, select, text, update
from sqlalchemy.exc import IntegrityError, OperationalError

from src.lib.observability.runtime import report_runtime_exception, sanitized_runtime_error
from src.lib.openai_agents.config import (
    get_document_source_dead_wait_polls,
    get_document_source_poll_interval_seconds,
    get_document_source_request_timeout_seconds,
    get_pdf_document_error_message_max_chars,
    get_pdf_job_error_message_max_chars,
    get_pdf_no_job_orphan_batch_size,
    get_pdf_no_job_orphan_repair_apply,
    get_pdf_no_job_orphan_repair_retry_count,
    get_pdf_no_job_orphan_repair_timeout_seconds,
    get_pdf_no_job_orphan_threshold_seconds,
)
from src.lib.pipeline.processing_receipt import (
    PDF_PROCESSING_RECEIPT_KEY,
    minimal_terminal_receipt,
)
from src.models.sql.database import SessionLocal
from src.models.sql.pdf_document import (
    FILE_ROLE_MAIN,
    FILE_STATUS_FAILED,
    FILE_STATUS_PENDING,
    PDFDocument,
    PDFDocumentFile,
)
from src.models.sql.pdf_processing_job import (
    ONE_ACTIVE_JOB_PER_PAPER_INDEX,
    PdfJobStatus,
    PdfProcessingJob,
)
from src.schemas.pdf_jobs import PdfJobListResponse, PdfJobResponse
from src.services.processing_status_policy import (
    PDF_JOB_STATUS_TO_PROCESSING_STATUS,
    TERMINAL_PROCESSING_STATUSES,
)


logger = logging.getLogger(__name__)
# Retain workers until completion even after their requesting stream disconnects.
_pending_snapshots: set[asyncio.Task] = set()


_TERMINAL_STATUSES = {
    PdfJobStatus.COMPLETED.value,
    PdfJobStatus.FAILED.value,
    PdfJobStatus.CANCELLED.value,
}
_ACTIVE_STATUSES = {
    PdfJobStatus.PENDING.value,
    PdfJobStatus.RUNNING.value,
    PdfJobStatus.CANCEL_REQUESTED.value,
}
# Stages in which a living job asks the document-source provider again at every
# poll interval and records it (``update_progress``). Only these can be told
# dead from their last activity alone.
PROVIDER_CONVERSION_STAGE = "provider_conversion"
_POLLING_STAGES = frozenset({PROVIDER_CONVERSION_STAGE})
# updated_at is mutable activity time, so equal creation times use a stable ID tie-break.
_JOB_CREATION_ORDERING = (
    PdfProcessingJob.created_at.desc(),
    PdfProcessingJob.id.desc(),
)
_DEFAULT_STALE_TIMEOUT_SECONDS = 7200
_MIN_STALE_TIMEOUT_SECONDS = 300
NO_JOB_ORPHAN_FAILURE_MESSAGE = (
    "PDF processing did not start because no durable processing job was created; "
    "retry the document processing request"
)
NO_JOB_ORPHAN_SKIPPED_REASON = (
    "skipped_has_processed_output: the paper was processed (processed or extraction "
    "output, or pieces in the search index, are stored for its main file), so it is not "
    "an unstarted upload and is never marked failed here"
)


@dataclass(frozen=True)
class NoJobOrphanRepairRecord:
    """Content-free report row for one qualifying pending document."""

    document_id: str
    upload_timestamp: datetime
    status: str
    reason: str
    job_id: str | None = None

    def to_json(self) -> dict[str, str | None]:
        return {
            "document_id": self.document_id,
            "upload_timestamp": _as_utc(self.upload_timestamp).isoformat(),
            "status": self.status,
            "reason": self.reason,
            "job_id": self.job_id,
        }


@dataclass(frozen=True)
class NoJobOrphanRepairSummary:
    """Bounded result from one manual no-job orphan reconciliation run."""

    dry_run: bool
    cutoff: datetime
    batch_size: int
    records: tuple[NoJobOrphanRepairRecord, ...]
    # Pending, no job, aged, but with processed output: never failed by this repair.
    skipped: tuple[NoJobOrphanRepairRecord, ...] = ()

    @property
    def qualifying_count(self) -> int:
        return len(self.records)

    def to_json(self) -> dict[str, object]:
        return {
            "dry_run": self.dry_run,
            "cutoff": _as_utc(self.cutoff).isoformat(),
            "batch_size": self.batch_size,
            "qualifying_count": self.qualifying_count,
            "records": [record.to_json() for record in self.records],
            "skipped_count": len(self.skipped),
            "skipped": [record.to_json() for record in self.skipped],
        }


def _to_uuid(value: UUID | str) -> UUID:
    if isinstance(value, UUID):
        return value
    return UUID(str(value))


def _clamp_progress(value: Optional[int]) -> Optional[int]:
    if value is None:
        return None
    return max(0, min(100, int(value)))


def _store_terminal_metadata(
    job: PdfProcessingJob,
    *,
    metadata: Optional[dict],
    outcome: str,
    completed_at: datetime,
) -> None:
    """Atomically merge terminal metadata and guarantee the canonical receipt."""
    current_metadata = dict(job.metadata_json or {})
    if metadata:
        current_metadata.update(metadata)
    if not isinstance(current_metadata.get(PDF_PROCESSING_RECEIPT_KEY), dict):
        started_at = job.started_at or job.created_at or completed_at
        current_metadata[PDF_PROCESSING_RECEIPT_KEY] = minimal_terminal_receipt(
            started_at=_as_utc(started_at),
            completed_at=_as_utc(completed_at),
            outcome=outcome,
        )
    job.metadata_json = current_metadata


def _to_response(job: PdfProcessingJob) -> PdfJobResponse:
    return PdfJobResponse(
        job_id=str(job.id),
        document_id=str(job.document_id),
        user_id=job.user_id,
        filename=job.filename,
        status=job.status,
        current_stage=job.current_stage,
        progress_percentage=job.progress_percentage,
        message=job.message,
        process_id=job.process_id,
        cancel_requested=job.cancel_requested,
        error_message=job.error_message,
        metadata=job.metadata_json,
        created_at=job.created_at,
        started_at=job.started_at,
        updated_at=job.updated_at,
        completed_at=job.completed_at,
    )


def _stale_timeout_seconds() -> int:
    configured = os.getenv("PDF_JOB_STALE_TIMEOUT_SECONDS", "").strip()
    if configured:
        try:
            return max(_MIN_STALE_TIMEOUT_SECONDS, int(configured))
        except (TypeError, ValueError):
            return _DEFAULT_STALE_TIMEOUT_SECONDS

    # If job-specific timeout is not set, fall back to a value tied to PDF extraction timeout.
    extraction_timeout = os.getenv("PDF_EXTRACTION_TIMEOUT", "").strip()
    if extraction_timeout:
        try:
            return max(_MIN_STALE_TIMEOUT_SECONDS, int(extraction_timeout) * 2)
        except (TypeError, ValueError):
            return _DEFAULT_STALE_TIMEOUT_SECONDS
    return _DEFAULT_STALE_TIMEOUT_SECONDS


def _as_utc(dt_value: datetime) -> datetime:
    if dt_value.tzinfo is None:
        return dt_value.replace(tzinfo=timezone.utc)
    return dt_value.astimezone(timezone.utc)


def _last_activity_timestamp(job: PdfProcessingJob) -> datetime:
    if job.updated_at:
        return _as_utc(job.updated_at)
    if job.started_at:
        return _as_utc(job.started_at)
    return _as_utc(job.created_at)


def _reconcile_terminal_document(session, job: PdfProcessingJob) -> bool:
    """Reconcile a document from its deterministic latest failed/cancelled job."""
    if job.status not in {
        PdfJobStatus.FAILED.value,
        PdfJobStatus.CANCELLED.value,
    }:
        return False

    # Lock the document before checking the latest job. A job insert's
    # foreign-key key-share lock would wait on this row, but a second job for a
    # paper that still has an active job waits on the one-active-job unique index
    # instead, which is released only when this transaction ends. So this lock
    # alone does not order such an insert after the decision below: a caller
    # adding a job to an existing paper must lock the paper row first.
    document = session.execute(
        select(PDFDocument).where(PDFDocument.id == job.document_id).with_for_update()
    ).scalar_one_or_none()
    if document is None:
        return False

    latest_job = session.execute(
        select(PdfProcessingJob)
        .where(PdfProcessingJob.document_id == job.document_id)
        .order_by(*_JOB_CREATION_ORDERING)
        .limit(1)
    ).scalar_one_or_none()
    if latest_job is None or latest_job.id != job.id:
        return False

    if document.status in TERMINAL_PROCESSING_STATUSES:
        return False

    terminal_message = job.error_message or job.message
    if terminal_message is None:
        raise ValueError(f"Terminal job {job.id} has no failure message to reconcile")
    if job.completed_at is None:
        raise ValueError(f"Terminal job {job.id} has no completed_at to reconcile")

    mapped_status = PDF_JOB_STATUS_TO_PROCESSING_STATUS[job.status]
    document.status = mapped_status
    if document.processing_started_at is None:
        document.processing_started_at = job.started_at or job.created_at
    document.processing_completed_at = job.completed_at
    document.error_message = terminal_message[
        :get_pdf_document_error_message_max_chars()
    ]
    # A new paper's files cannot outlive its failed or cancelled import as pending.
    for file_row in document.files:
        if file_row.status == FILE_STATUS_PENDING:
            file_row.status = FILE_STATUS_FAILED
            file_row.error_message = document.error_message
    return True


def _reconcile_stale_job(
    session,
    job: PdfProcessingJob,
    *,
    stale_after_seconds: int,
    now: datetime,
) -> bool:
    if job.status not in _ACTIVE_STATUSES:
        return False

    if stale_after_seconds <= 0:
        return False

    last_activity = _last_activity_timestamp(job)
    age_seconds = (now - last_activity).total_seconds()
    if age_seconds < stale_after_seconds:
        return False

    if job.started_at is None:
        job.started_at = last_activity
    job.completed_at = now

    if job.status == PdfJobStatus.CANCEL_REQUESTED.value:
        job.status = PdfJobStatus.CANCELLED.value
        job.current_stage = "cancelled"
        job.cancel_requested = True
        job.error_message = None
        job.message = "Cancellation finalized automatically after stale inactivity"
    else:
        stale_message = (
            "Job marked failed automatically after stale inactivity; "
            "likely interrupted before terminal state update"
        )
        job.status = PdfJobStatus.FAILED.value
        job.current_stage = job.current_stage or "failed"
        job.error_message = stale_message
        job.message = stale_message

    _store_terminal_metadata(
        job,
        metadata=None,
        outcome=(
            "cancelled"
            if job.status == PdfJobStatus.CANCELLED.value
            else "failed"
        ),
        completed_at=now,
    )

    _reconcile_terminal_document(session, job)
    return True


def _configure_orphan_repair_timeout(session, timeout_seconds: int) -> None:
    """Apply a transaction-local PostgreSQL timeout when supported."""
    get_bind = getattr(session, "get_bind", None)
    if not callable(get_bind):
        return
    bind = get_bind()
    if getattr(getattr(bind, "dialect", None), "name", None) != "postgresql":
        return
    session.execute(
        text("SELECT set_config('statement_timeout', :timeout, true)"),
        {"timeout": f"{timeout_seconds * 1000}ms"},
    )


def _terminalize_no_job_orphan(
    session,
    document: PDFDocument,
    *,
    now: datetime,
) -> PdfProcessingJob:
    """Create the canonical failed job and reconcile its pending document."""
    job = PdfProcessingJob(
        document_id=document.id,
        user_id=document.user_id,
        filename=document.filename,
        status=PdfJobStatus.FAILED.value,
        current_stage="failed",
        progress_percentage=0,
        message=NO_JOB_ORPHAN_FAILURE_MESSAGE,
        cancel_requested=False,
        error_message=NO_JOB_ORPHAN_FAILURE_MESSAGE,
        started_at=document.upload_timestamp,
        updated_at=now,
        completed_at=now,
    )
    _store_terminal_metadata(
        job,
        metadata=None,
        outcome="failed",
        completed_at=now,
    )
    session.add(job)
    session.flush()
    if not _reconcile_terminal_document(session, job):
        raise RuntimeError(
            f"Pending no-job orphan {document.id} could not be terminalized"
        )
    return job


def _reconcile_pending_documents_without_jobs(
    session,
    *,
    apply: bool,
    cutoff: datetime,
    batch_size: int,
    now: datetime,
) -> NoJobOrphanRepairSummary:
    """Select one bounded orphan batch and optionally reconcile it."""
    has_job = exists(
        select(PdfProcessingJob.id).where(
            PdfProcessingJob.document_id == PDFDocument.id
        )
    )
    aged_pending_without_job = (
        PDFDocument.status == "pending",
        PDFDocument.upload_timestamp <= cutoff,
        ~has_job,
    )
    # An unstarted upload never produced processed or extraction output and never
    # got pieces in the search index. A paper whose main file has any of these was
    # processed, so it is reported and never marked failed.
    was_processed = exists(
        select(PDFDocumentFile.id).where(
            PDFDocumentFile.document_id == PDFDocument.id,
            PDFDocumentFile.role == FILE_ROLE_MAIN,
            or_(
                PDFDocumentFile.processed_json_path.is_not(None),
                PDFDocumentFile.pdfx_json_path.is_not(None),
                PDFDocumentFile.chunk_index_start.is_not(None),
            ),
        )
    )
    statement = (
        select(PDFDocument)
        .where(*aged_pending_without_job, ~was_processed)
        .order_by(PDFDocument.upload_timestamp.asc(), PDFDocument.id.asc())
        .limit(batch_size)
    )
    if apply:
        statement = statement.with_for_update(skip_locked=True)

    documents = session.execute(statement).scalars().all()
    records: list[NoJobOrphanRepairRecord] = []
    for document in documents:
        job = (
            _terminalize_no_job_orphan(session, document, now=now)
            if apply
            else None
        )
        records.append(
            NoJobOrphanRepairRecord(
                document_id=str(document.id),
                upload_timestamp=document.upload_timestamp,
                status="failed" if apply else "would_fail",
                reason=NO_JOB_ORPHAN_FAILURE_MESSAGE,
                job_id=str(job.id) if job is not None else None,
            )
        )

    skipped_documents = session.execute(
        select(PDFDocument)
        .where(*aged_pending_without_job, was_processed)
        .order_by(PDFDocument.upload_timestamp.asc(), PDFDocument.id.asc())
    ).scalars().all()

    return NoJobOrphanRepairSummary(
        dry_run=not apply,
        cutoff=cutoff,
        batch_size=batch_size,
        records=tuple(records),
        skipped=tuple(
            NoJobOrphanRepairRecord(
                document_id=str(document.id),
                upload_timestamp=document.upload_timestamp,
                status="skipped",
                reason=NO_JOB_ORPHAN_SKIPPED_REASON,
            )
            for document in skipped_documents
        ),
    )


def reconcile_pending_documents_without_jobs(
    *,
    apply: bool | None = None,
    now: datetime | None = None,
) -> NoJobOrphanRepairSummary:
    """Run the bounded, idempotent manual repair with transient DB retries."""
    should_apply = (
        get_pdf_no_job_orphan_repair_apply()
        if apply is None
        else apply
    )
    current_time = _as_utc(now or datetime.now(timezone.utc))
    threshold_seconds = get_pdf_no_job_orphan_threshold_seconds()
    cutoff = current_time - timedelta(seconds=threshold_seconds)
    batch_size = get_pdf_no_job_orphan_batch_size()
    timeout_seconds = get_pdf_no_job_orphan_repair_timeout_seconds()
    retry_count = get_pdf_no_job_orphan_repair_retry_count()

    for attempt in range(retry_count + 1):
        session = SessionLocal()
        try:
            _configure_orphan_repair_timeout(session, timeout_seconds)
            summary = _reconcile_pending_documents_without_jobs(
                session,
                apply=should_apply,
                cutoff=cutoff,
                batch_size=batch_size,
                now=current_time,
            )
            if should_apply:
                session.commit()
            else:
                session.rollback()
            return summary
        except OperationalError:
            session.rollback()
            if attempt >= retry_count:
                raise
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    raise RuntimeError("No-job orphan reconciliation exhausted its retry loop")


def _violates_constraint(error: IntegrityError, name: str) -> bool:
    """Whether PostgreSQL reports ``name`` as the violated constraint or index."""

    diag = getattr(getattr(error, "orig", None), "diag", None)
    return getattr(diag, "constraint_name", None) == name


class ActiveJobExistsError(RuntimeError):
    """The paper already has an active processing job; a second one is refused."""

    def __init__(self, document_id: UUID | str) -> None:
        super().__init__(f"Document {document_id} already has an active processing job")
        self.document_id = document_id


def create_job(*, document_id: UUID | str, user_id: int, filename: Optional[str] = None) -> PdfJobResponse:
    """Create a new pending job for a paper.

    A paper has at most one active job (``ONE_ACTIVE_JOB_PER_PAPER_INDEX``);
    a second one raises ``ActiveJobExistsError``.
    """
    session = SessionLocal()
    try:
        job = PdfProcessingJob(
            document_id=_to_uuid(document_id),
            user_id=user_id,
            filename=filename,
            status=PdfJobStatus.PENDING.value,
            current_stage="pending",
            progress_percentage=0,
            message="Queued for background processing",
            cancel_requested=False,
        )
        session.add(job)
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            if _violates_constraint(exc, ONE_ACTIVE_JOB_PER_PAPER_INDEX):
                raise ActiveJobExistsError(document_id) from exc
            raise
        session.refresh(job)
        return _to_response(job)
    finally:
        session.close()


def get_job(*, job_id: UUID | str, user_id: int, reconcile_stale: bool = True) -> Optional[PdfJobResponse]:
    """Return a single job owned by a user."""
    session = SessionLocal()
    try:
        statement = (
            select(PdfProcessingJob).where(
                PdfProcessingJob.id == _to_uuid(job_id),
                PdfProcessingJob.user_id == user_id,
            )
        )
        if reconcile_stale:
            statement = statement.with_for_update()
        job = session.execute(statement).scalar_one_or_none()
        if job and reconcile_stale:
            now = datetime.now(timezone.utc)
            if _reconcile_stale_job(
                session,
                job,
                stale_after_seconds=_stale_timeout_seconds(),
                now=now,
            ):
                session.commit()
                session.refresh(job)
        return _to_response(job) if job else None
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_job_by_id(*, job_id: UUID | str, reconcile_stale: bool = True) -> Optional[PdfJobResponse]:
    """Return a single job by ID for internal orchestration checks only."""
    session = SessionLocal()
    try:
        statement = (
            select(PdfProcessingJob).where(PdfProcessingJob.id == _to_uuid(job_id))
        )
        if reconcile_stale:
            statement = statement.with_for_update()
        job = session.execute(statement).scalar_one_or_none()
        if job and reconcile_stale:
            now = datetime.now(timezone.utc)
            if _reconcile_stale_job(
                session,
                job,
                stale_after_seconds=_stale_timeout_seconds(),
                now=now,
            ):
                session.commit()
                session.refresh(job)
        return _to_response(job) if job else None
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_latest_job_for_document(
    *,
    document_id: UUID | str,
    user_id: int,
    reconcile_stale: bool = True,
) -> Optional[PdfJobResponse]:
    """Return most recent job for a document owned by user."""
    session = SessionLocal()
    try:
        statement = (
            select(PdfProcessingJob)
            .where(
                PdfProcessingJob.document_id == _to_uuid(document_id),
                PdfProcessingJob.user_id == user_id,
            )
            .order_by(*_JOB_CREATION_ORDERING)
            .limit(1)
        )
        if reconcile_stale:
            statement = statement.with_for_update()
        job = session.execute(statement).scalar_one_or_none()
        if job and reconcile_stale:
            now = datetime.now(timezone.utc)
            if _reconcile_stale_job(
                session,
                job,
                stale_after_seconds=_stale_timeout_seconds(),
                now=now,
            ):
                session.commit()
                session.refresh(job)
        return _to_response(job) if job else None
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _observe_abandoned_snapshot(task: asyncio.Task) -> None:
    _pending_snapshots.discard(task)
    if task.cancelled():
        return
    try:
        task.result()
    except Exception:
        try:
            report_runtime_exception(
                sanitized_runtime_error("Abandoned PDF job snapshot failed"),
                component="pdf_jobs",
                operation="abandoned_snapshot_failed",
            )
        except Exception:
            logger.warning(
                "Failed to report abandoned PDF job snapshot failure",
                extra={"sentry_skip_event": True},
            )


async def snapshot_jobs_async(
    *, user_id: int, window_days: int = 7, limit: int = 50,
) -> list[PdfJobResponse]:
    """Let live callers own failures; observe workers abandoned by cancellation."""
    task = asyncio.create_task(asyncio.to_thread(
        snapshot_jobs, user_id=user_id, window_days=window_days, limit=limit,
    ))
    _pending_snapshots.add(task)
    try:
        result = await asyncio.shield(task)
    except asyncio.CancelledError:
        task.add_done_callback(_observe_abandoned_snapshot)
        raise
    except BaseException:
        _pending_snapshots.discard(task)
        raise
    else:
        _pending_snapshots.discard(task)
        return result


def snapshot_jobs(*, user_id: int, window_days: int = 7, limit: int = 50) -> list[PdfJobResponse]:
    """Read one bounded page without counts, reconciliation, or writes.

    The caller runs this entire operation in a worker: no session or ORM row
    escapes, including when querying or DTO conversion fails.
    """
    session = SessionLocal()
    try:
        since = datetime.now(timezone.utc) - timedelta(days=max(1, min(window_days, 90)))
        rows = session.execute(
            select(PdfProcessingJob).where(
                PdfProcessingJob.user_id == user_id,
                PdfProcessingJob.created_at >= since,
            ).order_by(*_JOB_CREATION_ORDERING).limit(max(1, min(limit, 200)))
        ).scalars().all()
        return [_to_response(row) for row in rows]
    finally:
        session.close()


def list_jobs(
    *,
    user_id: int,
    window_days: int = 7,
    statuses: Optional[Iterable[str]] = None,
    limit: int = 50,
    offset: int = 0,
    reconcile_stale: bool = True,
) -> PdfJobListResponse:
    """List user jobs within a time window, newest first."""
    session = SessionLocal()
    try:
        window_days = max(1, min(window_days, 90))
        since = datetime.now(timezone.utc) - timedelta(days=window_days)

        if reconcile_stale:
            stale_after_seconds = _stale_timeout_seconds()
            now = datetime.now(timezone.utc)
            active_rows = session.execute(
                select(PdfProcessingJob).where(
                    PdfProcessingJob.user_id == user_id,
                    PdfProcessingJob.created_at >= since,
                    PdfProcessingJob.status.in_(tuple(_ACTIVE_STATUSES)),
                ).with_for_update()
            ).scalars().all()
            changed = False
            for row in active_rows:
                changed = _reconcile_stale_job(
                    session,
                    row,
                    stale_after_seconds=stale_after_seconds,
                    now=now,
                ) or changed
            if changed:
                session.commit()

        stmt = select(PdfProcessingJob).where(
            PdfProcessingJob.user_id == user_id,
            PdfProcessingJob.created_at >= since,
        )
        count_stmt = select(func.count(PdfProcessingJob.id)).where(
            PdfProcessingJob.user_id == user_id,
            PdfProcessingJob.created_at >= since,
        )

        status_values = [str(s).strip().lower() for s in (statuses or []) if str(s).strip()]
        if status_values:
            stmt = stmt.where(PdfProcessingJob.status.in_(status_values))
            count_stmt = count_stmt.where(PdfProcessingJob.status.in_(status_values))

        total = session.execute(count_stmt).scalar_one()
        rows = session.execute(
            stmt.order_by(*_JOB_CREATION_ORDERING)
            .offset(max(0, offset))
            .limit(max(1, min(limit, 200)))
        ).scalars().all()

        return PdfJobListResponse(
            jobs=[_to_response(row) for row in rows],
            total=int(total),
            limit=max(1, min(limit, 200)),
            offset=max(0, offset),
        )
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def is_cancel_requested(*, job_id: UUID | str) -> bool:
    """Check if cancellation has been requested for a job."""
    session = SessionLocal()
    try:
        job = session.execute(
            select(PdfProcessingJob).where(PdfProcessingJob.id == _to_uuid(job_id))
        ).scalar_one_or_none()
        return bool(job.cancel_requested) if job else False
    finally:
        session.close()


def request_cancel(*, job_id: UUID | str, user_id: int) -> Optional[PdfJobResponse]:
    """Mark a job as cancel-requested (idempotent for terminal jobs)."""
    session = SessionLocal()
    try:
        job = session.execute(
            select(PdfProcessingJob).where(
                PdfProcessingJob.id == _to_uuid(job_id),
                PdfProcessingJob.user_id == user_id,
            ).with_for_update()
        ).scalar_one_or_none()
        if not job:
            return None

        if job.status not in _TERMINAL_STATUSES:
            now = datetime.now(timezone.utc)
            job.cancel_requested = True
            if _polling_stopped(job, now=now):
                # No task will see the flag (a backend restart ended the
                # wait), so the cancel is finalized here, freeing the paper.
                _finalize_cancelled(session, job, now=now)
            else:
                if job.status != PdfJobStatus.CANCEL_REQUESTED.value:
                    job.status = PdfJobStatus.CANCEL_REQUESTED.value
                if not job.message:
                    job.message = "Cancellation requested"
            session.commit()
            session.refresh(job)

        return _to_response(job)
    finally:
        session.close()


def _polling_stopped(job: PdfProcessingJob, *, now: datetime) -> bool:
    """Whether a job in a polling stage has recorded no poll for too long."""
    if job.current_stage not in _POLLING_STAGES:
        return False
    dead_after_seconds = get_document_source_dead_wait_polls() * (
        get_document_source_poll_interval_seconds()
        + get_document_source_request_timeout_seconds()
    )
    return (now - _last_activity_timestamp(job)).total_seconds() > dead_after_seconds


def _finalize_cancelled(session, job: PdfProcessingJob, *, now: datetime) -> None:
    """Make a user's cancel terminal in this transaction, as the task would."""
    if job.started_at is None:
        job.started_at = _last_activity_timestamp(job)
    job.status = PdfJobStatus.CANCELLED.value
    job.current_stage = "cancelled"
    job.completed_at = now
    job.error_message = None
    job.message = "Processing cancelled by user request"
    _store_terminal_metadata(job, metadata=None, outcome="cancelled", completed_at=now)
    _reconcile_terminal_document(session, job)


def touch_pending_jobs(*, job_ids: Iterable[UUID | str]) -> int:
    """Record activity on jobs still queued (``pending``) behind a running one.

    A started job records its own activity; only queued ones are touched.
    Returns how many were touched.
    """
    ids = [_to_uuid(job_id) for job_id in job_ids]
    if not ids:
        return 0
    session = SessionLocal()
    try:
        result = session.execute(
            update(PdfProcessingJob)
            .where(
                PdfProcessingJob.id.in_(ids),
                PdfProcessingJob.status == PdfJobStatus.PENDING.value,
            )
            .values(updated_at=func.now())
            .execution_options(synchronize_session=False)
        )
        session.commit()
        return result.rowcount
    finally:
        session.close()


def set_process_id(*, job_id: UUID | str, process_id: str) -> Optional[PdfJobResponse]:
    """Persist upstream PDFX process ID for job tracing."""
    session = SessionLocal()
    try:
        job = session.execute(
            select(PdfProcessingJob).where(PdfProcessingJob.id == _to_uuid(job_id)).with_for_update()
        ).scalar_one_or_none()
        if not job:
            return None

        if process_id:
            job.process_id = process_id
            session.commit()
            session.refresh(job)

        return _to_response(job)
    finally:
        session.close()


def update_progress(
    *,
    job_id: UUID | str,
    stage: Optional[str] = None,
    progress_percentage: Optional[int] = None,
    message: Optional[str] = None,
    status: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> Optional[PdfJobResponse]:
    """Update job progress details while it is active."""
    session = SessionLocal()
    try:
        job = session.execute(
            select(PdfProcessingJob).where(PdfProcessingJob.id == _to_uuid(job_id)).with_for_update()
        ).scalar_one_or_none()
        if not job:
            return None

        if job.status in _TERMINAL_STATUSES:
            return _to_response(job)

        if stage:
            job.current_stage = str(stage)
        clamped = _clamp_progress(progress_percentage)
        if clamped is not None:
            job.progress_percentage = clamped
        if message:
            job.message = message
        if metadata is not None:
            current_metadata = dict(job.metadata_json or {})
            current_metadata.update(metadata)
            job.metadata_json = current_metadata

        requested_status = (status or "").strip().lower() or PdfJobStatus.RUNNING.value
        if job.cancel_requested and requested_status == PdfJobStatus.RUNNING.value:
            requested_status = PdfJobStatus.CANCEL_REQUESTED.value
        job.status = requested_status

        now = datetime.now(timezone.utc)
        if job.started_at is None and job.status in {PdfJobStatus.RUNNING.value, PdfJobStatus.CANCEL_REQUESTED.value}:
            job.started_at = now
        if job.current_stage in _POLLING_STAGES:
            # A provider wait's every poll is activity, even when no other
            # column changes (it repeats the same answer): ``onupdate`` alone
            # would not fire, and the dead-wait cancel and the stale sweep
            # would take a living wait for a dead one. Other stages keep
            # ``onupdate`` (a write only when something changed).
            job.updated_at = now

        session.commit()
        session.refresh(job)
        return _to_response(job)
    finally:
        session.close()


def mark_completed(
    *,
    job_id: UUID | str,
    message: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> Optional[PdfJobResponse]:
    """Mark job terminal success state."""
    session = SessionLocal()
    try:
        job = session.execute(
            select(PdfProcessingJob).where(PdfProcessingJob.id == _to_uuid(job_id)).with_for_update()
        ).scalar_one_or_none()
        if not job:
            return None
        if job.status in _TERMINAL_STATUSES:
            return _to_response(job)

        now = datetime.now(timezone.utc)
        if job.started_at is None:
            job.started_at = now
        job.completed_at = now
        job.status = PdfJobStatus.COMPLETED.value
        job.current_stage = "completed"
        job.progress_percentage = 100
        job.message = message or "Processing completed"
        job.error_message = None
        _store_terminal_metadata(
            job,
            metadata=metadata,
            outcome="completed",
            completed_at=now,
        )

        session.commit()
        session.refresh(job)
        return _to_response(job)
    finally:
        session.close()


def mark_failed(
    *,
    job_id: UUID | str,
    message: str,
    stage: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> Optional[PdfJobResponse]:
    """Mark job terminal failure state."""
    session = SessionLocal()
    try:
        job = session.execute(
            select(PdfProcessingJob).where(PdfProcessingJob.id == _to_uuid(job_id)).with_for_update()
        ).scalar_one_or_none()
        if not job:
            return None
        if job.status in _TERMINAL_STATUSES:
            if _reconcile_terminal_document(session, job):
                session.commit()
                session.refresh(job)
            return _to_response(job)

        now = datetime.now(timezone.utc)
        if job.started_at is None:
            job.started_at = now
        job.completed_at = now
        job.status = PdfJobStatus.FAILED.value
        job.current_stage = stage or job.current_stage or "failed"
        job.error_message = (message or "Processing failed")[
            :get_pdf_job_error_message_max_chars()
        ]
        job.message = job.error_message
        _store_terminal_metadata(
            job,
            metadata=metadata,
            outcome="failed",
            completed_at=now,
        )

        _reconcile_terminal_document(session, job)
        session.commit()
        session.refresh(job)
        return _to_response(job)
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def mark_cancelled(
    *,
    job_id: UUID | str,
    reason: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> Optional[PdfJobResponse]:
    """Mark job terminal cancelled state."""
    session = SessionLocal()
    try:
        job = session.execute(
            select(PdfProcessingJob).where(PdfProcessingJob.id == _to_uuid(job_id)).with_for_update()
        ).scalar_one_or_none()
        if not job:
            return None
        if job.status in _TERMINAL_STATUSES:
            if _reconcile_terminal_document(session, job):
                session.commit()
                session.refresh(job)
            return _to_response(job)

        now = datetime.now(timezone.utc)
        if job.started_at is None:
            job.started_at = now
        job.completed_at = now
        job.cancel_requested = True
        job.status = PdfJobStatus.CANCELLED.value
        job.current_stage = "cancelled"
        job.message = reason or "Cancelled by user"
        job.error_message = None
        _store_terminal_metadata(
            job,
            metadata=metadata,
            outcome="cancelled",
            completed_at=now,
        )

        _reconcile_terminal_document(session, job)
        session.commit()
        session.refresh(job)
        return _to_response(job)
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
