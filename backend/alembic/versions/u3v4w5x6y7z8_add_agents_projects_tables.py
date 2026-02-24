"""Add first-class agents + project sharing tables.

Revision ID: u3v4w5x6y7z8
Revises: t2u3v4w5x6y7
Create Date: 2026-02-22
"""

import uuid

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


# revision identifiers, used by Alembic.
revision = "u3v4w5x6y7z8"
down_revision = "t2u3v4w5x6y7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---------------------------------------------------------------------
    # projects
    # ---------------------------------------------------------------------
    op.create_table(
        "projects",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_projects_is_active", "projects", ["is_active"], unique=False)

    # ---------------------------------------------------------------------
    # project_members
    # ---------------------------------------------------------------------
    op.create_table(
        "project_members",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("project_id", UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column(
            "role",
            sa.String(length=50),
            nullable=False,
            server_default="member",
        ),
        sa.Column(
            "joined_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "role IN ('admin', 'member')",
            name="ck_project_members_role",
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.user_id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id",
            "user_id",
            name="uq_project_members_project_user",
        ),
    )
    op.create_index(
        "ix_project_members_project_id",
        "project_members",
        ["project_id"],
        unique=False,
    )
    op.create_index(
        "ix_project_members_user_id",
        "project_members",
        ["user_id"],
        unique=False,
    )

    # ---------------------------------------------------------------------
    # agents
    # ---------------------------------------------------------------------
    op.create_table(
        "agents",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("agent_key", sa.String(length=100), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("instructions", sa.Text(), nullable=False),
        sa.Column("model_id", sa.String(length=100), nullable=False),
        sa.Column(
            "model_temperature",
            sa.Float(),
            nullable=False,
            server_default="0.1",
        ),
        sa.Column("model_reasoning", sa.String(length=20), nullable=True),
        sa.Column(
            "tool_ids",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("output_schema_key", sa.String(length=100), nullable=True),
        sa.Column(
            "group_rules_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("group_rules_component", sa.String(length=100), nullable=True),
        sa.Column(
            "mod_prompt_overrides",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "icon",
            sa.String(length=10),
            nullable=False,
            server_default="\U0001F916",
        ),
        sa.Column("category", sa.String(length=100), nullable=True),
        sa.Column(
            "visibility",
            sa.String(length=20),
            nullable=False,
            server_default="private",
        ),
        sa.Column("project_id", UUID(as_uuid=True), nullable=True),
        sa.Column("shared_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("template_source", sa.String(length=100), nullable=True),
        sa.Column(
            "supervisor_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("supervisor_description", sa.Text(), nullable=True),
        sa.Column(
            "supervisor_batchable",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("supervisor_batching_entity", sa.String(length=100), nullable=True),
        sa.Column(
            "show_in_palette",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
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
        sa.CheckConstraint(
            "visibility IN ('private', 'project', 'system')",
            name="ck_agents_visibility",
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.user_id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agents_agent_key", "agents", ["agent_key"], unique=True)
    op.create_index("ix_agents_user_id", "agents", ["user_id"], unique=False)
    op.create_index("ix_agents_project_id", "agents", ["project_id"], unique=False)
    op.create_index("ix_agents_visibility", "agents", ["visibility"], unique=False)
    op.create_index("ix_agents_is_active", "agents", ["is_active"], unique=False)

    # ---------------------------------------------------------------------
    # seed default project + memberships
    # ---------------------------------------------------------------------
    connection = op.get_bind()
    default_project_id = uuid.uuid4()

    connection.execute(
        sa.text(
            """
            INSERT INTO projects (id, name, description, is_active)
            VALUES (:id, :name, :description, true)
            """
        ),
        {
            "id": default_project_id,
            "name": "Alliance Curation",
            "description": "Default project for Agent Workshop collaboration.",
        },
    )

    connection.execute(
        sa.text(
            """
            INSERT INTO project_members (id, project_id, user_id, role)
            SELECT gen_random_uuid(), :project_id, u.user_id, 'member'
            FROM users u
            ON CONFLICT (project_id, user_id) DO NOTHING
            """
        ),
        {"project_id": default_project_id},
    )


def downgrade() -> None:
    op.drop_index("ix_agents_is_active", table_name="agents")
    op.drop_index("ix_agents_visibility", table_name="agents")
    op.drop_index("ix_agents_project_id", table_name="agents")
    op.drop_index("ix_agents_user_id", table_name="agents")
    op.drop_index("ix_agents_agent_key", table_name="agents")
    op.drop_table("agents")

    op.drop_index("ix_project_members_user_id", table_name="project_members")
    op.drop_index("ix_project_members_project_id", table_name="project_members")
    op.drop_table("project_members")

    op.drop_index("ix_projects_is_active", table_name="projects")
    op.drop_table("projects")
