"""Unit tests for get_tool_inventory search and pagination."""

from __future__ import annotations

import pytest

from src.lib.agent_studio import catalog_service
from src.lib.agent_studio.diagnostic_tools import tool_definitions


_GLOBAL_TOOLS = {
    "search_genes": {
        "name": "search_genes",
        "description": "Look up gene identifiers",
        "category": "lookup",
    },
    "search_diseases": {
        "name": "search_diseases",
        "description": "Look up disease identifiers",
        "category": "lookup",
    },
    "record_evidence": {
        "name": "record_evidence",
        "description": "Attach evidence spans to a candidate",
        "category": "evidence",
    },
    "read_chunk": {
        "name": "read_chunk",
        "description": "Read a document chunk",
        "category": "document",
    },
}


@pytest.fixture
def _patched_catalog(monkeypatch):
    monkeypatch.setattr(catalog_service, "get_tool_registry", lambda: dict(_GLOBAL_TOOLS))
    monkeypatch.setattr(catalog_service, "get_all_tools", lambda: dict(_GLOBAL_TOOLS))
    return catalog_service


def test_get_tool_inventory_query_filters_global_catalog(_patched_catalog):
    handler = tool_definitions._create_get_tool_inventory_handler()

    result = handler(query="search")

    tool_ids = {item["tool_id"] for item in result["tools"]}
    assert tool_ids == {"search_genes", "search_diseases"}
    assert result["total_count"] == 2
    assert result["returned_count"] == 2
    assert result["filters"]["query"] == "search"
    assert result["truncated"] is False


def test_get_tool_inventory_query_matches_description(_patched_catalog):
    handler = tool_definitions._create_get_tool_inventory_handler()

    result = handler(query="evidence")

    assert {item["tool_id"] for item in result["tools"]} == {"record_evidence"}
    assert result["total_count"] == 1


def test_get_tool_inventory_pages_global_catalog(_patched_catalog):
    handler = tool_definitions._create_get_tool_inventory_handler()

    first = handler(limit=2)
    assert first["returned_count"] == 2
    assert first["total_count"] == 4
    assert first["truncated"] is True
    assert first["next_cursor"] == "2"

    second = handler(limit=2, cursor=first["next_cursor"])
    assert second["returned_count"] == 2
    assert second["truncated"] is False
    assert second["next_cursor"] is None

    first_ids = {item["tool_id"] for item in first["tools"]}
    second_ids = {item["tool_id"] for item in second["tools"]}
    assert first_ids.isdisjoint(second_ids)
    assert first_ids | second_ids == set(_GLOBAL_TOOLS)


def test_get_tool_inventory_agent_scope_query_and_paging(monkeypatch):
    monkeypatch.setattr(
        catalog_service,
        "AGENT_REGISTRY",
        {"gene_extractor": {"name": "Gene Specialist", "tools": ["search_genes", "read_chunk", "record_evidence"]}},
    )
    monkeypatch.setattr(
        catalog_service,
        "expand_tools_for_agent",
        lambda agent_id, tool_ids: list(tool_ids),
    )
    monkeypatch.setattr(
        catalog_service,
        "get_tool_for_agent",
        lambda tool_id, agent_id: _GLOBAL_TOOLS.get(tool_id),
    )

    handler = tool_definitions._create_get_tool_inventory_handler()

    queried = handler(agent_id="gene_extractor", query="search")
    assert {item["tool_id"] for item in queried["tools"]} == {"search_genes"}
    assert queried["total_count"] == 1
    assert queried["agent_id"] == "gene_extractor"

    paged = handler(agent_id="gene_extractor", limit=1)
    assert paged["returned_count"] == 1
    assert paged["total_count"] == 3
    assert paged["truncated"] is True
    assert paged["next_cursor"] == "1"
