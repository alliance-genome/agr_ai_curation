"""Offline regressions for the captured ordinary-chat provider refusal."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from agents import UserError
from agents.models.openai_responses import ResponsesWebSocketError
from agents.tool import default_tool_error_function

from src.lib.openai_agents import provider_errors
from src.lib.openai_agents.agents import supervisor_agent
from src.lib.observability.tool_results import classify_tool_result


def policy_error(code="bio_policy", error_type="invalid_request_error", wrapped=True):
    raw = ResponsesWebSocketError({
        "type": "error",
        "error": {"type": error_type, "code": code,
                  "message": "PRIVATE PROVIDER PAYLOAD", "param": None},
    })
    if not wrapped:
        return raw
    wrapper = UserError("SDK wrapper PRIVATE PROVIDER PAYLOAD")
    wrapper.__cause__ = raw
    return wrapper


@pytest.mark.asyncio
@pytest.mark.parametrize("wrapped", [False, True])
async def test_sdk_caught_policy_result_is_safe_and_reported_once(monkeypatch, wrapped):
    error = policy_error(wrapped=wrapped)
    report = Mock(return_value=True)
    monkeypatch.setattr(provider_errors, "report_runtime_exception", report)

    async def fail(**kwargs):
        raise error

    monkeypatch.setattr(supervisor_agent, "run_specialist_with_events", fail)
    tool = supervisor_agent._create_streaming_tool(
        agent=SimpleNamespace(name="PDF Specialist"),
        tool_name="ask_pdf_extraction_specialist", tool_description="Extract",
        specialist_name="PDF Specialist", propagate_errors=False,
    )
    output = await tool.on_invoke_tool(
        SimpleNamespace(tool_name=tool.name, run_config=None), json.dumps({"query": "extract"}),
    )
    payload = json.loads(output)
    assert payload == provider_errors.chat_policy_failure()
    assert payload["retryable"] is False
    assert "safety check" in payload["message"]
    assert "No finalized extraction or TSV" in payload["message"]
    assert "Please try again" not in output
    assert "PRIVATE" not in output
    assert not classify_tool_result(output).success
    # A later wrapper must not generate a second terminal report.
    provider_errors.report_chat_policy_failure(error, tool_name=tool.name, trace_id=None, session_id=None)
    report.assert_called_once()
    reported = report.call_args.args[0]
    assert reported.__cause__ is None and reported.__context__ is None
    assert "PRIVATE" not in str(reported)
    assert report.call_args.kwargs["tags"]["failure_category"] == "provider_content_policy"
    assert report.call_args.kwargs["context"] == {"error_code": "bio_policy", "retryable": False}


@pytest.mark.asyncio
@pytest.mark.parametrize("code,error_type", [
    ("context_length_exceeded", "invalid_request_error"), (None, "server_error"),
])
async def test_unrelated_sdk_tool_errors_keep_default_behavior(monkeypatch, code, error_type):
    error = policy_error(code, error_type)
    report = Mock()
    monkeypatch.setattr(provider_errors, "report_runtime_exception", report)

    async def fail(**kwargs):
        raise error

    monkeypatch.setattr(supervisor_agent, "run_specialist_with_events", fail)
    tool = supervisor_agent._create_streaming_tool(
        agent=SimpleNamespace(name="PDF Specialist"), tool_name="ask_pdf_extraction_specialist",
        tool_description="Extract", specialist_name="PDF Specialist", propagate_errors=False,
    )
    ctx = SimpleNamespace(tool_name=tool.name, run_config=None)
    assert await tool.on_invoke_tool(ctx, '{"query":"extract"}') == default_tool_error_function(ctx, error)
    report.assert_not_called()


def test_policy_classifier_handles_context_cycles_without_text_matching():
    error = RuntimeError("bio_policy")
    error.__context__ = error
    assert provider_errors.provider_policy_error(error) is None
    error.__context__ = policy_error(wrapped=False)
    assert provider_errors.provider_policy_error(error) is error.__context__


def test_sdk_policy_report_serialization_and_duplicate_guard():
    import subprocess
    import sys
    import textwrap

    script = textwrap.dedent('''
        import asyncio
        import json
        import logging
        from types import SimpleNamespace
        import sentry_sdk
        from sentry_sdk.integrations.logging import LoggingIntegration
        from agents.models.openai_responses import ResponsesWebSocketError
        from src.lib.observability import sentry
        from src.lib.openai_agents.agents import supervisor_agent
        from src.lib.openai_agents import provider_errors

        events = []
        sentry_sdk.init(dsn="http://public@example.invalid/1", transport=events.append,
            before_send=sentry.before_send, include_local_variables=False,
            integrations=[LoggingIntegration(level=logging.INFO, event_level=logging.ERROR)],
            default_integrations=False)
        error = ResponsesWebSocketError({"type": "error", "error": {
            "type": "invalid_request_error", "code": "bio_policy",
            "message": "PRIVATE PROVIDER PAYLOAD", "param": None}})
        async def fail(**kwargs):
            raise error
        supervisor_agent.run_specialist_with_events = fail
        tool = supervisor_agent._create_streaming_tool(
            agent=SimpleNamespace(name="PDF Specialist"), tool_name="ask_pdf_extraction_specialist",
            tool_description="Extract", specialist_name="PDF Specialist", propagate_errors=False)
        output = asyncio.run(tool.on_invoke_tool(
            SimpleNamespace(tool_name=tool.name, run_config=None), '{"query":"extract"}'))
        assert json.loads(output)["retryable"] is False
        # SDK/log re-reporting of the same exception is suppressed by the shared guard.
        logging.getLogger("openai.agents").error("Repeated provider failure: %s", error)
        provider_errors.report_chat_policy_failure(error, tool_name=tool.name, trace_id=None, session_id=None)
        sentry_sdk.flush(timeout=1)
        assert len(events) == 1, events
        event = events[0]
        assert event["tags"]["provider"] == "openai"
        assert event["tags"]["failure_category"] == "provider_content_policy"
        assert event["contexts"]["runtime_exception"]["error_code"] == "bio_policy"
        assert event["contexts"]["runtime_exception"]["retryable"] is False
        assert "PRIVATE" not in repr(event), event
    ''')
    result = subprocess.run([sys.executable, "-c", script], text=True, capture_output=True)
    assert result.returncode == 0, result.stderr + result.stdout
