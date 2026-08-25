"""Tests for hybrid tool registry (introspection + overrides)."""
import re
from types import SimpleNamespace

import pytest

from src.lib.agent_studio.catalog_service import get_tool_registry


@pytest.fixture(autouse=True)
def _reset_diagnostic_registry():
    from src.lib.agent_studio.diagnostic_tools import reset_registry

    reset_registry()
    yield
    reset_registry()


def _install_initialized_prompt_catalog(monkeypatch):
    from src.lib.agent_studio import catalog_service
    from src.lib.prompts import cache as prompt_cache

    monkeypatch.setattr(prompt_cache, "is_initialized", lambda: True)

    monkeypatch.setattr(
        catalog_service,
        "get_prompt_catalog",
        lambda: SimpleNamespace(
            catalog=SimpleNamespace(
                categories=[
                    SimpleNamespace(
                        agents=[SimpleNamespace(agent_id="demo_review")]
                    )
                ],
                available_groups=[],
            )
        ),
    )


def test_get_tool_registry_returns_dict():
    """Should return a dict of tools."""
    registry = get_tool_registry()
    assert isinstance(registry, dict)


def test_get_tool_registry_includes_agr_curation():
    """Should include agr_curation_query tool."""
    registry = get_tool_registry()
    assert "agr_curation_query" in registry


def test_get_diagnostic_registry_includes_codebase_tools(monkeypatch):
    from src.lib.agent_studio.diagnostic_tools import get_diagnostic_tools_registry

    _install_initialized_prompt_catalog(monkeypatch)
    registry = get_diagnostic_tools_registry()

    assert registry.has_tool("search_codebase")
    assert registry.has_tool("read_source_file")
    assert registry.has_tool("get_tool_inventory")
    assert registry.has_tool("get_tool_details")


def test_get_prompt_diagnostic_derives_targets_from_live_catalog(monkeypatch):
    from src.lib.agent_studio import catalog_service
    from src.lib.agent_studio.diagnostic_tools import tool_definitions
    from src.lib.prompts import cache as prompt_cache

    catalog = SimpleNamespace(
        categories=[
            SimpleNamespace(
                agents=[
                    SimpleNamespace(agent_id="demo_review"),
                    SimpleNamespace(agent_id="demo_extract"),
                ]
            )
        ],
        available_groups=["DEMO"],
    )
    monkeypatch.setattr(
        catalog_service,
        "get_prompt_catalog",
        lambda: SimpleNamespace(catalog=catalog),
    )
    monkeypatch.setattr(prompt_cache, "is_initialized", lambda: True)

    description, input_schema = tool_definitions._get_prompt_diagnostic_contract()

    assert "demo_extract, demo_review" in description
    assert "DEMO" in description
    assert "installed prompt targets" in input_schema["properties"]["agent_id"][
        "description"
    ]


def test_get_prompt_diagnostic_rejects_empty_agent_catalog(monkeypatch):
    from src.lib.agent_studio import catalog_service
    from src.lib.agent_studio.diagnostic_tools import tool_definitions
    from src.lib.prompts import cache as prompt_cache

    monkeypatch.setattr(
        catalog_service,
        "get_prompt_catalog",
        lambda: SimpleNamespace(
            catalog=SimpleNamespace(categories=[], available_groups=[])
        ),
    )
    monkeypatch.setattr(prompt_cache, "is_initialized", lambda: True)

    with pytest.raises(RuntimeError, match="prompt catalog is initialized"):
        tool_definitions._get_prompt_diagnostic_contract()


def test_get_prompt_diagnostic_omits_group_line_when_no_groups(monkeypatch):
    from src.lib.agent_studio import catalog_service
    from src.lib.agent_studio.diagnostic_tools import tool_definitions
    from src.lib.prompts import cache as prompt_cache

    monkeypatch.setattr(
        catalog_service,
        "get_prompt_catalog",
        lambda: SimpleNamespace(
            catalog=SimpleNamespace(
                categories=[
                    SimpleNamespace(
                        agents=[SimpleNamespace(agent_id="demo_review")]
                    )
                ],
                available_groups=[],
            )
        ),
    )
    monkeypatch.setattr(prompt_cache, "is_initialized", lambda: True)

    description, _ = tool_definitions._get_prompt_diagnostic_contract()

    assert "Installed prompt targets: demo_review." in description
    assert "Installed group-rule identifiers:" not in description


