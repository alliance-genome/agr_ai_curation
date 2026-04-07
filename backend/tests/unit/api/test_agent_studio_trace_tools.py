"""Unit tests for Agent Studio trace/tool helper functions."""

import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import src.api.agent_studio as api_module
from src.api import logs as logs_api
from src.lib.agent_studio.models import ChatContext


def _install_langfuse(monkeypatch, trace_obj=None, observations=None, raise_on_init=False):
    module = types.ModuleType("langfuse")

    class _Langfuse:
        def __init__(self):
            if raise_on_init:
                raise RuntimeError("langfuse init failed")
            self.api = SimpleNamespace(
                trace=SimpleNamespace(get=lambda _trace_id: trace_obj),
                observations=SimpleNamespace(
                    get_many=lambda **_kwargs: SimpleNamespace(data=observations or [])
                ),
            )

    module.Langfuse = _Langfuse
    monkeypatch.setitem(sys.modules, "langfuse", module)


def test_send_error_notification_sns_skips_when_disabled(monkeypatch):
    monkeypatch.setenv("PROMPT_SUGGESTIONS_USE_SNS", "false")
    monkeypatch.delenv("PROMPT_SUGGESTIONS_SNS_TOPIC_ARN", raising=False)
    monkeypatch.setattr(api_module.boto3, "client", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not call boto3.client")))

    api_module._send_error_notification_sns("curator@example.org", "failed")


def test_send_error_notification_sns_uses_profile_session(monkeypatch):
    publish_client = MagicMock()
    publish_client.publish.return_value = {"MessageId": "msg-123"}
    fake_session = MagicMock()
    fake_session.client.return_value = publish_client

    monkeypatch.setenv("PROMPT_SUGGESTIONS_USE_SNS", "true")
    monkeypatch.setenv("PROMPT_SUGGESTIONS_SNS_TOPIC_ARN", "arn:aws:sns:us-east-1:123456789012:topic")
    monkeypatch.setenv("AWS_PROFILE", "ctabone")
    monkeypatch.setenv("SNS_REGION", "us-west-2")
    monkeypatch.setattr(api_module.boto3, "Session", lambda profile_name: fake_session)

    context = ChatContext(trace_id="trace-1", selected_agent_id="gene")
    api_module._send_error_notification_sns("curator@example.org", "backend failed", context)

    publish_client.publish.assert_called_once()
    kwargs = publish_client.publish.call_args.kwargs
    assert kwargs["TopicArn"].endswith(":topic")
    assert kwargs["MessageAttributes"]["type"]["StringValue"] == "submission_error"
    assert "Trace ID: trace-1" in kwargs["Message"]


def test_send_error_notification_sns_swallows_publish_errors(monkeypatch):
    publish_client = MagicMock()
    publish_client.publish.side_effect = RuntimeError("publish failed")
    monkeypatch.setenv("PROMPT_SUGGESTIONS_USE_SNS", "true")
    monkeypatch.setenv("PROMPT_SUGGESTIONS_SNS_TOPIC_ARN", "arn:aws:sns:us-east-1:123:topic")
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.setattr(api_module.boto3, "client", lambda *_args, **_kwargs: publish_client)

    api_module._send_error_notification_sns("curator@example.org", "backend failed")


@pytest.mark.asyncio
async def test_handle_tool_call_trace_summary_missing_trace_id():
    result = await api_module._handle_tool_call(
        tool_name="get_trace_summary",
        tool_input={},
        context=None,
        user_email="dev@example.org",
        messages=[],
    )
    assert result["status"] == "error"
    assert "trace_id" in result["error"]


@pytest.mark.asyncio
async def test_handle_tool_call_get_tool_calls_page_forwards_inputs(monkeypatch):
    from src.lib.agent_studio import tools as tools_module

    async def _fake_page(**kwargs):
        return {"status": "ok", "kwargs": kwargs}

    monkeypatch.setattr(tools_module, "get_tool_calls_page", _fake_page)

    result = await api_module._handle_tool_call(
        tool_name="get_tool_calls_page",
        tool_input={"trace_id": "trace-1", "page": 2, "page_size": 25, "tool_name": "read_section"},
        context=None,
        user_email="dev@example.org",
        messages=[],
    )

    assert result["status"] == "ok"
    assert result["kwargs"]["trace_id"] == "trace-1"
    assert result["kwargs"]["page"] == 2
    assert result["kwargs"]["page_size"] == 25
    assert result["kwargs"]["tool_name"] == "read_section"


def test_get_service_logs_tool_schema_matches_logs_api_contract():
    schema = api_module.GET_SERVICE_LOGS_TOOL["input_schema"]["properties"]

    assert schema["container"]["enum"] == sorted(logs_api.ALLOWED_CONTAINERS)
    assert schema["level"]["enum"] == sorted(logs_api.ALLOWED_LOG_LEVELS)
    assert schema["since"]["type"] == "integer"
    assert schema["since"]["minimum"] == 1
    assert "minutes ago" in schema["since"]["description"]


@pytest.mark.asyncio
async def test_handle_tool_call_get_service_logs_forwards_inputs(monkeypatch):
    from src.lib.agent_studio import tools as tools_module

    async def _fake_get_service_logs(**kwargs):
        return {"status": "ok", "kwargs": kwargs}

    monkeypatch.setattr(tools_module, "get_service_logs", _fake_get_service_logs)

    result = await api_module._handle_tool_call(
        tool_name="get_service_logs",
        tool_input={"container": "backend", "lines": 250, "level": "FATAL", "since": 30},
        context=None,
        user_email="dev@example.org",
        messages=[],
    )

    assert result["status"] == "ok"
    assert result["kwargs"] == {
        "container": "backend",
        "lines": 250,
        "level": "FATAL",
        "since": 30,
    }


@pytest.mark.asyncio
async def test_handle_tool_call_get_docker_logs_is_unknown():
    result = await api_module._handle_tool_call(
        tool_name="get_docker_logs",
        tool_input={"container": "backend"},
        context=None,
        user_email="dev@example.org",
        messages=[],
    )

    assert result["success"] is False
    assert result["error"] == "Unknown tool: get_docker_logs"


@pytest.mark.asyncio
async def test_handle_tool_call_get_tool_call_detail_requires_call_id():
    result = await api_module._handle_tool_call(
        tool_name="get_tool_call_detail",
        tool_input={"trace_id": "trace-1"},
        context=None,
        user_email="dev@example.org",
        messages=[],
    )
    assert result["status"] == "error"
    assert "call_id" in result["error"]


@pytest.mark.asyncio
async def test_handle_tool_call_submit_prompt_suggestion_invalid_type():
    result = await api_module._handle_tool_call(
        tool_name="submit_prompt_suggestion",
        tool_input={
            "suggestion_type": "not-a-type",
            "summary": "summary",
            "detailed_reasoning": "details",
        },
        context=None,
        user_email="dev@example.org",
        messages=[],
    )
    assert result["success"] is False
    assert "Invalid suggestion_type" in result["error"]


@pytest.mark.asyncio
async def test_handle_tool_call_submit_prompt_suggestion_reports_sns_failed(monkeypatch):
    monkeypatch.setattr(api_module, "_format_conversation_context", lambda _messages: "conversation")

    async def _fake_submit_suggestion_sns(**_kwargs):
        return {"suggestion_id": "s-123", "sns_status": "failed"}

    monkeypatch.setattr(api_module, "submit_suggestion_sns", _fake_submit_suggestion_sns)

    result = await api_module._handle_tool_call(
        tool_name="submit_prompt_suggestion",
        tool_input={
            "suggestion_type": "improvement",
            "summary": "Improve prompt",
            "detailed_reasoning": "Needs better constraints",
            "proposed_change": "Add explicit rule",
        },
        context=ChatContext(trace_id="trace-1", selected_group_id="WB"),
        user_email="dev@example.org",
        messages=[{"role": "user", "content": "help"}],
    )

    assert result["success"] is True
    assert result["suggestion_id"] == "s-123"
    assert result["sns_failed"] is True


def test_fetch_trace_for_opus_returns_none_when_trace_missing(monkeypatch):
    _install_langfuse(monkeypatch, trace_obj=None, observations=[])
    assert api_module._fetch_trace_for_opus("trace-1") is None


def test_fetch_trace_for_opus_formats_trace_and_tool_calls(monkeypatch):
    trace = SimpleNamespace(
        input={"message": "What changed?"},
        output={"response": "x" * 2200},
    )
    observations = [
        SimpleNamespace(type="GENERATION", name="gene_extractor_run"),
        SimpleNamespace(
            type="SPAN",
            name="read_section",
            input={"section": "Results"},
            output="found evidence",
            metadata=SimpleNamespace(distance=0.11),
        ),
    ]
    _install_langfuse(monkeypatch, trace_obj=trace, observations=observations)

    rendered = api_module._fetch_trace_for_opus("trace-abc")

    assert rendered is not None
    assert "**Trace ID:** trace-abc" in rendered
    assert "**User Query:** What changed?" in rendered
    assert "Final Response" in rendered
    assert "... [truncated]" in rendered
    assert "Agents Involved" in rendered
    assert "Tool Calls" in rendered
    assert "read_section" in rendered


def test_fetch_trace_for_opus_returns_none_on_exception(monkeypatch):
    _install_langfuse(monkeypatch, raise_on_init=True)
    assert api_module._fetch_trace_for_opus("trace-1") is None
