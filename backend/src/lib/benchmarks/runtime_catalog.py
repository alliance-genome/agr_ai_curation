"""Curator-visible route catalogs grounded in the same sources as execution."""

import json
from uuid import UUID

from sqlalchemy.orm import Session

from src.lib.agent_access import is_resource_access_allowed
from src.lib.agent_studio.agent_service import list_agents_visible_to_user
from src.lib.agent_studio.catalog_service import get_agent_metadata
from src.lib.agent_studio.execution_revision_service import (
    current_execution_receipt, get_execution_revision, authorize_execution_receipt,
)
from src.schemas.agent_execution_revision import AgentExecutionReceipt
from src.lib.agent_studio.flow_tools import build_flow_definition_from_recipe
from src.lib.config.agent_loader import canonical_system_agent_key, get_agent_definition_for_package
from src.lib.config.models_loader import list_models
from src.lib.curation_workspace.curation_prep_constants import CURATION_PREP_AGENT_ID
from src.lib.flow_edge_roles import SUPPORTED_OUTPUT_FORMATTER_AGENT_IDS
from src.lib.flows.executor import CURATION_HANDOFF_AGENT_ID
from src.lib.flows.validation_attachments import (
    validation_attachment_options_for_agent,
    validation_schedule_from_node_data,
)
from src.lib.openai_agents.config import get_agent_config, normalize_reasoning_effort, resolve_model_provider

from .catalog import build_route_catalog
from .execution_context import BenchmarkCuratorContext
from .flow_catalog import load_benchmark_flow_templates
from .models import BenchmarkModelCatalogEntry, BenchmarkRouteCatalog, BenchmarkSuiteRoute


