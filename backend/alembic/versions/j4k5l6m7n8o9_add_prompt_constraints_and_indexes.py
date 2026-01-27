"""Add prompt constraints and analytics indexes

Revision ID: j4k5l6m7n8o9
Revises: i3j4k5l6m7n8
Create Date: 2025-12-30

This migration adds:
- Partial unique index to enforce single active prompt per (agent_name, prompt_type, mod_id)
- CHECK constraint to validate mod_id values (NULL or valid MOD identifiers)
- Analytics indexes on prompt_execution_log for common query patterns
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'j4k5l6m7n8o9'
down_revision = 'i3j4k5l6m7n8'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Partial UNIQUE index to enforce only one active prompt per (agent_name, prompt_type, mod_id)
    # This prevents data integrity issues where multiple prompts could be active simultaneously
    op.create_index(
        'uq_prompt_templates_single_active',
        'prompt_templates',
        ['agent_name', 'prompt_type', 'mod_id'],
        unique=True,
        postgresql_where=sa.text('is_active = true')
    )

    # CHECK constraint: mod_id must be NULL or a valid non-empty MOD identifier
    # Prevents empty strings and invalid MOD IDs from being stored
    op.execute(sa.text("""
        ALTER TABLE prompt_templates
        ADD CONSTRAINT check_mod_id_valid
        CHECK (
            mod_id IS NULL
            OR (
                mod_id != ''
                AND mod_id IN ('FB', 'WB', 'MGI', 'RGD', 'SGD', 'HGNC', 'ZFIN')
            )
        )
    """))

    # Analytics indexes for prompt_execution_log
    # These support common analytics queries like "how often is prompt X used?"
    op.create_index('idx_prompt_exec_template', 'prompt_execution_log', ['prompt_template_id'])
    op.create_index('idx_prompt_exec_agent', 'prompt_execution_log', ['agent_name'])
    op.create_index('idx_prompt_exec_executed_at', 'prompt_execution_log', ['executed_at'])


def downgrade() -> None:
    # Drop analytics indexes
    op.drop_index('idx_prompt_exec_executed_at', table_name='prompt_execution_log')
    op.drop_index('idx_prompt_exec_agent', table_name='prompt_execution_log')
    op.drop_index('idx_prompt_exec_template', table_name='prompt_execution_log')

    # Drop CHECK constraint
    op.execute(sa.text("ALTER TABLE prompt_templates DROP CONSTRAINT check_mod_id_valid"))

    # Drop unique active index
    op.drop_index('uq_prompt_templates_single_active', table_name='prompt_templates')
