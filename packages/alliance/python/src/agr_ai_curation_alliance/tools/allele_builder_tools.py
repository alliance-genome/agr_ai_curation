"""Per-domain builder tools for the allele extractor (Phase 4 envelope -> builder migration).

Thin domain adapter over the project-agnostic ``ExtractionBuilderWorkspace`` engine and the
shared ``finalize_builder_extraction`` orchestration. Mirrors ``gene_builder_tools.py`` /
``phenotype_builder_tools.py`` but adapted to the allele 4-object pending association graph
target (Reference + AlleleMention + EvidenceQuote(s) + AllelePaperEvidenceAssociation):

  * MENTION-ONLY: the candidate stages the exact paper mention text plus OPTIONAL validator
    selector context (normalized_hint / associated_gene / taxon) and source mentions; the active
    ``allele_mention_reference_validation`` binding resolves allele identity at validation time.
    The extractor NEVER stages an allele identifier or an Allele object.
  * NO resolver-backed controlled fields (the allele validator owns identity, mutation-type SO
    terms, and all CVs), so staging requires evidence but NOT resolver selections
    (``require_resolver_selections=False``) — same posture as gene.
  * NO mirror/projection fields (allele declares no ``materializes_to_field_paths``).

Tool names match the allele extractor prompt/agent: ``stage_allele_observation``,
``patch_allele_observation``, ``discard_allele_observation``,
``list_staged_allele_observations``, ``finalize_allele_extraction``.
"""

from __future__ import annotations

from typing import Any, List, Mapping, Optional, Sequence

from agents import function_tool
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictStr,
    ValidationError,
    field_validator,
)

from agr_ai_curation_runtime.agr_lookup import (
    LOOKUP_STATUS_BLOCKED,
    LOOKUP_STATUS_SUCCESS,
    attempt_query as _attempt_query,
)
from agr_ai_curation_runtime.extraction_builder import (
    CANDIDATE_STATUS_VALID,
    ExtractionBuilderError,
    get_active_extraction_builder_workspace,
)
from agr_ai_curation_runtime.evidence_workspace import get_active_evidence_records_snapshot
from agr_ai_curation_runtime.extraction_trace_events import write_extraction_trace_event

from agr_ai_curation_alliance.domain_packs.allele import (
    ALLELE_ASSOCIATION_OBJECT_TYPE,
    ALLELE_DOMAIN_PACK_ID,
    ALLELE_MATERIALIZER_ID,
    materialize_allele_builder_state,
)

# Shared result/builder-summary helpers live in the sibling agr_curation module.
from .agr_curation import (
    AgrQueryResult,
    _builder_summary,
    _builder_candidate_list,
    _search_builder_candidates,
    _ok,
)
from .builder_finalization import finalize_builder_extraction


# Patch field paths that map staging-input names to allele candidate staged-field names.
_ALLELE_PATCH_FIELD_PATHS = frozenset(
    {
        "mention",
        "normalized_hint",
        "associated_gene",
        "taxon",
        "reference_title",
        "reference_filename",
        "source_mentions",
        "evidence_record_ids",
    }
)


