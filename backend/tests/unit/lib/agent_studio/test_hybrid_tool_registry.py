"""Tests for hybrid tool registry (introspection + overrides)."""
import pytest
from src.lib.agent_studio.catalog_service import (
    get_tool_registry,
    TOOL_OVERRIDES,
)


def test_get_tool_registry_returns_dict():
    """Should return a dict of tools."""
    registry = get_tool_registry()
    assert isinstance(registry, dict)


def test_get_tool_registry_includes_agr_curation():
    """Should include agr_curation_query tool."""
    registry = get_tool_registry()
    assert "agr_curation_query" in registry


def test_get_tool_registry_has_description():
    """Tools should have descriptions."""
    registry = get_tool_registry()
    for tool_id, metadata in registry.items():
        assert "description" in metadata or hasattr(metadata, 'description')


def test_tool_overrides_merge_with_introspected():
    """Manual overrides should merge with introspected data."""
    registry = get_tool_registry()
    # If agr_curation_query has an override, it should be applied
    if "agr_curation_query" in TOOL_OVERRIDES:
        tool = registry.get("agr_curation_query", {})
        override = TOOL_OVERRIDES["agr_curation_query"]
        if "category" in override:
            assert tool.get("category") == override["category"]


def test_save_csv_file_docs_match_runtime_signature():
    registry = get_tool_registry()

    params = registry["save_csv_file"]["documentation"]["parameters"]
    param_names = [param["name"] for param in params]
    params_by_name = {param["name"]: param for param in params}

    assert param_names == ["data_json", "filename", "columns"]
    assert params_by_name["data_json"]["type"] == "string"
    assert params_by_name["data_json"]["required"] is True
    assert params_by_name["filename"]["type"] == "string"
    assert params_by_name["filename"]["required"] is True
    assert params_by_name["columns"]["type"] == "string"
    assert params_by_name["columns"]["required"] is False
