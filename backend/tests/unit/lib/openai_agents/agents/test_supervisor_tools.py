"""Tests for registry-driven supervisor tool generation."""
import importlib
from unittest.mock import patch


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
        "tool_name": "ask_pdf_specialist",
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
