"""Migrate custom (non-system) agents to the GPT-5.6 catalog.

Moves persisted user/custom agents off the superseded GPT-5.5 / GPT-5.4 Mini
model IDs and onto the new GPT-5.6 models:

- gpt-5.5      -> gpt-5.6-sol
- gpt-5.4-mini -> gpt-5.6-terra

Only ``visibility != 'system'`` rows are touched. System agents are owned by
``config/agents`` + package ``agent.yaml`` and are re-synced from that config on
startup (``sync_system_agents``), so they must not be migrated here. Each custom
agent keeps its own ``model_reasoning``.

Revision ID: a1b2c3d4e5f6
Revises: x9y0z1a2b3c4
Create Date: 2026-07-09
"""

from alembic import op


revision = "a1b2c3d4e5f6"
down_revision = "x9y0z1a2b3c4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Point custom agents at the GPT-5.6 models (reasoning unchanged)."""
    op.execute(
        """
        UPDATE agents
        SET model_id = 'gpt-5.6-sol',
            updated_at = now()
        WHERE model_id = 'gpt-5.5'
          AND visibility != 'system'
        """
    )
    op.execute(
        """
        UPDATE agents
        SET model_id = 'gpt-5.6-terra',
            updated_at = now()
        WHERE model_id = 'gpt-5.4-mini'
          AND visibility != 'system'
        """
    )


def downgrade() -> None:
    """Revert custom agents to the prior GPT-5.5 / GPT-5.4 Mini models."""
    op.execute(
        """
        UPDATE agents
        SET model_id = 'gpt-5.5',
            updated_at = now()
        WHERE model_id = 'gpt-5.6-sol'
          AND visibility != 'system'
        """
    )
    op.execute(
        """
        UPDATE agents
        SET model_id = 'gpt-5.4-mini',
            updated_at = now()
        WHERE model_id = 'gpt-5.6-terra'
          AND visibility != 'system'
        """
    )
