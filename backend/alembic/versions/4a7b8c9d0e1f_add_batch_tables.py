"""add batch processing tables

Revision ID: 4a7b8c9d0e1f
Revises: n8o9p0q1r2s3
Create Date: 2026-01-20 16:15:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, ENUM


# revision identifiers, used by Alembic.
revision = '4a7b8c9d0e1f'
down_revision = 'n8o9p0q1r2s3'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create enums using raw SQL with DO block to handle "already exists" gracefully
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE batchstatus AS ENUM ('pending', 'running', 'completed', 'cancelled');
        EXCEPTION
            WHEN duplicate_object THEN null;
        END $$;
    """)

    op.execute("""
        DO $$ BEGIN
            CREATE TYPE batchdocumentstatus AS ENUM ('pending', 'processing', 'completed', 'failed');
        EXCEPTION
            WHEN duplicate_object THEN null;
        END $$;
    """)

    # Create batches table - use ENUM with create_type=False since model import already created it
    batchstatus_enum = ENUM('pending', 'running', 'completed', 'cancelled', name='batchstatus', create_type=False)
    batchdocstatus_enum = ENUM('pending', 'processing', 'completed', 'failed', name='batchdocumentstatus', create_type=False)

    op.create_table(
        'batches',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('user_id', sa.Integer(), nullable=False, comment='Owner user ID - references users(user_id)'),
        sa.Column('flow_id', UUID(as_uuid=True), nullable=False, comment='Flow to execute - references curation_flows(id)'),
        sa.Column('status', batchstatus_enum, nullable=False, server_default='pending'),
        sa.Column('total_documents', sa.Integer(), nullable=False),
        sa.Column('completed_documents', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('failed_documents', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    )

    # Create indexes on batches
    op.create_index('idx_batches_user_id', 'batches', ['user_id'])
    op.create_index('idx_batches_status', 'batches', ['status'])

    # Create batch_documents table
    op.create_table(
        'batch_documents',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('batch_id', UUID(as_uuid=True), sa.ForeignKey('batches.id', ondelete='CASCADE'), nullable=False),
        sa.Column('document_id', UUID(as_uuid=True), nullable=False, comment='Reference to document in Weaviate PDFDocument collection'),
        sa.Column('position', sa.Integer(), nullable=False),
        sa.Column('status', batchdocstatus_enum, nullable=False, server_default='pending'),
        sa.Column('result_file_path', sa.String(500), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('processing_time_ms', sa.Integer(), nullable=True),
        sa.Column('processed_at', sa.DateTime(timezone=True), nullable=True),
    )

    # Create indexes on batch_documents
    op.create_index('idx_batch_documents_batch_id', 'batch_documents', ['batch_id'])
    op.create_index('uq_batch_document', 'batch_documents', ['batch_id', 'document_id'], unique=True)


def downgrade() -> None:
    # Drop batch_documents table and indexes
    op.drop_index('uq_batch_document', table_name='batch_documents')
    op.drop_index('idx_batch_documents_batch_id', table_name='batch_documents')
    op.drop_table('batch_documents')

    # Drop batches table and indexes
    op.drop_index('idx_batches_status', table_name='batches')
    op.drop_index('idx_batches_user_id', table_name='batches')
    op.drop_table('batches')

    # Drop enums
    sa.Enum(name='batchdocumentstatus').drop(op.get_bind(), checkfirst=True)
    sa.Enum(name='batchstatus').drop(op.get_bind(), checkfirst=True)
