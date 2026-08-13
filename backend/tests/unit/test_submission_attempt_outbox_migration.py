"""Regression tests for the durable submission-attempt migration."""

from pathlib import Path


MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "f9a0b1c2d3e4_add_submission_attempt_outbox.py"
)


def test_submission_attempt_migration_defines_durable_unique_outbox_contract():
    source = MIGRATION_PATH.read_text()

    assert 'sa.Column("idempotency_key", sa.String(), nullable=True)' in source
    assert 'sa.Column("attempt_state", sa.String(), nullable=True)' in source
    assert '"uq_curation_submissions_idempotency_key"' in source
    assert "('pending', 'sending', 'succeeded', 'failed', 'unknown')" in source
    assert "Backfilled from the pre-outbox submission record." in source


def test_submission_attempt_migration_keeps_unresolved_rows_out_of_retention_cleanup():
    source = MIGRATION_PATH.read_text()

    assert 'sa.Column("retention_expires_at"' in source
    assert '"ix_submissions_retention"' in source
    backfill_sql = source[
        source.index("UPDATE curation_submissions"):source.index("WHERE mode = 'direct_submit'")
    ]
    assert "retention_expires_at" not in backfill_sql
