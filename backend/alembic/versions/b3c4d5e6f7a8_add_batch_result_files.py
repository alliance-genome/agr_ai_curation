"""Add multi-file manifests and reconcile the v0.8.11 hotfix lineage.

Revision ID: b3c4d5e6f7a8
Revises: a0b1c2d3e4f5

The production v0.8.11 hotfix used revision ``b2c3d4e5f6a7`` for these
columns before current main was forward-ported. Main had independently used
that revision ID for the PDF page-count relaxation. This post-main-head
migration is intentionally idempotent for the batch columns and reasserts the
relaxed page-count constraint so either lineage converges safely.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "b3c4d5e6f7a8"
down_revision: str | Sequence[str] | None = "a0b1c2d3e4f5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    batch_columns = {
        column["name"] for column in inspector.get_columns("batch_documents")
    }
    if "result_files" not in batch_columns:
        op.add_column(
            "batch_documents",
            sa.Column(
                "result_files",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=True,
            ),
        )
    if "output_status" not in batch_columns:
        op.add_column(
            "batch_documents",
            sa.Column("output_status", sa.String(length=20), nullable=True),
        )
    if "output_branches" not in batch_columns:
        op.add_column(
            "batch_documents",
            sa.Column(
                "output_branches",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=True,
            ),
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

    # Production v0.8.11 records the colliding revision as applied even though
    # it did not run main's page-count migration. Reassert main's intended
    # invariant after both lineages have reached this revision.
    check_constraints = {
        constraint["name"]
        for constraint in inspector.get_check_constraints("pdf_documents")
    }
    if "ck_pdf_documents_page_count" in check_constraints:
        op.drop_constraint(
            "ck_pdf_documents_page_count",
            "pdf_documents",
            type_="check",
        )
    op.create_check_constraint(
        "ck_pdf_documents_page_count",
        "pdf_documents",
        "page_count > 0",
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
