"""Drop hardcoded group_id CHECK constraint for portability

Revision ID: q1r2s3t4u5v6
Revises: p0q1r2s3t4u5
Create Date: 2026-01-30

This migration removes the hardcoded CHECK constraint on group_id that
limited values to Alliance-specific group IDs ('FB', 'WB', 'MGI', etc.).

With config-driven architecture, valid group IDs are defined in:
- config/groups.yaml (source of truth)
- Loaded at startup via groups_loader

The database should NOT enforce a static list of group IDs, as this:
1. Breaks portability for other organizations
2. Requires database migrations to add new groups
3. Duplicates validation that already exists in the application layer

After this migration, group_id validation is handled by:
- groups_loader at startup (loads from YAML)
- Application code validates against loaded groups

See: KANBAN-1014, docs/plans/2026-01-30-epic-review-phase2-tasks.md
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'q1r2s3t4u5v6'
down_revision = 'p0q1r2s3t4u5'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop the hardcoded CHECK constraint
    # The constraint limited group_id to only: 'FB', 'WB', 'MGI', 'RGD', 'SGD', 'HGNC', 'ZFIN'
    # This prevented adding new groups without database migrations
    op.execute(sa.text(
        "ALTER TABLE prompt_templates DROP CONSTRAINT IF EXISTS check_group_id_valid"
    ))


def downgrade() -> None:
    # Restore the hardcoded CHECK constraint (for rollback only)
    # WARNING: This will fail if any group_id values exist that aren't in the list
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
