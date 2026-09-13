"""Retain benchmark stage occurrences and per-call attribution.

Revision ID: 7b3168a940de
Revises: f54e2c6f6848
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "7b3168a940de"
down_revision = "f54e2c6f6848"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("benchmark_cells", sa.Column("pipeline_started_at", sa.DateTime(timezone=True)))
    op.add_column("benchmark_cells", sa.Column("pipeline_completed_at", sa.DateTime(timezone=True)))
    op.add_column("benchmark_cells", sa.Column("pipeline_elapsed_ms", sa.BigInteger()))
    op.create_check_constraint("ck_benchmark_cells_pipeline_interval", "benchmark_cells",
        "(pipeline_completed_at IS NULL) = (pipeline_elapsed_ms IS NULL) "
        "AND (pipeline_completed_at IS NULL OR pipeline_started_at IS NOT NULL) "
        "AND (pipeline_elapsed_ms IS NULL OR pipeline_elapsed_ms >= 0)")
    op.create_table(
        "benchmark_stages",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("cell_id", UUID(as_uuid=True), sa.ForeignKey("benchmark_cells.id", ondelete="CASCADE"), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("stage_id", sa.String(), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("node_id", sa.String()),
        sa.Column("source_node_id", sa.String()),
        sa.Column("binding_id", sa.String()),
        sa.Column("agent_id", sa.String()),
        sa.Column("parent_execution_id", UUID(as_uuid=True)),
        sa.Column("parent_invocation_sequence", sa.Integer()),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("elapsed_ms", sa.BigInteger()),
        sa.Column("failure_type", sa.String()),
        sa.UniqueConstraint("id", "cell_id", "attempt", name="uq_benchmark_stages_identity"),
        sa.UniqueConstraint("cell_id", "ordinal", name="uq_benchmark_stages_cell_ordinal"),
        sa.CheckConstraint("ordinal >= 0", name="ck_benchmark_stages_ordinal"),
        sa.ForeignKeyConstraint(
            ["parent_execution_id", "cell_id", "attempt"],
            ["benchmark_stages.id", "benchmark_stages.cell_id", "benchmark_stages.attempt"],
            name="fk_benchmark_stages_parent", ondelete="CASCADE",
        ),
        sa.CheckConstraint("attempt >= 1 AND char_length(stage_id) > 0", name="ck_benchmark_stages_identity"),
        sa.CheckConstraint("role IN ('extraction','validation','output','supervisor','other')", name="ck_benchmark_stages_role"),
        sa.CheckConstraint("status IN ('running','succeeded','failed','interrupted')", name="ck_benchmark_stages_status"),
        sa.CheckConstraint("parent_execution_id IS NULL OR parent_execution_id <> id", name="ck_benchmark_stages_not_self"),
        sa.CheckConstraint("parent_invocation_sequence IS NULL OR parent_invocation_sequence >= 1", name="ck_benchmark_stages_parent_sequence"),
        sa.CheckConstraint(
            "(completed_at IS NULL) = (elapsed_ms IS NULL) AND (elapsed_ms IS NULL OR elapsed_ms >= 0) "
            "AND (status <> 'running' OR completed_at IS NULL) "
            "AND (status NOT IN ('succeeded','failed') OR completed_at IS NOT NULL)",
            name="ck_benchmark_stages_interval",
        ),
    )
    op.create_index("ix_benchmark_stages_cell_attempt", "benchmark_stages", ["cell_id", "attempt", "started_at", "id"])
    # Existing rows deliberately remain NULL: historical attribution is unknown.
    op.add_column("benchmark_invocations", sa.Column("stage_execution_id", UUID(as_uuid=True)))
    op.add_column("benchmark_invocations", sa.Column("parent_invocation_sequence", sa.Integer()))
    op.create_foreign_key(
        "fk_benchmark_invocations_stage", "benchmark_invocations", "benchmark_stages",
        ["stage_execution_id", "cell_id", "attempt"], ["id", "cell_id", "attempt"], ondelete="CASCADE",
    )
    op.create_check_constraint("ck_benchmark_invocations_parent_sequence", "benchmark_invocations",
                               "parent_invocation_sequence IS NULL OR parent_invocation_sequence >= 1")


def downgrade():
    op.drop_constraint("ck_benchmark_cells_pipeline_interval", "benchmark_cells", type_="check")
    op.drop_column("benchmark_cells", "pipeline_elapsed_ms")
    op.drop_column("benchmark_cells", "pipeline_completed_at")
    op.drop_column("benchmark_cells", "pipeline_started_at")
    op.drop_constraint("ck_benchmark_invocations_parent_sequence", "benchmark_invocations", type_="check")
    op.drop_constraint("fk_benchmark_invocations_stage", "benchmark_invocations", type_="foreignkey")
    op.drop_column("benchmark_invocations", "parent_invocation_sequence")
    op.drop_column("benchmark_invocations", "stage_execution_id")
    op.drop_table("benchmark_stages")
