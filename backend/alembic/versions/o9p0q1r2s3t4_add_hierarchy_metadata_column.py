"""Add hierarchy_metadata column to pdf_documents

Revision ID: o9p0q1r2s3t4
Revises: n8o9p0q1r2s3
Create Date: 2026-01-21

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision = 'o9p0q1r2s3t4'
down_revision = '5b8c9d0e1f2a'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('pdf_documents', sa.Column('hierarchy_metadata', JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column('pdf_documents', 'hierarchy_metadata')
