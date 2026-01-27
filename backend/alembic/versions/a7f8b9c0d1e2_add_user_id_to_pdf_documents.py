"""Add user_id to pdf_documents table

Revision ID: a7f8b9c0d1e2
Revises: 6e5d344c93d0
Create Date: 2025-01-25 12:05:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a7f8b9c0d1e2'
down_revision: Union[str, Sequence[str], None] = '6e5d344c93d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema - add user_id column to pdf_documents table."""
    # Add user_id column (nullable initially for existing rows)
    op.add_column('pdf_documents',
        sa.Column('user_id', sa.Integer(), nullable=True)
    )

    # Add foreign key constraint to users table
    op.create_foreign_key(
        'fk_pdf_documents_user_id',
        'pdf_documents', 'users',
        ['user_id'], ['user_id'],
        ondelete='CASCADE'
    )

    # Create index for efficient user-specific queries
    op.create_index(
        'idx_pdf_documents_user_id',
        'pdf_documents',
        ['user_id'],
        unique=False
    )


def downgrade() -> None:
    """Downgrade schema - remove user_id column from pdf_documents table."""
    op.drop_index('idx_pdf_documents_user_id', table_name='pdf_documents')
    op.drop_constraint('fk_pdf_documents_user_id', 'pdf_documents', type_='foreignkey')
    op.drop_column('pdf_documents', 'user_id')
