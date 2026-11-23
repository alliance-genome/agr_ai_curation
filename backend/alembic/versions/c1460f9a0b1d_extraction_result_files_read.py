"""The paper files each extraction result read (KANBAN-1976 / ALL-1460, P8b2).

Adds the nullable JSONB ``extraction_results.files_read``: the files whose text
the model was shown while producing the result, each with its id, name, role,
converted artifact id and SHA-256. NULL means "not recorded" (every result saved
before this release); an empty list means "recorded, no file read". Nothing
existing is read or changed.

Revision ID: c1460f9a0b1d
Revises: d1460fa1b2c3
Create Date: 2026-11-23
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "c1460f9a0b1d"
down_revision: Union[str, Sequence[str], None] = "d1460fa1b2c3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "extraction_results",
        sa.Column("files_read", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    raise RuntimeError(
        "Extraction result files read are forward-only; recover by restoring the pre-release backup"
    )
