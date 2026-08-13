"""Add durable exclusive worker leases to batches.

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-07-11
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "c3d4e5f6a7b8"
down_revision = "b2c3d4e5f6a7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("batches", sa.Column("lease_owner", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("batches", sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("batches", sa.Column("lease_heartbeat_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index(
        "idx_batches_recovery_lease",
        "batches",
        ["status", "lease_expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_batches_recovery_lease", table_name="batches")
    op.drop_column("batches", "lease_heartbeat_at")
    op.drop_column("batches", "lease_expires_at")
    op.drop_column("batches", "lease_owner")
