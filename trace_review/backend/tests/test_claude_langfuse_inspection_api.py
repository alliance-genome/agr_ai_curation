import json
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
                                    "estimated_tokens": "<redacted>",
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
            offset=0,
            limit=25,
            item_start=0,
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
        "total_items": 1,
        "source_exhausted": True,
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
        offset=0,
        limit=25,
        item_start=0,
    )

    assert response.status == "success"
    assert response.data["trace_count"] == 1
    assert response.data["traces"][0]["trace_id_short"] == "856df16f"
    extractor.list_traces.assert_called_once()


def _large_search_references(count: int, *, oversized: bool = False) -> list[dict]:
    references = []
    for index in range(count):
        width = 15_000 if oversized and index == 0 else 300
        references.append({
            "id": f"{index:032x}",
            "name": f"trace-{index}-" + "n" * width,
            "timestamp": f"2026-06-06T03:{index:02d}:00Z",
            "sessionId": "session-" + "s" * width,
            "userId": "user-" + "u" * width,
            "environment": "env-" + "e" * width,
            "tags": ["tag-" + "t" * width],
            "htmlPath": "/trace/" + "h" * width,
        })
    return references


def _search_listing(records: list[dict], kwargs: dict) -> dict:
    offset = kwargs["offset"]
    limit = kwargs["limit"]
    query = {
        key: value.isoformat() if hasattr(value, "isoformat") else value
        for key, value in kwargs.items()
        if key not in {"offset", "limit"}
    }
    query.update({"offset": offset, "limit": limit})
    return {
        "traces": records[offset:offset + limit],
        "query": query,
        "meta": {"totalItems": len(records)},
        "total_items": len(records),
        "source_exhausted": offset + limit >= len(records),
    }


@pytest.mark.asyncio
@patch("src.api.claude.TraceExtractor")
@pytest.mark.parametrize("record_count", [25, 100])
async def test_search_traces_fits_full_envelopes_and_replays_every_filter(
    extractor_cls: Mock,
    record_count: int,
):
    records = _large_search_references(record_count)
    extractor_cls.return_value.list_traces.side_effect = (
        lambda **kwargs: _search_listing(records, kwargs)
    )
    arguments = {
        "source": "local",
        "session_id": "session-filter",
        "user_id": "user-filter",
        "name": "trace-filter",
        "document_id": "document-filter",
        "run_id": "run-filter",
        "extraction_id": "extraction-filter",
        "from_timestamp": "2026-06-01T00:00:00Z",
        "to_timestamp": "2026-06-30T00:00:00Z",
        "offset": 0,
        "limit": record_count,
        "item_start": 0,
    }
    reconstructed = []
    while True:
        response = await claude.search_traces(**arguments)
        provider_result = {
            "status": "success",
            "data": response.data,
            "token_info": response.token_info.model_dump(),
            "error": None,
        }
        assert len(json.dumps(provider_result, default=str)) <= 12_000
        assert response.token_info.within_budget is True
        reconstructed.extend(response.data["traces"])
        next_call = response.data["pagination"]["next_call"]
        if next_call is None:
            break
        for key in (
            "session_id", "user_id", "name", "document_id", "run_id",
            "extraction_id", "from_timestamp", "to_timestamp",
        ):
            expected = (
                arguments[key].replace("Z", "+00:00")
                if key.endswith("timestamp")
                else arguments[key]
            )
            assert next_call[key] == expected
        arguments = {"source": "local", **next_call}

    assert [record["trace_id"] for record in reconstructed] == [
        record["id"] for record in records
    ]
    assert response.data["pagination"]["complete"] is True
    assert response.data["total_items"] == record_count


