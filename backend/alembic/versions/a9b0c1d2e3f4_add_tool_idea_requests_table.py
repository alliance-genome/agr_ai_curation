"""Add tool_idea_requests table for Agent Workshop ideation pipeline.

Revision ID: a9b0c1d2e3f4
Revises: z8a9b0c1d2e3
Create Date: 2026-02-23
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


# revision identifiers, used by Alembic.
revision = "a9b0c1d2e3f4"
down_revision = "z8a9b0c1d2e3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tool_idea_requests",
        sa.Column("id", UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("project_id", UUID(as_uuid=True), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("opus_conversation", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column(
            "status",
            sa.String(length=50),
            nullable=False,
            server_default="submitted",
        ),
        sa.Column("developer_notes", sa.Text(), nullable=True),
        sa.Column("resulting_tool_key", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.user_id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "status IN ('submitted', 'reviewed', 'in_progress', 'completed', 'declined')",
            name="ck_tool_idea_requests_status",
        ),
    )
    op.create_index("ix_tool_idea_requests_user_id", "tool_idea_requests", ["user_id"], unique=False)
    op.create_index("ix_tool_idea_requests_project_id", "tool_idea_requests", ["project_id"], unique=False)
    op.create_index("ix_tool_idea_requests_status", "tool_idea_requests", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_tool_idea_requests_status", table_name="tool_idea_requests")
    op.drop_index("ix_tool_idea_requests_project_id", table_name="tool_idea_requests")
    op.drop_index("ix_tool_idea_requests_user_id", table_name="tool_idea_requests")
    op.drop_table("tool_idea_requests")
