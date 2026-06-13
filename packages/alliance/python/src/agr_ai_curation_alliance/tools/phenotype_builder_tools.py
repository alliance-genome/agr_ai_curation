"""Per-domain builder tools for the phenotype extractor (Phase 3 envelope -> builder migration).

Thin domain adapter over the project-agnostic ``ExtractionBuilderWorkspace`` engine and the
shared ``finalize_builder_extraction`` orchestration. Mirrors ``gene_builder_tools.py`` but adapted
to the phenotype ``PhenotypeAnnotation`` curatable_unit target:

  * The candidate stages a free-text phenotype statement, a pending subject reference, a pending
    phenotype-term candidate (label/CURIE), source mentions, and evidence_record_ids.
  * NO resolver-backed controlled fields: the active ``phenotype_term_ontology_validator`` resolves
    the staged label/CURIE candidate inline (``require_resolver_selections=False``), preserving the
    existing pack's posture (runbook §3 — change the mechanism, not the curation target).
  * NO mirror/projection fields (the subject IS the canonical subject; no
    ``materializes_to_field_paths``).

Tool names match the phenotype extractor prompt/agent: ``stage_phenotype_observation``,
``patch_phenotype_observation``, ``discard_phenotype_observation``,
``list_staged_phenotype_observations``, ``finalize_phenotype_extraction``.
"""

from __future__ import annotations

from typing import Any, List, Mapping, Optional, Sequence

from agents import function_tool
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
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

