"""Tests for Agent Workshop prompt refresh behavior in Agent Studio chat."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

import src.api.agent_studio as api_module
from src.lib.agent_studio.models import AgentWorkshopContext, ChatContext
from src.lib.chat_history_repository import AGENT_STUDIO_CHAT_KIND


@pytest.fixture
def contract_client(monkeypatch):
    """Create a test client with deterministic API-key auth for the chat endpoint."""

    monkeypatch.setenv("DEV_MODE", "false")
    monkeypatch.setenv("TESTING_API_KEY", "contract-test-key")
    from fastapi.testclient import TestClient
    from main import app

    app.dependency_overrides.clear()
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def chat_contract_auth_headers():
    return {"X-API-Key": "contract-test-key"}


def _consume_sse_events(stream_response) -> list[dict]:
    events: list[dict] = []
    for line in stream_response.iter_lines():
        if not line.startswith("data: "):
            continue
        events.append(json.loads(line[6:]))
    return events


class _FakeSuccessfulStream:
    def __init__(self, events: list[object], final_message: object):
        self._events = list(events)
        self._final_message = final_message

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._events:
            raise StopAsyncIteration
        return self._events.pop(0)

    async def get_final_message(self):
        return self._final_message


class _FakeMessagesApi:
    def __init__(self, captured: dict[str, object]):
        self._captured = captured

    def stream(self, **kwargs):
        api_calls = self._captured.setdefault("api_calls", [])
        api_calls.append(kwargs)
        if len(api_calls) == 1:
            return _FakeSuccessfulStream(
                events=[],
                final_message=SimpleNamespace(
                    content=[
                        SimpleNamespace(
                            type="tool_use",
                            id="toolu_refresh_1",
                            name="refresh_workshop_prompt",
                            input={"target_prompt": "main"},
                        )
                    ],
                    stop_reason="tool_use",
                ),
            )

        self._captured["second_call_messages"] = kwargs["messages"]
        return _FakeSuccessfulStream(
            events=[
                SimpleNamespace(
                    type="content_block_delta",
                    delta=SimpleNamespace(text="The refreshed prompt no longer contains that typo."),
                )
            ],
            final_message=SimpleNamespace(
                content=[
                    SimpleNamespace(
                        type="text",
                        text="The refreshed prompt no longer contains that typo.",
                    )
                ],
                stop_reason="end_turn",
            ),
        )


class _FakeAnthropicClient:
    def __init__(self, captured: dict[str, object]):
        self.beta = SimpleNamespace(messages=_FakeMessagesApi(captured))


def test_workshop_refresh_tool_is_agent_workshop_scoped():
    workshop_context = ChatContext(
        active_tab="agent_workshop",
        agent_workshop=AgentWorkshopContext(prompt_draft="Review me"),
    )
    tools_by_name = {
        tool["name"]: tool
        for tool in api_module._get_all_opus_tools(workshop_context)
    }

    assert "refresh_workshop_prompt" in api_module._WORKSHOP_TOOLS
    assert "refresh_workshop_prompt" in tools_by_name
    assert tools_by_name["refresh_workshop_prompt"]["input_schema"]["required"] == []

    agents_tools = {
        tool["name"]
        for tool in api_module._get_all_opus_tools(ChatContext(active_tab="agents"))
    }
    assert "refresh_workshop_prompt" not in agents_tools


def test_workshop_prompt_refresh_is_forced_only_for_prompt_sensitive_turns():
    context = ChatContext(
        active_tab="agent_workshop",
        agent_workshop=AgentWorkshopContext(prompt_draft="Current draft"),
    )

    assert api_module._should_force_workshop_prompt_refresh(
        context=context,
        latest_user_message="Review main prompt",
    )
    assert api_module._should_force_workshop_prompt_refresh(
        context=context,
        latest_user_message="what do you think now?",
    )
    assert api_module._should_force_workshop_prompt_refresh(
        context=context,
        latest_user_message="Does it still mention minerite?",
    )
    assert not api_module._should_force_workshop_prompt_refresh(
        context=context,
        latest_user_message="How should I think about model tradeoffs?",
    )
    assert not api_module._should_force_workshop_prompt_refresh(
        context=context,
        latest_user_message="What should I do now about the flow?",
    )
    assert not api_module._should_force_workshop_prompt_refresh(
        context=context,
        latest_user_message="Can you explain minerite?",
    )
    assert not api_module._should_force_workshop_prompt_refresh(
        context=ChatContext(active_tab="agents"),
        latest_user_message="Review main prompt",
    )


@pytest.mark.asyncio
async def test_refresh_workshop_prompt_rejects_invalid_target_prompt():
    result = await api_module._handle_tool_call(
        tool_name="refresh_workshop_prompt",
        tool_input={"target_prompt": "mod"},
        context=ChatContext(
            active_tab="agent_workshop",
            agent_workshop=AgentWorkshopContext(prompt_draft="Current draft"),
        ),
        user_email="curator@example.org",
        user_auth_sub="auth-sub-1",
    )

    assert result == {
        "success": False,
        "error": "Invalid target_prompt: 'mod'. Must be 'main' or 'group'.",
    }


@pytest.mark.asyncio
async def test_refresh_workshop_prompt_rejects_invalid_context_timestamp():
    result = await api_module._handle_tool_call(
        tool_name="refresh_workshop_prompt",
        tool_input={"target_prompt": "main"},
        context=ChatContext(
            active_tab="agent_workshop",
            agent_workshop=AgentWorkshopContext(
                prompt_draft="Current draft",
                custom_agent_updated_at="not-a-date",
            ),
        ),
        user_email="curator@example.org",
        user_auth_sub="auth-sub-1",
    )

    assert result == {
        "success": False,
        "error": "Invalid custom_agent_updated_at value. Expected an ISO 8601 timestamp.",
    }


@pytest.mark.asyncio
async def test_refresh_workshop_prompt_returns_error_when_saved_agent_is_inaccessible(monkeypatch):
    custom_agent_uuid = uuid4()

    monkeypatch.setattr(api_module, "SessionLocal", lambda: SimpleNamespace(close=lambda: None))

    def _raise_access_error(*_args):
        raise api_module.CustomAgentAccessError("permission denied")

    monkeypatch.setattr(api_module, "get_custom_agent_visible_to_user", _raise_access_error)

    result = await api_module._handle_tool_call(
        tool_name="refresh_workshop_prompt",
        tool_input={"target_prompt": "main"},
        context=ChatContext(
            active_tab="agent_workshop",
            agent_workshop=AgentWorkshopContext(
                custom_agent_id=f"ca_{custom_agent_uuid}",
                prompt_draft="Potentially stale draft.",
            ),
        ),
        user_email="curator@example.org",
        user_auth_sub="auth-sub-1",
        user_db_id=7,
    )

    assert result == {
        "success": False,
        "error": f"Could not access custom agent {custom_agent_uuid}.",
    }


@pytest.mark.asyncio
async def test_refresh_workshop_prompt_prefers_newer_saved_custom_agent(monkeypatch):
    custom_agent_uuid = uuid4()
    saved_updated_at = datetime(2026, 5, 6, 14, 15, 20, tzinfo=timezone.utc)
    saved_agent = SimpleNamespace(
        id=custom_agent_uuid,
        custom_prompt="Current prompt with the typo removed.",
        version=3,
        updated_at=saved_updated_at,
    )

    monkeypatch.setattr(api_module, "SessionLocal", lambda: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(api_module, "get_custom_agent_visible_to_user", lambda *_args: saved_agent)

    result = await api_module._handle_tool_call(
        tool_name="refresh_workshop_prompt",
        tool_input={"target_prompt": "main"},
        context=ChatContext(
            active_tab="agent_workshop",
            agent_workshop=AgentWorkshopContext(
                custom_agent_id=f"ca_{custom_agent_uuid}",
                prompt_draft="Older prompt that still says minerite.",
                draft_is_dirty=True,
                custom_agent_updated_at="2026-05-06T14:10:00+00:00",
            ),
        ),
        user_email="curator@example.org",
        user_auth_sub="auth-sub-1",
        messages=[{"role": "assistant", "content": "Earlier review mentioned minerite."}],
        user_db_id=7,
    )

    assert result["success"] is True
    assert result["source"] == "saved_custom_agent"
    assert result["custom_agent_id"] == str(custom_agent_uuid)
    assert result["version"] == 3
    assert result["updated_at"] == saved_updated_at.isoformat()
    assert result["length"] == len("Current prompt with the typo removed.")
    assert result["hash"] == api_module._prompt_hash("Current prompt with the typo removed.")
    assert "minerite" not in result["current_prompt"]


def test_prompt_sensitive_agent_workshop_chat_forces_refresh_before_review(
    contract_client,
    chat_contract_auth_headers,
    monkeypatch,
):
    custom_agent_uuid = uuid4()
    captured: dict[str, object] = {}

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(
        api_module,
        "_resolve_prompt_explorer_model",
        lambda: ("claude-sonnet-test", "Claude Sonnet Test"),
    )
    monkeypatch.setattr(api_module, "_build_opus_system_prompt", lambda **_kwargs: "system prompt")
    monkeypatch.setattr(api_module, "set_workflow_user_context", lambda **_kwargs: None)
    monkeypatch.setattr(api_module, "clear_workflow_user_context", lambda: None)
    monkeypatch.setattr(api_module, "set_current_flow_context", lambda _context: None)
    monkeypatch.setattr(api_module, "clear_current_flow_context", lambda: None)
    monkeypatch.setattr(
        api_module,
        "set_global_user_from_cognito",
        lambda _db, _user: SimpleNamespace(id=7),
    )
    monkeypatch.setattr(api_module, "get_db", lambda: iter([SimpleNamespace(close=lambda: None)]))
    monkeypatch.setattr(api_module, "SessionLocal", lambda: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(
        api_module,
        "get_custom_agent_visible_to_user",
        lambda *_args: SimpleNamespace(
            id=custom_agent_uuid,
            custom_prompt="Current saved prompt with no typo.",
            version=4,
            updated_at=datetime(2026, 5, 6, 14, 15, 20, tzinfo=timezone.utc),
        ),
    )
    monkeypatch.setattr(
        api_module,
        "_prepare_agent_studio_turn",
        lambda *, request, **_kwargs: api_module.PreparedAgentStudioTurn(
            session_id="agent-studio-session-1",
            turn_id="opus-turn-1",
            user_message=request.messages[-1].content,
            requested_context_session_id=None,
            user_turn_created=False,
        ),
    )
    monkeypatch.setattr(
        api_module,
        "_persist_completed_agent_studio_turn",
        lambda **kwargs: api_module.ChatMessageRecord(
            message_id=uuid4(),
            session_id=kwargs["session_id"],
            chat_kind=AGENT_STUDIO_CHAT_KIND,
            turn_id=kwargs["turn_id"],
            role="assistant",
            message_type="text",
            content=kwargs["assistant_message"],
            payload_json=kwargs["payload_json"],
            trace_id=kwargs["trace_id"],
            created_at=datetime(2026, 5, 6, 14, 16, tzinfo=timezone.utc),
        ),
    )
    monkeypatch.setattr(
        api_module.anthropic,
        "AsyncAnthropic",
        lambda api_key: _FakeAnthropicClient(captured),
    )

    with contract_client.stream(
        "POST",
        "/api/agent-studio/chat",
        headers=chat_contract_auth_headers,
        json={
            "messages": [
                {"role": "assistant", "content": "I saw minerite in the earlier draft."},
                {"role": "user", "content": "Did I fix it? Please review the prompt now."},
            ],
            "context": {
                "active_tab": "agent_workshop",
                "agent_workshop": {
                    "custom_agent_id": f"ca_{custom_agent_uuid}",
                    "custom_agent_name": "Debbie test agent",
                    "prompt_draft": "Older context still says minerite.",
                    "draft_is_dirty": True,
                    "custom_agent_updated_at": "2026-05-06T14:10:00+00:00",
                },
            },
        },
    ) as response:
        events = _consume_sse_events(response)

    assert response.status_code == 200, response.text
    preflight_events = [
        event for event in events if event["type"] == "PROVIDER_CONTEXT_PREFLIGHT"
    ]
    assert [event["operation"] for event in preflight_events] == [
        "initial_anthropic_call",
        "tool_loop_continuation",
    ]
    output_events = [
        event for event in events if event["type"] != "PROVIDER_CONTEXT_PREFLIGHT"
    ]

    assert [event["type"] for event in output_events] == [
        "TOOL_USE",
        "TOOL_RESULT",
        "TEXT_DELTA",
        "DONE",
    ]
    first_call = captured["api_calls"][0]
    second_call = captured["api_calls"][1]
    assert first_call["tool_choice"] == {
        "type": "tool",
        "name": "refresh_workshop_prompt",
    }
    assert "tool_choice" not in second_call

    tool_result = output_events[1]["result"]
    assert tool_result["source"] == "saved_custom_agent"
    assert tool_result["current_prompt"] == "Current saved prompt with no typo."
    assert "minerite" not in tool_result["current_prompt"]

    second_messages = captured["second_call_messages"]
    tool_result_message = second_messages[-1]["content"][0]
    assert tool_result_message["type"] == "tool_result"
    assert "Current saved prompt with no typo." in tool_result_message["content"]
    assert "minerite" not in tool_result_message["content"]