class _StrictToolModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AlleleStageInput(_StrictToolModel):
    pending_ref_id: StrictStr
    mention: StrictStr
    evidence_record_ids: List[StrictStr] = Field(min_length=1, max_length=20)
    source_mentions: List[StrictStr] = Field(
        min_length=1,
        max_length=20,
        description=(
            "Exact short paper phrases that name or anchor this finding; "
            "validators use them for lookup and disambiguation context, while "
            "verified quote/provenance stays in evidence_record_ids."
        ),
    )
    normalized_hint: Optional[StrictStr] = None
    associated_gene: Optional[StrictStr] = None
    taxon: Optional[StrictStr] = None
    reference_title: Optional[StrictStr] = None
    reference_filename: Optional[StrictStr] = None

    @field_validator("pending_ref_id", "mention")
    @classmethod
    def _non_empty_string(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("value must be non-empty")
        return cleaned

    @field_validator("source_mentions")
    @classmethod
    def _non_empty_mentions(cls, value: List[str]) -> List[str]:
        cleaned = [str(item).strip() for item in value if str(item).strip()]
        if not cleaned:
            raise ValueError("source_mentions must contain at least one non-empty value")
        return cleaned


class AllelePatchUpdateInput(_StrictToolModel):
    field_path: StrictStr
    string_value: Optional[StrictStr] = None
    string_list_value: Optional[List[StrictStr]] = Field(default=None, max_length=20)

    @field_validator("field_path")
    @classmethod
    def _known_field_path(cls, value: str) -> str:
        cleaned = value.strip()
        if cleaned not in _ALLELE_PATCH_FIELD_PATHS:
            raise ValueError(f"field_path must be one of {sorted(_ALLELE_PATCH_FIELD_PATHS)}")
        return cleaned


class AllelePatchInput(_StrictToolModel):
    candidate_id: StrictStr
    pending_ref_id: StrictStr
    updates: List[AllelePatchUpdateInput] = Field(min_length=1, max_length=25)


class AlleleDiscardInput(_StrictToolModel):
    candidate_id: StrictStr
    reason: Optional[StrictStr] = None


class AlleleListInput(_StrictToolModel):
    include_discarded: bool
    limit: int = Field(default=50, ge=0)
    offset: int = Field(default=0, ge=0)


class AlleleFindInput(_StrictToolModel):
    field_value_contains: Optional[StrictStr] = None
    pending_ref_id: Optional[StrictStr] = None
    evidence_record_id: Optional[StrictStr] = None
    candidate_id: Optional[StrictStr] = None
    has_validation_errors: Optional[bool] = None
    include_discarded: bool = False
    limit: int = Field(default=50, ge=0)
    offset: int = Field(default=0, ge=0)


class AlleleFinalizeInput(_StrictToolModel):
    candidate_ids: List[StrictStr] = Field(min_length=1, max_length=50)


def _emit_allele_builder_event(
    event_type: str,
    *,
    action: str,
    input_summary: Any = None,
    output_summary: Any = None,
    validation: Optional[Mapping[str, Any]] = None,
    tool_call_id: Optional[str] = None,
) -> None:
    workspace = None
    try:
        workspace = get_active_extraction_builder_workspace()
    except RuntimeError:
        pass
    write_extraction_trace_event(
        event_type=event_type,
        trace_id=getattr(workspace, "run_id", None),
        tool_call_id=tool_call_id,
        domain_pack_id=ALLELE_DOMAIN_PACK_ID,
        input_summary=input_summary,
        output_summary=output_summary,
        validation=validation,
        metadata={
            "action": action,
            "builder_run_id": getattr(workspace, "run_id", None),
            "object_type": ALLELE_ASSOCIATION_OBJECT_TYPE,
        },
    )


def _model_validation_issues(exc: ValidationError) -> List[dict[str, Any]]:
    return [
        {
            "field_path": ".".join(str(part) for part in error.get("loc", ())),
            "reason": str(error.get("type") or "invalid"),
            "message": str(error.get("msg") or "Invalid value"),
        }
        for error in exc.errors()
    ]


def _allele_validation_result(
    *,
    message: str,
    issues: Sequence[Mapping[str, Any]],
    method: str,
    attempted_query: Optional[dict[str, Any]] = None,
) -> AgrQueryResult:
    issue_list = [dict(issue) for issue in issues]
    _emit_allele_builder_event(
        "allele_builder.validation_failed",
        action=method,
        input_summary=attempted_query,
        output_summary={"message": message, "validation_issues": issue_list},
        validation={"status": "failed", "issues": issue_list},
    )
    return AgrQueryResult(
        status="error",
        data={"validation_issues": issue_list},
        count=len(issue_list),
        message=message,
        lookup_status=LOOKUP_STATUS_BLOCKED,
        failure_classification="validation_failed",
        explanation=message,
    )


def _allele_candidate_id(workspace: Any, pending_ref_id: str) -> str:
    for candidate in workspace.candidates.values():
        if pending_ref_id in candidate.pending_ref_ids:
            return candidate.candidate_id
    return f"allele-candidate-{len(workspace.candidates) + 1}"


def _stage_payload_from_allele_input(stage_input: AlleleStageInput) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "domain_pack_id": ALLELE_DOMAIN_PACK_ID,
        "object_type": ALLELE_ASSOCIATION_OBJECT_TYPE,
        "pending_ref_id": stage_input.pending_ref_id,
        "mention": stage_input.mention,
        "source_mentions": list(stage_input.source_mentions),
    }
    for field_name in (
        "normalized_hint",
        "associated_gene",
        "taxon",
        "reference_title",
        "reference_filename",
    ):
        value = getattr(stage_input, field_name)
        if value is not None and value.strip():
            payload[field_name] = value.strip()
    return payload


