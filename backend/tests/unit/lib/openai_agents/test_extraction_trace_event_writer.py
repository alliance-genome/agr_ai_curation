import json
import logging

from src.lib.openai_agents import extraction_trace_events as events


def test_writer_persists_versioned_redacted_bounded_events(tmp_path, monkeypatch):
    monkeypatch.setenv("EXTRACTION_TRACE_EVENT_DIR", str(tmp_path))
    monkeypatch.setenv("EXTRACTION_TRACE_EVENT_PREVIEW_LIMIT", "100")
    monkeypatch.setattr(events, "_mirror_to_langfuse", lambda _event: None)

    events.start_extraction_trace_run(
        trace_id="trace-123",
        session_id="session-123",
        user_id="curator@example.org",
        observation_id="obs-root",
    )
    try:
        first = events.write_extraction_trace_event(
            event_type="evidence.operation",
            input_summary={
                "domain_pack_id": "agr.alliance.gene_expression",
                "api_key": "secret-value",
                "paper_text": "x" * 200,
            },
            output_summary={"candidate_id": "gex-candidate-1"},
            validation={"status": "ok"},
        )
        second = events.write_extraction_trace_event(
            event_type="resolver.operation",
            tool_call_id="call-resolve",
            input_summary={"token": "secret-token"},
        )
    finally:
        events.clear_extraction_trace_run()

    assert first is not None
    assert second is not None
    assert first["schema_version"] == events.SCHEMA_VERSION
    assert second["sequence"] == first["sequence"] + 1
    assert first["trace_id"] == "trace-123"
    assert first["observation_id"] == "obs-root"
    assert first["session_id"] == "session-123"
    assert first["user_id_hash"]
    assert first["domain_pack_id"] == "agr.alliance.gene_expression"
    assert first["input_summary"]["preview"]["api_key"] == "<redacted>"
    assert first["input_summary"]["preview"]["paper_text"]["truncated"] is True
    assert second["input_summary"]["preview"]["token"] == "<redacted>"

    lines = events.trace_event_path("trace-123").read_text(encoding="utf-8").splitlines()
    persisted = [json.loads(line) for line in lines]
    assert [event["event_id"] for event in persisted] == [first["event_id"], second["event_id"]]


def test_stream_event_maps_specialist_tool_and_validation_events(tmp_path, monkeypatch):
    monkeypatch.setenv("EXTRACTION_TRACE_EVENT_DIR", str(tmp_path))
    monkeypatch.setattr(events, "_mirror_to_langfuse", lambda _event: None)
    events.start_extraction_trace_run(trace_id="trace-specialist")
    try:
        tool_event = events.write_stream_event(
            {
                "type": "TOOL_START",
                "timestamp": "2026-05-29T00:00:00Z",
                "details": {
                    "toolCallId": "call-specialist",
                    "toolName": "ask_gene_expression_specialist",
                    "isSpecialistInternal": True,
                    "toolArgs": {"gene": "tmem67"},
                },
            }
        )
        complete_event = events.write_stream_event(
            {
                "type": "TOOL_COMPLETE",
                "timestamp": "2026-05-29T00:00:01Z",
                "details": {
                    "toolCallId": "call-specialist",
                    "toolName": "search_document",
                    "isSpecialistInternal": True,
                    "success": True,
                },
                "internal": {
                    "tool_output": {"summary": "found expression evidence"},
                },
            }
        )
        validation_event = events.write_stream_event(
            {
                "type": "SPECIALIST_ERROR",
                "details": {
                    "error": "Candidate failed validation",
                    "reason": "missing_evidence",
                },
            }
        )
    finally:
        events.clear_extraction_trace_run()

    assert tool_event is not None
    assert complete_event is not None
    assert validation_event is not None
    assert tool_event["event_type"] == "specialist_tool_call.started"
    assert complete_event["event_type"] == "specialist_tool_call.completed"
    assert tool_event["tool_call_id"] == "call-specialist"
    assert complete_event["output_summary"]["preview"] == {"summary": "found expression evidence"}
    assert validation_event["event_type"] == "validation.failure"
    assert validation_event["validation"]["status"] == "failed"


def test_writer_logs_debug_when_event_has_no_trace_context(caplog):
    events.clear_extraction_trace_run()

    with caplog.at_level(logging.DEBUG, logger=events.logger.name):
        event = events.write_extraction_trace_event(event_type="specialist_tool_call.started")

    assert event is None
    assert "Dropping extraction trace event without trace context" in caplog.text
