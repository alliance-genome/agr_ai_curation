"""Bound tool adapters shared by direct/workspace and named supervisors."""

import asyncio

from agents import function_tool

from . import decisions, reads, scheduling
from .decisions import ReviewEntry


from pydantic import BaseModel, ConfigDict, StrictStr


class EvidenceReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    fact_id: StrictStr
    sha256: StrictStr


def create_tools(context, *, checkpoint_provider):
    """Context and checkpoint loader come from authenticated runtime, never JSON."""

    @function_tool(failure_error_function=None)
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
        return await asyncio.to_thread(
            decisions.record_decisions,
            context,
            checkpoint_request=request,
            operation_id=operation_id,
            entries=[entry.model_dump(mode="json") for entry in entries],
        )

    @function_tool(failure_error_function=None)
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
        work_id = await asyncio.to_thread(
            scheduling.reconsider,
            context,
            operation_id=operation_id,
            parent_decision_id=parent_decision_id,
            target_ids=target_ids,
            instructions=instructions,
            evidence_refs=[ref.model_dump(mode="json") for ref in evidence_refs],
        )
        return {"state": "queued", "work_id": work_id, "coverage": target_ids}

    @function_tool(failure_error_function=None)
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
