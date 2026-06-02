"""Integration tests for flow lifecycle and execute-flow SSE behavior.

These tests exercise a larger-scope path:
1) create flow via API
2) update/save flow via API
3) execute flow via SSE endpoint
4) validate persisted execution stats + streamed events
"""

from __future__ import annotations

import json
import os
import sys
from contextlib import contextmanager
from uuid import uuid4
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


def _flow_definition(*, agent_id: str, agent_display_name: str, output_key: str = "final_output") -> dict:
    return {
        "version": "1.0",
        "entry_node_id": "task_input_1",
        "nodes": [
            {
                "id": "task_input_1",
                "type": "task_input",
                "position": {"x": 0, "y": 0},
                "data": {
                    "agent_id": "task_input",
                    "agent_display_name": "Initial Instructions",
                    "task_instructions": "Extract key curation-ready findings.",
                    "output_key": "task_input_text",
                    "input_source": "user_query",
                },
            },
            {
                "id": "agent_1",
                "type": "agent",
                "position": {"x": 260, "y": 0},
                "data": {
                    "agent_id": agent_id,
                    "agent_display_name": agent_display_name,
                    "output_key": output_key,
                    "input_source": "user_query",
                },
            },
        ],
        "edges": [
            {"id": "edge_1", "source": "task_input_1", "target": "agent_1"},
        ],
    }


def _sse_events(response) -> list[dict]:
    events: list[dict] = []
    for line in response.iter_lines():
        if not line:
            continue
        if line.startswith("data: "):
            event = json.loads(line[6:])
            events.append(event)
            if event.get("type") in {"FLOW_FINISHED", "RUN_ERROR"}:
                break
    return events


def _mark_fake_streaming_tool_complete(agent, *, output: str = "done") -> None:
    """Mirror the flow tool wrapper state update when runner calls are mocked.

    The production streaming tool wrapper records completed_steps before the
    output agent emits CHAT_OUTPUT_READY. These integration tests patch the
    runner itself, so the wrapper never executes unless we mark the state here.
    Keep this helper close to the fake streams so future test edits do not
    accidentally bypass the executor's incomplete-flow guard.
    """
    execution_state = getattr(agent, "_flow_execution_state", None)
    if not isinstance(execution_state, dict):
        return
    ordered_tool_names = execution_state.get("ordered_tool_names") or []
    next_tool_index = int(execution_state.get("next_tool_index") or 0)
    if next_tool_index >= len(ordered_tool_names):
        return
    tool_name = ordered_tool_names[next_tool_index]
    execution_state.setdefault("completed_steps", []).append(
        {
            "step": next_tool_index + 1,
            "agent_id": "chat_output",
            "agent_name": "Chat Output Agent",
            "tool_name": tool_name,
            "output": output,
            "output_preview": output,
            "evidence_records": [],
            "evidence_count": 0,
        }
    )
    execution_state["next_tool_index"] = next_tool_index + 1


@contextmanager
def _patched_flow_runner(run_agent_streamed):
    with patch("src.api.chat_execute_flow.run_agent_streamed", run_agent_streamed), \
         patch("src.api.chat_common.run_agent_streamed", run_agent_streamed), \
         patch("src.lib.openai_agents.runner.run_agent_streamed", run_agent_streamed):
        yield


