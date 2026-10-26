"""Opt-in trusted review execution context, enforced at each measured model call.

This is the shared D API; actor integrations establish the context in E/F/G.
Unsupported request shapes stop explicitly before generation. Accounting is
independent of optional cost telemetry and includes inherited nested calls.
"""

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from datetime import timedelta
from uuid import uuid4

from . import admission
from src.lib.validation_history import store


@dataclass
class _ExecutionState:
    deadline: float | None = None
    clock_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    provider_admitted: bool = False


@dataclass(frozen=True)
class ExecutionBudget:
    scope: store.HistoryScope
    episode_id: str
    token_pool: str
    reviewer: bool
    reviewer_agent_id: str | None = None
    work_id: str | None = None
    execution: _ExecutionState = field(default_factory=_ExecutionState, compare=False)

    def __post_init__(self):
        if self.reviewer and not self.reviewer_agent_id:
            raise ValueError("Reviewer budget requires an explicit reviewer agent ID")


ACTIVE_BUDGET: ContextVar[ExecutionBudget | None] = ContextVar(
    "validation_review_budget", default=None
)


@contextmanager
def budget_scope(budget):
    if budget.token_pool not in {
        "reviewer_tokens",
        "initial_tokens",
        "reconsideration_tokens",
    }:
        raise ValueError("Unknown review token pool")
    token = ACTIVE_BUDGET.set(budget)
    try:
        yield
    finally:
        ACTIVE_BUDGET.reset(token)


def remaining_seconds(budget):
    with store.SessionLocal.begin() as db:
        ep, _ = admission.lock_episode(db, budget.scope, budget.episode_id)
        if ep.started_at is None:
            return ep.policy["deadline_seconds"]
        return max(
            0,
            (
                ep.started_at
                + timedelta(seconds=ep.policy["deadline_seconds"])
                - admission.now()
            ).total_seconds(),
        )


async def guarded_await(awaitable):
    budget = ACTIVE_BUDGET.get()
    if budget is None:
        return await awaitable
    try:
        # The persisted deadline is immutable. Read once, off the event loop;
        # streaming deltas never take scope/artifact write locks. Admission at
        # each model/tool and the executor heartbeat still observe cancellation.
        async with budget.execution.clock_lock:
            if budget.execution.deadline is None:
                seconds = await asyncio.to_thread(remaining_seconds, budget)
                budget.execution.deadline = asyncio.get_running_loop().time() + seconds
        timeout = max(0, budget.execution.deadline - asyncio.get_running_loop().time())
    except BaseException:
        if hasattr(awaitable, "close"):
            awaitable.close()
        raise
    async with asyncio.timeout(timeout):
        return await awaitable


