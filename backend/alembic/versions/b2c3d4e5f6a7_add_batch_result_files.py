"""Add multi-file manifests to batch documents.

Revision ID: b2c3d4e5f6a7
Revises: a0b1c2d3e4f5
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "b2c3d4e5f6a7"
down_revision: str | Sequence[str] | None = "a0b1c2d3e4f5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "batch_documents",
        sa.Column("result_files", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "batch_documents",
        sa.Column("output_status", sa.String(length=20), nullable=True),
    )
    op.add_column(
        "batch_documents",
        sa.Column("output_branches", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.execute(
        """
        UPDATE batch_documents
        SET result_files = jsonb_build_array(
            jsonb_build_object('download_url', result_file_path)
        )
        WHERE result_file_path IS NOT NULL
          AND result_file_path <> ''
        """
    )
    op.execute(
        """
        UPDATE batch_documents
        SET output_status = 'complete'
        WHERE result_file_path IS NOT NULL
          AND result_file_path <> ''
        """
    )


def downgrade() -> None:
    op.drop_column("batch_documents", "output_branches")
    op.drop_column("batch_documents", "output_status")
    op.drop_column("batch_documents", "result_files")
