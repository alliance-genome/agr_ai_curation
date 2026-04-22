"""Unit tests for Agent Studio Anthropic chat error handling."""

import asyncio
import json
from types import SimpleNamespace

import httpx
from fastapi.responses import StreamingResponse

from src.api import agent_studio as api_module


async def _consume_stream(response: StreamingResponse) -> list[dict]:
    chunks = []
    async for chunk in response.body_iterator:
        chunks.append(chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk)

    payloads = []
    for line in "".join(chunks).splitlines():
        if line.startswith("data: "):
            payloads.append(json.loads(line[6:]))
    return payloads


class _RaisingStreamContext:
    def __init__(self, error: Exception):
        self._error = error

    async def __aenter__(self):
        raise self._error

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeMessagesAPI:
    def __init__(self, error: Exception):
        self._error = error

    def stream(self, **_kwargs):
        return _RaisingStreamContext(self._error)


class _FakeAnthropicClient:
    def __init__(self, error: Exception):
        self.beta = SimpleNamespace(messages=_FakeMessagesAPI(error))


def _configure_chat_endpoint(monkeypatch, error: Exception):
    alerts = []
    logger_errors = []
    prepared_turn = api_module.PreparedAgentStudioTurn(
        session_id="agent-studio-session-1",
        turn_id="opus-turn-1",
        user_message="Please help",
        requested_context_session_id=None,
    )

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
    monkeypatch.setattr(api_module, "set_current_flow_context", lambda _flow_context: None)
    monkeypatch.setattr(api_module, "clear_current_flow_context", lambda: None)
    monkeypatch.setattr(
        api_module,
        "set_global_user_from_cognito",
        lambda _db, _user: SimpleNamespace(id=1),
    )
    monkeypatch.setattr(
        api_module,
        "_prepare_agent_studio_turn",
        lambda **_kwargs: prepared_turn,
    )

    def _fake_get_db():
        yield SimpleNamespace(close=lambda: None)

    async def _fake_notify_tool_failure(**kwargs):
        alerts.append(kwargs)

    def _run_task_immediately(coro):
        try:
            coro.send(None)
        except StopIteration:
            return SimpleNamespace(done=lambda: True)
        raise AssertionError("Expected notify_tool_failure stub to complete immediately")

    monkeypatch.setattr(api_module, "get_db", _fake_get_db)
    monkeypatch.setattr(api_module, "notify_tool_failure", _fake_notify_tool_failure)
    monkeypatch.setattr(api_module.asyncio, "create_task", _run_task_immediately)
    monkeypatch.setattr(
        api_module.anthropic,
        "AsyncAnthropic",
        lambda api_key: _FakeAnthropicClient(error),
    )
    monkeypatch.setattr(
        api_module.logger,
        "error",
        lambda *args, **kwargs: logger_errors.append((args, kwargs)),
    )

    return alerts, logger_errors


def _make_bad_request_error(message: str):
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(400, request=request)
    return api_module.anthropic.BadRequestError(message, response=response, body={"request_id": "req_test_123"})


def _make_api_error(message: str):
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return api_module.anthropic.APIError(message, request, body={"request_id": "req_test_456"})


def _chat_request():
    return api_module.ChatRequest(
        messages=[api_module.ChatMessage(role="user", content="Please help")],
        context=api_module.ChatContext(trace_id="trace-123"),
    )


