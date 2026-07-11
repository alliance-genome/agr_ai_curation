"""Keep only the positive PDF page-count integrity invariant.

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-07-11
"""

from alembic import op


revision = "b2c3d4e5f6a7"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_pdf_documents_page_count", "pdf_documents", type_="check")
    op.create_check_constraint(
        "ck_pdf_documents_page_count",
        "pdf_documents",
        "page_count > 0",
    )


def downgrade() -> None:
    op.drop_constraint("ck_pdf_documents_page_count", "pdf_documents", type_="check")
    op.create_check_constraint(
        "ck_pdf_documents_page_count",
        "pdf_documents",
        "page_count > 0 AND page_count <= 50",
    )
