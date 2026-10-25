"""Explicit archival exclusions never conceal invalid current extraction output."""
import pytest

from src.lib.validation_history.migration import retained_source_reason


def test_known_legacy_wire_shapes_are_reportable_without_changing_payloads():
    from copy import deepcopy
    examples = [
        ("old_extractor", {"envelope_id": "old", "domain_pack_id": "fixture", "objects": []}, "legacy_objects_envelope"),
        ("old_extractor", {"run_summary": {}, "items": [], "genes": []}, "legacy_specialized_output"),
        ("curation_prep", {"envelope_refs": [], "review_row_count": 0, "candidates": [],
                           "run_metadata": {"model_name": "fixture"}}, "curation_preparation_output"),
    ]
    for agent, payload, reason in examples:
        original = deepcopy(payload)
        assert retained_source_reason(agent, payload) == reason
        assert payload == original


@pytest.mark.parametrize("payload", [
    [], {}, {"items": []}, {"run_summary": {}, "items": []},
    {"objects": [], "envelope_id": "missing-pack"},
    {"objects": [], "envelope_id": "old", "domain_pack_id": "fixture", "extracted_objects": None},
    {"run_summary": {}, "items": [], "genes": [], "curatable_objects": "broken"},
])
def test_unknown_or_current_shaped_payloads_cannot_be_excluded(payload):
    assert retained_source_reason("extractor", payload) is None


def test_malformed_preparation_output_still_stops_import():
    with pytest.raises(ValueError):
        retained_source_reason("curation_prep", {"run_metadata": None})


def test_cli_reports_every_authorized_exclusion_and_still_imports_current(monkeypatch, capsys):
    import json
    from src.lib.validation_history import migration
    calls = []
    monkeypatch.setattr("sys.argv", ["migration", "--workers-drained", "--retain-legacy-results",
                                    "--extraction", "legacy", "--extraction", "current"])
    monkeypatch.setattr(migration, "retained_extraction_reason",
                        lambda identity: "legacy_objects_envelope" if identity == "legacy" else None)
    monkeypatch.setattr(migration, "import_historical_extraction", lambda identity: calls.append(identity) or True)
    migration.main()
    records = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert calls == ["current"]
    assert records == [
        {"kind": "extraction", "id": "legacy", "imported": False,
         "disposition": "retained_unchanged", "reason": "legacy_objects_envelope"},
        {"kind": "extraction", "id": "current", "imported": True},
    ]


def test_cli_default_does_not_silently_exclude_unsupported_sources(monkeypatch):
    from src.lib.validation_history import migration
    monkeypatch.setattr("sys.argv", ["migration", "--workers-drained", "--extraction", "legacy"])
    monkeypatch.setattr(migration, "retained_extraction_reason", lambda _: pytest.fail("Retention not requested"))
    def reject(identity):
        raise ValueError("Unsupported historical source")
    monkeypatch.setattr(migration, "import_historical_extraction", reject)
    with pytest.raises(ValueError, match="Unsupported historical source"):
        migration.main()
