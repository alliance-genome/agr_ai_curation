"""Custom syntax redirects without probing saved resources or selecting a head."""
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID

import pytest
import jsonschema

from src.lib.agent_studio import catalog_service
from src.lib.agent_studio.diagnostic_tools import tool_definitions
from src.lib.agent_studio.diagnostic_tools.registry import DiagnosticToolRegistry
from src.lib.agent_studio.saved_resource_inspection import SavedResourceInspection

AGENT_ID = "ca_e698127f-4637-4f85-ad89-0bf587a899cd"
REVISION_ID = "1077b77c-816a-45b6-a8c1-3c9c8fd09304"


@pytest.mark.parametrize("factory,section", [
    (tool_definitions._create_get_prompt_handler, "prompt_manifest"),
    (tool_definitions._create_get_tool_inventory_handler, "tools"),
])
@pytest.mark.parametrize("revision_id", [None, REVISION_ID])
@pytest.mark.parametrize("agent_id", [AGENT_ID, AGENT_ID.removeprefix("ca_")])
def test_custom_redirect_is_syntax_only_and_next_call_is_valid(monkeypatch, factory, section, revision_id, agent_id):
    catalog = Mock(side_effect=AssertionError("Must not consult catalog for custom syntax"))
    registry = Mock()
    registry.get.side_effect = AssertionError("Must not consult installed registry")
    monkeypatch.setattr(catalog_service, "get_prompt_catalog", catalog)
    monkeypatch.setattr(catalog_service, "AGENT_REGISTRY", registry)
    result = factory()(agent_id=agent_id, revision_id=revision_id)
    assert result["code"] == "unsupported_custom_agent_target"
    assert "does not establish existence or access" in result["message"]
    assert "latest revision or a template" in result["message"]
    assert result["next_call"]["tool"] == "inspect_saved_studio_resource"
    request = SavedResourceInspection.model_validate(result["next_call"]["arguments"])
    assert UUID(request.agent_id.removeprefix("ca_")) == UUID(AGENT_ID.removeprefix("ca_"))
    if revision_id:
        assert request.action == "agent_revision"
        assert request.revision_id == REVISION_ID
        assert request.section == section
    else:
        assert request.action == "agent_revisions"
        assert request.revision_id is None
        assert f"section={section}" in result["message"]
    catalog.assert_not_called()
    registry.get.assert_not_called()


@pytest.mark.parametrize("factory", [tool_definitions._create_get_prompt_handler,
                                    tool_definitions._create_get_tool_inventory_handler])
@pytest.mark.parametrize("agent_id,revision", [("ca_not-a-uuid", None), (AGENT_ID, "not-a-revision")])
def test_malformed_custom_identifiers_do_not_claim_existence(factory, agent_id, revision):
    result = factory()(agent_id=agent_id, revision_id=revision)
    assert "No resource lookup was performed" in result["message"]
    assert "next_call" not in result
    assert result["success"] is False


def test_unknown_installed_target_keeps_existing_error(monkeypatch):
    monkeypatch.setattr(catalog_service, "get_prompt_catalog", lambda: SimpleNamespace(
        get_agent=lambda _: None, catalog=SimpleNamespace(categories=[])))
    monkeypatch.setattr(catalog_service, "AGENT_REGISTRY", {})
    assert tool_definitions._create_get_prompt_handler()("missing_installed")["message"] == "Agent 'missing_installed' not found"
    assert tool_definitions._create_get_tool_inventory_handler()("missing_installed")["error"] == "Agent missing_installed was not found."


def test_registered_contracts_accept_exact_revision_guidance(monkeypatch):
    monkeypatch.setattr(tool_definitions, "_register_package_diagnostic_tools", lambda _: None)
    monkeypatch.setattr("src.lib.prompts.cache.is_initialized", lambda: False)
    registry = DiagnosticToolRegistry()
    tool_definitions.register_all_tools(registry)
    for name, section in [("get_prompt", "prompt_manifest"), ("get_tool_inventory", "tools")]:
        definition = registry._tools[name]
        args = {"agent_id": AGENT_ID, "revision_id": REVISION_ID}
        jsonschema.validate(args, definition.input_schema)
        assert "Saved custom agents are unsupported here" in definition.description
        assert f"section={section}" in definition.description
        request = SavedResourceInspection.model_validate(definition.handler(**args)["next_call"]["arguments"])
        assert request.revision_id == REVISION_ID
