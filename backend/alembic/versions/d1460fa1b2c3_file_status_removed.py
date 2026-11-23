"""A file being removed from its paper: status ``removed`` (KANBAN-1976 / ALL-1460, P10b).

Widens ``ck_pdf_document_files_status`` to allow ``removed``. Removing a supplement
first marks its row ``removed`` (no reader sees it), clears its piece range, stored
files and counts, and deletes the row last; a row whose clean-up failed stays
``removed`` with its paths, so a retry or the paper delete can finish it.

Only the check constraint changes: no column, no data, no table rewrite. Every
existing row satisfies the wider check. The drop and the add run in the migration's
one transaction, so no moment exists without a status check. The add scans the
table once, under the ACCESS EXCLUSIVE lock that the drop already took; the table
holds one row per paper file, so the scan is short. ``lock_timeout`` makes the
migration fail at once instead of queueing behind a long reader (a queued ACCESS
EXCLUSIVE request would block every later query on the table). ``NOT VALID`` plus
``VALIDATE`` would not shorten the lock here: within one transaction the lock is held
until commit either way.

Revision ID: d1460fa1b2c3
Revises: b1460f8e9a0c
Create Date: 2026-11-23
"""

from collections.abc import Sequence
from typing import Union

from alembic import op


revision: str = "d1460fa1b2c3"
down_revision: Union[str, Sequence[str], None] = "b1460f8e9a0c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.drop_constraint("ck_pdf_document_files_status", "pdf_document_files", type_="check")
    op.create_check_constraint(
        "ck_pdf_document_files_status",
        "pdf_document_files",
        "status IN ('pending', 'ready', 'failed', 'removed')",
    )


def downgrade() -> None:
    raise RuntimeError(
        "The removed file status is forward-only; recover by restoring the pre-release backup"
    )
