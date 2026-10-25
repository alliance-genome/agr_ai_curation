"""Distinguish incomplete review from completed and operationally failed batches.

Revision ID: b0c4d5e6f7a8
Revises: a9b3c4d5e6f7
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "b0c4d5e6f7a8"
down_revision = "a9b3c4d5e6f7"
branch_labels = None
depends_on = None


def upgrade():
    for value in ("review_pending", "review_exhausted", "interrupted"):
        op.execute("ALTER TYPE batchdocumentstatus ADD VALUE IF NOT EXISTS '" + value + "'")
    for name in ("review_pending_documents", "review_exhausted_documents", "interrupted_documents"):
        op.add_column("batches", sa.Column(name, sa.Integer, nullable=False, server_default="0"))
    op.add_column("batch_documents", sa.Column("review_details", JSONB(none_as_null=True)))


def downgrade():
    raise RuntimeError("Batch review outcomes are retained; use a forward recovery migration")
