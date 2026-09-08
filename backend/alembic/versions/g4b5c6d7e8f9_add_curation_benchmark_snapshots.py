"""Add immutable curation benchmark snapshots and durable handoff attempts.

Revision ID: g4b5c6d7e8f9
Revises: f3a4b5c6d7e8
"""

from collections.abc import Sequence

from alembic import op  # pyright: ignore[reportAttributeAccessIssue]
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision: str = "g4b5c6d7e8f9"
down_revision: str | Sequence[str] | None = "f3a4b5c6d7e8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "curation_benchmark_snapshots",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("session_id", UUID(as_uuid=True), nullable=False),
        sa.Column("envelope_id", sa.String(), nullable=False),
        sa.Column("envelope_revision", sa.Integer(), nullable=False),
        sa.Column("envelope_digest", sa.String(71), nullable=False),
        sa.Column("bundle_json", sa.Text(), nullable=False),
        sa.Column("created_by_id", sa.String(), nullable=False),
        sa.Column("exported_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["curation_review_sessions.id"], ondelete="NO ACTION"),
        sa.ForeignKeyConstraint(["envelope_id"], ["domain_envelopes.envelope_id"], ondelete="NO ACTION"),
        sa.CheckConstraint("envelope_revision >= 1", name="ck_curation_benchmark_snapshots_revision"),
        sa.CheckConstraint("envelope_digest ~ '^sha256:[0-9a-f]{64}$'", name="ck_curation_benchmark_snapshots_digest"),
    )
    op.create_index("ix_curation_benchmark_snapshots_envelope_revision", "curation_benchmark_snapshots", ["envelope_id", "envelope_revision"])
    op.create_index("ix_curation_benchmark_snapshots_session", "curation_benchmark_snapshots", ["session_id"])
    op.create_table(
        "curation_benchmark_handoff_attempts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("snapshot_id", UUID(as_uuid=True), nullable=False),
        sa.Column("destination_id", sa.String(), nullable=False),
        sa.Column("replay_key", sa.String(71), nullable=False),
        sa.Column("idempotency_key", sa.String(71), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("receipt_id", sa.String(), nullable=True),
        sa.Column("redirect_path", sa.String(), nullable=True),
        sa.Column("failure_code", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["snapshot_id"], ["curation_benchmark_snapshots.id"], ondelete="NO ACTION"),
        sa.CheckConstraint("status IN ('sending', 'succeeded', 'failed', 'unknown')", name="ck_curation_benchmark_handoff_attempts_status"),
        sa.UniqueConstraint("replay_key", name="uq_curation_benchmark_handoff_attempts_replay_key"),
        sa.UniqueConstraint("idempotency_key", name="uq_curation_benchmark_handoff_attempts_idempotency_key"),
    )
    op.create_index("ix_curation_benchmark_handoff_attempts_snapshot", "curation_benchmark_handoff_attempts", ["snapshot_id"])
    op.execute(
        """
        CREATE FUNCTION curation_benchmark_restrict_snapshot_update()
        RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION 'curation benchmark snapshots are immutable';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_curation_benchmark_snapshots_immutable
        BEFORE UPDATE ON curation_benchmark_snapshots
        FOR EACH ROW EXECUTE FUNCTION curation_benchmark_restrict_snapshot_update()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_curation_benchmark_snapshots_immutable ON curation_benchmark_snapshots")
    op.execute("DROP FUNCTION IF EXISTS curation_benchmark_restrict_snapshot_update()")
    op.drop_index("ix_curation_benchmark_handoff_attempts_snapshot", table_name="curation_benchmark_handoff_attempts")
    op.drop_table("curation_benchmark_handoff_attempts")
    op.drop_index("ix_curation_benchmark_snapshots_session", table_name="curation_benchmark_snapshots")
    op.drop_index("ix_curation_benchmark_snapshots_envelope_revision", table_name="curation_benchmark_snapshots")
    op.drop_table("curation_benchmark_snapshots")
