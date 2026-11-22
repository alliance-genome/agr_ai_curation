"""One lock per paper that orders a paper's delete against an add to it (KANBAN-1976 / ALL-1460, P7c1).

A delete checks for an active job, wipes the paper's pieces from the search index,
then deletes its rows. An add creates the paper's job and then writes pieces. If an
add could start between the delete's check and its row delete, its pieces could be
written after the wipe and outlive the paper.

The lock is a PostgreSQL advisory lock keyed by the paper id, not the paper row's
own lock: the search-index delete helper removes the paper row in a session of its
own, which a row lock held by the endpoint would block forever. Advisory locks are
taken only here and by ``start_supplement_add``:

- ``paper_delete_lock`` holds the lock on its own connection for the whole delete
  (the active-job check through the row delete), and releases it when the delete
  ends, succeeds or fails, or when its connection closes.
- ``lock_paper_for_add`` takes it inside the add's job-creating transaction, so it is
  released when that transaction commits or rolls back.

A delete waits only while an add is creating its job (one short transaction); an add
waits while a delete of the same paper runs, then finds the paper gone.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session


def paper_lock_key(document_id: UUID | str) -> int:
    """The paper's advisory-lock key: the first 8 bytes of its UUID as a signed 64-bit integer."""

    return int.from_bytes(UUID(str(document_id)).bytes[:8], "big", signed=True)


def lock_paper_for_add(session: Session, document_id: UUID | str) -> None:
    """Take the paper's lock for the rest of ``session``'s transaction."""

    session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": paper_lock_key(document_id)})


@contextmanager
def paper_delete_lock(document_id: UUID | str) -> Iterator[None]:
    """Hold the paper's lock on a connection of its own while the paper is deleted."""

    from src.models.sql.database import engine

    key = paper_lock_key(document_id)
    with engine.connect() as connection:
        connection.execute(text("SELECT pg_advisory_lock(:key)"), {"key": key})
        connection.commit()
        try:
            yield
        finally:
            connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
            connection.commit()
