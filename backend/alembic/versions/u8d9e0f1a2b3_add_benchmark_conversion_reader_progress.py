"""Record what the PDF reader reports while a benchmark conversion extracts text.

While a running conversion is on ``extracting_text`` with PDF extraction, it keeps
the PDF reader's (PDFX's) own state: ``reader_detail`` is ``waking_reader`` (the
reader's GPU worker is starting from sleep, which takes minutes),
``waiting_for_reader`` (queued behind other work) or ``reading``, and
``reader_percent`` is PDFX's reported percent while reading, when it gives one.
Both are cleared when the stage moves on or the conversion finishes.

Revision ID: u8d9e0f1a2b3
Revises: t7c8d9e0f1a2
Create Date: 2026-10-03
"""

from collections.abc import Sequence

from alembic import op  # pyright: ignore[reportAttributeAccessIssue]
import sqlalchemy as sa


revision: str = "u8d9e0f1a2b3"
down_revision: str | Sequence[str] | None = "t7c8d9e0f1a2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "benchmark_document_conversions",
        sa.Column("reader_detail", sa.String(32), nullable=True),
    )
    op.add_column(
        "benchmark_document_conversions",
        sa.Column("reader_percent", sa.Integer(), nullable=True),
    )
    op.create_check_constraint(
        "ck_benchmark_document_conversions_reader",
        "benchmark_document_conversions",
        "(reader_detail IS NULL AND reader_percent IS NULL) OR "
        "(stage = 'extracting_text' "
        "AND reader_detail IN ('waking_reader', 'waiting_for_reader', 'reading') "
        "AND (reader_percent IS NULL OR (reader_detail = 'reading' "
        "AND reader_percent BETWEEN 0 AND 100)))",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_benchmark_document_conversions_reader",
        "benchmark_document_conversions",
        type_="check",
    )
    op.drop_column("benchmark_document_conversions", "reader_percent")
    op.drop_column("benchmark_document_conversions", "reader_detail")
