"""Add durable benchmark worker result and invocation telemetry.

Revision ID: h5c6d7e8f9a0
Revises: g4b5c6d7e8f9
"""

from collections.abc import Sequence

from alembic import op  # pyright: ignore[reportAttributeAccessIssue]
import sqlalchemy as sa


revision: str = "h5c6d7e8f9a0"
down_revision: str | Sequence[str] | None = "g4b5c6d7e8f9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("benchmark_cells", sa.Column("envelope_digest", sa.String(71)))
    op.add_column("benchmark_cells", sa.Column("result_digest", sa.String(71)))
    op.create_check_constraint(
        "ck_benchmark_cells_result_digests",
        "benchmark_cells",
        "(envelope_digest IS NULL OR envelope_digest ~ '^sha256:[0-9a-f]{64}$') "
        "AND (result_digest IS NULL OR result_digest ~ '^sha256:[0-9a-f]{64}$')",
    )

    invocation_columns = (
        sa.Column("requested_provider", sa.String(64)),
        sa.Column("requested_model", sa.String(255)),
        sa.Column("reasoning_effort", sa.String(16)),
        sa.Column("actual_provider", sa.String(64)),
        sa.Column("actual_model", sa.String(255)),
        sa.Column("routing_attempt", sa.Integer()),
        sa.Column("sequence", sa.Integer()),
        sa.Column("latency_ms", sa.Integer()),
        sa.Column("input_tokens", sa.Integer()),
        sa.Column("output_tokens", sa.Integer()),
        sa.Column("total_tokens", sa.Integer()),
        sa.Column("billed_amount", sa.Numeric()),
        sa.Column("billed_unit", sa.String(32)),
        sa.Column("billed_source", sa.String(64)),
    )
    for column in invocation_columns:
        op.add_column("benchmark_invocations", column)
    # Historical terminal invocations are immutable to runtime writers. The
    # migration already holds the table's DDL lock; suspend only the three
    # content guards for this backfill and restore them in this same transaction.
    # A failed migration rolls back both the data and trigger-state changes.
    content_guards = (
        "trg_benchmark_invocations_terminal_update",
        "trg_benchmark_invocations_running_cell",
        "trg_benchmark_invocations_terminal_job_content",
    )
    for trigger in content_guards:
        op.execute(f"ALTER TABLE benchmark_invocations DISABLE TRIGGER {trigger}")
    op.execute("UPDATE benchmark_invocations SET sequence = ordinal + 1")
    for trigger in content_guards:
        op.execute(f"ALTER TABLE benchmark_invocations ENABLE TRIGGER {trigger}")
    op.alter_column("benchmark_invocations", "sequence", nullable=False)
    op.create_check_constraint(
        "ck_benchmark_invocations_telemetry_values",
        "benchmark_invocations",
        "sequence >= 1 AND "
        "(routing_attempt IS NULL OR routing_attempt >= 0) AND "
        "(latency_ms IS NULL OR latency_ms >= 0) AND "
        "(input_tokens IS NULL OR input_tokens >= 0) AND "
        "(output_tokens IS NULL OR output_tokens >= 0) AND "
        "(total_tokens IS NULL OR total_tokens >= 0) AND "
        "(billed_amount IS NULL OR billed_amount >= 0)",
    )
    op.create_check_constraint(
        "ck_benchmark_invocations_billed_cost",
        "benchmark_invocations",
        "(billed_amount IS NULL AND billed_unit IS NULL AND billed_source IS NULL) OR "
        "(billed_amount IS NOT NULL AND billed_unit IS NOT NULL AND billed_source IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_benchmark_invocations_billed_cost",
        "benchmark_invocations",
        type_="check",
    )
    op.drop_constraint(
        "ck_benchmark_invocations_telemetry_values",
        "benchmark_invocations",
        type_="check",
    )
    for name in (
        "billed_source",
        "billed_unit",
        "billed_amount",
        "total_tokens",
        "output_tokens",
        "input_tokens",
        "latency_ms",
        "sequence",
        "routing_attempt",
        "actual_model",
        "actual_provider",
        "reasoning_effort",
        "requested_model",
        "requested_provider",
    ):
        op.drop_column("benchmark_invocations", name)
    op.drop_constraint(
        "ck_benchmark_cells_result_digests", "benchmark_cells", type_="check"
    )
    op.drop_column("benchmark_cells", "result_digest")
    op.drop_column("benchmark_cells", "envelope_digest")
