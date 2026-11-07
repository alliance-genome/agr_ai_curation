"""Bound tool adapters shared by direct/workspace and named supervisors."""

import asyncio
from time import perf_counter

from agents import function_tool

from . import decisions, reads, scheduling
from .admission import ReviewStopped
from .progress import ReviewLines
from src.lib.validation_history import store
from functools import partial, wraps
from typing import Literal
from inspect import signature
from .decisions import PacketDecision, ReviewEntry


from pydantic import BaseModel, ConfigDict, StrictStr


class EvidenceReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    fact_id: StrictStr
    sha256: StrictStr


def _recoverable_tool(function, *, lines):
    contract = signature(function)

    @wraps(function)
    async def invoke(*args, **kwargs):
        from .observations import record_tool

        arguments = contract.bind(*args, **kwargs).arguments
        lines.start(function.__name__, arguments)
        result = None
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
            if exc.reason == "extraction_attempts_exhausted":
                # Every requested record had no follow-up left; name them all.
                result["exhausted"] = list(exc.exhausted)
        except (store.HistoryConflict, store.HistoryAccessError, ValueError) as exc:
            result = {
                "state": "conflict",
                "code": type(exc).__name__,
                "applied": [],
                "message": str(exc),
                "operation_id": arguments.get("operation_id"),
                "targets": arguments.get("target_ids", []),
                "decision_units": [
                    e.decision_unit_id for e in arguments.get("entries") or []
                ],
            }
            if arguments.get("packet_decisions"):
                result["handles"] = [d.handle for d in arguments["packet_decisions"]]
        finally:
            lines.finish(function.__name__, result)

        return record_tool(function.__name__, arguments, result)

    return invoke


