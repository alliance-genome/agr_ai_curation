"""Represent non-conversation runtime work without fabricated sessions.

Revision ID: b08d16f1037b
Revises: a97c05e0926a
"""
from alembic import op
import sqlalchemy as sa

revision = "b08d16f1037b"
down_revision = "a97c05e0926a"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("runtime_cost_requests", "session_id", existing_type=sa.Text(), nullable=True)
    for name in ("document_id", "job_id", "invocation_id", "parent_invocation_id", "operation_type"):
        op.add_column("runtime_cost_requests", sa.Column(name, sa.Text(), nullable=True))
    op.add_column("runtime_cost_requests", sa.Column("candidate_count", sa.BigInteger(), nullable=True))
    op.add_column("runtime_cost_requests", sa.Column("pagination_request", sa.Boolean(), nullable=True))


def downgrade():
    raise RuntimeError("Runtime job attribution requires a forward migration")
