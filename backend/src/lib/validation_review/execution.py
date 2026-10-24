"""Recoverable executor for trusted validator adapters and the shared reviewer."""

import asyncio
from dataclasses import dataclass
from typing import Awaitable, Callable

from src.lib.validation_history import store
from src.models.sql.validation_review import ValidationReviewWork
from . import admission, work
from .provider import ExecutionBudget, budget_scope, guarded_await
from .protocol import RECONSIDERATION_PROTOCOL


@dataclass(frozen=True)
class ValidatorOutcome:
    attempt_id: str
    state: str
    result: dict | None
    diagnostics: dict


async def execute(
    scope, work_id, *, call: Callable[[dict], Awaitable[list[ValidatorOutcome]]]
):
    """Adapter must reauthorize the exact receipt and honor supplied-context tools.

    It receives a frozen invocation, including all logical attempts sharing one
    execution. Model calls inherit the hard budget through the measured seam.
    The adapter returns per-attempt answers, never selected/public values.
    """
    claim = await asyncio.to_thread(work.claim, scope, work_id)
    if claim is None:
        return {"state": "not_claimed", "work_id": work_id}
    with store.SessionLocal() as db:
        row = db.get(ValidationReviewWork, work_id)
        episode_id = row.episode_id
        ep, _ = admission.lock_episode(db, scope, episode_id)
        artifact_id = ep.artifact_id
    from copy import deepcopy

    payload = deepcopy(claim["payload"])
    expected = tuple(payload["invocation"]["attempt_ids"])
    payload = {
        **payload,
        "reconsideration_protocol": (
            RECONSIDERATION_PROTOCOL
            if payload["token_pool"] == "reconsideration_tokens"
            else None
        ),
    }
    budget = ExecutionBudget(scope, episode_id, payload["token_pool"], reviewer=False)
    await asyncio.to_thread(work.mark_submitted, scope, work_id, claim["lease_token"])
    try:
        with budget_scope(budget):
            results = await guarded_await(call(payload))
        if len(results) != len(expected) or {r.attempt_id for r in results} != set(
            expected
        ):
            raise store.HistoryConflict(
                "Validator outcome coverage differs from frozen invocation"
            )
        for result in results:
            await asyncio.to_thread(
                store.complete_attempt,
                scope,
                artifact_id,
                result.attempt_id,
                state=result.state,
                result=result.result,
                diagnostics=result.diagnostics,
            )
        outcome = {"completion_attempts": list(expected)}
        await asyncio.to_thread(
            work.finish, scope, work_id, claim["lease_token"], outcome=outcome
        )
        return {"state": "completed", "work_id": work_id, **outcome}
    except (
        admission.ReviewStopped,
        asyncio.TimeoutError,
        asyncio.CancelledError,
    ) as stopped:
        # A cancellation can race a submitted provider call. Do not create a
        # fictitious scientific failure or release uncertain charges.
        await asyncio.to_thread(
            work.mark_uncertain,
            scope,
            work_id,
            claim["lease_token"],
            reason=type(stopped).__name__,
        )
        raise
    except Exception as error:
        # Submitted work remains recoverable/uncertain. Respect any existing
        # provider-error capture and mark ownership for enclosing runtimes.
        from .audit import report_operational_failure

        await asyncio.to_thread(
            work.mark_uncertain,
            scope,
            work_id,
            claim["lease_token"],
            reason=type(error).__name__,
        )
        report_operational_failure(error)
        raise
