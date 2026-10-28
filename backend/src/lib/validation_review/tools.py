"""Bound tool adapters shared by direct/workspace and named supervisors."""

import asyncio

from agents import function_tool

from . import decisions, reads, scheduling
from .admission import ReviewStopped
from src.lib.validation_history import store
from functools import wraps
from typing import Literal
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
        from .observations import record_tool

        arguments = contract.bind(*args, **kwargs).arguments
        try:
            result = await function(*args, **kwargs)
        except ReviewStopped as exc:
            if not (
                exc.reason
                in {
                    "scientific_attempts_exhausted",
                    "fresh_prerequisite_adapter_required",
                    "prerequisite_review_pending",
                    "prerequisite_reconsideration_required",
                    "non_validator_prerequisite",
                    "required_inputs_unavailable",
                    "source_input_changed",
                    "extraction_attempts_exhausted",
                    "saved_extractor_contract_unavailable",
                    "fresh_extraction_required",
                    "configured_extractor_changed",
                }
                or (
                    exc.reason == "budget_exhausted"
                    and exc.resource == "extra_attempts"
                )
            ):
                raise
            result = {
                "state": "not_applied",
                "code": exc.reason,
                "resource": exc.resource,
                "applied": [],
                "targets": arguments.get("target_ids", []),
            }
        except (store.HistoryConflict, store.HistoryAccessError, ValueError) as exc:
            result = {
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

        return record_tool(function.__name__, arguments, result)

    return invoke


def create_tools(context, *, checkpoint_provider, refresh_inputs=None, rebind_receipt=None,
                 execute_work=None, advance_work=None, prepare_inputs=None,
                 request_extraction=None, select_source=None, assess_source=None):
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
    async def get_outstanding_validation_reviews(cursor: str | None = None) -> dict:
        """Find current outstanding units and exact answer/optional-absence references.

        Pass next_cursor to continue the live keyset scan; completed earlier rows
        do not shift later pages. Start a fresh scan after changing source/coverage
        and before finishing review. scan_complete only ends this scan; ready checks
        the entire requested scope. Read exact evidence with read_validation_history
        or get_validation_record_source before deciding; listing is not assessment.
        """
        return await asyncio.to_thread(reads.outstanding, context.history_scope,
                                      context.episode_id, cursor=cursor)

    @function_tool(failure_error_function=None)
    @_recoverable_tool
    async def get_validation_record_source(target_id: str) -> dict:
        """Reference one original record and all its attached supporting evidence.

        Supply an object, field, element or binding target from the review packet.
        Read the returned exact fact with read_validation_history. This is the
        saved normalized source, not current selected values or a new validation.
        Historical records explicitly lack an attested original. Added/replacement
        records have contribution history instead of a fabricated baseline original.
        Full baseline and other history remain available when your assessment needs
        them; this tool neither reads the paper nor chooses relevant records for you.
        """
        from .record_source import snapshot

        return await asyncio.to_thread(
            snapshot, context.history_scope, context.episode_id, target_id=target_id
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

        For answers, target_id is attempt input.target_id; decision_unit_id is a
        covered ID matching the manifest unit target_id. No component units means
        decision_unit_id equals target_id: null decision_unit / empty decision_units
        describe whole-target review, not missing IDs. Components use covered child
        IDs. logical_attempt_id is the attempt fact ID, not the completion fact ID.
        expected_version is the unit's current head version, not selection_generation.
        Omitted targets remain pending. Accept caveated/unresolved complete answers
        when appropriate. For extraction-only acceptance, unresolved_fields names
        uncertain plain payload paths and explanations; it does not alter source values.
        For declared optional-input absence, use the exact applicability_ref fact/hash
        instead of an attempt or baseline, with its binding target and covered unit.
        Acceptance requires absence_assessment (omission_supported or unresolved)
        and your reason; optionality alone is not justification. No value is invented.
        Reject leaves an older selection intact; revoke withdraws
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
            refresh_inputs=refresh_inputs, rebind_receipt=rebind_receipt,
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

    @function_tool(failure_error_function=None)
    @_recoverable_tool
    async def request_targeted_extraction(operation_id: str, parent_decision_id: str,
                                           entry_ids: list[str], instructions: str) -> dict:
        """Ask the saved extractor to investigate named existing entries in this paper.

        First record the review decision motivating the investigation. Explain
        what is missing or ambiguous and useful search guidance. Runtime supplies
        originals, exact prior validator inputs/answers/caveats, your decision and
        current attempt state automatically. The extractor can use only its saved
        paper tools. Initial extraction plus one follow-up is the default ceiling;
        changing operation IDs cannot reset it. A proposal is not an accepted
        source or validation. Read it and separately select supported fields and
        item relationships, or retain a truthful no-new-information assessment.
        """
        assert request_extraction is not None  # Tool is exposed only with its bound callback.
        return await request_extraction(operation_id=operation_id, parent_decision_id=parent_decision_id,
            entry_ids=entry_ids, instructions=instructions)

    @function_tool(failure_error_function=None)
    @_recoverable_tool
    async def select_extraction_contribution(operation_id: str, contribution: EvidenceReference,
            field_paths: list[str], evidence_ids: list[str], expected_source_sha: str, reason: str) -> dict:
        """Approve exact proposed source fields/evidence and their item relationships.

        Read the full contribution first. Decide whether each proposed correction,
        merge, split, reorder, addition or item retirement within a record is justified.
        field_paths select complete proposals including their explicit predecessor
        mappings; the runtime never guesses identity from matching text or indices.
        Include the evidence required by selected fields. expected_source_sha is
        the current packet manifest's source_state_sha256. Originals stay in history;
        only affected validator inputs become stale. This does not accept validation
        or establish submission readiness. Curators do not manage item IDs.
        """
        assert select_source is not None  # Tool is exposed only with its bound callback.
        return await select_source(operation_id=operation_id,
            contribution_ref=contribution.model_dump(mode="json"), field_paths=field_paths,
            evidence_ids=evidence_ids, expected_source_sha=expected_source_sha, reason=reason)

    @function_tool(failure_error_function=None)
    @_recoverable_tool
    async def decide_record_retirement(operation_id: str, contribution: EvidenceReference,
            decision: Literal["approve", "decline"], expected_source_sha: str, reason: str) -> dict:
        """Approve or decline an extractor's whole-record retirement recommendation.

        Read its exact contribution, evidence and uncertainty first. Approval omits
        the record from current results without deleting original data or history;
        decline preserves current values and readiness. Curator authority is protected.
        Use the current packet manifest's source_state_sha256. A field or nested-item
        correction belongs to select_extraction_contribution instead.
        """
        assert select_source is not None
        return await select_source(operation_id=operation_id,
            contribution_ref=contribution.model_dump(mode="json"), retirement_decision=decision,
            expected_source_sha=expected_source_sha, reason=reason)

    @function_tool(failure_error_function=None)
    @_recoverable_tool
    async def validate_selected_extraction_source(operation_id: str, selection_id: str,
            target_ids: list[str], instructions: str) -> dict:
        """Validate changed inputs from an explicitly approved extraction contribution.

        Use the exact selection_id and affected binding target IDs returned by
        select_extraction_contribution. Explain what the new source clarifies and
        what the saved validator must reassess. Runtime automatically supplies the
        original extraction, previous requests/answers, selected source and your
        selection reason. Existing attempt limits still apply; selecting source
        never resets them. Read the returned answer before separately accepting it.
        Do not call this when the extractor found no new information or when no
        binding input changed. Uncertainty alone does not require another attempt.
        If prerequisite_reconsideration_required is returned, review the prerequisites
        first, then use request_validation_reconsideration for the existing answer.
        """
        assert assess_source is not None  # Tool is exposed only with its bound callback.
        return await assess_source(operation_id=operation_id, selection_id=selection_id,
            target_ids=target_ids, instructions=instructions)

    result = [
        get_validation_review_packet,
        get_outstanding_validation_reviews,
        get_validation_record_source,
        read_validation_history,
        record_validation_review,
        request_validation_reconsideration,
        get_validation_review_status,
    ]
    if all(callback is not None for callback in (request_extraction, select_source, assess_source)):
        result.extend([request_targeted_extraction, select_extraction_contribution,
                       decide_record_retirement, validate_selected_extraction_source])
    elif any(callback is not None for callback in (request_extraction, select_source, assess_source)):
        raise ValueError("Targeted extraction, source selection and assessment must be wired together")
    return result


def runtime_tool_contract():
    """Freeze the real reviewer tool schemas without invoking any tool or model."""
    async def unavailable(**arguments):
        raise RuntimeError("Contract inspection cannot execute work")
    return [{"name": tool.name, "description": tool.description, "parameters": tool.params_json_schema}
            for tool in create_tools(None, checkpoint_provider=lambda: None,
                request_extraction=unavailable, select_source=unavailable, assess_source=unavailable)]
