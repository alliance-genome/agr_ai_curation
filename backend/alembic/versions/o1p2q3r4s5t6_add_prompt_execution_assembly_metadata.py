"""Add prompt execution assembly metadata.

Revision ID: o1p2q3r4s5t6
Revises: n1o2p3q4r5s6
Create Date: 2026-05-18
"""

from collections.abc import Sequence
from typing import Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "o1p2q3r4s5t6"
down_revision: Union[str, Sequence[str], None] = "n1o2p3q4r5s6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "prompt_execution_log",
        sa.Column("effective_prompt_hash", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "prompt_execution_log",
        sa.Column("layer_manifest", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("prompt_execution_log", "layer_manifest")
    op.drop_column("prompt_execution_log", "effective_prompt_hash")