def _stage_allele_observation_impl(
    pending_ref_id: str,
    mention: str,
    evidence_record_ids: List[str],
    source_mentions: List[str],
    normalized_hint: Optional[str] = None,
    associated_gene: Optional[str] = None,
    taxon: Optional[str] = None,
    reference_title: Optional[str] = None,
    reference_filename: Optional[str] = None,
) -> AgrQueryResult:
    """Stage one retained, evidence-backed allele mention through the builder workspace."""

    attempted_query = _attempt_query(
        "stage_allele_observation",
        pending_ref_id=pending_ref_id,
        mention=mention,
        evidence_record_ids=evidence_record_ids,
    )
    _emit_allele_builder_event(
        "allele_builder.stage_requested", action="stage", input_summary=attempted_query
    )
    try:
        stage_input = AlleleStageInput(
            pending_ref_id=pending_ref_id,
            mention=mention,
            evidence_record_ids=evidence_record_ids,
            source_mentions=source_mentions,
            normalized_hint=normalized_hint,
            associated_gene=associated_gene,
            taxon=taxon,
            reference_title=reference_title,
            reference_filename=reference_filename,
        )
    except ValidationError as exc:
        return _allele_validation_result(
            message="stage_allele_observation failed input validation.",
            issues=_model_validation_issues(exc),
            method="stage_allele_observation",
            attempted_query=attempted_query,
        )

    workspace = get_active_extraction_builder_workspace()
    candidate_id = _allele_candidate_id(workspace, stage_input.pending_ref_id)
    payload = _stage_payload_from_allele_input(stage_input)
    candidate = workspace.upsert_candidate(
        candidate_id=candidate_id,
        staged_fields=payload,
        pending_ref_ids=[stage_input.pending_ref_id],
        evidence_record_ids=stage_input.evidence_record_ids,
        resolver_selection_refs=[],
        status=CANDIDATE_STATUS_VALID,
    )
    summary = {
        "candidate_id": candidate.candidate_id,
        "status": candidate.status,
        "pending_ref_ids": candidate.pending_ref_ids,
        "evidence_record_ids": candidate.evidence_record_ids,
        "builder": _builder_summary(workspace),
    }
    _emit_allele_builder_event(
        "allele_builder.stage_completed",
        action="stage",
        input_summary=attempted_query,
        output_summary=summary,
    )
    return _ok(data=summary, count=1, lookup_status=LOOKUP_STATUS_SUCCESS)


def _set_allele_patch_value(payload: dict[str, Any], field_path: str, value: Optional[str]) -> None:
    cleaned = value.strip() if isinstance(value, str) else value
    if cleaned in (None, ""):
        payload.pop(field_path, None)
        return
    payload[field_path] = cleaned


