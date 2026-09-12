"""Store each curator's ordered main-chat flow shortcuts.

Revision ID: p3e4f5a6b7c8
Revises: o2d3e4f5a6b7
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
revision = "p3e4f5a6b7c8"
down_revision = "o2d3e4f5a6b7"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "flow_shortcut_preferences",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.user_id", ondelete="CASCADE"), primary_key=True),
        sa.Column("flow_ids", postgresql.JSONB(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
    )


def downgrade():
    op.drop_table("flow_shortcut_preferences")
