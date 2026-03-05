"""Service helpers for durable PDF processing jobs."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional
from uuid import UUID

from sqlalchemy import func, select

from src.models.sql.database import SessionLocal
from src.models.sql.pdf_processing_job import PdfJobStatus, PdfProcessingJob
from src.schemas.pdf_jobs import PdfJobListResponse, PdfJobResponse


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
_DEFAULT_STALE_TIMEOUT_SECONDS = 7200
_MIN_STALE_TIMEOUT_SECONDS = 300


def _to_uuid(value: UUID | str) -> UUID:
    if isinstance(value, UUID):
        return value
    return UUID(str(value))


def _clamp_progress(value: Optional[int]) -> Optional[int]:
    if value is None:
        return None
    return max(0, min(100, int(value)))


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


def _reconcile_stale_job(job: PdfProcessingJob, *, stale_after_seconds: int, now: datetime) -> bool:
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

    return True


def create_job(*, document_id: UUID | str, user_id: int, filename: Optional[str] = None) -> PdfJobResponse:
    """Create a new pending job for an uploaded PDF document."""
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
        session.commit()
        session.refresh(job)
        return _to_response(job)
    finally:
        session.close()


def get_job(*, job_id: UUID | str, user_id: int, reconcile_stale: bool = True) -> Optional[PdfJobResponse]:
    """Return a single job owned by a user."""
    session = SessionLocal()
    try:
        job = session.execute(
            select(PdfProcessingJob).where(
                PdfProcessingJob.id == _to_uuid(job_id),
                PdfProcessingJob.user_id == user_id,
            )
        ).scalar_one_or_none()
        if job and reconcile_stale:
            now = datetime.now(timezone.utc)
            if _reconcile_stale_job(job, stale_after_seconds=_stale_timeout_seconds(), now=now):
                session.commit()
                session.refresh(job)
        return _to_response(job) if job else None
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
        job = session.execute(
            select(PdfProcessingJob)
            .where(
                PdfProcessingJob.document_id == _to_uuid(document_id),
                PdfProcessingJob.user_id == user_id,
            )
            .order_by(PdfProcessingJob.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        if job and reconcile_stale:
            now = datetime.now(timezone.utc)
            if _reconcile_stale_job(job, stale_after_seconds=_stale_timeout_seconds(), now=now):
                session.commit()
                session.refresh(job)
        return _to_response(job) if job else None
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
                )
            ).scalars().all()
            changed = False
            for row in active_rows:
                changed = _reconcile_stale_job(row, stale_after_seconds=stale_after_seconds, now=now) or changed
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
            stmt.order_by(PdfProcessingJob.created_at.desc()).offset(max(0, offset)).limit(max(1, min(limit, 200)))
        ).scalars().all()

        return PdfJobListResponse(
            jobs=[_to_response(row) for row in rows],
            total=int(total),
            limit=max(1, min(limit, 200)),
            offset=max(0, offset),
        )
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
            )
        ).scalar_one_or_none()
        if not job:
            return None

        if job.status not in _TERMINAL_STATUSES:
            job.cancel_requested = True
            if job.status != PdfJobStatus.CANCEL_REQUESTED.value:
                job.status = PdfJobStatus.CANCEL_REQUESTED.value
            if not job.message:
                job.message = "Cancellation requested"
            session.commit()
            session.refresh(job)

        return _to_response(job)
    finally:
        session.close()


def set_process_id(*, job_id: UUID | str, process_id: str) -> Optional[PdfJobResponse]:
    """Persist upstream PDFX process ID for job tracing."""
    session = SessionLocal()
    try:
        job = session.execute(
            select(PdfProcessingJob).where(PdfProcessingJob.id == _to_uuid(job_id))
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
) -> Optional[PdfJobResponse]:
    """Update job progress details while it is active."""
    session = SessionLocal()
    try:
        job = session.execute(
            select(PdfProcessingJob).where(PdfProcessingJob.id == _to_uuid(job_id))
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

        requested_status = (status or "").strip().lower() or PdfJobStatus.RUNNING.value
        if job.cancel_requested and requested_status == PdfJobStatus.RUNNING.value:
            requested_status = PdfJobStatus.CANCEL_REQUESTED.value
        job.status = requested_status

        now = datetime.now(timezone.utc)
        if job.started_at is None and job.status in {PdfJobStatus.RUNNING.value, PdfJobStatus.CANCEL_REQUESTED.value}:
            job.started_at = now

        session.commit()
        session.refresh(job)
        return _to_response(job)
    finally:
        session.close()


def mark_completed(*, job_id: UUID | str, message: Optional[str] = None) -> Optional[PdfJobResponse]:
    """Mark job terminal success state."""
    session = SessionLocal()
    try:
        job = session.execute(
            select(PdfProcessingJob).where(PdfProcessingJob.id == _to_uuid(job_id))
        ).scalar_one_or_none()
        if not job:
            return None

        now = datetime.now(timezone.utc)
        if job.started_at is None:
            job.started_at = now
        job.completed_at = now
        job.status = PdfJobStatus.COMPLETED.value
        job.current_stage = "completed"
        job.progress_percentage = 100
        job.message = message or "Processing completed"
        job.error_message = None

        session.commit()
        session.refresh(job)
        return _to_response(job)
    finally:
        session.close()


def mark_failed(*, job_id: UUID | str, message: str, stage: Optional[str] = None) -> Optional[PdfJobResponse]:
    """Mark job terminal failure state."""
    session = SessionLocal()
    try:
        job = session.execute(
            select(PdfProcessingJob).where(PdfProcessingJob.id == _to_uuid(job_id))
        ).scalar_one_or_none()
        if not job:
            return None

        now = datetime.now(timezone.utc)
        if job.started_at is None:
            job.started_at = now
        job.completed_at = now
        job.status = PdfJobStatus.FAILED.value
        job.current_stage = stage or job.current_stage or "failed"
        job.error_message = (message or "Processing failed")[:2000]
        job.message = job.error_message

        session.commit()
        session.refresh(job)
        return _to_response(job)
    finally:
        session.close()


def mark_cancelled(*, job_id: UUID | str, reason: Optional[str] = None) -> Optional[PdfJobResponse]:
    """Mark job terminal cancelled state."""
    session = SessionLocal()
    try:
        job = session.execute(
            select(PdfProcessingJob).where(PdfProcessingJob.id == _to_uuid(job_id))
        ).scalar_one_or_none()
        if not job:
            return None

        now = datetime.now(timezone.utc)
        if job.started_at is None:
            job.started_at = now
        job.completed_at = now
        job.cancel_requested = True
        job.status = PdfJobStatus.CANCELLED.value
        job.current_stage = "cancelled"
        job.message = reason or "Cancelled by user"
        job.error_message = None

        session.commit()
        session.refresh(job)
        return _to_response(job)
    finally:
        session.close()
