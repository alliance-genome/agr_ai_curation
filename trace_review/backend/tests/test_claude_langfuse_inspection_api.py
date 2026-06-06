from unittest.mock import Mock, patch

import pytest
from fastapi import HTTPException

from src.api import claude


def _trace_data():
    repeated = {"question": "Which payload got large?"}
    return {
        "raw_trace": {
            "id": "856df16f1752cb53ee43dcb2f5ecfd16",
            "name": "AI Curation chat",
            "timestamp": "2026-06-06T03:00:00Z",
            "sessionId": "session-1",
            "userId": "user-1",
            "metadata": {"document_id": "doc-1", "run_id": "run-1"},
            "input": repeated,
            "output": {"answer": "Done"},
        },
        "observations": [
            {
                "id": "agent-1",
                "type": "SPAN",
                "name": "Supervisor agent",
                "startTime": "2026-06-06T03:00:01Z",
                "metadata": {
                    "agent_name": "supervisor",
                    "agent_config": {"agent_name": "Supervisor", "tools": ["fetch_entities"]},
                },
                "input": repeated,
                "output": {"next": "tool-1"},
            },
            {
                "id": "gen-1",
                "type": "GENERATION",
                "name": "OpenAI response",
                "parentObservationId": "agent-1",
                "startTime": "2026-06-06T03:00:02Z",
                "providedModelName": "gpt-5-mini",
                "input": "prompt text",
                "output": "model answer",
                "usage": {"input": 10, "output": 5, "total": 15},
                "calculatedTotalCost": 0.03,
            },
            {
                "id": "event-1",
                "type": "EVENT",
                "name": "extraction_trace_event",
                "startTime": "2026-06-06T03:00:03Z",
                "metadata": {
                    "event_payload": {
                        "event_type": "runtime.provider_context_preflight",
                        "input_summary": {
                            "preview": {
                                "surface": "validator",
                                "operation": "domain_validator_batch",
                                "provider": "openai",
                                "model": "gpt-5.5",
                                "payload_summary": {
                                    "json_chars": 1200,
                                    "estimated_tokens": 300,
                                    "threshold": None,
                                    "largest_paths": [
                                        {"path": "requests", "json_chars": 900}
                                    ],
                                },
                            }
                        },
                    }
                },
            },
        ],
        "scores": [],
        "metadata": {},
    }


@pytest.mark.asyncio
@patch("src.api.claude.TraceExtractor")
async def test_claude_search_traces_requires_scope_and_returns_references(extractor_cls: Mock):
    with pytest.raises(HTTPException) as exc_info:
        await claude.search_traces(
            source="local",
            session_id=None,
            user_id=None,
            name=None,
            document_id=None,
            run_id=None,
            extraction_id=None,
            from_timestamp=None,
            to_timestamp=None,
            limit=25,
        )
    assert exc_info.value.status_code == 400

    extractor = extractor_cls.return_value
    extractor.list_traces.return_value = {
        "traces": [
            {
                "id": "856df16f1752cb53ee43dcb2f5ecfd16",
                "name": "AI Curation chat",
                "timestamp": "2026-06-06T03:00:00Z",
                "sessionId": "session-1",
                "userId": "user-1",
                "totalCost": 0.03,
            }
        ],
        "query": {"session_id": "session-1"},
        "meta": {"page": 1, "limit": 25, "totalItems": 1},
    }

    response = await claude.search_traces(
        source="local",
        session_id="session-1",
        user_id=None,
        name=None,
        document_id=None,
        run_id=None,
        extraction_id=None,
        from_timestamp=None,
        to_timestamp=None,
        limit=25,
    )

    assert response.status == "success"
    assert response.data["trace_count"] == 1
    assert response.data["traces"][0]["trace_id_short"] == "856df16f"
    extractor.list_traces.assert_called_once()