def test_get_prompt_diagnostic_is_generic_before_prompt_cache_initialization(
    monkeypatch,
):
    from src.lib.agent_studio import catalog_service
    from src.lib.agent_studio.diagnostic_tools import tool_definitions
    from src.lib.prompts import cache as prompt_cache

    monkeypatch.setattr(prompt_cache, "is_initialized", lambda: False)
    monkeypatch.setattr(
        catalog_service,
        "get_prompt_catalog",
        lambda: pytest.fail("uninitialized prompt catalog must not be memoized"),
    )

    description, input_schema = tool_definitions._get_prompt_diagnostic_contract()

    assert "Installed prompt targets:" not in description
    assert "Use these live catalog values" not in description
    assert "none currently available" not in description
    assert input_schema["required"] == ["agent_id"]


@pytest.mark.parametrize(
    ("database_url", "expected_registered"),
    [("postgresql://curator@example.test/curation", True), (None, False)],
)
def test_package_diagnostic_registration_respects_required_context(
    monkeypatch,
    caplog,
    database_url,
    expected_registered,
):
    from src.lib.agent_studio import catalog_service
    from src.lib.agent_studio.diagnostic_tools import tool_definitions
    from src.lib.agent_studio.diagnostic_tools.registry import DiagnosticToolRegistry

    context_builds = []

    def build_execution_context(_kwargs):
        context_builds.append(True)
        return SimpleNamespace(database_url=database_url)

    monkeypatch.setattr(
        catalog_service,
        "_build_tool_execution_context",
        build_execution_context,
    )
    monkeypatch.setattr(
        catalog_service,
        "_instantiate_package_tool",
        lambda _binding, execution_context: lambda **_kwargs: {
            "database_url": execution_context.database_url
        },
    )

    registry = DiagnosticToolRegistry()
    tool_definitions._register_package_diagnostic_tools(registry)

    assert context_builds == [True]
    assert registry.has_tool("curation_db_sql") is expected_registered
    if expected_registered:
        assert "Skipping package diagnostic tool curation_db_sql" not in caplog.text
    else:
        assert "Skipping package diagnostic tool curation_db_sql" in caplog.text
        assert "database_url" in caplog.text


def test_package_diagnostic_registration_rejects_unknown_context(monkeypatch):
    from src.lib.agent_studio import catalog_service
    from src.lib.agent_studio.diagnostic_tools import tool_definitions
    from src.lib.agent_studio.diagnostic_tools.registry import DiagnosticToolRegistry

    diagnostic = {
        "enabled": True,
        "input_schema": {"type": "object", "properties": {}},
        "description": "Demo diagnostic",
        "category": "demo",
        "tags": ["demo"],
    }
    binding = SimpleNamespace(
        tool_id="demo_diagnostic",
        required_context=("database_urll",),
    )
    monkeypatch.setattr(
        catalog_service,
        "get_tool_registry",
        lambda: {"demo_diagnostic": {"agent_studio": {"diagnostic": diagnostic}}},
    )
    monkeypatch.setattr(
        catalog_service,
        "_load_package_tool_registry",
        lambda: SimpleNamespace(bindings=[binding]),
    )
    monkeypatch.setattr(
        catalog_service,
        "_build_tool_execution_context",
        lambda _kwargs: SimpleNamespace(database_url=None),
    )

    with pytest.raises(ValueError, match="unknown execution context: database_urll"):
        tool_definitions._register_package_diagnostic_tools(
            DiagnosticToolRegistry()
        )


def test_diagnostic_registry_does_not_memoize_partial_registration(monkeypatch):
    from src.lib.agent_studio.diagnostic_tools import registry, tool_definitions

    registry.reset_registry()
    monkeypatch.setattr(
        tool_definitions,
        "register_all_tools",
        lambda _registry: (_ for _ in ()).throw(RuntimeError("invalid metadata")),
    )

    with pytest.raises(RuntimeError, match="invalid metadata"):
        registry.get_diagnostic_tools_registry()

    assert registry._registry_instance is None