from agr_ai_curation_alliance.domain_packs.phenotype import (
    PHENOTYPE_DOMAIN_PACK_ID,
    PHENOTYPE_MATERIALIZER_ID,
    PHENOTYPE_OBJECT_TYPE,
    materialize_phenotype_builder_state,
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


# Patch field paths that map staging-input names to phenotype candidate staged-field names.
_PHENOTYPE_PATCH_FIELD_PATHS = frozenset(
    {
        "phenotype_annotation_object",
        "subject_identifier",
        "subject_label",
        "subject_type",
        "subject_taxon",
        "term_curie",
        "term_label",
        "data_provider",
        "term_taxon_id",
        "negated",
        "source_mentions",
        "evidence_record_ids",
        "condition_relations",
    }
)


class _StrictToolModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExperimentalConditionInput(_StrictToolModel):
    """One grounded ExperimentalCondition the extractor read from the paper.

    All ontology/chemical/taxon CURIEs are GROUNDED by the extractor via the term-helper lookup
    tools before staging (do not guess ZECO/ChEBI from memory). Every field is optional and sparse
    — stage only what the paper explicitly states. The condition carries no quote text: the
    validator reads the annotation's evidence_record_ids (the spans the condition was read from)
    per the evidence contract.
    """

    condition_class_curie: Optional[StrictStr] = None
    condition_id_curie: Optional[StrictStr] = None
    condition_chemical_curie: Optional[StrictStr] = None
    condition_taxon_curie: Optional[StrictStr] = None
    condition_free_text: Optional[StrictStr] = None
    condition_summary: Optional[StrictStr] = None


class ConditionRelationInput(_StrictToolModel):
    """One ConditionRelation: a relation type plus its experimental conditions."""

    condition_relation_type: StrictStr
    conditions: List[ExperimentalConditionInput] = Field(min_length=1, max_length=20)

    @field_validator("condition_relation_type")
    @classmethod
    def _non_empty_relation_type(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("condition_relation_type must be non-empty")
        return cleaned


class PhenotypeStageInput(_StrictToolModel):
    pending_ref_id: StrictStr
    phenotype_annotation_object: StrictStr
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
    subject_identifier: Optional[StrictStr] = None
    subject_label: Optional[StrictStr] = None
    subject_type: Optional[StrictStr] = None
    subject_taxon: Optional[StrictStr] = None
    term_curie: Optional[StrictStr] = None
    term_label: Optional[StrictStr] = None
    data_provider: Optional[StrictStr] = None
    term_taxon_id: Optional[StrictStr] = None
    # Nested experimental conditions. Each ConditionRelation carries a relation type plus its
    # grounded ExperimentalCondition components; the engine fans out per condition and the composite
    # validator decides each one. Optional + sparse — staged only when the paper explicitly states
    # experimental conditions.
    condition_relations: List[ConditionRelationInput] = Field(
        default_factory=list, max_length=20
    )
    negated: Optional[StrictBool] = None

    @field_validator("pending_ref_id", "phenotype_annotation_object")
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


class PhenotypePatchUpdateInput(_StrictToolModel):
    field_path: StrictStr
    string_value: Optional[StrictStr] = None
    bool_value: Optional[StrictBool] = None
    string_list_value: Optional[List[StrictStr]] = Field(default=None, max_length=20)
    # Nested ConditionRelation patch payload (only used when field_path == condition_relations).
    condition_relations_value: Optional[List[ConditionRelationInput]] = Field(
        default=None, max_length=20
    )

    @field_validator("field_path")
    @classmethod
    def _known_field_path(cls, value: str) -> str:
        cleaned = value.strip()
        if cleaned not in _PHENOTYPE_PATCH_FIELD_PATHS:
            raise ValueError(f"field_path must be one of {sorted(_PHENOTYPE_PATCH_FIELD_PATHS)}")
        return cleaned


class PhenotypePatchInput(_StrictToolModel):
    candidate_id: StrictStr
    pending_ref_id: StrictStr
    updates: List[PhenotypePatchUpdateInput] = Field(min_length=1, max_length=25)


class PhenotypeDiscardInput(_StrictToolModel):
    candidate_id: StrictStr
    reason: Optional[StrictStr] = None


class PhenotypeListInput(_StrictToolModel):
    include_discarded: bool
    limit: int = Field(default=50, ge=0)
    offset: int = Field(default=0, ge=0)


class PhenotypeFindInput(_StrictToolModel):
    field_value_contains: Optional[StrictStr] = None
    pending_ref_id: Optional[StrictStr] = None
    evidence_record_id: Optional[StrictStr] = None
    candidate_id: Optional[StrictStr] = None
    has_validation_errors: Optional[StrictBool] = None
    include_discarded: bool = False
    limit: int = Field(default=50, ge=0)
    offset: int = Field(default=0, ge=0)


class PhenotypeFinalizeInput(_StrictToolModel):
    candidate_ids: List[StrictStr] = Field(min_length=1, max_length=50)


def _emit_phenotype_builder_event(
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
        domain_pack_id=PHENOTYPE_DOMAIN_PACK_ID,
        input_summary=input_summary,
        output_summary=output_summary,
        validation=validation,
        metadata={
            "action": action,
            "builder_run_id": getattr(workspace, "run_id", None),
            "object_type": PHENOTYPE_OBJECT_TYPE,
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


def _phenotype_validation_result(
    *,
    message: str,
    issues: Sequence[Mapping[str, Any]],
    method: str,
    attempted_query: Optional[dict[str, Any]] = None,
) -> AgrQueryResult:
    issue_list = [dict(issue) for issue in issues]
    _emit_phenotype_builder_event(
        "phenotype_builder.validation_failed",
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


def _phenotype_candidate_id(workspace: Any, pending_ref_id: str) -> str:
    for candidate in workspace.candidates.values():
        if pending_ref_id in candidate.pending_ref_ids:
            return candidate.candidate_id
    return f"phenotype-candidate-{len(workspace.candidates) + 1}"


def _staged_condition_relations(
    condition_relations: Sequence[ConditionRelationInput],
) -> List[dict[str, Any]]:
    """Serialize nested ConditionRelation inputs into staged-field dicts.

    Drops empty leaves so a condition carries only the components the paper stated. A relation with
    no resolvable conditions is dropped entirely. The structure mirrors the materialized annotation
    shape (condition_relation_type.name + conditions[].condition_*); materialization re-reads these
    to build the concrete nested payload.
    """

    staged: List[dict[str, Any]] = []
    for relation in condition_relations:
        relation_type = relation.condition_relation_type.strip()
        conditions: List[dict[str, Any]] = []
        for condition in relation.conditions:
            component: dict[str, Any] = {}
            for field_name in (
                "condition_class_curie",
                "condition_id_curie",
                "condition_chemical_curie",
                "condition_taxon_curie",
                "condition_free_text",
                "condition_summary",
            ):
                value = getattr(condition, field_name)
                if value is not None and value.strip():
                    component[field_name] = value.strip()
            if component:
                conditions.append(component)
        if relation_type and conditions:
            staged.append(
                {
                    "condition_relation_type": relation_type,
                    "conditions": conditions,
                }
            )
    return staged


def _stage_payload_from_phenotype_input(stage_input: PhenotypeStageInput) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "domain_pack_id": PHENOTYPE_DOMAIN_PACK_ID,
        "object_type": PHENOTYPE_OBJECT_TYPE,
        "pending_ref_id": stage_input.pending_ref_id,
        "phenotype_annotation_object": stage_input.phenotype_annotation_object,
        "source_mentions": list(stage_input.source_mentions),
        "negated": bool(stage_input.negated),
    }
    for field_name in (
        "subject_identifier",
        "subject_label",
        "subject_type",
        "subject_taxon",
        "term_curie",
        "term_label",
        "data_provider",
        "term_taxon_id",
    ):
        value = getattr(stage_input, field_name)
        if value is not None and value.strip():
            payload[field_name] = value.strip()
    staged_condition_relations = _staged_condition_relations(stage_input.condition_relations)
    if staged_condition_relations:
        payload["condition_relations"] = staged_condition_relations
    return payload


def _stage_phenotype_observation_impl(
    pending_ref_id: str,
    phenotype_annotation_object: str,
    evidence_record_ids: List[str],
    source_mentions: List[str],
    subject_identifier: Optional[str] = None,
    subject_label: Optional[str] = None,
    subject_type: Optional[str] = None,
    subject_taxon: Optional[str] = None,
    term_curie: Optional[str] = None,
    term_label: Optional[str] = None,
    data_provider: Optional[str] = None,
    term_taxon_id: Optional[str] = None,
    condition_relations: Optional[List[Mapping[str, Any]]] = None,
    negated: Optional[bool] = None,
) -> AgrQueryResult:
    """Stage one retained, evidence-backed phenotype assertion through the builder workspace."""

    attempted_query = _attempt_query(
        "stage_phenotype_observation",
        pending_ref_id=pending_ref_id,
        phenotype_annotation_object=phenotype_annotation_object,
        evidence_record_ids=evidence_record_ids,
    )
    _emit_phenotype_builder_event(
        "phenotype_builder.stage_requested", action="stage", input_summary=attempted_query
    )
    try:
        stage_input = PhenotypeStageInput(
            pending_ref_id=pending_ref_id,
            phenotype_annotation_object=phenotype_annotation_object,
            evidence_record_ids=evidence_record_ids,
            source_mentions=source_mentions,
            subject_identifier=subject_identifier,
            subject_label=subject_label,
            subject_type=subject_type,
            subject_taxon=subject_taxon,
            term_curie=term_curie,
            term_label=term_label,
            data_provider=data_provider,
            term_taxon_id=term_taxon_id,
            condition_relations=list(condition_relations or []),
            negated=negated,
        )
    except ValidationError as exc:
        return _phenotype_validation_result(
            message="stage_phenotype_observation failed input validation.",
            issues=_model_validation_issues(exc),
            method="stage_phenotype_observation",
            attempted_query=attempted_query,
        )

    workspace = get_active_extraction_builder_workspace()
    candidate_id = _phenotype_candidate_id(workspace, stage_input.pending_ref_id)
    payload = _stage_payload_from_phenotype_input(stage_input)
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
    _emit_phenotype_builder_event(
        "phenotype_builder.stage_completed",
        action="stage",
        input_summary=attempted_query,
        output_summary=summary,
    )
    return _ok(data=summary, count=1, lookup_status=LOOKUP_STATUS_SUCCESS)


def _set_phenotype_patch_value(
    payload: dict[str, Any], field_path: str, value: Optional[str]
) -> None:
    cleaned = value.strip() if isinstance(value, str) else value
    if cleaned in (None, ""):
        payload.pop(field_path, None)
        return
    payload[field_path] = cleaned


def _patch_phenotype_observation_impl(
    candidate_id: str,
    pending_ref_id: str,
    updates: List[Mapping[str, Any]],
) -> AgrQueryResult:
    """Patch enumerated fields on one staged phenotype candidate."""

    attempted_query = _attempt_query(
        "patch_phenotype_observation",
        candidate_id=candidate_id,
        pending_ref_id=pending_ref_id,
        updates=list(updates or []),
    )
    _emit_phenotype_builder_event(
        "phenotype_builder.patch_requested", action="patch", input_summary=attempted_query
    )
    try:
        patch_input = PhenotypePatchInput(
            candidate_id=candidate_id,
            pending_ref_id=pending_ref_id,
            updates=updates,
        )
    except ValidationError as exc:
        return _phenotype_validation_result(
            message="patch_phenotype_observation failed input validation.",
            issues=_model_validation_issues(exc),
            method="patch_phenotype_observation",
            attempted_query=attempted_query,
        )

    workspace = get_active_extraction_builder_workspace()
    try:
        candidate = workspace.get_candidate(patch_input.candidate_id)
    except KeyError as exc:
        return _phenotype_validation_result(
            message=str(exc),
            issues=[{"field_path": "candidate_id", "reason": "unknown_candidate_id", "message": str(exc)}],
            method="patch_phenotype_observation",
            attempted_query=attempted_query,
        )
    if patch_input.pending_ref_id not in candidate.pending_ref_ids:
        return _phenotype_validation_result(
            message="patch_phenotype_observation pending_ref_id does not match the staged candidate.",
            issues=[{"field_path": "pending_ref_id", "reason": "pending_ref_id_mismatch", "message": "pending_ref_id must match the staged candidate."}],
            method="patch_phenotype_observation",
            attempted_query=attempted_query,
        )

    payload = dict(candidate.staged_fields)
    evidence_ids = list(candidate.evidence_record_ids)
    for update in patch_input.updates:
        if update.field_path == "evidence_record_ids":
            new_ids = [str(item).strip() for item in (update.string_list_value or []) if str(item).strip()]
            if not new_ids:
                return _phenotype_validation_result(
                    message="evidence_record_ids patch requires at least one evidence ID.",
                    issues=[{"field_path": "evidence_record_ids", "reason": "missing_evidence_record_ids", "message": "evidence_record_ids patch requires evidence_record_ids."}],
                    method="patch_phenotype_observation",
                    attempted_query=attempted_query,
                )
            evidence_ids = new_ids
            continue
        if update.field_path == "source_mentions":
            new_mentions = [str(item).strip() for item in (update.string_list_value or []) if str(item).strip()]
            if not new_mentions:
                return _phenotype_validation_result(
                    message="source_mentions patch requires at least one non-empty mention.",
                    issues=[{"field_path": "source_mentions", "reason": "missing_source_mentions", "message": "source_mentions patch requires non-empty mentions."}],
                    method="patch_phenotype_observation",
                    attempted_query=attempted_query,
                )
            payload["source_mentions"] = new_mentions
            continue
        if update.field_path == "condition_relations":
            staged_conditions = _staged_condition_relations(
                update.condition_relations_value or []
            )
            if staged_conditions:
                payload["condition_relations"] = staged_conditions
            else:
                payload.pop("condition_relations", None)
            continue
        if update.field_path == "negated":
            payload["negated"] = bool(update.bool_value)
            continue
        _set_phenotype_patch_value(payload, update.field_path, update.string_value)

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
    _emit_phenotype_builder_event(
        "phenotype_builder.patch_completed",
        action="patch",
        input_summary=attempted_query,
        output_summary=summary,
    )
    return _ok(data=summary, count=1, lookup_status=LOOKUP_STATUS_SUCCESS)


def _discard_phenotype_observation_impl(
    candidate_id: str,
    reason: Optional[str] = None,
) -> AgrQueryResult:
    """Discard one staged phenotype candidate."""

    attempted_query = _attempt_query(
        "discard_phenotype_observation", candidate_id=candidate_id, reason=reason
    )
    _emit_phenotype_builder_event(
        "phenotype_builder.discard_requested", action="discard", input_summary=attempted_query
    )
    try:
        discard_input = PhenotypeDiscardInput(candidate_id=candidate_id, reason=reason)
    except ValidationError as exc:
        return _phenotype_validation_result(
            message="discard_phenotype_observation failed input validation.",
            issues=_model_validation_issues(exc),
            method="discard_phenotype_observation",
            attempted_query=attempted_query,
        )
    workspace = get_active_extraction_builder_workspace()
    try:
        workspace.discard_candidate(discard_input.candidate_id, reason=discard_input.reason)
    except (KeyError, ExtractionBuilderError) as exc:
        return _phenotype_validation_result(
            message=str(exc),
            issues=[{"field_path": "candidate_id", "reason": "discard_failed", "message": str(exc)}],
            method="discard_phenotype_observation",
            attempted_query=attempted_query,
        )
    summary = _builder_summary(workspace, include_discarded=True)
    _emit_phenotype_builder_event(
        "phenotype_builder.discard_completed",
        action="discard",
        input_summary=attempted_query,
        output_summary=summary,
    )
    return _ok(data=summary, count=summary["candidate_count"], lookup_status=LOOKUP_STATUS_SUCCESS)


def _list_staged_phenotype_observations_impl(
    include_discarded: bool,
    limit: int = 50,
    offset: int = 0,
) -> AgrQueryResult:
    """List compact summaries for staged phenotype candidates, one page at a time."""

    attempted_query = _attempt_query(
        "list_staged_phenotype_observations",
        include_discarded=include_discarded,
        limit=limit,
        offset=offset,
    )
    _emit_phenotype_builder_event(
        "phenotype_builder.list_requested", action="list", input_summary=attempted_query
    )
    try:
        list_input = PhenotypeListInput(
            include_discarded=include_discarded, limit=limit, offset=offset
        )
    except ValidationError as exc:
        return _phenotype_validation_result(
            message="list_staged_phenotype_observations failed input validation.",
            issues=_model_validation_issues(exc),
            method="list_staged_phenotype_observations",
            attempted_query=attempted_query,
        )
    workspace = get_active_extraction_builder_workspace()
    summary = _builder_candidate_list(
        workspace,
        include_discarded=list_input.include_discarded,
        limit=list_input.limit,
        offset=list_input.offset,
    )
    _emit_phenotype_builder_event(
        "phenotype_builder.list_completed",
        action="list",
        input_summary=attempted_query,
        output_summary=summary,
    )
    return _ok(data=summary, count=summary["candidate_count"], lookup_status=LOOKUP_STATUS_SUCCESS)


def _find_staged_phenotype_observations_impl(
    field_value_contains: Optional[str] = None,
    pending_ref_id: Optional[str] = None,
    evidence_record_id: Optional[str] = None,
    candidate_id: Optional[str] = None,
    has_validation_errors: Optional[bool] = None,
    include_discarded: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> AgrQueryResult:
    """Find specific staged phenotype drafts by content or id, one page at a time."""

    attempted_query = _attempt_query(
        "find_staged_phenotype_observations",
        field_value_contains=field_value_contains,
        pending_ref_id=pending_ref_id,
        evidence_record_id=evidence_record_id,
        candidate_id=candidate_id,
        has_validation_errors=has_validation_errors,
        include_discarded=include_discarded,
        limit=limit,
        offset=offset,
    )
    _emit_phenotype_builder_event(
        "phenotype_builder.find_requested", action="find", input_summary=attempted_query
    )
    try:
        find_input = PhenotypeFindInput(
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
        return _phenotype_validation_result(
            message="find_staged_phenotype_observations failed input validation.",
            issues=_model_validation_issues(exc),
            method="find_staged_phenotype_observations",
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
    _emit_phenotype_builder_event(
        "phenotype_builder.find_completed",
        action="find",
        input_summary=attempted_query,
        output_summary=summary,
    )
    return _ok(
        data=summary,
        count=summary["matched_candidate_count"],
        lookup_status=LOOKUP_STATUS_SUCCESS,
    )


def _materialize_phenotype_with_events(
    *,
    workspace: Any,
    candidate_ids: Sequence[str],
    evidence_records: Sequence[Mapping[str, Any]],
    resolver_entry_lookup: Optional[Any],
) -> Any:
    """Domain materializer wrapper emitting phenotype builder events.

    Only phenotype-specific step the generic finalize orchestration calls. Wraps
    ``materialize_phenotype_builder_state`` with started/validation/completed trace events.
    """

    candidate_id_list = list(candidate_ids)
    _emit_phenotype_builder_event(
        "phenotype_materializer.started",
        action="materialize",
        input_summary={"candidate_ids": candidate_id_list, "materializer_id": PHENOTYPE_MATERIALIZER_ID},
    )
    materialization = materialize_phenotype_builder_state(
        workspace=workspace,
        candidate_ids=candidate_id_list,
        evidence_records=evidence_records,
        resolver_entry_lookup=resolver_entry_lookup,
    )
    if not materialization.ok or materialization.payload is None:
        _emit_phenotype_builder_event(
            "phenotype_materializer.validation_failed",
            action="materialize",
            input_summary={"candidate_ids": candidate_id_list},
            output_summary=materialization.summary(),
            validation={
                "status": "failed",
                "issues": [dict(issue) for issue in materialization.issues],
            },
        )
        return materialization
    _emit_phenotype_builder_event(
        "phenotype_materializer.completed",
        action="materialize",
        input_summary={"candidate_ids": candidate_id_list},
        output_summary=materialization.summary(),
    )
    return materialization


def _finalize_phenotype_extraction_impl(candidate_ids: List[str]) -> AgrQueryResult:
    """Finalize staged phenotype candidates through the builder handoff contract.

    Thin domain adapter: input validation + result shape live here; all structural
    staging/finalize control flow is delegated to ``finalize_builder_extraction``. Phenotype has no
    resolver-backed controlled fields (the active ontology validator resolves the staged term
    inline), so ``require_resolver_selections=False``.
    """

    attempted_query = _attempt_query("finalize_phenotype_extraction", candidate_ids=candidate_ids)
    _emit_phenotype_builder_event(
        "phenotype_builder.finalize_requested", action="finalize", input_summary=attempted_query
    )
    try:
        PhenotypeFinalizeInput(candidate_ids=candidate_ids)
    except ValidationError as exc:
        return _phenotype_validation_result(
            message="finalize_phenotype_extraction failed input validation.",
            issues=_model_validation_issues(exc),
            method="finalize_phenotype_extraction",
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
        materialize=_materialize_phenotype_with_events,
        evidence_records=evidence_records,
        resolver_entry_lookup=None,
        materialized_candidate_prefix="phenotype-annotation-envelope",
        require_resolver_selections=False,
    )

    if not outcome.ok:
        return _phenotype_validation_result(
            message=f"finalize_phenotype_extraction {outcome.message}",
            issues=list(outcome.issues),
            method="finalize_phenotype_extraction",
            attempted_query=attempted_query,
        )

    finalization = outcome.finalization
    summary = {
        "builder_finalization": finalization.summary(),
        "builder": _builder_summary(workspace, include_discarded=True),
    }
    _emit_phenotype_builder_event(
        "phenotype_builder.finalize_completed",
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
stage_phenotype_observation = function_tool(
    strict_mode=False, name_override="stage_phenotype_observation"
)(_stage_phenotype_observation_impl)
patch_phenotype_observation = function_tool(
    strict_mode=False, name_override="patch_phenotype_observation"
)(_patch_phenotype_observation_impl)
discard_phenotype_observation = function_tool(
    strict_mode=False, name_override="discard_phenotype_observation"
)(_discard_phenotype_observation_impl)
list_staged_phenotype_observations = function_tool(
    strict_mode=False, name_override="list_staged_phenotype_observations"
)(_list_staged_phenotype_observations_impl)
find_staged_phenotype_observations = function_tool(
    strict_mode=False, name_override="find_staged_phenotype_observations"
)(_find_staged_phenotype_observations_impl)
finalize_phenotype_extraction = function_tool(
    strict_mode=False, name_override="finalize_phenotype_extraction"
)(_finalize_phenotype_extraction_impl)


__all__ = [
    "discard_phenotype_observation",
    "finalize_phenotype_extraction",
    "find_staged_phenotype_observations",
    "list_staged_phenotype_observations",
    "materialize_phenotype_builder_state",
    "patch_phenotype_observation",
    "stage_phenotype_observation",
]
