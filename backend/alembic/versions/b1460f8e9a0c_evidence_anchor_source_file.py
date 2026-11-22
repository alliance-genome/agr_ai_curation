"""The file an evidence anchor quotes (KANBAN-1976 / ALL-1460, P8b).

Adds the nullable ``evidence_anchors.source_file_id``, a foreign key to the
paper's ``pdf_document_files`` row with ``ON DELETE RESTRICT``: a file that a
review session quotes cannot be deleted while the anchor exists. Deleting a
whole paper deletes its anchors first (``document_cleanup``), so it is not
blocked. Anchors saved before this release keep NULL, which means "saved before
files were recorded"; nothing existing is read or changed.

Revision ID: b1460f8e9a0c
Revises: a1460f6b2c3d
Create Date: 2026-11-22
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "b1460f8e9a0c"
down_revision: Union[str, Sequence[str], None] = "a1460f6b2c3d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "evidence_anchors",
        sa.Column("source_file_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_evidence_anchors_source_file_id",
        "evidence_anchors",
        "pdf_document_files",
        ["source_file_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_evidence_anchors_source_file_id",
        "evidence_anchors",
        ["source_file_id"],
    )


def downgrade() -> None:
    raise RuntimeError(
        "Evidence anchor files are forward-only; recover by restoring the pre-release backup"
    )
