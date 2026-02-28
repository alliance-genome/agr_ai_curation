"""rename_docling_json_column_to_pdfx_json

Revision ID: 9c4d6e8f1a2b
Revises: 08b9c0d1e2f3
Create Date: 2026-02-28
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "9c4d6e8f1a2b"
down_revision: Union[str, Sequence[str], None] = "08b9c0d1e2f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_names(connection: sa.Connection) -> set[str]:
    inspector = sa.inspect(connection)
    return {col["name"] for col in inspector.get_columns("pdf_documents")}


def upgrade() -> None:
    """Rename legacy docling_json_path column to pdfx_json_path.

    Production databases may already be beyond revision 2f96... and still have
    the old column name. This migration performs an in-place rename when needed.
    """
    connection = op.get_bind()
    columns = _column_names(connection)

    if "docling_json_path" in columns and "pdfx_json_path" not in columns:
        op.alter_column(
            "pdf_documents",
            "docling_json_path",
            new_column_name="pdfx_json_path",
            existing_type=sa.String(length=500),
            existing_nullable=True,
        )
    elif "pdfx_json_path" not in columns:
        op.add_column("pdf_documents", sa.Column("pdfx_json_path", sa.String(length=500), nullable=True))

    op.execute("DROP INDEX IF EXISTS idx_pdf_documents_docling_json_path")
    op.execute("CREATE INDEX IF NOT EXISTS idx_pdf_documents_pdfx_json_path ON pdf_documents(pdfx_json_path)")


def downgrade() -> None:
    """Revert pdfx_json_path column name back to docling_json_path."""
    connection = op.get_bind()
    columns = _column_names(connection)

    if "pdfx_json_path" in columns and "docling_json_path" not in columns:
        op.alter_column(
            "pdf_documents",
            "pdfx_json_path",
            new_column_name="docling_json_path",
            existing_type=sa.String(length=500),
            existing_nullable=True,
        )
    elif "docling_json_path" not in columns:
        op.add_column("pdf_documents", sa.Column("docling_json_path", sa.String(length=500), nullable=True))

    op.execute("DROP INDEX IF EXISTS idx_pdf_documents_pdfx_json_path")
    op.execute("CREATE INDEX IF NOT EXISTS idx_pdf_documents_docling_json_path ON pdf_documents(docling_json_path)")
