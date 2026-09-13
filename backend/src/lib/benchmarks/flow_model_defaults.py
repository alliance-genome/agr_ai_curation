"""Read each saved stage's actual model default, without constructing clients."""

from sqlalchemy.orm import Session

from src.lib.agent_studio.agent_service import list_agents_visible_to_user
from src.lib.agent_studio.execution_revision_service import get_execution_revision
from src.lib.config.models_loader import list_models
from src.lib.openai_agents.config import get_agent_config, normalize_reasoning_effort, resolve_model_provider

from .execution_context import BenchmarkCuratorContext
from .flow_stages import BenchmarkFlowStage
from .models import BenchmarkSuiteRoute


def stage_model_defaults(
    session: Session, curator: BenchmarkCuratorContext, stages: tuple[BenchmarkFlowStage, ...],
) -> tuple[tuple[BenchmarkFlowStage, ...], tuple[str, ...]]:
    """Preserve conflicting saved defaults; the wizard must ask for one choice.

    Two nodes may pin different revisions of the same agent while still sharing
    a canonical model override slot. Do not silently choose one node's default.
    """
    agents = {agent.agent_key: agent for agent in list_agents_visible_to_user(
        session, curator.db_user_id, active_group_ids=curator.active_groups,
    )}
    models = {model.model_id: model for model in list_models()}
    supervisor = get_agent_config("supervisor")
    result = []
    defaults: dict[str, BenchmarkSuiteRoute] = {}
    conflicts = set()
    for stage in stages:
        if stage.route_slot is None:
            result.append(stage)
            continue
        if stage.role == "supervisor":
            model_id, reasoning = supervisor.model, supervisor.reasoning
        elif stage.execution_receipt is not None:
            receipt = stage.execution_receipt
            _, saved = get_execution_revision(
                session, receipt.agent_id, receipt.agent_revision_id, curator.db_user_id,
                active_group_ids=list(curator.active_groups),
            )
            model_id, reasoning = saved.model_id, saved.model_reasoning
        else:
            agent = agents.get(stage.agent_id)
            if agent is None:
                raise ValueError("Stage model is unavailable to this curator")
            model_id, reasoning = agent.model_id, agent.model_reasoning
        model = models.get(model_id)
        if model is None:
            raise ValueError("Saved stage model is not in the installed model catalog")
        route = BenchmarkSuiteRoute(
            provider=resolve_model_provider(model_id), model=model_id,
            reasoning_effort=normalize_reasoning_effort(reasoning) if model.supports_reasoning else None,
        )
        if stage.route_slot in defaults and defaults[stage.route_slot] != route:
            conflicts.add(stage.route_slot)
        defaults[stage.route_slot] = route
        result.append(stage.model_copy(update={"default_route": route}))
    return tuple(result), tuple(sorted(conflicts))
