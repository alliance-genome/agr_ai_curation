"""Add custom_agents tables and execution-log support

Revision ID: r7s8t9u0v1w2
Revises: q1r2s3t4u5v6
Create Date: 2026-02-11

Adds:
- custom_agents
- custom_agent_versions

Also updates prompt_execution_log to support custom-agent prompt executions:
- prompt_template_id becomes nullable
- custom_agent_id UUID FK column is added
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


# revision identifiers, used by Alembic.
revision = "r7s8t9u0v1w2"
down_revision = "q1r2s3t4u5v6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---------------------------------------------------------------------
    # custom_agents
    # ---------------------------------------------------------------------
    op.create_table(
        "custom_agents",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("parent_agent_key", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("custom_prompt", sa.Text(), nullable=False),
        sa.Column("icon", sa.String(length=10), nullable=False),
        sa.Column("include_mod_rules", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("parent_prompt_hash", sa.String(length=64), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["users.user_id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_custom_agents_user_id", "custom_agents", ["user_id"], unique=False)
    op.create_index(
        "uq_custom_agents_active",
        "custom_agents",
        ["user_id", "name"],
        unique=True,
        postgresql_where=sa.text("is_active = true"),
    )

    # ---------------------------------------------------------------------
    # custom_agent_versions
    # ---------------------------------------------------------------------
    op.create_table(
        "custom_agent_versions",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("custom_agent_id", UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("custom_prompt", sa.Text(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["custom_agent_id"], ["custom_agents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_custom_agent_versions_custom_agent_id",
        "custom_agent_versions",
        ["custom_agent_id"],
        unique=False,
    )
    op.create_index(
        "uq_custom_agent_versions_version",
        "custom_agent_versions",
        ["custom_agent_id", "version"],
        unique=True,
    )

    # ---------------------------------------------------------------------
    # prompt_execution_log compatibility for custom agents
    # ---------------------------------------------------------------------
    op.alter_column("prompt_execution_log", "prompt_template_id", nullable=True)
    op.add_column(
        "prompt_execution_log",
        sa.Column("custom_agent_id", UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_prompt_execution_log_custom_agent_id_custom_agents",
        "prompt_execution_log",
        "custom_agents",
        ["custom_agent_id"],
        ["id"],
    )
    op.create_index(
        "idx_prompt_exec_custom_agent",
        "prompt_execution_log",
        ["custom_agent_id"],
        unique=False,
    )


def downgrade() -> None:
    # prompt_execution_log rollback
    op.drop_index("idx_prompt_exec_custom_agent", table_name="prompt_execution_log")
    op.drop_constraint(
        "fk_prompt_execution_log_custom_agent_id_custom_agents",
        "prompt_execution_log",
        type_="foreignkey",
    )
    op.drop_column("prompt_execution_log", "custom_agent_id")
    op.execute(sa.text("DELETE FROM prompt_execution_log WHERE prompt_template_id IS NULL"))
    op.alter_column("prompt_execution_log", "prompt_template_id", nullable=False)

    # custom_agent_versions rollback
    op.drop_index("uq_custom_agent_versions_version", table_name="custom_agent_versions")
    op.drop_index("ix_custom_agent_versions_custom_agent_id", table_name="custom_agent_versions")
    op.drop_table("custom_agent_versions")

    # custom_agents rollback
    op.drop_index("uq_custom_agents_active", table_name="custom_agents")
    op.drop_index("ix_custom_agents_user_id", table_name="custom_agents")
    op.drop_table("custom_agents")
