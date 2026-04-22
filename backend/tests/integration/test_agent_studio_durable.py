"""Integration coverage for durable Agent Studio Opus persistence."""

from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy import select

from src.lib.chat_history_repository import (
    AGENT_STUDIO_CHAT_KIND,
    ASSISTANT_CHAT_KIND,
    ChatHistoryRepository,
)
from src.models.sql.chat_message import ChatMessage as ChatMessageModel
from src.models.sql.chat_session import ChatSession as ChatSessionModel
from tests.integration.evidence_test_support import collect_sse_events

pytest_plugins = ["tests.integration.evidence_test_support"]


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
    def __init__(self, scenarios: list[tuple[list[object], object]], calls: list[dict]):
        self._scenarios = list(scenarios)
        self._calls = calls

    def stream(self, **kwargs):
        self._calls.append(kwargs)
        if not self._scenarios:
            raise AssertionError("Anthropic should not stream again for this test")
        events, final_message = self._scenarios.pop(0)
        return _FakeSuccessfulStream(events, final_message)


class _FakeAnthropicClient:
    def __init__(self, scenarios: list[tuple[list[object], object]], calls: list[dict]):
        self.beta = SimpleNamespace(messages=_FakeMessagesApi(scenarios, calls))


class _UnexpectedAnthropicClient:
    class _Messages:
        def stream(self, **_kwargs):
            raise AssertionError("Anthropic should not be called for durable replay")

    def __init__(self):
        self.beta = SimpleNamespace(messages=self._Messages())


def _configure_agent_studio_chat(monkeypatch, *, scenarios, tool_result=None, stream_calls=None):
    from src.api import agent_studio as api_module

    calls = stream_calls if stream_calls is not None else []

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

    async def _fake_tool_call(**_kwargs):
        return tool_result if tool_result is not None else {"summary": "trace summary"}

    monkeypatch.setattr(api_module, "_handle_tool_call", _fake_tool_call)
    monkeypatch.setattr(
        api_module.anthropic,
        "AsyncAnthropic",
        lambda api_key: _FakeAnthropicClient(scenarios, calls),
    )
    return calls


def test_agent_studio_chat_persists_durable_rows_and_replays_completed_turn(
    client,
    monkeypatch,
    test_db,
):
    stream_calls = _configure_agent_studio_chat(
        monkeypatch,
        scenarios=[
            (
                [],
                SimpleNamespace(
                    content=[
                        SimpleNamespace(
                            type="tool_use",
                            id="toolu-1",
                            name="summarize_trace",
                            input={"trace_id": "trace-123"},
                        )
                    ],
                    stop_reason="tool_use",
                ),
            ),
            (
                [
                    SimpleNamespace(
                        type="content_block_delta",
                        delta=SimpleNamespace(text="Stored answer"),
                    )
                ],
                SimpleNamespace(
                    content=[SimpleNamespace(type="text", text="Stored answer")],
                    stop_reason="end_turn",
                ),
            ),
        ],
        tool_result={"summary": "trace summary"},
    )

    request_payload = {
        "messages": [{"role": "user", "content": "Please analyze this trace"}],
        "context": {
            "session_id": "agent-studio-session-replay",
            "trace_id": "trace-123",
        },
    }

    with client.stream("POST", "/api/agent-studio/chat", json=request_payload) as stream_response:
        events = collect_sse_events(stream_response)
        assert stream_response.status_code == 200

    assert [event["type"] for event in events] == [
        "TOOL_USE",
        "TOOL_RESULT",
        "TEXT_DELTA",
        "DONE",
    ]
    assert all(event["session_id"] == "agent-studio-session-replay" for event in events)
    assert len(stream_calls) == 2

    test_db.expire_all()
    session_row = test_db.scalar(
        select(ChatSessionModel).where(
            ChatSessionModel.session_id == "agent-studio-session-replay"
        )
    )
    assert session_row is not None
    assert session_row.chat_kind == AGENT_STUDIO_CHAT_KIND

    rows = test_db.scalars(
        select(ChatMessageModel)
        .where(ChatMessageModel.session_id == "agent-studio-session-replay")
        .order_by(ChatMessageModel.created_at.asc(), ChatMessageModel.message_id.asc())
    ).all()
    assert [(row.role, row.chat_kind, row.content) for row in rows] == [
        ("user", AGENT_STUDIO_CHAT_KIND, "Please analyze this trace"),
        ("assistant", AGENT_STUDIO_CHAT_KIND, "Stored answer"),
    ]
    assert rows[1].payload_json == {
        "tool_calls": [
            {
                "tool_name": "summarize_trace",
                "tool_input": {"trace_id": "trace-123"},
                "result": {"summary": "trace summary"},
            }
        ]
    }

    from src.api import agent_studio as api_module

    monkeypatch.setattr(
        api_module.anthropic,
        "AsyncAnthropic",
        lambda api_key: _UnexpectedAnthropicClient(),
    )

    with client.stream("POST", "/api/agent-studio/chat", json=request_payload) as replay_response:
        replay_events = collect_sse_events(replay_response)
        assert replay_response.status_code == 200

    assert [event["type"] for event in replay_events] == [
        "TOOL_USE",
        "TOOL_RESULT",
        "TEXT_DELTA",
        "DONE",
    ]
    assert all(event["session_id"] == "agent-studio-session-replay" for event in replay_events)
    assert len(stream_calls) == 2


