"""Periodic retention cleanup for terminal direct-submission attempts."""

import asyncio
from datetime import datetime, timezone
import logging

from sqlalchemy import text
from sqlalchemy.engine import Connection

from src.lib.openai_agents.config import get_submission_attempt_cleanup_interval_seconds
from src.models.sql.database import SessionLocal, engine

from .session_submission_service import purge_expired_submission_attempts


logger = logging.getLogger(__name__)
_cleanup_task: asyncio.Task[None] | None = None
_CLEANUP_LEADER_LOCK_KEY = "agr_ai_curation_submission_attempt_cleanup"


def _open_cleanup_leadership_connection() -> Connection:
    """Open the dedicated autocommit connection that owns cleanup leadership."""

    connection = engine.connect()
    try:
        return connection.execution_options(isolation_level="AUTOCOMMIT")
    except Exception:
        connection.close()
        raise


async def _open_cleanup_leadership_connection_in_thread() -> Connection:
    """Open off-loop and close a late result if task cancellation wins the race."""

    open_task = asyncio.create_task(
        asyncio.to_thread(_open_cleanup_leadership_connection)
    )
    try:
        return await asyncio.shield(open_task)
    except asyncio.CancelledError:
        try:
            connection = await open_task
        except Exception:
            logger.debug(
                "Submission attempt cleanup connection checkout failed during shutdown",
                exc_info=True,
            )
        else:
            await asyncio.to_thread(connection.close)
        raise


def _try_acquire_cleanup_leadership(connection: Connection) -> bool:
    """Claim database-wide cleanup leadership on this session's connection."""

    result = connection.execute(
        text(
            "SELECT pg_try_advisory_lock("
            "hashtextextended(:lock_key, 0)"
            ")"
        ),
        {"lock_key": _CLEANUP_LEADER_LOCK_KEY},
    )
    return bool(result.scalar())


def _release_cleanup_leadership(connection: Connection) -> None:
    """Release cleanup leadership before the session returns its connection."""

    released = connection.execute(
        text(
            "SELECT pg_advisory_unlock("
            "hashtextextended(:lock_key, 0)"
            ")"
        ),
        {"lock_key": _CLEANUP_LEADER_LOCK_KEY},
    ).scalar()
    if not released:
        logger.warning("Submission attempt cleanup leadership lock was not held")


def _verify_cleanup_leadership_connection(connection: Connection) -> None:
    """Fail a cleanup pass if the connection holding leadership was lost."""

    connection.execute(text("SELECT 1"))


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
    """Run retention cleanup under database-wide, failover-capable leadership."""

    interval_seconds = get_submission_attempt_cleanup_interval_seconds()
    while True:
        try:
            leader_connection = (
                await _open_cleanup_leadership_connection_in_thread()
            )
            try:
                is_leader = await asyncio.to_thread(
                    _try_acquire_cleanup_leadership,
                    leader_connection,
                )
                if is_leader:
                    logger.info("Acquired submission attempt cleanup leadership")
                    try:
                        while True:
                            await asyncio.to_thread(
                                _verify_cleanup_leadership_connection,
                                leader_connection,
                            )
                            try:
                                await asyncio.to_thread(purge_submission_attempts_once)
                            except Exception:
                                logger.exception(
                                    "Submission attempt retention cleanup failed"
                                )
                            await asyncio.sleep(interval_seconds)
                    finally:
                        await asyncio.to_thread(
                            _release_cleanup_leadership,
                            leader_connection,
                        )
            finally:
                await asyncio.to_thread(leader_connection.close)
        except Exception:
            logger.exception("Submission attempt cleanup leadership coordination failed")
        await asyncio.sleep(interval_seconds)


def schedule_submission_attempt_cleanup() -> asyncio.Task[None]:
    """Start the database-coordinated periodic cleanup task once per process."""

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
