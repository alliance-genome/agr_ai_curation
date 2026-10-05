"""Record each flow a curator imports into the benchmark, one row per version.

Main AI Curation never writes this table. In the benchmark resolver each import adds
a row: which AI Curation flow and version it came from, the resolver flow it became,
and the exact revisions it pinned. Rows are never changed or deleted.

Revision ID: a7f1c2e3d4b5
Revises: u8d9e0f1a2b3
Create Date: 2026-10-05
"""

from collections.abc import Sequence

from alembic import op  # pyright: ignore[reportAttributeAccessIssue]
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "a7f1c2e3d4b5"
down_revision: str | Sequence[str] | None = "u8d9e0f1a2b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DIGEST = "^sha256:[a-f0-9]{64}$"


def upgrade() -> None:
    op.create_table(
        "benchmark_flow_imports",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.user_id", ondelete="RESTRICT"),
                  nullable=False),
        sa.Column("export_issuer", sa.Text(), nullable=False),
        sa.Column("source_flow_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_version", sa.String(71), nullable=False),
        sa.Column("flow_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("curation_flows.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("flow_revision", sa.String(71), nullable=False),
        sa.Column("pins", postgresql.JSONB(), nullable=False),
        sa.Column("bundle_sha256", sa.String(71), nullable=False),
        sa.Column("source_app_version", sa.Text(), nullable=False),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "export_issuer", "source_flow_id", "version",
                            name="uq_benchmark_flow_import_version"),
        sa.CheckConstraint("version > 0", name="ck_benchmark_flow_import_version_positive"),
        sa.CheckConstraint(
            f"source_version ~ '{DIGEST}' AND flow_revision ~ '{DIGEST}' "
            f"AND bundle_sha256 ~ '{DIGEST}'",
            name="ck_benchmark_flow_import_digests",
        ),
        sa.CheckConstraint("jsonb_typeof(pins) = 'array'", name="ck_benchmark_flow_import_pins_array"),
    )
    op.create_index("ix_benchmark_flow_imports_flow", "benchmark_flow_imports", ["flow_id"])
    op.execute(
        """
        CREATE FUNCTION benchmark_flow_imports_append_only() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'benchmark_flow_imports rows are append-only';
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER benchmark_flow_imports_append_only BEFORE UPDATE OR DELETE "
        "ON benchmark_flow_imports FOR EACH ROW EXECUTE FUNCTION benchmark_flow_imports_append_only()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER benchmark_flow_imports_append_only ON benchmark_flow_imports")
    op.execute("DROP FUNCTION benchmark_flow_imports_append_only()")
    op.drop_index("ix_benchmark_flow_imports_flow", table_name="benchmark_flow_imports")
    op.drop_table("benchmark_flow_imports")
