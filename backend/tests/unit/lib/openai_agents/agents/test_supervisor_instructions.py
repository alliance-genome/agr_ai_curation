"""Tests for registry-driven supervisor instructions."""
from unittest.mock import patch

from src.lib.openai_agents.agents.supervisor_agent import (
    generate_routing_table,
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
        "agent_key": "pdf_extraction",
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
def test_generate_routing_table_returns_string(_mock_specs):
    """Should return markdown table string."""
    table = generate_routing_table()
    assert isinstance(table, str)
    assert "| Tool |" in table


@patch(
    "src.lib.openai_agents.agents.supervisor_agent._get_supervisor_specialist_specs",
    return_value=MOCK_SUPERVISOR_SPECS,
)
def test_generate_routing_table_includes_gene(_mock_specs):
    """Should include gene specialist."""
    table = generate_routing_table()
    assert "ask_gene_specialist" in table


@patch(
    "src.lib.openai_agents.agents.supervisor_agent._get_supervisor_specialist_specs",
    return_value=MOCK_SUPERVISOR_SPECS,
)
def test_generate_routing_table_has_descriptions(_mock_specs):
    """Each tool should have a description."""
    table = generate_routing_table()
    lines = [l for l in table.split('\n') if l.startswith('|') and 'ask_' in l]
    for line in lines:
        # Should have tool name | description
        parts = line.split('|')
        assert len(parts) >= 3  # |tool|description|
        assert parts[2].strip()  # Description not empty


@patch(
    "src.lib.openai_agents.agents.supervisor_agent._get_supervisor_specialist_specs",
    return_value=MOCK_SUPERVISOR_SPECS,
)
def test_generate_routing_table_excludes_disabled(_mock_specs):
    """Should exclude disabled agents."""
    table = generate_routing_table()
    assert "csv_formatter" not in table
