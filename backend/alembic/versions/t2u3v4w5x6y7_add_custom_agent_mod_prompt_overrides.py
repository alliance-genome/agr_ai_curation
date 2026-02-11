"""Add per-MOD prompt override storage for custom agents.

Revision ID: t2u3v4w5x6y7
Revises: r7s8t9u0v1w2
Create Date: 2026-02-11
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


# revision identifiers, used by Alembic.
revision = "t2u3v4w5x6y7"
down_revision = "r7s8t9u0v1w2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "custom_agents",
        sa.Column(
            "mod_prompt_overrides",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "custom_agent_versions",
        sa.Column(
            "mod_prompt_overrides",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("custom_agent_versions", "mod_prompt_overrides")
    op.drop_column("custom_agents", "mod_prompt_overrides")
