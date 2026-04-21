"""Raise the pdf_documents file-size limit to 100 MB.

Revision ID: b9c0d1e2f3a4
Revises: y8z9a0b1c2d3
Create Date: 2026-04-21
"""

from alembic import op


MAX_PDF_FILE_SIZE_BYTES = 100 * 1024 * 1024
PREVIOUS_MAX_PDF_FILE_SIZE_BYTES = 50 * 1024 * 1024

# revision identifiers, used by Alembic.
revision = "b9c0d1e2f3a4"
down_revision = "y8z9a0b1c2d3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_pdf_documents_file_size", "pdf_documents", type_="check")
    op.create_check_constraint(
        "ck_pdf_documents_file_size",
        "pdf_documents",
        f"file_size > 0 AND file_size <= {MAX_PDF_FILE_SIZE_BYTES}",
    )


def downgrade() -> None:
    op.drop_constraint("ck_pdf_documents_file_size", "pdf_documents", type_="check")
    op.create_check_constraint(
        "ck_pdf_documents_file_size",
        "pdf_documents",
        f"file_size > 0 AND file_size <= {PREVIOUS_MAX_PDF_FILE_SIZE_BYTES}",
    )
