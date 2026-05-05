"""Migrate agent GPT-5.4 Nano model references to GPT-5.4 Mini.

Revision ID: f6a7b8c9d0e1
Revises: f5a6b7c8d9e0
Create Date: 2026-05-05
"""

from alembic import op


revision = "f6a7b8c9d0e1"
down_revision = "f5a6b7c8d9e0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Move persisted agents off GPT-5.4 Nano."""
    op.execute(
        """
        UPDATE agents
        SET model_id = 'gpt-5.4-mini',
            updated_at = now()
        WHERE model_id = 'gpt-5.4-nano'
        """
    )


def downgrade() -> None:
    """Leave agent model choices unchanged on downgrade."""
    pass
