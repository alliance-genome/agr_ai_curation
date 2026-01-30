"""Rename mod_id column to group_id in prompt tables

Revision ID: p0q1r2s3t4u5
Revises: o9p0q1r2s3t4
Create Date: 2026-01-30

This migration renames the mod_id column to group_id in:
- prompt_templates
- prompt_execution_log

This is part of the config-driven architecture portability effort
to use generic "groups" terminology instead of Alliance-specific "MOD".

See: KANBAN-1009, docs/plans/2026-01-30-config-driven-phase2-fixes.md
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'p0q1r2s3t4u5'
down_revision = 'o9p0q1r2s3t4'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # =========================================================================
    # PROMPT_TEMPLATES TABLE
    # =========================================================================

    # Step 1: Drop the CHECK constraint (references old column name and old MOD values)
    op.execute(sa.text("ALTER TABLE prompt_templates DROP CONSTRAINT IF EXISTS check_mod_id_valid"))

    # Step 2: Drop indexes that reference mod_id
    # These will be recreated with the new column name
    op.drop_index('uq_prompt_templates_single_active', table_name='prompt_templates')
    op.drop_index('uq_prompt_templates_with_mod', table_name='prompt_templates')
    op.drop_index('idx_prompt_templates_version', table_name='prompt_templates')
    op.drop_index('idx_prompt_templates_active', table_name='prompt_templates')
    op.drop_index('idx_prompt_templates_base_unique', table_name='prompt_templates')

    # Step 3: Rename the column
    op.alter_column('prompt_templates', 'mod_id', new_column_name='group_id')

    # Step 4: Recreate indexes with new column name
    # Index for fast lookups of active prompts
    op.create_index(
        'idx_prompt_templates_active',
        'prompt_templates',
        ['agent_name', 'prompt_type', 'group_id'],
        postgresql_where=sa.text('is_active = true')
    )

    # Index for version lookups
    op.create_index(
        'idx_prompt_templates_version',
        'prompt_templates',
        ['agent_name', 'prompt_type', 'group_id', 'version']
    )

    # Partial unique index for base prompts (group_id IS NULL)
    op.create_index(
        'idx_prompt_templates_base_unique',
        'prompt_templates',
        ['agent_name', 'prompt_type', 'version'],
        unique=True,
        postgresql_where=sa.text('group_id IS NULL')
    )

    # Unique constraint for non-NULL group_id
    op.create_index(
        'uq_prompt_templates_with_group',
        'prompt_templates',
        ['agent_name', 'prompt_type', 'group_id', 'version'],
        unique=True,
        postgresql_where=sa.text('group_id IS NOT NULL')
    )

    # Partial UNIQUE index to enforce only one active prompt per (agent_name, prompt_type, group_id)
    op.create_index(
        'uq_prompt_templates_single_active',
        'prompt_templates',
        ['agent_name', 'prompt_type', 'group_id'],
        unique=True,
        postgresql_where=sa.text('is_active = true')
    )

    # Step 5: Recreate CHECK constraint with new column name
    # NOTE: This constraint now uses group_id and includes the same valid group identifiers
    # Organizations using this system should modify this constraint for their own group IDs
    op.execute(sa.text("""
        ALTER TABLE prompt_templates
        ADD CONSTRAINT check_group_id_valid
        CHECK (
            group_id IS NULL
            OR (
                group_id != ''
                AND group_id IN ('FB', 'WB', 'MGI', 'RGD', 'SGD', 'HGNC', 'ZFIN')
            )
        )
    """))

    # =========================================================================
    # PROMPT_EXECUTION_LOG TABLE
    # =========================================================================

    # Rename the column (no indexes reference mod_id in this table)
    op.alter_column('prompt_execution_log', 'mod_id', new_column_name='group_id')


def downgrade() -> None:
    # =========================================================================
    # PROMPT_EXECUTION_LOG TABLE
    # =========================================================================

    # Rename back to mod_id
    op.alter_column('prompt_execution_log', 'group_id', new_column_name='mod_id')

    # =========================================================================
    # PROMPT_TEMPLATES TABLE
    # =========================================================================

    # Step 1: Drop the CHECK constraint
    op.execute(sa.text("ALTER TABLE prompt_templates DROP CONSTRAINT IF EXISTS check_group_id_valid"))

    # Step 2: Drop indexes
    op.drop_index('uq_prompt_templates_single_active', table_name='prompt_templates')
    op.drop_index('uq_prompt_templates_with_group', table_name='prompt_templates')
    op.drop_index('idx_prompt_templates_base_unique', table_name='prompt_templates')
    op.drop_index('idx_prompt_templates_version', table_name='prompt_templates')
    op.drop_index('idx_prompt_templates_active', table_name='prompt_templates')

    # Step 3: Rename column back
    op.alter_column('prompt_templates', 'group_id', new_column_name='mod_id')

    # Step 4: Recreate indexes with old column name
    op.create_index(
        'idx_prompt_templates_active',
        'prompt_templates',
        ['agent_name', 'prompt_type', 'mod_id'],
        postgresql_where=sa.text('is_active = true')
    )

    op.create_index(
        'idx_prompt_templates_version',
        'prompt_templates',
        ['agent_name', 'prompt_type', 'mod_id', 'version']
    )

    # Partial unique index for base prompts (mod_id IS NULL)
    op.create_index(
        'idx_prompt_templates_base_unique',
        'prompt_templates',
        ['agent_name', 'prompt_type', 'version'],
        unique=True,
        postgresql_where=sa.text('mod_id IS NULL')
    )

    op.create_index(
        'uq_prompt_templates_with_mod',
        'prompt_templates',
        ['agent_name', 'prompt_type', 'mod_id', 'version'],
        unique=True,
        postgresql_where=sa.text('mod_id IS NOT NULL')
    )

    op.create_index(
        'uq_prompt_templates_single_active',
        'prompt_templates',
        ['agent_name', 'prompt_type', 'mod_id'],
        unique=True,
        postgresql_where=sa.text('is_active = true')
    )

    # Step 5: Recreate original CHECK constraint
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
