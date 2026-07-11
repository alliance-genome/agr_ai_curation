"""Periodic retention cleanup for terminal direct-submission attempts."""

import asyncio
from datetime import datetime, timezone
import logging

from src.lib.openai_agents.config import get_submission_attempt_cleanup_interval_seconds
from src.models.sql.database import SessionLocal

from .session_submission_service import purge_expired_submission_attempts


logger = logging.getLogger(__name__)
_cleanup_task: asyncio.Task[None] | None = None


def purge_submission_attempts_once() -> int:
    """Purge expired terminal attempts in an independent transaction."""

    with SessionLocal() as db:
        try:
            deleted_count = purge_expired_submission_attempts(
                db,
                before=datetime.now(timezone.utc),
            )
            db.commit()
        except Exception:
            db.rollback()
            raise

    if deleted_count:
        logger.info("Purged %d expired submission attempt(s)", deleted_count)
    return deleted_count


async def _run_submission_attempt_cleanup() -> None:
    """Run retention cleanup serially for the lifetime of this backend process."""

    interval_seconds = get_submission_attempt_cleanup_interval_seconds()
    while True:
        try:
            await asyncio.to_thread(purge_submission_attempts_once)
        except Exception:
            logger.exception("Submission attempt retention cleanup failed")
        await asyncio.sleep(interval_seconds)


def schedule_submission_attempt_cleanup() -> asyncio.Task[None]:
    """Start the process-local periodic cleanup task once."""

    global _cleanup_task
    if _cleanup_task is None or _cleanup_task.done():
        _cleanup_task = asyncio.create_task(
            _run_submission_attempt_cleanup(),
            name="submission-attempt-retention-cleanup",
        )
    return _cleanup_task


async def stop_submission_attempt_cleanup() -> None:
    """Cancel and await the process-local cleanup task during shutdown."""

    global _cleanup_task
    task = _cleanup_task
    if task is None:
        return

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    finally:
        if _cleanup_task is task:
            _cleanup_task = None
