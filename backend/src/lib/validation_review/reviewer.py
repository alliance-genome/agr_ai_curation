"""One configured reviewer for direct/workspace phases; no runtime activation here."""

import asyncio
from agents import Runner

from src.lib.openai_agents.model_request_measurement import (
    install_model_request_measurement,
)

from . import admission
from .provider import ExecutionBudget, budget_scope, guarded_await
from .tools import create_tools

install_model_request_measurement()


def create_reviewer(
    context,
    *,
    checkpoint_provider,
    authenticated_groups,
    reading_tools=(),
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
    ) + list(reading_tools)
    agent.tool_surface_runtime = "validation_reviewer"
    identity = get_agent_cost_identity(agent)
    attach_agent_cost_identity(agent, {**identity, "agent_role": "supervisor"})
    apply_tool_surface(agent, runtime="validation_reviewer")
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

            apply_tool_surface(agent, runtime="validation_reviewer")
            result = await guarded_await(
                Runner.run(agent, input=input, run_config=run_config, max_turns=turns)
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