@pytest.fixture
def client(test_db, get_auth_mock, monkeypatch):
    """Create isolated app client with explicit auth + DB dependency overrides."""
    monkeypatch.setenv("OPENAI_API_KEY", os.getenv("OPENAI_API_KEY", "test-key"))
    monkeypatch.setenv("GROQ_API_KEY", os.getenv("GROQ_API_KEY", "test-key"))
    monkeypatch.setenv("LLM_PROVIDER_STRICT_MODE", "false")

    get_auth_mock.set_user("valid_user")

    from main import create_app
    from src.api.auth import _get_user_from_cookie_impl
    from src.lib.config.prompt_loader import load_prompts
    from src.lib.agent_studio.system_agent_sync import sync_system_agents
    from src.lib.prompts import cache as prompt_cache
    from src.models.sql.agent import Agent as UnifiedAgent
    from src.models.sql.agent import Project
    from src.models.sql.agent import ProjectMember
    from src.models.sql.chat_message import ChatMessage
    from src.models.sql.chat_session import ChatSession
    from src.models.sql.curation_flow import CurationFlow
    from src.models.sql.database import Base
    from src.models.sql.database import get_db
    from src.models.sql.prompts import PromptExecutionLog, PromptTemplate
    from src.models.sql.user import User

    # Flow execution now resolves every step through the unified agents table
    # before the runner is invoked. Seed system agents in this isolated DB.
    Base.metadata.create_all(
        bind=test_db.get_bind(),
        tables=[
            User.__table__,
            Project.__table__,
            ProjectMember.__table__,
            UnifiedAgent.__table__,
            CurationFlow.__table__,
            ChatSession.__table__,
            ChatMessage.__table__,
            PromptTemplate.__table__,
            PromptExecutionLog.__table__,
        ],
    )
    load_prompts(db=test_db, force_reload=True)
    prompt_cache.initialize(test_db)
    sync_system_agents(test_db, force_reload=True)
    test_db.commit()

    app = create_app()

    def override_get_db():
        yield test_db

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[_get_user_from_cookie_impl] = get_auth_mock.get_user
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_flow_lifecycle_create_update_execute_stream_and_stats(client: TestClient):
    flow_name = f"it-flow-{uuid4().hex[:12]}"

    create_payload = {
        "name": flow_name,
        "description": "integration lifecycle test",
        "flow_definition": _flow_definition(
            agent_id="chat_output",
            agent_display_name="Chat Output Agent",
        ),
    }
    create_resp = client.post("/api/flows", json=create_payload)
    assert create_resp.status_code == 201, create_resp.text
    created = create_resp.json()
    flow_id = created["id"]
    assert created["execution_count"] == 0

    update_payload = {
        "description": "integration lifecycle test (updated)",
        "flow_definition": _flow_definition(
            agent_id="chat_output",
            agent_display_name="Chat Output Agent",
            output_key="final_output_updated",
        ),
    }
    update_resp = client.put(f"/api/flows/{flow_id}", json=update_payload)
    assert update_resp.status_code == 200, update_resp.text
    updated = update_resp.json()
    assert updated["description"] == "integration lifecycle test (updated)"
    assert updated["flow_definition"]["nodes"][1]["data"]["output_key"] == "final_output_updated"

    async def _fake_run_agent_streamed(**kwargs):
        yield {"type": "RUN_STARTED", "data": {"trace_id": "trace-it-flow"}}
        _mark_fake_streaming_tool_complete(kwargs.get("agent"), output="done")
        yield {"type": "CHAT_OUTPUT_READY", "data": {"response": "done"}}

    execute_payload = {
        "flow_id": flow_id,
        "session_id": f"session-{uuid4().hex[:10]}",
        "user_query": "Run this flow end-to-end",
    }
    with _patched_flow_runner(_fake_run_agent_streamed):
        with client.stream("POST", "/api/chat/execute-flow", json=execute_payload) as stream_resp:
            events = _sse_events(stream_resp)
            assert stream_resp.status_code == 200

    event_types = [event.get("type") for event in events]
    assert "FLOW_STARTED" in event_types
    assert "RUN_STARTED" in event_types
    assert "CHAT_OUTPUT_READY" in event_types
    assert "FLOW_FINISHED" in event_types

    flow_started = next(event for event in events if event.get("type") == "FLOW_STARTED")
    assert flow_started.get("flow_id") == flow_id
    assert flow_started.get("session_id") == execute_payload["session_id"]

    flow_finished = next(event for event in events if event.get("type") == "FLOW_FINISHED")
    assert flow_finished.get("status") == "completed"

    get_resp = client.get(f"/api/flows/{flow_id}")
    assert get_resp.status_code == 200, get_resp.text
    fetched = get_resp.json()
    assert fetched["execution_count"] >= 1
    assert fetched["last_executed_at"] is not None