def test_prompt_catalog_refresh_resets_diagnostic_registry(monkeypatch):
    from src.lib.agent_studio import catalog_service, diagnostic_tools

    built = SimpleNamespace(total_agents=1, available_groups=[])
    diagnostic_reset = {"called": False}
    monkeypatch.setattr(catalog_service, "_build_catalog", lambda: built)
    monkeypatch.setattr(
        diagnostic_tools,
        "reset_registry",
        lambda: diagnostic_reset.update(called=True),
    )

    service = catalog_service.PromptCatalogService()

    assert service.refresh() is built
    assert diagnostic_reset["called"] is True


def test_core_only_diagnostic_registry_excludes_alliance_content(monkeypatch):
    from src.lib.agent_studio import catalog_service
    from src.lib.agent_studio.diagnostic_tools import get_diagnostic_tools_registry

    catalog = SimpleNamespace(
        categories=[
            SimpleNamespace(agents=[SimpleNamespace(agent_id="demo_review")])
        ],
        available_groups=["DEMO"],
    )
    monkeypatch.setattr(
        catalog_service,
        "get_prompt_catalog",
        lambda: SimpleNamespace(catalog=catalog),
    )
    monkeypatch.setattr(catalog_service, "get_tool_registry", lambda: {})
    monkeypatch.setattr(
        catalog_service,
        "_load_package_tool_registry",
        lambda: SimpleNamespace(bindings=[]),
    )

    registry = get_diagnostic_tools_registry()
    serialized_registry = repr(registry.get_anthropic_tools())

    forbidden_patterns = (
        r"\bagr_curation_query\b",
        r"\bcuration_db_sql\b",
        r"\bchebi_api_call\b",
        r"\bquickgo_api_call\b",
        r"\bgo_api_call\b",
        r"alliancegenome\.org",
        r"ebi\.ac\.uk",
        r"geneontology\.org",
        r"\bWB\b",
        r"\bFB\b",
        r"\bMGI\b",
        r"\bRGD\b",
        r"\bSGD\b",
        r"\bZFIN\b",
    )
    assert not any(
        re.search(pattern, serialized_registry) for pattern in forbidden_patterns
    )


def test_tool_inventory_diagnostic_reports_agent_attached_tools(monkeypatch):
    from src.lib.agent_studio.diagnostic_tools import get_diagnostic_tools_registry, reset_registry

    _install_initialized_prompt_catalog(monkeypatch)
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


def test_tool_details_diagnostic_reports_agent_specific_metadata(monkeypatch):
    from src.lib.agent_studio.diagnostic_tools import get_diagnostic_tools_registry, reset_registry

    _install_initialized_prompt_catalog(monkeypatch)
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


def test_bindings_metadata_merges_with_introspected():
    """Curator metadata from bindings.yaml should merge with introspected data.

    search_document's category is supplied by its bindings.yaml metadata block
    (formerly the hardcoded TOOL_OVERRIDES), so it should reach the catalog.
    """
    registry = get_tool_registry()
    tool = registry.get("search_document", {})
    assert tool.get("category") == "Document"


@pytest.mark.parametrize(
    "tool_id",
    [
        "save_csv_file",
        "save_tsv_file",
        "save_json_file",
    ],
)
def test_raw_file_output_tools_are_not_catalogued(tool_id):
    registry = get_tool_registry()

    assert tool_id not in registry


def test_runtime_formatter_tool_docs_are_projection_based():
    registry = get_tool_registry()

    finalize_params = {
        param["name"]: param
        for param in registry["finalize_and_save"]["documentation"]["parameters"]
    }
    preview_params = {
        param["name"]: param
        for param in registry["preview_output_projection"]["documentation"]["parameters"]
    }

    assert registry["finalize_and_save"]["runtime_bound"] is True
    assert registry["finalize_and_save"]["package_backed"] is False
    assert finalize_params["plan_json"]["required"] is False
    assert finalize_params["filename_hint"]["required"] is False
    assert "Projection plan JSON" in finalize_params["plan_json"]["description"]
    assert preview_params["plan_json"]["required"] is True

    forbidden_params = {"data_json", "rows", "raw_csv", "raw_tsv", "raw_json"}
    assert not forbidden_params & set(finalize_params)
    assert not forbidden_params & set(preview_params)
