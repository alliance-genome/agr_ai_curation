"""Builder tools for broad generic PDF extraction."""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence

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
from agr_ai_curation_runtime.evidence_workspace import get_active_evidence_records_snapshot
from agr_ai_curation_runtime.extraction_builder import (
    CANDIDATE_STATUS_VALID,
    ExtractionBuilderError,
    get_active_extraction_builder_workspace,
)
from agr_ai_curation_runtime.extraction_trace_events import write_extraction_trace_event

from agr_ai_curation_alliance.domain_packs.generic import (
    GENERIC_DOMAIN_PACK_ID,
    GENERIC_MATERIALIZER_ID,
    load_generic_class_catalog,
    materialize_generic_builder_state,
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


_GENERIC_TOP_LEVEL_PATCH_FIELDS = frozenset(
    {
        "class_key",
        "label",
        "pending_ref_id",
        "source_label",
        "description",
        "confidence",
        "semantic_class",
        "classification_notes",
        "payload",
        "attributes",
        "evidence_record_ids",
    }
)


class _StrictToolModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GenericClassListInput(_StrictToolModel):
    include_non_stageable: bool = False


class GenericStageInput(_StrictToolModel):
    class_key: StrictStr
    label: StrictStr
    evidence_record_ids: List[StrictStr] = Field(min_length=1)
    classification_notes: List[StrictStr] = Field(min_length=1)
    pending_ref_id: Optional[StrictStr] = None
    source_label: Optional[StrictStr] = None
    description: Optional[StrictStr] = None
    confidence: Optional[StrictStr] = None
    semantic_class: Optional[StrictStr] = None
    payload: Dict[str, Any] = Field(default_factory=dict)
    attributes: Dict[str, Any] = Field(default_factory=dict)

    @field_validator(
        "class_key",
        "label",
        "pending_ref_id",
        "source_label",
        "description",
        "confidence",
        "semantic_class",
    )
    @classmethod
    def _clean_optional_string(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("value must be non-empty")
        return cleaned

    @field_validator("classification_notes")
    @classmethod
    def _non_empty_notes(cls, value: List[str]) -> List[str]:
        cleaned = [str(item).strip() for item in value if str(item).strip()]
        if not cleaned:
            raise ValueError("classification_notes must contain at least one non-empty value")
        return cleaned


class GenericPatchUpdateInput(_StrictToolModel):
    field_path: StrictStr
    value: Any = None
    evidence_record_ids: Optional[List[StrictStr]] = None

    @field_validator("field_path")
    @classmethod
    def _known_field_path(cls, value: str) -> str:
        cleaned = value.strip()
        if cleaned in _GENERIC_TOP_LEVEL_PATCH_FIELDS:
            return cleaned
        if cleaned.startswith("payload.") and cleaned[8:].strip():
            return cleaned
        if cleaned.startswith("attributes.") and cleaned[11:].strip():
            return cleaned
        raise ValueError(
            "field_path must be a generic top-level field, payload.<key>, or attributes.<key>"
        )


class GenericPatchInput(_StrictToolModel):
    candidate_id: StrictStr
    updates: List[GenericPatchUpdateInput] = Field(min_length=1)


class GenericDiscardInput(_StrictToolModel):
    candidate_id: StrictStr
    reason: Optional[StrictStr] = None


class GenericListInput(_StrictToolModel):
    include_discarded: bool
    limit: int = Field(default=_BUILDER_LIST_DEFAULT_LIMIT, ge=0)
    offset: int = Field(default=0, ge=0)


class GenericFindInput(_StrictToolModel):
    field_value_contains: Optional[StrictStr] = None
    pending_ref_id: Optional[StrictStr] = None
    evidence_record_id: Optional[StrictStr] = None
    candidate_id: Optional[StrictStr] = None
    has_validation_errors: Optional[bool] = None
    include_discarded: bool = False
    limit: int = Field(default=_BUILDER_LIST_DEFAULT_LIMIT, ge=0)
    offset: int = Field(default=0, ge=0)


class GenericFinalizeInput(_StrictToolModel):
    candidate_ids: List[StrictStr] = Field(default_factory=list)


def _emit_generic_builder_event(
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
        domain_pack_id=GENERIC_DOMAIN_PACK_ID,
        input_summary=input_summary,
        output_summary=output_summary,
        validation=validation,
        metadata={
            "action": action,
            "builder_run_id": getattr(workspace, "run_id", None),
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


def _generic_validation_result(
    *,
    message: str,
    issues: Sequence[Mapping[str, Any]],
    method: str,
    attempted_query: Optional[dict[str, Any]] = None,
) -> AgrQueryResult:
    issue_list = [dict(issue) for issue in issues]
    _emit_generic_builder_event(
        "generic_builder.validation_failed",
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


def _generic_candidate_id(workspace: Any, pending_ref_id: str | None) -> str:
    if pending_ref_id:
        for candidate in workspace.candidates.values():
            if pending_ref_id in candidate.pending_ref_ids:
                return candidate.candidate_id
    return f"generic-candidate-{len(workspace.candidates) + 1}"


def _stage_payload_from_generic_input(stage_input: GenericStageInput) -> dict[str, Any]:
    entry = load_generic_class_catalog().require_stageable(stage_input.class_key)
    _validate_payload_keys_for_entry(stage_input.payload, entry=entry)
    payload: dict[str, Any] = {
        "domain_pack_id": GENERIC_DOMAIN_PACK_ID,
        "object_type": entry.generic_object_type,
        "class_key": entry.class_key,
        "label": stage_input.label,
        "classification_notes": list(stage_input.classification_notes),
        "payload": dict(stage_input.payload),
    }
    if stage_input.pending_ref_id:
        payload["pending_ref_id"] = stage_input.pending_ref_id
    for field_name in (
        "source_label",
        "description",
        "confidence",
        "semantic_class",
    ):
        value = getattr(stage_input, field_name)
        if value is not None:
            payload[field_name] = value
    if stage_input.attributes:
        payload["attributes"] = dict(stage_input.attributes)
    return payload


def _validate_payload_keys_for_entry(
    payload: Mapping[str, Any],
    *,
    entry: Any,
) -> None:
    unknown_keys = sorted({str(key) for key in payload} - set(entry.payload_fields))
    if unknown_keys:
        raise ValueError(
            "payload contains field(s) not declared by class_key "
            f"{entry.class_key}: {', '.join(unknown_keys)}"
        )


def _list_generic_object_classes_impl(
    include_non_stageable: bool = False,
) -> AgrQueryResult:
    """Return the compact generic extraction class catalog."""

    attempted_query = _attempt_query(
        "list_generic_object_classes",
        include_non_stageable=include_non_stageable,
    )
    try:
        list_input = GenericClassListInput(include_non_stageable=include_non_stageable)
    except ValidationError as exc:
        return _generic_validation_result(
            message="list_generic_object_classes failed input validation.",
            issues=_model_validation_issues(exc),
            method="list_generic_object_classes",
            attempted_query=attempted_query,
        )
    catalog_payload = load_generic_class_catalog().tool_payload(
        include_non_stageable=list_input.include_non_stageable
    )
    return _ok(
        data=catalog_payload,
        count=catalog_payload["class_count"],
        lookup_status=LOOKUP_STATUS_SUCCESS,
    )


def _stage_generic_object_impl(
    class_key: str,
    label: str,
    evidence_record_ids: List[str],
    classification_notes: List[str],
    pending_ref_id: Optional[str] = None,
    source_label: Optional[str] = None,
    description: Optional[str] = None,
    confidence: Optional[str] = None,
    semantic_class: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
    attributes: Optional[Dict[str, Any]] = None,
) -> AgrQueryResult:
    """Stage one retained, evidence-backed generic object through the builder."""

    attempted_query = _attempt_query(
        "stage_generic_object",
        class_key=class_key,
        label=label,
        evidence_record_ids=evidence_record_ids,
        pending_ref_id=pending_ref_id,
    )
    _emit_generic_builder_event(
        "generic_builder.stage_requested", action="stage", input_summary=attempted_query
    )
    try:
        stage_input = GenericStageInput(
            class_key=class_key,
            label=label,
            evidence_record_ids=evidence_record_ids,
            classification_notes=classification_notes,
            pending_ref_id=pending_ref_id,
            source_label=source_label,
            description=description,
            confidence=confidence,
            semantic_class=semantic_class,
            payload=payload or {},
            attributes=attributes or {},
        )
        staged_payload = _stage_payload_from_generic_input(stage_input)
    except (ValidationError, KeyError, ValueError) as exc:
        issues = (
            _model_validation_issues(exc)
            if isinstance(exc, ValidationError)
            else [
                {
                    "field_path": "class_key",
                    "reason": "invalid_class_key",
                    "message": str(exc),
                }
            ]
        )
        return _generic_validation_result(
            message="stage_generic_object failed input validation.",
            issues=issues,
            method="stage_generic_object",
            attempted_query=attempted_query,
        )

    workspace = get_active_extraction_builder_workspace()
    candidate_id = _generic_candidate_id(workspace, stage_input.pending_ref_id)
    candidate = workspace.upsert_candidate(
        candidate_id=candidate_id,
        staged_fields=staged_payload,
        pending_ref_ids=[stage_input.pending_ref_id] if stage_input.pending_ref_id else [],
        evidence_record_ids=list(stage_input.evidence_record_ids),
        resolver_selection_refs=[],
        status=CANDIDATE_STATUS_VALID,
    )
    summary = {
        "candidate_id": candidate.candidate_id,
        "status": candidate.status,
        "class_key": stage_input.class_key,
        "label": stage_input.label,
        "pending_ref_ids": candidate.pending_ref_ids,
        "evidence_record_ids": candidate.evidence_record_ids,
        "builder": _builder_summary(workspace),
    }
    _emit_generic_builder_event(
        "generic_builder.stage_completed",
        action="stage",
        input_summary=attempted_query,
        output_summary=summary,
    )
    return _ok(data=summary, count=1, lookup_status=LOOKUP_STATUS_SUCCESS)


def _set_nested_patch_value(payload: dict[str, Any], field_path: str, value: Any) -> None:
    if field_path.startswith("payload."):
        container_name = "payload"
        key = field_path[8:]
    elif field_path.startswith("attributes."):
        container_name = "attributes"
        key = field_path[11:]
    else:
        if value in (None, "", []):
            payload.pop(field_path, None)
        else:
            payload[field_path] = value
        return

    container = payload.get(container_name)
    if not isinstance(container, dict):
        container = {}
    if value in (None, "", []):
        container.pop(key, None)
    else:
        container[key] = value
    if container:
        payload[container_name] = container
    else:
        payload.pop(container_name, None)


def _patch_generic_object_impl(
    candidate_id: str,
    updates: List[Mapping[str, Any]],
) -> AgrQueryResult:
    """Patch allowed fields on one staged generic candidate."""

    attempted_query = _attempt_query(
        "patch_generic_object", candidate_id=candidate_id, updates=list(updates or [])
    )
    _emit_generic_builder_event(
        "generic_builder.patch_requested", action="patch", input_summary=attempted_query
    )
    try:
        patch_input = GenericPatchInput.model_validate(
            {"candidate_id": candidate_id, "updates": list(updates or [])}
        )
    except ValidationError as exc:
        return _generic_validation_result(
            message="patch_generic_object failed input validation.",
            issues=_model_validation_issues(exc),
            method="patch_generic_object",
            attempted_query=attempted_query,
        )

    workspace = get_active_extraction_builder_workspace()
    try:
        candidate = workspace.get_candidate(patch_input.candidate_id)
    except KeyError as exc:
        return _generic_validation_result(
            message=str(exc),
            issues=[
                {
                    "field_path": "candidate_id",
                    "reason": "unknown_candidate_id",
                    "message": str(exc),
                }
            ],
            method="patch_generic_object",
            attempted_query=attempted_query,
        )

    staged_payload = dict(candidate.staged_fields)
    evidence_ids = list(candidate.evidence_record_ids)
    pending_ref_ids = list(candidate.pending_ref_ids)
    for update in patch_input.updates:
        if update.field_path == "evidence_record_ids":
            new_ids = [
                str(item).strip()
                for item in (update.evidence_record_ids or [])
                if str(item).strip()
            ]
            if not new_ids:
                return _generic_validation_result(
                    message="evidence_record_ids patch requires at least one evidence ID.",
                    issues=[
                        {
                            "field_path": "evidence_record_ids",
                            "reason": "missing_evidence_record_ids",
                            "message": "evidence_record_ids patch requires evidence_record_ids.",
                        }
                    ],
                    method="patch_generic_object",
                    attempted_query=attempted_query,
                )
            evidence_ids = new_ids
            continue
        _set_nested_patch_value(staged_payload, update.field_path, update.value)

    class_key = str(staged_payload.get("class_key") or "").strip()
    try:
        entry = load_generic_class_catalog().require_stageable(class_key)
    except (KeyError, ValueError) as exc:
        return _generic_validation_result(
            message="patch_generic_object produced an invalid class_key.",
            issues=[
                {
                    "field_path": "class_key",
                    "reason": "invalid_class_key",
                    "message": str(exc),
                }
            ],
            method="patch_generic_object",
            attempted_query=attempted_query,
        )
    staged_payload["class_key"] = entry.class_key
    staged_payload["object_type"] = entry.generic_object_type
    raw_payload = staged_payload.get("payload")
    if isinstance(raw_payload, Mapping):
        try:
            _validate_payload_keys_for_entry(raw_payload, entry=entry)
        except ValueError as exc:
            return _generic_validation_result(
                message="patch_generic_object produced invalid payload fields.",
                issues=[
                    {
                        "field_path": "payload",
                        "reason": "unknown_payload_field",
                        "message": str(exc),
                    }
                ],
                method="patch_generic_object",
                attempted_query=attempted_query,
            )
    if isinstance(staged_payload.get("pending_ref_id"), str):
        pending_ref_ids = [staged_payload["pending_ref_id"]]

    workspace.upsert_candidate(
        candidate_id=patch_input.candidate_id,
        staged_fields=staged_payload,
        pending_ref_ids=pending_ref_ids,
        evidence_record_ids=evidence_ids,
        resolver_selection_refs=[],
        status=CANDIDATE_STATUS_VALID,
    )
    summary = {
        "candidate_id": patch_input.candidate_id,
        "patched_field_count": len(patch_input.updates),
        "class_key": entry.class_key,
        "builder": _builder_summary(workspace),
    }
    _emit_generic_builder_event(
        "generic_builder.patch_completed",
        action="patch",
        input_summary=attempted_query,
        output_summary=summary,
    )
    return _ok(data=summary, count=1, lookup_status=LOOKUP_STATUS_SUCCESS)


def _discard_generic_object_impl(
    candidate_id: str,
    reason: Optional[str] = None,
) -> AgrQueryResult:
    """Discard one staged generic candidate."""

    attempted_query = _attempt_query(
        "discard_generic_object", candidate_id=candidate_id, reason=reason
    )
    _emit_generic_builder_event(
        "generic_builder.discard_requested",
        action="discard",
        input_summary=attempted_query,
    )
    try:
        discard_input = GenericDiscardInput(candidate_id=candidate_id, reason=reason)
    except ValidationError as exc:
        return _generic_validation_result(
            message="discard_generic_object failed input validation.",
            issues=_model_validation_issues(exc),
            method="discard_generic_object",
            attempted_query=attempted_query,
        )
    workspace = get_active_extraction_builder_workspace()
    try:
        workspace.discard_candidate(discard_input.candidate_id, reason=discard_input.reason)
    except (KeyError, ExtractionBuilderError) as exc:
        return _generic_validation_result(
            message=str(exc),
            issues=[
                {
                    "field_path": "candidate_id",
                    "reason": "discard_failed",
                    "message": str(exc),
                }
            ],
            method="discard_generic_object",
            attempted_query=attempted_query,
        )
    summary = _builder_summary(workspace, include_discarded=True)
    _emit_generic_builder_event(
        "generic_builder.discard_completed",
        action="discard",
        input_summary=attempted_query,
        output_summary=summary,
    )
    return _ok(
        data=summary,
        count=summary["candidate_count"],
        lookup_status=LOOKUP_STATUS_SUCCESS,
    )


def _list_staged_generic_objects_impl(
    include_discarded: bool,
    limit: int = _BUILDER_LIST_DEFAULT_LIMIT,
    offset: int = 0,
) -> AgrQueryResult:
    """List compact summaries for staged generic candidates, one page at a time."""

    attempted_query = _attempt_query(
        "list_staged_generic_objects",
        include_discarded=include_discarded,
        limit=limit,
        offset=offset,
    )
    _emit_generic_builder_event(
        "generic_builder.list_requested", action="list", input_summary=attempted_query
    )
    try:
        list_input = GenericListInput(
            include_discarded=include_discarded, limit=limit, offset=offset
        )
    except ValidationError as exc:
        return _generic_validation_result(
            message="list_staged_generic_objects failed input validation.",
            issues=_model_validation_issues(exc),
            method="list_staged_generic_objects",
            attempted_query=attempted_query,
        )
    workspace = get_active_extraction_builder_workspace()
    summary = _builder_candidate_list(
        workspace,
        include_discarded=list_input.include_discarded,
        limit=list_input.limit,
        offset=list_input.offset,
    )
    _emit_generic_builder_event(
        "generic_builder.list_completed",
        action="list",
        input_summary=attempted_query,
        output_summary=summary,
    )
    return _ok(
        data=summary,
        count=summary["candidate_count"],
        lookup_status=LOOKUP_STATUS_SUCCESS,
    )


def _find_staged_generic_objects_impl(
    field_value_contains: Optional[str] = None,
    pending_ref_id: Optional[str] = None,
    evidence_record_id: Optional[str] = None,
    candidate_id: Optional[str] = None,
    has_validation_errors: Optional[bool] = None,
    include_discarded: bool = False,
    limit: int = _BUILDER_LIST_DEFAULT_LIMIT,
    offset: int = 0,
) -> AgrQueryResult:
    """Find staged generic drafts by content or id, one page at a time."""

    attempted_query = _attempt_query(
        "find_staged_generic_objects",
        field_value_contains=field_value_contains,
        pending_ref_id=pending_ref_id,
        evidence_record_id=evidence_record_id,
        candidate_id=candidate_id,
        has_validation_errors=has_validation_errors,
        include_discarded=include_discarded,
        limit=limit,
        offset=offset,
    )
    _emit_generic_builder_event(
        "generic_builder.find_requested", action="find", input_summary=attempted_query
    )
    try:
        find_input = GenericFindInput(
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
        return _generic_validation_result(
            message="find_staged_generic_objects failed input validation.",
            issues=_model_validation_issues(exc),
            method="find_staged_generic_objects",
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
    _emit_generic_builder_event(
        "generic_builder.find_completed",
        action="find",
        input_summary=attempted_query,
        output_summary=summary,
    )
    return _ok(
        data=summary,
        count=summary["matched_candidate_count"],
        lookup_status=LOOKUP_STATUS_SUCCESS,
    )


def _materialize_generic_with_events(
    *,
    workspace: Any,
    candidate_ids: Sequence[str],
    evidence_records: Sequence[Mapping[str, Any]],
    resolver_entry_lookup: Optional[Any],
) -> Any:
    """Wrap generic materialization with trace events."""

    candidate_id_list = list(candidate_ids)
    _emit_generic_builder_event(
        "generic_materializer.started",
        action="materialize",
        input_summary={
            "candidate_ids": candidate_id_list,
            "materializer_id": GENERIC_MATERIALIZER_ID,
        },
    )
    materialization = materialize_generic_builder_state(
        workspace=workspace,
        candidate_ids=candidate_id_list,
        evidence_records=evidence_records,
        resolver_entry_lookup=resolver_entry_lookup,
    )
    if not materialization.ok or materialization.payload is None:
        _emit_generic_builder_event(
            "generic_materializer.validation_failed",
            action="materialize",
            input_summary={"candidate_ids": candidate_id_list},
            output_summary=materialization.summary(),
            validation={
                "status": "failed",
                "issues": [dict(issue) for issue in materialization.issues],
            },
        )
        return materialization
    _emit_generic_builder_event(
        "generic_materializer.completed",
        action="materialize",
        input_summary={"candidate_ids": candidate_id_list},
        output_summary={
            **materialization.summary(),
            "curatable_objects": materialization.payload.get("curatable_objects", []),
            "materialized_envelope": materialization.payload,
        },
    )
    return materialization


def _finalize_generic_extraction_impl(candidate_ids: List[str]) -> AgrQueryResult:
    """Finalize staged generic candidates through the builder handoff contract."""

    attempted_query = _attempt_query(
        "finalize_generic_extraction", candidate_ids=candidate_ids
    )
    _emit_generic_builder_event(
        "generic_builder.finalize_requested",
        action="finalize",
        input_summary=attempted_query,
    )
    try:
        GenericFinalizeInput(candidate_ids=candidate_ids)
    except ValidationError as exc:
        return _generic_validation_result(
            message="finalize_generic_extraction failed input validation.",
            issues=_model_validation_issues(exc),
            method="finalize_generic_extraction",
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
        materialize=_materialize_generic_with_events,
        evidence_records=evidence_records,
        resolver_entry_lookup=None,
        materialized_candidate_prefix="generic-envelope",
        require_evidence_record_ids=True,
        require_resolver_selections=False,
    )

    if not outcome.ok:
        return _generic_validation_result(
            message=f"finalize_generic_extraction {outcome.message}",
            issues=list(outcome.issues),
            method="finalize_generic_extraction",
            attempted_query=attempted_query,
        )

    finalization = outcome.finalization
    if finalization is None:
        return _generic_validation_result(
            message="finalize_generic_extraction did not produce a finalization payload.",
            issues=[
                {
                    "field_path": "builder_finalization",
                    "reason": "missing_finalization",
                    "message": "Builder finalization succeeded without a finalization payload.",
                }
            ],
            method="finalize_generic_extraction",
            attempted_query=attempted_query,
        )
    summary = {
        "builder_finalization": finalization.summary(),
        "builder": _builder_summary(workspace, include_discarded=True),
    }
    _emit_generic_builder_event(
        "generic_builder.finalize_completed",
        action="finalize",
        input_summary=attempted_query,
        output_summary=summary,
    )
    return _ok(
        data=summary,
        count=finalization.finalized_candidate_count,
        lookup_status=LOOKUP_STATUS_SUCCESS,
    )


list_generic_object_classes = function_tool(
    strict_mode=False, name_override="list_generic_object_classes"
)(_list_generic_object_classes_impl)
stage_generic_object = function_tool(
    strict_mode=False, name_override="stage_generic_object"
)(_stage_generic_object_impl)
patch_generic_object = function_tool(
    strict_mode=False, name_override="patch_generic_object"
)(_patch_generic_object_impl)
discard_generic_object = function_tool(
    strict_mode=False, name_override="discard_generic_object"
)(_discard_generic_object_impl)
list_staged_generic_objects = function_tool(
    strict_mode=False, name_override="list_staged_generic_objects"
)(_list_staged_generic_objects_impl)
find_staged_generic_objects = function_tool(
    strict_mode=False, name_override="find_staged_generic_objects"
)(_find_staged_generic_objects_impl)
finalize_generic_extraction = function_tool(
    strict_mode=False, name_override="finalize_generic_extraction"
)(_finalize_generic_extraction_impl)


__all__ = [
    "discard_generic_object",
    "finalize_generic_extraction",
    "find_staged_generic_objects",
    "list_generic_object_classes",
    "list_staged_generic_objects",
    "patch_generic_object",
    "stage_generic_object",
]
