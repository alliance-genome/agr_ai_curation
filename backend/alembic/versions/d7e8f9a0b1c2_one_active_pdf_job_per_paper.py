"""One active processing job per paper.

Adds a partial unique index on ``pdf_processing_jobs(document_id)`` over the
active statuses (``pending``, ``running``, ``cancel_requested``). A paper can
then hold several files without two jobs importing into it at once.

Live data can make this fail: a paper that already has two or more active
jobs. The migration does not pick a job to keep. It stops with
``ActivePdfJobsMigrationError`` naming the internal paper ids, and the jobs are
looked at by hand before the release (read-only pre-check in
docs/developer/guides/PAPER_FILES_MIGRATION.md). Forward-only; the downgrade
raises.

Revision ID: d7e8f9a0b1c2
Revises: c6d7e8f9a0b1
Create Date: 2026-11-21
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op


revision: str = "d7e8f9a0b1c2"
down_revision: Union[str, Sequence[str], None] = "c6d7e8f9a0b1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_INDEX_NAME = "uq_pdf_processing_jobs_one_active_per_document"
_ACTIVE_STATUSES = ("pending", "running", "cancel_requested")
_ID_LIST_LIMIT = 50


class ActivePdfJobsMigrationError(RuntimeError):
    """Some papers already have more than one active processing job."""


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT document_id FROM pdf_processing_jobs "
            "WHERE status IN :statuses "
            "GROUP BY document_id HAVING count(*) > 1 ORDER BY document_id"
        ).bindparams(sa.bindparam("statuses", expanding=True)),
        {"statuses": list(_ACTIVE_STATUSES)},
    ).all()
    if rows:
        ids = [str(row[0]) for row in rows]
        shown = ", ".join(ids[:_ID_LIST_LIMIT])
        more = len(ids) - _ID_LIST_LIMIT
        raise ActivePdfJobsMigrationError(
            f"{len(ids)} paper(s) have more than one active processing job; "
            "resolve them by hand before upgrading: "
            + (f"{shown} (and {more} more)" if more > 0 else shown)
        )
    op.create_index(
        _INDEX_NAME,
        "pdf_processing_jobs",
        ["document_id"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('pending', 'running', 'cancel_requested')"
        ),
    )


def downgrade() -> None:
    raise RuntimeError(
        "One active job per paper is forward-only; inside the maintenance window, "
        "restore the pre-migration database backup, and after traffic reopens fix forward"
    )