@pytest.mark.asyncio
@patch("src.api.claude.TraceExtractor")
async def test_claude_langfuse_reconstruction_is_event_paginated(extractor_cls: Mock):
    extractor_cls.return_value.extract_complete_trace.return_value = _trace_data()

    response = await claude.get_langfuse_reconstruction(
        "856df16f1752cb53ee43dcb2f5ecfd16",
        source="local",
        include_payloads=False,
        limit=2,
        offset=1,
    )

    assert response.status == "success"
    assert response.data["event_count"] == 5
    assert len(response.data["events"]) == 2
    assert response.data["events"][0]["event_id"] == "agent-1"
    assert response.data["pagination"] == {
        "limit": 2,
        "offset": 1,
        "total_items": 5,
        "has_next": True,
        "next_offset": 3,
    }


@pytest.mark.asyncio
@patch("src.api.claude.TraceExtractor")
async def test_claude_langfuse_payload_inventory_and_exact_chunk(extractor_cls: Mock):
    extractor_cls.return_value.extract_complete_trace.return_value = _trace_data()

    inventory = await claude.get_langfuse_payloads(
        "856df16f1752cb53ee43dcb2f5ecfd16",
        source="local",
        include_values=False,
        sort="chronological",
        limit=10,
        offset=0,
    )

    payload_ids = {payload["payload_id"] for payload in inventory.data["payloads"]}
    assert "trace:856df16f1752cb53ee43dcb2f5ecfd16:input" in payload_ids
    assert "observation:agent-1:metadata.agent_config" in payload_ids

    exact = await claude.get_langfuse_payload(
        "856df16f1752cb53ee43dcb2f5ecfd16",
        source="local",
        payload_id="observation:agent-1:metadata.agent_config",
        scope=None,
        observation_id=None,
        field=None,
        start=0,
        max_chars=8,
    )

    assert exact.status == "success"
    assert exact.data["payload"]["serialized"] == '{"agent_'
    assert exact.data["payload"]["truncated"] is True
    assert exact.data["payload"]["next_start"] == 8


@pytest.mark.asyncio
@patch("src.api.claude.TraceExtractor")
async def test_claude_langfuse_costs_and_duplicates(extractor_cls: Mock):
    extractor_cls.return_value.extract_complete_trace.return_value = _trace_data()

    costs = await claude.get_langfuse_costs("856df16f1752cb53ee43dcb2f5ecfd16", source="local")
    duplicates = await claude.get_langfuse_duplicates("856df16f1752cb53ee43dcb2f5ecfd16", source="local")

    assert costs.data["costs"]["totals"]["total_tokens"] == 15
    assert duplicates.data["duplicates"]["duplicate_group_count"] == 1


@pytest.mark.asyncio
@patch("src.api.claude.TraceExtractor")
async def test_claude_model_live_context_uses_preflight_and_generation_inputs(extractor_cls: Mock):
    extractor_cls.return_value.extract_complete_trace.return_value = _trace_data()

    response = await claude.get_model_live_context(
        "856df16f1752cb53ee43dcb2f5ecfd16",
        source="local",
    )

    assert response.status == "success"
    model_live = response.data["model_live_context"]
    assert model_live["observed_call_record_count"] == 2
    assert model_live["classification"]["preflight_event_count"] == 1
    assert model_live["classification"]["inferred_generation_count"] == 1
    assert model_live["classification"]["historical_precision"] == (
        "mixed_explicit_and_inferred"
    )
    assert model_live["classification"]["possible_double_count"] is True
    assert model_live["totals_by_classification"]["provider_context_preflight"] == {
        "call_count": 1,
        "total_input_json_chars": 1200,
        "total_estimated_input_tokens": 300,
    }
    assert model_live["calls"][0]["classification_source"] == "inferred_generation_input"
    assert model_live["calls"][1]["classification_source"] == "provider_context_preflight"
    assert model_live["calls"][1]["largest_paths"][0]["path"] == "requests"
    assert response.data["observability_payloads"]["exact_payload_requires_explicit_lookup"] is True
