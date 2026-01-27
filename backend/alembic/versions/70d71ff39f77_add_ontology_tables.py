"""add_ontology_tables

Revision ID: 70d71ff39f77
Revises: dd4636519fc0
Create Date: 2025-09-29 17:58:39.909864

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '70d71ff39f77'
down_revision: Union[str, Sequence[str], None] = 'dd4636519fc0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Create ontologies table
    op.create_table(
        'ontologies',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('version', sa.String(length=50), nullable=True),
        sa.Column('source_url', sa.String(length=500), nullable=True),
        sa.Column('date_loaded', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('term_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('namespace', sa.String(length=255), nullable=True),
        sa.Column('format_version', sa.String(length=50), nullable=True),
        sa.Column('last_refreshed', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id', name='pk_ontologies'),
        sa.UniqueConstraint('name', name='uq_ontologies_name'),
        sa.CheckConstraint('term_count >= 0', name='ck_ontologies_term_count'),
    )
    op.create_index(op.f('ix_ontologies_name'), 'ontologies', ['name'], unique=False)
    op.create_index(op.f('ix_ontologies_date_loaded'), 'ontologies', ['date_loaded'], unique=False)

    # Create ontology_terms table
    op.create_table(
        'ontology_terms',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('ontology_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('term_id', sa.String(length=50), nullable=False),
        sa.Column('name', sa.String(length=500), nullable=False),
        sa.Column('definition', sa.Text(), nullable=True),
        sa.Column('namespace', sa.String(length=255), nullable=True),
        sa.Column('is_obsolete', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('replaced_by', sa.String(length=50), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id', name='pk_ontology_terms'),
        sa.ForeignKeyConstraint(['ontology_id'], ['ontologies.id'], name='fk_ontology_terms_ontology_id', ondelete='CASCADE'),
        sa.UniqueConstraint('ontology_id', 'term_id', name='uq_ontology_terms_ontology_term'),
    )
    op.create_index(op.f('ix_ontology_terms_term_id'), 'ontology_terms', ['term_id'], unique=False)
    op.create_index(op.f('ix_ontology_terms_name'), 'ontology_terms', ['name'], unique=False)
    op.create_index(op.f('ix_ontology_terms_is_obsolete'), 'ontology_terms', ['is_obsolete'], unique=False)
    op.create_index(op.f('ix_ontology_terms_ontology_id'), 'ontology_terms', ['ontology_id'], unique=False)

    # Create term_synonyms table
    op.create_table(
        'term_synonyms',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('term_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('synonym', sa.String(length=500), nullable=False),
        sa.Column('scope', sa.String(length=20), nullable=False),
        sa.Column('synonym_type', sa.String(length=100), nullable=True),
        sa.PrimaryKeyConstraint('id', name='pk_term_synonyms'),
        sa.ForeignKeyConstraint(['term_id'], ['ontology_terms.id'], name='fk_term_synonyms_term_id', ondelete='CASCADE'),
    )
    op.create_index(op.f('ix_term_synonyms_synonym'), 'term_synonyms', ['synonym'], unique=False)
    op.create_index(op.f('ix_term_synonyms_term_id'), 'term_synonyms', ['term_id'], unique=False)
    op.create_index(op.f('ix_term_synonyms_scope'), 'term_synonyms', ['term_id', 'scope'], unique=False)

    # Create term_relationships table
    op.create_table(
        'term_relationships',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('subject_term_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('predicate', sa.String(length=100), nullable=False),
        sa.Column('object_term_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.PrimaryKeyConstraint('id', name='pk_term_relationships'),
        sa.ForeignKeyConstraint(['subject_term_id'], ['ontology_terms.id'], name='fk_term_relationships_subject', ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['object_term_id'], ['ontology_terms.id'], name='fk_term_relationships_object', ondelete='CASCADE'),
        sa.CheckConstraint('subject_term_id != object_term_id', name='ck_term_relationships_no_self_reference'),
    )
    op.create_index(op.f('ix_term_relationships_subject_term_id'), 'term_relationships', ['subject_term_id'], unique=False)
    op.create_index(op.f('ix_term_relationships_object_term_id'), 'term_relationships', ['object_term_id'], unique=False)
    op.create_index(op.f('ix_term_relationships_predicate'), 'term_relationships', ['predicate'], unique=False)

    # Create term_metadata table
    op.create_table(
        'term_metadata',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('term_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('metadata', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('property_name', sa.String(length=255), nullable=True),
        sa.Column('property_value', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('id', name='pk_term_metadata'),
        sa.ForeignKeyConstraint(['term_id'], ['ontology_terms.id'], name='fk_term_metadata_term_id', ondelete='CASCADE'),
    )
    op.create_index(op.f('ix_term_metadata_term_id'), 'term_metadata', ['term_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    # Drop tables in reverse order (respecting foreign key dependencies)
    op.drop_index(op.f('ix_term_metadata_term_id'), table_name='term_metadata')
    op.drop_table('term_metadata')

    op.drop_index(op.f('ix_term_relationships_predicate'), table_name='term_relationships')
    op.drop_index(op.f('ix_term_relationships_object_term_id'), table_name='term_relationships')
    op.drop_index(op.f('ix_term_relationships_subject_term_id'), table_name='term_relationships')
    op.drop_table('term_relationships')

    op.drop_index(op.f('ix_term_synonyms_scope'), table_name='term_synonyms')
    op.drop_index(op.f('ix_term_synonyms_term_id'), table_name='term_synonyms')
    op.drop_index(op.f('ix_term_synonyms_synonym'), table_name='term_synonyms')
    op.drop_table('term_synonyms')

    op.drop_index(op.f('ix_ontology_terms_ontology_id'), table_name='ontology_terms')
    op.drop_index(op.f('ix_ontology_terms_is_obsolete'), table_name='ontology_terms')
    op.drop_index(op.f('ix_ontology_terms_name'), table_name='ontology_terms')
    op.drop_index(op.f('ix_ontology_terms_term_id'), table_name='ontology_terms')
    op.drop_table('ontology_terms')

    op.drop_index(op.f('ix_ontologies_date_loaded'), table_name='ontologies')
    op.drop_index(op.f('ix_ontologies_name'), table_name='ontologies')
    op.drop_table('ontologies')
