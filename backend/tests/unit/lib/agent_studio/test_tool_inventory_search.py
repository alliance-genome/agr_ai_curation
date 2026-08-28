"""Unit tests for get_tool_inventory search and pagination."""

from __future__ import annotations

import hashlib
import json
from typing import Any
import pytest

from src.api import agent_studio as api_module
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


def test_get_tool_inventory_query_matches_description_beyond_summary_preview(monkeypatch):
    tools = {"deep_match": {"name": "Deep match", "description": ("x" * 500) + " sentinel",
                            "category": "lookup"}}
    monkeypatch.setattr(catalog_service, "get_tool_registry", lambda: tools)
    result = tool_definitions._create_get_tool_inventory_handler()(query="sentinel")
    assert [item["tool_id"] for item in result["tools"]] == ["deep_match"]
    assert result["tools"][0]["description_truncated"] is True
    assert "search_description" not in result["tools"][0]


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


def test_installed_catalog_pages_stay_bounded_and_have_executable_continuations(monkeypatch):
    installed = {f"tool_{index:02d}": {"name": f"Installed tool {index:02d}",
                 "description": "rich installed metadata " * 80,
                 "category": f"category_{index % 4}"} for index in range(67)}
    monkeypatch.setattr(catalog_service, "get_tool_registry", lambda: installed)
    handler = tool_definitions._create_get_tool_inventory_handler()
    seen = []
    arguments: dict[str, Any] = {}
    while True:
        result = handler(**arguments)
        assert len(json.dumps(result, ensure_ascii=False, sort_keys=True)) <= 8_000
        content = api_module._provider_tool_result_content(
            tool_name="get_tool_inventory", tool_input=arguments, tool_result=result,
            session_id="session-1", turn_id="turn-1")
        assert json.loads(content).get("status") != "compacted_tool_result"
        assert result["returned_count"] <= 20
        seen.extend(item["tool_id"] for item in result["tools"])
        if not result["truncated"]:
            break
        arguments = result["next_call"]["arguments"]
    assert seen == sorted(installed)


def test_runtime_installed_catalog_default_page_stays_provider_visible():
    result = tool_definitions._create_get_tool_inventory_handler()()
    assert result["total_count"] == len(catalog_service.get_tool_registry())
    assert result["returned_count"] <= 20
    content = api_module._provider_tool_result_content(
        tool_name="get_tool_inventory", tool_input={}, tool_result=result,
        session_id="session-1", turn_id="turn-1")
    assert json.loads(content).get("status") != "compacted_tool_result"


def test_large_parent_tool_metadata_is_exactly_section_addressable(monkeypatch):
    metadata = {"name": "Large parent", "category": "database",
                "documentation": {"methods": [{"id": f"method_{index}", "schema": "x" * 500}
                                                for index in range(40)]}}
    monkeypatch.setattr(catalog_service, "get_tool_details",
                        lambda tool_id: metadata if tool_id == "large_parent" else None)
    handler = tool_definitions._create_get_tool_details_handler()
    index = handler(tool_id="large_parent")
    assert index["detail_mode"] == "sections"
    documentation = next(item for item in index["sections"] if item["section"] == "documentation")
    chunks = []
    arguments: dict[str, Any] = {"tool_id": "large_parent", "section": "documentation"}
    while True:
        result = handler(**arguments)
        assert len(json.dumps(result, ensure_ascii=False, sort_keys=True)) <= 8_000
        content = api_module._provider_tool_result_content(
            tool_name="get_tool_details", tool_input=arguments, tool_result=result,
            session_id="session-1", turn_id="turn-1")
        assert json.loads(content).get("status") != "compacted_tool_result"
        chunks.append(result["content"])
        assert result["sha256"] == documentation["sha256"]
        if result["complete"]:
            break
        arguments = result["next_call"]["arguments"]
    reconstructed = "".join(chunks)
    assert hashlib.sha256(reconstructed.encode()).hexdigest() == documentation["sha256"]
    assert json.loads(reconstructed) == metadata["documentation"]


def test_small_focused_tool_details_remain_one_call(monkeypatch):
    metadata = {"name": "read_chunk", "documentation": {"parameters": {}}}
    monkeypatch.setattr(catalog_service, "get_tool_for_agent", lambda tool_id, agent_id: metadata)
    result = tool_definitions._create_get_tool_details_handler()(
        tool_id="read_chunk", agent_id="pdf_extraction")
    assert result["tool"] == metadata
    assert "detail_mode" not in result
