"""Tests for registry-driven supervisor tool generation."""
import pytest
from src.lib.openai_agents.agents.supervisor_agent import (
    get_supervisor_agent_tools,
)


def test_get_supervisor_agent_tools_returns_list():
    """Should return a list of tool names."""
    tools = get_supervisor_agent_tools()
    assert isinstance(tools, list)


def test_get_supervisor_agent_tools_includes_gene():
    """Should include gene specialist tool."""
    tools = get_supervisor_agent_tools()
    assert "ask_gene_specialist" in tools


def test_get_supervisor_agent_tools_excludes_disabled():
    """Should exclude agents with supervisor.enabled=False."""
    tools = get_supervisor_agent_tools()
    # Formatter agents should not be in supervisor
    assert "csv_formatter" not in tools
    assert "ask_csv_formatter" not in tools


def test_get_supervisor_agent_tools_excludes_task_input():
    """Should exclude non-agent entries like task_input."""
    tools = get_supervisor_agent_tools()
    assert "task_input" not in tools
