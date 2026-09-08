from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from src.lib.benchmarks import runtime_catalog as runtime
from src.lib.benchmarks.execution_context import BenchmarkCuratorContext


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr("src.lib.config.groups_loader.get_valid_group_ids", lambda: ["group-a", "group-b"])
    rows = [NS(agent_key="extractor", model_id="model-a", model_reasoning="high", visibility="system"),
            NS(agent_key="validator", model_id="model-a", model_reasoning="high", visibility="system")]
    listing = Mock(return_value=rows)
    monkeypatch.setattr(runtime, "list_agents_visible_to_user", listing)
    monkeypatch.setattr(runtime, "get_agent_metadata", lambda key, **kwargs: {})
    monkeypatch.setattr(runtime, "list_models", lambda: [NS(
        model_id=model, provider="provider-a", reasoning_options=["high"], supports_reasoning=True,
    ) for model in ("model-a", "model-b")])
    monkeypatch.setattr(runtime, "resolve_model_provider", lambda model: "provider-a")
    supervisor = NS(model="model-a", reasoning="high")
    monkeypatch.setattr(runtime, "get_agent_config", lambda key: supervisor if key == "supervisor" else pytest.fail(key))
    recipe = Mock(name="recipe")
    recipe.name = "Configured Flow"
    recipe.description = "Extract experimentally relevant entities"
    recipe.access = NS(allowed_group_ids=[])
    recipe.model_dump.return_value = {"steps": [{"agent_id": "extractor"}]}
    monkeypatch.setattr(runtime, "load_benchmark_flow_templates", lambda groups: [{
        "name": recipe.name, "description": recipe.description,
        "steps": recipe.model_dump()["steps"], "allowed_group_ids": recipe.access.allowed_group_ids,
    }])
    nodes = [NS(type="agent", data=NS(agent_id=agent, model_dump=lambda: {}))
             for agent in ("extractor", "csv_formatter", "curation_prep", "curation_handoff")]
    hydrate = Mock(return_value=NS(nodes=nodes))
    monkeypatch.setattr(runtime, "build_flow_definition_from_recipe", hydrate)
    schedule = Mock(return_value={"scheduled_validators": [
        {"validator_agent_id": "semantic", "validator_package_id": "package", "validator_binding_id": "semantic-binding"},
        {"tool_name": "deterministic-check", "validator_binding_id": "deterministic-binding"},
    ]})
    monkeypatch.setattr(runtime, "validation_schedule_from_node_data", schedule)
    definition = object()
    lookup = Mock(return_value=definition)
    monkeypatch.setattr(runtime, "get_agent_definition_for_package", lookup)
    monkeypatch.setattr(runtime, "canonical_system_agent_key", lambda item: "validator" if item is definition else pytest.fail())
    curator = BenchmarkCuratorContext(subject="curator", auth_provider="oidc", db_user_id=42, active_groups=("group-a",))
    return NS(rows=rows, listing=listing, supervisor=supervisor, recipe=recipe,
              hydrate=hydrate, lookup=lookup, curator=curator)


def test_catalog_uses_db_defaults_and_hydrated_validator_binding(configured):
    session = object()
    catalog = runtime.build_curator_route_catalog(session, configured.curator)
    configured.listing.assert_called_once_with(session, 42, active_group_ids=("group-a",))
    configured.hydrate.assert_called_once_with(
        steps=[{"agent_id": "extractor"}], task_instructions=configured.recipe.description,
    )
    slots = {slot.slot: slot.default_route for slot in catalog.route_slots}
    assert set(slots) == {"supervisor", "agent:extractor", "agent:validator", "validator:semantic-binding"}
    flow = next(item for item in catalog.targets if item.target.kind == "flow")
    assert flow.route_slots == ("supervisor", "agent:extractor", "validator:semantic-binding")
    configured.rows[0].model_id = "model-b"
    configured.supervisor.model = "model-b"
    refreshed = runtime.build_curator_route_catalog(session, configured.curator)
    updated = {slot.slot: slot.default_route for slot in refreshed.route_slots}
    assert updated["agent:extractor"].model == updated["supervisor"].model == "model-b"


@pytest.mark.parametrize("restriction", ["recipe", "extractor", "validator"])
def test_inaccessible_flows_not_advertised(configured, restriction):
    if restriction == "recipe":
        configured.recipe.access.allowed_group_ids = ["group-b"]
    else:
        configured.rows[:] = [row for row in configured.rows if row.agent_key != restriction]
    catalog = runtime.build_curator_route_catalog(object(), configured.curator)
    assert all(item.target.kind != "flow" for item in catalog.targets)
    if restriction == "recipe":
        configured.hydrate.assert_not_called()


def test_invalid_db_model_fails_without_substitution(configured):
    configured.rows[0].model_id = "missing-model"
    with pytest.raises(ValueError, match="not in the model catalog"):
        runtime.build_curator_route_catalog(object(), configured.curator)


def test_authored_chat_has_frozen_model_slot_and_respects_visibility(configured):
    configured.rows.append(NS(agent_key="chat_output_formatter", model_id="model-b",
                              model_reasoning="high", visibility="system"))
    configured.hydrate.return_value.nodes.append(NS(
        type="agent", data=NS(agent_id="chat_output_formatter", model_dump=lambda: {}),
    ))
    catalog = runtime.build_curator_route_catalog(object(), configured.curator)
    slot = next(slot for slot in catalog.route_slots if slot.slot == "agent:chat_output_formatter")
    assert slot.default_route.model == "model-b"
    flow = next(target for target in catalog.targets if target.target.kind == "flow")
    assert "agent:chat_output_formatter" in flow.route_slots
    assert "agent:csv_formatter" not in flow.route_slots
    assert not any(target.target.kind == "agent" and target.target.id == "chat_output_formatter"
                   for target in catalog.targets)
    configured.rows.pop()
    hidden = runtime.build_curator_route_catalog(object(), configured.curator)
    assert not any(target.target.kind == "flow" for target in hidden.targets)


