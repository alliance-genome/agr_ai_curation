"""Add authenticated user chat route preferences.

Revision ID: d1e2f3a4b5c6
Revises: c925d1e2f3a4
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op  # pyright: ignore[reportAttributeAccessIssue]
from sqlalchemy.dialects.postgresql import UUID


revision: str = "d1e2f3a4b5c6"
down_revision: str | Sequence[str] | None = "c925d1e2f3a4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "chat_route_preferences",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("mode", sa.String(length=20), nullable=False),
        sa.Column("agent_id", UUID(as_uuid=True), nullable=True),
        sa.Column("flow_id", UUID(as_uuid=True), nullable=True),
        sa.Column("target_public_id", sa.String(length=255), nullable=True),
        sa.Column("target_display_name", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "(mode = 'automatic' AND agent_id IS NULL AND flow_id IS NULL "
            "AND target_public_id IS NULL AND target_display_name IS NULL) OR "
            "(mode = 'agent' AND agent_id IS NOT NULL AND flow_id IS NULL "
            "AND target_public_id IS NOT NULL AND target_display_name IS NOT NULL) OR "
            "(mode = 'flow' AND agent_id IS NULL AND flow_id IS NOT NULL "
            "AND target_public_id IS NOT NULL AND target_display_name IS NOT NULL)",
            name="ck_chat_route_preference_mode_target",
        ),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"]),
        sa.ForeignKeyConstraint(["flow_id"], ["curation_flows.id"]),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.user_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("user_id"),
    )


def downgrade() -> None:
    op.drop_table("chat_route_preferences")