async def admit_model(
    model,
    settings,
    *,
    instructions,
    input,
    tools,
    output_schema,
    handoffs,
    previous_response_id,
    conversation_id,
    prompt,
    provider_hint,
    agent=None,
):
    budget = ACTIVE_BUDGET.get()
    if budget is None:
        return settings, None
    from src.lib.openai_agents.model_request_measurement import describe_model
    from src.lib.openai_agents.context_capacity import NativeCapacityRequest

    provider, api, transport = describe_model(model, provider_hint=provider_hint)
    if (provider, api) != ("openai", "responses"):
        raise admission.ReviewStopped("unsupported_hard_admission")
    from agents import FunctionTool

    if any(not isinstance(tool, FunctionTool) for tool in tools):
        raise admission.ReviewStopped("unsupported_hosted_tool_accounting")
    # The pinned WS adapter does not replay a frame once sending starts.
    # HTTP client retries are below measurement and must be disabled by the
    # trusted runtime before entering this context, never silently ignored.
    if transport == "http" and model._get_client().max_retries != 0:
        raise admission.ReviewStopped("unsupported_hidden_http_retries")
    from src.lib.openai_agents.model_context_view import ContextRecoveryError

    try:
        request = NativeCapacityRequest(
            model,
            settings,
            instructions=instructions,
            tools=tools,
            output_schema=output_schema,
            handoffs=handoffs,
            previous_response_id=previous_response_id,
            conversation_id=conversation_id,
            prompt=prompt,
        )
        from src.lib.config.models_loader import get_model
        from src.lib.openai_agents.config import get_agent_model_capacity_overrides

        wire = request._wire([])
        if wire.get("context_management"):
            raise admission.ReviewStopped("unsupported_native_compaction_accounting")
        ceiling = wire.get("max_output_tokens")
        if ceiling is None:
            overrides = get_agent_model_capacity_overrides().get(
                request.effective_model, {}
            )
            definition = get_model(request.effective_model)
            ceiling = (
                overrides["max_output_tokens"]
                if "max_output_tokens" in overrides
                else (definition.max_output_tokens if definition is not None else None)
            )
        if type(ceiling) is not int or ceiling <= 0:
            raise admission.ReviewStopped("unsupported_output_ceiling")
        # Preserve the existing permitted ceiling, and explicitly send it.
        settings = replace(settings, max_tokens=ceiling)
        request.set_settings(settings)
        if request._wire([]).get("max_output_tokens") != ceiling:
            raise admission.ReviewStopped("unsupported_output_override")
        items = (
            [{"role": "user", "content": input}] if isinstance(input, str) else input
        )
        count = await guarded_await(request.count(items))
        if type(count) is not int or count < 0:
            raise admission.ReviewStopped("unsupported_input_count")
    except ContextRecoveryError as exc:
        raise admission.ReviewStopped("unsupported_exact_context_admission") from exc
    charges: dict[str, int] = {budget.token_pool: count + ceiling}
    from src.lib.observability.cost_context import get_agent_cost_identity

    agent_id = (
        get_agent_cost_identity(agent).get("agent_id") if agent is not None else None
    )
    if budget.reviewer and agent_id == budget.reviewer_agent_id:
        charges["reviewer_turns"] = 1
    operation_id = "provider:" + str(uuid4())
    request_details = {
        "work_id": budget.work_id,
        "model": request.effective_model,
        "input_tokens": count,
        "output_ceiling": ceiling,
        "transport": transport,
    }
    while True:
        try:
            rid = await asyncio.to_thread(
                admission.reserve, budget.scope, budget.episode_id,
                operation_id=operation_id, charges=charges, request=request_details,
            )
            break
        except admission.ReviewStopped as error:
            if not error.temporary:
                raise
            # Internal capacity polling; the existing episode deadline bounds
            # waiting. Every retry rechecks access, Stop and all ancestor quotas.
            await guarded_await(asyncio.sleep(1))
    budget.execution.provider_admitted = True
    return settings, (budget, rid, charges)


def settle(reservation, usage):
    if reservation is None:
        return
    budget, rid, charges = reservation
    actual = None
    if usage and usage.get("status") == "reported":
        values = [usage.get("input_tokens"), usage.get("output_tokens")]
        if all(type(value) is int and value >= 0 for value in values):
            actual = {**charges, budget.token_pool: sum(values)}
    admission.reconcile(budget.scope, budget.episode_id, rid, actual=actual)


def charge_tool(name):
    budget = ACTIVE_BUDGET.get()
    if budget is not None and budget.reviewer:
        admission.reserve(
            budget.scope,
            budget.episode_id,
            operation_id="tool:" + str(uuid4()),
            charges={"reviewer_tools": 1},
            request={"tool": name},
        )


def wrap_budget_tools(tools):
    """Applied to all SDK function tools, including inherited nested-agent tools."""
    if ACTIVE_BUDGET.get() is None:
        return tools
    from copy import copy
    from agents import FunctionTool

    result = []
    for tool in tools:
        if not isinstance(tool, FunctionTool) or getattr(
            tool, "_review_budget_wrapped", False
        ):
            result.append(tool)
            continue
        wrapped = copy(tool)

        async def invoke(ctx, arguments, _tool=tool):
            await asyncio.to_thread(charge_tool, _tool.name)
            return await guarded_await(_tool.on_invoke_tool(ctx, arguments))

        wrapped.on_invoke_tool = invoke
        wrapped._review_budget_wrapped = True
        result.append(wrapped)
    return result


def guard_direct_request():
    if ACTIVE_BUDGET.get() is not None:
        # /compact has no enforceable max_output_tokens; it cannot be treated
        # as hard-budgeted based on post-call usage or a character estimate.
        raise admission.ReviewStopped("unsupported_direct_request_hard_admission")
