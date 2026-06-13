"""Add extraction result idempotency columns.

Revision ID: w8x9y0z1a2b3
Revises: v8w9x0y1z2a3
Create Date: 2026-06-13 14:45:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "w8x9y0z1a2b3"
down_revision: Union[str, Sequence[str], None] = "v8w9x0y1z2a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add duplicate-prevention metadata for inline extraction persistence."""

    op.add_column(
        "extraction_results",
        sa.Column("idempotency_key", sa.String(), nullable=True),
    )
    op.add_column(
        "extraction_results",
        sa.Column("payload_hash", sa.String(), nullable=True),
    )
    op.create_index(
        "uq_extraction_results_idempotency_key",
        "extraction_results",
        ["idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )


def downgrade() -> None:
    """Remove duplicate-prevention metadata."""

    op.drop_index("uq_extraction_results_idempotency_key", table_name="extraction_results")
    op.drop_column("extraction_results", "payload_hash")
    op.drop_column("extraction_results", "idempotency_key")
