"""Raise the pdf_documents file-size limit to 150 MB.

Revision ID: c0d1e2f3a4b5
Revises: h7i8j9k0l1m2
Create Date: 2026-06-12
"""

from alembic import op


MAX_PDF_FILE_SIZE_BYTES = 150 * 1024 * 1024
PREVIOUS_MAX_PDF_FILE_SIZE_BYTES = 100 * 1024 * 1024

# revision identifiers, used by Alembic.
revision = "c0d1e2f3a4b5"
down_revision = "h7i8j9k0l1m2"
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
