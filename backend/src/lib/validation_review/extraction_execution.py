"""Execute a durable targeted follow-up under the existing measured review budget."""
import asyncio
from copy import deepcopy

from src.lib.validation_history import store
from src.models.sql.validation_review import ValidationReviewWork as Work
from . import admission, work, extractor_phase, extraction_results
from .execution import _while_owned
from .provider import ExecutionBudget, budget_scope, guarded_await


async def execute(scope, work_id, *, resolve_agent):
    """resolve_agent reauthorizes only the saved receipt; never model-supplied IDs."""
    from src.lib.openai_agents.model_request_measurement import install_model_request_measurement
    from src.lib.openai_agents.tools.evidence_workspace import (
        set_active_evidence_records, reset_active_evidence_records,
    )
    from .audit import report_operational_failure
    from .stops import review_stop

    install_model_request_measurement()
    await asyncio.to_thread(work.recover, scope, work_id)
    claim = await asyncio.to_thread(work.claim, scope, work_id)
    if claim is None:
        return {"state": "not_claimed", "work_id": work_id}

    def details():
        with store.SessionLocal() as db:
            row = db.get(Work, work_id)
            if row.kind != "extraction_followup":
                raise store.HistoryAccessError("Not targeted extraction work")
            ep, _ = admission.lock_episode(db, scope, row.episode_id)
            return ep.id, ep.artifact_id, ep.policy

    episode_id, artifact_id, policy = await asyncio.to_thread(details)
    payload = deepcopy(claim["payload"])
    budget = ExecutionBudget(scope, episode_id, "reviewer_tokens", reviewer=False, work_id=work_id)
    # Explicit submission fence covers authorization/setup failures too. It is
    # marked not-admitted when we know no provider admission occurred.
    await asyncio.to_thread(work.mark_submitted, scope, work_id, claim["lease_token"])
    # Original records are read-only context, never mutable run-workspace entries.
    # Tools can revise/discard only evidence created by this follow-up invocation.
    evidence = []
    evidence_token = set_active_evidence_records(evidence)

    async def invoke():
        agent = await asyncio.to_thread(resolve_agent, payload["receipt"])
        pinned = payload["receipt"]["contract"]
        if extractor_phase.contract(agent, max_turns=pinned["max_turns"]) != pinned:
            raise admission.ReviewStopped("configured_extractor_changed")
        result = await extractor_phase.invoke(agent, scope=scope, artifact_id=artifact_id,
            packet=payload["specialist_input"], packet_bytes=policy["packet_bytes"],
            max_turns=pinned["max_turns"], evidence_records=evidence)
        return await asyncio.to_thread(extraction_results.retain, scope, work_id,
            claim["lease_token"], result, evidence_records=evidence)

    try:
        with budget_scope(budget):
            outcome = await _while_owned(guarded_await(invoke()), scope, work_id,
                                         claim["lease_token"], policy["lease_seconds"])
        await asyncio.to_thread(work.finish, scope, work_id, claim["lease_token"], outcome=outcome)
        return {"state": "completed", "work_id": work_id, **outcome}
    except BaseException as error:
        stopped = review_stop(error)
        reason = getattr(stopped, "reason", None) or (
            "cancelled" if isinstance(error, asyncio.CancelledError)
            else "expired" if isinstance(error, asyncio.TimeoutError)
            else type(error).__name__)
        try:
            if stopped is not None and not budget.execution.provider_admitted:
                await asyncio.to_thread(work.mark_not_admitted, scope, work_id,
                    claim["lease_token"], reason=reason, resource=getattr(stopped, "resource", None))
            else:
                await asyncio.to_thread(work.mark_uncertain, scope, work_id,
                    claim["lease_token"], reason=reason)
        except Exception as recording_error:
            report_operational_failure(recording_error)
            error.add_note("Follow-up outcome recording failed; durable recovery is required")
        if stopped is None and isinstance(error, Exception):
            report_operational_failure(error)
        if stopped is not None and stopped is not error:
            raise stopped from error
        raise
    finally:
        reset_active_evidence_records(evidence_token)
