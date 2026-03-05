"""Add durable PDF processing jobs table.

Revision ID: 1f2e3d4c5b6a
Revises: 9c4d6e8f1a2b
Create Date: 2026-03-03
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


# revision identifiers, used by Alembic.
revision: str = "1f2e3d4c5b6a"
down_revision: Union[str, Sequence[str], None] = "9c4d6e8f1a2b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create pdf_processing_jobs table and indexes."""
    op.create_table(
        "pdf_processing_jobs",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("current_stage", sa.String(length=64), nullable=True),
        sa.Column("progress_percentage", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("process_id", sa.String(length=255), nullable=True),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("metadata_json", JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'failed', 'cancel_requested', 'cancelled')",
            name="ck_pdf_processing_jobs_status",
        ),
        sa.CheckConstraint(
            "progress_percentage >= 0 AND progress_percentage <= 100",
            name="ck_pdf_processing_jobs_progress_percentage",
        ),
        sa.ForeignKeyConstraint(["document_id"], ["pdf_documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.user_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index("ix_pdf_processing_jobs_document_id", "pdf_processing_jobs", ["document_id"], unique=False)
    op.create_index("ix_pdf_processing_jobs_user_id", "pdf_processing_jobs", ["user_id"], unique=False)
    op.create_index("ix_pdf_processing_jobs_status", "pdf_processing_jobs", ["status"], unique=False)
    op.create_index(
        "ix_pdf_processing_jobs_user_id_created_at",
        "pdf_processing_jobs",
        ["user_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    """Drop pdf_processing_jobs table and indexes."""
    op.drop_index("ix_pdf_processing_jobs_user_id_created_at", table_name="pdf_processing_jobs")
    op.drop_index("ix_pdf_processing_jobs_status", table_name="pdf_processing_jobs")
    op.drop_index("ix_pdf_processing_jobs_user_id", table_name="pdf_processing_jobs")
    op.drop_index("ix_pdf_processing_jobs_document_id", table_name="pdf_processing_jobs")
    op.drop_table("pdf_processing_jobs")
