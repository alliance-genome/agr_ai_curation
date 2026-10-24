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
    from src.lib.openai_agents.model_request_measurement import (
        install_model_request_measurement,
    )
    from src.lib.openai_agents.model_context_view import ContextRecoveryError

    install_model_request_measurement()
    claim = await asyncio.to_thread(work.claim, scope, work_id)
    if claim is None:
        return {"state": "not_claimed", "work_id": work_id}

    def details():
        with store.SessionLocal() as db:
            row = db.get(ValidationReviewWork, work_id)
            ep, _ = admission.lock_episode(db, scope, row.episode_id)
            return ep.id, ep.artifact_id, ep.policy["lease_seconds"]

    episode_id, artifact_id, lease_seconds = await asyncio.to_thread(details)
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
    budget = ExecutionBudget(
        scope, episode_id, payload["token_pool"], reviewer=False, work_id=work_id
    )
    await asyncio.to_thread(work.mark_submitted, scope, work_id, claim["lease_token"])
    try:
        with budget_scope(budget):
            results = await _while_owned(
                guarded_await(call(payload)),
                scope,
                work_id,
                claim["lease_token"],
                lease_seconds,
            )
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
    except BaseException as error:
        from .stops import review_stop

        stopped = review_stop(error)
        if stopped is not None:
            reason = (
                stopped.reason
                if isinstance(stopped, admission.ReviewStopped)
                else "unsupported_exact_context_admission"
                if isinstance(stopped, ContextRecoveryError)
                else "expired"
                if isinstance(stopped, asyncio.TimeoutError)
                else "cancelled"
                if isinstance(stopped, asyncio.CancelledError)
                else "validator_turns_exhausted"
            )
            resource = getattr(stopped, "resource", None)
            try:
                if (
                    isinstance(stopped, (admission.ReviewStopped, ContextRecoveryError))
                    and not budget.execution.provider_admitted
                ):
                    for attempt in expected:
                        await asyncio.to_thread(
                            store.complete_attempt,
                            scope,
                            artifact_id,
                            attempt,
                            state="failed",
                            result=None,
                            diagnostics={
                                "operational_state": "not_admitted",
                                "reason": reason,
                                "resource": resource,
                            },
                        )
                    await asyncio.to_thread(
                        work.mark_not_admitted,
                        scope,
                        work_id,
                        claim["lease_token"],
                        reason=reason,
                        resource=resource,
                    )
                else:
                    # Cancellation may race a submitted provider call. Retain charges.
                    await asyncio.to_thread(
                        work.mark_uncertain,
                        scope,
                        work_id,
                        claim["lease_token"],
                        reason=reason,
                    )
            except Exception as recording_error:
                from .audit import report_operational_failure

                report_operational_failure(recording_error)
                stopped.add_note(
                    "Review stop recording failed; durable recovery is required"
                )
            if stopped is not error:
                raise stopped from error
            raise
        if not isinstance(error, Exception):
            raise
        # Submitted work remains recoverable/uncertain. Respect any existing
        # provider-error capture and mark ownership for enclosing runtimes.
        from .audit import report_operational_failure

        try:
            await asyncio.to_thread(
                work.mark_uncertain,
                scope,
                work_id,
                claim["lease_token"],
                reason=type(error).__name__,
            )
        except Exception:
            error.add_note(
                "Uncertain outcome recording failed; durable recovery is required"
            )
        report_operational_failure(error)
        raise


async def _while_owned(awaitable, scope, work_id, token, lease_seconds):
    async def pulse():
        while True:
            # Internal lease polling, derived from the frozen configurable lease.
            await asyncio.sleep(lease_seconds / 3)
            await asyncio.to_thread(work.heartbeat, scope, work_id, token)

    task = asyncio.ensure_future(awaitable)
    heartbeat = asyncio.create_task(pulse())
    try:
        done, _ = await asyncio.wait(
            {task, heartbeat}, return_when=asyncio.FIRST_COMPLETED
        )
        if heartbeat in done:
            await heartbeat
        return await task
    finally:
        for pending in (task, heartbeat):
            if not pending.done():
                pending.cancel()
        await asyncio.gather(task, heartbeat, return_exceptions=True)
