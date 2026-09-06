"""Unit tests for Agent Studio trace/tool helper functions."""

import sys
import types
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
import src.api.agent_studio as api_module
from src.api import logs as logs_api
from src.lib.agent_studio.models import ChatContext
from src.lib.openai_agents.config import get_agent_studio_trace_review_summary_max_chars


def _chat_context(**overrides: Any) -> ChatContext:
    return ChatContext(
        selected_agent_id=overrides.get("selected_agent_id"),
        selected_group_id=overrides.get("selected_group_id"),
        trace_id=overrides.get("trace_id"),
        session_id=overrides.get("session_id"),
        view_mode=overrides.get("view_mode", "base"),
        active_tab=overrides.get("active_tab"),
        flow_name=overrides.get("flow_name"),
        flow_definition=overrides.get("flow_definition"),
        agent_workshop=overrides.get("agent_workshop"),
    )


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

    setattr(module, "Langfuse", _Langfuse)
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
    monkeypatch.setenv("AWS_PROFILE", "developer")
    monkeypatch.setenv("SNS_REGION", "us-west-2")
    monkeypatch.setattr(api_module.boto3, "Session", lambda profile_name: fake_session)

    context = _chat_context(trace_id="trace-1", selected_agent_id="gene")
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
        user_auth_sub="auth-sub-1",
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
        user_auth_sub="auth-sub-1",
        messages=[],
    )

    assert result["status"] == "ok"
    assert result["kwargs"]["trace_id"] == "trace-1"
    assert result["kwargs"]["page"] == 2
    assert result["kwargs"]["page_size"] == 25
    assert result["kwargs"]["tool_name"] == "read_section"


@pytest.mark.asyncio
async def test_handle_tool_call_search_traces_forwards_continuation(monkeypatch):
    from src.lib.agent_studio import tools as tools_module

    async def _fake_search(**kwargs):
        return {"status": "ok", "kwargs": kwargs}

    monkeypatch.setattr(tools_module, "search_traces", _fake_search)
    tool_input = {
        "session_id": "session-1",
        "user_id": "attacker-controlled-user",
        "name": "trace-name",
        "document_id": "doc-1",
        "run_id": "run-1",
        "extraction_id": "extraction-1",
        "from_timestamp": "2026-01-01T00:00:00Z",
        "to_timestamp": "2026-02-01T00:00:00Z",
        "offset": 12,
        "limit": 25,
        "item_start": 345,
    }
    result = await api_module._handle_tool_call(
        tool_name="search_traces",
        tool_input=tool_input,
        context=None,
        user_email="dev@example.org",
        user_auth_sub="auth-sub-1",
        messages=[],
    )

    expected = {key: value for key, value in tool_input.items() if key != "user_id"}
    assert result == {"status": "ok", "kwargs": expected}


def test_search_traces_schema_does_not_expose_user_scope():
    properties = api_module.SEARCH_TRACES_TOOL["input_schema"]["properties"]
    assert "user_id" not in properties


def test_get_service_logs_tool_schema_matches_logs_api_contract():
    schema = api_module.GET_SERVICE_LOGS_TOOL["input_schema"]["properties"]

    assert schema["container"]["enum"] == sorted(logs_api.ALLOWED_CONTAINERS)
    assert schema["level"]["enum"] == sorted(logs_api.ALLOWED_LOG_LEVELS)
    assert schema["since"]["type"] == "integer"
    assert schema["since"]["minimum"] == 1
    assert "minutes ago" in schema["since"]["description"]