def _patch_allele_observation_impl(
    candidate_id: str,
    pending_ref_id: str,
    updates: List[Mapping[str, Any]],
) -> AgrQueryResult:
    """Patch enumerated fields on one staged allele mention candidate."""

    attempted_query = _attempt_query(
        "patch_allele_observation",
        candidate_id=candidate_id,
        pending_ref_id=pending_ref_id,
        updates=list(updates or []),
    )
    _emit_allele_builder_event(
        "allele_builder.patch_requested", action="patch", input_summary=attempted_query
    )
    try:
        patch_input = AllelePatchInput(
            candidate_id=candidate_id,
            pending_ref_id=pending_ref_id,
            updates=updates,
        )
    except ValidationError as exc:
        return _allele_validation_result(
            message="patch_allele_observation failed input validation.",
            issues=_model_validation_issues(exc),
            method="patch_allele_observation",
            attempted_query=attempted_query,
        )

    workspace = get_active_extraction_builder_workspace()
    try:
        candidate = workspace.get_candidate(patch_input.candidate_id)
    except KeyError as exc:
        return _allele_validation_result(
            message=str(exc),
            issues=[{"field_path": "candidate_id", "reason": "unknown_candidate_id", "message": str(exc)}],
            method="patch_allele_observation",
            attempted_query=attempted_query,
        )
    if patch_input.pending_ref_id not in candidate.pending_ref_ids:
        return _allele_validation_result(
            message="patch_allele_observation pending_ref_id does not match the staged candidate.",
            issues=[{"field_path": "pending_ref_id", "reason": "pending_ref_id_mismatch", "message": "pending_ref_id must match the staged candidate."}],
            method="patch_allele_observation",
            attempted_query=attempted_query,
        )

    payload = dict(candidate.staged_fields)
    evidence_ids = list(candidate.evidence_record_ids)
    for update in patch_input.updates:
        if update.field_path == "evidence_record_ids":
            new_ids = [str(item).strip() for item in (update.string_list_value or []) if str(item).strip()]
            if not new_ids:
                return _allele_validation_result(
                    message="evidence_record_ids patch requires at least one evidence ID.",
                    issues=[{"field_path": "evidence_record_ids", "reason": "missing_evidence_record_ids", "message": "evidence_record_ids patch requires evidence_record_ids."}],
                    method="patch_allele_observation",
                    attempted_query=attempted_query,
                )
            evidence_ids = new_ids
            continue
        if update.field_path == "source_mentions":
            new_mentions = [str(item).strip() for item in (update.string_list_value or []) if str(item).strip()]
            if not new_mentions:
                return _allele_validation_result(
                    message="source_mentions patch requires at least one non-empty mention.",
                    issues=[{"field_path": "source_mentions", "reason": "missing_source_mentions", "message": "source_mentions patch requires non-empty mentions."}],
                    method="patch_allele_observation",
                    attempted_query=attempted_query,
                )
            payload["source_mentions"] = new_mentions
            continue
        _set_allele_patch_value(payload, update.field_path, update.string_value)

    workspace.upsert_candidate(
        candidate_id=patch_input.candidate_id,
        staged_fields=payload,
        pending_ref_ids=candidate.pending_ref_ids,
        evidence_record_ids=evidence_ids,
        resolver_selection_refs=[],
        status=CANDIDATE_STATUS_VALID,
    )
    summary = {
        "candidate_id": patch_input.candidate_id,
        "patched_field_count": len(patch_input.updates),
        "builder": _builder_summary(workspace),
    }
    _emit_allele_builder_event(
        "allele_builder.patch_completed",
        action="patch",
        input_summary=attempted_query,
        output_summary=summary,
    )
    return _ok(data=summary, count=1, lookup_status=LOOKUP_STATUS_SUCCESS)


def _discard_allele_observation_impl(
    candidate_id: str,
    reason: Optional[str] = None,
) -> AgrQueryResult:
    """Discard one staged allele mention candidate."""

    attempted_query = _attempt_query(
        "discard_allele_observation", candidate_id=candidate_id, reason=reason
    )
    _emit_allele_builder_event(
        "allele_builder.discard_requested", action="discard", input_summary=attempted_query
    )
    try:
        discard_input = AlleleDiscardInput(candidate_id=candidate_id, reason=reason)
    except ValidationError as exc:
        return _allele_validation_result(
            message="discard_allele_observation failed input validation.",
            issues=_model_validation_issues(exc),
            method="discard_allele_observation",
            attempted_query=attempted_query,
        )
    workspace = get_active_extraction_builder_workspace()
    try:
        workspace.discard_candidate(discard_input.candidate_id, reason=discard_input.reason)
    except (KeyError, ExtractionBuilderError) as exc:
        return _allele_validation_result(
            message=str(exc),
            issues=[{"field_path": "candidate_id", "reason": "discard_failed", "message": str(exc)}],
            method="discard_allele_observation",
            attempted_query=attempted_query,
        )
    summary = _builder_summary(workspace, include_discarded=True)
    _emit_allele_builder_event(
        "allele_builder.discard_completed",
        action="discard",
        input_summary=attempted_query,
        output_summary=summary,
    )
    return _ok(data=summary, count=summary["candidate_count"], lookup_status=LOOKUP_STATUS_SUCCESS)


