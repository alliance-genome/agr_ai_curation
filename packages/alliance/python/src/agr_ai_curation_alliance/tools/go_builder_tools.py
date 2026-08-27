"""Structured builder tools for the RGD GO paper-curation specialist."""

from __future__ import annotations

import copy
from typing import Any, Dict, List, Mapping, Optional, Sequence

from agents import function_tool
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictStr,
    ValidationError,
    field_validator,
    model_validator,
)

from agr_ai_curation_runtime.agr_lookup import (
    LOOKUP_STATUS_BLOCKED,
    LOOKUP_STATUS_SUCCESS,
    attempt_query as _attempt_query,
)
from agr_ai_curation_runtime.evidence_workspace import (
    get_active_evidence_records_snapshot,
)
from agr_ai_curation_runtime.extraction_builder import (
    CANDIDATE_STATUS_VALID,
    ExtractionBuilderError,
    get_active_extraction_builder_workspace,
)
from agr_ai_curation_runtime.extraction_trace_events import write_extraction_trace_event
from agr_ai_curation_runtime.resolver_call_ledger import get_active_resolver_call_ledger

from agr_ai_curation_alliance.domain_packs.go import (
    GO_DOMAIN_PACK_ID,
    GO_MATERIALIZER_ID,
    GO_OBJECT_TYPE,
    materialize_go_builder_state,
)

from .agr_curation import (
    AgrQueryResult,
    _BUILDER_LIST_DEFAULT_LIMIT,
    _builder_candidate_list,
    _builder_summary,
    _ok,
    _search_builder_candidates,
)
from .builder_finalization import finalize_builder_extraction


_GO_PATCH_FIELD_PATHS = frozenset(
    {
        "gene_product",
        "go_term",
        "evidence_code",
        "evidence_eco_curie",
        "reference_curie",
        "with_from",
        "qualifiers",
        "annotation_extensions",
        "negated",
        "rationale",
        "provider_context",
        "resolution_state",
        "blocking_reasons",
        "evidence_record_ids",
    }
)

_IDENTITY_TOOLS = {"resolve_gene_product"}
_TERM_TOOLS = {"quickgo_api_call"}
_ANNOTATION_TOOLS = {"go_api_call"}
_CONTROLLED_VALUE_TOOLS = {
    *_IDENTITY_TOOLS,
    *_TERM_TOOLS,
    *_ANNOTATION_TOOLS,
    "search_document",
    "read_chunk",
    "read_section",
    "read_subsection",
    "record_evidence",
}


