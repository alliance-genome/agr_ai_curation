"""Drop legacy custom_agents table after unified migration.

Revision ID: x6y7z8a9b0c1
Revises: w5x6y7z8a9b0
Create Date: 2026-02-22
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


# revision identifiers, used by Alembic.
revision = "x6y7z8a9b0c1"
down_revision = "w5x6y7z8a9b0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("uq_custom_agents_active", table_name="custom_agents")
    op.drop_index("ix_custom_agents_user_id", table_name="custom_agents")
    op.drop_table("custom_agents")


def downgrade() -> None:
    op.create_table(
        "custom_agents",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("parent_agent_key", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("custom_prompt", sa.Text(), nullable=False),
        sa.Column(
            "mod_prompt_overrides",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("icon", sa.String(length=10), nullable=False),
        sa.Column(
            "include_mod_rules",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column("parent_prompt_hash", sa.String(length=64), nullable=True),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
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
        sa.ForeignKeyConstraint(["user_id"], ["users.user_id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_custom_agents_user_id",
        "custom_agents",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "uq_custom_agents_active",
        "custom_agents",
        ["user_id", "name"],
        unique=True,
        postgresql_where=sa.text("is_active = true"),
    )

    # Rehydrate from unified agents rows (best-effort, no fallback metadata).
    op.execute(
        sa.text(
            """
            INSERT INTO custom_agents (
                id,
                user_id,
                parent_agent_key,
                name,
                description,
                custom_prompt,
                mod_prompt_overrides,
                icon,
                include_mod_rules,
                parent_prompt_hash,
                is_active,
                created_at,
                updated_at
            )
            SELECT
                a.id,
                a.user_id,
                COALESCE(
                    a.template_source,
                    a.group_rules_component,
                    ''
                ) AS parent_agent_key,
                LEFT(a.name, 100),
                a.description,
                a.instructions,
                COALESCE(a.mod_prompt_overrides, '{}'::jsonb),
                COALESCE(a.icon, 'tool'),
                COALESCE(a.group_rules_enabled, false),
                NULL AS parent_prompt_hash,
                a.is_active,
                a.created_at,
                a.updated_at
            FROM agents a
            WHERE a.agent_key LIKE 'ca_%'
              AND a.visibility IN ('private', 'project')
            """
        )
    )