def _list_staged_allele_observations_impl(
    include_discarded: bool,
    limit: int = 50,
    offset: int = 0,
) -> AgrQueryResult:
    """List compact summaries for staged allele mention candidates, one page at a time."""

    attempted_query = _attempt_query(
        "list_staged_allele_observations",
        include_discarded=include_discarded,
        limit=limit,
        offset=offset,
    )
    _emit_allele_builder_event(
        "allele_builder.list_requested", action="list", input_summary=attempted_query
    )
    try:
        list_input = AlleleListInput(
            include_discarded=include_discarded, limit=limit, offset=offset
        )
    except ValidationError as exc:
        return _allele_validation_result(
            message="list_staged_allele_observations failed input validation.",
            issues=_model_validation_issues(exc),
            method="list_staged_allele_observations",
            attempted_query=attempted_query,
        )
    workspace = get_active_extraction_builder_workspace()
    summary = _builder_candidate_list(
        workspace,
        include_discarded=list_input.include_discarded,
        limit=list_input.limit,
        offset=list_input.offset,
    )
    _emit_allele_builder_event(
        "allele_builder.list_completed",
        action="list",
        input_summary=attempted_query,
        output_summary=summary,
    )
    return _ok(data=summary, count=summary["candidate_count"], lookup_status=LOOKUP_STATUS_SUCCESS)


