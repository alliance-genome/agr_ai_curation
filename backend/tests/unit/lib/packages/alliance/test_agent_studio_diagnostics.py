"""Alliance-owned Agent Studio diagnostic metadata coverage."""

from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=True)
def _reset_diagnostic_registry():
    from src.lib.agent_studio.diagnostic_tools import reset_registry

    reset_registry()
    yield
    reset_registry()


def test_alliance_diagnostic_descriptions_preserve_service_call_guidance(monkeypatch):
    from src.lib.agent_studio import catalog_service
    from src.lib.agent_studio.diagnostic_tools import get_diagnostic_tools_registry
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

    registry = get_diagnostic_tools_registry()

    assert registry.has_tool("chebi_api_call")
    assert registry.has_tool("quickgo_api_call")
    assert registry.has_tool("go_api_call")

    tool_catalog = catalog_service.get_tool_registry()
    for tool_id in (
        "curation_db_sql",
        "chebi_api_call",
        "quickgo_api_call",
        "go_api_call",
    ):
        assert tool_catalog[tool_id]["agent_studio"]["diagnostic"]["enabled"] is True

    chebi_tool = registry.get_tool("chebi_api_call")
    assert chebi_tool is not None
    assert "/backend/api/public/es_search/?term={term}" in chebi_tool.description
    assert "CHEBI:17234" in chebi_tool.description

    quickgo_tool = registry.get_tool("quickgo_api_call")
    assert quickgo_tool is not None
    assert "/ontology/go/terms/{GO:ID}/ancestors" in quickgo_tool.description
    assert "GO:0003677" in quickgo_tool.description
    assert (
        "molecular_function, biological_process, and cellular_component"
        in quickgo_tool.description
    )

    go_tool = registry.get_tool("go_api_call")
    assert go_tool is not None
    assert "/bioentity/gene/{gene_id}/function" in go_tool.description
    assert "WB:WBGene00000898" in go_tool.description
    assert "IDA, IMP, IPI, IGI, ISS" in go_tool.description
    assert "IEA, IBA" in go_tool.description
