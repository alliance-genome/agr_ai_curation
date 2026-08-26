"""Persist canonical group-scoped agent availability.

Revision ID: b824c1d2e3f4
Revises: a823b1c2d3e4
Create Date: 2026-08-26
"""

from collections.abc import Sequence

from alembic import op  # pyright: ignore[reportAttributeAccessIssue]
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision: str = "b824c1d2e3f4"
down_revision: str | Sequence[str] | None = "a823b1c2d3e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _add_allowed_group_ids(table_name: str) -> None:
    op.add_column(
        table_name,
        sa.Column("allowed_group_ids", JSONB(), nullable=True),
    )
    op.execute(
        sa.text(
            f"UPDATE {table_name} SET allowed_group_ids = '[]'::jsonb "
            "WHERE allowed_group_ids IS NULL"
        )
    )
    op.alter_column(
        table_name,
        "allowed_group_ids",
        existing_type=JSONB(),
        nullable=False,
        server_default=sa.text("'[]'::jsonb"),
    )


def upgrade() -> None:
    _add_allowed_group_ids("agents")
    op.add_column(
        "agents",
        sa.Column("inherited_allowed_group_ids", JSONB(), nullable=True),
    )
    op.execute(
        sa.text(
            "UPDATE agents SET inherited_allowed_group_ids = '[]'::jsonb "
            "WHERE inherited_allowed_group_ids IS NULL"
        )
    )
    op.alter_column(
        "agents",
        "inherited_allowed_group_ids",
        existing_type=JSONB(),
        nullable=False,
        server_default=sa.text("'[]'::jsonb"),
    )
    _add_allowed_group_ids("custom_agent_versions")


def downgrade() -> None:
    op.drop_column("custom_agent_versions", "allowed_group_ids")
    op.drop_column("agents", "inherited_allowed_group_ids")
    op.drop_column("agents", "allowed_group_ids")