class _StrictToolModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GOStageInput(_StrictToolModel):
    pending_ref_id: StrictStr
    gene_product_mention: StrictStr
    gene_product_label: StrictStr
    gene_product_entity_type: StrictStr
    gene_product_taxon_curie: StrictStr
    gene_product_curie: Optional[StrictStr] = None
    resolution_state: StrictStr
    go_term_curie: StrictStr
    go_term_label: StrictStr
    go_term_aspect: StrictStr
    evidence_code: StrictStr
    evidence_eco_curie: StrictStr
    reference_curie: StrictStr
    rationale: StrictStr
    evidence_record_ids: List[StrictStr] = Field(min_length=1)
    with_from: List[StrictStr] = Field(default_factory=list)
    qualifiers: List[StrictStr] = Field(default_factory=list)
    annotation_extensions: List[StrictStr] = Field(default_factory=list)
    negated: StrictBool = False
    blocking_reasons: List[StrictStr] = Field(default_factory=list)
    existing_annotation_status: StrictStr
    existing_annotations: List[Dict[str, Any]] = Field(default_factory=list)
    existing_annotation_provenance: Dict[str, Any] = Field(default_factory=dict)
    existing_annotation_note: Optional[StrictStr] = None
    identity_resolution: Dict[str, Any] = Field(default_factory=dict)
    hierarchy_limitations: List[StrictStr] = Field(default_factory=list)
    section_limitations: List[StrictStr] = Field(default_factory=list)

    @field_validator(
        "pending_ref_id",
        "gene_product_mention",
        "gene_product_label",
        "gene_product_entity_type",
        "gene_product_taxon_curie",
        "resolution_state",
        "go_term_curie",
        "go_term_label",
        "go_term_aspect",
        "evidence_code",
        "evidence_eco_curie",
        "reference_curie",
        "rationale",
        "existing_annotation_status",
    )
    @classmethod
    def _non_empty_string(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("value must be non-empty")
        return cleaned

    @field_validator(
        "evidence_record_ids",
        "with_from",
        "qualifiers",
        "annotation_extensions",
        "blocking_reasons",
        "hierarchy_limitations",
        "section_limitations",
    )
    @classmethod
    def _clean_string_list(cls, value: List[str]) -> List[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for raw in value:
            item = str(raw).strip()
            if item and item not in seen:
                seen.add(item)
                cleaned.append(item)
        return cleaned

    @model_validator(mode="after")
    def _validate_identity_and_context(self) -> "GOStageInput":
        if self.resolution_state not in {"resolved", "unresolved"}:
            raise ValueError("resolution_state must be resolved or unresolved")
        if self.go_term_aspect not in {
            "molecular_function",
            "biological_process",
            "cellular_component",
        }:
            raise ValueError("go_term_aspect is not a canonical GO aspect")
        if self.existing_annotation_status not in {
            "available",
            "not_found",
            "unavailable",
        }:
            raise ValueError(
                "existing_annotation_status must be available, not_found, or unavailable"
            )
        if self.resolution_state == "resolved" and not self.gene_product_curie:
            raise ValueError("resolved identity requires gene_product_curie")
        if self.resolution_state == "unresolved" and self.gene_product_curie:
            raise ValueError("unresolved identity must not include gene_product_curie")
        if self.resolution_state == "unresolved" and not self.blocking_reasons:
            raise ValueError("unresolved identity requires blocking_reasons")
        if (
            self.existing_annotation_status == "unavailable"
            and not self.existing_annotation_note
        ):
            raise ValueError(
                "unavailable existing annotations require existing_annotation_note"
            )
        return self


class GOPatchUpdateInput(_StrictToolModel):
    field_path: StrictStr
    value: Any = None
    evidence_record_ids: Optional[List[StrictStr]] = None

    @field_validator("field_path")
    @classmethod
    def _known_field_path(cls, value: str) -> str:
        cleaned = value.strip()
        if cleaned not in _GO_PATCH_FIELD_PATHS:
            raise ValueError(
                f"field_path must be one of {sorted(_GO_PATCH_FIELD_PATHS)}"
            )
        return cleaned


class GOPatchInput(_StrictToolModel):
    candidate_id: StrictStr
    updates: List[GOPatchUpdateInput] = Field(min_length=1)


class GODiscardInput(_StrictToolModel):
    candidate_id: StrictStr
    reason: Optional[StrictStr] = None


class GOListInput(_StrictToolModel):
    include_discarded: bool
    limit: int = Field(default=_BUILDER_LIST_DEFAULT_LIMIT, ge=0)
    offset: int = Field(default=0, ge=0)


class GOFindInput(_StrictToolModel):
    field_value_contains: Optional[StrictStr] = None
    pending_ref_id: Optional[StrictStr] = None
    evidence_record_id: Optional[StrictStr] = None
    candidate_id: Optional[StrictStr] = None
    has_validation_errors: Optional[StrictBool] = None
    include_discarded: bool = False
    limit: int = Field(default=_BUILDER_LIST_DEFAULT_LIMIT, ge=0)
    offset: int = Field(default=0, ge=0)


class GOFinalizeInput(_StrictToolModel):
    candidate_ids: List[StrictStr] = Field(min_length=1)


def _emit_go_builder_event(
    event_type: str,
    *,
    action: str,
    input_summary: Any = None,
    output_summary: Any = None,
    validation: Optional[Mapping[str, Any]] = None,
) -> None:
    workspace = None
    try:
        workspace = get_active_extraction_builder_workspace()
    except RuntimeError:
        pass
    write_extraction_trace_event(
        event_type=event_type,
        trace_id=getattr(workspace, "run_id", None),
        domain_pack_id=GO_DOMAIN_PACK_ID,
        input_summary=input_summary,
        output_summary=output_summary,
        validation=validation,
        metadata={
            "action": action,
            "builder_run_id": getattr(workspace, "run_id", None),
            "object_type": GO_OBJECT_TYPE,
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


def _go_validation_result(
    *,
    message: str,
    issues: Sequence[Mapping[str, Any]],
    method: str,
    attempted_query: Optional[dict[str, Any]] = None,
) -> AgrQueryResult:
    issue_list = [dict(issue) for issue in issues]
    _emit_go_builder_event(
        "go_builder.validation_failed",
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


def _go_candidate_id(workspace: Any, pending_ref_id: str) -> str:
    for candidate in workspace.candidates.values():
        if pending_ref_id in candidate.pending_ref_ids:
            return candidate.candidate_id
    return f"rgd-go-candidate-{len(workspace.candidates) + 1}"


def _stage_payload(stage_input: GOStageInput) -> dict[str, Any]:
    gene_product = {
        "mention": stage_input.gene_product_mention,
        "label": stage_input.gene_product_label,
        "entity_type": stage_input.gene_product_entity_type,
        "taxon_curie": stage_input.gene_product_taxon_curie,
    }
    if stage_input.gene_product_curie:
        gene_product["curie"] = stage_input.gene_product_curie
    return {
        "domain_pack_id": GO_DOMAIN_PACK_ID,
        "object_type": GO_OBJECT_TYPE,
        "pending_ref_id": stage_input.pending_ref_id,
        "payload": {
            "gene_product": gene_product,
            "go_term": {
                "curie": stage_input.go_term_curie,
                "label": stage_input.go_term_label,
                "aspect": stage_input.go_term_aspect,
            },
            "evidence_code": stage_input.evidence_code,
            "evidence_eco_curie": stage_input.evidence_eco_curie,
            "reference_curie": stage_input.reference_curie,
            "with_from": list(stage_input.with_from),
            "qualifiers": list(stage_input.qualifiers),
            "annotation_extensions": list(stage_input.annotation_extensions),
            "negated": stage_input.negated,
            "rationale": stage_input.rationale,
            "provider_context": {
                "provider_key": "RGD",
                "taxon_curie": stage_input.gene_product_taxon_curie,
                "review_lane": "rgd_go_curator_review",
                "existing_annotation_context": {
                    "status": stage_input.existing_annotation_status,
                    "annotations": list(stage_input.existing_annotations),
                    "provenance": dict(stage_input.existing_annotation_provenance),
                    "note": stage_input.existing_annotation_note,
                },
                "identity_resolution": dict(stage_input.identity_resolution),
                "hierarchy_limitations": list(stage_input.hierarchy_limitations),
                "section_limitations": list(stage_input.section_limitations),
            },
            "resolution_state": stage_input.resolution_state,
            "blocking_reasons": list(stage_input.blocking_reasons),
        },
        "evidence_record_ids": list(stage_input.evidence_record_ids),
    }


def _grounding_leaf_values(value: Any) -> list[Any]:
    if isinstance(value, Mapping):
        return [
            leaf
            for nested in value.values()
            for leaf in _grounding_leaf_values(nested)
        ]
    if isinstance(value, list):
        return [leaf for nested in value for leaf in _grounding_leaf_values(nested)]
    if value in (None, "", [], {}) or isinstance(value, bool):
        return []
    return [value]


def _append_grounding_requirements(
    requirements: list[dict[str, Any]],
    *,
    field_path: str,
    tool_names: set[str],
    values: Any,
) -> None:
    for value in _grounding_leaf_values(values):
        requirement = {
            "field_path": field_path,
            "tool_names": sorted(tool_names),
            "value": value,
        }
        if requirement not in requirements:
            requirements.append(requirement)


def _grounding_requirements(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    gene_product = payload.get("gene_product") or {}
    go_term = payload.get("go_term") or {}
    provider_context = payload.get("provider_context") or {}
    existing = provider_context.get("existing_annotation_context") or {}
    identity = provider_context.get("identity_resolution") or {}
    requirements: list[dict[str, Any]] = []

    identity_value = gene_product.get("curie") or gene_product.get("mention")
    _append_grounding_requirements(
        requirements,
        field_path="gene_product",
        tool_names=_IDENTITY_TOOLS,
        values=identity_value,
    )
    if identity:
        _append_grounding_requirements(
            requirements,
            field_path="provider_context.identity_resolution",
            tool_names=_IDENTITY_TOOLS,
            values=identity,
        )
    _append_grounding_requirements(
        requirements,
        field_path="go_term",
        tool_names=_TERM_TOOLS,
        values=go_term,
    )
    if gene_product.get("curie"):
        annotation_values = [
            identity_value,
            existing.get("annotations"),
            existing.get("provenance"),
        ]
        _append_grounding_requirements(
            requirements,
            field_path="provider_context.existing_annotation_context",
            tool_names=_ANNOTATION_TOOLS,
            values=annotation_values,
        )
    for field_path in (
        "evidence_code",
        "evidence_eco_curie",
        "reference_curie",
        "with_from",
        "qualifiers",
        "annotation_extensions",
    ):
        value = payload.get(field_path)
        if value in (None, "", []):
            continue
        _append_grounding_requirements(
            requirements,
            field_path=field_path,
            tool_names=_CONTROLLED_VALUE_TOOLS,
            values=value,
        )
    return requirements


def _ground_payload(
    payload: Mapping[str, Any],
) -> tuple[list[str], list[dict[str, Any]], list[dict[str, Any]]]:
    requirements = _grounding_requirements(payload)
    try:
        ledger = get_active_resolver_call_ledger()
    except RuntimeError as exc:
        return (
            [],
            requirements,
            [
                {
                    "field_path": "source_grounding",
                    "reason": "tool_output_ledger_unavailable",
                    "message": str(exc),
                }
            ],
        )

    refs: list[str] = []
    issues: list[dict[str, Any]] = []
    for requirement in requirements:
        entry = ledger.find_tool_output_containing(
            tool_names=set(requirement["tool_names"]), value=requirement["value"]
        )
        if entry is None:
            issues.append(
                {
                    "field_path": requirement["field_path"],
                    "reason": "unobserved_tool_value",
                    "message": (
                        "The staged value does not match a run-scoped output from an "
                        "authoritative read-only tool."
                    ),
                }
            )
        elif entry.tool_call_id not in refs:
            refs.append(entry.tool_call_id)
    return refs, requirements, issues


def _stage_go_recommendation_impl(
    pending_ref_id: str,
    gene_product_mention: str,
    gene_product_label: str,
    gene_product_entity_type: str,
    gene_product_taxon_curie: str,
    resolution_state: str,
    go_term_curie: str,
    go_term_label: str,
    go_term_aspect: str,
    evidence_code: str,
    evidence_eco_curie: str,
    reference_curie: str,
    rationale: str,
    existing_annotation_status: str,
    evidence_record_ids: List[str],
    gene_product_curie: Optional[str] = None,
    with_from: Optional[List[str]] = None,
    qualifiers: Optional[List[str]] = None,
    annotation_extensions: Optional[List[str]] = None,
    negated: bool = False,
    blocking_reasons: Optional[List[str]] = None,
    existing_annotations: Optional[List[Dict[str, Any]]] = None,
    existing_annotation_provenance: Optional[Dict[str, Any]] = None,
    existing_annotation_note: Optional[str] = None,
    identity_resolution: Optional[Dict[str, Any]] = None,
    hierarchy_limitations: Optional[List[str]] = None,
    section_limitations: Optional[List[str]] = None,
) -> AgrQueryResult:
    """Stage one evidence-backed GO recommendation for canonical finalization."""

    attempted_query = _attempt_query(
        "stage_go_recommendation",
        pending_ref_id=pending_ref_id,
        gene_product_mention=gene_product_mention,
        go_term_curie=go_term_curie,
        evidence_record_ids=evidence_record_ids,
    )
    _emit_go_builder_event(
        "go_builder.stage_requested", action="stage", input_summary=attempted_query
    )
    try:
        stage_input = GOStageInput(
            pending_ref_id=pending_ref_id,
            gene_product_mention=gene_product_mention,
            gene_product_label=gene_product_label,
            gene_product_entity_type=gene_product_entity_type,
            gene_product_taxon_curie=gene_product_taxon_curie,
            gene_product_curie=gene_product_curie,
            resolution_state=resolution_state,
            go_term_curie=go_term_curie,
            go_term_label=go_term_label,
            go_term_aspect=go_term_aspect,
            evidence_code=evidence_code,
            evidence_eco_curie=evidence_eco_curie,
            reference_curie=reference_curie,
            rationale=rationale,
            evidence_record_ids=evidence_record_ids,
            with_from=with_from or [],
            qualifiers=qualifiers or [],
            annotation_extensions=annotation_extensions or [],
            negated=negated,
            blocking_reasons=blocking_reasons or [],
            existing_annotation_status=existing_annotation_status,
            existing_annotations=existing_annotations or [],
            existing_annotation_provenance=existing_annotation_provenance or {},
            existing_annotation_note=existing_annotation_note,
            identity_resolution=identity_resolution or {},
            hierarchy_limitations=hierarchy_limitations or [],
            section_limitations=section_limitations or [],
        )
    except ValidationError as exc:
        return _go_validation_result(
            message="stage_go_recommendation failed input validation.",
            issues=_model_validation_issues(exc),
            method="stage_go_recommendation",
            attempted_query=attempted_query,
        )

    staged_fields = _stage_payload(stage_input)
    grounding_refs, grounding_requirements, grounding_issues = _ground_payload(
        staged_fields["payload"]
    )
    if grounding_issues:
        return _go_validation_result(
            message="stage_go_recommendation rejected ungrounded source values.",
            issues=grounding_issues,
            method="stage_go_recommendation",
            attempted_query=attempted_query,
        )
    staged_fields["source_grounding"] = {
        "payload": copy.deepcopy(staged_fields["payload"]),
        "requirements": grounding_requirements,
    }

    workspace = get_active_extraction_builder_workspace()
    candidate_id = _go_candidate_id(workspace, stage_input.pending_ref_id)
    candidate = workspace.upsert_candidate(
        candidate_id=candidate_id,
        staged_fields=staged_fields,
        pending_ref_ids=[stage_input.pending_ref_id],
        evidence_record_ids=list(stage_input.evidence_record_ids),
        resolver_selection_refs=grounding_refs,
        status=CANDIDATE_STATUS_VALID,
    )
    summary = {
        "candidate_id": candidate.candidate_id,
        "status": candidate.status,
        "pending_ref_ids": candidate.pending_ref_ids,
        "evidence_record_ids": candidate.evidence_record_ids,
        "resolution_state": stage_input.resolution_state,
        "builder": _builder_summary(workspace),
    }
    _emit_go_builder_event(
        "go_builder.stage_completed",
        action="stage",
        input_summary=attempted_query,
        output_summary=summary,
    )
    return _ok(data=summary, count=1, lookup_status=LOOKUP_STATUS_SUCCESS)


def _patch_go_recommendation_impl(
    candidate_id: str,
    updates: List[Mapping[str, Any]],
) -> AgrQueryResult:
    attempted_query = _attempt_query(
        "patch_go_recommendation",
        candidate_id=candidate_id,
        updates=list(updates or []),
    )
    try:
        patch_input = GOPatchInput.model_validate(
            {"candidate_id": candidate_id, "updates": list(updates or [])}
        )
    except ValidationError as exc:
        return _go_validation_result(
            message="patch_go_recommendation failed input validation.",
            issues=_model_validation_issues(exc),
            method="patch_go_recommendation",
            attempted_query=attempted_query,
        )
    workspace = get_active_extraction_builder_workspace()
    try:
        candidate = workspace.get_candidate(patch_input.candidate_id)
    except KeyError as exc:
        return _go_validation_result(
            message=str(exc),
            issues=[
                {
                    "field_path": "candidate_id",
                    "reason": "unknown_candidate_id",
                    "message": str(exc),
                }
            ],
            method="patch_go_recommendation",
            attempted_query=attempted_query,
        )
    staged_fields = dict(candidate.staged_fields)
    payload = dict(staged_fields.get("payload") or {})
    evidence_ids = list(candidate.evidence_record_ids)
    for update in patch_input.updates:
        if update.field_path == "evidence_record_ids":
            evidence_ids = [
                str(item).strip()
                for item in (update.evidence_record_ids or [])
                if str(item).strip()
            ]
            if not evidence_ids:
                return _go_validation_result(
                    message="evidence_record_ids patch requires verified evidence IDs.",
                    issues=[
                        {
                            "field_path": "evidence_record_ids",
                            "reason": "missing_evidence_record_ids",
                            "message": "At least one evidence ID is required.",
                        }
                    ],
                    method="patch_go_recommendation",
                    attempted_query=attempted_query,
                )
        elif update.value in (None, ""):
            payload.pop(update.field_path, None)
        else:
            payload[update.field_path] = update.value
    staged_fields["payload"] = payload
    grounding_refs, grounding_requirements, grounding_issues = _ground_payload(payload)
    if grounding_issues:
        return _go_validation_result(
            message="patch_go_recommendation rejected ungrounded source values.",
            issues=grounding_issues,
            method="patch_go_recommendation",
            attempted_query=attempted_query,
        )
    staged_fields["source_grounding"] = {
        "payload": copy.deepcopy(payload),
        "requirements": grounding_requirements,
    }
    workspace.upsert_candidate(
        candidate_id=patch_input.candidate_id,
        staged_fields=staged_fields,
        pending_ref_ids=list(candidate.pending_ref_ids),
        evidence_record_ids=evidence_ids,
        resolver_selection_refs=grounding_refs,
        status=CANDIDATE_STATUS_VALID,
    )
    summary = {
        "candidate_id": patch_input.candidate_id,
        "patched_field_count": len(patch_input.updates),
        "builder": _builder_summary(workspace),
    }
    return _ok(data=summary, count=1, lookup_status=LOOKUP_STATUS_SUCCESS)


def _discard_go_recommendation_impl(
    candidate_id: str,
    reason: Optional[str] = None,
) -> AgrQueryResult:
    attempted_query = _attempt_query(
        "discard_go_recommendation", candidate_id=candidate_id, reason=reason
    )
    try:
        discard_input = GODiscardInput(candidate_id=candidate_id, reason=reason)
    except ValidationError as exc:
        return _go_validation_result(
            message="discard_go_recommendation failed input validation.",
            issues=_model_validation_issues(exc),
            method="discard_go_recommendation",
            attempted_query=attempted_query,
        )
    workspace = get_active_extraction_builder_workspace()
    try:
        workspace.discard_candidate(
            discard_input.candidate_id, reason=discard_input.reason
        )
    except (KeyError, ExtractionBuilderError) as exc:
        return _go_validation_result(
            message=str(exc),
            issues=[
                {
                    "field_path": "candidate_id",
                    "reason": "discard_failed",
                    "message": str(exc),
                }
            ],
            method="discard_go_recommendation",
            attempted_query=attempted_query,
        )
    summary = _builder_summary(workspace, include_discarded=True)
    return _ok(
        data=summary,
        count=summary["candidate_count"],
        lookup_status=LOOKUP_STATUS_SUCCESS,
    )


def _list_staged_go_recommendations_impl(
    include_discarded: bool,
    limit: int = _BUILDER_LIST_DEFAULT_LIMIT,
    offset: int = 0,
) -> AgrQueryResult:
    try:
        list_input = GOListInput(
            include_discarded=include_discarded, limit=limit, offset=offset
        )
    except ValidationError as exc:
        return _go_validation_result(
            message="list_staged_go_recommendations failed input validation.",
            issues=_model_validation_issues(exc),
            method="list_staged_go_recommendations",
        )
    workspace = get_active_extraction_builder_workspace()
    summary = _builder_candidate_list(
        workspace,
        include_discarded=list_input.include_discarded,
        limit=list_input.limit,
        offset=list_input.offset,
    )
    return _ok(
        data=summary,
        count=summary["candidate_count"],
        lookup_status=LOOKUP_STATUS_SUCCESS,
    )


def _find_staged_go_recommendations_impl(
    field_value_contains: Optional[str] = None,
    pending_ref_id: Optional[str] = None,
    evidence_record_id: Optional[str] = None,
    candidate_id: Optional[str] = None,
    has_validation_errors: Optional[bool] = None,
    include_discarded: bool = False,
    limit: int = _BUILDER_LIST_DEFAULT_LIMIT,
    offset: int = 0,
) -> AgrQueryResult:
    try:
        find_input = GOFindInput(
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
        return _go_validation_result(
            message="find_staged_go_recommendations failed input validation.",
            issues=_model_validation_issues(exc),
            method="find_staged_go_recommendations",
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
    return _ok(
        data=summary,
        count=summary["matched_candidate_count"],
        lookup_status=LOOKUP_STATUS_SUCCESS,
    )


def _materialize_go_with_events(
    *,
    workspace: Any,
    candidate_ids: Sequence[str],
    evidence_records: Sequence[Mapping[str, Any]],
    resolver_entry_lookup: Optional[Any],
) -> Any:
    materialization = materialize_go_builder_state(
        workspace=workspace,
        candidate_ids=candidate_ids,
        evidence_records=evidence_records,
        resolver_entry_lookup=resolver_entry_lookup,
    )
    _emit_go_builder_event(
        "go_materializer.completed"
        if materialization.ok
        else "go_materializer.validation_failed",
        action="materialize",
        input_summary={
            "candidate_ids": list(candidate_ids),
            "materializer_id": GO_MATERIALIZER_ID,
        },
        output_summary=materialization.summary(),
        validation=(
            None
            if materialization.ok
            else {"status": "failed", "issues": list(materialization.issues)}
        ),
    )
    return materialization


def _finalize_go_extraction_impl(candidate_ids: List[str]) -> AgrQueryResult:
    attempted_query = _attempt_query(
        "finalize_go_extraction", candidate_ids=candidate_ids
    )
    try:
        GOFinalizeInput(candidate_ids=candidate_ids)
    except ValidationError as exc:
        return _go_validation_result(
            message="finalize_go_extraction failed input validation.",
            issues=_model_validation_issues(exc),
            method="finalize_go_extraction",
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
        materialize=_materialize_go_with_events,
        evidence_records=evidence_records,
        resolver_entry_lookup=get_active_resolver_call_ledger().get_tool_output,
        materialized_candidate_prefix="rgd-go-envelope",
        require_evidence_record_ids=True,
        require_resolver_selections=True,
    )
    if not outcome.ok:
        return _go_validation_result(
            message=f"finalize_go_extraction {outcome.message}",
            issues=list(outcome.issues),
            method="finalize_go_extraction",
            attempted_query=attempted_query,
        )
    finalization = outcome.finalization
    if finalization is None:
        return _go_validation_result(
            message="finalize_go_extraction did not produce a finalization payload.",
            issues=[
                {
                    "field_path": "builder_finalization",
                    "reason": "missing_finalization",
                    "message": "Builder finalization payload is missing.",
                }
            ],
            method="finalize_go_extraction",
            attempted_query=attempted_query,
        )
    summary = {
        "builder_finalization": finalization.summary(),
        "builder": _builder_summary(workspace, include_discarded=True),
    }
    _emit_go_builder_event(
        "go_builder.finalize_completed",
        action="finalize",
        input_summary=attempted_query,
        output_summary=summary,
    )
    return _ok(
        data=summary,
        count=finalization.finalized_candidate_count,
        lookup_status=LOOKUP_STATUS_SUCCESS,
    )


stage_go_recommendation = function_tool(
    strict_mode=False, name_override="stage_go_recommendation"
)(_stage_go_recommendation_impl)
patch_go_recommendation = function_tool(
    strict_mode=False, name_override="patch_go_recommendation"
)(_patch_go_recommendation_impl)
discard_go_recommendation = function_tool(
    strict_mode=False, name_override="discard_go_recommendation"
)(_discard_go_recommendation_impl)
list_staged_go_recommendations = function_tool(
    strict_mode=False, name_override="list_staged_go_recommendations"
)(_list_staged_go_recommendations_impl)
find_staged_go_recommendations = function_tool(
    strict_mode=False, name_override="find_staged_go_recommendations"
)(_find_staged_go_recommendations_impl)
finalize_go_extraction = function_tool(
    strict_mode=False, name_override="finalize_go_extraction"
)(_finalize_go_extraction_impl)


__all__ = [
    "discard_go_recommendation",
    "finalize_go_extraction",
    "find_staged_go_recommendations",
    "list_staged_go_recommendations",
    "patch_go_recommendation",
    "stage_go_recommendation",
]
