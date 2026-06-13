"""Read-only supervisor inspection for persisted extraction results."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence
from uuid import UUID

from pydantic import ValidationError

from src.lib.chat_state import document_state
from src.lib.context import get_current_session_id, get_current_user_id
from src.lib.curation_workspace.extraction_results import list_extraction_results
from src.lib.domain_packs.supervisor_manifest import (
    supervisor_manifest_policy_for_object,
)
from src.lib.openai_agents.bounded_list import normalize_page_limit, offset_page
from src.lib.openai_agents.config import (
    get_inspect_results_json_depth_limit,
    get_inspect_results_json_object_item_limit,
    get_inspect_results_evidence_page_size,
    get_inspect_results_evidence_text_limit,
    get_inspect_results_list_page_size,
    get_inspect_results_validation_detail_list_limit,
    get_inspect_results_validation_page_size,
    get_supervisor_field_text_limit,
    get_supervisor_manifest_page_size,
    get_supervisor_max_list_limit,
    get_supervisor_text_preview_limit,
)
from src.lib.openai_agents.extraction_manifest import (
    ExtractionManifestError,
    build_extraction_manifest_object,
    build_extraction_manifest_page,
)
from src.schemas.curation_workspace import CurationExtractionSourceKind
from src.schemas.domain_envelope import (
    CuratableObjectEnvelope,
    DomainEnvelope,
    ValidationFinding,
    parse_field_path,
)
from src.schemas.domain_pack_metadata import DomainPackMetadata


_RESULT_REF_PREFIX = "extraction-result:"
_ACTIONS = frozenset(
    {"help", "list", "summary", "objects", "object", "field", "evidence", "validation"}
)
_TARGETS = frozenset(
    {"latest", "this_chat", "current_document", "flow_run", "all_authorized"}
)
_MAX_LIST_LIMIT = get_supervisor_max_list_limit()
_MANIFEST_PAGE_SIZE = get_supervisor_manifest_page_size()
_EVIDENCE_PAGE_SIZE = get_inspect_results_evidence_page_size()
_EVIDENCE_TEXT_LIMIT = get_inspect_results_evidence_text_limit()
_RESULT_LIST_PAGE_SIZE = get_inspect_results_list_page_size()
_VALIDATION_PAGE_SIZE = get_inspect_results_validation_page_size()
_VALIDATION_DETAIL_LIST_LIMIT = get_inspect_results_validation_detail_list_limit()
_JSON_DEPTH_LIMIT = get_inspect_results_json_depth_limit()
_JSON_OBJECT_ITEM_LIMIT = get_inspect_results_json_object_item_limit()
_FIELD_TEXT_LIMIT = get_supervisor_field_text_limit()
_TEXT_PREVIEW_LIMIT = get_supervisor_text_preview_limit()
_JSON_LIST_LIMIT = max(_MAX_LIST_LIMIT, _MANIFEST_PAGE_SIZE, _EVIDENCE_PAGE_SIZE)
_EVIDENCE_PATH_PARTS = frozenset(
    {
        "evidence",
        "evidence_records",
        "evidence_items",
        "evidence_anchors",
        "verified_quote",
        "quote",
        "evidence_quote",
        "source_quote",
        "snippet",
        "source_chunk_id",
        "chunk_id",
        "chunk_text",
        "source_text",
    }
)
_EVIDENCE_TEXT_KEYS = (
    "verified_quote",
    "quote",
    "evidence_quote",
    "source_quote",
    "snippet",
)
_EVIDENCE_CONTEXT_KEYS = (
    "page",
    "page_number",
    "section",
    "source_section",
    "subsection",
    "figure_reference",
    "chunk_id",
    "source_chunk_id",
    "status",
    "confidence",
)


async def inspect_results(
    *,
    action: str = "help",
    result_ref: str | None = None,
    target: str = "latest",
    object_ref: str | None = None,
    field_path: str | None = None,
    adapter_keys: list[str] | None = None,
    flow_run_id: str | None = None,
    cursor: str | None = None,
    limit: int | None = None,
) -> str:
    """Inspect persisted canonical extraction results through bounded actions."""

    normalized_action = _normalize_action(action)
    if normalized_action == "help":
        return _help_response()
    if normalized_action not in _ACTIONS:
        return _error_response(
            "invalid_action",
            "Unsupported inspect_results action. Use action=\"help\" for supported actions.",
            action=normalized_action,
        )

    session_id = get_current_session_id()
    user_id = get_current_user_id()
    if not session_id or not user_id:
        return _error_response(
            "unavailable",
            "inspect_results is only available inside an active chat session.",
            action=normalized_action,
        )

    parsed_ref, ref_error = _parse_result_ref(result_ref)
    if ref_error:
        return _error_response(
            ref_error,
            "result_ref must use the canonical extraction-result:<uuid> form.",
            action=normalized_action,
            result_ref=result_ref,
        )

    normalized_target = _normalize_target(target)
    if normalized_target not in _TARGETS:
        return _error_response(
            "invalid_target",
            "Unsupported target. Use latest, this_chat, current_document, flow_run, or all_authorized.",
            action=normalized_action,
            target=normalized_target,
        )

    records, resolve_error = _authorized_records(
        result_id=parsed_ref,
        target=normalized_target,
        session_id=session_id,
        user_id=user_id,
        flow_run_id=flow_run_id,
        adapter_keys=adapter_keys,
    )
    if resolve_error:
        return _error_response(
            resolve_error,
            _resolve_error_message(resolve_error),
            action=normalized_action,
            target=normalized_target,
        )

    if normalized_action == "list":
        return _list_response(
            records,
            action=normalized_action,
            target=normalized_target,
            cursor=cursor,
            limit=limit,
        )

    if not records:
        return _error_response(
            "no_context",
            "No authorized persisted extraction results matched this request.",
            action=normalized_action,
            target=normalized_target,
        )

    record = records[0]
    try:
        envelope = _canonical_envelope_for_record(record)
    except (TypeError, ValueError, ValidationError) as exc:
        return _error_response(
            "unsupported_payload",
            "inspect_results only reads canonical domain-envelope extraction results.",
            action=normalized_action,
            result_ref=_record_result_ref(record),
            extraction_result_id=_record_id(record),
            detail=str(exc),
        )

    if normalized_action == "summary":
        return _summary_response(record, cursor=cursor, limit=limit)
    if normalized_action == "objects":
        return _objects_response(record, cursor=cursor, limit=limit)
    if normalized_action == "object":
        return _object_response(record, envelope=envelope, object_ref=object_ref)
    if normalized_action == "field":
        return _field_response(
            record,
            envelope=envelope,
            object_ref=object_ref,
            field_path=field_path,
        )
    if normalized_action == "evidence":
        return _evidence_response(
            record,
            envelope=envelope,
            object_ref=object_ref,
            cursor=cursor,
            limit=limit,
        )
    if normalized_action == "validation":
        return _validation_response(
            record,
            envelope=envelope,
            object_ref=object_ref,
            field_path=field_path,
            cursor=cursor,
            limit=limit,
        )

    return _error_response(
        "invalid_action",
        "Unsupported inspect_results action. Use action=\"help\" for supported actions.",
        action=normalized_action,
    )


def _help_response() -> str:
    return _tool_response(
        "ok",
        "inspect_results browses persisted canonical extraction results without rerunning specialists.",
        action="help",
        actions=sorted(_ACTIONS),
        targets=sorted(_TARGETS),
        result_ref_format="extraction-result:<uuid>",
        boundaries=[
            "Default summary/object manifests use only domain-pack YAML supervisor_manifest fields.",
            "Evidence text is excluded from summary and objects; use action=\"evidence\" with object_ref.",
            "Raw UUIDs and transient lookup refs are rejected as result_ref values.",
            "Export and curation prep are separate explicit supervisor actions.",
            "Trace tools are for debugging behavior, not browsing extraction payloads.",
        ],
        examples=[
            'inspect_results(action="list", target="latest")',
            'inspect_results(action="objects", result_ref="extraction-result:<uuid>")',
            'inspect_results(action="evidence", result_ref="extraction-result:<uuid>", object_ref="<object_ref>")',
            'inspect_results(action="field", result_ref="extraction-result:<uuid>", object_ref="<object_ref>", field_path="<yaml_field>")',
        ],
    )


def _list_response(
    records: Sequence[Any],
    *,
    action: str,
    target: str,
    cursor: str | None,
    limit: int | None,
) -> str:
    bounded_limit = normalize_page_limit(
        limit,
        default=_RESULT_LIST_PAGE_SIZE,
        maximum=_MAX_LIST_LIMIT,
    )
    page, truncated, next_cursor = offset_page(records, limit=bounded_limit, cursor=cursor)
    return _tool_response(
        "ok",
        f"{len(page)} authorized persisted extraction result(s) matched.",
        action=action,
        target=target,
        results=[_record_summary(record) for record in page],
        total_count=len(records),
        cursor=cursor,
        next_cursor=next_cursor,
        limit=bounded_limit,
        truncated=truncated,
        next_actions=[
            'Use inspect_results(action="summary", result_ref="<result_ref>") for one result.',
            'Use inspect_results(action="objects", result_ref="<result_ref>") to browse manifest rows.',
        ],
    )


def _summary_response(
    record: Any,
    *,
    cursor: str | None,
    limit: int | None,
) -> str:
    try:
        manifest = _manifest_page_for_record(record, cursor=cursor, limit=limit)
    except ExtractionManifestError as exc:
        return _error_response(
            "manifest_unavailable",
            str(exc),
            action="summary",
            result_ref=_record_result_ref(record),
            extraction_result_id=_record_id(record),
        )
    page_info = _page_info(manifest)
    return _tool_response(
        "ok",
        "Canonical extraction result summary is ready.",
        action="summary",
        result_ref=_record_result_ref(record),
        extraction_result_id=_record_id(record),
        summary=_record_summary(record),
        manifest=manifest,
        cursor=page_info.get("cursor"),
        next_cursor=page_info.get("next_cursor"),
        limit=page_info.get("limit"),
        truncated=page_info.get("next_cursor") is not None,
        next_actions=manifest.get("next_actions", []),
    )


def _objects_response(
    record: Any,
    *,
    cursor: str | None,
    limit: int | None,
) -> str:
    try:
        manifest = _manifest_page_for_record(record, cursor=cursor, limit=limit)
    except ExtractionManifestError as exc:
        return _error_response(
            "manifest_unavailable",
            str(exc),
            action="objects",
            result_ref=_record_result_ref(record),
            extraction_result_id=_record_id(record),
        )
    page_info = _page_info(manifest)
    return _tool_response(
        "ok",
        "Supervisor-visible extraction objects are ready.",
        action="objects",
        result_ref=_record_result_ref(record),
        extraction_result_id=_record_id(record),
        domain_pack_id=manifest.get("domain_pack_id"),
        objects=manifest.get("objects", []),
        object_count=manifest.get("object_count"),
        page=page_info,
        cursor=page_info.get("cursor"),
        next_cursor=page_info.get("next_cursor"),
        limit=page_info.get("limit"),
        truncated=page_info.get("next_cursor") is not None,
        next_actions=manifest.get("next_actions", []),
    )


def _object_response(
    record: Any,
    *,
    envelope: DomainEnvelope,
    object_ref: str | None,
) -> str:
    normalized_ref = _optional_text(object_ref)
    if not normalized_ref:
        return _error_response(
            "invalid_request",
            "object_ref is required for action=\"object\".",
            action="object",
            result_ref=_record_result_ref(record),
        )
    try:
        obj = _resolve_object(envelope, normalized_ref)
        manifest_object = build_extraction_manifest_object(
            _record_payload_mapping(record),
            object_ref=normalized_ref,
        )
    except (ExtractionManifestError, ValueError) as exc:
        return _error_response(
            "object_not_found",
            str(exc),
            action="object",
            result_ref=_record_result_ref(record),
            extraction_result_id=_record_id(record),
            object_ref=normalized_ref,
        )
    return _tool_response(
        "ok",
        "Supervisor-visible extraction object is ready.",
        action="object",
        result_ref=_record_result_ref(record),
        extraction_result_id=_record_id(record),
        object_ref=normalized_ref,
        object=manifest_object,
        evidence_count=len(obj.evidence_record_ids),
        next_actions=[
            f'Use inspect_results(action="field", result_ref="{_record_result_ref(record)}", object_ref="{normalized_ref}", field_path="<yaml_field>") for one field.',
            f'Use inspect_results(action="evidence", result_ref="{_record_result_ref(record)}", object_ref="{normalized_ref}") for evidence text.',
        ],
    )


def _field_response(
    record: Any,
    *,
    envelope: DomainEnvelope,
    object_ref: str | None,
    field_path: str | None,
) -> str:
    normalized_ref = _optional_text(object_ref)
    normalized_path = _optional_text(field_path)
    if not normalized_ref:
        return _error_response(
            "invalid_request",
            "object_ref is required for action=\"field\".",
            action="field",
            result_ref=_record_result_ref(record),
        )
    if not normalized_path:
        return _error_response(
            "invalid_request",
            "field_path is required for action=\"field\".",
            action="field",
            result_ref=_record_result_ref(record),
            object_ref=normalized_ref,
        )
    if _is_evidence_path(normalized_path):
        return _error_response(
            "evidence_path_requires_evidence_action",
            "Evidence text paths are only available through action=\"evidence\".",
            action="field",
            result_ref=_record_result_ref(record),
            object_ref=normalized_ref,
            field_path=normalized_path,
        )

    try:
        obj = _resolve_object(envelope, normalized_ref)
        visible_paths = _supervisor_visible_field_paths(envelope.domain_pack_id, obj.object_type)
    except ValueError as exc:
        return _error_response(
            "object_not_found",
            str(exc),
            action="field",
            result_ref=_record_result_ref(record),
            extraction_result_id=_record_id(record),
            object_ref=normalized_ref,
        )
    if normalized_path not in visible_paths:
        return _error_response(
            "field_not_supervisor_visible",
            "field_path must be one of this object's domain-pack YAML supervisor_manifest fields.",
            action="field",
            result_ref=_record_result_ref(record),
            extraction_result_id=_record_id(record),
            object_ref=normalized_ref,
            field_path=normalized_path,
            visible_field_paths=sorted(visible_paths),
        )

    try:
        value = _payload_path_value(obj.payload, normalized_path)
    except ValueError as exc:
        return _error_response(
            "invalid_field_path",
            str(exc),
            action="field",
            result_ref=_record_result_ref(record),
            object_ref=normalized_ref,
            field_path=normalized_path,
        )
    if value is None:
        return _error_response(
            "field_not_found",
            "field_path did not resolve on this object payload.",
            action="field",
            result_ref=_record_result_ref(record),
            extraction_result_id=_record_id(record),
            object_ref=normalized_ref,
            field_path=normalized_path,
        )
    if not _is_scalar(value):
        return _error_response(
            "field_not_scalar",
            "inspect_results field views only return scalar YAML manifest fields.",
            action="field",
            result_ref=_record_result_ref(record),
            extraction_result_id=_record_id(record),
            object_ref=normalized_ref,
            field_path=normalized_path,
        )

    return _tool_response(
        "ok",
        "Supervisor-visible field value is ready.",
        action="field",
        result_ref=_record_result_ref(record),
        extraction_result_id=_record_id(record),
        object_ref=normalized_ref,
        field_path=normalized_path,
        value=_preview_text(value, limit=_FIELD_TEXT_LIMIT),
    )


def _evidence_response(
    record: Any,
    *,
    envelope: DomainEnvelope,
    object_ref: str | None,
    cursor: str | None,
    limit: int | None,
) -> str:
    normalized_ref = _optional_text(object_ref)
    if not normalized_ref:
        return _evidence_inventory_response(record, cursor=cursor, limit=limit)

    try:
        obj = _resolve_object(envelope, normalized_ref)
    except ValueError as exc:
        return _error_response(
            "object_not_found",
            str(exc),
            action="evidence",
            result_ref=_record_result_ref(record),
            extraction_result_id=_record_id(record),
            object_ref=normalized_ref,
        )

    bounded_limit = normalize_page_limit(
        limit,
        default=_EVIDENCE_PAGE_SIZE,
        maximum=_MAX_LIST_LIMIT,
    )
    evidence = _object_evidence_records(envelope, obj)
    page, truncated, next_cursor = offset_page(
        evidence,
        limit=bounded_limit,
        cursor=cursor,
    )
    return _tool_response(
        "ok",
        "Bounded evidence text is ready.",
        action="evidence",
        result_ref=_record_result_ref(record),
        extraction_result_id=_record_id(record),
        object_ref=normalized_ref,
        evidence=[_compact_evidence_record(item) for item in page],
        evidence_count=len(evidence),
        cursor=cursor,
        next_cursor=next_cursor,
        limit=bounded_limit,
        truncated=truncated,
    )


def _evidence_inventory_response(
    record: Any,
    *,
    cursor: str | None,
    limit: int | None,
) -> str:
    try:
        manifest = _manifest_page_for_record(record, cursor=cursor, limit=limit)
    except ExtractionManifestError as exc:
        return _error_response(
            "manifest_unavailable",
            str(exc),
            action="evidence",
            result_ref=_record_result_ref(record),
            extraction_result_id=_record_id(record),
        )
    raw_objects = manifest.get("objects")
    objects: list[Any] = raw_objects if isinstance(raw_objects, list) else []
    inventory = [
        {
            "object_ref": item.get("object_ref"),
            "object_type": item.get("object_type"),
            "status": item.get("status"),
            "evidence_count": item.get("evidence_count", 0),
        }
        for item in objects
        if isinstance(item, Mapping)
    ]
    page_info = _page_info(manifest)
    return _tool_response(
        "ok",
        "Evidence inventory is ready. Pass object_ref to fetch bounded evidence text.",
        action="evidence",
        result_ref=_record_result_ref(record),
        extraction_result_id=_record_id(record),
        evidence_inventory=inventory,
        object_count=manifest.get("object_count"),
        page=page_info,
        cursor=page_info.get("cursor"),
        next_cursor=page_info.get("next_cursor"),
        limit=page_info.get("limit"),
        truncated=page_info.get("next_cursor") is not None,
        next_actions=[
            f'Use inspect_results(action="evidence", result_ref="{_record_result_ref(record)}", object_ref="<object_ref>") for evidence text.'
        ],
    )


def _validation_response(
    record: Any,
    *,
    envelope: DomainEnvelope,
    object_ref: str | None,
    field_path: str | None,
    cursor: str | None,
    limit: int | None,
) -> str:
    normalized_ref = _optional_text(object_ref)
    normalized_path = _optional_text(field_path)
    if normalized_path and not normalized_ref:
        return _error_response(
            "invalid_request",
            "object_ref is required when field_path is supplied for action=\"validation\".",
            action="validation",
            result_ref=_record_result_ref(record),
            field_path=normalized_path,
        )

    object_keys: set[tuple[str, str]] | None = None
    if normalized_ref:
        try:
            obj = _resolve_object(envelope, normalized_ref)
        except ValueError as exc:
            return _error_response(
                "object_not_found",
                str(exc),
                action="validation",
                result_ref=_record_result_ref(record),
                extraction_result_id=_record_id(record),
                object_ref=normalized_ref,
            )
        object_keys = set(obj.ref_keys())

    findings = [
        finding
        for finding in envelope.validation_findings
        if _finding_matches(finding, object_keys=object_keys, field_path=normalized_path)
    ]
    bounded_limit = normalize_page_limit(
        limit,
        default=_VALIDATION_PAGE_SIZE,
        maximum=_MAX_LIST_LIMIT,
    )
    page, truncated, next_cursor = offset_page(
        findings,
        limit=bounded_limit,
        cursor=cursor,
    )
    return _tool_response(
        "ok",
        "Validation findings are ready.",
        action="validation",
        result_ref=_record_result_ref(record),
        extraction_result_id=_record_id(record),
        object_ref=normalized_ref,
        field_path=normalized_path,
        validation_findings=[_validation_finding_view(finding) for finding in page],
        finding_count=len(findings),
        cursor=cursor,
        next_cursor=next_cursor,
        limit=bounded_limit,
        truncated=truncated,
    )


def _authorized_records(
    *,
    result_id: str | None,
    target: str,
    session_id: str,
    user_id: str,
    flow_run_id: str | None,
    adapter_keys: Sequence[str] | None,
) -> tuple[list[Any], str | None]:
    document_id = _active_document_id(user_id)
    if result_id:
        if target in {"latest", "this_chat"}:
            records = _session_records(session_id=session_id, user_id=user_id)
        elif target == "current_document":
            if not document_id:
                return [], "document_required"
            records = _document_records(document_id=document_id, user_id=user_id)
        elif target == "flow_run":
            records = (
                list_extraction_results(user_id=user_id, flow_run_id=flow_run_id)
                if _optional_text(flow_run_id)
                else _session_records(session_id=session_id, user_id=user_id)
            )
        else:
            records = list_extraction_results(user_id=user_id)
        records = _dedupe_records(records)
        records = [record for record in records if _record_id(record) == result_id]
    elif target in {"latest", "this_chat"}:
        records = _session_records(session_id=session_id, user_id=user_id)
    elif target == "current_document":
        if not document_id:
            return [], "document_required"
        records = _document_records(document_id=document_id, user_id=user_id)
    elif target == "flow_run":
        if not _optional_text(flow_run_id):
            return [], "flow_run_required"
        records = list_extraction_results(user_id=user_id, flow_run_id=flow_run_id)
    else:
        records = list_extraction_results(user_id=user_id)

    records = _filter_by_adapter(records, adapter_keys)
    return _sort_records_newest(records), None


def _session_records(*, session_id: str, user_id: str) -> list[Any]:
    return _dedupe_records(
        [
            *list_extraction_results(
                origin_session_id=session_id,
                user_id=user_id,
                source_kind=CurationExtractionSourceKind.CHAT,
            ),
            *list_extraction_results(
                origin_session_id=session_id,
                user_id=user_id,
                source_kind=CurationExtractionSourceKind.FLOW,
            ),
        ]
    )


def _document_records(*, document_id: str, user_id: str) -> list[Any]:
    return list_extraction_results(document_id=document_id, user_id=user_id)


def _filter_by_adapter(records: Sequence[Any], adapter_keys: Sequence[str] | None) -> list[Any]:
    allowed = {str(key).strip() for key in adapter_keys or [] if str(key).strip()}
    if not allowed:
        return list(records)
    return [
        record
        for record in records
        if str(_record_attr(record, "adapter_key") or "") in allowed
    ]


def _dedupe_records(records: Sequence[Any]) -> list[Any]:
    deduped: list[Any] = []
    seen: set[str] = set()
    for record in records:
        record_id = _record_id(record)
        key = record_id or str(id(record))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(record)
    return deduped


def _sort_records_newest(records: Sequence[Any]) -> list[Any]:
    return sorted(
        records,
        key=lambda record: (_record_created_timestamp(record), _record_id(record)),
        reverse=True,
    )


def _record_summary(record: Any) -> dict[str, Any]:
    try:
        envelope = _canonical_envelope_for_record(record)
        canonical_status = "canonical_domain_envelope"
        object_count: int | None = len(envelope.objects)
        validation_count: int | None = len(envelope.validation_findings)
        domain_pack_id = envelope.domain_pack_id
        result_status = envelope.status.value
    except (TypeError, ValueError, ValidationError):
        canonical_status = "unsupported_payload"
        object_count = None
        validation_count = None
        domain_pack_id = None
        result_status = None
    return {
        "result_ref": _record_result_ref(record),
        "extraction_result_id": _record_id(record),
        "document_id": _record_attr(record, "document_id"),
        "adapter_key": _record_attr(record, "adapter_key"),
        "agent_key": _record_attr(record, "agent_key"),
        "source_kind": _record_source_kind(record),
        "origin_session_id": _record_attr(record, "origin_session_id"),
        "flow_run_id": _record_attr(record, "flow_run_id"),
        "trace_id": _record_attr(record, "trace_id"),
        "created_at": _record_created_at_text(record),
        "domain_pack_id": domain_pack_id,
        "result_status": result_status,
        "canonical_status": canonical_status,
        "object_count": object_count,
        "validation_finding_count": validation_count,
    }


def _manifest_page_for_record(
    record: Any,
    *,
    cursor: str | None,
    limit: int | None,
) -> dict[str, Any]:
    return build_extraction_manifest_page(
        _record_payload_mapping(record),
        extraction_result_id=_record_id(record),
        result_ref=_record_result_ref(record),
        adapter_key=_optional_text(_record_attr(record, "adapter_key")),
        agent_key=_optional_text(_record_attr(record, "agent_key")),
        cursor=cursor,
        limit=_manifest_limit(limit),
    )


def _canonical_envelope_for_record(record: Any) -> DomainEnvelope:
    return DomainEnvelope.model_validate(dict(_record_payload_mapping(record)))


def _record_payload_mapping(record: Any) -> Mapping[str, Any]:
    payload = _record_attr(record, "payload_json")
    if not isinstance(payload, Mapping):
        raise TypeError("extraction result payload_json must be a canonical object")
    return payload


def _resolve_object(
    envelope: DomainEnvelope,
    object_ref: str,
) -> CuratableObjectEnvelope:
    normalized_ref = str(object_ref or "").strip()
    for obj in envelope.objects:
        if _canonical_object_ref(obj) == normalized_ref:
            return obj
    raise ValueError(f"No object matched object_ref {normalized_ref!r}")


def _canonical_object_ref(obj: CuratableObjectEnvelope) -> str:
    if obj.object_id:
        return obj.object_id
    if obj.pending_ref_id:
        return obj.pending_ref_id
    return obj.object_type


def _supervisor_visible_field_paths(domain_pack_id: str, object_type: str) -> set[str]:
    metadata = _domain_pack_metadata(domain_pack_id)
    policy = supervisor_manifest_policy_for_object(metadata, object_type)
    paths = {field.path for field in policy.primary_label_fields}
    if policy.secondary_label_field is not None:
        paths.add(policy.secondary_label_field.path)
    paths.update(field.path for field in policy.summary_fields)
    return paths


def _domain_pack_metadata(domain_pack_id: str) -> DomainPackMetadata:
    from src.lib.curation_workspace.adapter_registry import (
        resolve_curation_domain_pack_by_id,
    )

    domain_pack = resolve_curation_domain_pack_by_id(domain_pack_id)
    if isinstance(domain_pack, DomainPackMetadata):
        return domain_pack
    metadata = getattr(domain_pack, "metadata", domain_pack)
    if isinstance(metadata, DomainPackMetadata):
        return metadata
    raise ValueError(f"Cannot resolve domain pack {domain_pack_id!r}")


def _payload_path_value(payload: Mapping[str, Any], field_path: str) -> Any:
    current: Any = payload
    for part in parse_field_path(field_path):
        if isinstance(part, str):
            if not isinstance(current, Mapping) or part not in current:
                return None
            current = current[part]
            continue
        if (
            not isinstance(current, Sequence)
            or isinstance(current, (str, bytes, bytearray))
            or part >= len(current)
        ):
            return None
        current = current[part]
    return current


def _is_evidence_path(field_path: str) -> bool:
    try:
        parts = parse_field_path(field_path)
    except ValueError:
        return False
    for part in parts:
        if not isinstance(part, str):
            continue
        lowered = part.lower()
        if lowered in _EVIDENCE_PATH_PARTS or lowered.endswith("_quote"):
            return True
    return False


def _object_evidence_records(
    envelope: DomainEnvelope,
    obj: CuratableObjectEnvelope,
) -> list[Mapping[str, Any]]:
    records: list[Mapping[str, Any]] = []
    metadata_records = _metadata_evidence_records(envelope.metadata)
    metadata_by_id = {
        str(item.get("evidence_record_id") or item.get("id") or ""): item
        for item in metadata_records
        if isinstance(item, Mapping)
    }
    for evidence_id in obj.evidence_record_ids:
        metadata_record = metadata_by_id.get(str(evidence_id))
        if metadata_record is not None:
            records.append(metadata_record)
        else:
            records.append({"evidence_record_id": evidence_id})

    direct_evidence = _payload_evidence_record(obj)
    if direct_evidence:
        if records and not any(_record_has_evidence_text(item) for item in records):
            merged = dict(records[0])
            merged.update(direct_evidence)
            records[0] = merged
        else:
            records.append(direct_evidence)

    return _dedupe_evidence_records(records)


def _metadata_evidence_records(metadata: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    raw_records = metadata.get("evidence_records")
    if not isinstance(raw_records, list):
        return []
    return [item for item in raw_records if isinstance(item, Mapping)]


def _payload_evidence_record(obj: CuratableObjectEnvelope) -> dict[str, Any]:
    record: dict[str, Any] = {}
    if obj.evidence_record_ids:
        record["evidence_record_id"] = obj.evidence_record_ids[0]
    for key in (*_EVIDENCE_TEXT_KEYS, *_EVIDENCE_CONTEXT_KEYS):
        value = obj.payload.get(key)
        if value is not None:
            record[key] = value
    return record


def _record_has_evidence_text(record: Mapping[str, Any]) -> bool:
    return any(record.get(key) for key in _EVIDENCE_TEXT_KEYS)


def _dedupe_evidence_records(records: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    deduped: list[Mapping[str, Any]] = []
    seen: set[str] = set()
    for record in records:
        key = str(
            record.get("evidence_record_id")
            or record.get("id")
            or record.get("verified_quote")
            or record.get("quote")
            or id(record)
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(record)
    return deduped


def _compact_evidence_record(record: Mapping[str, Any]) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    for key in ("evidence_record_id", "id", "field_path", *_EVIDENCE_CONTEXT_KEYS):
        value = record.get(key)
        if value is not None:
            compact[key] = _preview_text(value, limit=_TEXT_PREVIEW_LIMIT)
    for key in _EVIDENCE_TEXT_KEYS:
        value = record.get(key)
        if value is not None:
            compact[key] = _preview_text(value, limit=_EVIDENCE_TEXT_LIMIT)
    return compact


def _finding_matches(
    finding: ValidationFinding,
    *,
    object_keys: set[tuple[str, str]] | None,
    field_path: str | None,
) -> bool:
    if object_keys is not None:
        finding_key = None
        if finding.field_ref is not None:
            finding_key = finding.field_ref.object_ref.ref_key()
        elif finding.object_ref is not None:
            finding_key = finding.object_ref.ref_key()
        if finding_key not in object_keys:
            return False
    if field_path is not None:
        if finding.field_ref is None:
            return False
        return finding.field_ref.field_path == field_path
    return True


def _validation_finding_view(finding: ValidationFinding) -> dict[str, Any]:
    object_ref = None
    field_path = None
    if finding.field_ref is not None:
        object_ref = _object_ref_text(finding.field_ref.object_ref)
        field_path = finding.field_ref.field_path
    elif finding.object_ref is not None:
        object_ref = _object_ref_text(finding.object_ref)
    return {
        "finding_id": finding.finding_id,
        "severity": finding.severity.value,
        "status": finding.status.value,
        "code": finding.code,
        "message": _preview_text(finding.message, limit=_FIELD_TEXT_LIMIT),
        "object_ref": object_ref,
        "field_path": field_path,
        "details": _bounded_json(
            finding.details,
            text_limit=_FIELD_TEXT_LIMIT,
            list_limit=_VALIDATION_DETAIL_LIST_LIMIT,
        ),
    }


def _object_ref_text(object_ref: Any) -> str:
    if getattr(object_ref, "object_id", None):
        return str(object_ref.object_id)
    if getattr(object_ref, "pending_ref_id", None):
        return str(object_ref.pending_ref_id)
    return str(getattr(object_ref, "object_type", "") or "")


def _parse_result_ref(value: str | None) -> tuple[str | None, str | None]:
    text = _optional_text(value)
    if text is None:
        return None, None
    if not text.startswith(_RESULT_REF_PREFIX):
        try:
            UUID(text)
        except (TypeError, ValueError):
            return None, "invalid_result_ref"
        return None, "raw_uuid_result_ref"
    raw_uuid = text[len(_RESULT_REF_PREFIX) :].strip()
    try:
        return str(UUID(raw_uuid)), None
    except (TypeError, ValueError):
        return None, "invalid_result_ref"


def _normalize_action(action: str | None) -> str:
    return str(action or "help").strip().lower() or "help"


def _normalize_target(target: str | None) -> str:
    return str(target or "latest").strip().lower() or "latest"


def _resolve_error_message(error_code: str) -> str:
    if error_code == "document_required":
        return "target=\"current_document\" requires an active document in this chat."
    if error_code == "flow_run_required":
        return "target=\"flow_run\" requires flow_run_id unless result_ref is supplied."
    return "Could not resolve authorized extraction results."


def _active_document_id(user_id: str) -> str | None:
    active_document = document_state.get_document(user_id)
    if not isinstance(active_document, Mapping):
        return None
    return _optional_text(active_document.get("id"))


def _record_attr(record: Any, name: str) -> Any:
    if isinstance(record, Mapping):
        if name == "extraction_result_id":
            return record.get("extraction_result_id") or record.get("id")
        if name == "metadata":
            return record.get("metadata") or record.get("extraction_metadata")
        return record.get(name)
    if name == "extraction_result_id":
        return getattr(record, "extraction_result_id", None) or getattr(record, "id", None)
    if name == "metadata":
        return getattr(record, "metadata", None) or getattr(record, "extraction_metadata", None)
    return getattr(record, name, None)


def _record_id(record: Any) -> str:
    return str(_record_attr(record, "extraction_result_id") or "").strip()


def _record_result_ref(record: Any) -> str:
    return f"{_RESULT_REF_PREFIX}{_record_id(record)}"


def _record_source_kind(record: Any) -> str | None:
    source_kind = _record_attr(record, "source_kind")
    value = getattr(source_kind, "value", source_kind)
    return str(value) if value is not None else None


def _record_created_timestamp(record: Any) -> float:
    created_at = _record_attr(record, "created_at")
    if isinstance(created_at, datetime):
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        return created_at.timestamp()
    if isinstance(created_at, str):
        try:
            parsed = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        except ValueError:
            return 0.0
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    return 0.0


def _record_created_at_text(record: Any) -> str | None:
    created_at = _record_attr(record, "created_at")
    if isinstance(created_at, datetime):
        return created_at.isoformat()
    if created_at is None:
        return None
    return str(created_at)


def _page_info(manifest: Mapping[str, Any]) -> Mapping[str, Any]:
    page = manifest.get("page")
    return page if isinstance(page, Mapping) else {}


def _manifest_limit(limit: int | None) -> int:
    return normalize_page_limit(
        limit,
        default=_MANIFEST_PAGE_SIZE,
        maximum=_MANIFEST_PAGE_SIZE,
    )


def _is_scalar(value: Any) -> bool:
    return isinstance(value, (str, int, float, bool)) or value is None


def _optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _preview_text(value: Any, *, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return f"{text[: max(1, limit - 3)].rstrip()}..."


def _bounded_json(
    value: Any,
    *,
    text_limit: int,
    list_limit: int,
    depth: int = 0,
) -> Any:
    if isinstance(value, str):
        return _preview_text(value, limit=text_limit)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if depth >= _JSON_DEPTH_LIMIT:
        return "<truncated>"
    if isinstance(value, Mapping):
        return {
            str(key): _bounded_json(
                item,
                text_limit=text_limit,
                list_limit=list_limit,
                depth=depth + 1,
            )
            for key, item in list(value.items())[:_JSON_OBJECT_ITEM_LIMIT]
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        items = [
            _bounded_json(
                item,
                text_limit=text_limit,
                list_limit=list_limit,
                depth=depth + 1,
            )
            for item in list(value)[:list_limit]
        ]
        if len(value) > list_limit:
            items.append({"truncated_count": len(value) - list_limit})
        return items
    return _preview_text(value, limit=text_limit)


def _tool_response(status: str, message: str, **extra: Any) -> str:
    payload = {"status": status, "message": message}
    payload.update(extra)
    return json.dumps(
        _bounded_json(payload, text_limit=_FIELD_TEXT_LIMIT, list_limit=_JSON_LIST_LIMIT),
        ensure_ascii=True,
        default=str,
    )


def _error_response(error_code: str, message: str, **extra: Any) -> str:
    return _tool_response(
        "error" if error_code not in {"unavailable", "no_context"} else error_code,
        message,
        error_code=error_code,
        **extra,
    )


__all__ = ["inspect_results"]
