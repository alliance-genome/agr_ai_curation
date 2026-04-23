"""Contract tests for Agent Studio ChatContext session_id support."""

from __future__ import annotations

import json
from types import SimpleNamespace
from uuid import uuid4

from src.api import agent_studio as api_module
from src.lib.chat_history_repository import AGENT_STUDIO_CHAT_KIND, ChatMessageRecord


def _consume_sse_events(stream_response) -> list[dict]:
    events: list[dict] = []
    for line in stream_response.iter_lines():
        if not line.startswith("data: "):
            continue
        events.append(json.loads(line[6:]))
    return events


class _FakeSuccessfulStream:
    def __init__(self, events: list[object], final_message: object):
        self._events = events
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
    def __init__(self, events: list[object], final_message: object):
        self._events = events
        self._final_message = final_message

    def stream(self, **_kwargs):
        return _FakeSuccessfulStream(list(self._events), self._final_message)


class _FakeAnthropicClient:
    def __init__(self, events: list[object], final_message: object):
        self.beta = SimpleNamespace(messages=_FakeMessagesApi(events, final_message))


def test_chat_context_model_round_trips_session_id():
    context = api_module.ChatContext.model_validate(
        {
            "trace_id": "trace-123",
            "session_id": "assistant-session-123",
            "active_tab": "agents",
        }
    )

    assert context.session_id == "assistant-session-123"
    assert context.model_dump(exclude_none=True)["session_id"] == "assistant-session-123"


def test_agent_studio_chat_endpoint_round_trips_context_session_id(
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
    monkeypatch.setattr(api_module, "_get_all_opus_tools", lambda _context=None: [])
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

    def _prepare_turn(*, request, **_kwargs):
        captured["request_context_session_id"] = request.context.session_id
        return api_module.PreparedAgentStudioTurn(
            session_id="agent-studio-session-1",
            turn_id="opus-turn-1",
            user_message=request.messages[-1].content,
            requested_context_session_id=request.context.session_id,
        )

    def _persist_turn(*, payload_json, trace_id, **_kwargs):
        captured["assistant_payload"] = payload_json
        captured["assistant_trace_id"] = trace_id
        return ChatMessageRecord(
            message_id=uuid4(),
            session_id="agent-studio-session-1",
            chat_kind=AGENT_STUDIO_CHAT_KIND,
            turn_id="opus-turn-1",
            role="assistant",
            message_type="text",
            content="Stored answer",
            payload_json=payload_json,
            trace_id=trace_id,
            created_at=api_module.datetime.utcnow(),
        )

    monkeypatch.setattr(api_module, "_prepare_agent_studio_turn", _prepare_turn)
    monkeypatch.setattr(api_module, "_persist_completed_agent_studio_turn", _persist_turn)
    monkeypatch.setattr(
        api_module.anthropic,
        "AsyncAnthropic",
        lambda api_key: _FakeAnthropicClient(
            events=[
                SimpleNamespace(
                    type="content_block_delta",
                    delta=SimpleNamespace(text="Stored answer"),
                )
            ],
            final_message=SimpleNamespace(
                content=[SimpleNamespace(type="text", text="Stored answer")],
                stop_reason="end_turn",
            ),
        ),
    )

    with contract_client.stream(
        "POST",
        "/api/agent-studio/chat",
        headers=chat_contract_auth_headers,
        json={
            "messages": [{"role": "user", "content": "Please analyze this trace"}],
            "context": {
                "trace_id": "trace-123",
                "session_id": "assistant-session-123",
            },
        },
    ) as response:
        events = _consume_sse_events(response)

    assert response.status_code == 200, response.text
    assert [event["type"] for event in events] == ["TEXT_DELTA", "DONE"]
    assert all(event["session_id"] == "agent-studio-session-1" for event in events)
    assert captured["request_context_session_id"] == "assistant-session-123"
    assert captured["assistant_trace_id"] == "trace-123"
    assert captured["assistant_payload"] == {"seed_session_id": "assistant-session-123"}
