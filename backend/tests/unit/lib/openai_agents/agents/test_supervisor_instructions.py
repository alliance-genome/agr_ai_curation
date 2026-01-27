"""Tests for registry-driven supervisor instructions."""
import pytest
from src.lib.openai_agents.agents.supervisor_agent import (
    generate_routing_table,
)


def test_generate_routing_table_returns_string():
    """Should return markdown table string."""
    table = generate_routing_table()
    assert isinstance(table, str)
    assert "| Tool |" in table


def test_generate_routing_table_includes_gene():
    """Should include gene specialist."""
    table = generate_routing_table()
    assert "ask_gene_specialist" in table


def test_generate_routing_table_has_descriptions():
    """Each tool should have a description."""
    table = generate_routing_table()
    lines = [l for l in table.split('\n') if l.startswith('|') and 'ask_' in l]
    for line in lines:
        # Should have tool name | description
        parts = line.split('|')
        assert len(parts) >= 3  # |tool|description|
        assert parts[2].strip()  # Description not empty


def test_generate_routing_table_excludes_disabled():
    """Should exclude disabled agents."""
    table = generate_routing_table()
    assert "csv_formatter" not in table
