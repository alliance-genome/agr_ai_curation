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


def test_custom_catalog_pins_authorized_source_and_uses_saved_model(configured, monkeypatch):
    from tests.unit.lib.benchmarks.test_source_revisions import source_receipt

    receipt = source_receipt()
    configured.rows.append(NS(agent_key=receipt.agent_key, model_id="unrelated-head-model",
                              model_reasoning="high", visibility="private"))
    capture = Mock(return_value=receipt)
    load = Mock(return_value=(NS(), NS(model_id="model-b", model_reasoning=None, curation={})))
    monkeypatch.setattr(runtime, "current_execution_receipt", capture)
    monkeypatch.setattr(runtime, "get_execution_revision", load)
    catalog = runtime.build_curator_route_catalog(object(), configured.curator)
    target = next(t for t in catalog.targets if t.target.id == receipt.agent_key)
    assert target.source_execution_receipts == {f"agent:{receipt.agent_key}": receipt}
    route = next(s.default_route for s in catalog.route_slots if s.slot == f"agent:{receipt.agent_key}")
    assert route.model == "model-b"
    assert route.reasoning_effort is None
    assert capture.call_args.args[1:] == (receipt.agent_key, 42)
    assert capture.call_args.kwargs["active_group_ids"] == ["group-a"]


def test_profile_mapping_catalog_freezes_custom_validator_source(configured, monkeypatch):
    from uuid import uuid4
    from tests.unit.lib.benchmarks.test_source_revisions import source_receipt
    from src.schemas.agent_execution_revision import AgentOutputContract, GenericProfilePin
    from src.lib.curation_workspace import adapter_registry
    from src.lib.domain_packs import profile_validation

    source = source_receipt("ca_extractor")
    source.output_contract = AgentOutputContract(output_state="structured_extraction", output_mode="profile_bound_generic",
        generic_profile_ref=GenericProfilePin(profile_id=uuid4(), profile_revision_id=uuid4(), revision=1,
                                             fingerprint="sha256:" + "d" * 64))
    validator = source_receipt("ca_validator")
    pin = {"agent_id": str(validator.agent_id), "agent_key": validator.agent_key,
           "revision_id": str(validator.agent_revision_id), "fingerprint": validator.fingerprint}
    configured.rows.append(NS(agent_key=source.agent_key, model_id="unused-head", model_reasoning=None, visibility="private"))
    monkeypatch.setattr(runtime, "current_execution_receipt", lambda *a, **k: source)

    def saved(_session, agent_id, revision_id, user_id, **kwargs):
        assert user_id == 42 and kwargs["active_group_ids"] == ["group-a"]
        selected = source if agent_id == source.agent_id else validator
        assert revision_id == selected.agent_revision_id
        return NS(id=selected.agent_revision_id, agent_id=selected.agent_id, revision=1), NS(
            model_id="model-b" if selected is validator else "model-a", model_reasoning=None,
            curation={"domain_pack_id": "generic"}, output_contract=selected.output_contract)

    monkeypatch.setattr(runtime, "get_execution_revision", saved)
    authorize = Mock(return_value=validator)
    monkeypatch.setattr(runtime, "authorize_execution_receipt", authorize)
    # This synthetic catalog fixture owns its groups and profile mapping. Do not
    # load installed Alliance packs against those groups or depend on warm caches.
    generic_pack = object()

    def resolve_pack(pack_id):
        assert pack_id == "generic"
        return generic_pack

    def resolve_mapping(receipt, pack, **kwargs):
        assert receipt == source and pack is generic_pack
        assert kwargs["user_id"] == 42 and kwargs["active_group_ids"] == ("group-a",)
        return NS(registry=NS(bindings=[NS(binding_id="mapped-custom", raw={"custom_validator": pin})]))

    monkeypatch.setattr(adapter_registry, "resolve_curation_domain_pack_by_id", resolve_pack)
    monkeypatch.setattr(profile_validation, "resolve_profile_validation", resolve_mapping)
    monkeypatch.setattr(profile_validation, "profile_validation_attachment_options", lambda _: [NS(
        state=NS(value="active"), to_dict=lambda: {"validator_agent_id": "semantic",
            "validator_package_id": "package", "validator_binding_id": "mapped-custom"},
    )])
    catalog = runtime.build_curator_route_catalog(object(), configured.curator)
    target = next(t for t in catalog.targets if t.target.id == source.agent_key)
    assert target.source_execution_receipts == {"agent:ca_extractor": source, "validator:mapped-custom": validator}
    assert next(s.default_route.model for s in catalog.route_slots if s.slot == "validator:mapped-custom") == "model-b"
    authorize.assert_called_once()
    assert authorize.call_args.args[1] == validator.model_dump(mode="json")


def test_authored_chat_has_frozen_model_slot_and_respects_visibility(configured):
    configured.rows.append(NS(agent_key="chat_output", model_id="model-b",
                              model_reasoning="high", visibility="system"))
    configured.hydrate.return_value.nodes.append(NS(
        type="output", data=NS(agent_id="chat_output", model_dump=lambda: {}),
    ))
    catalog = runtime.build_curator_route_catalog(object(), configured.curator)
    slot = next(slot for slot in catalog.route_slots if slot.slot == "agent:chat_output")
    assert slot.default_route.model == "model-b"
    flow = next(target for target in catalog.targets if target.target.kind == "flow")
    assert "agent:chat_output" in flow.route_slots
    assert "agent:csv_formatter" not in flow.route_slots
    assert not any(target.target.kind == "agent" and target.target.id == "chat_output"
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
            if node["type"] in ("agent", "output") and node["data"]["agent_id"] not in (
                runtime.SUPPORTED_OUTPUT_FORMATTER_AGENT_IDS - {"chat_output", "chat_output_formatter"}
            )
        }
        target = next(item for item in catalog.targets if item.target.kind == "flow" and item.target.id == name)
        assert "chat_output" in model_agents
        assert "agent:chat_output" in target.route_slots
        assert {slot.removeprefix("agent:") for slot in target.route_slots if slot.startswith("agent:")} == model_agents
