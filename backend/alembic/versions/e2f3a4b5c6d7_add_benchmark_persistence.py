"""Add durable PostgreSQL benchmark persistence.

Revision ID: e2f3a4b5c6d7
Revises: d1e2f3a4b5c6
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op  # pyright: ignore[reportAttributeAccessIssue]
from sqlalchemy.dialects.postgresql import JSONB, UUID


revision: str = "e2f3a4b5c6d7"
down_revision: str | Sequence[str] | None = "d1e2f3a4b5c6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DIGEST_CHECK = "{column} ~ '^sha256:[0-9a-f]{{64}}$'"


def upgrade() -> None:
    op.create_table(
        "benchmark_jobs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_subject", sa.String(255), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="queued"),
        sa.Column("suite_id", sa.String(128), nullable=False),
        sa.Column("suite_specification", JSONB, nullable=False),
        sa.Column("resolved_plan", JSONB, nullable=False),
        sa.Column("suite_digest", sa.String(71), nullable=False),
        sa.Column("catalog_digest", sa.String(71), nullable=False),
        sa.Column("plan_digest", sa.String(71), nullable=False),
        sa.Column("config_digest", sa.String(71), nullable=False),
        sa.Column("code_digest", sa.String(71), nullable=False),
        sa.Column("inputs_digest", sa.String(71), nullable=False),
        sa.Column("rerun_of_job_id", UUID(as_uuid=True), nullable=True),
        sa.Column("total_cells", sa.Integer(), nullable=False),
        sa.Column("queued_cells", sa.Integer(), nullable=False),
        sa.Column("running_cells", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("succeeded_cells", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_cells", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cancelled_cells", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_owner", UUID(as_uuid=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("id", "owner_subject", name="uq_benchmark_jobs_id_owner"),
        sa.ForeignKeyConstraint(
            ["rerun_of_job_id", "owner_subject"],
            ["benchmark_jobs.id", "benchmark_jobs.owner_subject"],
            name="fk_benchmark_jobs_rerun_same_owner",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'completed_with_failures', "
            "'cancel_requested', 'cancelled', 'failed')",
            name="ck_benchmark_jobs_status_values",
        ),
        sa.CheckConstraint("char_length(owner_subject) > 0", name="ck_benchmark_jobs_owner"),
        sa.CheckConstraint(
            "jsonb_typeof(suite_specification) = 'object'",
            name="ck_benchmark_jobs_suite_object",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(resolved_plan) = 'object'",
            name="ck_benchmark_jobs_plan_object",
        ),
        sa.CheckConstraint(
            "total_cells > 0 AND queued_cells >= 0 AND running_cells >= 0 "
            "AND succeeded_cells >= 0 AND failed_cells >= 0 AND cancelled_cells >= 0 "
            "AND queued_cells + running_cells + succeeded_cells + failed_cells + "
            "cancelled_cells = total_cells",
            name="ck_benchmark_jobs_counters",
        ),
        sa.CheckConstraint(
            "rerun_of_job_id IS NULL OR rerun_of_job_id <> id",
            name="ck_benchmark_jobs_not_self_rerun",
        ),
        sa.CheckConstraint(
            "(status = 'queued' AND started_at IS NULL AND completed_at IS NULL "
            "AND lease_owner IS NULL AND lease_expires_at IS NULL AND lease_heartbeat_at IS NULL) OR "
            "(status = 'running' AND started_at IS NOT NULL AND completed_at IS NULL "
            "AND lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL "
            "AND lease_heartbeat_at IS NOT NULL) OR "
            "(status = 'cancel_requested' AND started_at IS NOT NULL "
            "AND completed_at IS NULL AND cancel_requested_at IS NOT NULL "
            "AND lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL "
            "AND lease_heartbeat_at IS NOT NULL) OR "
            "(status IN ('completed', 'completed_with_failures', 'cancelled', 'failed') "
            "AND completed_at IS NOT NULL AND queued_cells = 0 AND running_cells = 0 "
            "AND lease_owner IS NULL AND lease_expires_at IS NULL AND lease_heartbeat_at IS NULL)",
            name="ck_benchmark_jobs_lifecycle",
        ),
        *[
            sa.CheckConstraint(
                _DIGEST_CHECK.format(column=column),
                name=f"ck_benchmark_jobs_{column}",
            )
            for column in (
                "suite_digest",
                "catalog_digest",
                "plan_digest",
                "config_digest",
                "code_digest",
                "inputs_digest",
            )
        ],
    )
    op.create_index(
        "ix_benchmark_jobs_owner_status_created",
        "benchmark_jobs",
        ["owner_subject", "status", sa.text("created_at DESC"), sa.text("id DESC")],
    )
    op.create_index(
        "ix_benchmark_jobs_queued_claim",
        "benchmark_jobs",
        ["created_at", "id"],
        postgresql_where=sa.text("status = 'queued'"),
    )
    op.create_index(
        "ix_benchmark_jobs_expired_lease_claim",
        "benchmark_jobs",
        ["lease_expires_at", "id"],
        postgresql_where=sa.text("status IN ('running', 'cancel_requested')"),
    )

    op.create_table(
        "benchmark_cells",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "job_id",
            UUID(as_uuid=True),
            sa.ForeignKey("benchmark_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("cell_key", sa.String(255), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("case_id", sa.String(128), nullable=False),
        sa.Column("configuration_id", sa.String(128), nullable=False),
        sa.Column("repetition", sa.Integer(), nullable=False),
        sa.Column("target_kind", sa.String(16), nullable=False),
        sa.Column("target_id", sa.String(255), nullable=False),
        sa.Column("routes", JSONB, nullable=False),
        sa.Column("input_resolver", sa.String(128), nullable=False),
        sa.Column("input_reference", sa.String(1024), nullable=False),
        sa.Column("input_version", sa.String(255), nullable=False),
        sa.Column("input_digest", sa.String(71), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("source_cell_id", UUID(as_uuid=True), nullable=True),
        sa.Column("source_job_id", UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_owner", UUID(as_uuid=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("generated_envelope", JSONB, nullable=True),
        sa.Column("envelope_size_bytes", sa.Integer(), nullable=True),
        sa.Column("failure", JSONB, nullable=True),
        sa.UniqueConstraint("id", "job_id", name="uq_benchmark_cells_id_job"),
        sa.UniqueConstraint("job_id", "cell_key", name="uq_benchmark_cells_job_key"),
        sa.UniqueConstraint("job_id", "position", name="uq_benchmark_cells_job_position"),
        sa.ForeignKeyConstraint(
            ["source_cell_id", "source_job_id"],
            ["benchmark_cells.id", "benchmark_cells.job_id"],
            name="fk_benchmark_cells_source",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')",
            name="ck_benchmark_cells_status_values",
        ),
        sa.CheckConstraint("position >= 0 AND repetition >= 1", name="ck_benchmark_cells_position"),
        sa.CheckConstraint("attempt_count >= 0", name="ck_benchmark_cells_attempt_count"),
        sa.CheckConstraint("target_kind IN ('agent', 'flow')", name="ck_benchmark_cells_target_kind"),
        sa.CheckConstraint("jsonb_typeof(routes) = 'object'", name="ck_benchmark_cells_routes_object"),
        sa.CheckConstraint(
            "input_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_benchmark_cells_input_digest",
        ),
        sa.CheckConstraint(
            "(source_cell_id IS NULL) = (source_job_id IS NULL)",
            name="ck_benchmark_cells_source_pair",
        ),
        sa.CheckConstraint(
            "(generated_envelope IS NULL AND envelope_size_bytes IS NULL) OR "
            "(generated_envelope IS NOT NULL AND jsonb_typeof(generated_envelope) = 'object' "
            "AND envelope_size_bytes > 0)",
            name="ck_benchmark_cells_envelope_pair",
        ),
        sa.CheckConstraint(
            "failure IS NULL OR jsonb_typeof(failure) = 'object'",
            name="ck_benchmark_cells_failure_object",
        ),
        sa.CheckConstraint(
            "(status = 'queued' AND started_at IS NULL AND completed_at IS NULL "
            "AND lease_owner IS NULL AND lease_expires_at IS NULL AND lease_heartbeat_at IS NULL "
            "AND generated_envelope IS NULL AND failure IS NULL) OR "
            "(status = 'running' AND started_at IS NOT NULL AND completed_at IS NULL "
            "AND lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL "
            "AND lease_heartbeat_at IS NOT NULL AND generated_envelope IS NULL AND failure IS NULL) OR "
            "(status = 'succeeded' AND completed_at IS NOT NULL AND generated_envelope IS NOT NULL "
            "AND failure IS NULL AND lease_owner IS NULL AND lease_expires_at IS NULL "
            "AND lease_heartbeat_at IS NULL) OR "
            "(status = 'failed' AND completed_at IS NOT NULL AND generated_envelope IS NULL "
            "AND failure IS NOT NULL AND lease_owner IS NULL AND lease_expires_at IS NULL "
            "AND lease_heartbeat_at IS NULL) OR "
            "(status = 'cancelled' AND completed_at IS NOT NULL AND generated_envelope IS NULL "
            "AND failure IS NULL AND lease_owner IS NULL AND lease_expires_at IS NULL "
            "AND lease_heartbeat_at IS NULL)",
            name="ck_benchmark_cells_lifecycle",
        ),
    )
    op.create_index(
        "ix_benchmark_cells_job_page", "benchmark_cells", ["job_id", "position", "id"]
    )
    op.create_index(
        "ix_benchmark_cells_queued_claim",
        "benchmark_cells",
        ["job_id", "position", "id"],
        postgresql_where=sa.text("status = 'queued'"),
    )
    op.create_index(
        "ix_benchmark_cells_expired_lease_claim",
        "benchmark_cells",
        ["job_id", "lease_expires_at", "id"],
        postgresql_where=sa.text("status = 'running'"),
    )

    op.create_table(
        "benchmark_invocations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "cell_id",
            UUID(as_uuid=True),
            sa.ForeignKey("benchmark_cells.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("route_slot", sa.String(255), nullable=False),
        sa.Column("request_digest", sa.String(71), nullable=False),
        sa.Column("response_digest", sa.String(71), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("failure", JSONB, nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "cell_id", "ordinal", name="uq_benchmark_invocations_cell_ordinal"
        ),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'failed', 'cancelled')",
            name="ck_benchmark_invocations_status_values",
        ),
        sa.CheckConstraint("ordinal >= 0 AND attempt >= 1", name="ck_benchmark_invocations_order"),
        sa.CheckConstraint(
            "request_digest ~ '^sha256:[0-9a-f]{64}$' AND "
            "(response_digest IS NULL OR response_digest ~ '^sha256:[0-9a-f]{64}$')",
            name="ck_benchmark_invocations_digests",
        ),
        sa.CheckConstraint(
            "failure IS NULL OR jsonb_typeof(failure) = 'object'",
            name="ck_benchmark_invocations_failure_object",
        ),
        sa.CheckConstraint(
            "(status = 'running' AND completed_at IS NULL AND response_digest IS NULL "
            "AND failure IS NULL) OR "
            "(status = 'succeeded' AND completed_at IS NOT NULL AND response_digest IS NOT NULL "
            "AND failure IS NULL) OR "
            "(status = 'failed' AND completed_at IS NOT NULL AND response_digest IS NULL "
            "AND failure IS NOT NULL) OR "
            "(status = 'cancelled' AND completed_at IS NOT NULL AND response_digest IS NULL "
            "AND failure IS NULL)",
            name="ck_benchmark_invocations_lifecycle",
        ),
    )
    op.create_index(
        "ix_benchmark_invocations_cell_order",
        "benchmark_invocations",
        ["cell_id", "ordinal", "id"],
    )

    op.create_table(
        "benchmark_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "job_id",
            UUID(as_uuid=True),
            sa.ForeignKey("benchmark_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("event_type", sa.String(128), nullable=False),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("job_id", "sequence", name="uq_benchmark_events_job_sequence"),
        sa.CheckConstraint("sequence >= 1", name="ck_benchmark_events_sequence"),
        sa.CheckConstraint("char_length(event_type) > 0", name="ck_benchmark_events_type"),
        sa.CheckConstraint("jsonb_typeof(payload) = 'object'", name="ck_benchmark_events_payload_object"),
    )
    op.create_index(
        "ix_benchmark_events_replay",
        "benchmark_events",
        ["job_id", "sequence", "id"],
    )

    op.execute(
        """
        CREATE FUNCTION benchmark_reject_terminal_update() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_TABLE_NAME = 'benchmark_jobs'
               AND OLD.status IN ('completed', 'completed_with_failures', 'cancelled', 'failed') THEN
                RAISE EXCEPTION 'terminal benchmark job content is immutable';
            END IF;
            IF TG_TABLE_NAME = 'benchmark_cells'
               AND OLD.status IN ('succeeded', 'failed', 'cancelled') THEN
                RAISE EXCEPTION 'terminal benchmark cell content is immutable';
            END IF;
            IF TG_TABLE_NAME = 'benchmark_invocations'
               AND OLD.status IN ('succeeded', 'failed', 'cancelled') THEN
                RAISE EXCEPTION 'terminal benchmark invocation content is immutable';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    for table in ("benchmark_jobs", "benchmark_cells", "benchmark_invocations"):
        op.execute(
            f"CREATE TRIGGER trg_{table}_terminal_update "
            f"BEFORE UPDATE ON {table} FOR EACH ROW "
            "EXECUTE FUNCTION benchmark_reject_terminal_update()"
        )

    op.execute(
        """
        CREATE FUNCTION benchmark_restrict_job_delete() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.status NOT IN ('completed', 'completed_with_failures', 'cancelled', 'failed') THEN
                RAISE EXCEPTION 'only terminal benchmark jobs may be deleted';
            END IF;
            RETURN OLD;
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_benchmark_jobs_terminal_delete "
        "BEFORE DELETE ON benchmark_jobs FOR EACH ROW "
        "EXECUTE FUNCTION benchmark_restrict_job_delete()"
    )

    op.execute(
        """
        CREATE FUNCTION benchmark_validate_cell_source() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE parent_job UUID;
        BEGIN
            SELECT rerun_of_job_id INTO parent_job FROM benchmark_jobs WHERE id = NEW.job_id;
            IF NEW.source_cell_id IS NULL AND parent_job IS NOT NULL THEN
                RAISE EXCEPTION 'rerun cells require source-cell lineage';
            END IF;
            IF NEW.source_cell_id IS NOT NULL AND parent_job IS DISTINCT FROM NEW.source_job_id THEN
                RAISE EXCEPTION 'source cell must belong to the rerun parent job';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_benchmark_cells_source "
        "BEFORE INSERT OR UPDATE OF job_id, source_cell_id, source_job_id ON benchmark_cells "
        "FOR EACH ROW EXECUTE FUNCTION benchmark_validate_cell_source()"
    )

    op.execute(
        """
        CREATE FUNCTION benchmark_bound_envelope() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE configured_limit INTEGER;
        DECLARE serialized_size INTEGER;
        BEGIN
            IF NEW.generated_envelope IS NULL THEN
                NEW.envelope_size_bytes := NULL;
                RETURN NEW;
            END IF;
            configured_limit := COALESCE(
                NULLIF(current_setting('app.benchmark_max_envelope_bytes', true), '')::INTEGER,
                10485760
            );
            serialized_size := octet_length(convert_to(NEW.generated_envelope::text, 'UTF8'));
            IF serialized_size > configured_limit THEN
                RAISE EXCEPTION 'generated benchmark envelope exceeds configured byte limit';
            END IF;
            NEW.envelope_size_bytes := serialized_size;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_benchmark_cells_envelope_bound "
        "BEFORE INSERT OR UPDATE OF generated_envelope ON benchmark_cells "
        "FOR EACH ROW EXECUTE FUNCTION benchmark_bound_envelope()"
    )

    op.execute(
        """
        CREATE FUNCTION benchmark_restrict_terminal_job_content() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE affected_job UUID;
        DECLARE parent_status TEXT;
        BEGIN
            IF TG_TABLE_NAME IN ('benchmark_cells', 'benchmark_events') THEN
                affected_job := CASE WHEN TG_OP = 'DELETE' THEN OLD.job_id ELSE NEW.job_id END;
            ELSE
                SELECT job_id INTO affected_job
                FROM benchmark_cells
                WHERE id = CASE WHEN TG_OP = 'DELETE' THEN OLD.cell_id ELSE NEW.cell_id END;
            END IF;
            SELECT status INTO parent_status FROM benchmark_jobs WHERE id = affected_job;
            IF parent_status IN ('completed', 'completed_with_failures', 'cancelled', 'failed') THEN
                RAISE EXCEPTION 'terminal benchmark job child content is immutable';
            END IF;
            RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END;
        $$
        """
    )
    for table in ("benchmark_cells", "benchmark_invocations", "benchmark_events"):
        op.execute(
            f"CREATE TRIGGER trg_{table}_terminal_job_content "
            f"BEFORE INSERT OR UPDATE OR DELETE ON {table} FOR EACH ROW "
            "EXECUTE FUNCTION benchmark_restrict_terminal_job_content()"
        )


def downgrade() -> None:
    for table in ("benchmark_events", "benchmark_invocations", "benchmark_cells"):
        op.execute(
            f"DROP TRIGGER IF EXISTS trg_{table}_terminal_job_content ON {table}"
        )
    op.execute("DROP FUNCTION IF EXISTS benchmark_restrict_terminal_job_content()")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_benchmark_cells_envelope_bound ON benchmark_cells"
    )
    op.execute("DROP FUNCTION IF EXISTS benchmark_bound_envelope()")
    op.execute("DROP TRIGGER trg_benchmark_cells_source ON benchmark_cells")
    op.execute("DROP FUNCTION benchmark_validate_cell_source()")
    op.execute("DROP TRIGGER trg_benchmark_jobs_terminal_delete ON benchmark_jobs")
    op.execute("DROP FUNCTION benchmark_restrict_job_delete()")
    for table in ("benchmark_invocations", "benchmark_cells", "benchmark_jobs"):
        op.execute(f"DROP TRIGGER trg_{table}_terminal_update ON {table}")
    op.execute("DROP FUNCTION benchmark_reject_terminal_update()")
    op.drop_table("benchmark_events")
    op.drop_table("benchmark_invocations")
    op.drop_table("benchmark_cells")
    op.drop_table("benchmark_jobs")