def test_langfuse_trace_tools_are_registered_and_trace_scoped():
    agents_tools = api_module._get_all_opus_tools(_chat_context(active_tab="agents"))
    flows_tools = api_module._get_all_opus_tools(_chat_context(active_tab="flows"))
    trace_tools = api_module._get_all_opus_tools(_chat_context(active_tab="flows", trace_id="trace-1"))

    agents_tool_names = {tool["name"] for tool in agents_tools}
    flows_tool_names = {tool["name"] for tool in flows_tools}
    trace_tool_names = {tool["name"] for tool in trace_tools}

    expected = {
        "search_traces",
        "get_extraction_diagnostic_report",
        "get_extraction_timeline",
        "get_evidence_revisions",
        "get_trace_tree",
        "get_trace_reconstruction",
        "get_trace_payloads",
        "get_trace_payload",
        "get_trace_costs",
        "get_trace_duplicates",
    }
    assert expected <= agents_tool_names
    assert expected.isdisjoint(flows_tool_names)
    assert expected <= trace_tool_names

    tools_by_name = {tool["name"]: tool for tool in agents_tools}
    assert tools_by_name["get_trace_payload"]["input_schema"]["properties"]["field"]["enum"] == [
        "input",
        "output",
        "metadata.agent_config",
        "metadata.event_payload",
    ]
    assert "include_values" not in tools_by_name["get_trace_payloads"]["input_schema"]["properties"]
    assert "include_payloads" not in tools_by_name["get_trace_reconstruction"]["input_schema"]["properties"]
    assert tools_by_name["get_tool_call_detail"]["input_schema"]["required"] == [
        "trace_id",
        "call_id",
        "field",
    ]
    assert tools_by_name["get_tool_call_detail"]["input_schema"]["properties"]["field"]["enum"] == [
        "input",
        "tool_result",
        "thought",
        "metadata",
        "domain_envelope",
    ]
    assert tools_by_name["get_trace_conversation"]["input_schema"]["required"] == [
        "trace_id",
        "field",
    ]
    payload_chunk_schema = tools_by_name["get_trace_payload"]["input_schema"]["properties"]["max_chars"]
    assert payload_chunk_schema["minimum"] == 1
    assert payload_chunk_schema["default"] == payload_chunk_schema["maximum"]
    assert payload_chunk_schema["default"] < api_module.get_agent_studio_provider_tool_result_inline_max_chars()
    assert (
        tools_by_name["get_trace_payloads"]["input_schema"]["properties"]["limit"]["maximum"]
        == api_module.get_agent_studio_trace_review_aggregate_page_size()
    )
    assert "extraction_timeline" in tools_by_name["get_trace_view"]["input_schema"]["properties"]["view_name"]["enum"]
    assert "evidence_revisions" in tools_by_name["get_trace_view"]["input_schema"]["properties"]["view_name"]["enum"]
    assert "tool_calls" in tools_by_name["get_trace_view"]["input_schema"]["properties"]["view_name"]["enum"]
    assert "group_context" in tools_by_name["get_trace_view"]["input_schema"]["properties"]["view_name"]["enum"]
    assert "mod_context" not in tools_by_name["get_trace_view"]["input_schema"]["properties"]["view_name"]["enum"]
    search_schema = tools_by_name["search_traces"]["input_schema"]["properties"]
    assert search_schema["limit"]["default"] == 25
    assert search_schema["limit"]["maximum"] == 100
    assert search_schema["session_id"]["maxLength"] == 256
    assert {"offset", "item_start"} <= search_schema.keys()
    assert "item_offset" in tools_by_name["get_tool_calls_summary"]["input_schema"]["properties"]
    assert "item_offset" in tools_by_name["get_tool_calls_page"]["input_schema"]["properties"]
    assert (
        tools_by_name["get_tool_calls_page"]["input_schema"]["properties"]["tool_name"]["maxLength"]
        == get_agent_studio_trace_review_summary_max_chars()
    )


@pytest.mark.parametrize("tab", ["agents", "flows", "agent_workshop"])
def test_source_inspection_is_available_while_authoring(tab):
    context = _chat_context(active_tab=tab)
    for name in ("search_codebase", "read_source_file"):
        assert api_module._is_tool_allowed_for_context(name, context) is True
    tools = {tool["name"] for tool in api_module._get_all_opus_tools(context)}
    assert {"search_codebase", "read_source_file"} <= tools


def test_every_registered_aggregate_trace_and_log_tool_exposes_bounded_continuation():
    tools_by_name = {
        tool["name"]: tool
        for tool in api_module._get_all_opus_tools(_chat_context(active_tab="agents"))
    }
    aggregate_trace_tools = {
        "get_extraction_diagnostic_report",
        "get_extraction_timeline",
        "get_evidence_revisions",
        "get_trace_tree",
        "get_trace_view",
        "get_trace_model_live_context",
        "get_trace_costs",
        "get_trace_duplicates",
        "get_trace_reconstruction",
        "get_trace_payloads",
    }
    for tool_name in aggregate_trace_tools:
        properties = tools_by_name[tool_name]["input_schema"]["properties"]
        assert {"section", "offset", "limit", "item_start"} <= properties.keys()
        assert properties["limit"]["maximum"] == api_module.get_agent_studio_trace_review_aggregate_page_size()

    log_properties = tools_by_name["get_service_logs"]["input_schema"]["properties"]
    assert {"line_cursor", "line_cursor_offset", "char_cursor"} <= log_properties.keys()
    assert log_properties["lines"]["default"] == api_module.get_agent_studio_service_log_default_lines()


