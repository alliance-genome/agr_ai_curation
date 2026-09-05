"""Curator-visible route catalogs grounded in the same sources as execution."""

from sqlalchemy.orm import Session

from src.lib.agent_access import is_resource_access_allowed
from src.lib.agent_studio.agent_service import list_agents_visible_to_user
from src.lib.agent_studio.flow_tools import build_flow_definition_from_recipe
from src.lib.config.agent_loader import canonical_system_agent_key, get_agent_definition_for_package
from src.lib.config.models_loader import list_models
from src.lib.curation_workspace.curation_prep_constants import CURATION_PREP_AGENT_ID
from src.lib.flow_edge_roles import SUPPORTED_OUTPUT_FORMATTER_AGENT_IDS
from src.lib.flows.executor import CURATION_HANDOFF_AGENT_ID
from src.lib.flows.validation_attachments import validation_schedule_from_node_data
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
    runtime_only = SUPPORTED_OUTPUT_FORMATTER_AGENT_IDS | {
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
            for validator in schedule["scheduled_validators"]:
                validator_agent = validator.get("validator_agent_id")
                if not validator_agent:
                    # Tool-backed validation is deterministic, not a model slot.
                    continue
                package = validator.get("validator_package_id")
                binding = validator.get("validator_binding_id")
                if not package or not binding:
                    raise ValueError("Model validator lacks its package/binding identity")
                agent_definition = get_agent_definition_for_package(package, validator_agent)
                if agent_definition is None:
                    raise ValueError("Model validator agent is not configured")
                key = canonical_system_agent_key(agent_definition)
                agent = visible.get(key)
                if agent is None or agent.visibility != "system":
                    accessible = False
                    break
                default = agent_defaults[key]
                if binding in validators and validators[binding] != default:
                    raise ValueError("Model validator binding has conflicting defaults")
                validators[binding] = default
            if not accessible:
                break
        if not accessible:
            continue
        for binding, default in validators.items():
            if binding in validator_defaults and validator_defaults[binding] != default:
                raise ValueError("Model validator binding has conflicting defaults")
            validator_defaults[binding] = default
        flow_agents[recipe["name"]] = tuple(sorted(agents))
        flow_validators[recipe["name"]] = tuple(sorted(validators))
    return build_route_catalog(
        models=tuple(BenchmarkModelCatalogEntry.model_validate({
            "provider": model.provider, "model": model.model_id,
            "reasoning_efforts": tuple(model.reasoning_options) if model.supports_reasoning else (),
        }) for model in models),
        supervisor_default=route(supervisor.model, supervisor.reasoning),
        agent_defaults=agent_defaults, model_validator_defaults=validator_defaults,
        agent_targets=visible, flow_agents=flow_agents, flow_model_validators=flow_validators,
    )