def test_execute_flow_persists_durable_history_and_replays_completed_turn(client: TestClient):
    flow_name = f"it-flow-replay-{uuid4().hex[:12]}"

    create_resp = client.post(
        "/api/flows",
        json={
            "name": flow_name,
            "description": "integration durable replay test",
            "flow_definition": _flow_definition(
                agent_id="chat_output",
                agent_display_name="Chat Output Agent",
            ),
        },
    )
    assert create_resp.status_code == 201, create_resp.text
    flow_id = create_resp.json()["id"]

    session_id = f"session-{uuid4().hex[:10]}"
    turn_id = f"turn-{uuid4().hex[:10]}"
    runner_calls = 0

    async def _fake_run_agent_streamed(**kwargs):
        nonlocal runner_calls
        runner_calls += 1
        yield {"type": "RUN_STARTED", "data": {"trace_id": "trace-it-flow-replay"}}
        _mark_fake_streaming_tool_complete(
            kwargs.get("agent"),
            output="Selected TP53 for highest evidence confidence.",
        )
        yield {
            "type": "CHAT_OUTPUT_READY",
            "timestamp": "2026-02-26T00:00:03+00:00",
            "details": {"output": "Selected TP53 for highest evidence confidence."},
        }

    execute_payload = {
        "flow_id": flow_id,
        "session_id": session_id,
        "turn_id": turn_id,
        "user_query": "Run this flow end-to-end",
    }

    with _patched_flow_runner(_fake_run_agent_streamed):
        with client.stream("POST", "/api/chat/execute-flow", json=execute_payload) as stream_resp:
            events = _sse_events(stream_resp)
            assert stream_resp.status_code == 200

    assert runner_calls == 1
    assert [event.get("type") for event in events] == [
        "FLOW_STARTED",
        "RUN_STARTED",
        "CHAT_OUTPUT_READY",
        "FLOW_FINISHED",
    ]
    assert all(event.get("turn_id") == turn_id for event in events)

    history_resp = client.get(f"/api/chat/history/{session_id}")
    assert history_resp.status_code == 200, history_resp.text
    history_payload = history_resp.json()
    assert [(message["role"], message["message_type"], message["turn_id"]) for message in history_payload["messages"]] == [
        ("user", "text", turn_id),
        ("flow", "flow_summary", turn_id),
    ]
    assert history_payload["messages"][1]["content"] == "Selected TP53 for highest evidence confidence."
    assert "_assistant_message" not in (history_payload["messages"][1]["payload_json"] or {})

    async def _unexpected_run_agent_streamed(**_kwargs):
        raise AssertionError("run_agent_streamed should not run for a completed replayed flow turn")
        yield  # pragma: no cover

    with _patched_flow_runner(_unexpected_run_agent_streamed):
        with client.stream("POST", "/api/chat/execute-flow", json=execute_payload) as stream_resp:
            replay_events = _sse_events(stream_resp)
            assert stream_resp.status_code == 200

    assert [event.get("type") for event in replay_events] == [
        "RUN_STARTED",
        "CHAT_OUTPUT_READY",
        "FLOW_FINISHED",
    ]
    assert all(event.get("turn_id") == turn_id for event in replay_events)
    assert replay_events[0]["trace_id"] == "trace-it-flow-replay"

    fetched = client.get(f"/api/flows/{flow_id}").json()
    assert fetched["execution_count"] == 1


def test_execute_flow_emits_stream_error_for_unresolvable_agent(client: TestClient):
    flow_name = f"it-fail-flow-{uuid4().hex[:12]}"
    create_payload = {
        "name": flow_name,
        "description": "integration failure path",
        "flow_definition": _flow_definition(
            agent_id="unknown_agent_for_failure_path",
            agent_display_name="Unknown Agent",
        ),
    }
    create_resp = client.post("/api/flows", json=create_payload)
    assert create_resp.status_code == 201, create_resp.text
    flow_id = create_resp.json()["id"]

    execute_payload = {
        "flow_id": flow_id,
        "session_id": f"session-{uuid4().hex[:10]}",
        "user_query": "Run flow with invalid agent",
    }
    with client.stream("POST", "/api/chat/execute-flow", json=execute_payload) as stream_resp:
        events = _sse_events(stream_resp)
        assert stream_resp.status_code == 200

    event_types = [event.get("type") for event in events]
    assert "SUPERVISOR_ERROR" in event_types
    assert "RUN_ERROR" in event_types

    run_error = next(event for event in events if event.get("type") == "RUN_ERROR")
    run_error_text = (run_error.get("message") or "").lower()
    assert "no agent tools" in run_error_text or "could be created" in run_error_text