def create_tools(context, *, checkpoint_provider, refresh_inputs=None, rebind_receipt=None,
                 execute_work=None, advance_work=None, prepare_inputs=None,
                 request_extraction=None, select_source=None, assess_source=None):
    """Context and checkpoint loader come from authenticated runtime, never JSON."""
    recoverable = partial(_recoverable_tool,
                          lines=ReviewLines(None if context is None else context.episode_id))

    @function_tool(failure_error_function=None)
    @recoverable
    async def read_review_packet(packet_id: str | None = None, page: int | None = None) -> dict:
        """Read all outstanding review work once: records, evidence and validator answers.

        Call with no packet_id to pin a fresh packet of the current state. If
        next_page is set, read it with the same packet_id before deciding. Each
        record shows its originally extracted values, the evidence it cites (E
        handles; each quote appears once under evidence, and same_quote_as marks
        another use of an earlier quote) and review items. Items with a D handle
        are decided with record_validation_review using this packet_id: record
        (the extracted record itself; no validator ran), validator_answer
        (additional_fields holds the validator's own verdict fields; prerequisites
        lists each selection a dependent answer used and the answer or source it
        chose, as exact references for read_validation_history),
        optional_input_absent, lookup_not_supported, or uncertain_outcome (a
        validation that stopped without an answer; only acknowledge_failure
        applies). A component answer lists one handle per component. waiting
        items, including fields whose newest validation is still running, cannot
        be decided now. A record's target_id is for follow-up tools. too_large
        names records to read with the exact-history tools instead. Handles
        belong to this packet only.
        """
        return await asyncio.to_thread(
            reads.review_packet, context.history_scope, context.episode_id,
            packet_id=packet_id, page=page,
        )

    @function_tool(failure_error_function=None)
    @recoverable
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
    @recoverable
    async def get_outstanding_validation_reviews(cursor: str | None = None) -> dict:
        """Find current outstanding units and exact answer/applicability references.

        Pass next_cursor to continue the live keyset scan; completed earlier rows
        do not shift later pages. Start a fresh scan after changing source/coverage
        and before finishing review. scan_complete only ends this scan; ready checks
        the entire requested scope. Rows are ordered so records citing the same
        evidence share an evidence_group and sit together. Before deciding, read the
        records with get_validation_record_sources and each attempt_ref/completion_ref
        with read_validation_history; listing is not assessment.
        """
        return await asyncio.to_thread(reads.outstanding, context.history_scope,
                                      context.episode_id, cursor=cursor)

    @function_tool(failure_error_function=None)
    @recoverable
    async def get_validation_record_sources(target_ids: list[str]) -> dict:
        """Read related original records together, inline, with shared evidence once.

        Supply record_target_id values from outstanding rows, up to group_limit, keeping
        rows that share an evidence_group together. Validator answers are not included;
        read each completion_ref with read_validation_history.
        Each record cites its evidence_record_ids in the evidence map;
        own_evidence holds a differing record with the same ID. Call again with
        not_returned for any remainder. too_large names records to read through
        read_validation_history using their ref; unavailable explains the rest.
        Scientific values are complete. Stable-definition bookkeeping, plain evidence
        pointers, empty provenance fields and fragment text repeating the verified
        quote are left out; each record's
        ref holds the exact complete source. This is the saved normalized source, not
        current selected values or a new validation, and it does not read the paper.
        """
        from .record_source import group_snapshot

        return await asyncio.to_thread(
            group_snapshot, context.history_scope, context.episode_id, target_ids=target_ids
        )

    @function_tool(failure_error_function=None)
    @recoverable
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
    @recoverable
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
    @recoverable
    async def record_validation_review(
        operation_id: str,
        packet_id: str | None = None,
        packet_decisions: list[PacketDecision] | None = None,
        entries: list[ReviewEntry] | None = None,
    ) -> dict:
        """Atomically record explicit decisions for exact target/answer components.

        Each record ends accepted, retired or left pending. Reject records an
        opinion; it does not remove an original record, which stays pending. To
        exclude a record that does not answer the question, use action retire on
        any of its handles with a reason (and any E handles that show it); it
        leaves current results, stays in history, and a curator can restore it.
        Retire is packet-only. If the result lists affected_bindings, assess them
        with validate_selected_extraction_source and the returned selection_id.
        Give a reason specific to each record. If any decision conflicts, nothing
        in the call applies. From read_review_packet, send its packet_id and one
        packet_decisions item per D handle, with entries null; the server fills in
        the exact target, answer and the version pinned when the packet was read.
        After a conflict, read a fresh packet and resend. When not ready, blocking
        lists what still blocks readiness, by this packet's handles, and what clears
        it; read a fresh packet before deciding those again. Otherwise
        send entries with packet_id and packet_decisions null, one call per group.
        For answers, target_id is attempt input.target_id; decision_unit_id is a
        covered ID matching the manifest unit target_id. No component units means
        decision_unit_id equals target_id: null decision_unit / empty decision_units
        describe whole-target review, not missing IDs. Components use covered child
        IDs. logical_attempt_id is the attempt fact ID, not the completion fact ID.
        expected_version is the unit's current head version, not selection_generation.
        Omitted targets remain pending. Accept caveated/unresolved complete answers
        when appropriate. For extraction-only acceptance, unresolved_fields names
        uncertain plain payload paths and explanations; it does not alter source values.
        For declared optional-input absence or unsupported lookup, use the exact applicability_ref fact/hash
        instead of an attempt or baseline, with its binding target and covered unit.
        Acceptance requires absence_assessment (omission_supported or unresolved)
        and your reason; unsupported lookups require unresolved. Optionality alone
        is not justification. No value is invented.
        Reject also leaves an older selection intact; revoke withdraws it.
        Operational failures require acknowledge_failure, not scientific rejection.
        """
        if (entries is None) == (packet_decisions is None) or (packet_id is None) != (packet_decisions is None):
            raise ValueError("Send either packet_id with packet_decisions, or entries")
        started = perf_counter()
        retiring = packet_decisions is not None and any(item.action == "retire" for item in packet_decisions)
        if retiring and select_source is None:
            raise ValueError("Retiring a record is not available in this review; leave it pending for the curator")
        request = None if retiring else await asyncio.to_thread(checkpoint_provider)
        checkpointed = perf_counter()
        if retiring:
            # Retirement changes the source, so it publishes through the bound
            # source boundary together with the other decisions of this save.
            result = await select_source(operation_id=operation_id, packet_id=packet_id,
                packet_decisions=[item.model_dump(mode="json") for item in packet_decisions])
        elif packet_decisions is not None:
            result = await asyncio.to_thread(
                decisions.record_packet_decisions,
                context,
                checkpoint_request=request,
                operation_id=operation_id,
                packet_id=packet_id,
                packet_decisions=[item.model_dump(mode="json") for item in packet_decisions],
            )
        else:
            result = await asyncio.to_thread(
                decisions.record_decisions,
                context,
                checkpoint_request=request,
                operation_id=operation_id,
                entries=[entry.model_dump(mode="json") for entry in entries],
            )
        recorded = perf_counter()
        if advance_work is not None:
            result = {**result, "dependent_work": await advance_work()}
        from src.lib.openai_agents.extraction_trace_events import write_extraction_trace_event

        write_extraction_trace_event(
            event_type="runtime.review_save_timing",
            output_summary={
                "entries": len(result["applied"]),
                "checkpoint_ms": round((checkpointed - started) * 1000),
                "record_decisions_ms": round((recorded - checkpointed) * 1000),
                "advance_work_ms": round((perf_counter() - recorded) * 1000),
            },
        )
        return result

    @function_tool(failure_error_function=None)
    @recoverable
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
    @recoverable
    async def get_validation_review_status() -> dict:
        """Return readiness and an exact history reference to full budgets/uncovered work.

        Read its fact ID/SHA with read_validation_history for all remaining budgets,
        warning thresholds and uncovered targets, with exact continuation if needed.
        """
        return await asyncio.to_thread(
            reads.status_reference, context.history_scope, context.episode_id
        )

    @function_tool(failure_error_function=None)
    @recoverable
    async def request_targeted_extraction(operation_id: str, parent_decision_id: str,
                                           entry_ids: list[str], instructions: str) -> dict:
        """Ask the saved extractor to investigate named existing entries in this paper.

        First record the review decision motivating the investigation. Explain
        what is missing or ambiguous and useful search guidance. Runtime supplies
        originals, exact prior validator inputs/answers/caveats, your decision and
        current attempt state automatically. The extractor can use only its saved
        paper tools. Each record gets one follow-up per review round; a
        curator-requested round grants a fresh one, and changing operation IDs or
        turns cannot reset it. Records with none
        left are not investigated and are listed under exhausted: decide them now
        (accept, retire or leave pending). A proposal is not an accepted
        source or validation. Read it and separately select supported fields and
        item relationships, or retain a truthful no-new-information assessment.
        """
        assert request_extraction is not None  # Tool is exposed only with its bound callback.
        return await request_extraction(operation_id=operation_id, parent_decision_id=parent_decision_id,
            entry_ids=entry_ids, instructions=instructions)

    @function_tool(failure_error_function=None)
    @recoverable
    async def select_extraction_contribution(operation_id: str, contribution: EvidenceReference,
            field_paths: list[str], evidence_ids: list[str], expected_source_sha: str, reason: str) -> dict:
        """Approve exact proposed source fields/evidence and their item relationships.

        Read the full contribution first. Decide whether each proposed correction,
        merge, split, reorder, addition or item retirement within a record is justified.
        field_paths select complete proposals including their explicit predecessor
        mappings; the runtime never guesses identity from matching text or indices.
        Include the evidence required by selected fields. expected_source_sha is the
        current source_state_sha256 from read_review_packet, get_outstanding_validation_reviews
        or the latest retirement response, never from get_validation_review_packet. Originals stay in history;
        only affected validator inputs become stale. This does not accept validation
        or establish submission readiness. Curators do not manage item IDs.
        """
        assert select_source is not None  # Tool is exposed only with its bound callback.
        return await select_source(operation_id=operation_id,
            contribution_ref=contribution.model_dump(mode="json"), field_paths=field_paths,
            evidence_ids=evidence_ids, expected_source_sha=expected_source_sha, reason=reason)

    @function_tool(failure_error_function=None)
    @recoverable
    async def decide_record_retirement(operation_id: str, contribution: EvidenceReference,
            decision: Literal["approve", "decline"], expected_source_sha: str, reason: str) -> dict:
        """Approve or decline an extractor's whole-record retirement recommendation.

        To retire a record yourself, use action retire in record_validation_review.
        Read its exact contribution, evidence and uncertainty first. Approval omits
        the record from current results without deleting original data or history;
        decline preserves current values and readiness. Curator authority is protected.
        Use the current source_state_sha256 from read_review_packet,
        get_outstanding_validation_reviews or the latest retirement response, never from
        get_validation_review_packet. A field or nested-item
        correction belongs to select_extraction_contribution instead.
        """
        assert select_source is not None
        return await select_source(operation_id=operation_id,
            contribution_ref=contribution.model_dump(mode="json"), retirement_decision=decision,
            expected_source_sha=expected_source_sha, reason=reason)

    @function_tool(failure_error_function=None)
    @recoverable
    async def validate_selected_extraction_source(operation_id: str, selection_id: str,
            target_ids: list[str], instructions: str) -> dict:
        """Validate changed inputs from an explicitly approved extraction contribution.

        Use the exact selection_id and affected binding target IDs returned by
        select_extraction_contribution or by a save that retired records. Explain what the new source clarifies and
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
        read_review_packet,
        get_validation_review_packet,
        get_outstanding_validation_reviews,
        get_validation_record_sources,
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
