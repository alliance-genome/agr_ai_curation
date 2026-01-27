"""Add prompt_templates and prompt_execution_log tables

Revision ID: i3j4k5l6m7n8
Revises: h2i3j4k5l6m7
Create Date: 2025-12-29

This migration adds tables for versioned prompt management:
- prompt_templates: Stores versioned prompts for agents with support for MOD-specific rules
- prompt_execution_log: Audit trail of which prompts were used in each execution
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


# revision identifiers, used by Alembic.
revision = 'i3j4k5l6m7n8'
down_revision = 'h2i3j4k5l6m7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create prompt_templates table
    op.create_table(
        'prompt_templates',
        sa.Column('id', UUID(as_uuid=True), nullable=False),
        sa.Column('agent_name', sa.String(length=100), nullable=False),
        sa.Column('prompt_type', sa.String(length=50), nullable=False),
        sa.Column('mod_id', sa.String(length=20), nullable=True),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False, default=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('created_by', sa.String(length=255), nullable=True),
        sa.Column('change_notes', sa.Text(), nullable=True),
        sa.Column('source_file', sa.Text(), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )

    # Index for fast lookups of active prompts
    op.create_index(
        'idx_prompt_templates_active',
        'prompt_templates',
        ['agent_name', 'prompt_type', 'mod_id'],
        postgresql_where=sa.text('is_active = true')
    )

    # Partial unique index for base prompts (mod_id IS NULL)
    op.create_index(
        'idx_prompt_templates_base_unique',
        'prompt_templates',
        ['agent_name', 'prompt_type', 'version'],
        unique=True,
        postgresql_where=sa.text('mod_id IS NULL')
    )

    # Index for version lookups
    op.create_index(
        'idx_prompt_templates_version',
        'prompt_templates',
        ['agent_name', 'prompt_type', 'mod_id', 'version']
    )

    # Unique constraint for non-NULL mod_id
    op.create_index(
        'uq_prompt_templates_with_mod',
        'prompt_templates',
        ['agent_name', 'prompt_type', 'mod_id', 'version'],
        unique=True,
        postgresql_where=sa.text('mod_id IS NOT NULL')
    )

    # Create prompt_execution_log table
    op.create_table(
        'prompt_execution_log',
        sa.Column('id', UUID(as_uuid=True), nullable=False),
        sa.Column('trace_id', sa.String(length=64), nullable=True),
        sa.Column('session_id', sa.String(length=255), nullable=True),
        sa.Column('flow_execution_id', UUID(as_uuid=True), nullable=True),
        sa.Column('prompt_template_id', UUID(as_uuid=True), nullable=False),
        sa.Column('agent_name', sa.String(length=100), nullable=False),
        sa.Column('prompt_type', sa.String(length=50), nullable=False),
        sa.Column('mod_id', sa.String(length=20), nullable=True),
        sa.Column('prompt_version', sa.Integer(), nullable=False),
        sa.Column('executed_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['prompt_template_id'], ['prompt_templates.id']),
        sa.PrimaryKeyConstraint('id')
    )

    # Indexes for prompt_execution_log
    op.create_index('idx_prompt_exec_trace', 'prompt_execution_log', ['trace_id'])
    op.create_index('idx_prompt_exec_session', 'prompt_execution_log', ['session_id'])


def downgrade() -> None:
    # Drop prompt_execution_log table and indexes
    op.drop_index('idx_prompt_exec_session', table_name='prompt_execution_log')
    op.drop_index('idx_prompt_exec_trace', table_name='prompt_execution_log')
    op.drop_table('prompt_execution_log')

    # Drop prompt_templates table and indexes
    op.drop_index('uq_prompt_templates_with_mod', table_name='prompt_templates')
    op.drop_index('idx_prompt_templates_version', table_name='prompt_templates')
    op.drop_index('idx_prompt_templates_base_unique', table_name='prompt_templates')
    op.drop_index('idx_prompt_templates_active', table_name='prompt_templates')
    op.drop_table('prompt_templates')
