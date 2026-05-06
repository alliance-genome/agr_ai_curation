"""Backfill record_evidence tool policy.

Revision ID: h7i8j9k0l1m2
Revises: g7h8i9j0k1l2
Create Date: 2026-05-06
"""

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = "h7i8j9k0l1m2"
down_revision = "g7h8i9j0k1l2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            INSERT INTO tool_policies (
                tool_key,
                display_name,
                description,
                category,
                curator_visible,
                allow_attach,
                allow_execute,
                config
            ) VALUES (
                'record_evidence',
                'Record Evidence',
                'Verify exact source text against a specific chunk before evidence is persisted.',
                'Document',
                false,
                false,
                true,
                '{}'::jsonb
            )
            ON CONFLICT (tool_key) DO NOTHING
            """
        )
    )


def downgrade() -> None:
    # Preserve runtime policy data on downgrade. The row may have existed before
    # this backfill in some environments.
    pass
