import json

from src.lib.runtime_payload_budget import (
    classify_threshold,
    estimate_tokens_from_chars,
    json_size,
    large_scalar_paths,
    largest_json_paths,
    provider_context_preflight,
)


def test_runtime_payload_size_helpers_are_deterministic():
    payload = {"b": "two", "a": ["one", "three"]}

    size = json_size(payload)

    assert size.json_chars == len('{"a": ["one", "three"], "b": "two"}')
    assert size.estimated_tokens == estimate_tokens_from_chars(size.json_chars)
    assert size.threshold is None


def test_threshold_classification_uses_estimated_tokens():
    assert classify_threshold(99_999) is None
    assert classify_threshold(100_000) == "100000"
    assert classify_threshold(300_000) == "250000"


def test_path_reports_find_large_structures_and_scalars():
    payload = {
        "selected_inputs": {
            "identifier": "FB:FBgn0000008",
            "evidence_quote": "large quote " * 120,
        },
        "requests": [{"id": index} for index in range(30)],
    }

    scalar_paths = large_scalar_paths(
        payload["selected_inputs"],
        root_path="selected_inputs",
        min_chars=500,
    )
    largest_paths = largest_json_paths(payload, min_json_chars=200)

    assert scalar_paths[0]["path"] == "selected_inputs.evidence_quote"
    assert largest_paths[0]["path"] == "$"
    assert any(path["path"] == "requests" for path in largest_paths)


def test_preflight_trace_event_is_summary_only(monkeypatch):
    captured = []

    from src.lib.openai_agents import extraction_trace_events

    def fake_write_extraction_trace_event(**kwargs):
        captured.append(kwargs)

    monkeypatch.setattr(
        extraction_trace_events,
        "write_extraction_trace_event",
        fake_write_extraction_trace_event,
    )

    provider_context_preflight(
        surface="validator",
        operation="domain_validator_single",
        provider="openai",
        model="gpt-5.5",
        payload={
            "messages": [{"content": "raw prompt value that must not be emitted"}],
            "selected_inputs": {
                "evidence_quote": "full selected input quote that must not be emitted",
            },
        },
        metadata={"trace_id": "trace-123", "session_id": "session-1"},
        emit_trace_event=True,
    )

    assert len(captured) == 1
    event = captured[0]
    serialized_event = json.dumps(event, sort_keys=True, default=str)
    assert event["trace_id"] == "trace-123"
    assert event["event_type"] == "runtime.provider_context_preflight"
    assert event["input_summary"]["payload_summary"]["json_chars"] > 0
    assert "raw prompt value that must not be emitted" not in serialized_event
    assert "full selected input quote that must not be emitted" not in serialized_event