@pytest.mark.asyncio
@patch("src.api.claude.TraceExtractor")
async def test_search_traces_oversized_reference_reconstructs_with_forward_progress(extractor_cls: Mock):
    records = _large_search_references(1, oversized=True)
    extractor_cls.return_value.list_traces.side_effect = (
        lambda **kwargs: _search_listing(records, kwargs)
    )
    arguments = {
        "source": "local",
        "session_id": "session-filter",
        "user_id": None,
        "name": None,
        "document_id": None,
        "run_id": None,
        "extraction_id": None,
        "from_timestamp": None,
        "to_timestamp": None,
        "offset": 0,
        "limit": 1,
        "item_start": 0,
    }
    content = []
    starts = []
    while True:
        response = await claude.search_traces(**arguments)
        assert len(json.dumps({
            "status": "success",
            "data": response.data,
            "token_info": response.token_info.model_dump(),
            "error": None,
        }, default=str)) <= 12_000
        chunk = response.data["trace_chunk"]
        starts.append(chunk["start"])
        content.append(chunk["content"])
        assert chunk["end"] > chunk["start"]
        next_call = response.data["pagination"]["next_call"]
        if next_call is None:
            break
        arguments = {
            "source": "local",
            "session_id": None,
            "user_id": None,
            "name": None,
            "document_id": None,
            "run_id": None,
            "extraction_id": None,
            "from_timestamp": None,
            "to_timestamp": None,
            **next_call,
        }

    expected = json.dumps(
        claude._listed_trace_reference(records[0]),
        default=str,
        sort_keys=True,
        separators=(",", ":"),
    )
    assert "".join(content) == expected
    assert starts == sorted(starts)
    assert response.data["pagination"]["complete"] is True


@pytest.mark.asyncio
@patch("src.api.claude.TraceExtractor")
async def test_claude_langfuse_reconstruction_is_event_paginated(extractor_cls: Mock):
    extractor_cls.return_value.extract_complete_trace.return_value = _trace_data()

    response = await claude.get_langfuse_reconstruction(
        "856df16f1752cb53ee43dcb2f5ecfd16",
        source="local",
        limit=2,
        offset=1,
        section="events",
    )

    assert response.status == "success"
    assert response.data["summary"]["event_count"] == 5
    assert len(response.data["page"]["items"]) == 2
    assert response.data["page"]["items"][0]["event_id"] == "agent-1"
    assert response.data["page"]["complete"] is False
    assert response.data["page"]["next_call"]["offset"] == 3


@pytest.mark.asyncio
@patch("src.api.claude.TraceExtractor")
async def test_claude_langfuse_payload_inventory_and_exact_chunk(extractor_cls: Mock):
    extractor_cls.return_value.extract_complete_trace.return_value = _trace_data()

    inventory = await claude.get_langfuse_payloads(
        "856df16f1752cb53ee43dcb2f5ecfd16",
        source="local",
        sort="chronological",
        limit=10,
        offset=0,
        section="payloads",
    )

    payload_ids = {payload["payload_id"] for payload in inventory.data["page"]["items"]}
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

    costs = await claude.get_langfuse_costs("856df16f1752cb53ee43dcb2f5ecfd16", source="local", section="observations")
    duplicates = await claude.get_langfuse_duplicates("856df16f1752cb53ee43dcb2f5ecfd16", source="local", section="duplicate_groups")

    assert costs.data["summary"]["totals"]["total_tokens"] == 15
    assert duplicates.data["summary"]["duplicate_group_count"] == 1
    assert duplicates.data["page"]["complete"] is True


@pytest.mark.asyncio
@patch("src.api.claude.TraceExtractor")
async def test_claude_model_live_context_uses_preflight_and_generation_inputs(extractor_cls: Mock):
    extractor_cls.return_value.extract_complete_trace.return_value = _trace_data()

    response = await claude.get_model_live_context(
        "856df16f1752cb53ee43dcb2f5ecfd16",
        source="local",
    )

    assert response.status == "success"
    model_live = response.data["summary"]
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
    assert model_live["totals_by_classification"]["inferred_generation_input"] == {
        "call_count": 1,
        "total_input_json_chars": 11,
        "total_estimated_input_tokens": 3,
    }
    assert response.data["page"] is None
    assert model_live["observability_payloads"]["exact_payload_requires_explicit_lookup"] is True
