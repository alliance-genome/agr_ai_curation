"""Add immutable benchmark input snapshots.

Revision ID: f3a4b5c6d7e8
Revises: e2f3a4b5c6d7
"""

from collections.abc import Sequence

from alembic import op  # pyright: ignore[reportAttributeAccessIssue]
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


revision: str = "f3a4b5c6d7e8"
down_revision: str | Sequence[str] | None = "e2f3a4b5c6d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "benchmark_input_snapshots",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("digest", sa.String(71), nullable=False),
        sa.Column("source_version", sa.String(255), nullable=False),
        sa.Column("content_type", sa.String(128), nullable=False),
        sa.Column("content_bytes", sa.BigInteger(), nullable=False),
        sa.Column("resolver_id", sa.String(64), nullable=False),
        sa.Column("source_reference", sa.String(1024), nullable=False),
        sa.Column("sanitized_provenance", JSONB, nullable=False),
        sa.Column("owner_subject", sa.String(255), nullable=False),
        sa.Column("service_principal", sa.String(255), nullable=False),
        sa.Column("blob_reference", sa.String(2048), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "owner_subject",
            "resolver_id",
            "source_reference",
            "source_version",
            "digest",
            name="uq_benchmark_snapshot_owner_source_version_digest",
        ),
        sa.CheckConstraint(
            "digest ~ '^sha256:[0-9a-f]{64}$'", name="ck_benchmark_snapshot_digest"
        ),
        sa.CheckConstraint(
            "content_bytes > 0", name="ck_benchmark_snapshot_content_bytes"
        ),
        sa.CheckConstraint(
            "char_length(owner_subject) > 0", name="ck_benchmark_snapshot_owner"
        ),
        sa.CheckConstraint(
            "char_length(service_principal) > 0", name="ck_benchmark_snapshot_service"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(sanitized_provenance) = 'object'",
            name="ck_benchmark_snapshot_provenance_object",
        ),
    )
    op.create_index(
        "ix_benchmark_snapshots_digest", "benchmark_input_snapshots", ["digest"]
    )
    op.execute(
        """
        CREATE FUNCTION benchmark_restrict_snapshot_update()
        RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION 'benchmark input snapshots are immutable';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_benchmark_input_snapshots_immutable
        BEFORE UPDATE ON benchmark_input_snapshots
        FOR EACH ROW EXECUTE FUNCTION benchmark_restrict_snapshot_update()
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM benchmark_cells LIMIT 1) THEN
            RAISE EXCEPTION 'benchmark cells must be recreated from frozen input snapshots';
          END IF;
        END $$
        """
    )
    op.add_column(
        "benchmark_cells",
        sa.Column("input_snapshot_id", UUID(as_uuid=True), nullable=False),
    )
    op.create_foreign_key(
        "fk_benchmark_cells_input_snapshot",
        "benchmark_cells",
        "benchmark_input_snapshots",
        ["input_snapshot_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_table(
        "benchmark_job_input_snapshots",
        sa.Column(
            "job_id",
            UUID(as_uuid=True),
            sa.ForeignKey("benchmark_jobs.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("case_id", sa.String(128), primary_key=True),
        sa.Column(
            "snapshot_id",
            UUID(as_uuid=True),
            sa.ForeignKey("benchmark_input_snapshots.id", ondelete="RESTRICT"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_benchmark_job_input_snapshot_id",
        "benchmark_job_input_snapshots",
        ["snapshot_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_benchmark_job_input_snapshot_id",
        table_name="benchmark_job_input_snapshots",
    )
    op.drop_table("benchmark_job_input_snapshots")
    op.drop_constraint(
        "fk_benchmark_cells_input_snapshot", "benchmark_cells", type_="foreignkey"
    )
    op.drop_column("benchmark_cells", "input_snapshot_id")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_benchmark_input_snapshots_immutable "
        "ON benchmark_input_snapshots"
    )
    op.execute("DROP FUNCTION IF EXISTS benchmark_restrict_snapshot_update()")
    op.drop_index("ix_benchmark_snapshots_digest", table_name="benchmark_input_snapshots")
    op.drop_table("benchmark_input_snapshots")
