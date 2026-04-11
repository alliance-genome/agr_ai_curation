"""Tests for registry-driven supervisor instructions."""
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


def test_generate_routing_table_returns_string():
    """Should return markdown table string."""
    with patch.object(
        _supervisor_module(),
        "_get_supervisor_specialist_specs",
        return_value=MOCK_SUPERVISOR_SPECS,
    ):
        table = _supervisor_module().generate_routing_table()
    assert isinstance(table, str)
    assert "| Tool |" in table


def test_generate_routing_table_includes_gene():
    """Should include gene specialist."""
    with patch.object(
        _supervisor_module(),
        "_get_supervisor_specialist_specs",
        return_value=MOCK_SUPERVISOR_SPECS,
    ):
        table = _supervisor_module().generate_routing_table()
    assert "ask_gene_specialist" in table


def test_generate_routing_table_has_descriptions():
    """Each tool should have a description."""
    with patch.object(
        _supervisor_module(),
        "_get_supervisor_specialist_specs",
        return_value=MOCK_SUPERVISOR_SPECS,
    ):
        table = _supervisor_module().generate_routing_table()
    lines = [l for l in table.split('\n') if l.startswith('|') and 'ask_' in l]
    for line in lines:
        # Should have tool name | description
        parts = line.split('|')
        assert len(parts) >= 3  # |tool|description|
        assert parts[2].strip()  # Description not empty


def test_generate_routing_table_excludes_disabled():
    """Should exclude disabled agents."""
    with patch.object(
        _supervisor_module(),
        "_get_supervisor_specialist_specs",
        return_value=MOCK_SUPERVISOR_SPECS,
    ):
        table = _supervisor_module().generate_routing_table()
    assert "csv_formatter" not in table
