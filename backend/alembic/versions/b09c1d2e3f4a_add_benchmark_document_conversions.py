"""Add service-owned benchmark document conversion records.

Revision ID: b09c1d2e3f4a
Revises: b08d16f1037b
"""

from collections.abc import Sequence

from alembic import op  # pyright: ignore[reportAttributeAccessIssue]
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


revision: str = "b09c1d2e3f4a"
down_revision: str | Sequence[str] | None = "b08d16f1037b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "benchmark_document_conversions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_subject", sa.String(255), nullable=False),
        sa.Column("service_principal", sa.String(255), nullable=False),
        sa.Column("curator_subject", sa.String(255), nullable=False),
        sa.Column(
            "curator_db_user_id",
            sa.Integer(),
            sa.ForeignKey("users.user_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("input_kind", sa.String(16), nullable=False),
        sa.Column("source_digest", sa.String(71), nullable=True),
        sa.Column("source_blob_reference", sa.String(2048), nullable=True),
        sa.Column("abc_reference", sa.String(64), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_message", sa.String(512), nullable=True),
        sa.Column(
            "snapshot_id",
            UUID(as_uuid=True),
            sa.ForeignKey("benchmark_input_snapshots.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("conversion_identity", JSONB, nullable=True),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "owner_subject",
            "idempotency_key",
            name="uq_benchmark_document_conversions_owner_key",
        ),
        sa.CheckConstraint(
            "char_length(owner_subject) > 0 AND char_length(service_principal) > 0 "
            "AND char_length(curator_subject) > 0 AND char_length(idempotency_key) > 0",
            name="ck_benchmark_document_conversions_identity",
        ),
        sa.CheckConstraint(
            "input_kind IN ('pdf', 'abc_reference')",
            name="ck_benchmark_document_conversions_input_kind",
        ),
        sa.CheckConstraint(
            "(input_kind = 'pdf' AND source_digest IS NOT NULL "
            "AND source_digest ~ '^sha256:[0-9a-f]{64}$' AND source_blob_reference IS NOT NULL "
            "AND char_length(source_blob_reference) > 0 AND abc_reference IS NULL) OR "
            "(input_kind = 'abc_reference' AND abc_reference IS NOT NULL "
            "AND abc_reference ~ '^AGRKB:[0-9]+$' "
            "AND source_digest IS NULL AND source_blob_reference IS NULL)",
            name="ck_benchmark_document_conversions_input_fields",
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed')",
            name="ck_benchmark_document_conversions_status",
        ),
        sa.CheckConstraint(
            "(status = 'queued' AND started_at IS NULL AND completed_at IS NULL "
            "AND snapshot_id IS NULL AND conversion_identity IS NULL "
            "AND error_code IS NULL AND error_message IS NULL) OR "
            "(status = 'running' AND started_at IS NOT NULL AND completed_at IS NULL "
            "AND snapshot_id IS NULL AND conversion_identity IS NULL "
            "AND error_code IS NULL AND error_message IS NULL) OR "
            "(status = 'succeeded' AND started_at IS NOT NULL AND completed_at IS NOT NULL "
            "AND snapshot_id IS NOT NULL AND conversion_identity IS NOT NULL "
            "AND jsonb_typeof(conversion_identity) = 'object' "
            "AND error_code IS NULL AND error_message IS NULL) OR "
            "(status = 'failed' AND completed_at IS NOT NULL AND snapshot_id IS NULL "
            "AND conversion_identity IS NULL AND error_code IS NOT NULL AND error_message IS NOT NULL "
            "AND char_length(error_code) > 0 AND char_length(error_message) > 0)",
            name="ck_benchmark_document_conversions_status_fields",
        ),
    )
    op.create_index(
        "ix_benchmark_document_conversions_unfinished",
        "benchmark_document_conversions",
        ["created_at", "id"],
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )
    op.create_index(
        "ix_benchmark_document_conversions_snapshot",
        "benchmark_document_conversions",
        ["snapshot_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_benchmark_document_conversions_snapshot",
        table_name="benchmark_document_conversions",
    )
    op.drop_index(
        "ix_benchmark_document_conversions_unfinished",
        table_name="benchmark_document_conversions",
    )
    op.drop_table("benchmark_document_conversions")