@pytest.mark.asyncio
async def test_handle_tool_call_get_service_logs_forwards_inputs(monkeypatch):
    from src.lib.agent_studio import tools as tools_module

    async def _fake_get_service_logs(**kwargs):
        return {"status": "ok", "kwargs": kwargs}

    monkeypatch.setattr(tools_module, "get_service_logs", _fake_get_service_logs)

    result = await api_module._handle_tool_call(
        tool_name="get_service_logs",
        tool_input={
            "container": "backend",
            "lines": 250,
            "level": "FATAL",
            "since": 30,
            "line_cursor_offset": 4,
        },
        context=None,
        user_email="dev@example.org",
        user_auth_sub="auth-sub-1",
        messages=[],
    )

    assert result["status"] == "ok"
    assert result["kwargs"] == {
        "container": "backend",
        "lines": 250,
        "level": "FATAL",
        "since": 30,
        "line_cursor": None,
        "line_cursor_offset": 4,
        "char_cursor": 0,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "function_name"),
    [
        ("get_tool_calls_summary", "get_tool_calls_summary"),
        ("get_tool_calls_page", "get_tool_calls_page"),
    ],
)
async def test_handle_tool_call_forwards_tool_call_page_continuation(
    monkeypatch,
    tool_name,
    function_name,
):
    from src.lib.agent_studio import tools as tools_module

    captured = {}

    async def _fake_page(**kwargs):
        captured.update(kwargs)
        return {"status": "ok"}

    monkeypatch.setattr(tools_module, function_name, _fake_page)
    result = await api_module._handle_tool_call(
        tool_name=tool_name,
        tool_input={"trace_id": "trace-1", "page": 2, "page_size": 10, "item_offset": 4},
        context=None,
        user_email="dev@example.org",
        user_auth_sub="auth-sub-1",
        messages=[],
    )

    assert result == {"status": "ok"}
    assert captured["item_offset"] == 4


@pytest.mark.asyncio
async def test_handle_tool_call_new_trace_tools_forward_inputs(monkeypatch):
    from src.lib.agent_studio import tools as tools_module

    captured: dict[str, dict] = {}

    async def _fake_report(**kwargs):
        captured["report"] = kwargs
        return {"status": "ok", "tool": "report"}

    async def _fake_reconstruction(**kwargs):
        captured["reconstruction"] = kwargs
        return {"status": "ok", "tool": "reconstruction"}

    async def _fake_evidence_revisions(**kwargs):
        captured["evidence_revisions"] = kwargs
        return {"status": "ok", "tool": "evidence_revisions"}

    async def _fake_payload(**kwargs):
        captured["payload"] = kwargs
        return {"status": "ok", "tool": "payload"}

    async def _fake_view(**kwargs):
        captured["view"] = kwargs
        return {"status": "ok", "tool": "view"}

    monkeypatch.setattr(tools_module, "get_extraction_diagnostic_report", _fake_report)
    monkeypatch.setattr(tools_module, "get_evidence_revisions", _fake_evidence_revisions)
    monkeypatch.setattr(tools_module, "get_trace_reconstruction", _fake_reconstruction)
    monkeypatch.setattr(tools_module, "get_trace_payload", _fake_payload)
    monkeypatch.setattr(tools_module, "get_trace_view", _fake_view)

    report = await api_module._handle_tool_call(
        tool_name="get_extraction_diagnostic_report",
        tool_input={
            "trace_id": "trace-1",
            "session_id": "session-1",
            "include_sibling_traces": True,
            "include_raw_args": True,
            "tool_name": "stage",
        },
        context=None,
        user_email="dev@example.org",
        user_auth_sub="auth-sub-1",
        messages=[],
    )
    assert report["tool"] == "report"
    assert captured["report"]["trace_id"] == "trace-1"
    assert captured["report"]["session_id"] == "session-1"
    assert captured["report"]["include_sibling_traces"] is True
    assert captured["report"]["include_raw_args"] is True
    assert captured["report"]["tool_name"] == "stage"

    evidence_revisions = await api_module._handle_tool_call(
        tool_name="get_evidence_revisions",
        tool_input={
            "trace_id": "trace-1",
            "session_id": "session-1",
            "feedback_id": "feedback-1",
            "include_sibling_traces": True,
            "refresh": True,
            "tool_name": "record_evidence",
            "event_type": "evidence.summary",
            "candidate_id": "candidate-1",
        },
        context=None,
        user_email="dev@example.org",
        user_auth_sub="auth-sub-1",
        messages=[],
    )
    assert evidence_revisions["tool"] == "evidence_revisions"
    assert captured["evidence_revisions"] == {
        "trace_id": "trace-1",
        "session_id": "session-1",
        "feedback_id": "feedback-1",
        "include_sibling_traces": True,
        "refresh": True,
        "tool_name": "record_evidence",
        "event_type": "evidence.summary",
        "candidate_id": "candidate-1",
        "section": None,
        "offset": 0,
        "limit": None,
        "item_start": 0,
    }

    reconstruction = await api_module._handle_tool_call(
        tool_name="get_trace_reconstruction",
        tool_input={"trace_id": "trace-1", "limit": 5, "offset": 10},
        context=None,
        user_email="dev@example.org",
        user_auth_sub="auth-sub-1",
        messages=[],
    )
    assert reconstruction["tool"] == "reconstruction"
    assert captured["reconstruction"] == {
        "trace_id": "trace-1",
        "limit": 5,
        "offset": 10,
        "section": None,
        "item_start": 0,
    }

    payload = await api_module._handle_tool_call(
        tool_name="get_trace_payload",
        tool_input={
            "trace_id": "trace-1",
            "payload_id": "observation:obs-1:output",
            "start": 12,
            "max_chars": 120,
        },
        context=None,
        user_email="dev@example.org",
        user_auth_sub="auth-sub-1",
        messages=[],
    )
    assert payload["tool"] == "payload"
    assert captured["payload"]["payload_id"] == "observation:obs-1:output"
    assert captured["payload"]["start"] == 12
    assert captured["payload"]["max_chars"] == 120

    view = await api_module._handle_tool_call(
        tool_name="get_trace_view",
        tool_input={
            "trace_id": "trace-1",
            "view_name": "agent_configs",
            "section": "agents",
            "offset": 2,
            "limit": 1,
            "item_start": 9000,
        },
        context=None,
        user_email="dev@example.org",
        user_auth_sub="auth-sub-1",
        messages=[],
    )
    assert view["tool"] == "view"
    assert captured["view"] == {
        "trace_id": "trace-1",
        "view_name": "agent_configs",
        "section": "agents",
        "offset": 2,
        "limit": 1,
        "item_start": 9000,
    }


