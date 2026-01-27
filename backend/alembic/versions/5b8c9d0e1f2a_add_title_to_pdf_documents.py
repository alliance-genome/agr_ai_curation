"""add title column to pdf_documents

Revision ID: 5b8c9d0e1f2a
Revises: 4a7b8c9d0e1f
Create Date: 2026-01-20 18:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '5b8c9d0e1f2a'
down_revision = '4a7b8c9d0e1f'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add title column to pdf_documents table
    # This allows users to set custom titles for batch processing clarity
    op.add_column(
        'pdf_documents',
        sa.Column('title', sa.String(255), nullable=True)
    )


def downgrade() -> None:
    op.drop_column('pdf_documents', 'title')
