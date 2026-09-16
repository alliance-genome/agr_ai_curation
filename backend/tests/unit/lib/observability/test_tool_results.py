"""Returned-result success, capture eligibility, correlation and chain ownership."""

import json
from unittest.mock import Mock

import pytest

from src.lib.observability import tool_results, runtime


@pytest.mark.parametrize(
    "payload,studio,success,code",
    [
        ({"success": False, "code": "unavailable_agent"}, True, False, None),
        ({"status": "error", "error": "invalid_regex"}, True, False, None),
        (
            {
                "status": "error",
                "lookup_status": "blocked",
                "failure_classification": "validation_failed",
            },
            False,
            False,
            None,
        ),
        (
            {
                "status": "success",
                "lookup_status": "not_found",
                "failure_classification": "not_found",
                "data": [],
            },
            False,
            True,
            None,
        ),
        (
            {
                "status": "error",
                "lookup_status": "transient",
                "failure_classification": "transient",
            },
            False,
            False,
            "lookup_dependency_failure",
        ),
        (
            {
                "success": False,
                "code": "flow_authoring_compile_failed",
                "failure_kind": "operational",
            },
            True,
            False,
            "flow_authoring_compile_failed",
        ),
        (
            {
                "success": False,
                "code": "authoring_validation_engine_failure",
                "failure_kind": "operational",
            },
            True,
            False,
            None,
        ),
        ({"status": "error", "data": {"success": False}}, False, True, None),
        ("ERROR timeout secret arbitrary text", False, True, None),
    ],
)
def test_contract_matrix(payload, studio, success, code):
    result = tool_results.classify_tool_result(payload, studio=studio)
    assert (result.success, result.operational_code) == (success, code)
    if isinstance(payload, dict):
        assert (
            tool_results.classify_tool_result(json.dumps(payload), studio=studio)
            == result
        )


@pytest.mark.asyncio
async def test_studio_automatic_explicit_and_linked_terminal_capture_once(monkeypatch):
    from src.api import agent_studio
    from src.lib.agent_studio.models import ChatContext

    capture = Mock(return_value=True)
    monkeypatch.setattr(tool_results, "report_runtime_exception", capture)

    async def execute(*args, **kwargs):
        return {
            "success": False,
            "code": "flow_authoring_compile_failed",
            "failure_kind": "operational",
        }

    monkeypatch.setattr(agent_studio, "_execute_tool_call", execute)
    state = tool_results.ToolFailureState()
    args = dict(
        context=ChatContext.model_validate({"trace_id": "older-inspected-trace"}),
        user_email="private@example.org",
        user_auth_sub="private-sub",
        failure_state=state,
        runtime_trace_id="current-studio-trace",
        runtime_session_id="current-studio-session",
    )
    first = await agent_studio._handle_tool_call(
        "propose_flow_draft_update", {}, tool_call_id="call-1", **args
    )
    assert first["success"] is False and first["sentry_capture_queued"] is True
    repeated = await agent_studio._handle_tool_call(
        "report_tool_failure", {"failure_id": first["failure_id"]}, **args
    )
    assert repeated["already_reported"] is True
    assert repeated["notification_submitted"] is False
    capture.assert_called_once()
    serialized = str(capture.call_args)
    assert (
        "current-studio-trace" not in serialized
        and "private@example.org" not in serialized
    )
    assert capture.call_args.kwargs["tags"][
        "ai_curation.trace.id_hash"
    ] == tool_results.hash_sentry_identifier("current-studio-trace")
    # Suppress only an explicit cause link to the same captured failure object.
    terminal = RuntimeError("terminal")
    terminal.__cause__ = state.failures[first["failure_id"]][0]
    sdk = Mock()
    monkeypatch.setattr(runtime.importlib, "import_module", lambda _: sdk)
    assert (
        runtime.report_runtime_exception(
            terminal, component="test", operation="terminal"
        )
        is False
    )
    sdk.capture_exception.assert_not_called()
    await agent_studio._handle_tool_call(
        "propose_flow_draft_update", {}, tool_call_id="call-2", **args
    )
    assert capture.call_count == 2


