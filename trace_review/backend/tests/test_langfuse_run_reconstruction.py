from src.services.langfuse_run_reconstruction import (
    build_cost_summary,
    build_duplicate_report,
    build_ordered_reconstruction,
    build_payload_inventory,
    build_trace_tree,
    find_payload,
    paginate_payloads,
)


def _trace_data():
    repeated = {"question": "Which payload got large?"}
    return {
        "raw_trace": {
            "id": "trace-123",
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
                "id": "tool-1",
                "type": "SPAN",
                "name": "tool call fetch_entities",
                "parentObservationId": "agent-1",
                "startTime": "2026-06-06T03:00:03Z",
                "metadata": {
                    "tool_name": "fetch_entities",
                    "event_payload": {"event_type": "tool_call.completed", "sequence": 3},
                },
                "input": {"query": "genes"},
                "output": {"rows": [1, 2]},
            },
        ],
        "scores": [],
        "metadata": {},
    }


def test_trace_tree_nests_observations_under_parents():
    tree = build_trace_tree(_trace_data())

    assert tree["id"] == "trace-123"
    assert [child["id"] for child in tree["children"]] == ["agent-1"]
    assert [child["id"] for child in tree["children"][0]["children"]] == ["gen-1", "tool-1"]
    assert tree["children"][0]["kind"] == "agent"
    assert tree["children"][0]["children"][0]["kind"] == "model"
    assert tree["children"][0]["children"][1]["kind"] == "tool"


def test_ordered_reconstruction_preserves_event_order_and_payload_refs():
    reconstruction = build_ordered_reconstruction(_trace_data())

    assert reconstruction["trace"]["session_id"] == "session-1"
    assert [event["kind"] for event in reconstruction["events"]] == [
        "trace_input",
        "agent",
        "model",
        "tool",
        "trace_output",
    ]
    model_event = reconstruction["events"][2]
    assert model_event["model"] == "gpt-5-mini"
    assert {payload["field"] for payload in model_event["payloads"]} == {"input", "output"}


def test_payload_inventory_and_exact_payload_chunks_are_langfuse_payloads():
    payloads = build_payload_inventory(_trace_data())
    payload_ids = {payload["payload_id"] for payload in payloads}

    assert "trace:trace-123:input" in payload_ids
    assert "observation:tool-1:output" in payload_ids
    assert "observation:agent-1:metadata.agent_config" in payload_ids
    assert "observation:tool-1:metadata.event_payload" in payload_ids

    payload = find_payload(
        _trace_data(),
        payload_id="observation:tool-1:metadata.event_payload",
        start=0,
        max_chars=8,
    )

    assert payload is not None
    assert payload["scope"] == "observation"
    assert payload["observation_id"] == "tool-1"
    assert payload["field"] == "metadata.event_payload"
    assert payload["serialized"] == '{"event_'
    assert payload["truncated"] is True
    assert payload["next_start"] == 8


def test_payload_pagination_sorts_largest_first():
    payloads = build_payload_inventory(_trace_data())
    page, pagination = paginate_payloads(payloads, limit=2, offset=0, sort="largest")

    assert len(page) == 2
    assert page[0]["byte_count"] >= page[1]["byte_count"]
    assert pagination["has_next"] is True


def test_duplicate_report_groups_repeated_payload_fingerprints():
    report = build_duplicate_report(_trace_data())

    assert report["duplicate_group_count"] == 1
    duplicate = report["duplicates"][0]
    assert duplicate["count"] == 2
    assert {item["payload_id"] for item in duplicate["payloads"]} == {
        "trace:trace-123:input",
        "observation:agent-1:input",
    }


def test_cost_summary_rolls_up_tokens_and_costs():
    summary = build_cost_summary(_trace_data())

    assert summary["totals"]["total_tokens"] == 15
    assert summary["totals"]["total_cost"] == 0.03
    assert summary["by_model"]["gpt-5-mini"]["total_tokens"] == 15
    assert summary["by_kind"]["model"]["observation_count"] == 1
