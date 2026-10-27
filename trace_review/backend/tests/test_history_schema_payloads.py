"""Tool schemas must not be confused with emitted validation-history facts."""
from copy import deepcopy
from unittest.mock import Mock

import pytest

from src.analyzers.domain_envelopes import DomainEnvelopeTraceAnalyzer


@pytest.mark.parametrize("field,value", [
    ("kind", {"type": "string"}), ("kind", ["record_restoration"]),
    ("fact_id", {"type": "string"}), ("fact_id", ["fact"]),
    ("sha256", {"type": "string"}), ("sha256", ["digest"]),
    ("fact_id", 42), ("fact_id", ""), ("sha256", ""),
])
def test_malformed_history_identity_does_not_hide_valid_sibling(field, value):
    valid = {"kind": "record_restoration", "fact_id": "fact", "sha256": "digest"}
    malformed = {**valid, field: value, "envelope_id": "still-visible"}
    payload = {"schema": malformed, "history": [valid, deepcopy(valid)]}
    original = deepcopy(payload)
    result = DomainEnvelopeTraceAnalyzer.analyze_payload(payload)
    assert len(result["followup_history"]) == 1
    assert result["followup_history"][0]["fact_id"] == "fact"
    assert "still-visible" in result["envelope_ids"]
    assert payload == original


def test_full_refresh_export_preserves_schema_observations_and_history(tmp_path, monkeypatch):
    from src.api.traces import _get_or_analyze_trace_export
    from src.services.cache_manager import CacheManager
    from src.services.trace_extractor import TraceExtractor

    monkeypatch.setenv("EXTRACTION_TRACE_EVENT_DIR", str(tmp_path))
    fact = {"kind": "record_restoration", "fact_id": "fact", "sha256": "digest"}
    observations = [{
        "id": "root", "trace_name": "schema and history", "is_root_observation": True,
        "start_time": "2026-10-27T00:00:00Z",
        "input": {"tools": [{"parameters": {"type": "object", "properties": {
            "kind": {"type": "string", "enum": ["record_restoration"]},
            "fact_id": {"type": "string"}, "sha256": {"type": "string"},
        }}}]},
        "output": {"history": [fact, deepcopy(fact)]},
    }]
    original = deepcopy(observations)
    extractor = object.__new__(TraceExtractor)
    extractor.source = "remote"
    extractor.host = "https://langfuse.example"
    extractor.client = Mock()
    extractor.get_observations = Mock(return_value=observations)
    extractor.get_scores = Mock(return_value=[])
    result, _, cached = _get_or_analyze_trace_export(
        "schema-history", CacheManager(), "remote", extractor=extractor, refresh=True,
    )
    assert not cached
    assert result["observations"] == original
    history = result["analysis"]["domain_envelope"]["followup_history"]
    assert len(history) == 1
    assert history[0]["fact_id"] == "fact"
