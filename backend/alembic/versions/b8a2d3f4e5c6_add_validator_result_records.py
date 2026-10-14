"""Store exact standalone validator results separately from extractions.

Revision ID: b8a2d3f4e5c6
Revises: a7f1c2e3d4b5
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "b8a2d3f4e5c6"
down_revision = "a7f1c2e3d4b5"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "validator_result_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("origin_session_id", sa.String(), nullable=False),
        sa.Column("invocation_id", sa.String(), nullable=False),
        sa.Column("agent_key", sa.String(), nullable=False),
        sa.Column("payload_sha256", sa.String(64), nullable=False),
        sa.Column("payload_json", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("user_id", "origin_session_id", "invocation_id", name="uq_validator_result_invocation"),
    )
    op.create_index("ix_validator_result_owner_session", "validator_result_records", ["user_id", "origin_session_id"])


def downgrade():
    op.drop_index("ix_validator_result_owner_session", table_name="validator_result_records")
    op.drop_table("validator_result_records")
