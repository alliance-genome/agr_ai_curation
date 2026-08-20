"""Use provider-neutral access group IDs for PDF document provenance.

Revision ID: 3c4d5e6f7a8b
Revises: 2b3c4d5e6f7a
Create Date: 2026-08-20
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op  # pyright: ignore[reportAttributeAccessIssue]


revision: str = "3c4d5e6f7a8b"
down_revision: str | Sequence[str] | None = "2b3c4d5e6f7a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Rename the column and convert the legacy object to a flat ID list."""

    op.alter_column(
        "pdf_documents",
        "source_access_mods",
        new_column_name="source_access_group_ids",
    )
    op.get_bind().execute(
        sa.text(
            """
            UPDATE pdf_documents
            SET source_access_group_ids = CASE
                WHEN jsonb_typeof(source_access_group_ids) = 'object'
                  AND jsonb_typeof(source_access_group_ids -> 'mods') = 'array'
                  AND NOT EXISTS (
                      SELECT 1
                      FROM jsonb_array_elements(
                          source_access_group_ids -> 'mods'
                      ) AS item
                      WHERE jsonb_typeof(item) <> 'string'
                         OR btrim(item #>> '{}') = ''
                  )
                THEN source_access_group_ids -> 'mods'
                ELSE NULL
            END
            WHERE source_access_group_ids IS NOT NULL
            """
        )
    )


def downgrade() -> None:
    """Restore the historical MOD-oriented object and column name."""

    op.get_bind().execute(
        sa.text(
            """
            UPDATE pdf_documents
            SET source_access_group_ids = CASE
                WHEN jsonb_typeof(source_access_group_ids) = 'array'
                THEN jsonb_build_object('mods', source_access_group_ids)
                ELSE NULL
            END
            WHERE source_access_group_ids IS NOT NULL
            """
        )
    )
    op.alter_column(
        "pdf_documents",
        "source_access_group_ids",
        new_column_name="source_access_mods",
    )
