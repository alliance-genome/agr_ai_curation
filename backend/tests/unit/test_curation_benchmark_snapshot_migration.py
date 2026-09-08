"""Regression checks for immutable curation snapshot persistence."""

from pathlib import Path


MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "g4b5c6d7e8f9_add_curation_benchmark_snapshots.py"
)


def test_snapshot_migration_is_immutable_and_handoff_state_is_durable():
    source = MIGRATION.read_text()

    assert '"curation_benchmark_snapshots"' in source
    assert 'sa.Column("bundle_json", sa.Text(), nullable=False)' in source
    assert "trg_curation_benchmark_snapshots_immutable" in source
    assert '"curation_benchmark_handoff_attempts"' in source
    assert "('sending', 'succeeded', 'failed', 'unknown')" in source
    assert "uq_curation_benchmark_handoff_attempts_replay_key" in source
    assert "uq_curation_benchmark_handoff_attempts_idempotency_key" in source
