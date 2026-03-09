"""Rename mod_prompt_overrides columns to group_prompt_overrides.

Revision ID: c2d3e4f5a6b7
Revises: a9b0c1d2e3f4
Create Date: 2026-03-09
"""

from alembic import op


# revision identifiers, used by Alembic.
revision = "c2d3e4f5a6b7"
down_revision = "a9b0c1d2e3f4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "agents",
        "mod_prompt_overrides",
        new_column_name="group_prompt_overrides",
    )
    op.alter_column(
        "custom_agent_versions",
        "mod_prompt_overrides",
        new_column_name="group_prompt_overrides",
    )


def downgrade() -> None:
    op.alter_column(
        "custom_agent_versions",
        "group_prompt_overrides",
        new_column_name="mod_prompt_overrides",
    )
    op.alter_column(
        "agents",
        "group_prompt_overrides",
        new_column_name="mod_prompt_overrides",
    )
