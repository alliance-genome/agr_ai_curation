"""Curator-visible route catalogs grounded in the same sources as execution."""

from sqlalchemy.orm import Session

from src.lib.agent_access import is_resource_access_allowed
from src.lib.agent_studio.agent_service import list_agents_visible_to_user
from src.lib.agent_studio.catalog_service import get_agent_metadata
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


def build_curator_route_catalog(session: Session, curator: BenchmarkCuratorContext) -> BenchmarkRouteCatalog:
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
    visible = {
        agent.agent_key: agent for agent in list_agents_visible_to_user(
            session, curator.db_user_id, active_group_ids=curator.active_groups,
        ) if agent.agent_key not in runtime_only
    }
    models = list_models()
    models_by_id = {model.model_id: model for model in models}

    def route(model: str, reasoning: str | None) -> BenchmarkSuiteRoute:
        definition = models_by_id.get(model)
        return BenchmarkSuiteRoute(
            provider=resolve_model_provider(model), model=model,
            reasoning_effort=normalize_reasoning_effort(reasoning)
            if definition is None or definition.supports_reasoning else None,
        )

    agent_defaults = {key: route(agent.model_id, agent.model_reasoning) for key, agent in visible.items()}
    supervisor = get_agent_config("supervisor")
    flow_agents: dict[str, tuple[str, ...]] = {}
    flow_validators: dict[str, tuple[str, ...]] = {}
    validator_defaults: dict[str, BenchmarkSuiteRoute] = {}

    def model_validators(schedule: list[dict]) -> dict[str, BenchmarkSuiteRoute] | None:
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
            definition = get_agent_definition_for_package(package, validator_agent)
            if definition is None:
                raise ValueError("Model validator agent is not configured")
            key = canonical_system_agent_key(definition)
            agent = visible.get(key)
            if agent is None or agent.visibility != "system":
                return None
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
        metadata = get_agent_metadata(key, _resolved_db_agent=visible[key])
        direct_bindings = model_validators([
            option.to_dict() for option in validation_attachment_options_for_agent(
                key, agent_registry={key: metadata},
            )
            if option.state.value == "active"
        ])
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
            if node.type != "agent":
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
    return build_route_catalog(
        models=tuple(BenchmarkModelCatalogEntry.model_validate({
            "provider": model.provider, "model": model.model_id,
            "reasoning_efforts": tuple(model.reasoning_options) if model.supports_reasoning else (),
        }) for model in models),
        supervisor_default=route(supervisor.model, supervisor.reasoning),
        agent_defaults=agent_defaults, model_validator_defaults=validator_defaults,
        agent_targets=direct_validators, agent_model_validators=direct_validators,
        flow_agents=flow_agents, flow_model_validators=flow_validators,
    )
