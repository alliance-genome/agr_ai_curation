"""Add durable agent history without changing saved agents or curator data.

Revision ID: c9b3e4f5a6d7
Revises: b8a2d3f4e5c6
"""
from alembic import op
import sqlalchemy as sa

revision = "c9b3e4f5a6d7"
down_revision = "b8a2d3f4e5c6"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("agent_history_payloads",
        sa.Column("sha256", sa.String(64), primary_key=True),
        sa.Column("content", sa.Text(), nullable=False))
    op.create_table("agent_history_events",
        sa.Column("sequence", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("event_id", sa.String(36), nullable=False, unique=True),
        sa.Column("owner_id", sa.Text(), nullable=False),
        sa.Column("session_id", sa.Text()),
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("invocation_id", sa.String(36), nullable=False),
        sa.Column("parent_invocation_id", sa.String(36)),
        sa.Column("lineage", sa.Text(), nullable=False),
        sa.Column("agent_key", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("tool_name", sa.Text()),
        sa.Column("call_id", sa.Text()),
        sa.Column("outcome", sa.Text()),
        sa.Column("payload_sha256", sa.String(64), sa.ForeignKey("agent_history_payloads.sha256"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    for name, columns in (
        ("owner_run", ["owner_id", "run_id", "sequence"]),
        ("owner_session", ["owner_id", "session_id", "sequence"]),
        ("invocation", ["invocation_id", "sequence"]),
    ):
        op.create_index(f"ix_agent_history_{name}_sequence", "agent_history_events", columns)


def downgrade():
    raise RuntimeError("Agent history is canonical retained data; downgrade requires an explicit archival plan.")
