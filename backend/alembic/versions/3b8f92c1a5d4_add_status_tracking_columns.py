"""add_status_tracking_columns

Revision ID: 3b8f92c1a5d4
Revises: 2f96f87d0456
Create Date: 2025-11-19 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3b8f92c1a5d4'
down_revision: Union[str, Sequence[str], None] = '2f96f87d0456'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add status tracking columns to pdf_documents table."""
    op.add_column('pdf_documents', sa.Column('status', sa.String(length=20), nullable=False, server_default='pending'))
    op.add_column('pdf_documents', sa.Column('error_message', sa.String(), nullable=True))
    op.add_column('pdf_documents', sa.Column('processing_started_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('pdf_documents', sa.Column('processing_completed_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    """Remove status tracking columns from pdf_documents table."""
    op.drop_column('pdf_documents', 'processing_completed_at')
    op.drop_column('pdf_documents', 'processing_started_at')
    op.drop_column('pdf_documents', 'error_message')
    op.drop_column('pdf_documents', 'status')
