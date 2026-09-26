"""Preserve runtime agent/step and requested versus observed service tier.

Revision ID: a97c05e0926a
Revises: f86c05e0926f
"""
from alembic import op
import sqlalchemy as sa

revision = "a97c05e0926a"
down_revision = "f86c05e0926f"
branch_labels = None
depends_on = None


def upgrade():
    # Historical requests remain unknown: today's agent/settings are not
    # evidence of what actually executed before this capture was introduced.
    for name in ("agent_name", "agent_role", "agent_revision", "node_id",
                 "requested_service_tier", "effective_service_tier"):
        op.add_column("runtime_cost_requests", sa.Column(name, sa.Text(), nullable=True))


def downgrade():
    raise RuntimeError("Runtime attribution requires a forward migration; do not discard it")