@pytest.mark.asyncio
async def test_handle_tool_call_get_docker_logs_is_unknown():
    result = await api_module._handle_tool_call(
        tool_name="get_docker_logs",
        tool_input={"container": "backend"},
        context=None,
        user_email="dev@example.org",
        user_auth_sub="auth-sub-1",
        messages=[],
    )

    assert result["success"] is False
    assert result["error"] == "Unknown tool: get_docker_logs"


@pytest.mark.asyncio
async def test_handle_tool_call_get_tool_call_detail_requires_call_id():
    result = await api_module._handle_tool_call(
        tool_name="get_tool_call_detail",
        tool_input={"trace_id": "trace-1", "field": "input"},
        context=None,
        user_email="dev@example.org",
        user_auth_sub="auth-sub-1",
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
        user_auth_sub="auth-sub-1",
        messages=[],
    )
    assert result["success"] is False
    assert "Invalid suggestion_type" in result["error"]


@pytest.mark.asyncio
async def test_handle_tool_call_submit_prompt_suggestion_returns_clear_failure(monkeypatch):
    monkeypatch.setattr(api_module, "_format_conversation_context", lambda _messages: "conversation")

    async def _fake_submit_suggestion_sns(**_kwargs):
        return {
            "status": "failed",
            "sns_status": "failed",
            "message": "Suggestion submission failed because prompt suggestion delivery is temporarily unavailable. Please try again.",
        }

    monkeypatch.setattr(api_module, "submit_suggestion_sns", _fake_submit_suggestion_sns)

    result = await api_module._handle_tool_call(
        tool_name="submit_prompt_suggestion",
        tool_input={
            "suggestion_type": "improvement",
            "summary": "Improve prompt",
            "detailed_reasoning": "Needs better constraints",
            "proposed_change": "Add explicit rule",
        },
        context=_chat_context(trace_id="trace-1", selected_group_id="WB"),
        user_email="dev@example.org",
        user_auth_sub="auth-sub-1",
        messages=[{"role": "user", "content": "help"}],
    )

    assert result["success"] is False
    assert result["error"] == "Suggestion submission failed because prompt suggestion delivery is temporarily unavailable. Please try again."
