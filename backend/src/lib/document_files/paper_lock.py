"""One lock per paper that orders a paper's delete, an add to it and a file removal (KANBAN-1976 / ALL-1460, P7c1, P10b).

A delete checks for an active job, wipes the paper's pieces from the search index,
then deletes its rows. An add creates the paper's job and then writes pieces. If an
add could start between the delete's check and its row delete, its pieces could be
written after the wipe and outlive the paper.

The lock is a PostgreSQL advisory lock keyed by the paper id, not the paper row's
own lock: the search-index delete helper removes the paper row in a session of its
own, which a row lock held by the endpoint would block forever.

**Nobody ever waits for it.** Both sides use the ``try`` form and give up at once
when it is held: a delete answers "another operation on this paper is in progress",
an add answers ``import_in_progress``. Every database call of the lock runs in a
worker thread, never on the event loop.

- ``paper_operation_lock`` (async) holds the lock for the whole of a paper delete
  (from the active-job check through the row delete) or a file removal, on a
  connection of its own. That
  connection stays checked out of the pool for the delete's few seconds: a
  session-level advisory lock lives exactly as long as its session, so it has to.
  It is released when the delete ends, succeeds, fails or is cancelled; if the
  release itself fails (or the lock's connection dropped), the connection is
  invalidated, which ends its database session and so the lock, and the delete's
  own outcome is answered (logged and reported to Sentry). A dropped connection ends
  the lock too.
- ``try_lock_paper_for_add`` takes it inside the add's job-creating transaction, so
  it is released when that transaction commits or rolls back.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from src.lib.observability import runtime


logger = logging.getLogger(__name__)
_detached_releases: set[asyncio.Task[None]] = set()


class PaperOperationInProgress(RuntimeError):
    """Another delete of, or add to, this paper holds its lock right now."""


def paper_lock_key(document_id: UUID | str) -> int:
    """The paper's advisory-lock key: the first 8 bytes of its UUID as a signed 64-bit integer."""

    return int.from_bytes(UUID(str(document_id)).bytes[:8], "big", signed=True)


def try_lock_paper_for_add(session: Session, document_id: UUID | str) -> bool:
    """Take the paper's lock for the rest of ``session``'s transaction, or return False at once."""

    return bool(session.execute(
        text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": paper_lock_key(document_id)},
    ).scalar_one())


def _acquire(key: int) -> Any | None:
    """In a worker thread: a dedicated connection holding the lock, or None when it is held."""

    from src.models.sql.database import engine

    connection = engine.connect()
    try:
        taken = bool(connection.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": key}).scalar_one())
        connection.commit()
    except BaseException:
        connection.invalidate()
        connection.close()
        raise
    if not taken:
        connection.close()
        return None
    return connection


def _release(connection: Any, key: int) -> None:
    """In a worker thread: unlock and return the connection; if unlocking fails, end its session."""

    try:
        connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
        connection.commit()
    except BaseException:
        connection.invalidate()
        raise
    finally:
        connection.close()


@asynccontextmanager
async def paper_operation_lock(document_id: UUID | str) -> AsyncIterator[None]:
    """Hold the paper's lock while it is deleted or a file is removed; ``PaperOperationInProgress`` when held."""

    key = paper_lock_key(document_id)
    acquiring = asyncio.ensure_future(asyncio.to_thread(_acquire, key))
    try:
        connection = await asyncio.shield(acquiring)
    except asyncio.CancelledError:
        # The request was cancelled while the thread was taking the lock: release it
        # as soon as the thread is done, so nothing is left holding it.
        def release_when_done(task: asyncio.Future) -> None:
            if task.cancelled():
                return
            try:
                acquired = task.result()
            except Exception as exc:
                message = f"Acquiring a paper's lock after cancellation failed ({type(exc).__name__})"
                _report_lock_failure(
                    runtime.sanitized_runtime_error(message), "paper_lock_acquire_failed_after_cancel", message,
                )
                return
            if acquired is not None:
                releasing = asyncio.create_task(asyncio.to_thread(_release, acquired, key))
                _detached_releases.add(releasing)
                releasing.add_done_callback(_observe_detached_release)

        acquiring.add_done_callback(release_when_done)
        raise
    if connection is None:
        raise PaperOperationInProgress(str(document_id))
    try:
        yield
    finally:
        # Shielded so a cancelled request still finishes releasing in its thread.
        try:
            await asyncio.shield(asyncio.to_thread(_release, connection, key))
        except Exception as exc:
            # ``_release`` invalidated the connection, which ends its database session
            # and so the lock. The delete's own outcome stands: a delete that finished
            # is answered as finished. Logged and reported, never raised.
            _report_release_failure(type(exc).__name__)


def _observe_detached_release(task: asyncio.Task[None]) -> None:
    _detached_releases.discard(task)
    if task.cancelled():
        return
    try:
        task.result()
    except Exception as exc:
        _report_release_failure(type(exc).__name__)


def _report_release_failure(error_type: str) -> None:
    class PaperLockReleaseFailed(RuntimeError):
        pass

    try:
        raise PaperLockReleaseFailed(f"Releasing a paper's delete lock failed ({error_type})") from None
    except PaperLockReleaseFailed as error:
        error.__context__ = None
        error.__cause__ = None
        _report_lock_failure(error, "paper_lock_release_failed", f"{error}; its connection was ended")


def _report_lock_failure(error: RuntimeError, operation: str, log_message: str) -> None:
    logger.warning(log_message, extra={"sentry_skip_event": True})
    try:
        runtime.report_runtime_exception(
            error, component="document_files", operation=operation, level="warning", fingerprint=[operation],
        )
    except Exception:
        logger.warning("Failed to report paper lock failure", extra={"sentry_skip_event": True})
