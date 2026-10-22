"""Supervisor-facing manifests for canonical extraction results."""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any, Mapping

from pydantic import ValidationError

from src.lib.domain_packs.resolvable_values import (
    ResolvableSpec,
    declared_resolvable_fields,
    unresolved_header_text,
)
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
from src.lib.openai_agents.tool_result_bounds import content_sha256


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
    manifest_objects = supervisor_manifest_objects(envelope, metadata)

    page_limit = _normalize_limit(limit)
    offset = _cursor_offset(cursor)
    page_objects = manifest_objects[offset : offset + page_limit]
    next_offset = offset + len(page_objects)
    next_cursor = (
        str(next_offset) if next_offset < len(manifest_objects) else None
    )
    manifest_result_ref = result_ref or _result_ref(extraction_result_id)

    page = {
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
        "specialist_explanation": envelope.specialist_explanation,
        "object_count": len(manifest_objects),
        "page": {
            "cursor": cursor,
            "limit": page_limit,
            "next_cursor": next_cursor,
            "offset": offset,
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
        "validator_results": _validator_results_summary(
            envelope.validation_findings, envelope.validator_results, result_ref=manifest_result_ref,
        ),
        "next_actions": _next_actions(
            result_ref=manifest_result_ref,
            has_objects=bool(manifest_objects),
            next_cursor=next_cursor,
        ),
    }
    page["page"]["returned"] = len(page["objects"])
    return page


def bind_extraction_manifest_reference(page: Mapping[str, Any], extraction_result_id: str) -> dict[str, Any]:
    """Bind a prevalidated manifest to its actual durable result, without reloading configuration."""
    bound = deepcopy(dict(page))
    ref = _result_ref(extraction_result_id)
    bound["result_ref"] = ref
    bound["extraction_result_id"] = extraction_result_id
    bound["validator_results"]["inspect"]["result_ref"] = ref
    for entry in bound["validator_results"]["results"]:
        entry["read"]["result_ref"] = ref
    bound["next_actions"] = _next_actions(
        result_ref=ref, has_objects=bool(bound["object_count"]),
        next_cursor=bound["page"]["next_cursor"],
    )
    return bound


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

    for domain_object in envelope.extracted_objects:
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


def supervisor_manifest_objects(
    envelope: DomainEnvelope,
    metadata: DomainPackMetadata,
) -> list[CuratableObjectEnvelope]:
    """Objects a supervisor manifest lists, in envelope order."""

    object_definitions = {
        definition.object_type: definition
        for definition in metadata.object_definitions
    }
    return [
        domain_object
        for domain_object in envelope.extracted_objects
        if _include_object_in_manifest(domain_object, object_definitions)
    ]


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
    page_offset = int(page_info.get("offset") or 0)
    page_returned = int(page_info.get("returned") or 0)
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
        f"Object preview: {page_returned} returned from offset {page_offset}; row ceiling={page_limit}",
    ]

    explanation = page.get("specialist_explanation")
    if isinstance(explanation, str) and explanation:
        lines.extend([
            "Specialist explanation (complete authored text; saved objects and "
            "validation findings determine persisted state):",
            explanation,
            "End specialist explanation.",
        ])

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
            warning_count = int(validation.get("open_warning_count") or 0)
            error_count = int(validation.get("open_error_count") or 0)
            unresolved_count = int(validation.get("unresolved_count") or 0)
            if warning_count or error_count or unresolved_count:
                details.append(
                    "open_findings="
                    f"errors:{error_count}, warnings:{warning_count}, "
                    f"total:{unresolved_count}"
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
            f"open_errors={int(validation.get('open_error_count') or 0)}, "
            f"open_warnings={int(validation.get('open_warning_count') or 0)}, "
            f"statuses={json.dumps(validation.get('status_counts') or {}, sort_keys=True)}."
        )

    results = page.get("validator_results")
    if isinstance(results, Mapping) and results.get("total"):
        lines.append(
            "Automatic validator results (target-level, not submission readiness): "
            + json.dumps(results, ensure_ascii=False, separators=(",", ":"))
        )
        lines.append(
            "These are validator decisions, not proof of accepted writeback. A writeback_rejected "
            "result must not be described as an accepted identity. Preserve open findings. "
            "Use these automatic results when describing validation, even if "
            "extractor instructions said not to resolve identities. A resolved target does "
            "not establish annotation or export readiness. Missing or omitted result details "
            "mean unknown, not that no identifiers were resolved. Inspect the saved result "
            "for omitted details before making a claim about them."
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
    resolvable_fields = declared_resolvable_fields(metadata, domain_object.object_type)
    scoped_findings = [
        finding
        for finding in validation_findings
        if _finding_targets_object(finding, domain_object)
    ]
    return {
        "object_ref": object_ref,
        "object_type": domain_object.object_type,
        "status": domain_object.status.value,
        "display_label": _policy_label(
            domain_object,
            policy.primary_label_field,
            resolvable_fields,
            limit=get_supervisor_text_preview_limit(),
        ) or _truncate(object_ref, limit=get_supervisor_text_preview_limit()),
        "secondary_label": _policy_label(
            domain_object,
            policy.secondary_label_field,
            resolvable_fields,
            limit=get_supervisor_text_preview_limit(),
        ),
        "fields": _policy_fields(domain_object, policy, resolvable_fields),
        "validation": _validation_counts(scoped_findings),
        "evidence_count": len(domain_object.evidence_record_ids),
    }


def _policy_fields(
    domain_object: CuratableObjectEnvelope,
    policy: SupervisorManifestPolicy,
    resolvable_fields: Mapping[str, ResolvableSpec],
) -> list[dict[str, Any]]:
    fields: list[dict[str, Any]] = []
    for field in policy.summary_fields:
        value = _policy_label(
            domain_object,
            field,
            resolvable_fields,
            limit=get_supervisor_field_text_limit(),
        )
        if value is None:
            continue
        fields.append({"path": field.path, "label": field.label, "value": value})
    return fields


def _policy_label(
    domain_object: CuratableObjectEnvelope,
    field: SupervisorManifestField | None,
    resolvable_fields: Mapping[str, ResolvableSpec],
    *,
    limit: int,
) -> str | None:
    """One declared label field; an unresolved value reads as its paper wording (ALL-1283)."""

    if field is None:
        return None
    paper_wording = unresolved_header_text(
        domain_object.payload,
        field.path,
        object_metadata=domain_object.metadata,
        resolvable_fields=resolvable_fields,
    )
    if paper_wording is not None:
        return _truncate(paper_wording, limit=limit)
    return _policy_value(domain_object.payload, field, limit=limit) or None


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


def validator_result_entries(
    findings: list[ValidationFinding],
    originals: Mapping[str, dict[str, Any]] | None = None,
) -> list[tuple[str, dict[str, Any]]]:
    """Complete canonical validator decisions keyed by request (or finding index).

    Includes targets hidden by object policy. Entries are never shortened;
    callers decide how to present large ones.
    """
    decisions: dict[str, dict[str, Any]] = {}
    originals = originals or {}
    for ref, snapshot in sorted(originals.items(), key=lambda item: item[1]["recorded_order"]):
        result = snapshot["result"]
        decisions[ref] = {
            name: result[name] for name in (
                "status", "request_id", "validator_binding_id", "target", "resolved_values",
                "missing_expected_fields",
            ) if name in result
        }
        decisions[ref].update({
            "original_available": snapshot.get("original_available", True),
            "review_status": snapshot.get("review_status", "historical_unknown"),
            "history_ref": snapshot.get("history_ref"),
            "original_ref": ref,
            "source_envelope_revision": snapshot.get("source_envelope_revision"),
            "output_complete": snapshot.get("output_complete", not bool(result.get("output_issues"))),
            "follow_up_question_count": snapshot.get("follow_up_question_count", len(result.get("unresolved_questions") or [])),
        })
        if isinstance(result.get("target"), Mapping):
            decisions[ref]["target"] = {name: result["target"][name] for name in (
                "object_type", "object_id", "pending_ref_id", "field_path",
            ) if result["target"].get(name) is not None}
    for index, finding in enumerate(findings):
        details = finding.details or {}
        original_ref = details.get("validation_result_ref")
        snapshot = originals.get(original_ref) if isinstance(original_ref, str) else None
        result = snapshot["result"] if snapshot is not None else details.get("validation_result")
        if not isinstance(result, Mapping) or not isinstance(result.get("status"), str):
            continue
        request_id = result.get("request_id")
        key = original_ref if isinstance(original_ref, str) and original_ref in originals else ((f"request:{request_id}" if len(str(request_id)) < get_supervisor_field_text_limit() - len("request:") else f"request-sha256:{content_sha256(request_id)}") if request_id else f"finding:{index}")
        entry = decisions.setdefault(key, {
            name: result[name]
            for name in ("status", "request_id", "validator_binding_id", "resolved_values", "missing_expected_fields")
            if name in result
        })
        entry.setdefault("follow_up_question_count", len(result.get("unresolved_questions") or []))
        entry.setdefault("original_available", False)
        entry.setdefault("output_complete", None)  # Unknown for historical projections.
        target = result.get("target")
        if isinstance(target, Mapping):
            entry["target"] = {
                name: target[name]
                for name in ("object_type", "object_id", "pending_ref_id", "field_path")
                if target.get(name) is not None
            }
        # Per-field copies can include later rejection: never let the first result
        # hide a failed writeback or an associated open finding.
        if finding.status is ValidationFindingStatus.OPEN:
            entry["open_finding"] = True
            entry["open_finding_count"] = entry.get("open_finding_count", 0) + 1
            if finding.severity in {ValidationFindingSeverity.BLOCKER, ValidationFindingSeverity.ERROR}:
                entry["open_error_count"] = entry.get("open_error_count", 0) + 1
        if (details.get("materialization") == "rejected"
                or details.get("failure_classification") == "invalid_materialization_input"
                or finding.code == "domain_pack.validator_materialization_invalid"):
            entry["writeback_rejected"] = True
    # This is attempt recency, never supervisor acceptance.
    # Source revision and append order distinguish revalidation from per-field
    # copies. Historical records remain inspectable, never counted as current
    # solely because they used the same target in an earlier round.
    latest: dict[str, str] = {}
    for key, entry in decisions.items():
        target_key = json.dumps([entry.get("validator_binding_id"), entry.get("target")], sort_keys=True) if entry.get("validator_binding_id") and entry.get("target") else key
        entry["_target_key"] = target_key
        previous = decisions.get(latest.get(target_key, ""), {})
        if (entry.get("source_envelope_revision") or 0) >= (previous.get("source_envelope_revision") or 0):
            latest[target_key] = key
    for key, entry in decisions.items():
        entry["is_latest_attempt"] = latest[entry.pop("_target_key")] == key
        entry["outstanding"] = bool(entry.get("open_finding") or (
            entry["is_latest_attempt"] and (entry.get("writeback_rejected") or entry.get("status") != "resolved" or entry.get("output_complete") is False)
        ))
    return sorted(decisions.items(), key=lambda item: (
        not bool(item[1]["outstanding"] and item[1].get("writeback_rejected")),
        not bool(item[1].get("open_error_count")),
        not item[1]["outstanding"],
        not item[1]["is_latest_attempt"],
    ))


def _validator_results_summary(
    findings: list[ValidationFinding], originals: Mapping[str, dict[str, Any]] | None = None,
    *, result_ref: str | None = None,
) -> dict[str, Any]:
    """Expose canonical validator decisions, including targets hidden by object policy."""
    decisions = dict(validator_result_entries(findings, originals))
    counts: dict[str, int] = {}
    latest_attempt_counts: dict[str, int] = {}
    entries: list[dict[str, Any]] = []
    for key, original_entry in decisions.items():
        entry = dict(original_entry)
        status = entry["status"]
        counts[status] = counts.get(status, 0) + 1
        if entry["is_latest_attempt"]:
            latest_attempt_counts[status] = latest_attempt_counts.get(status, 0) + 1
        if len(entries) >= get_supervisor_manifest_page_size():
            continue
        entry["validator_result_key"] = key
        entry["read"] = {"action": "validator_results", "result_ref": result_ref, "validator_result_key": key}
        # Keep stable complete identities; large values remain exact saved reads.
        if len(json.dumps({name: entry.get(name) for name in ("resolved_values", "missing_expected_fields")}, ensure_ascii=False)) > get_supervisor_field_text_limit():
            entry = {key: value for key, value in entry.items()
                     if key not in {"resolved_values", "missing_expected_fields"}}
            entry["details_omitted"] = True
        entries.append(entry)
    summary = {
        "total": len(decisions),
        "unit": "validator_decisions",
        "status_counts": counts,
        "latest_attempt_status_counts": latest_attempt_counts,
        "historical_total": sum(not entry["is_latest_attempt"] for entry in decisions.values()),
        "incomplete_total": sum(entry.get("output_complete") is False for entry in decisions.values()),
        "original_unavailable_total": sum(not entry["original_available"] for entry in decisions.values()),
        "outstanding_total": sum(entry["outstanding"] for entry in decisions.values()),
        "ordering": "outstanding_writeback_rejected,open_errors,outstanding,current,stored_order",
        "results": entries,
        "inspect": {"action": "validator_results", "result_ref": result_ref},
        "snapshot_sha256": content_sha256(decisions),
    }
    _update_decision_coverage(summary)
    return summary


def _update_decision_coverage(summary: dict[str, Any]) -> None:
    summary["returned"] = len(summary["results"])
    summary["outstanding_returned"] = sum(entry["outstanding"] for entry in summary["results"])
    summary["outstanding_remaining"] = summary["outstanding_total"] - summary["outstanding_returned"]
    summary["details_complete"] = summary["total"] == summary["returned"] and not any(
        entry.get("details_omitted") for entry in summary["results"]
    )


def _validation_counts(findings: list[ValidationFinding]) -> dict[str, Any]:
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
        "unit": "validation_findings",
        "status_counts": {status.value: sum(f.status is status for f in findings) for status in ValidationFindingStatus},
        "open_error_count": sum(f.status is ValidationFindingStatus.OPEN and f.severity in {ValidationFindingSeverity.ERROR, ValidationFindingSeverity.BLOCKER} for f in findings),
        "open_warning_count": sum(f.status is ValidationFindingStatus.OPEN and f.severity is ValidationFindingSeverity.WARNING for f in findings),
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
            "Report that the extractor returned zero retained objects and preserve its explanation. An explained empty result can fully answer the request.",
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
            f'For generic/custom objects, use inspect_results(result_ref="{result_ref}", action="details", object_ref="<object_ref>") to read saved attributes and nested parts. Follow paths and cursors; do not call the extractor again to inspect prior results.'
        )
        actions.append(
            "Use the matching CSV/TSV/JSON formatter specialist only if the user "
            "asks for a file/export; the formatter is bound to saved extraction "
            "results and must build the file through source-backed projection tools."
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
    "supervisor_manifest_objects",
    "validator_result_entries",
]
