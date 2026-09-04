"""Unit tests for Agent Studio OpenAI chat error handling."""

import asyncio
import json
import logging
from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from src.api import agent_studio as api_module
from src.lib import http_errors
from src.lib.agent_studio.authoring_context import workshop_draft_fingerprint
from src.lib.agent_studio.models import AgentWorkshopContext


@pytest.fixture(autouse=True)
def _reset_executable_runs():
    api_module.executable_run_manager._runs.clear()
    api_module.executable_run_manager._active_session_run_ids.clear()
    yield
    api_module.executable_run_manager._runs.clear()
    api_module.executable_run_manager._active_session_run_ids.clear()


async def _consume_stream(response: StreamingResponse) -> list[dict]:
    chunks = []
    async for chunk in response.body_iterator:
        chunks.append(chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk)

    payloads = []
    for line in "".join(chunks).splitlines():
        if line.startswith("data: "):
            payloads.append(json.loads(line[6:]))
    await asyncio.sleep(0)
    return payloads


def _configure_chat_endpoint(monkeypatch, error: Exception):
    alerts = []
    logger_errors = []
    runtime_reports = []
    prepared_turn = api_module.PreparedAgentStudioTurn(
        session_id="agent-studio-session-1",
        turn_id="opus-turn-1",
        user_message="Please help",
        requested_context_session_id=None,
        user_turn_created=False,
    )

    monkeypatch.setattr(api_module, "get_api_key", lambda _provider: "test-key")
    monkeypatch.setattr(
        api_module.uuid,
        "uuid4",
        lambda: UUID("12345678-1234-5678-1234-567812345678"),
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

    def _fake_notify_tool_failure(**kwargs):
        alerts.append(kwargs)
        async def _complete_notification():
            return None

        return _complete_notification()

    monkeypatch.setattr(api_module, "get_db", _fake_get_db)
    monkeypatch.setattr(api_module, "notify_tool_failure", _fake_notify_tool_failure)
    monkeypatch.setattr(
        api_module,
        "report_runtime_exception",
        lambda exc, **kwargs: runtime_reports.append((exc, kwargs)) or True,
    )
    async def _raise_from_openai_runtime(**_kwargs):
        if False:
            yield {}
        raise error

    monkeypatch.setattr(api_module, "stream_agent_studio_run", _raise_from_openai_runtime)
    monkeypatch.setattr(
        api_module.logger,
        "error",
        lambda *args, **kwargs: logger_errors.append((args, kwargs)),
    )

    return alerts, logger_errors, runtime_reports


def _make_bad_request_error(message: str):
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    response = httpx.Response(400, request=request)
    return api_module.openai.BadRequestError(message, response=response, body={"request_id": "req_test_123"})


def _make_api_error(message: str):
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    return api_module.openai.APIError(message, request, body={"request_id": "req_test_456"})


def _chat_request():
    return api_module.ChatRequest(
        messages=[api_module.ChatMessage(role="user", content="Please help")],
        context=api_module.ChatContext(trace_id="trace-123"),
    )


def _assert_provider_context_preflight(event: dict) -> None:
    assert event["type"] == "PROVIDER_CONTEXT_PREFLIGHT"
    assert event["session_id"] == "agent-studio-session-1"
    assert event["turn_id"] == "opus-turn-1"
    assert event["trace_id"] == "12345678-1234-5678-1234-567812345678"
    assert event["operation"] == "agents_sdk_run"
    assert event["provider"] == "openai"
    assert event["model"] == "gpt-5.6-sol"
    assert event["model_live"] is True
    assert event["payload_summary"]["json_chars"] > 0


def _events_after_preflight(events: list[dict]) -> list[dict]:
    assert events
    _assert_provider_context_preflight(events[0])
    return events[1:]


def test_chat_with_opus_sanitizes_invalid_request_errors(monkeypatch, caplog):
    caplog.set_level(logging.WARNING, logger=api_module.logger.name)

    def _fake_get_db():
        yield SimpleNamespace(close=lambda: None)

    monkeypatch.setattr(api_module, "get_db", _fake_get_db)
    monkeypatch.setattr(
        api_module,
        "set_global_user_from_cognito",
        lambda _db, _user: SimpleNamespace(id=1),
    )
    monkeypatch.setattr(
        api_module,
        "_prepare_agent_studio_turn",
        lambda **_kwargs: (_ for _ in ()).throw(ValueError("session context exploded")),
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            api_module.chat_with_opus(
                request=_chat_request(),
                user={"email": "curator@example.org", "sub": "auth-sub"},
            )
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Agent Studio chat request is invalid"
    assert "session context exploded" not in str(exc_info.value.detail)
    assert "session context exploded" in caplog.text


def test_chat_with_opus_hides_group_restricted_workshop_agent_without_selected_id(
    monkeypatch,
):
    from src.lib.config.groups_loader import get_valid_group_ids

    custom_agent_uuid = uuid4()
    db = SimpleNamespace(close=lambda: None)
    allowed_group_id, active_group_id = get_valid_group_ids()[:2]

    def _fake_get_db():
        yield db

    monkeypatch.setattr(api_module, "get_db", _fake_get_db)
    monkeypatch.setattr(
        api_module,
        "set_global_user_from_cognito",
        lambda _db, _user: SimpleNamespace(id=1),
    )
    monkeypatch.setattr(
        api_module,
        "get_custom_agent_visible_to_user",
        lambda _db, _uuid, _user_id: SimpleNamespace(
            allowed_group_ids=[allowed_group_id]
        ),
    )
    monkeypatch.setattr(
        api_module,
        "get_groups_from_provider_groups",
        lambda _groups: [active_group_id],
    )
    monkeypatch.setattr(
        api_module,
        "_prepare_agent_studio_turn",
        lambda **_kwargs: pytest.fail("inaccessible workshop agent reached persistence"),
    )

    workshop = AgentWorkshopContext(
        custom_agent_id=str(custom_agent_uuid),
        custom_agent_updated_at="2026-09-04T00:00:00Z",
    )
    workshop.draft_fingerprint = workshop_draft_fingerprint(workshop)
    request = api_module.ChatRequest(
        messages=[api_module.ChatMessage(role="user", content="Review this prompt")],
        context=api_module.ChatContext(
            active_tab="agent_workshop",
            agent_workshop=workshop,
        ),
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            api_module.chat_with_opus(
                request=request,
                user={"sub": "auth-sub", "cognito:groups": ["provider-group-a"]},
            )
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Agent not found"


def test_chat_with_opus_reports_sanitized_persistence_errors_and_closes_session(
    monkeypatch,
):
    raw_error = RuntimeError("sensitive persisted request payload")
    calls: dict[str, object] = {}

    class _FakeDb:
        def close(self):
            calls["closed"] = True

    def _fake_get_db():
        yield _FakeDb()

    def _report_runtime_exception(exc, *, component, operation, context):
        calls["reported"] = {
            "exc": exc,
            "component": component,
            "operation": operation,
            "context": context,
        }
        return True

    monkeypatch.setattr(api_module, "get_db", _fake_get_db)
    monkeypatch.setattr(
        api_module,
        "set_global_user_from_cognito",
        lambda _db, _user: SimpleNamespace(id=1),
    )
    monkeypatch.setattr(
        api_module,
        "_prepare_agent_studio_turn",
        lambda **_kwargs: (_ for _ in ()).throw(raw_error),
    )
    monkeypatch.setattr(http_errors, "report_runtime_exception", _report_runtime_exception)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            api_module.chat_with_opus(
                request=_chat_request(),
                user={"email": "curator@example.org", "sub": "auth-sub"},
            )
        )

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "Failed to persist Agent Studio chat request"
    assert "sensitive persisted request payload" not in str(exc_info.value.detail)
    assert calls["closed"] is True
    assert calls["reported"] == {
        "exc": raw_error,
        "component": "api",
        "operation": "sanitized_http_exception",
        "context": {
            "logger_name": api_module.logger.name,
            "status_code": 500,
            "log_level": logging.ERROR,
            "level_name": "ERROR",
        },
    }


def test_chat_with_opus_sanitizes_bad_request_errors(monkeypatch):
    raw_message = (
        "Bad request: {'type': 'error', 'error': {'type': 'invalid_request_error', "
        "'message': 'Bad body'}, 'request_id': 'req_test_123'}"
    )
    alerts, logger_errors, runtime_reports = _configure_chat_endpoint(
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

    output_events = _events_after_preflight(events)

    assert output_events == [
        {
            "type": "ERROR",
            "session_id": "agent-studio-session-1",
            "turn_id": "opus-turn-1",
            "trace_id": "12345678-1234-5678-1234-567812345678",
            "message": "Agent Studio could not complete the model request. Please review the last step and retry.",
            "error_source": "openai",
        }
    ]
    assert "req_test_123" not in output_events[0]["message"]
    assert alerts == []
    assert logger_errors == []
    assert runtime_reports[0][1]["operation"] == "openai_bad_request"
    assert runtime_reports[0][1]["tags"] == {
        "phase": "agents_sdk_run",
        "provider": "openai",
    }
    assert runtime_reports[0][1]["context"] == {"model": "gpt-5.6-sol"}


def test_chat_with_opus_sanitizes_api_errors(monkeypatch):
    raw_message = (
        "API error: {'type': 'error', 'error': {'details': None, 'type': 'api_error', "
        "'message': 'Internal server error'}, 'request_id': 'req_test_456'}"
    )
    alerts, logger_errors, runtime_reports = _configure_chat_endpoint(
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

    output_events = _events_after_preflight(events)

    assert output_events == [
        {
            "type": "ERROR",
            "session_id": "agent-studio-session-1",
            "turn_id": "opus-turn-1",
            "trace_id": "12345678-1234-5678-1234-567812345678",
            "message": "The model service had a temporary problem. Check any completed tool actions before retrying.",
            "error_source": "openai",
        }
    ]
    assert "req_test_456" not in output_events[0]["message"]
    assert alerts == [
        {
            "error_type": "APIError",
            "error_message": raw_message,
            "source": "infrastructure",
            "specialist_name": "agent_studio_openai",
            "trace_id": "12345678-1234-5678-1234-567812345678",
            "session_id": "agent-studio-session-1",
            "curator_id": "curator@example.org",
            "capture_sentry": False,
        }
    ]
    assert logger_errors[0][0][0] == "OpenAI Agent Studio API error: %s"
    assert logger_errors[0][1]["exc_info"] is True
    assert logger_errors[0][1]["extra"] == {"sentry_skip_event": True}
    assert runtime_reports[0][1]["operation"] == "openai_provider_failure"


def test_chat_with_opus_preserves_context_overflow_branch(monkeypatch):
    alerts, logger_errors, runtime_reports = _configure_chat_endpoint(
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

    output_events = _events_after_preflight(events)

    assert output_events == [
        {
            "type": "CONTEXT_OVERFLOW",
            "session_id": "agent-studio-session-1",
            "turn_id": "opus-turn-1",
            "trace_id": "12345678-1234-5678-1234-567812345678",
            "message": "The conversation exceeded the model context. Use a bounded recall tool or start a new chat.",
            "error_source": "openai",
        }
    ]
    assert alerts == []
    assert logger_errors == []
    assert runtime_reports == []


@pytest.mark.parametrize(
    ("error", "event_type", "error_source"),
    [
        (
            api_module.ModelRefusalError("sensitive refusal details"),
            "REFUSAL",
            "model_refusal",
        ),
        (
            api_module.ModelBehaviorError(
                "Responses stream ended with terminal event `response.incomplete`."
            ),
            "INCOMPLETE",
            "openai",
        ),
    ],
)
def test_chat_with_opus_preserves_typed_non_crash_terminal_outcomes(
    monkeypatch,
    error,
    event_type,
    error_source,
):
    alerts, logger_errors, runtime_reports = _configure_chat_endpoint(monkeypatch, error)

    response = asyncio.run(
        api_module.chat_with_opus(
            request=_chat_request(),
            user={"email": "curator@example.org", "sub": "auth-sub"},
        )
    )
    events = asyncio.run(_consume_stream(response))
    output_events = _events_after_preflight(events)

    assert output_events[0]["type"] == event_type
    assert output_events[0]["error_source"] == error_source
    assert "sensitive refusal details" not in output_events[0]["message"]
    assert alerts == []
    assert logger_errors == []
    assert runtime_reports == []


def test_chat_with_opus_sanitizes_unexpected_errors(monkeypatch):
    raw_message = "stream exploded while completing Agent Studio response"
    alerts, logger_errors, runtime_reports = _configure_chat_endpoint(
        monkeypatch,
        RuntimeError(raw_message),
    )

    response = asyncio.run(
        api_module.chat_with_opus(
            request=_chat_request(),
            user={"email": "curator@example.org", "sub": "auth-sub"},
        )
    )

    events = asyncio.run(_consume_stream(response))

    output_events = _events_after_preflight(events)

    assert output_events == [
        {
            "type": "ERROR",
            "session_id": "agent-studio-session-1",
            "turn_id": "opus-turn-1",
            "trace_id": "12345678-1234-5678-1234-567812345678",
            "message": "Agent Studio ran into an unexpected problem. Check completed actions before retrying.",
            "error_source": "RuntimeError",
        }
    ]
    assert raw_message not in output_events[0]["message"]
    assert alerts == []
    assert logger_errors[0][0][0] == "Agent Studio OpenAI stream error: %s"
    assert logger_errors[0][1]["exc_info"] is True
    assert logger_errors[0][1]["extra"] == {"sentry_skip_event": True}
    assert runtime_reports[0][1]["operation"] == "openai_stream_failure"


def test_chat_with_opus_reports_turn_limit_without_leaking_sdk_detail(monkeypatch):
    raw_message = "turn limit reached after sensitive tool activity"
    alerts, logger_errors, runtime_reports = _configure_chat_endpoint(
        monkeypatch,
        api_module.MaxTurnsExceeded(raw_message),
    )

    response = asyncio.run(
        api_module.chat_with_opus(
            request=_chat_request(),
            user={"email": "curator@example.org", "sub": "auth-sub"},
        )
    )
    events = asyncio.run(_consume_stream(response))
    output_events = _events_after_preflight(events)

    assert output_events == [
        {
            "type": "ERROR",
            "session_id": "agent-studio-session-1",
            "turn_id": "opus-turn-1",
            "trace_id": "12345678-1234-5678-1234-567812345678",
            "message": "Agent Studio reached its configured tool-turn limit without completing.",
            "error_source": "turn_limit",
        }
    ]
    assert raw_message not in output_events[0]["message"]
    assert alerts == []
    assert logger_errors == []
    assert runtime_reports[0][1]["operation"] == "openai_turn_limit_exceeded"
    assert runtime_reports[0][1]["tags"] == {
        "phase": "agents_sdk_run",
        "provider": "openai",
    }


def test_chat_preflight_sizes_instructions_messages_and_authorized_tool_schemas(monkeypatch):
    captured = {}
    tool_definition = {
        "name": "inspect_catalog",
        "description": "Inspect the authorized catalog",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "additionalProperties": False,
        },
    }
    _configure_chat_endpoint(
        monkeypatch,
        api_module.ModelRefusalError("expected typed refusal"),
    )
    monkeypatch.setattr(
        api_module,
        "_get_all_opus_tools",
        lambda _context=None: [tool_definition],
    )

    def _capture_preflight(**kwargs):
        captured.update(kwargs)
        return {
            "operation": kwargs["operation"],
            "json_chars": 100,
            "estimated_tokens": 25,
            "threshold": None,
            "largest_paths": [],
        }

    monkeypatch.setattr(api_module, "provider_context_preflight", _capture_preflight)

    response = asyncio.run(
        api_module.chat_with_opus(
            request=_chat_request(),
            user={"email": "curator@example.org", "sub": "auth-sub"},
        )
    )
    asyncio.run(_consume_stream(response))

    assert captured["payload"]["instructions"] == "system prompt"
    assert captured["payload"]["input"] == [
        {"role": "user", "content": "Please help"}
    ]
    assert captured["payload"]["tools"] == [tool_definition]
    assert captured["payload"]["tool_search"] == {
        "forced_tool_name": None,
        "candidate_count": 1,
        "eager_count": 0,
        "deferred_count": 1,
        "namespace_count": 1,
    }