def build_curator_route_catalog(
    session: Session, curator: BenchmarkCuratorContext, *,
    freeze_targets: set[tuple[str, str]] | None = None,
) -> BenchmarkRouteCatalog:
    """Use current visible DB agents and hydrated package recipes, never client data.

    The caller owns the session. Admission constructs this only after determining
    that the idempotency key does not already have a durable outcome.
    """
    # Authored chat now invokes a model and must have a frozen route like any
    # other specialist. Keep the existing non-chat terminal classification.
    runtime_only = (SUPPORTED_OUTPUT_FORMATTER_AGENT_IDS - {
        "chat_output", "chat_output_formatter",
    }) | {
        CURATION_PREP_AGENT_ID, CURATION_HANDOFF_AGENT_ID, "task_input",
    }
    all_visible = {
        agent.agent_key: agent for agent in list_agents_visible_to_user(
            session, curator.db_user_id, active_group_ids=curator.active_groups,
        )
    }
    visible = {key: agent for key, agent in all_visible.items() if key not in runtime_only}
    models = list_models()
    models_by_id = {model.model_id: model for model in models}

    def route(model: str, reasoning: str | None) -> BenchmarkSuiteRoute:
        definition = models_by_id.get(model)
        return BenchmarkSuiteRoute(
            provider=resolve_model_provider(model), model=model,
            reasoning_effort=normalize_reasoning_effort(reasoning)
            if definition is None or definition.supports_reasoning else None,
        )

    sources: dict[str, AgentExecutionReceipt] = {}
    saved_metadata: dict[str, dict] = {}
    agent_defaults = {}
    for key, agent in visible.items():
        if key.startswith("ca_"):
            receipt = current_execution_receipt(session, key, curator.db_user_id,
                                                active_group_ids=list(curator.active_groups))
            _, saved = get_execution_revision(session, receipt.agent_id, receipt.agent_revision_id,
                                              curator.db_user_id, active_group_ids=list(curator.active_groups))
            sources[f"agent:{key}"] = receipt
            agent_defaults[key] = route(saved.model_id, saved.model_reasoning)
            saved_metadata[key] = {
                "curation": saved.curation,
                "execution_receipt": receipt.model_dump(mode="json"),
                "authenticated_group_ids": list(curator.active_groups),
                "authenticated_user_id": curator.db_user_id,
            }
        else:
            agent_defaults[key] = route(agent.model_id, agent.model_reasoning)
    supervisor = get_agent_config("supervisor")
    flow_agents: dict[str, tuple[str, ...]] = {}
    flow_validators: dict[str, tuple[str, ...]] = {}
    validator_defaults: dict[str, BenchmarkSuiteRoute] = {}
    validator_agents: dict[str, str] = {}
    frozen_flows = {}

    def model_validators(schedule: list[dict], custom_pins: dict | None = None) -> dict[str, BenchmarkSuiteRoute] | None:
        validators: dict[str, BenchmarkSuiteRoute] = {}
        for validator in schedule:
            validator_agent = validator.get("validator_agent_id")
            if not validator_agent:
                # Tool-backed validation is deterministic, not a model slot.
                continue
            package = validator.get("validator_package_id")
            binding = validator.get("validator_binding_id")
            if not package or not binding:
                raise ValueError("Model validator lacks its package/binding identity")
            custom_pin = (custom_pins or {}).get(binding)
            if custom_pin is not None:
                revision, saved = get_execution_revision(
                    session, UUID(custom_pin["agent_id"]), UUID(custom_pin["revision_id"]), curator.db_user_id,
                    active_group_ids=list(curator.active_groups),
                )
                receipt = AgentExecutionReceipt(
                    agent_id=revision.agent_id, agent_key=custom_pin["agent_key"],
                    agent_revision_id=revision.id, revision=revision.revision,
                    fingerprint=custom_pin["fingerprint"], output_contract=saved.output_contract,
                )
                receipt = authorize_execution_receipt(session, receipt.model_dump(mode="json"), curator.db_user_id,
                                                      active_group_ids=list(curator.active_groups))
                slot = f"validator:{binding}"
                if slot in sources and sources[slot] != receipt:
                    raise ValueError("Validator slot has conflicting source revisions")
                sources[slot] = receipt
                validators[binding] = route(saved.model_id, saved.model_reasoning)
                continue
            definition = get_agent_definition_for_package(package, validator_agent)
            if definition is None:
                raise ValueError("Model validator agent is not configured")
            key = canonical_system_agent_key(definition)
            agent = visible.get(key)
            if agent is None or agent.visibility != "system":
                return None
            previous_agent = validator_agents.setdefault(binding, key)
            if previous_agent != key:
                raise ValueError("Model validator binding has conflicting agent identities")
            default = agent_defaults[key]
            if binding in validators and validators[binding] != default:
                raise ValueError("Model validator binding has conflicting defaults")
            validators[binding] = default
        return validators

    def register_validators(validators: dict[str, BenchmarkSuiteRoute]) -> None:
        for binding, default in validators.items():
            if binding in validator_defaults and validator_defaults[binding] != default:
                raise ValueError("Model validator binding has conflicting defaults")
            validator_defaults[binding] = default

    direct_validators: dict[str, tuple[str, ...]] = {}
    for key in visible:
        if key in SUPPORTED_OUTPUT_FORMATTER_AGENT_IDS:
            # Formatters consume saved flow artifacts, not a standalone paper.
            continue
        # Direct curator runs dispatch active package bindings, without flow opt-outs.
        # Resolve the same inherited curation ownership as custom runtime agents.
        metadata = saved_metadata[key] if key in saved_metadata else get_agent_metadata(key, _resolved_db_agent=visible[key])
        custom_pins = {}
        if key in saved_metadata and sources[f"agent:{key}"].output_contract.generic_profile_ref is not None:
            from src.lib.curation_workspace.adapter_registry import resolve_curation_domain_pack_by_id
            from src.lib.domain_packs.profile_validation import resolve_profile_validation, profile_validation_attachment_options

            generic_pack = resolve_curation_domain_pack_by_id("generic")
            if generic_pack is None:
                raise ValueError("Generic profile package is unavailable")
            context = resolve_profile_validation(
                sources[f"agent:{key}"], generic_pack,
                db=session, user_id=curator.db_user_id, active_group_ids=curator.active_groups,
            )
            assert context is not None
            options = profile_validation_attachment_options(context)
            custom_pins = {binding.binding_id: binding.raw["custom_validator"]
                           for binding in context.registry.bindings if binding.raw.get("custom_validator")}
        else:
            options = validation_attachment_options_for_agent(key, agent_registry={key: metadata})
        direct_bindings = model_validators([
            option.to_dict() for option in options
            if option.state.value == "active"
        ], custom_pins)
        if direct_bindings is not None:
            register_validators(direct_bindings)
            direct_validators[key] = tuple(sorted(direct_bindings))
    for recipe in load_benchmark_flow_templates(curator.active_groups):
        if not is_resource_access_allowed(
            visibility_allowed=True, allowed_group_ids=recipe["allowed_group_ids"],
            active_group_ids=list(curator.active_groups), resource_kind="flow_recipe",
        ):
            continue
        definition = build_flow_definition_from_recipe(
            steps=recipe["steps"], task_instructions=recipe["description"],
        )
        agents: set[str] = set()
        validators: dict[str, BenchmarkSuiteRoute] = {}
        accessible = True
        for node in definition.nodes:
            if node.type not in ("agent", "output"):
                continue
            agent_id = node.data.agent_id
            if agent_id not in runtime_only:
                if agent_id not in visible:
                    accessible = False
                    break
                agents.add(agent_id)
            schedule = validation_schedule_from_node_data(node.data.model_dump())
            node_validators = model_validators(schedule["scheduled_validators"])
            if node_validators is None:
                accessible = False
                break
            for binding, default in node_validators.items():
                if binding in validators and validators[binding] != default:
                    raise ValueError("Model validator binding has conflicting defaults")
                validators[binding] = default
        if not accessible:
            continue
        register_validators(validators)
        flow_agents[recipe["name"]] = tuple(sorted(agents))
        flow_validators[recipe["name"]] = tuple(sorted(validators))
        if freeze_targets is not None and ("flow", recipe["name"]) in freeze_targets:
            from .flow_capture import capture_recipe_flow
            frozen_flows[recipe["name"]] = capture_recipe_flow(session, curator, recipe, definition)
    catalog = build_route_catalog(
        models=tuple(BenchmarkModelCatalogEntry.model_validate({
            "provider": model.provider, "model": model.model_id,
            "reasoning_efforts": tuple(model.reasoning_options) if model.supports_reasoning else (),
        }) for model in models),
        supervisor_default=route(supervisor.model, supervisor.reasoning),
        agent_defaults=agent_defaults, model_validator_defaults=validator_defaults,
        agent_targets=direct_validators, agent_model_validators=direct_validators,
        source_execution_receipts=sources,
        flow_agents=flow_agents, flow_model_validators=flow_validators,
    )
    if freeze_targets is None:
        return catalog
    from .models import BenchmarkTargetCatalogEntry
    from .system_snapshot import capture_system_agent
    from .supervisor_snapshot import capture_flow_supervisor

    targets = []
    for target in catalog.targets:
        if (target.target.kind, target.target.id) not in freeze_targets:
            continue
        systems = {}
        for slot in target.route_slots:
            if slot == "supervisor" or slot in target.source_execution_receipts:
                continue
            kind, key = slot.split(":", 1)
            agent_key = key if kind == "agent" else validator_agents[key]
            if agent_key not in systems:
                systems[agent_key] = capture_system_agent(visible[agent_key], active_groups=curator.active_groups)
        if target.target.kind == "flow":
            for node in frozen_flows[target.target.id].definition["nodes"]:
                if node["type"] not in ("agent", "output"):
                    continue
                agent_key = node["data"]["agent_id"]
                row = all_visible.get(agent_key)
                if agent_key in runtime_only and row is not None and row.visibility == "system":
                    systems[agent_key] = capture_system_agent(row, active_groups=curator.active_groups)
        payload = target.model_dump(mode="json")
        payload["system_agent_snapshots"] = {
            key: source.model_dump(mode="json") for key, source in systems.items()
        }
        if target.target.kind == "flow":
            frozen = frozen_flows[target.target.id]
            payload["flow_snapshot"] = frozen.model_dump(mode="json")
            payload["supervisor_snapshot"] = capture_flow_supervisor(frozen, curator).model_dump(mode="json")
        targets.append(BenchmarkTargetCatalogEntry.model_validate_json(json.dumps(payload)))
    if {(item.target.kind, item.target.id) for item in targets} != freeze_targets:
        raise ValueError("Selected installed target is unavailable")
    used = {slot for target in targets for slot in target.route_slots}
    return BenchmarkRouteCatalog(
        models=catalog.models, route_slots=tuple(slot for slot in catalog.route_slots if slot.slot in used),
        targets=tuple(targets),
    )
