"""Integration coverage for chat SSE evidence_summary emission."""

from __future__ import annotations

import pytest

from tests.fixtures.evidence.harness import (
    ALL_EVIDENCE_FIXTURE_NAMES,
    build_expected_sse_records,
)
from tests.integration.evidence_test_support import (
    collect_sse_events,
    configure_chat_stream_mocks,
    make_fixture_runner,
)

pytest_plugins = ["tests.integration.evidence_test_support"]


def _assert_frontend_evidence_records_match(
    actual_records: list[dict[str, object]],
    expected_records: list[dict[str, object]],
) -> None:
    """Validate the frontend-required shape while allowing additive record metadata."""

    assert len(actual_records) == len(expected_records)

    seen_record_ids: set[str] = set()
    for actual_record, expected_record in zip(actual_records, expected_records):
        assert {key: actual_record[key] for key in expected_record} == expected_record

        evidence_record_id = actual_record.get("evidence_record_id")
        assert isinstance(evidence_record_id, str)
        assert evidence_record_id
        assert evidence_record_id not in seen_record_ids
        seen_record_ids.add(evidence_record_id)


@pytest.mark.parametrize("evidence_fixture", ALL_EVIDENCE_FIXTURE_NAMES, indirect=True)
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

    expected_sse_records = build_expected_sse_records(evidence_fixture)
    event_types = [event["type"] for event in events]
    assert event_types == [
        "RUN_STARTED",
        *(["TOOL_COMPLETE"] * len(expected_sse_records)),
        "TOOL_COMPLETE",
        "evidence_summary",
        "turn_completed",
    ]

    evidence_summary_event = next(
        event for event in events if event["type"] == "evidence_summary"
    )
    assert evidence_summary_event["session_id"] == session_id
    _assert_frontend_evidence_records_match(
        evidence_summary_event["evidence_records"],
        expected_sse_records,
    )
