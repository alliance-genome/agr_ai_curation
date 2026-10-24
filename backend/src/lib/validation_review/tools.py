"""Bound tool adapters shared by direct/workspace and named supervisors."""

import asyncio

from agents import function_tool

from . import decisions, reads, scheduling
from .admission import ReviewStopped
from src.lib.validation_history import store
from functools import wraps
from inspect import signature
from .decisions import ReviewEntry


from pydantic import BaseModel, ConfigDict, StrictStr


class EvidenceReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    fact_id: StrictStr
    sha256: StrictStr


def _recoverable_tool(function):
    contract = signature(function)

    @wraps(function)
    async def invoke(*args, **kwargs):
        arguments = contract.bind(*args, **kwargs).arguments
        try:
            return await function(*args, **kwargs)
        except ReviewStopped as exc:
            if not (
                exc.reason
                in {
                    "scientific_attempts_exhausted",
                    "fresh_prerequisite_adapter_required",
                    "prerequisite_review_pending",
                    "non_validator_prerequisite",
                    "required_inputs_unavailable",
                }
                or (
                    exc.reason == "budget_exhausted"
                    and exc.resource == "extra_attempts"
                )
            ):
                raise
            return {
                "state": "not_applied",
                "code": exc.reason,
                "resource": exc.resource,
                "applied": [],
                "targets": arguments.get("target_ids", []),
            }
        except (store.HistoryConflict, store.HistoryAccessError, ValueError) as exc:
            return {
                "state": "conflict",
                "code": type(exc).__name__,
                "applied": [],
                "message": str(exc),
                "operation_id": arguments.get("operation_id"),
                "targets": arguments.get("target_ids", []),
                "decision_units": [
                    e.decision_unit_id for e in arguments.get("entries", [])
                ],
            }

    return invoke


def create_tools(context, *, checkpoint_provider, refresh_inputs=None,
                 execute_work=None, advance_work=None, prepare_inputs=None):
    """Context and checkpoint loader come from authenticated runtime, never JSON."""

    @function_tool(failure_error_function=None)
    @_recoverable_tool
    async def get_validation_review_packet(operation_id: str) -> dict:
        """Pin exact original/answer/decision/prerequisite references and budget state.

        Use the returned fact ID/SHA with read_validation_history to read every page.
        Reuse an operation ID only to retrieve the same immutable snapshot.
        """
        return await asyncio.to_thread(
            reads.snapshot,
            context.history_scope,
            context.episode_id,
            operation_id=operation_id,
        )

    @function_tool(failure_error_function=None)
    @_recoverable_tool
    async def read_validation_history(
        fact_id: str, sha256: str, offset: int = 0
    ) -> dict:
        """Read exact canonical JSON bytes from an authorized immutable fact.

        Follow next_offset until null; concatenate content in order. Never infer
        missing data from one page. SHA and offsets pin the same complete body.
        """
        return await asyncio.to_thread(
            reads.read,
            context.history_scope,
            context.episode_id,
            fact_id=fact_id,
            sha256=sha256,
            offset=offset,
        )

    @function_tool(failure_error_function=None)
    @_recoverable_tool
    async def record_validation_review(
        operation_id: str, entries: list[ReviewEntry]
    ) -> dict:
        """Atomically record explicit decisions for exact target/answer components.

        expected_version is the current head version, not selection_generation.
        Omitted targets remain pending. Accept caveated/unresolved complete answers
        when appropriate. Reject leaves an older selection intact; revoke withdraws
        it. Operational failures require acknowledge_failure, not scientific rejection.
        """
        request = await asyncio.to_thread(checkpoint_provider)
        result = await asyncio.to_thread(
            decisions.record_decisions,
            context,
            checkpoint_request=request,
            operation_id=operation_id,
            entries=[entry.model_dump(mode="json") for entry in entries],
        )
        if advance_work is not None:
            result = {**result, "dependent_work": await advance_work()}
        return result

    @function_tool(failure_error_function=None)
    @_recoverable_tool
    async def request_validation_reconsideration(
        operation_id: str,
        parent_decision_id: str,
        target_ids: list[str],
        instructions: str,
        evidence_refs: list[EvidenceReference],
    ) -> dict:
        """Queue another attempt only for named reviewed bindings using saved validators.

        The parent decision must name each exact previous answer. Supply precise
        guidance and existing evidence fact references; do not author new evidence.
        Whole-binding retry coverage can exceed an individual decision unit.
        Returns queued work identity, not a validation result or acceptance.
        """
        if prepare_inputs is not None:
            await asyncio.to_thread(prepare_inputs)
        work_id = await asyncio.to_thread(
            scheduling.reconsider,
            context,
            operation_id=operation_id,
            parent_decision_id=parent_decision_id,
            target_ids=target_ids,
            instructions=instructions,
            evidence_refs=[ref.model_dump(mode="json") for ref in evidence_refs],
            refresh_inputs=refresh_inputs,
        )
        if execute_work is not None:
            return {**await execute_work(work_id), "coverage": target_ids,
                    "instruction": "Read the new answer and record a separate decision."}
        return {"state": "queued", "work_id": work_id, "coverage": target_ids}

    @function_tool(failure_error_function=None)
    @_recoverable_tool
    async def get_validation_review_status() -> dict:
        """Return readiness and an exact history reference to full budgets/uncovered work.

        Read its fact ID/SHA with read_validation_history for all remaining budgets,
        warning thresholds and uncovered targets, with exact continuation if needed.
        """
        return await asyncio.to_thread(
            reads.status_reference, context.history_scope, context.episode_id
        )

    return [
        get_validation_review_packet,
        read_validation_history,
        record_validation_review,
        request_validation_reconsideration,
        get_validation_review_status,
    ]
