"""Tests for registry-driven supervisor tool generation."""
from unittest.mock import patch

from src.lib.openai_agents.agents.supervisor_agent import (
    get_supervisor_agent_tools,
)

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
        "agent_key": "pdf",
        "name": "PDF Specialist",
        "description": "Document search and extraction",
        "tool_name": "ask_pdf_specialist",
        "requires_document": True,
        "group_rules_enabled": True,
    },
]


@patch(
    "src.lib.openai_agents.agents.supervisor_agent._get_supervisor_specialist_specs",
    return_value=MOCK_SUPERVISOR_SPECS,
)
def test_get_supervisor_agent_tools_returns_list(_mock_specs):
    """Should return a list of tool names."""
    tools = get_supervisor_agent_tools()
    assert isinstance(tools, list)


@patch(
    "src.lib.openai_agents.agents.supervisor_agent._get_supervisor_specialist_specs",
    return_value=MOCK_SUPERVISOR_SPECS,
)
def test_get_supervisor_agent_tools_includes_gene(_mock_specs):
    """Should include gene specialist tool."""
    tools = get_supervisor_agent_tools()
    assert "ask_gene_specialist" in tools


@patch(
    "src.lib.openai_agents.agents.supervisor_agent._get_supervisor_specialist_specs",
    return_value=MOCK_SUPERVISOR_SPECS,
)
def test_get_supervisor_agent_tools_excludes_disabled(_mock_specs):
    """Should exclude tools not returned by supervisor-enabled spec lookup."""
    tools = get_supervisor_agent_tools()
    # Formatter agents should not be in supervisor
    assert "ask_csv_formatter_specialist" not in tools


@patch(
    "src.lib.openai_agents.agents.supervisor_agent._get_supervisor_specialist_specs",
    return_value=MOCK_SUPERVISOR_SPECS,
)
def test_get_supervisor_agent_tools_excludes_task_input(_mock_specs):
    """Should exclude non-agent entries like task_input."""
    tools = get_supervisor_agent_tools()
    assert "task_input" not in tools
