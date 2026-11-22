"""One lock per paper that orders a paper's delete against an add to it (KANBAN-1976 / ALL-1460, P7c1).

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

- ``paper_delete_lock`` (async) holds the lock for the whole delete, from the
  active-job check through the row delete, on a connection of its own. That
  connection stays checked out of the pool for the delete's few seconds: a
  session-level advisory lock lives exactly as long as its session, so it has to.
  It is released when the delete ends, succeeds, fails or is cancelled; if the
  release itself fails, the connection is invalidated, which ends its database
  session and so the lock. A dropped connection ends it too.
- ``try_lock_paper_for_add`` takes it inside the add's job-creating transaction, so
  it is released when that transaction commits or rolls back.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session


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
async def paper_delete_lock(document_id: UUID | str) -> AsyncIterator[None]:
    """Hold the paper's lock while it is deleted; ``PaperOperationInProgress`` when it is held."""

    key = paper_lock_key(document_id)
    acquiring = asyncio.ensure_future(asyncio.to_thread(_acquire, key))
    try:
        connection = await asyncio.shield(acquiring)
    except asyncio.CancelledError:
        # The request was cancelled while the thread was taking the lock: release it
        # as soon as the thread is done, so nothing is left holding it.
        def release_when_done(task: asyncio.Future) -> None:
            if not task.cancelled() and task.exception() is None and task.result() is not None:
                asyncio.ensure_future(asyncio.to_thread(_release, task.result(), key))

        acquiring.add_done_callback(release_when_done)
        raise
    if connection is None:
        raise PaperOperationInProgress(str(document_id))
    try:
        yield
    finally:
        # Shielded so a cancelled request still finishes releasing in its thread.
        await asyncio.shield(asyncio.to_thread(_release, connection, key))
