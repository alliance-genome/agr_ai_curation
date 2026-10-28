"""Opt-in trusted review execution context, enforced at each measured model call.

This is the shared D API; actor integrations establish the context in E/F/G.
Unsupported request shapes stop explicitly before generation. Accounting is
independent of optional cost telemetry and includes inherited nested calls.
"""

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import timedelta
from uuid import uuid4

from . import admission
from src.lib.validation_history import store


@dataclass
class _ExecutionState:
    deadline: float | None = None
    clock_initialized: bool = False
    clock_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    provider_admitted: bool = False
    owner_stopped: bool = False
    submitted: bool = False
    capture_owner_active: bool = False


@dataclass(frozen=True)
class ExecutionBudget:
    scope: store.HistoryScope
    episode_id: str
    token_pool: str
    reviewer: bool
    reviewer_agent_id: str | None = None
    work_id: str | None = None
    lease_token: str | None = None
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
    previous_owner = budget.execution.capture_owner_active
    budget.execution.capture_owner_active = True
    try:
        yield
    finally:
        # Copied task/thread contexts must not suppress reports after the owner exits.
        budget.execution.capture_owner_active = previous_owner
        ACTIVE_BUDGET.reset(token)


def remaining_seconds(budget):
    with store.SessionLocal.begin() as db:
        ep, _ = admission.lock_episode(db, budget.scope, budget.episode_id)
        if ep.policy["deadline_seconds"] is None:
            return None
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
            if not budget.execution.clock_initialized:
                seconds = await asyncio.to_thread(remaining_seconds, budget)
                if seconds is not None:
                    budget.execution.deadline = asyncio.get_running_loop().time() + seconds
                budget.execution.clock_initialized = True
        timeout = (None if budget.execution.deadline is None else
                   max(0, budget.execution.deadline - asyncio.get_running_loop().time()))
    except BaseException:
        if hasattr(awaitable, "close"):
            awaitable.close()
        raise
    deadline = asyncio.timeout(timeout)
    try:
        async with deadline:
            return await awaitable
    except TimeoutError as error:
        # Distinguish our deadline from a provider timeout for error reporting.
        # Keep the existing review outcome classification unchanged.
        if deadline.expired():
            error._review_deadline_expired = True
        raise


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
    provider, api, transport = describe_model(model, provider_hint=provider_hint)
    if (provider, api) != ("openai", "responses"):
        raise admission.ReviewStopped("unsupported_review_provider")
    from agents import FunctionTool

    if any(not isinstance(tool, FunctionTool) for tool in tools):
        raise admission.ReviewStopped("unsupported_hosted_tool_accounting")
    if transport == "http" and model._get_client().max_retries != 0:
        raise admission.ReviewStopped("unsupported_hidden_http_retries")
    from src.lib.observability.cost_context import get_agent_cost_identity

    agent_id = get_agent_cost_identity(agent).get("agent_id") if agent is not None else None
    reservation = await register_provider_call(
        operation="responses", model=str(getattr(model, "model", "unknown")),
        reviewer_turn=budget.reviewer and agent_id == budget.reviewer_agent_id,
    )
    # Model capacity is checked by the shared context path before this call.
    # Preserve output settings: usage accounting never invents or shrinks a cap.
    return settings, reservation


async def register_provider_call(*, operation, model, reviewer_turn=False):
    """Register a measured call without reserving hypothetical token spend."""
    budget = ACTIVE_BUDGET.get()
    if budget is None:
        return None
    charges = {budget.token_pool: 0}
    if reviewer_turn:
        charges["reviewer_turns"] = 1
    if budget.execution.owner_stopped:
        raise admission.ReviewStopped("cancelled")
    fence = ({"work_id": budget.work_id, "lease_token": budget.lease_token}
             if budget.work_id is not None else {})
    registering = asyncio.create_task(asyncio.to_thread(
        admission.reserve, budget.scope, budget.episode_id,
        operation_id="provider:" + str(uuid4()), charges=charges,
        request={"work_id": budget.work_id, "model": model, "operation": operation,
                 "usage_metered": True, "token_pool": budget.token_pool}, **fence,
    ))
    try:
        rid = await asyncio.shield(registering)
    except asyncio.CancelledError:
        budget.execution.owner_stopped = True
        # A cancelled asyncio wrapper cannot cancel a running SQL transaction.
        # Resolve it before classifying work or releasing its accounting owner.
        while not registering.done():
            try:
                await asyncio.shield(registering)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        error = registering.exception()
        if error is None:
            rid = registering.result()
            budget.execution.provider_admitted = True
            # This function never returned the reservation, so no provider
            # invocation used it. Record known zero tokens, not missing usage.
            settling = asyncio.create_task(asyncio.to_thread(
                admission.reconcile, budget.scope, budget.episode_id, rid,
                actual=charges,
            ))
            while not settling.done():
                try:
                    await asyncio.shield(settling)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            error = settling.exception()
        if error is not None:
            from .stops import review_stop
            if review_stop(error) is None:
                from .audit import report_operational_failure
                report_operational_failure(error)
        raise
    budget.execution.provider_admitted = True
    if budget.execution.owner_stopped:
        # Cancellation can arrive from the owner on a different event loop
        # while this worker waits for SQL. Never send the newly reserved call.
        closing = asyncio.create_task(asyncio.to_thread(
            admission.reconcile, budget.scope, budget.episode_id, rid, actual=charges,
        ))
        cancelled = None
        while not closing.done():
            try:
                await asyncio.shield(closing)
            except asyncio.CancelledError as error:
                cancelled = error
        closing.result()
        if cancelled is not None:
            raise cancelled
        raise admission.ReviewStopped("cancelled")
    return budget, rid, charges


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


def guard_direct_request(*, provider=None, api=None):
    if ACTIVE_BUDGET.get() is not None and (provider, api) != ("openai", "responses.compact"):
        raise admission.ReviewStopped("unsupported_review_direct_request")
