"""Migrate agent GPT-5 Mini model references to GPT-5.4 Mini.

Revision ID: t6u7v8w9x0y1
Revises: s5t6u7v8w9x0
Create Date: 2026-06-05
"""

# pyright: reportAttributeAccessIssue=false

from alembic import op


revision = "t6u7v8w9x0y1"
down_revision = "s5t6u7v8w9x0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Move persisted agents onto the active mini model catalog entry."""
    op.execute(
        """
        UPDATE agents
        SET model_id = 'gpt-5.4-mini',
            updated_at = now()
        WHERE model_id = 'gpt-5-mini'
        """
    )


def downgrade() -> None:
    """Leave agent model choices unchanged on downgrade."""
    pass
