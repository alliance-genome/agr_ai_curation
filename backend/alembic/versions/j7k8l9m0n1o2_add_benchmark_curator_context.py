"""Freeze the initiating curator context for new benchmark jobs.

Historical jobs deliberately retain NULL: their human authorization cannot be
reconstructed from service ownership or paper contents.
"""

from alembic import op  # pyright: ignore[reportAttributeAccessIssue]
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "j7k8l9m0n1o2"
down_revision = "i6j7k8l9m0n1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("benchmark_jobs", sa.Column("curator_context", JSONB(), nullable=True))
    op.create_check_constraint(
        "ck_benchmark_jobs_curator_context_object",
        "benchmark_jobs",
        "curator_context IS NULL OR jsonb_typeof(curator_context) = 'object'",
    )
    op.execute("""
        CREATE FUNCTION benchmark_guard_curator_context() RETURNS trigger AS $$
        BEGIN
          IF TG_OP = 'INSERT' THEN
            IF NEW.curator_context IS NULL THEN
              RAISE EXCEPTION 'new benchmark jobs require curator context';
            END IF;
          ELSIF NEW.curator_context IS DISTINCT FROM OLD.curator_context THEN
            RAISE EXCEPTION 'benchmark curator context is immutable';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER trg_benchmark_jobs_curator_context
        BEFORE INSERT OR UPDATE ON benchmark_jobs
        FOR EACH ROW EXECUTE FUNCTION benchmark_guard_curator_context()
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER trg_benchmark_jobs_curator_context ON benchmark_jobs")
    op.execute("DROP FUNCTION benchmark_guard_curator_context()")
    op.drop_constraint("ck_benchmark_jobs_curator_context_object", "benchmark_jobs")
    op.drop_column("benchmark_jobs", "curator_context")
