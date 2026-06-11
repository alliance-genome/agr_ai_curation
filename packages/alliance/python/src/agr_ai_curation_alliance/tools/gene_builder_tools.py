"""Per-domain builder tools for the gene extractor (Phase 1 envelope -> builder migration).

Thin domain adapter over the project-agnostic ``ExtractionBuilderWorkspace`` engine and the
shared ``finalize_builder_extraction`` orchestration. Mirrors the gene_expression builder tools
(``stage_gene_expression_observation`` etc. in ``agr_curation.py``) but adapted to the gene
``gene_mention_evidence`` target:

  * NO resolver-backed controlled fields (the gene validator owns identity), so staging requires
    evidence but NOT resolver selections (``require_resolver_selections=False``).
  * NO mirror/projection fields.

Tool names match the gene extractor prompt/agent: ``stage_gene_mention_evidence``,
``patch_gene_mention_evidence``, ``discard_gene_mention_evidence``,
``list_staged_gene_mention_evidence``, ``finalize_gene_extraction``.

The raw ``_*_impl`` functions read run-scoped state via the ``get_active_*`` runtime shims; the
platform closure-binds them per run (see ``streaming_tools._bind_run_state_into_tools``). They are
placed in their OWN module (not ``agr_curation.py``) to avoid bloating the shared file, per the
runbook's per-domain-module preference.
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

from agr_ai_curation_alliance.domain_packs.gene import (
    GENE_DOMAIN_PACK_ID,
    GENE_MATERIALIZER_ID,
    GENE_MENTION_EVIDENCE_OBJECT_TYPE,
    materialize_gene_builder_state,
)

# Shared result/builder-summary helpers live in the sibling agr_curation module. Importing them
# keeps the gene tool-result shape identical to gene_expression (AgrQueryResult, ok/blocked).
from .agr_curation import (
    AgrQueryResult,
    _builder_summary,
    _builder_candidate_list,
    _search_builder_candidates,
    _ok,
)
from .builder_finalization import finalize_builder_extraction


_GENE_CONFIDENCE_VALUES = frozenset({"high", "medium", "low"})
# Patch field paths that map staging-input names to gene_mention_evidence payload field paths.
_GENE_PATCH_FIELD_PATHS = frozenset(
    {
        "mention",
        "species",
        "taxon_hint",
        "data_provider_hint",
        "proposed_primary_external_id",
        "proposed_gene_symbol",
        "proposed_taxon",
        "confidence",
        "evidence_record_ids",
    }
)


class _StrictToolModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GeneStageInput(_StrictToolModel):
    pending_ref_id: StrictStr
    mention: StrictStr
    evidence_record_ids: List[StrictStr] = Field(min_length=1, max_length=20)
    identity_resolution_notes: List[StrictStr] = Field(min_length=1, max_length=20)
    confidence: StrictStr
    species: Optional[StrictStr] = None
    taxon_hint: Optional[StrictStr] = None
    data_provider_hint: Optional[StrictStr] = None
    proposed_primary_external_id: Optional[StrictStr] = None
    proposed_gene_symbol: Optional[StrictStr] = None
    proposed_taxon: Optional[StrictStr] = None

    @field_validator("pending_ref_id", "mention", "confidence")
    @classmethod
    def _non_empty_string(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("value must be non-empty")
        return cleaned

    @field_validator("confidence")
    @classmethod
    def _valid_confidence(cls, value: str) -> str:
        cleaned = value.strip().lower()
        if cleaned not in _GENE_CONFIDENCE_VALUES:
            raise ValueError("confidence must be one of high, medium, low")
        return cleaned

    @field_validator("identity_resolution_notes")
    @classmethod
    def _non_empty_notes(cls, value: List[str]) -> List[str]:
        cleaned = [str(item).strip() for item in value if str(item).strip()]
        if not cleaned:
            raise ValueError("identity_resolution_notes must contain at least one non-empty value")
        return cleaned


class GenePatchUpdateInput(_StrictToolModel):
    field_path: StrictStr
    string_value: Optional[StrictStr] = None
    evidence_record_ids: Optional[List[StrictStr]] = Field(default=None, max_length=20)

    @field_validator("field_path")
    @classmethod
    def _known_field_path(cls, value: str) -> str:
        cleaned = value.strip()
        if cleaned not in _GENE_PATCH_FIELD_PATHS:
            raise ValueError(f"field_path must be one of {sorted(_GENE_PATCH_FIELD_PATHS)}")
        return cleaned


class GenePatchInput(_StrictToolModel):
    candidate_id: StrictStr
    pending_ref_id: StrictStr
    updates: List[GenePatchUpdateInput] = Field(min_length=1, max_length=25)


class GeneDiscardInput(_StrictToolModel):
    candidate_id: StrictStr
    reason: Optional[StrictStr] = None


class GeneListInput(_StrictToolModel):
    include_discarded: bool
    limit: int = Field(default=50, ge=0)
    offset: int = Field(default=0, ge=0)


class GeneFindInput(_StrictToolModel):
    field_value_contains: Optional[StrictStr] = None
    pending_ref_id: Optional[StrictStr] = None
    evidence_record_id: Optional[StrictStr] = None
    candidate_id: Optional[StrictStr] = None
    has_validation_errors: Optional[bool] = None
    include_discarded: bool = False
    limit: int = Field(default=50, ge=0)
    offset: int = Field(default=0, ge=0)


class GeneFinalizeInput(_StrictToolModel):
    candidate_ids: List[StrictStr] = Field(min_length=1, max_length=50)


def _emit_gene_builder_event(
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
        domain_pack_id=GENE_DOMAIN_PACK_ID,
        input_summary=input_summary,
        output_summary=output_summary,
        validation=validation,
        metadata={
            "action": action,
            "builder_run_id": getattr(workspace, "run_id", None),
            "object_type": GENE_MENTION_EVIDENCE_OBJECT_TYPE,
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


def _gene_validation_result(
    *,
    message: str,
    issues: Sequence[Mapping[str, Any]],
    method: str,
    attempted_query: Optional[dict[str, Any]] = None,
) -> AgrQueryResult:
    issue_list = [dict(issue) for issue in issues]
    _emit_gene_builder_event(
        "gene_builder.validation_failed",
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


def _gene_candidate_id(workspace: Any, pending_ref_id: str) -> str:
    for candidate in workspace.candidates.values():
        if pending_ref_id in candidate.pending_ref_ids:
            return candidate.candidate_id
    return f"gene-candidate-{len(workspace.candidates) + 1}"


def _stage_payload_from_gene_input(stage_input: GeneStageInput) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "domain_pack_id": GENE_DOMAIN_PACK_ID,
        "object_type": GENE_MENTION_EVIDENCE_OBJECT_TYPE,
        "pending_ref_id": stage_input.pending_ref_id,
        "mention": stage_input.mention,
        "confidence": stage_input.confidence,
        "identity_resolution_notes": list(stage_input.identity_resolution_notes),
    }
    for field_name in (
        "species",
        "taxon_hint",
        "data_provider_hint",
        "proposed_primary_external_id",
        "proposed_gene_symbol",
        "proposed_taxon",
    ):
        value = getattr(stage_input, field_name)
        if value is not None and value.strip():
            payload[field_name] = value.strip()
    return payload


def _stage_gene_mention_evidence_impl(
    pending_ref_id: str,
    mention: str,
    evidence_record_ids: List[str],
    identity_resolution_notes: List[str],
    confidence: str,
    species: Optional[str] = None,
    taxon_hint: Optional[str] = None,
    data_provider_hint: Optional[str] = None,
    proposed_primary_external_id: Optional[str] = None,
    proposed_gene_symbol: Optional[str] = None,
    proposed_taxon: Optional[str] = None,
) -> AgrQueryResult:
    """Stage one retained, evidence-backed gene mention through the builder workspace."""

    attempted_query = _attempt_query(
        "stage_gene_mention_evidence",
        pending_ref_id=pending_ref_id,
        mention=mention,
        evidence_record_ids=evidence_record_ids,
        confidence=confidence,
    )
    _emit_gene_builder_event(
        "gene_builder.stage_requested", action="stage", input_summary=attempted_query
    )
    try:
        stage_input = GeneStageInput(
            pending_ref_id=pending_ref_id,
            mention=mention,
            evidence_record_ids=evidence_record_ids,
            identity_resolution_notes=identity_resolution_notes,
            confidence=confidence,
            species=species,
            taxon_hint=taxon_hint,
            data_provider_hint=data_provider_hint,
            proposed_primary_external_id=proposed_primary_external_id,
            proposed_gene_symbol=proposed_gene_symbol,
            proposed_taxon=proposed_taxon,
        )
    except ValidationError as exc:
        return _gene_validation_result(
            message="stage_gene_mention_evidence failed input validation.",
            issues=_model_validation_issues(exc),
            method="stage_gene_mention_evidence",
            attempted_query=attempted_query,
        )

    workspace = get_active_extraction_builder_workspace()
    candidate_id = _gene_candidate_id(workspace, stage_input.pending_ref_id)
    payload = _stage_payload_from_gene_input(stage_input)
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
    _emit_gene_builder_event(
        "gene_builder.stage_completed",
        action="stage",
        input_summary=attempted_query,
        output_summary=summary,
    )
    return _ok(data=summary, count=1, lookup_status=LOOKUP_STATUS_SUCCESS)


def _set_gene_patch_value(payload: dict[str, Any], field_path: str, value: Optional[str]) -> None:
    cleaned = value.strip() if isinstance(value, str) else value
    if cleaned in (None, ""):
        payload.pop(field_path, None)
        return
    payload[field_path] = cleaned


def _patch_gene_mention_evidence_impl(
    candidate_id: str,
    pending_ref_id: str,
    updates: List[Mapping[str, Any]],
) -> AgrQueryResult:
    """Patch enumerated fields on one staged gene mention candidate."""

    attempted_query = _attempt_query(
        "patch_gene_mention_evidence",
        candidate_id=candidate_id,
        pending_ref_id=pending_ref_id,
        updates=list(updates or []),
    )
    _emit_gene_builder_event(
        "gene_builder.patch_requested", action="patch", input_summary=attempted_query
    )
    try:
        patch_input = GenePatchInput(
            candidate_id=candidate_id,
            pending_ref_id=pending_ref_id,
            updates=updates,
        )
    except ValidationError as exc:
        return _gene_validation_result(
            message="patch_gene_mention_evidence failed input validation.",
            issues=_model_validation_issues(exc),
            method="patch_gene_mention_evidence",
            attempted_query=attempted_query,
        )

    workspace = get_active_extraction_builder_workspace()
    try:
        candidate = workspace.get_candidate(patch_input.candidate_id)
    except KeyError as exc:
        return _gene_validation_result(
            message=str(exc),
            issues=[{"field_path": "candidate_id", "reason": "unknown_candidate_id", "message": str(exc)}],
            method="patch_gene_mention_evidence",
            attempted_query=attempted_query,
        )
    if patch_input.pending_ref_id not in candidate.pending_ref_ids:
        return _gene_validation_result(
            message="patch_gene_mention_evidence pending_ref_id does not match the staged candidate.",
            issues=[{"field_path": "pending_ref_id", "reason": "pending_ref_id_mismatch", "message": "pending_ref_id must match the staged candidate."}],
            method="patch_gene_mention_evidence",
            attempted_query=attempted_query,
        )

    payload = dict(candidate.staged_fields)
    evidence_ids = list(candidate.evidence_record_ids)
    for update in patch_input.updates:
        if update.field_path == "evidence_record_ids":
            new_ids = [str(item).strip() for item in (update.evidence_record_ids or []) if str(item).strip()]
            if not new_ids:
                return _gene_validation_result(
                    message="evidence_record_ids patch requires at least one evidence ID.",
                    issues=[{"field_path": "evidence_record_ids", "reason": "missing_evidence_record_ids", "message": "evidence_record_ids patch requires evidence_record_ids."}],
                    method="patch_gene_mention_evidence",
                    attempted_query=attempted_query,
                )
            evidence_ids = new_ids
            continue
        _set_gene_patch_value(payload, update.field_path, update.string_value)

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
    _emit_gene_builder_event(
        "gene_builder.patch_completed",
        action="patch",
        input_summary=attempted_query,
        output_summary=summary,
    )
    return _ok(data=summary, count=1, lookup_status=LOOKUP_STATUS_SUCCESS)


def _discard_gene_mention_evidence_impl(
    candidate_id: str,
    reason: Optional[str] = None,
) -> AgrQueryResult:
    """Discard one staged gene mention candidate."""

    attempted_query = _attempt_query(
        "discard_gene_mention_evidence", candidate_id=candidate_id, reason=reason
    )
    _emit_gene_builder_event(
        "gene_builder.discard_requested", action="discard", input_summary=attempted_query
    )
    try:
        discard_input = GeneDiscardInput(candidate_id=candidate_id, reason=reason)
    except ValidationError as exc:
        return _gene_validation_result(
            message="discard_gene_mention_evidence failed input validation.",
            issues=_model_validation_issues(exc),
            method="discard_gene_mention_evidence",
            attempted_query=attempted_query,
        )
    workspace = get_active_extraction_builder_workspace()
    try:
        workspace.discard_candidate(discard_input.candidate_id, reason=discard_input.reason)
    except (KeyError, ExtractionBuilderError) as exc:
        return _gene_validation_result(
            message=str(exc),
            issues=[{"field_path": "candidate_id", "reason": "discard_failed", "message": str(exc)}],
            method="discard_gene_mention_evidence",
            attempted_query=attempted_query,
        )
    summary = _builder_summary(workspace, include_discarded=True)
    _emit_gene_builder_event(
        "gene_builder.discard_completed",
        action="discard",
        input_summary=attempted_query,
        output_summary=summary,
    )
    return _ok(data=summary, count=summary["candidate_count"], lookup_status=LOOKUP_STATUS_SUCCESS)


def _list_staged_gene_mention_evidence_impl(
    include_discarded: bool,
    limit: int = 50,
    offset: int = 0,
) -> AgrQueryResult:
    """List compact summaries for staged gene mention candidates, one page at a time."""

    attempted_query = _attempt_query(
        "list_staged_gene_mention_evidence",
        include_discarded=include_discarded,
        limit=limit,
        offset=offset,
    )
    _emit_gene_builder_event(
        "gene_builder.list_requested", action="list", input_summary=attempted_query
    )
    try:
        list_input = GeneListInput(
            include_discarded=include_discarded, limit=limit, offset=offset
        )
    except ValidationError as exc:
        return _gene_validation_result(
            message="list_staged_gene_mention_evidence failed input validation.",
            issues=_model_validation_issues(exc),
            method="list_staged_gene_mention_evidence",
            attempted_query=attempted_query,
        )
    workspace = get_active_extraction_builder_workspace()
    summary = _builder_candidate_list(
        workspace,
        include_discarded=list_input.include_discarded,
        limit=list_input.limit,
        offset=list_input.offset,
    )
    _emit_gene_builder_event(
        "gene_builder.list_completed",
        action="list",
        input_summary=attempted_query,
        output_summary=summary,
    )
    return _ok(data=summary, count=summary["candidate_count"], lookup_status=LOOKUP_STATUS_SUCCESS)


def _find_staged_gene_mention_evidence_impl(
    field_value_contains: Optional[str] = None,
    pending_ref_id: Optional[str] = None,
    evidence_record_id: Optional[str] = None,
    candidate_id: Optional[str] = None,
    has_validation_errors: Optional[bool] = None,
    include_discarded: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> AgrQueryResult:
    """Find specific staged gene drafts by content or id, one page at a time."""

    attempted_query = _attempt_query(
        "find_staged_gene_mention_evidence",
        field_value_contains=field_value_contains,
        pending_ref_id=pending_ref_id,
        evidence_record_id=evidence_record_id,
        candidate_id=candidate_id,
        has_validation_errors=has_validation_errors,
        include_discarded=include_discarded,
        limit=limit,
        offset=offset,
    )
    _emit_gene_builder_event(
        "gene_builder.find_requested", action="find", input_summary=attempted_query
    )
    try:
        find_input = GeneFindInput(
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
        return _gene_validation_result(
            message="find_staged_gene_mention_evidence failed input validation.",
            issues=_model_validation_issues(exc),
            method="find_staged_gene_mention_evidence",
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
    _emit_gene_builder_event(
        "gene_builder.find_completed",
        action="find",
        input_summary=attempted_query,
        output_summary=summary,
    )
    return _ok(
        data=summary,
        count=summary["matched_candidate_count"],
        lookup_status=LOOKUP_STATUS_SUCCESS,
    )


def _materialize_gene_with_events(
    *,
    workspace: Any,
    candidate_ids: Sequence[str],
    evidence_records: Sequence[Mapping[str, Any]],
    resolver_entry_lookup: Optional[Any],
) -> Any:
    """Domain materializer wrapper emitting gene builder events.

    Only gene-specific step the generic finalize orchestration calls. Wraps
    ``materialize_gene_builder_state`` with started/validation/completed trace events.
    """

    candidate_id_list = list(candidate_ids)
    _emit_gene_builder_event(
        "gene_materializer.started",
        action="materialize",
        input_summary={"candidate_ids": candidate_id_list, "materializer_id": GENE_MATERIALIZER_ID},
    )
    materialization = materialize_gene_builder_state(
        workspace=workspace,
        candidate_ids=candidate_id_list,
        evidence_records=evidence_records,
        resolver_entry_lookup=resolver_entry_lookup,
    )
    if not materialization.ok or materialization.payload is None:
        _emit_gene_builder_event(
            "gene_materializer.validation_failed",
            action="materialize",
            input_summary={"candidate_ids": candidate_id_list},
            output_summary=materialization.summary(),
            validation={
                "status": "failed",
                "issues": [dict(issue) for issue in materialization.issues],
            },
        )
        return materialization
    _emit_gene_builder_event(
        "gene_materializer.completed",
        action="materialize",
        input_summary={"candidate_ids": candidate_id_list},
        output_summary={
            **materialization.summary(),
            "curatable_objects": materialization.payload.get("curatable_objects", []),
            "materialized_envelope": materialization.payload,
        },
    )
    return materialization


def _finalize_gene_extraction_impl(candidate_ids: List[str]) -> AgrQueryResult:
    """Finalize staged gene candidates through the builder handoff contract.

    Thin domain adapter: input validation + result shape live here; all structural
    staging/finalize control flow is delegated to ``finalize_builder_extraction``. Gene has no
    resolver-backed controlled fields, so ``require_resolver_selections=False``.
    """

    attempted_query = _attempt_query("finalize_gene_extraction", candidate_ids=candidate_ids)
    _emit_gene_builder_event(
        "gene_builder.finalize_requested", action="finalize", input_summary=attempted_query
    )
    try:
        GeneFinalizeInput(candidate_ids=candidate_ids)
    except ValidationError as exc:
        return _gene_validation_result(
            message="finalize_gene_extraction failed input validation.",
            issues=_model_validation_issues(exc),
            method="finalize_gene_extraction",
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
        materialize=_materialize_gene_with_events,
        evidence_records=evidence_records,
        resolver_entry_lookup=None,
        materialized_candidate_prefix="gene-envelope",
        require_resolver_selections=False,
    )

    if not outcome.ok:
        return _gene_validation_result(
            message=f"finalize_gene_extraction {outcome.message}",
            issues=list(outcome.issues),
            method="finalize_gene_extraction",
            attempted_query=attempted_query,
        )

    finalization = outcome.finalization
    summary = {
        "builder_finalization": finalization.summary(),
        "builder": _builder_summary(workspace, include_discarded=True),
    }
    _emit_gene_builder_event(
        "gene_builder.finalize_completed",
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
stage_gene_mention_evidence = function_tool(
    strict_mode=False, name_override="stage_gene_mention_evidence"
)(_stage_gene_mention_evidence_impl)
patch_gene_mention_evidence = function_tool(
    strict_mode=False, name_override="patch_gene_mention_evidence"
)(_patch_gene_mention_evidence_impl)
discard_gene_mention_evidence = function_tool(
    strict_mode=False, name_override="discard_gene_mention_evidence"
)(_discard_gene_mention_evidence_impl)
list_staged_gene_mention_evidence = function_tool(
    strict_mode=False, name_override="list_staged_gene_mention_evidence"
)(_list_staged_gene_mention_evidence_impl)
find_staged_gene_mention_evidence = function_tool(
    strict_mode=False, name_override="find_staged_gene_mention_evidence"
)(_find_staged_gene_mention_evidence_impl)
finalize_gene_extraction = function_tool(
    strict_mode=False, name_override="finalize_gene_extraction"
)(_finalize_gene_extraction_impl)


__all__ = [
    "discard_gene_mention_evidence",
    "finalize_gene_extraction",
    "find_staged_gene_mention_evidence",
    "list_staged_gene_mention_evidence",
    "materialize_gene_builder_state",
    "patch_gene_mention_evidence",
    "stage_gene_mention_evidence",
]
