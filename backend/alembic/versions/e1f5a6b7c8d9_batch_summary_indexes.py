"""Index owner-scoped history and completion-time discovery.

Revision ID: e1f5a6b7c8d9
Revises: d0e4f5a6b7c8
"""

from alembic import op
import sqlalchemy as sa

revision = "e1f5a6b7c8d9"
down_revision = "d0e4f5a6b7c8"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index(
        "idx_batches_owner_history", "batches", ["user_id", "created_at", "id"]
    )
    op.create_index(
        "idx_batches_owner_active_history",
        "batches",
        ["user_id", "created_at", "id"],
        postgresql_where=sa.text("status IN ('pending', 'running')"),
    )
    op.create_index(
        "idx_batches_owner_terminal",
        "batches",
        ["user_id", "completed_at", "id"],
        postgresql_where=sa.text("status IN ('completed', 'cancelled')"),
    )


def downgrade():
    op.drop_index("idx_batches_owner_terminal", table_name="batches")
    op.drop_index("idx_batches_owner_active_history", table_name="batches")
    op.drop_index("idx_batches_owner_history", table_name="batches")
