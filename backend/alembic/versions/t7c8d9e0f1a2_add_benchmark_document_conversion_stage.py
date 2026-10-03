"""Record which coarse stage a running benchmark document conversion is in.

A running conversion moves through ``fetching_source`` (reading the uploaded
PDF, or getting the paper from the configured document source),
``extracting_text`` (PDF extraction, or converting the source's main text) and
``saving`` (freezing the converted document as a benchmark input). Only a
running conversion has a stage. Conversions already running when this upgrade
applies keep no stage, so their status reports no progress until they finish.

Revision ID: t7c8d9e0f1a2
Revises: s6b7c8d9e0f1
Create Date: 2026-10-02
"""

from collections.abc import Sequence

from alembic import op  # pyright: ignore[reportAttributeAccessIssue]
import sqlalchemy as sa


revision: str = "t7c8d9e0f1a2"
down_revision: str | Sequence[str] | None = "s6b7c8d9e0f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "benchmark_document_conversions",
        sa.Column("stage", sa.String(32), nullable=True),
    )
    op.create_check_constraint(
        "ck_benchmark_document_conversions_stage",
        "benchmark_document_conversions",
        "stage IS NULL OR (status = 'running' "
        "AND stage IN ('fetching_source', 'extracting_text', 'saving'))",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_benchmark_document_conversions_stage",
        "benchmark_document_conversions",
        type_="check",
    )
    op.drop_column("benchmark_document_conversions", "stage")
