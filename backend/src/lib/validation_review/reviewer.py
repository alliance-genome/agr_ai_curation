"""One configured reviewer for direct/workspace phases; no runtime activation here."""

import asyncio

from src.lib.openai_agents.model_request_measurement import (
    install_model_request_measurement,
)

from . import admission
from .provider import ExecutionBudget, budget_scope, guarded_await
from .tools import create_tools

install_model_request_measurement()


def _require_review_tools(agent):
    """Check the authorized source tools before any deferred-surface compilation."""
    from src.lib.packages.tool_roles import require_supervisor_review_tool_boundary

    surface = getattr(agent, "tool_surface", None)
    tools = surface.source_tools if surface is not None and agent.tools is surface.tools else agent.tools
    require_supervisor_review_tool_boundary(tool.name for tool in tools)


def create_reviewer(
    context,
    *,
    checkpoint_provider,
    authenticated_groups,
    refresh_inputs=None,
):
    from src.lib.agent_studio.catalog_service import get_agent_by_id
    from src.lib.openai_agents.tool_surface import apply_tool_surface
    from src.lib.observability.cost_context import (
        get_agent_cost_identity,
        attach_agent_cost_identity,
    )

    agent = get_agent_by_id(
        "validation_reviewer",
        user_id=context.history_scope.owner_id,
        document_id=context.history_scope.document_id,
        authenticated_groups=list(authenticated_groups),
    )
    agent.tools = create_tools(
        context, checkpoint_provider=checkpoint_provider, refresh_inputs=refresh_inputs
    )
    agent.tool_surface_runtime = "validation_reviewer"
    identity = get_agent_cost_identity(agent)
    attach_agent_cost_identity(agent, {**identity, "agent_role": "supervisor"})
    _require_review_tools(agent)
    apply_tool_surface(agent, runtime="validation_reviewer")
    return agent


def create_supervisor_phase(
    context, *, supervisor, checkpoint_provider, authenticated_groups, **callbacks
):
    """Same resolved supervisor identity/model/policy; a strictly smaller capability set."""
    from src.lib.observability.cost_context import (
        get_agent_cost_identity,
        attach_agent_cost_identity,
    )
    from src.lib.openai_agents.tool_surface import apply_tool_surface
    from .protocol import REVIEW_PROTOCOL

    from src.lib.prompts.assembly import (
        PromptLayer,
        _bundle,
        _make_layer,
        prompt_templates_for_bundle,
    )
    from src.lib.prompts.context import set_pending_prompts, bind_prompt_run
    from src.lib.openai_agents.langfuse_client import log_agent_config

    layers: list[PromptLayer] = list(
        getattr(supervisor, "validation_review_policy_layers", ())
    )
    layers.append(
        _make_layer(
            layer_id="supervisor:validation_review",
            kind="runtime_context",
            title="Supervisor validation review",
            content=REVIEW_PROTOCOL
            + "\n"
            + "Review the bound saved extraction using saved evidence and history. "
            "Delegate paper investigation through the authorized targeted-follow-up tool when available. "
            "Never read the paper directly, dispatch arbitrary agents, change the workflow, or author evidence. "
            "Only saved configured extractors and validators may perform the explicitly requested follow-up.",
            provenance="backend:validation_review",
            editable=False,
            locked=True,
            source_ref="src.lib.validation_review.protocol:REVIEW_PROTOCOL",
        )
    )
    bundle = _bundle("supervisor", layers)
    agent = supervisor.clone(
        name=supervisor.name + " Validation Review",
        instructions=bundle.render(),
        tools=create_tools(
            context, checkpoint_provider=checkpoint_provider, **callbacks
        ),
        handoffs=[],
        output_type=None,
    )
    identity = get_agent_cost_identity(supervisor)
    attach_agent_cost_identity(
        agent, {**identity, "agent_id": context.actor_id, "agent_name": agent.name}
    )
    agent.tool_surface_base_instructions = agent.instructions
    agent.tool_surface_runtime = "validation_reviewer"
    prompt_id = set_pending_prompts(
        agent.name,
        list(prompt_templates_for_bundle(bundle)),
        effective_prompt_hash=bundle.hash,
        layer_manifest=bundle.to_manifest(),
    )
    bind_prompt_run(agent, prompt_id)
    _require_review_tools(agent)
    apply_tool_surface(agent, runtime="validation_reviewer")
    log_agent_config(
        agent_name=agent.name,
        instructions=agent.instructions,
        model=str(agent.model),
        tools=[tool.name for tool in agent.tools],
        model_settings=agent.model_settings.to_json_dict()
        if agent.model_settings
        else {},
        metadata={
            "review_episode_id": context.episode_id,
            "actor_id": context.actor_id,
            "effective_prompt_hash": bundle.hash,
            "layer_manifest": bundle.to_manifest(),
        },
    )
    return agent


