"""Lifespan-owned, bounded recovery of abandoned durable PDF jobs."""

import asyncio
from datetime import datetime, timedelta, timezone
import logging
from uuid import UUID

from sqlalchemy import func, select, text

from src.lib.observability.runtime import report_runtime_exception, sanitized_runtime_error
from src.lib.openai_agents.config import (
    get_pdf_job_recovery_batch_size,
    get_pdf_job_recovery_interval_seconds,
    get_pdf_job_recovery_timeout_seconds,
)
from src.models.sql.database import SessionLocal
from src.models.sql.pdf_processing_job import PdfProcessingJob

from . import service

logger = logging.getLogger(__name__)
_recovery_task: asyncio.Task | None = None
_RECOVERY_LOCK_KEY = "pdf_job_stale_recovery"


def _report_failure() -> None:
    report_runtime_exception(
        sanitized_runtime_error("PDF job stale recovery failed"),
        component="pdf_job_recovery",
        operation="reconcile_stale_jobs",
    )
    logger.error("PDF job stale recovery failed", extra={"sentry_skip_event": True})


def reconcile_stale_jobs_once(*, after: UUID | None = None, now: datetime | None = None) -> UUID | None:
    """Recover one global page, returning a keyset cursor for fair retries.

    The transaction advisory lock excludes competing sweep owners; row locks
    serialize with progress, cancellation, finalizers, and REST read repair.
    Eligibility is selected under lock from current database values. Failed rows
    roll back to a savepoint and are retried after the cursor wraps, so a damaged
    row cannot starve later users. No connection survives the pass.
    """
    session = SessionLocal()
    try:
        session.execute(
            text("SELECT set_config('statement_timeout', :timeout, true)"),
            {"timeout": f"{get_pdf_job_recovery_timeout_seconds() * 1000}ms"},
        )
        acquired = session.execute(
            text("SELECT pg_try_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": _RECOVERY_LOCK_KEY},
        ).scalar_one()
        if not acquired:
            return after
        current_time = service._as_utc(now or datetime.now(timezone.utc))
        stale_seconds = service._stale_timeout_seconds()
        activity = func.coalesce(
            PdfProcessingJob.updated_at, PdfProcessingJob.started_at, PdfProcessingJob.created_at,
        )
        statement = select(PdfProcessingJob).where(
            PdfProcessingJob.status.in_(tuple(service._ACTIVE_STATUSES)),
            activity <= current_time - timedelta(seconds=stale_seconds),
        )
        if after is not None:
            statement = statement.where(PdfProcessingJob.id > after)
        batch_size = get_pdf_job_recovery_batch_size()
        rows = session.execute(
            statement.order_by(PdfProcessingJob.id).limit(batch_size)
            .with_for_update(skip_locked=True)
        ).scalars().all()
        cursor = rows[-1].id if len(rows) == batch_size else None
        for job in rows:
            try:
                with session.begin_nested():
                    service._reconcile_stale_job(
                        session, job, stale_after_seconds=stale_seconds, now=current_time,
                    )
                    session.flush()
            except Exception:
                _report_failure()
        session.commit()
        return cursor
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


async def _run_recovery() -> None:
    cursor = None
    while True:
        # Shield the worker so shutdown waits for its finally/session close.
        work = asyncio.create_task(asyncio.to_thread(reconcile_stale_jobs_once, after=cursor))
        try:
            cursor = await asyncio.shield(work)
        except asyncio.CancelledError:
            try:
                await work
            except Exception:
                _report_failure()
            raise
        except Exception:
            _report_failure()
        await asyncio.sleep(get_pdf_job_recovery_interval_seconds())


def schedule_pdf_job_recovery() -> asyncio.Task:
    """Start once per application process; database locking coordinates owners."""
    global _recovery_task
    if _recovery_task is None or _recovery_task.done():
        _recovery_task = asyncio.create_task(_run_recovery(), name="pdf-job-stale-recovery")
    return _recovery_task


async def stop_pdf_job_recovery() -> None:
    """Stop scheduling and await any in-flight worker before database shutdown."""
    global _recovery_task
    task = _recovery_task
    if task is None:
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    finally:
        if _recovery_task is task:
            _recovery_task = None
