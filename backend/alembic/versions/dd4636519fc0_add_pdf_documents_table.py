"""Add pdf_documents table

Revision ID: dd4636519fc0
Revises: 
Create Date: 2025-09-26 23:12:17.499955

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'dd4636519fc0'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'pdf_documents',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('filename', sa.String(length=255), nullable=False),
        sa.Column('file_path', sa.String(length=500), nullable=False),
        sa.Column('file_hash', sa.String(length=32), nullable=False),
        sa.Column('file_size', sa.Integer(), nullable=False),
        sa.Column('page_count', sa.Integer(), nullable=False),
        sa.Column('upload_timestamp', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('last_accessed', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id', name='pk_pdf_documents'),
        sa.UniqueConstraint('file_path', name='uq_pdf_documents_file_path'),
        sa.UniqueConstraint('file_hash', name='uq_pdf_documents_file_hash'),
        sa.CheckConstraint('file_size > 0 AND file_size <= 52428800', name='ck_pdf_documents_file_size'),
        sa.CheckConstraint('page_count > 0 AND page_count <= 50', name='ck_pdf_documents_page_count'),
    )
    op.create_index(op.f('ix_pdf_documents_upload_timestamp'), 'pdf_documents', ['upload_timestamp'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_pdf_documents_upload_timestamp'), table_name='pdf_documents')
    op.drop_table('pdf_documents')