def _find_staged_allele_observations_impl(
    field_value_contains: Optional[str] = None,
    pending_ref_id: Optional[str] = None,
    evidence_record_id: Optional[str] = None,
    candidate_id: Optional[str] = None,
    has_validation_errors: Optional[bool] = None,
    include_discarded: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> AgrQueryResult:
    """Find specific staged allele drafts by content or id, one page at a time."""

    attempted_query = _attempt_query(
        "find_staged_allele_observations",
        field_value_contains=field_value_contains,
        pending_ref_id=pending_ref_id,
        evidence_record_id=evidence_record_id,
        candidate_id=candidate_id,
        has_validation_errors=has_validation_errors,
        include_discarded=include_discarded,
        limit=limit,
        offset=offset,
    )
    _emit_allele_builder_event(
        "allele_builder.find_requested", action="find", input_summary=attempted_query
    )
    try:
        find_input = AlleleFindInput(
            field_value_contains=field_value_contains,
            pending_ref_id=pending_ref_id,
            evidence_record_id=evidence_record_id,
            candidate_id=candidate_id,
            has_validation_errors=has_validation_errors,
            include_discarded=include_discarded,
            limit=limit,
            offset=offset,
        )
    except ValidationError as exc:
        return _allele_validation_result(
            message="find_staged_allele_observations failed input validation.",
            issues=_model_validation_issues(exc),
            method="find_staged_allele_observations",
            attempted_query=attempted_query,
        )
    workspace = get_active_extraction_builder_workspace()
    summary = _search_builder_candidates(
        workspace,
        field_value_contains=find_input.field_value_contains,
        pending_ref_id=find_input.pending_ref_id,
        evidence_record_id=find_input.evidence_record_id,
        candidate_id=find_input.candidate_id,
        has_validation_errors=find_input.has_validation_errors,
        include_discarded=find_input.include_discarded,
        limit=find_input.limit,
        offset=find_input.offset,
    )
    _emit_allele_builder_event(
        "allele_builder.find_completed",
        action="find",
        input_summary=attempted_query,
        output_summary=summary,
    )
    return _ok(
        data=summary,
        count=summary["matched_candidate_count"],
        lookup_status=LOOKUP_STATUS_SUCCESS,
    )


def _materialize_allele_with_events(
    *,
    workspace: Any,
    candidate_ids: Sequence[str],
    evidence_records: Sequence[Mapping[str, Any]],
    resolver_entry_lookup: Optional[Any],
) -> Any:
    """Domain materializer wrapper emitting allele builder events.

    Only allele-specific step the generic finalize orchestration calls. Wraps
    ``materialize_allele_builder_state`` with started/validation/completed trace events.
    """

    candidate_id_list = list(candidate_ids)
    _emit_allele_builder_event(
        "allele_materializer.started",
        action="materialize",
        input_summary={"candidate_ids": candidate_id_list, "materializer_id": ALLELE_MATERIALIZER_ID},
    )
    materialization = materialize_allele_builder_state(
        workspace=workspace,
        candidate_ids=candidate_id_list,
        evidence_records=evidence_records,
        resolver_entry_lookup=resolver_entry_lookup,
    )
    if not materialization.ok or materialization.payload is None:
        _emit_allele_builder_event(
            "allele_materializer.validation_failed",
            action="materialize",
            input_summary={"candidate_ids": candidate_id_list},
            output_summary=materialization.summary(),
            validation={
                "status": "failed",
                "issues": [dict(issue) for issue in materialization.issues],
            },
        )
        return materialization
    _emit_allele_builder_event(
        "allele_materializer.completed",
        action="materialize",
        input_summary={"candidate_ids": candidate_id_list},
        output_summary=materialization.summary(),
    )
    return materialization


def _finalize_allele_extraction_impl(candidate_ids: List[str]) -> AgrQueryResult:
    """Finalize staged allele candidates through the builder handoff contract.

    Thin domain adapter: input validation + result shape live here; all structural
    staging/finalize control flow is delegated to ``finalize_builder_extraction``. Allele is
    mention-only with the validator owning identity, so there are no resolver-backed controlled
    fields and ``require_resolver_selections=False`` (same posture as gene).
    """

    attempted_query = _attempt_query("finalize_allele_extraction", candidate_ids=candidate_ids)
    _emit_allele_builder_event(
        "allele_builder.finalize_requested", action="finalize", input_summary=attempted_query
    )
    try:
        AlleleFinalizeInput(candidate_ids=candidate_ids)
    except ValidationError as exc:
        return _allele_validation_result(
            message="finalize_allele_extraction failed input validation.",
            issues=_model_validation_issues(exc),
            method="finalize_allele_extraction",
            attempted_query=attempted_query,
        )

    workspace = get_active_extraction_builder_workspace()
    try:
        evidence_records = get_active_evidence_records_snapshot()
    except RuntimeError:
        evidence_records = []

    outcome = finalize_builder_extraction(
        workspace=workspace,
        candidate_ids=candidate_ids,
        materialize=_materialize_allele_with_events,
        evidence_records=evidence_records,
        resolver_entry_lookup=None,
        materialized_candidate_prefix="allele-paper-evidence-association",
        require_resolver_selections=False,
    )

    if not outcome.ok:
        return _allele_validation_result(
            message=f"finalize_allele_extraction {outcome.message}",
            issues=list(outcome.issues),
            method="finalize_allele_extraction",
            attempted_query=attempted_query,
        )

    finalization = outcome.finalization
    summary = {
        "builder_finalization": finalization.summary(),
        "builder": _builder_summary(workspace, include_discarded=True),
    }
    _emit_allele_builder_event(
        "allele_builder.finalize_completed",
        action="finalize",
        input_summary=attempted_query,
        output_summary=summary,
    )
    return _ok(
        data=summary,
        count=finalization.finalized_candidate_count,
        lookup_status=LOOKUP_STATUS_SUCCESS,
    )


# Public, LLM-facing FunctionTools. The platform swaps these for closure-bound rebuilds at run
# time (streaming_tools._bind_run_state_into_tools), so the run-scoped builder workspace + evidence
# records resolve inside the worker thread.
stage_allele_observation = function_tool(
    strict_mode=False, name_override="stage_allele_observation"
)(_stage_allele_observation_impl)
patch_allele_observation = function_tool(
    strict_mode=False, name_override="patch_allele_observation"
)(_patch_allele_observation_impl)
discard_allele_observation = function_tool(
    strict_mode=False, name_override="discard_allele_observation"
)(_discard_allele_observation_impl)
list_staged_allele_observations = function_tool(
    strict_mode=False, name_override="list_staged_allele_observations"
)(_list_staged_allele_observations_impl)
find_staged_allele_observations = function_tool(
    strict_mode=False, name_override="find_staged_allele_observations"
)(_find_staged_allele_observations_impl)
finalize_allele_extraction = function_tool(
    strict_mode=False, name_override="finalize_allele_extraction"
)(_finalize_allele_extraction_impl)


__all__ = [
    "discard_allele_observation",
    "finalize_allele_extraction",
    "find_staged_allele_observations",
    "list_staged_allele_observations",
    "materialize_allele_builder_state",
    "patch_allele_observation",
    "stage_allele_observation",
]
