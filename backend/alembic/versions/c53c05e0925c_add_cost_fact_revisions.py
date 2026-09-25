"""Persist append-only sparse accounting facts with source-bound provenance.

Revision ID: c53c05e0925c
Revises: b42c05e0925b
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "c53c05e0925c"
down_revision = "b42c05e0925b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_cost_source_bound_attempt", "cost_source_references",
        ["deployment_id", "source_system", "source_namespace", "source_id", "attempt_id"],
    )
    op.create_table(
        "cost_fact_revisions",
        sa.Column("deployment_id", sa.Text(), nullable=False),
        sa.Column("attempt_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("source_system", sa.Text(), nullable=False),
        sa.Column("source_namespace", sa.Text(), nullable=False),
        sa.Column("source_id", sa.Text(), nullable=False),
        sa.Column("input_tokens", sa.BigInteger()),
        sa.Column("output_tokens", sa.BigInteger()),
        sa.Column("total_tokens", sa.BigInteger()),
        sa.Column("cache_read_tokens", sa.BigInteger()),
        sa.Column("cache_write_tokens", sa.BigInteger()),
        sa.Column("reasoning_tokens", sa.BigInteger()),
        sa.Column("billed_amount", sa.Numeric()),
        sa.Column("billed_unit", sa.Text()),
        sa.Column("billed_source", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("deployment_id", "attempt_id", "revision"),
        sa.ForeignKeyConstraint(
            ["deployment_id", "source_system", "source_namespace", "source_id", "attempt_id"],
            ["cost_source_references.deployment_id", "cost_source_references.source_system",
             "cost_source_references.source_namespace", "cost_source_references.source_id",
             "cost_source_references.attempt_id"],
            name="fk_cost_revision_source_attempt", ondelete="RESTRICT",
        ),
        sa.CheckConstraint("revision >= 1", name="ck_cost_revision_positive"),
        sa.CheckConstraint(
            "(input_tokens IS NULL OR input_tokens >= 0) AND "
            "(output_tokens IS NULL OR output_tokens >= 0) AND "
            "(total_tokens IS NULL OR total_tokens >= 0) AND "
            "(cache_read_tokens IS NULL OR cache_read_tokens >= 0) AND "
            "(cache_write_tokens IS NULL OR cache_write_tokens >= 0) AND "
            "(reasoning_tokens IS NULL OR reasoning_tokens >= 0)",
            name="ck_cost_revision_tokens",
        ),
        sa.CheckConstraint(
            "(billed_amount IS NULL AND billed_unit IS NULL AND billed_source IS NULL) OR "
            "(billed_amount IS NOT NULL AND billed_amount >= 0 AND "
            "billed_amount NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric) AND "
            "billed_unit IS NOT NULL AND length(trim(billed_unit)) > 0 AND "
            "billed_source IS NOT NULL AND length(trim(billed_source)) > 0)",
            name="ck_cost_revision_charge",
        ),
        sa.CheckConstraint(
            "num_nonnulls(input_tokens, output_tokens, total_tokens, cache_read_tokens, "
            "cache_write_tokens, reasoning_tokens, billed_amount) > 0",
            name="ck_cost_revision_nonempty",
        ),
    )
    op.execute("""
        CREATE FUNCTION guard_cost_fact_revision() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP <> 'INSERT' THEN
                RAISE EXCEPTION 'Accounting fact revisions are immutable';
            END IF;
            -- NO KEY UPDATE permits source FK key-share locks held by another
            -- delivering transaction, avoiding lock-upgrade deadlocks.
            PERFORM 1 FROM cost_attempts
            WHERE deployment_id = NEW.deployment_id AND id = NEW.attempt_id
            FOR NO KEY UPDATE;
            IF NEW.revision <> (SELECT COALESCE(MAX(revision), 0) + 1 FROM cost_fact_revisions
                WHERE deployment_id = NEW.deployment_id AND attempt_id = NEW.attempt_id) THEN
                RAISE EXCEPTION 'Accounting fact revisions must be sequential';
            END IF;
            IF EXISTS (SELECT 1 FROM cost_fact_revisions
                WHERE deployment_id = NEW.deployment_id AND attempt_id = NEW.attempt_id AND (
                    (input_tokens IS NOT NULL AND NEW.input_tokens IS NOT NULL) OR
                    (output_tokens IS NOT NULL AND NEW.output_tokens IS NOT NULL) OR
                    (total_tokens IS NOT NULL AND NEW.total_tokens IS NOT NULL) OR
                    (cache_read_tokens IS NOT NULL AND NEW.cache_read_tokens IS NOT NULL) OR
                    (cache_write_tokens IS NOT NULL AND NEW.cache_write_tokens IS NOT NULL) OR
                    (reasoning_tokens IS NOT NULL AND NEW.reasoning_tokens IS NOT NULL) OR
                    (billed_amount IS NOT NULL AND NEW.billed_amount IS NOT NULL))) THEN
                RAISE EXCEPTION 'Accounting facts cannot be repeated or overwritten';
            END IF;
            RETURN NEW;
        END;
        $$;
        CREATE TRIGGER cost_fact_revision_guard
        BEFORE INSERT OR UPDATE OR DELETE ON cost_fact_revisions
        FOR EACH ROW EXECUTE FUNCTION guard_cost_fact_revision();
    """)


def downgrade() -> None:
    op.drop_table("cost_fact_revisions")
    op.execute("DROP FUNCTION guard_cost_fact_revision()")
    op.drop_constraint("uq_cost_source_bound_attempt", "cost_source_references", type_="unique")
