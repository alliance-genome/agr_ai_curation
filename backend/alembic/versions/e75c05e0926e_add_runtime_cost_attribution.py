"""Add reference-only attribution for ordinary chat and flow model attempts.

Revision ID: e75c05e0926e
Revises: d64c05e0925d
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "e75c05e0926e"
down_revision = "d64c05e0925d"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "runtime_cost_requests",
        sa.Column("deployment_id", sa.Text(), primary_key=True),
        sa.Column("attempt_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("activity", sa.Text(), nullable=False),
        sa.Column("workflow_id", sa.Text()),
        sa.Column("flow_run_id", sa.Text()),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model", sa.Text()),
        sa.Column("agent_id", sa.Text()),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["deployment_id", "attempt_id"],
                                ["cost_attempts.deployment_id", "cost_attempts.id"],
                                ondelete="RESTRICT", name="fk_runtime_cost_attempt"),
    )
    op.create_index("ix_runtime_cost_session", "runtime_cost_requests",
                    ["deployment_id", "session_id", "run_id"])


def downgrade():
    raise RuntimeError("Runtime accounting attribution requires a forward migration; do not discard it")