def test_agent_studio_chat_derives_a_durable_session_from_assistant_seed_id(
    client,
    monkeypatch,
    test_db,
):
    _configure_agent_studio_chat(
        monkeypatch,
        scenarios=[
            (
                [
                    SimpleNamespace(
                        type="content_block_delta",
                        delta=SimpleNamespace(text="Seeded durable answer"),
                    )
                ],
                SimpleNamespace(
                    content=[SimpleNamespace(type="text", text="Seeded durable answer")],
                    stop_reason="end_turn",
                ),
            ),
        ],
    )

    repository = ChatHistoryRepository(test_db)
    repository.create_session(
        session_id="assistant-seed-session",
        user_auth_sub=client.current_user_auth_sub,
        chat_kind=ASSISTANT_CHAT_KIND,
    )
    test_db.commit()

    with client.stream(
        "POST",
        "/api/agent-studio/chat",
        json={
            "messages": [{"role": "user", "content": "Please continue from the seeded transcript"}],
            "context": {
                "session_id": "assistant-seed-session",
                "trace_id": "trace-seeded",
            },
        },
    ) as stream_response:
        events = collect_sse_events(stream_response)
        assert stream_response.status_code == 200

    derived_session_id = "agent-studio-seed:assistant-seed-session"
    assert all(event["session_id"] == derived_session_id for event in events)

    test_db.expire_all()
    assistant_seed_row = test_db.scalar(
        select(ChatSessionModel).where(
            ChatSessionModel.session_id == "assistant-seed-session"
        )
    )
    derived_row = test_db.scalar(
        select(ChatSessionModel).where(
            ChatSessionModel.session_id == derived_session_id
        )
    )
    assert assistant_seed_row is not None
    assert assistant_seed_row.chat_kind == ASSISTANT_CHAT_KIND
    assert derived_row is not None
    assert derived_row.chat_kind == AGENT_STUDIO_CHAT_KIND

    stored_messages = test_db.scalars(
        select(ChatMessageModel)
        .where(ChatMessageModel.session_id == derived_session_id)
        .order_by(ChatMessageModel.created_at.asc(), ChatMessageModel.message_id.asc())
    ).all()
    assert [(row.role, row.content) for row in stored_messages] == [
        ("user", "Please continue from the seeded transcript"),
        ("assistant", "Seeded durable answer"),
    ]
    assert stored_messages[1].payload_json == {"seed_session_id": "assistant-seed-session"}


def test_agent_studio_chat_returns_404_for_other_users_session(
    client,
    monkeypatch,
    test_db,
    get_auth_mock,
    curator1_user,
):
    repository = ChatHistoryRepository(test_db)
    repository.create_session(
        session_id="foreign-agent-studio-session",
        user_auth_sub=curator1_user["sub"],
        chat_kind=AGENT_STUDIO_CHAT_KIND,
    )
    test_db.commit()

    from src.api import agent_studio as api_module

    monkeypatch.setattr(
        api_module.anthropic,
        "AsyncAnthropic",
        lambda api_key: _UnexpectedAnthropicClient(),
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    get_auth_mock.set_user("chat2")

    response = client.post(
        "/api/agent-studio/chat",
        json={
            "messages": [{"role": "user", "content": "Open another user's session"}],
            "context": {"session_id": "foreign-agent-studio-session"},
        },
    )

    assert response.status_code == 404, response.text
    assert response.json()["detail"] == "Chat session not found"
