"""cleanup_ai_curation_pdf_schema

Drop tables that don't belong in ai_curation_pdf database.
This migration removes ontology tables and feedback_reports which belong in ai_curation instead.

Revision ID: 148ad0f8d61e
Revises: f8e9a2b3c4d5
Create Date: 2025-10-22 19:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '148ad0f8d61e'
down_revision: Union[str, None] = 'f8e9a2b3c4d5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Drop tables that don't belong in ai_curation_pdf database."""
    # Drop ontology-related tables (they belong in ai_curation)
    op.execute('DROP TABLE IF EXISTS term_relationships CASCADE')
    op.execute('DROP TABLE IF EXISTS term_synonyms CASCADE')
    op.execute('DROP TABLE IF EXISTS term_metadata CASCADE')
    op.execute('DROP TABLE IF EXISTS ontology_terms CASCADE')
    op.execute('DROP TABLE IF EXISTS ontologies CASCADE')

    # Drop feedback_reports (now stored in ai_curation)
    op.execute('DROP TABLE IF EXISTS feedback_reports CASCADE')


def downgrade() -> None:
    """Recreate tables if needed (not recommended - data would be lost)."""
    # Note: This downgrade would recreate empty tables without data.
    # In practice, downgrade should not be used as it would cause data loss.
    pass
