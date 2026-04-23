"""Contract coverage for Agent Studio chat history tool registration."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

from src.api import agent_studio as api_module
from src.lib.chat_history_repository import AGENT_STUDIO_CHAT_KIND, ALL_CHAT_KINDS_SENTINEL


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
        self._captured["tools"] = kwargs["tools"]
        return _FakeSuccessfulStream(
            events=[
                SimpleNamespace(
                    type="content_block_delta",
                    delta=SimpleNamespace(text="History tools ready"),
                )
            ],
            final_message=SimpleNamespace(
                content=[SimpleNamespace(type="text", text="History tools ready")],
                stop_reason="end_turn",
            ),
        )


class _FakeAnthropicClient:
    def __init__(self, captured: dict[str, object]):
        self.beta = SimpleNamespace(messages=_FakeMessagesApi(captured))


def test_agent_studio_chat_endpoint_registers_chat_history_tools_on_the_wire(
    contract_client,
    chat_contract_auth_headers,
    monkeypatch,
):
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
        lambda _db, _user: SimpleNamespace(id=1),
    )

    def _fake_get_db():
        yield SimpleNamespace(close=lambda: None)

    monkeypatch.setattr(api_module, "get_db", _fake_get_db)
    monkeypatch.setattr(
        api_module,
        "_prepare_agent_studio_turn",
        lambda *, request, **_kwargs: api_module.PreparedAgentStudioTurn(
            session_id="agent-studio-session-1",
            turn_id="opus-turn-1",
            user_message=request.messages[-1].content,
            requested_context_session_id=request.context.session_id if request.context else None,
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
            created_at=datetime(2026, 4, 23, 4, 30, tzinfo=timezone.utc),
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
            "messages": [{"role": "user", "content": "Show me my recent sessions"}],
            "context": {"active_tab": "agents"},
        },
    ) as response:
        events = _consume_sse_events(response)

    assert response.status_code == 200, response.text
    assert [event["type"] for event in events] == ["TEXT_DELTA", "DONE"]

    tools_by_name = {tool["name"]: tool for tool in captured["tools"]}
    assert {"list_recent_chats", "search_chat_history", "get_chat_conversation"} <= set(
        tools_by_name
    )
    assert tools_by_name["list_recent_chats"]["input_schema"]["required"] == ["chat_kind"]
    assert tools_by_name["list_recent_chats"]["input_schema"]["properties"]["chat_kind"]["enum"] == [
        "assistant_chat",
        "agent_studio",
        ALL_CHAT_KINDS_SENTINEL,
    ]
    assert tools_by_name["search_chat_history"]["input_schema"]["required"] == [
        "query",
        "chat_kind",
    ]
    assert tools_by_name["get_chat_conversation"]["input_schema"]["required"] == ["session_id"]
