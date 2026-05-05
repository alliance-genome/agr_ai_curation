"""Migrate agent GPT-5.4 model references to GPT-5.5.

Revision ID: f5a6b7c8d9e0
Revises: e4f5a6b7c8d9
Create Date: 2026-05-05
"""

from alembic import op


revision = "f5a6b7c8d9e0"
down_revision = "e4f5a6b7c8d9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Move persisted agents off the retired GPT-5.4 catalog entry."""
    op.execute(
        """
        UPDATE agents
        SET model_id = 'gpt-5.5',
            updated_at = now()
        WHERE model_id = 'gpt-5.4'
        """
    )


def downgrade() -> None:
    """Leave agent model choices unchanged on downgrade."""
    pass