async def run(context, agent, *, input, run_config=None):
    """A model's final prose cannot mark the episode complete; consult the ledger."""
    from src.lib.validation_history import store

    try:

        def policy_turns():
            with store.SessionLocal.begin() as db:
                ep, _ = admission.lock_episode(
                    db, context.history_scope, context.episode_id
                )
                return ep.policy["reviewer_turns"]

        turns = await asyncio.to_thread(policy_turns)
        from src.lib.observability.cost_context import get_agent_cost_identity

        budget = ExecutionBudget(
            context.history_scope,
            context.episode_id,
            "reviewer_tokens",
            True,
            reviewer_agent_id=get_agent_cost_identity(agent)["agent_id"],
        )
        with budget_scope(budget):
            from src.lib.openai_agents.tool_surface import apply_tool_surface

            _require_review_tools(agent)
            apply_tool_surface(agent, runtime="validation_reviewer")
            from src.lib.openai_agents.runner import (
                run_agent_with_owned_openai_resources,
            )
            from src.lib.prompts.context import commit_pending_prompts

            commit_pending_prompts(agent)
            flush_phase_config()
            from agents import RunConfig
            from dataclasses import replace

            phase_config = replace(
                run_config or RunConfig(),
                tool_not_found_behavior="return_error_to_model",
            )
            result = await guarded_await(
                run_agent_with_owned_openai_resources(
                    agent, input, run_config=phase_config, max_turns=turns
                )
            )
    except Exception as error:
        from .stops import review_stop

        stopped = review_stop(error)
        if stopped is not None:
            return await _stopped_response(context, stopped)
        from .audit import report_operational_failure

        report_operational_failure(error)
        raise
    return {
        "output": result.final_output,
        "review": await asyncio.to_thread(
            admission.status, context.history_scope, context.episode_id
        ),
    }


async def _stopped_response(context, exc):
    from src.lib.openai_agents.model_context_view import ContextRecoveryError

    status = await asyncio.to_thread(
        admission.status, context.history_scope, context.episode_id
    )
    return {
        "state": "complete" if status["ready"] else "needs_curator_review",
        "reason": (
            exc.reason
            if isinstance(exc, admission.ReviewStopped)
            else (
                "expired"
                if isinstance(exc, asyncio.TimeoutError)
                else "unsupported_exact_context_admission"
                if isinstance(exc, ContextRecoveryError)
                else "cancelled"
                if isinstance(exc, asyncio.CancelledError)
                else "reviewer_turns_exhausted"
            )
        ),
        "resource": (
            exc.resource if isinstance(exc, admission.ReviewStopped) else None
        ),
        "review": status,
    }


def create_direct_supervisor(
    *,
    active_groups,
    model_override=None,
    temperature_override=None,
    reasoning_override=None,
):
    """Resolve supervisor policy/settings without building or logging a routing agent."""
    from agents import Agent
    from src.lib.openai_agents.config import (
        get_agent_config,
        get_model_for_agent,
        resolve_model_provider,
        PromptCacheIdentity,
    )
    from src.lib.openai_agents.agents.supervisor_agent import _build_model_settings
    from src.lib.prompts.assembly import build_agent_prompt_layers
    from src.lib.observability.cost_context import (
        attach_agent_cost_identity,
        agent_identity,
    )

    config = get_agent_config("supervisor")
    model = str(model_override or config.model).strip() or config.model
    provider = resolve_model_provider(model)
    bundle = build_agent_prompt_layers("supervisor", group_id=active_groups)
    layers = tuple(
        layer
        for layer in bundle.layers
        if layer.kind in {"group_rules", "curator_overlay"}
    )
    policy = "You are the supervisor for this direct-agent chat. Use only the capabilities provided for the current phase."
    policy += "\n\n" + "\n\n".join(layer.content for layer in layers)
    agent = Agent(
        name="Direct Chat Supervisor",
        instructions=policy,
        model=get_model_for_agent(model, provider_override=provider),
        model_settings=_build_model_settings(
            model,
            temperature=temperature_override
            if temperature_override is not None
            else config.temperature,
            reasoning_effort=reasoning_override
            if reasoning_override is not None
            else config.reasoning,
            provider_override=provider,
            prompt_cache=PromptCacheIdentity(
                agent_key="supervisor", static_prompt=policy
            ),
        ),
        tools=[],
    )
    agent.validation_review_policy_layers = layers
    attach_agent_cost_identity(
        agent,
        {
            **agent_identity(
                "supervisor.direct_chat", agent.name, "supervisor", bundle.hash
            ),
            "provider": provider,
        },
    )
    return agent


def flush_phase_config():
    from types import SimpleNamespace
    from src.lib.openai_agents.extraction_trace_events import (
        get_current_extraction_trace_run,
    )
    from src.lib.openai_agents.langfuse_client import flush_agent_configs

    trace = get_current_extraction_trace_run()
    if trace is not None:
        flush_agent_configs(
            SimpleNamespace(trace_id=trace.trace_id, id=trace.observation_id)
        )
