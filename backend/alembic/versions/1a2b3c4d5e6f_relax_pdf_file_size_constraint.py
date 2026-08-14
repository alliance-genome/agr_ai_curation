"""Keep only the positive PDF file-size integrity invariant.

Revision ID: 1a2b3c4d5e6f
Revises: 0f1e2d3c4b5a
Create Date: 2026-08-14
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op  # pyright: ignore[reportAttributeAccessIssue]


revision: str = "1a2b3c4d5e6f"
down_revision: str | Sequence[str] | None = "0f1e2d3c4b5a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Let application admission own the configurable upper limit."""
    op.drop_constraint("ck_pdf_documents_file_size", "pdf_documents", type_="check")
    op.create_check_constraint(
        "ck_pdf_documents_file_size",
        "pdf_documents",
        "file_size > 0",
    )


def downgrade() -> None:
    """Restore the historical 500 MiB database ceiling."""
    op.drop_constraint("ck_pdf_documents_file_size", "pdf_documents", type_="check")
    op.create_check_constraint(
        "ck_pdf_documents_file_size",
        "pdf_documents",
        "file_size > 0 AND file_size <= 524288000",
    )
