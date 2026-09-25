"""Bind durable benchmark invocations to their measured model requests.

Revision ID: a31c05e0925a
Revises: 92a22b250925
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "a31c05e0925a"
down_revision = "92a22b250925"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Historical invocations have no verified measurement binding. Do not
    # manufacture one from their row ID, request digest, or sequence number.
    op.add_column(
        "benchmark_invocations",
        sa.Column("model_request_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_unique_constraint(
        "uq_benchmark_invocations_model_request", "benchmark_invocations", ["model_request_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_benchmark_invocations_model_request", "benchmark_invocations", type_="unique",
    )
    op.drop_column("benchmark_invocations", "model_request_id")
