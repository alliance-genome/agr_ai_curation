"""Contract checks for scoped domain-envelope identity migration."""

from pathlib import Path


MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "c3d4e5f6a7b8_scope_domain_envelope_identity.py"
)


def test_scope_migration_blocks_colliding_and_unbound_legacy_rows():
    source = MIGRATION.read_text()

    assert "count(DISTINCT session_id) > 1" in source
    assert "envelope.session_id IS DISTINCT FROM candidate.session_id" in source
    assert "envelope linked to multiple review sessions" in source
    assert "session_id IS NULL AND source_extraction_result_id IS NULL" in source
    assert "null or unbound row requires explicit repair" in source
    assert "source_adapter_key" in source
    assert "source_extraction_result_id" in source


def test_scope_migration_enforces_candidate_session_ownership():
    source = MIGRATION.read_text()

    assert "uq_domain_envelopes_session_owner" in source
    assert "fk_curation_candidates_envelope_session_owner" in source
    assert 'initially="DEFERRED"' in source
    assert "uq_domain_envelopes_source_scope" in source
    assert "trg_domain_envelope_scope_immutable" in source
    assert "identity scope is immutable after materialization" in source
