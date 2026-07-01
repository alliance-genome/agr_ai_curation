"""Raise the pdf_documents file-size limit to 500 MB.

Revision ID: x0y1z2a3b4c5
Revises: w8x9y0z1a2b3
Create Date: 2026-07-01
"""

from typing import Sequence, Union

from alembic import op


MAX_PDF_FILE_SIZE_BYTES = 500 * 1024 * 1024
PREVIOUS_MAX_PDF_FILE_SIZE_BYTES = 150 * 1024 * 1024

revision: str = "x0y1z2a3b4c5"
down_revision: Union[str, Sequence[str], None] = "w8x9y0z1a2b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


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
