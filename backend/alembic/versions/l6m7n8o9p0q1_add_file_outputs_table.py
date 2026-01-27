"""Add file_outputs table.

Revision ID: l6m7n8o9p0q1
Revises: k5l6m7n8o9p0
Create Date: 2025-01-07

Feature: 008-file-output-downloads
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "l6m7n8o9p0q1"
down_revision = "k5l6m7n8o9p0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create file_outputs table for tracking generated files."""
    op.create_table(
        "file_outputs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.func.gen_random_uuid(),
            nullable=False,
        ),
        sa.Column("filename", sa.String(512), nullable=False),
        sa.Column("file_path", sa.Text(), nullable=False, unique=True),
        sa.Column("file_type", sa.String(20), nullable=False),
        sa.Column("file_size", sa.BigInteger(), nullable=False),
        sa.Column("file_hash", sa.String(64), nullable=True),
        sa.Column("curator_id", sa.String(255), nullable=False),
        sa.Column("session_id", sa.String(255), nullable=False),
        sa.Column("trace_id", sa.String(32), nullable=False),
        sa.Column("agent_name", sa.String(255), nullable=True),
        sa.Column("generation_model", sa.String(255), nullable=True),
        sa.Column("file_metadata", postgresql.JSONB(), nullable=True),
        sa.Column("download_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_download_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # Add check constraints
    op.create_check_constraint(
        "ck_file_outputs_file_type",
        "file_outputs",
        "file_type IN ('csv', 'tsv', 'json')",
    )
    op.create_check_constraint(
        "ck_file_outputs_file_size", "file_outputs", "file_size > 0"
    )
    op.create_check_constraint(
        "ck_file_outputs_trace_id", "file_outputs", "length(trace_id) = 32"
    )
    op.create_check_constraint(
        "ck_file_outputs_curator_id", "file_outputs", "curator_id <> ''"
    )
    op.create_check_constraint(
        "ck_file_outputs_session_id", "file_outputs", "session_id <> ''"
    )

    # Create indexes for common query patterns
    op.create_index(
        "ix_file_outputs_session_curator",
        "file_outputs",
        ["session_id", "curator_id"],
    )
    op.create_index("ix_file_outputs_trace_id", "file_outputs", ["trace_id"])
    op.create_index(
        "ix_file_outputs_created_at", "file_outputs", [sa.text("created_at DESC")]
    )
    op.create_index(
        "ix_file_outputs_curator_created",
        "file_outputs",
        ["curator_id", sa.text("created_at DESC")],
    )


def downgrade() -> None:
    """Drop file_outputs table and related objects."""
    # Drop indexes first (explicit for consistency with other migrations)
    op.drop_index("ix_file_outputs_curator_created", table_name="file_outputs")
    op.drop_index("ix_file_outputs_created_at", table_name="file_outputs")
    op.drop_index("ix_file_outputs_trace_id", table_name="file_outputs")
    op.drop_index("ix_file_outputs_session_curator", table_name="file_outputs")

    # Drop check constraints (explicit for consistency)
    op.drop_constraint("ck_file_outputs_session_id", "file_outputs", type_="check")
    op.drop_constraint("ck_file_outputs_curator_id", "file_outputs", type_="check")
    op.drop_constraint("ck_file_outputs_trace_id", "file_outputs", type_="check")
    op.drop_constraint("ck_file_outputs_file_size", "file_outputs", type_="check")
    op.drop_constraint("ck_file_outputs_file_type", "file_outputs", type_="check")

    # Drop the table
    op.drop_table("file_outputs")
