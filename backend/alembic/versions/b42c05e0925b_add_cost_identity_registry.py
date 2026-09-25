"""Add shared accounting identities without copying usage or money.

Revision ID: b42c05e0925b
Revises: a31c05e0925a
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "b42c05e0925b"
down_revision = "a31c05e0925a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cost_attempts",
        sa.Column("deployment_id", sa.Text(), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_subject", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("deployment_id", "id"),
    )
    op.create_table(
        "cost_source_references",
        sa.Column("deployment_id", sa.Text(), nullable=False),
        sa.Column("source_system", sa.Text(), nullable=False),
        sa.Column("source_namespace", sa.Text(), nullable=False),
        sa.Column("source_id", sa.Text(), nullable=False),
        sa.Column("attempt_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("deployment_id", "source_system", "source_namespace", "source_id"),
        sa.ForeignKeyConstraint(
            ["deployment_id", "attempt_id"], ["cost_attempts.deployment_id", "cost_attempts.id"],
            name="fk_cost_source_attempt", ondelete="RESTRICT",
        ),
    )


def downgrade() -> None:
    op.drop_table("cost_source_references")
    op.drop_table("cost_attempts")
