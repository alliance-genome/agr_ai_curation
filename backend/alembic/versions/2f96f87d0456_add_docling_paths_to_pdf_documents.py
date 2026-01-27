"""add_docling_paths_to_pdf_documents

Revision ID: 2f96f87d0456
Revises: a7f8b9c0d1e2
Create Date: 2025-10-29 12:32:59.419968

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '2f96f87d0456'
down_revision: Union[str, Sequence[str], None] = 'a7f8b9c0d1e2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    """Add docling_json_path and processed_json_path columns to pdf_documents table."""
    op.add_column('pdf_documents', sa.Column('docling_json_path', sa.String(500), nullable=True))
    op.add_column('pdf_documents', sa.Column('processed_json_path', sa.String(500), nullable=True))

def downgrade() -> None:
    """Remove docling_json_path and processed_json_path columns from pdf_documents table."""
    op.drop_column('pdf_documents', 'processed_json_path')
    op.drop_column('pdf_documents', 'docling_json_path')
