"""Add package group-tool policy and durable batch group snapshots.

Revision ID: c925d1e2f3a4
Revises: b824c1d2e3f4
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB


revision: str = "c925d1e2f3a4"
down_revision: str | Sequence[str] | None = "b824c1d2e3f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agents",
        sa.Column(
            "group_tool_policy",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "batches",
        sa.Column(
            "active_group_ids",
            JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
            comment=(
                "Authenticated internal-group snapshot captured when the batch was created"
            ),
        ),
    )


def downgrade() -> None:
    op.drop_column("batches", "active_group_ids")
    op.drop_column("agents", "group_tool_policy")