@pytest.mark.parametrize("validator_visible", [True, False])
def test_direct_target_requires_visible_model_validator(configured, monkeypatch, validator_visible):
    configured.rows.append(NS(agent_key="plain", model_id="model-a", model_reasoning="high", visibility="system"))
    def option(state, binding, model=None):
        return NS(state=NS(value=state), to_dict=lambda: {
            "validator_binding_id": binding, "validator_agent_id": model,
            "validator_package_id": "package" if model else None,
        })

    monkeypatch.setattr(runtime, "load_benchmark_flow_templates", lambda groups: [])
    monkeypatch.setattr(runtime, "validation_attachment_options_for_agent", lambda key, **kwargs: (
        option("active", "semantic-binding", "semantic"),
        option("active", "tool-only"),
        option("under_development", "future-binding", "future"),
    ) if key == "extractor" else ())
    if not validator_visible:
        configured.rows[:] = [row for row in configured.rows if row.agent_key != "validator"]
    catalog = runtime.build_curator_route_catalog(object(), configured.curator)
    direct = {target.target.id: target.route_slots for target in catalog.targets}
    if validator_visible:
        assert direct["extractor"] == ("agent:extractor", "validator:semantic-binding")
    else:
        assert "extractor" not in direct
    assert all(slot.slot not in {"validator:tool-only", "validator:future-binding"}
               for slot in catalog.route_slots)


@pytest.mark.parametrize("reasoning,expected", [("disabled", None), ("none", None), ("off", None), (" HIGH ", "high")])
def test_persisted_reasoning_uses_normal_runtime_normalization(configured, reasoning, expected):
    configured.rows[0].model_reasoning = reasoning
    catalog = runtime.build_curator_route_catalog(object(), configured.curator)
    slot = next(slot for slot in catalog.route_slots if slot.slot == "agent:extractor")
    assert slot.default_route.reasoning_effort == expected


def test_nonreasoning_model_clears_server_default_effort(configured, monkeypatch):
    models = runtime.list_models()
    for model in models:
        model.supports_reasoning = False
    monkeypatch.setattr(runtime, "list_models", lambda: models)
    catalog = runtime.build_curator_route_catalog(object(), configured.curator)
    assert all(slot.default_route.reasoning_effort is None for slot in catalog.route_slots)


def test_real_package_catalog_and_hydrated_recipes(monkeypatch):
    from src.lib.config.agent_loader import load_agent_definitions, canonical_system_agent_key
    from src.lib.packages.flow_recipes import load_flow_recipe_catalog
    from src.lib.benchmarks.runtime import _flow_from_recipe

    rows = [NS(
        agent_key=canonical_system_agent_key(definition),
        name=definition.name, description=definition.description,
        model_id=definition.model_config.model,
        model_reasoning=definition.model_config.reasoning,
        visibility="system",
    ) for definition in load_agent_definitions().values() if definition.model_config is not None]
    rows.append(NS(
        agent_key="custom_gene", name="Custom gene", description="Custom builder",
        model_id="gpt-5.6-sol", model_reasoning="medium", visibility="private",
        tool_ids=["read_section", "stage_gene_mention_evidence", "finalize_gene_extraction"],
        template_source="gene_extractor",
    ))
    monkeypatch.setattr(runtime, "list_agents_visible_to_user", lambda *args, **kwargs: rows)
    recipes = load_flow_recipe_catalog().recipes
    groups = tuple(sorted({group for recipe in recipes for group in recipe.access.allowed_group_ids}))
    curator = BenchmarkCuratorContext(subject="curator", auth_provider="oidc", db_user_id=42, active_groups=groups)
    catalog = runtime.build_curator_route_catalog(object(), curator)
    assert catalog.models
    advertised_flows = {target.target.id for target in catalog.targets if target.target.kind == "flow"}
    assert advertised_flows == {recipe["name"] for recipe in runtime.load_benchmark_flow_templates(groups)}
    assert {"Gene Extraction", "Allele/Variant Extraction"} <= advertised_flows
    direct = {target.target.id: target.route_slots for target in catalog.targets
              if target.target.kind == "agent"}
    assert direct["gene_extractor"] == (
        "agent:gene_extractor", "validator:alliance_gene_reference_lookup",
    )
    assert direct["allele_extractor"] == (
        "agent:allele_extractor", "validator:allele_mention_reference_validation",
    )
    assert direct["custom_gene"] == (
        "agent:custom_gene", "validator:alliance_gene_reference_lookup",
    )
    for name in ("Gene Extraction", "Allele/Variant Extraction"):
        flow = _flow_from_recipe(name, list(groups))
        model_agents = {
            node["data"]["agent_id"] for node in flow.flow_definition["nodes"]
            if node["type"] == "agent" and node["data"]["agent_id"] not in (
                runtime.SUPPORTED_OUTPUT_FORMATTER_AGENT_IDS - {"chat_output", "chat_output_formatter"}
            )
        }
        target = next(item for item in catalog.targets if item.target.kind == "flow" and item.target.id == name)
        assert {slot.removeprefix("agent:") for slot in target.route_slots if slot.startswith("agent:")} == model_agents
