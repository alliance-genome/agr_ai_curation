"""Integration coverage for chat SSE evidence_summary emission."""

from __future__ import annotations

from tests.fixtures.evidence.harness import build_expected_sse_records
from tests.integration.evidence_test_support import (
    collect_sse_events,
    configure_chat_stream_mocks,
    make_fixture_runner,
)

pytest_plugins = ["tests.integration.evidence_test_support"]


def test_chat_stream_emits_tool_verified_evidence_summary_with_frontend_shape(
    client,
    evidence_fixture,
    evidence_integration_context,
    monkeypatch,
):
    extraction = evidence_fixture["extraction"]
    session_id = "session-evidence-sse"

    configure_chat_stream_mocks(
        monkeypatch,
        document_id=evidence_integration_context["document_id"],
        filename=evidence_integration_context["paper"]["filename"],
        tool_agent_map={extraction["tool_name"]: extraction["agent_key"]},
        run_agent_streamed=make_fixture_runner(evidence_fixture),
    )

    with client.stream(
        "POST",
        "/api/chat/stream",
        json={
            "message": evidence_integration_context["paper"]["conversation_summary"],
            "session_id": session_id,
        },
    ) as stream_response:
        events = collect_sse_events(stream_response)
        assert stream_response.status_code == 200

    event_types = [event["type"] for event in events]
    assert event_types == [
        "RUN_STARTED",
        "TOOL_COMPLETE",
        "TOOL_COMPLETE",
        "TOOL_COMPLETE",
        "evidence_summary",
        "RUN_FINISHED",
    ]

    evidence_summary_event = next(
        event for event in events if event["type"] == "evidence_summary"
    )
    assert evidence_summary_event["session_id"] == session_id
    assert evidence_summary_event["sessionId"] == session_id
    assert evidence_summary_event["evidence_records"] == build_expected_sse_records(
        evidence_fixture
    )
