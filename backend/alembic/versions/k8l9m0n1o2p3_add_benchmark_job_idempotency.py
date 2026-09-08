"""Add durable benchmark job-creation idempotency reservations."""

from alembic import op  # pyright: ignore[reportAttributeAccessIssue]
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "k8l9m0n1o2p3"
down_revision = "j7k8l9m0n1o2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "benchmark_job_idempotency",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("owner_subject", sa.String(255), nullable=False),
        sa.Column("operation", sa.String(16), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("request_digest", sa.String(71), nullable=False),
        sa.Column("curator_context_digest", sa.String(71), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("job_id", UUID(as_uuid=True), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_message", sa.String(512), nullable=True),
        sa.Column("error_status", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["job_id"], ["benchmark_jobs.id"], ondelete="SET NULL"
        ),
        sa.UniqueConstraint(
            "owner_subject",
            "operation",
            "idempotency_key",
            name="uq_benchmark_job_idempotency_owner_operation_key",
        ),
        sa.CheckConstraint(
            "operation IN ('submit', 'rerun')",
            name="ck_benchmark_job_idempotency_operation",
        ),
        sa.CheckConstraint(
            "outcome IN ('pending', 'accepted', 'failed')",
            name="ck_benchmark_job_idempotency_outcome",
        ),
        sa.CheckConstraint(
            "request_digest ~ '^sha256:[0-9a-f]{64}$' AND "
            "curator_context_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_benchmark_job_idempotency_digests",
        ),
        sa.CheckConstraint(
            "(outcome = 'pending' AND job_id IS NULL AND error_code IS NULL "
            "AND error_message IS NULL AND error_status IS NULL) OR "
            "(outcome = 'accepted' AND error_code IS NULL "
            "AND error_message IS NULL AND error_status IS NULL) OR "
            "(outcome = 'failed' AND job_id IS NULL AND error_code IS NOT NULL "
            "AND error_message IS NOT NULL AND error_status BETWEEN 400 AND 599)",
            name="ck_benchmark_job_idempotency_result",
        ),
    )
    op.create_index(
        "ix_benchmark_job_idempotency_job",
        "benchmark_job_idempotency",
        ["job_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_benchmark_job_idempotency_job",
        table_name="benchmark_job_idempotency",
    )
    op.drop_table("benchmark_job_idempotency")