def test_chat_with_opus_sanitizes_bad_request_errors(monkeypatch):
    raw_message = (
        "Bad request: {'type': 'error', 'error': {'type': 'invalid_request_error', "
        "'message': 'Bad body'}, 'request_id': 'req_test_123'}"
    )
    alerts, logger_errors = _configure_chat_endpoint(
        monkeypatch,
        _make_bad_request_error(raw_message),
    )

    response = asyncio.run(
        api_module.chat_with_opus(
            request=_chat_request(),
            user={"email": "curator@example.org", "sub": "auth-sub"},
        )
    )

    events = asyncio.run(_consume_stream(response))

    assert events == [
        {
            "type": "ERROR",
            "session_id": "agent-studio-session-1",
            "sessionId": "agent-studio-session-1",
            "turn_id": "opus-turn-1",
            "trace_id": "trace-123",
            "message": (
                "Agent Studio couldn't complete that request because it ran into a problem "
                "sending it to the model. Please review your last step and try again. If "
                "the problem continues, refresh Agent Studio and retry."
            ),
            "error_source": "anthropic",
        }
    ]
    assert "req_test_123" not in events[0]["message"]
    assert alerts == [
        {
            "error_type": "BadRequestError",
            "error_message": raw_message,
            "source": "infrastructure",
            "specialist_name": "agent_studio_opus",
            "trace_id": "trace-123",
            "session_id": "agent-studio-session-1",
            "curator_id": "curator@example.org",
        }
    ]
    assert logger_errors[0][0][0] == "Anthropic bad request error: %s"
    assert logger_errors[0][1]["exc_info"] is True


def test_chat_with_opus_sanitizes_api_errors(monkeypatch):
    raw_message = (
        "API error: {'type': 'error', 'error': {'details': None, 'type': 'api_error', "
        "'message': 'Internal server error'}, 'request_id': 'req_test_456'}"
    )
    alerts, logger_errors = _configure_chat_endpoint(
        monkeypatch,
        _make_api_error(raw_message),
    )

    response = asyncio.run(
        api_module.chat_with_opus(
            request=_chat_request(),
            user={"email": "curator@example.org", "sub": "auth-sub"},
        )
    )

    events = asyncio.run(_consume_stream(response))

    assert events == [
        {
            "type": "ERROR",
            "session_id": "agent-studio-session-1",
            "sessionId": "agent-studio-session-1",
            "turn_id": "opus-turn-1",
            "trace_id": "trace-123",
            "message": (
                "The model service had a temporary problem while working on your request. "
                "Any tool actions started during this turn may already have completed, so "
                "please check the results before retrying. If needed, try again in a moment."
            ),
            "error_source": "anthropic",
        }
    ]
    assert "req_test_456" not in events[0]["message"]
    assert alerts == [
        {
            "error_type": "APIError",
            "error_message": raw_message,
            "source": "infrastructure",
            "specialist_name": "agent_studio_opus",
            "trace_id": "trace-123",
            "session_id": "agent-studio-session-1",
            "curator_id": "curator@example.org",
        }
    ]
    assert logger_errors[0][0][0] == "Anthropic API error: %s"
    assert logger_errors[0][1]["exc_info"] is True


def test_chat_with_opus_preserves_context_overflow_branch(monkeypatch):
    alerts, logger_errors = _configure_chat_endpoint(
        monkeypatch,
        _make_bad_request_error("Prompt is too long and exceeded the token limit"),
    )

    response = asyncio.run(
        api_module.chat_with_opus(
            request=_chat_request(),
            user={"email": "curator@example.org", "sub": "auth-sub"},
        )
    )

    events = asyncio.run(_consume_stream(response))

    assert events == [
        {
            "type": "CONTEXT_OVERFLOW",
            "session_id": "agent-studio-session-1",
            "sessionId": "agent-studio-session-1",
            "turn_id": "opus-turn-1",
            "trace_id": "trace-123",
            "message": "I've hit my token limit for this conversation. The last tool call returned too much data.",
            "recovery_hint": (
                "Try a lighter-weight tool call: use get_trace_summary instead of full views, "
                "get_tool_calls_summary instead of get_tool_calls_page, or use smaller page_size "
                "(e.g., 5) with get_tool_calls_page. You can also filter by tool_name to get "
                "only specific tool calls."
            ),
            "suggested_tools": [
                "get_trace_summary - lightweight overview (~500 tokens)",
                "get_tool_calls_summary - summaries only, no full results",
                "get_tool_calls_page with page_size=5 - smaller batches",
                "get_tool_call_detail - single call at a time",
            ],
        }
    ]
    assert alerts == []
    assert logger_errors == []
    assert "error_source" not in events[0]
