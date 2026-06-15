"""Tests for registry-driven supervisor tool generation."""
import importlib
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest


def _supervisor_module():
    """Load the supervisor module lazily so patches hit the active module instance."""

    return importlib.import_module("src.lib.openai_agents.agents.supervisor_agent")

MOCK_SUPERVISOR_SPECS = [
    {
        "agent_key": "gene",
        "name": "Gene Specialist",
        "description": "Gene lookups and validation",
        "tool_name": "ask_gene_specialist",
        "requires_document": False,
        "group_rules_enabled": True,
    },
    {
        "agent_key": "pdf_extraction",
        "name": "PDF Specialist",
        "description": "Document search and extraction",
        "tool_name": "ask_pdf_extraction_specialist",
        "requires_document": True,
        "group_rules_enabled": True,
    },
]


def test_get_supervisor_agent_tools_returns_list():
    """Should return a list of tool names."""
    with patch.object(
        _supervisor_module(),
        "_get_supervisor_specialist_specs",
        return_value=MOCK_SUPERVISOR_SPECS,
    ):
        tools = _supervisor_module().get_supervisor_agent_tools()
    assert isinstance(tools, list)


def test_get_supervisor_agent_tools_includes_gene():
    """Should include gene specialist tool."""
    with patch.object(
        _supervisor_module(),
        "_get_supervisor_specialist_specs",
        return_value=MOCK_SUPERVISOR_SPECS,
    ):
        tools = _supervisor_module().get_supervisor_agent_tools()
    assert "ask_gene_specialist" in tools


def test_get_supervisor_agent_tools_excludes_disabled():
    """Should exclude tools not returned by supervisor-enabled spec lookup."""
    with patch.object(
        _supervisor_module(),
        "_get_supervisor_specialist_specs",
        return_value=MOCK_SUPERVISOR_SPECS,
    ):
        tools = _supervisor_module().get_supervisor_agent_tools()
    # Formatter agents should not be in supervisor
    assert "ask_csv_formatter_specialist" not in tools


def test_get_supervisor_agent_tools_excludes_task_input():
    """Should exclude non-agent entries like task_input."""
    with patch.object(
        _supervisor_module(),
        "_get_supervisor_specialist_specs",
        return_value=MOCK_SUPERVISOR_SPECS,
    ):
        tools = _supervisor_module().get_supervisor_agent_tools()
    assert "task_input" not in tools


@pytest.mark.asyncio
async def test_flow_streaming_tool_uses_isolated_run_config_and_closes(monkeypatch):
    supervisor = _supervisor_module()
    from src.lib.openai_agents import runner

    parent_config = runner.RunConfig(
        model_provider=object(),
        tracing_disabled=True,
        trace_include_sensitive_data=True,
    )
    child_config = runner.RunConfig(
        model_provider=object(),
        tracing_disabled=True,
        trace_include_sensitive_data=True,
    )
    provider = object()
    calls = []
    captured = {}

    def _build_isolated(parent):
        calls.append(("build", parent))
        return child_config, provider

    async def _close_isolated(close_provider, **kwargs):
        calls.append(("close", close_provider, kwargs))

    async def _run_specialist_with_events(**kwargs):
        captured.update(kwargs)
        return "flow step output"

    monkeypatch.setattr(runner, "build_isolated_openai_run_config", _build_isolated)
    monkeypatch.setattr(runner, "close_isolated_openai_provider", _close_isolated)
    monkeypatch.setattr(
        supervisor,
        "run_specialist_with_events",
        _run_specialist_with_events,
    )
    monkeypatch.setattr(supervisor, "get_current_trace_id", lambda: "trace-1")
    monkeypatch.setattr(supervisor, "get_current_user_id", lambda: "user-1")

    tool = supervisor._create_streaming_tool(
        agent=SimpleNamespace(name="Flow Specialist"),
        tool_name="run_flow_specialist",
        tool_description="Run flow specialist",
        specialist_name="Flow Specialist",
        inline_chat_persistence=False,
        isolate_run_config=True,
    )

    output = await tool.on_invoke_tool(
        SimpleNamespace(tool_name="run_flow_specialist", run_config=parent_config),
        json.dumps({"query": "extract this"}),
    )

    assert output == "flow step output"
    assert captured["run_config"] is child_config
    assert captured["inline_chat_persistence"] is False
    assert calls == [
        ("build", parent_config),
        (
            "close",
            provider,
            {"trace_id": "trace-1", "user_id": "user-1"},
        ),
    ]


@pytest.mark.asyncio
async def test_flow_streaming_tool_closes_isolated_provider_after_error(monkeypatch):
    supervisor = _supervisor_module()
    from src.lib.openai_agents import runner

    parent_config = runner.RunConfig(
        model_provider=object(),
        tracing_disabled=True,
        trace_include_sensitive_data=True,
    )
    child_config = runner.RunConfig(
        model_provider=object(),
        tracing_disabled=True,
        trace_include_sensitive_data=True,
    )
    provider = object()
    close_calls = []

    def _build_isolated(parent):
        assert parent is parent_config
        return child_config, provider

    async def _close_isolated(close_provider, **kwargs):
        close_calls.append((close_provider, kwargs))

    async def _run_specialist_with_events(**_kwargs):
        raise RuntimeError("specialist failed")

    monkeypatch.setattr(runner, "build_isolated_openai_run_config", _build_isolated)
    monkeypatch.setattr(runner, "close_isolated_openai_provider", _close_isolated)
    monkeypatch.setattr(
        supervisor,
        "run_specialist_with_events",
        _run_specialist_with_events,
    )
    monkeypatch.setattr(supervisor, "get_current_trace_id", lambda: "trace-1")
    monkeypatch.setattr(supervisor, "get_current_user_id", lambda: "user-1")

    tool = supervisor._create_streaming_tool(
        agent=SimpleNamespace(name="Flow Specialist"),
        tool_name="run_flow_specialist",
        tool_description="Run flow specialist",
        specialist_name="Flow Specialist",
        inline_chat_persistence=False,
        isolate_run_config=True,
    )

    output = await tool.on_invoke_tool(
        SimpleNamespace(tool_name="run_flow_specialist", run_config=parent_config),
        json.dumps({"query": "extract this"}),
    )

    assert "specialist failed" in output
    assert close_calls == [
        (provider, {"trace_id": "trace-1", "user_id": "user-1"}),
    ]
