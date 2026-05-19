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


def test_get_diagnostic_registry_includes_codebase_tools():
    from src.lib.agent_studio.diagnostic_tools import get_diagnostic_tools_registry, reset_registry

    reset_registry()
    registry = get_diagnostic_tools_registry()

    assert registry.has_tool("search_codebase")
    assert registry.has_tool("read_source_file")
    assert registry.has_tool("get_tool_inventory")
    assert registry.has_tool("get_tool_details")


def test_get_prompt_diagnostic_documents_current_extractor_and_validator_targets():
    from src.lib.agent_studio.diagnostic_tools import get_diagnostic_tools_registry, reset_registry

    reset_registry()
    registry = get_diagnostic_tools_registry()

    get_prompt_tool = registry.get_tool("get_prompt")
    assert get_prompt_tool is not None
    description = get_prompt_tool.description

    assert "Domain-envelope extractors" in description
    assert "gene_expression_extraction" in description
    assert "Validator/resolver agents" in description
    assert "phenotype_extractor" in description
    assert "controlled_vocabulary_validation" in description
    assert "data_provider_validation" in description
    assert "reference_validation" in description
    assert "experimental_condition_validation" in description


def test_tool_inventory_diagnostic_reports_agent_attached_tools():
    from src.lib.agent_studio.diagnostic_tools import get_diagnostic_tools_registry, reset_registry

    reset_registry()
    registry = get_diagnostic_tools_registry()

    inventory_tool = registry.get_tool("get_tool_inventory")
    assert inventory_tool is not None
    inventory = inventory_tool.handler(agent_id="disease_validation")

    assert inventory["success"] is True
    assert inventory["agent_id"] == "disease_validation"
    assert inventory["raw_tool_ids"] == ["get_agent_contract", "agr_curation_query"]
    assert "curation_db_sql" not in inventory["expanded_tool_ids"]
    assert {
        item["tool_id"] for item in inventory["tools"]
    } == {"get_agent_contract", "agr_curation_query"}


def test_tool_details_diagnostic_reports_agent_specific_metadata():
    from src.lib.agent_studio.diagnostic_tools import get_diagnostic_tools_registry, reset_registry

    reset_registry()
    registry = get_diagnostic_tools_registry()

    details_tool = registry.get_tool("get_tool_details")
    assert details_tool is not None
    result = details_tool.handler(
        tool_id="agr_curation_query",
        agent_id="disease_validation",
    )

    assert result["success"] is True
    assert result["tool_id"] == "agr_curation_query"
    assert result["agent_id"] == "disease_validation"
    assert result["tool"]["name"]


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


@pytest.mark.parametrize(
    ("tool_id", "expected_params"),
    [
        (
            "save_csv_file",
            [
                ("data_json", "string", True),
                ("filename", "string", True),
                ("columns", "string", False),
            ],
        ),
        (
            "save_tsv_file",
            [
                ("data_json", "string", True),
                ("filename", "string", True),
                ("columns", "string", False),
            ],
        ),
        (
            "save_json_file",
            [
                ("data_json", "string", True),
                ("filename", "string", True),
                ("pretty", "boolean", False),
            ],
        ),
    ],
)
def test_file_output_tool_docs_match_runtime_signature(tool_id, expected_params):
    registry = get_tool_registry()

    params = registry[tool_id]["documentation"]["parameters"]
    param_names = [param["name"] for param in params]
    params_by_name = {param["name"]: param for param in params}

    assert param_names == [name for name, _type, _required in expected_params]
    for name, expected_type, expected_required in expected_params:
        assert params_by_name[name]["type"] == expected_type
        assert params_by_name[name]["required"] is expected_required
