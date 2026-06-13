"""Supervisor-facing manifests for canonical extraction results."""

from __future__ import annotations

import math
from typing import Any, Mapping

from pydantic import ValidationError

from src.lib.domain_packs.supervisor_manifest import (
    SupervisorManifestField,
    SupervisorManifestPolicy,
    is_default_supervisor_manifest_object,
    supervisor_manifest_policy_for_object,
)
from src.lib.openai_agents.config import (
    get_supervisor_field_text_limit,
    get_supervisor_manifest_page_size,
    get_supervisor_text_preview_limit,
)
from src.schemas.domain_envelope import (
    CuratableObjectEnvelope,
    DomainEnvelope,
    ValidationFinding,
    ValidationFindingSeverity,
    ValidationFindingStatus,
)
from src.schemas.domain_pack_metadata import DomainPackMetadata, DomainPackObjectDefinition


class ExtractionManifestError(ValueError):
    """Raised when a canonical envelope cannot produce a safe manifest."""


def build_extraction_manifest_page(
    payload: Mapping[str, Any],
    *,
    extraction_result_id: str | None = None,
    result_ref: str | None = None,
    adapter_key: str | None = None,
    agent_key: str | None = None,
    cursor: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Build a structured page from a canonical domain-envelope payload."""

    envelope = _canonical_envelope(payload)
    metadata = _domain_pack_metadata(envelope.domain_pack_id)
    object_definitions = {
        definition.object_type: definition
        for definition in metadata.object_definitions
    }
    manifest_objects = [
        domain_object
        for domain_object in envelope.objects
        if _include_object_in_manifest(domain_object, object_definitions)
    ]

    page_limit = _normalize_limit(limit)
    offset = _cursor_offset(cursor)
    page_objects = manifest_objects[offset : offset + page_limit]
    next_offset = offset + len(page_objects)
    next_cursor = (
        str(next_offset) if next_offset < len(manifest_objects) else None
    )
    manifest_result_ref = result_ref or _result_ref(extraction_result_id)

    return {
        "result_ref": manifest_result_ref,
        "extraction_result_id": extraction_result_id,
        "domain_pack_id": envelope.domain_pack_id,
        "adapter_key": adapter_key,
        "agent_key": agent_key,
        "result_status": (
            "non_empty_extraction_ready"
            if manifest_objects
            else "empty_extraction"
        ),
        "object_count": len(manifest_objects),
        "page": {
            "cursor": cursor,
            "limit": page_limit,
            "next_cursor": next_cursor,
            "page_number": (offset // page_limit) + 1,
            "page_count": max(1, math.ceil(len(manifest_objects) / page_limit)),
        },
        "objects": [
            _manifest_object(
                domain_object,
                metadata=metadata,
                validation_findings=envelope.validation_findings,
            )
            for domain_object in page_objects
        ],
        "validation": _validation_counts(envelope.validation_findings),
        "next_actions": _next_actions(
            result_ref=manifest_result_ref,
            has_objects=bool(manifest_objects),
            next_cursor=next_cursor,
        ),
    }


def build_extraction_manifest_object(
    payload: Mapping[str, Any],
    *,
    object_ref: str,
) -> dict[str, Any]:
    """Build one supervisor-visible manifest object by canonical object ref."""

    envelope = _canonical_envelope(payload)
    metadata = _domain_pack_metadata(envelope.domain_pack_id)
    object_definitions = {
        definition.object_type: definition
        for definition in metadata.object_definitions
    }
    normalized_ref = str(object_ref or "").strip()
    if not normalized_ref:
        raise ExtractionManifestError("object_ref is required")

    for domain_object in envelope.objects:
        if _object_ref(domain_object) != normalized_ref:
            continue
        if not _include_object_in_manifest(domain_object, object_definitions):
            raise ExtractionManifestError(
                f"Object {normalized_ref!r} is not supervisor-visible"
            )
        return _manifest_object(
            domain_object,
            metadata=metadata,
            validation_findings=envelope.validation_findings,
        )

    raise ExtractionManifestError(
        f"No supervisor-visible object matched object_ref {normalized_ref!r}"
    )


def render_extraction_manifest_page(page: Mapping[str, Any]) -> str:
    """Render a structured manifest page for the supervisor handoff."""

    domain_pack_id = str(page.get("domain_pack_id") or "domain envelope")
    result_ref = page.get("result_ref")
    object_count = int(page.get("object_count") or 0)
    result_status = str(page.get("result_status") or "empty_extraction")
    raw_page_info = page.get("page")
    page_info: Mapping[str, Any] = (
        raw_page_info if isinstance(raw_page_info, Mapping) else {}
    )
    page_number = int(page_info.get("page_number") or 1)
    page_count = int(page_info.get("page_count") or 1)
    page_limit = int(page_info.get("limit") or get_supervisor_manifest_page_size())
    next_cursor = page_info.get("next_cursor")

    lines = [
        f"Extraction result ready: {domain_pack_id}",
        (
            f"Result ref: {result_ref}"
            if result_ref
            else "Result ref: unavailable until inline persistence assigns extraction-result:<uuid>"
        ),
        f"Objects found: {object_count}",
        f"Result status: {result_status}",
        f"Manifest page: {page_number} of {page_count}, page_size={page_limit}",
    ]

    if result_status == "non_empty_extraction_ready":
        lines.extend(
            [
                "Recommended supervisor action: answer_from_manifest",
                (
                    "Retry guidance: do not rerun this extractor unless the curator "
                    "asks to broaden/narrow the search or the manifest says the "
                    "requested scope was not handled."
                ),
            ]
        )
    else:
        lines.extend(
            [
                "Recommended supervisor action: report_empty_result",
                (
                    "Retry guidance: retry only with a narrower/broader explicit "
                    "scope, or ask the curator for clarification."
                ),
            ]
        )

    raw_objects = page.get("objects")
    objects: list[Any] = raw_objects if isinstance(raw_objects, list) else []
    for index, item in enumerate(objects, start=1):
        if not isinstance(item, Mapping):
            continue
        label = _text(item.get("display_label")) or "(no label)"
        object_type = _text(item.get("object_type")) or "object"
        object_ref = _text(item.get("object_ref")) or "(no object ref)"
        status = _text(item.get("status")) or "unknown"
        line = f"{index}. {object_type} {object_ref}: {label}"
        secondary_label = _text(item.get("secondary_label"))
        if secondary_label:
            line += f" ({secondary_label})"
        lines.append(line)

        details: list[str] = [f"status={status}"]
        raw_fields = item.get("fields")
        fields: list[Any] = raw_fields if isinstance(raw_fields, list) else []
        for field in fields:
            if not isinstance(field, Mapping):
                continue
            label_text = _text(field.get("label")) or _text(field.get("path"))
            value_text = _text(field.get("value"))
            if label_text and value_text:
                details.append(f"{label_text}={value_text}")
        validation = item.get("validation")
        if isinstance(validation, Mapping):
            warning_count = int(validation.get("warning_count") or 0)
            error_count = int(validation.get("error_count") or 0)
            unresolved_count = int(validation.get("unresolved_count") or 0)
            if warning_count or error_count or unresolved_count:
                details.append(
                    "validation="
                    f"errors:{error_count}, warnings:{warning_count}, "
                    f"unresolved:{unresolved_count}"
                )
        evidence_count = int(item.get("evidence_count") or 0)
        details.append(f"evidence_count={evidence_count}")
        lines.append(f"   {'; '.join(details)}")

    if next_cursor is not None:
        lines.append(f"Next cursor: {next_cursor}")

    validation = page.get("validation")
    if isinstance(validation, Mapping) and int(validation.get("total") or 0):
        lines.append(
            "Validation findings: "
            f"total={int(validation.get('total') or 0)}, "
            f"errors={int(validation.get('error_count') or 0)}, "
            f"warnings={int(validation.get('warning_count') or 0)}, "
            f"unresolved={int(validation.get('unresolved_count') or 0)}."
        )

    next_actions = page.get("next_actions")
    if isinstance(next_actions, list) and next_actions:
        lines.append("Available actions:")
        for action in next_actions:
            if isinstance(action, str) and action.strip():
                lines.append(f"- {action.strip()}")

    return "\n".join(lines)


def build_and_render_extraction_manifest(
    payload: Mapping[str, Any],
    *,
    extraction_result_id: str | None = None,
    result_ref: str | None = None,
    adapter_key: str | None = None,
    agent_key: str | None = None,
    cursor: str | None = None,
    limit: int | None = None,
) -> str:
    """Build and render a supervisor manifest from a canonical envelope."""

    page = build_extraction_manifest_page(
        payload,
        extraction_result_id=extraction_result_id,
        result_ref=result_ref,
        adapter_key=adapter_key,
        agent_key=agent_key,
        cursor=cursor,
        limit=limit,
    )
    return render_extraction_manifest_page(page)


def _canonical_envelope(payload: Mapping[str, Any]) -> DomainEnvelope:
    try:
        return DomainEnvelope.model_validate(dict(payload))
    except ValidationError as exc:
        raise ExtractionManifestError(
            "Supervisor manifests require a strict canonical domain envelope"
        ) from exc


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
    raise ExtractionManifestError(
        f"Supervisor manifest cannot resolve domain pack {domain_pack_id!r}"
    )


def _include_object_in_manifest(
    domain_object: CuratableObjectEnvelope,
    object_definitions: Mapping[str, DomainPackObjectDefinition],
) -> bool:
    object_definition = object_definitions.get(domain_object.object_type)
    if object_definition is None:
        return False
    role = _object_role(domain_object)
    if role == "curatable_unit":
        return True
    return is_default_supervisor_manifest_object(object_definition)


def _manifest_object(
    domain_object: CuratableObjectEnvelope,
    *,
    metadata: DomainPackMetadata,
    validation_findings: list[ValidationFinding],
) -> dict[str, Any]:
    policy = supervisor_manifest_policy_for_object(
        metadata,
        domain_object.object_type,
    )
    object_ref = _object_ref(domain_object)
    scoped_findings = [
        finding
        for finding in validation_findings
        if _finding_targets_object(finding, domain_object)
    ]
    return {
        "object_ref": object_ref,
        "object_type": domain_object.object_type,
        "status": domain_object.status.value,
        "display_label": _first_policy_value(
            domain_object.payload,
            policy.primary_label_fields,
            default=object_ref,
            limit=get_supervisor_text_preview_limit(),
        ),
        "secondary_label": (
            _policy_value(
                domain_object.payload,
                policy.secondary_label_field,
                limit=get_supervisor_text_preview_limit(),
            )
            if policy.secondary_label_field is not None
            else None
        ),
        "fields": _policy_fields(domain_object.payload, policy),
        "validation": _validation_counts(scoped_findings),
        "evidence_count": len(domain_object.evidence_record_ids),
    }


def _policy_fields(
    payload: Mapping[str, Any],
    policy: SupervisorManifestPolicy,
) -> list[dict[str, Any]]:
    fields: list[dict[str, Any]] = []
    for field in policy.summary_fields:
        value = _policy_value(
            payload,
            field,
            limit=get_supervisor_field_text_limit(),
        )
        if value is None:
            continue
        fields.append({"path": field.path, "label": field.label, "value": value})
    return fields


def _first_policy_value(
    payload: Mapping[str, Any],
    fields: tuple[SupervisorManifestField, ...],
    *,
    default: str,
    limit: int,
) -> str:
    for field in fields:
        value = _policy_value(payload, field, limit=limit)
        if value:
            return value
    return _truncate(default, limit=limit)


def _policy_value(
    payload: Mapping[str, Any],
    field: SupervisorManifestField | None,
    *,
    limit: int,
) -> str | None:
    if field is None:
        return None
    value = _payload_value(payload, field.path)
    if not _is_scalar(value):
        return None
    return _truncate(str(value).strip(), limit=limit)


def _payload_value(payload: Mapping[str, Any], field_path: str) -> Any:
    current: Any = payload
    for part in _parse_path(field_path):
        if isinstance(part, str):
            if not isinstance(current, Mapping) or part not in current:
                return None
            current = current[part]
            continue
        if not isinstance(current, list) or part >= len(current):
            return None
        current = current[part]
    return current


def _parse_path(field_path: str) -> tuple[str | int, ...]:
    # Field syntax is already domain-pack validated; this is only the runtime reader.
    parts: list[str | int] = []
    for segment in field_path.split("."):
        remaining = segment
        if "[" not in remaining:
            parts.append(remaining)
            continue
        key, rest = remaining.split("[", 1)
        parts.append(key)
        while rest:
            index_text, _, rest = rest.partition("]")
            parts.append(int(index_text))
            if rest.startswith("["):
                rest = rest[1:]
            else:
                rest = rest.lstrip(".")
    return tuple(parts)


def _validation_counts(findings: list[ValidationFinding]) -> dict[str, int]:
    error_count = 0
    warning_count = 0
    unresolved_count = 0
    for finding in findings:
        if finding.severity in {
            ValidationFindingSeverity.ERROR,
            ValidationFindingSeverity.BLOCKER,
        }:
            error_count += 1
        elif finding.severity is ValidationFindingSeverity.WARNING:
            warning_count += 1
        if finding.status is ValidationFindingStatus.OPEN:
            unresolved_count += 1
    return {
        "total": len(findings),
        "error_count": error_count,
        "warning_count": warning_count,
        "unresolved_count": unresolved_count,
    }


def _finding_targets_object(
    finding: ValidationFinding,
    domain_object: CuratableObjectEnvelope,
) -> bool:
    if finding.object_ref is not None:
        return finding.object_ref.ref_key() in domain_object.ref_keys()
    if finding.field_ref is not None:
        return finding.field_ref.object_ref.ref_key() in domain_object.ref_keys()
    return False


def _next_actions(
    *,
    result_ref: str | None,
    has_objects: bool,
    next_cursor: str | None,
) -> list[str]:
    if not has_objects:
        return [
            "Report that the extractor returned zero retained objects.",
            "Ask for clarification or make one better-scoped retry only when the missing scope is clear.",
        ]

    actions = [
        "Answer from this manifest when it satisfies the user's request. A non-empty extractor result is usually enough to answer; do not rerun an extractor merely to gain confidence.",
    ]
    if result_ref:
        if next_cursor is not None:
            actions.append(
                f'Use inspect_results(result_ref="{result_ref}", action="objects", cursor="{next_cursor}") for more objects.'
            )
        else:
            actions.append(
                f'Use inspect_results(result_ref="{result_ref}", action="objects") for the manifest objects.'
            )
        actions.append(
            f'Use inspect_results(result_ref="{result_ref}", action="evidence", object_ref="<object_ref>") for evidence.'
        )
        actions.append(
            "Use export_to_file(format_type=..., data=..., filename_hint=...) "
            "only if the user asks for a file/export; inspect this result first "
            "to choose the bounded rows or fields to export."
        )
    actions.append('Ask "Ready to prepare these for curation?" before prepare_for_curation.')
    return actions


def _normalize_limit(limit: int | None) -> int:
    if limit is None:
        return get_supervisor_manifest_page_size()
    return max(1, int(limit))


def _cursor_offset(cursor: str | None) -> int:
    if cursor is None or str(cursor).strip() == "":
        return 0
    try:
        return max(0, int(str(cursor).strip()))
    except ValueError as exc:
        raise ExtractionManifestError(f"Invalid manifest cursor {cursor!r}") from exc


def _result_ref(extraction_result_id: str | None) -> str | None:
    if not extraction_result_id:
        return None
    if extraction_result_id.startswith("extraction-result:"):
        return extraction_result_id
    return f"extraction-result:{extraction_result_id}"


def _object_ref(domain_object: CuratableObjectEnvelope) -> str:
    if domain_object.object_id:
        return domain_object.object_id
    if domain_object.pending_ref_id:
        return domain_object.pending_ref_id
    return domain_object.object_type


def _object_role(domain_object: CuratableObjectEnvelope) -> str:
    if domain_object.object_role:
        return domain_object.object_role
    value = domain_object.metadata.get("object_role")
    return value.strip() if isinstance(value, str) else ""


def _is_scalar(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return isinstance(value, (int, float, bool))


def _truncate(value: str, *, limit: int) -> str:
    text = " ".join(value.split())
    if len(text) <= limit:
        return text
    return f"{text[: max(1, limit - 3)].rstrip()}..."


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


__all__ = [
    "ExtractionManifestError",
    "build_and_render_extraction_manifest",
    "build_extraction_manifest_object",
    "build_extraction_manifest_page",
    "render_extraction_manifest_page",
]