def test_reporter_failure_does_not_mark_chain_captured(monkeypatch):
    monkeypatch.setattr(
        tool_results,
        "report_runtime_exception",
        Mock(side_effect=RuntimeError("offline")),
    )
    state = tool_results.ToolFailureState()
    identity, captured = state.capture(
        code="flow_authoring_compile_failed",
        tool_name="propose_flow_draft_update",
        invocation_id="one",
        trace_id=None,
        session_id=None,
    )
    assert not captured
    assert not getattr(
        state.failures[identity][0], "_ai_curation_sentry_captured", False
    )


@pytest.mark.asyncio
async def test_engine_capture_receipt_is_reused_by_explicit_report(monkeypatch):
    from src.api import agent_studio
    from src.lib.agent_studio import flow_tools

    capture = Mock(return_value=True)
    monkeypatch.setattr(runtime, "report_runtime_exception", capture)
    monkeypatch.setattr(
        tool_results,
        "report_runtime_exception",
        Mock(side_effect=AssertionError("duplicate")),
    )
    monkeypatch.setattr(
        flow_tools,
        "_validate_exact_flow_for_current_user",
        Mock(side_effect=RuntimeError("private")),
    )

    async def execute(*args, **kwargs):
        return flow_tools._validate_flow_handler()(flow_definition={})

    monkeypatch.setattr(agent_studio, "_execute_tool_call", execute)
    state = tool_results.ToolFailureState()
    args = dict(
        context=None,
        user_email="private@example.org",
        user_auth_sub="user",
        failure_state=state,
    )
    result = await agent_studio._handle_tool_call("validate_flow", {}, **args)
    assert result["success"] is False
    explicit = await agent_studio._handle_tool_call(
        "report_tool_failure", {"failure_id": result["failure_id"]}, **args
    )
    assert explicit["already_reported"] is True
    capture.assert_called_once()


def test_real_agr_error_log_and_returned_result_have_one_owner():
    import subprocess
    import sys
    import textwrap

    script = textwrap.dedent("""
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path.cwd().parent / "packages/alliance/python/src"))
        sys.path.insert(0, str(Path.cwd() / "src"))
        import logging
        import sentry_sdk
        from sentry_sdk.integrations.logging import LoggingIntegration
        from src.lib.observability import sentry, tool_results
        from agr_ai_curation_alliance.tools import agr_curation as agr
        events = []
        sentry_sdk.init(dsn="http://public@example.invalid/1", transport=events.append,
            before_send=sentry.before_send, include_local_variables=False,
            integrations=[LoggingIntegration(level=logging.INFO, event_level=logging.ERROR)],
            default_integrations=False)
        def broken():
            raise RuntimeError("private database details")
        agr.get_curation_resolver = broken
        query = agr._unwrap_function_tool_callable(agr.agr_curation_query, "agr_curation_query")
        output = query(method="get_gene_by_id", gene_id="private-identifier")
        outcome = tool_results.classify_tool_result(output)
        assert not outcome.success and outcome.operational_code
        state = tool_results.ToolFailureState()
        identity, queued = state.capture(code=outcome.operational_code,
            tool_name="agr_curation_query", invocation_id="call-one", trace_id="private-trace", session_id="private-session")
        sentry_sdk.flush(timeout=1)
        assert queued and len(events) == 1, events
        assert events[0]["tags"]["operation"] == "lookup_dependency_failure"
        assert events[0]["tags"]["ai_curation.trace.id_hash"] == sentry.hash_sentry_identifier("private-trace")
        assert events[0]["contexts"]["runtime_exception"]["failure_id"] == sentry.hash_sentry_identifier(identity)
        assert "private database details" not in repr(events)
        assert "private-identifier" not in repr(events)
    """)
    result = subprocess.run(
        [sys.executable, "-c", script], text=True, capture_output=True
    )
    assert result.returncode == 0, result.stderr + result.stdout
