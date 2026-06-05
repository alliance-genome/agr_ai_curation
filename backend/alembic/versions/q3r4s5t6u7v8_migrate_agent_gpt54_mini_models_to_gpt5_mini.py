"""Historically migrate agent GPT-5.4 Mini model references to GPT-5 Mini.

This migration reflects the May 2026 catalog state where `gpt-5.4-mini` was
temporarily moved to `gpt-5-mini`. Later migrations may move those rows forward
again if the active catalog changes.

Revision ID: q3r4s5t6u7v8
Revises: p2q3r4s5t6u7
Create Date: 2026-05-30
"""

from alembic import op


revision = "q3r4s5t6u7v8"
down_revision = "p2q3r4s5t6u7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Move persisted agents off the retired gpt-5.4-mini catalog entry."""
    op.execute(
        """
        UPDATE agents
        SET model_id = 'gpt-5-mini',
            updated_at = now()
        WHERE model_id = 'gpt-5.4-mini'
        """
    )


def downgrade() -> None:
    """Leave agent model choices unchanged on downgrade."""
    pass
