"""Tests for Agent Studio Opus provider-context compaction helpers."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import src.api.agent_studio as api_module
from src.lib.chat_history_repository import (
    AGENT_STUDIO_CHAT_KIND,
    ChatMessageRecord,
    ChatSessionRecord,
)


async def _consume_stream(response) -> list[dict[str, Any]]:
    chunks: list[str] = []
    async for chunk in response.body_iterator:
        chunks.append(chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk)

    events: list[dict[str, Any]] = []
    for line in "".join(chunks).splitlines():
        if line.startswith("data: "):
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
    def __init__(self, captured: dict[str, Any]):
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
                            id="toolu_big_1",
                            name="get_trace_payload",
                            input={
                                "trace_id": "trace-1",
                                "payload_id": "observation:abc:output",
                                "max_chars": 0,
                            },
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
                    delta=SimpleNamespace(text="I fetched the compacted result."),
                )
            ],
            final_message=SimpleNamespace(
                content=[SimpleNamespace(type="text", text="I fetched the compacted result.")],
                stop_reason="end_turn",
            ),
        )


class _FakeAnthropicClient:
    def __init__(self, captured: dict[str, Any]):
        self.beta = SimpleNamespace(messages=_FakeMessagesApi(captured))


class _RepeatedToolLoopMessagesApi:
    def __init__(self, captured: dict[str, Any]):
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
                            id="toolu_inventory_1",
                            name="get_trace_payloads",
                            input={
                                "trace_id": "trace-1",
                                "include_values": False,
                            },
                        )
                    ],
                    stop_reason="tool_use",
                ),
            )
        if len(api_calls) == 2:
            self._captured["first_continuation_messages"] = kwargs["messages"]
            self._captured["first_provider_result"] = kwargs["messages"][-1]["content"][0][
                "content"
            ]
            return _FakeSuccessfulStream(
                events=[],
                final_message=SimpleNamespace(
                    content=[
                        SimpleNamespace(
                            type="tool_use",
                            id="toolu_payload_2",
                            name="get_trace_payload",
                            input={
                                "trace_id": "trace-1",
                                "payload_id": "observation:abc:output",
                                "max_chars": 0,
                            },
                        )
                    ],
                    stop_reason="tool_use",
                ),
            )

        self._captured["second_continuation_messages"] = kwargs["messages"]
        self._captured["second_provider_result"] = kwargs["messages"][-1]["content"][0][
            "content"
        ]
        return _FakeSuccessfulStream(
            events=[
                SimpleNamespace(
                    type="content_block_delta",
                    delta=SimpleNamespace(text="I recalled the exact payload."),
                )
            ],
            final_message=SimpleNamespace(
                content=[SimpleNamespace(type="text", text="I recalled the exact payload.")],
                stop_reason="end_turn",
            ),
        )


class _RepeatedToolLoopAnthropicClient:
    def __init__(self, captured: dict[str, Any]):
        self.beta = SimpleNamespace(messages=_RepeatedToolLoopMessagesApi(captured))


def _agent_studio_message(
    *,
    session_id: str,
    turn_id: str,
    role: str,
    content: str,
) -> ChatMessageRecord:
    return ChatMessageRecord(
        message_id=uuid4(),
        session_id=session_id,
        chat_kind=AGENT_STUDIO_CHAT_KIND,
        turn_id=turn_id,
        role=role,
        message_type="text",
        content=content,
        payload_json=None,
        trace_id=None,
        created_at=datetime(2026, 6, 15, 20, 0, tzinfo=timezone.utc),
    )


def _agent_studio_session(*, session_id: str) -> ChatSessionRecord:
    timestamp = datetime(2026, 6, 15, 20, 0, tzinfo=timezone.utc)
    return ChatSessionRecord(
        session_id=session_id,
        user_auth_sub="auth-sub-1",
        title=f"title-{session_id}",
        generated_title=None,
        active_document_id=None,
        created_at=timestamp,
        updated_at=timestamp,
        last_message_at=timestamp,
        deleted_at=None,
        chat_kind=AGENT_STUDIO_CHAT_KIND,
    )


def test_large_tool_result_is_compacted_for_provider_continuation(monkeypatch):
    monkeypatch.setenv("AGENT_STUDIO_PROVIDER_TOOL_RESULT_INLINE_MAX_CHARS", "500")

    large_value = "payload chunk " * 500
    content = api_module._provider_tool_result_content(
        tool_name="get_trace_payload",
        tool_input={
            "trace_id": "trace-1",
            "payload_id": "observation:abc:output",
            "start": 0,
            "max_chars": 0,
        },
        tool_result={
            "status": "success",
            "trace_id": "trace-1",
            "data": {
                "payload_id": "observation:abc:output",
                "value": large_value,
                "next_start": None,
            },
        },
        session_id="agent-studio-session-1",
        turn_id="opus-turn-4-abc123",
    )

    compact = json.loads(content)

    assert compact["status"] == "compacted_tool_result"
    assert compact["tool_result_compacted"] is True
    assert compact["raw_result_json_chars"] > 500
    assert len(content) < len(large_value)
    assert large_value not in content
    assert compact["recall"]["chat_turn"] == {
        "tool": "get_chat_turn",
        "session_id": "agent-studio-session-1",
        "turn_id": "opus-turn-4-abc123",
        "purpose": "Reload durable current-session turn text and tool-call summaries after provider context editing.",
    }
    assert compact["recall"]["trace_payloads"]["payload_ids"] == [
        "observation:abc:output"
    ]
    assert compact["recall"]["repeat_or_narrow_tool"]["input"]["payload_id"] == (
        "observation:abc:output"
    )


def test_small_tool_result_stays_inline_for_provider_continuation(monkeypatch):
    monkeypatch.setenv("AGENT_STUDIO_PROVIDER_TOOL_RESULT_INLINE_MAX_CHARS", "500")

    tool_result = {"success": True, "current_prompt": "Small refreshed prompt."}

    content = api_module._provider_tool_result_content(
        tool_name="refresh_workshop_prompt",
        tool_input={"target_prompt": "main"},
        tool_result=tool_result,
        session_id="agent-studio-session-1",
        turn_id="opus-turn-1",
    )

    assert json.loads(content) == tool_result


def test_streaming_tool_loop_sends_compact_large_result_to_provider(monkeypatch):
    captured: dict[str, Any] = {}
    large_value = "payload chunk " * 500

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("AGENT_STUDIO_PROVIDER_TOOL_RESULT_INLINE_MAX_CHARS", "500")
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
        lambda _db, _user: SimpleNamespace(id=7),
    )
    monkeypatch.setattr(api_module, "get_db", lambda: iter([SimpleNamespace(close=lambda: None)]))
    monkeypatch.setattr(
        api_module,
        "_prepare_agent_studio_turn",
        lambda *, request, **_kwargs: api_module.PreparedAgentStudioTurn(
            session_id="agent-studio-session-1",
            turn_id="opus-turn-4-abc123",
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
            created_at=datetime(2026, 6, 15, 20, 0, tzinfo=timezone.utc),
        ),
    )

    async def _fake_handle_tool_call(**_kwargs):
        return {
            "status": "success",
            "trace_id": "trace-1",
            "data": {
                "payload_id": "observation:abc:output",
                "value": large_value,
            },
        }

    monkeypatch.setattr(api_module, "_handle_tool_call", _fake_handle_tool_call)
    monkeypatch.setattr(
        api_module.anthropic,
        "AsyncAnthropic",
        lambda api_key: _FakeAnthropicClient(captured),
    )

    request = api_module.ChatRequest(
        messages=[api_module.ChatMessage(role="user", content="Fetch the large payload")],
        context=api_module.ChatContext(active_tab="agents"),
    )

    response = asyncio.run(
        api_module.chat_with_opus(
            request,
            user={"sub": "auth-sub-1", "email": "dev@example.org"},
        )
    )
    events = asyncio.run(_consume_stream(response))

    tool_result_events = [event for event in events if event["type"] == "TOOL_RESULT"]
    assert tool_result_events[0]["result"]["data"]["value"] == large_value

    second_messages = captured["second_call_messages"]
    tool_result_content = second_messages[-1]["content"][0]["content"]
    compact = json.loads(tool_result_content)

    assert compact["status"] == "compacted_tool_result"
    assert compact["recall"]["chat_turn"]["turn_id"] == "opus-turn-4-abc123"
    assert compact["recall"]["trace_payloads"]["payload_ids"] == [
        "observation:abc:output"
    ]
    assert large_value not in tool_result_content


def test_repeated_tool_loop_continuations_stay_compact_and_keep_exact_results(
    monkeypatch,
):
    captured: dict[str, Any] = {}
    inventory_value = "payload inventory entry " * 400
    exact_payload_value = "exact TraceReview payload " * 500

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("AGENT_STUDIO_PROVIDER_TOOL_RESULT_INLINE_MAX_CHARS", "500")
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
        lambda _db, _user: SimpleNamespace(id=7),
    )
    monkeypatch.setattr(api_module, "get_db", lambda: iter([SimpleNamespace(close=lambda: None)]))
    monkeypatch.setattr(
        api_module,
        "_prepare_agent_studio_turn",
        lambda *, request, **_kwargs: api_module.PreparedAgentStudioTurn(
            session_id="agent-studio-session-1",
            turn_id="opus-turn-repeat-abc123",
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
            created_at=datetime(2026, 6, 15, 20, 0, tzinfo=timezone.utc),
        ),
    )

    async def _fake_handle_tool_call(*, tool_name, **_kwargs):
        if tool_name == "get_trace_payloads":
            return {
                "status": "success",
                "data": {
                    "payloads": [
                        {
                            "payload_id": "observation:abc:output",
                            "preview": inventory_value,
                            "model_live": False,
                        }
                    ],
                    "observability_payloads": {
                        "exact_payload_requires_explicit_lookup": True,
                    },
                },
            }
        if tool_name == "get_trace_payload":
            return {
                "status": "success",
                "trace_id": "trace-1",
                "data": {
                    "payload_id": "observation:abc:output",
                    "value": exact_payload_value,
                    "next_start": None,
                },
            }
        raise AssertionError(f"unexpected tool: {tool_name}")

    monkeypatch.setattr(api_module, "_handle_tool_call", _fake_handle_tool_call)
    monkeypatch.setattr(
        api_module.anthropic,
        "AsyncAnthropic",
        lambda api_key: _RepeatedToolLoopAnthropicClient(captured),
    )

    request = api_module.ChatRequest(
        messages=[
            api_module.ChatMessage(
                role="user",
                content="Inspect the trace inventory, then fetch the exact payload.",
            )
        ],
        context=api_module.ChatContext(active_tab="agents", trace_id="trace-1"),
    )

    response = asyncio.run(
        api_module.chat_with_opus(
            request,
            user={"sub": "auth-sub-1", "email": "dev@example.org"},
        )
    )
    events = asyncio.run(_consume_stream(response))

    preflight_operations = [
        event["operation"]
        for event in events
        if event["type"] == "PROVIDER_CONTEXT_PREFLIGHT"
    ]
    assert preflight_operations == [
        "initial_anthropic_call",
        "tool_loop_continuation",
        "tool_loop_continuation",
    ]

    tool_result_events = [event for event in events if event["type"] == "TOOL_RESULT"]
    assert tool_result_events[0]["result"]["data"]["payloads"][0]["preview"] == inventory_value
    assert tool_result_events[1]["result"]["data"]["value"] == exact_payload_value

    first_provider_result = captured["first_provider_result"]
    second_provider_result = captured["second_provider_result"]
    first_compact = json.loads(first_provider_result)
    second_compact = json.loads(second_provider_result)

    assert first_compact["status"] == "compacted_tool_result"
    assert "payloads" in first_compact["summary"]["fields"]["data"]["keys"]
    assert first_compact["recall"]["trace_payloads"]["payload_ids"] == [
        "observation:abc:output"
    ]
    assert second_compact["status"] == "compacted_tool_result"
    assert second_compact["recall"]["chat_turn"]["turn_id"] == "opus-turn-repeat-abc123"
    assert second_compact["recall"]["trace_payloads"]["payload_ids"] == [
        "observation:abc:output"
    ]
    assert inventory_value not in first_provider_result
    assert exact_payload_value not in second_provider_result


def test_compact_tool_result_recall_hints_fetch_exact_turn_and_trace_payload(
    monkeypatch,
):
    monkeypatch.setenv("AGENT_STUDIO_PROVIDER_TOOL_RESULT_INLINE_MAX_CHARS", "500")

    exact_turn_phrase = "Early Agent Studio note: preserve WB:WBGene00006789 exactly."
    exact_payload_value = "TraceReview exact payload body " * 300
    compact = json.loads(
        api_module._provider_tool_result_content(
            tool_name="get_trace_payload",
            tool_input={
                "trace_id": "trace-1",
                "payload_id": "observation:abc:output",
                "max_chars": 0,
            },
            tool_result={
                "status": "success",
                "trace_id": "trace-1",
                "data": {
                    "payload_id": "observation:abc:output",
                    "value": exact_payload_value,
                },
            },
            session_id="agent-studio-session-1",
            turn_id="opus-turn-early-abc123",
        )
    )

    class _FakeRepository:
        def __init__(self, _db):
            pass

        def get_session(self, **kwargs):
            assert kwargs == {
                "session_id": "agent-studio-session-1",
                "user_auth_sub": "auth-sub-1",
            }
            return _agent_studio_session(session_id=kwargs["session_id"])

        def list_messages_for_turn(self, **kwargs):
            assert kwargs == {
                "session_id": "agent-studio-session-1",
                "user_auth_sub": "auth-sub-1",
                "chat_kind": AGENT_STUDIO_CHAT_KIND,
                "turn_id": "opus-turn-early-abc123",
            }
            return [
                _agent_studio_message(
                    session_id=kwargs["session_id"],
                    turn_id=kwargs["turn_id"],
                    role="user",
                    content=exact_turn_phrase,
                ),
                _agent_studio_message(
                    session_id=kwargs["session_id"],
                    turn_id=kwargs["turn_id"],
                    role="assistant",
                    content="I recorded that exact note.",
                ),
            ]

    monkeypatch.setattr(api_module, "SessionLocal", lambda: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(api_module, "ChatHistoryRepository", _FakeRepository)

    turn_result = asyncio.run(
        api_module._handle_tool_call(
            tool_name=compact["recall"]["chat_turn"]["tool"],
            tool_input=compact["recall"]["chat_turn"],
            context=None,
            user_email="dev@example.org",
            user_auth_sub="auth-sub-1",
            messages=[],
        )
    )

    from src.lib.agent_studio import tools as tools_module

    captured_payload_lookup: dict[str, Any] = {}

    async def _fake_get_trace_payload(**kwargs):
        captured_payload_lookup.update(kwargs)
        return {
            "status": "success",
            "data": {
                "payload_id": kwargs["payload_id"],
                "value": exact_payload_value,
            },
        }

    monkeypatch.setattr(tools_module, "get_trace_payload", _fake_get_trace_payload)
    payload_result = asyncio.run(
        api_module._handle_tool_call(
            tool_name=compact["recall"]["trace_payloads"]["tool"],
            tool_input={
                "trace_id": "trace-1",
                "payload_id": compact["recall"]["trace_payloads"]["payload_ids"][0],
                "max_chars": 0,
            },
            context=None,
            user_email="dev@example.org",
            user_auth_sub="auth-sub-1",
            messages=[],
        )
    )

    assert exact_payload_value not in json.dumps(compact, sort_keys=True)
    assert turn_result["success"] is True
    assert turn_result["messages"][0]["content"] == exact_turn_phrase
    assert captured_payload_lookup == {
        "trace_id": "trace-1",
        "payload_id": "observation:abc:output",
        "scope": None,
        "observation_id": None,
        "field": None,
        "start": 0,
        "max_chars": 0,
    }
    assert payload_result["data"]["value"] == exact_payload_value
