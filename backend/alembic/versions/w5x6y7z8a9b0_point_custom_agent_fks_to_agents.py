"""Point custom-agent foreign keys to unified agents table.

Revision ID: w5x6y7z8a9b0
Revises: v4w5x6y7z8a9
Create Date: 2026-02-22
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "w5x6y7z8a9b0"
down_revision = "v4w5x6y7z8a9"
branch_labels = None
depends_on = None


def _drop_constraint_if_exists(table_name: str, constraint_name: str) -> None:
    op.execute(
        sa.text(
            f"""
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1
                    FROM pg_constraint
                    WHERE conname = :constraint_name
                ) THEN
                    ALTER TABLE {table_name}
                    DROP CONSTRAINT {constraint_name};
                END IF;
            END $$;
            """
        ).bindparams(constraint_name=constraint_name)
    )


def upgrade() -> None:
    # custom_agent_versions.custom_agent_id -> agents.id
    _drop_constraint_if_exists(
        "custom_agent_versions",
        "custom_agent_versions_custom_agent_id_fkey",
    )
    _drop_constraint_if_exists(
        "custom_agent_versions",
        "fk_custom_agent_versions_custom_agent_id_custom_agents",
    )
    op.create_foreign_key(
        "fk_custom_agent_versions_custom_agent_id_agents",
        "custom_agent_versions",
        "agents",
        ["custom_agent_id"],
        ["id"],
        ondelete="CASCADE",
    )

    # prompt_execution_log.custom_agent_id -> agents.id
    _drop_constraint_if_exists(
        "prompt_execution_log",
        "fk_prompt_execution_log_custom_agent_id_custom_agents",
    )
    _drop_constraint_if_exists(
        "prompt_execution_log",
        "prompt_execution_log_custom_agent_id_fkey",
    )
    op.create_foreign_key(
        "fk_prompt_execution_log_custom_agent_id_agents",
        "prompt_execution_log",
        "agents",
        ["custom_agent_id"],
        ["id"],
    )


def downgrade() -> None:
    _drop_constraint_if_exists(
        "prompt_execution_log",
        "fk_prompt_execution_log_custom_agent_id_agents",
    )
    op.create_foreign_key(
        "fk_prompt_execution_log_custom_agent_id_custom_agents",
        "prompt_execution_log",
        "custom_agents",
        ["custom_agent_id"],
        ["id"],
    )

    _drop_constraint_if_exists(
        "custom_agent_versions",
        "fk_custom_agent_versions_custom_agent_id_agents",
    )
    op.create_foreign_key(
        "custom_agent_versions_custom_agent_id_fkey",
        "custom_agent_versions",
        "custom_agents",
        ["custom_agent_id"],
        ["id"],
        ondelete="CASCADE",
    )
