"""Shared helpers for appending domain-envelope validation findings."""

from __future__ import annotations

import json
from hashlib import sha256
from typing import Any, Iterable, Mapping

from src.schemas.domain_envelope import (
    CuratableObjectStatus,
    DomainEnvelope,
    FieldRef,
    HistoryActorType,
    HistoryEvent,
    HistoryEventKind,
    ValidationFinding,
    ValidationFindingStatus,
    field_path_exists,
)


def _history_ref_payload(value: Any) -> Any:
    """Serialize refs before HistoryEvent validation to avoid import-alias drift."""

    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


def append_validation_findings_to_envelope(
    envelope: DomainEnvelope,
    findings: Iterable[ValidationFinding],
    *,
    actor_id: str = "domain_validation_finding_writer",
) -> tuple[DomainEnvelope, tuple[ValidationFinding, ...]]:
    """Append findings and matching history events with stable IDs."""

    existing_findings = list(envelope.validation_findings)
    existing_findings_by_id: dict[str, list[ValidationFinding]] = {}
    for existing_finding in existing_findings:
        if existing_finding.finding_id is None:
            continue
        existing_findings_by_id.setdefault(existing_finding.finding_id, []).append(
            existing_finding
        )
    existing_finding_ids = set(existing_findings_by_id)
    appended_findings: list[ValidationFinding] = []
    history_events = list(envelope.history)

    for raw_finding in findings:
        finding = _with_stable_finding_id(envelope.envelope_id, raw_finding)
        finding_id = finding.finding_id
        if finding_id is None:
            continue
        matching_findings = existing_findings_by_id.get(finding_id, [])
        if any(
            _same_validation_finding_identity(
                envelope_id=envelope.envelope_id,
                existing_finding=existing_finding,
                new_finding=finding,
            )
            for existing_finding in matching_findings
        ):
            continue
        if matching_findings:
            finding = _with_finding_identity_id(envelope.envelope_id, finding)
            finding_id = finding.finding_id
            if finding_id is None or finding_id in existing_finding_ids:
                continue
        existing_finding_ids.add(finding_id)
        existing_findings_by_id.setdefault(finding_id, []).append(finding)
        existing_findings.append(finding)
        appended_findings.append(finding)
        history_events.append(
            _history_event_for_finding(
                envelope=envelope,
                finding=finding,
                actor_id=actor_id,
            )
        )

    # An individual resolved field cannot certify an object with other open
    # findings. This boundary also covers selector failures without dispatch.
    objects = []
    for obj in envelope.extracted_objects:
        has_open_finding = False
        for finding in existing_findings:
            ref = finding.field_ref.object_ref if finding.field_ref else finding.object_ref
            if (finding.status is ValidationFindingStatus.OPEN and ref is not None
                    and ref.object_type == obj.object_type
                    and (ref.object_id is not None or ref.pending_ref_id is not None)
                    and (ref.object_id is None or ref.object_id == obj.object_id)
                    and (ref.pending_ref_id is None or ref.pending_ref_id == obj.pending_ref_id)):
                has_open_finding = True
                break
        if obj.status is CuratableObjectStatus.VALIDATED and has_open_finding:
            obj = obj.model_copy(update={"status": CuratableObjectStatus.NEEDS_REVIEW})
        objects.append(obj)

    return (
        envelope.model_copy(
            update={
                "extracted_objects": objects,
                "validation_findings": existing_findings,
                "history": history_events,
            }
        ),
        tuple(appended_findings),
    )


def _with_stable_finding_id(
    envelope_id: str,
    finding: ValidationFinding,
) -> ValidationFinding:
    if finding.finding_id is not None:
        return finding
    seed_payload = _validation_finding_identity_payload(
        envelope_id=envelope_id,
        finding=finding,
    )
    digest = sha256(
        json.dumps(seed_payload, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return finding.model_copy(update={"finding_id": f"validation:{digest}"})


def _with_finding_identity_id(
    envelope_id: str,
    finding: ValidationFinding,
) -> ValidationFinding:
    seed_payload = {
        "supplied_finding_id": finding.finding_id,
        "identity": _validation_finding_identity_payload(
            envelope_id=envelope_id,
            finding=finding,
        ),
    }
    digest = sha256(
        json.dumps(seed_payload, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return finding.model_copy(
        update={"finding_id": f"{finding.finding_id}:rerun:{digest}"}
    )


def _same_validation_finding_identity(
    *,
    envelope_id: str,
    existing_finding: ValidationFinding,
    new_finding: ValidationFinding,
) -> bool:
    return _validation_finding_identity_payload(
        envelope_id=envelope_id,
        finding=existing_finding,
    ) == _validation_finding_identity_payload(
        envelope_id=envelope_id,
        finding=new_finding,
    )


def _validation_finding_identity_payload(
    *,
    envelope_id: str,
    finding: ValidationFinding,
) -> dict[str, Any]:
    return {
        "envelope_id": envelope_id,
        "code": finding.code,
        "severity": finding.severity.value,
        "message": finding.message,
        "target": _finding_target_payload(finding),
        "details": _normalized_finding_identity_details(finding.details),
    }


def _normalized_finding_identity_details(details: Mapping[str, Any]) -> dict[str, Any]:
    normalized = dict(details)
    validation_request = normalized.get("validation_request")
    if isinstance(validation_request, Mapping):
        normalized["validation_request"] = {
            key: value
            for key, value in validation_request.items()
            if key != "request_id"
        }
    lookup_attempts = normalized.get("lookup_attempts")
    if isinstance(lookup_attempts, list):
        normalized["lookup_attempts"] = [
            _normalized_lookup_attempt_identity(attempt)
            for attempt in lookup_attempts
        ]
    return normalized


def _normalized_lookup_attempt_identity(attempt: Any) -> Any:
    if not isinstance(attempt, Mapping):
        return attempt
    normalized = dict(attempt)
    attempted_query = normalized.get("attempted_query")
    if isinstance(attempted_query, Mapping):
        normalized["attempted_query"] = {
            key: value
            for key, value in attempted_query.items()
            if key != "request_id"
        }
    return normalized


def _history_event_for_finding(
    *,
    envelope: DomainEnvelope,
    finding: ValidationFinding,
    actor_id: str,
) -> HistoryEvent:
    target = _finding_target_payload(finding)
    seed_payload = {
        "envelope_id": envelope.envelope_id,
        "finding_id": finding.finding_id,
        "target": target,
        "event_type": HistoryEventKind.VALIDATION_FINDING_ADDED.value,
    }
    digest = sha256(
        json.dumps(seed_payload, sort_keys=True).encode("utf-8")
    ).hexdigest()
    field_ref = finding.field_ref
    if field_ref is not None and _finding_field_ref_exists(
        envelope=envelope, field_ref=field_ref
    ):
        event_field_ref = field_ref
        event_object_ref = None
    else:
        event_field_ref = None
        event_object_ref = finding.object_ref or (
            finding.field_ref.object_ref if finding.field_ref is not None else None
        )

    details = {
        "finding_id": finding.finding_id,
        "finding_code": finding.code,
        "finding_severity": finding.severity.value,
        "finding_status": finding.status.value,
        "target": target,
    }
    if finding.details:
        details["validation_detail_keys"] = sorted(finding.details.keys())
        for key in (
            "lookup_attempts",
            "lookup_explanation",
            "failure_classification",
            "provider_projections",
        ):
            if key in finding.details:
                details[key] = finding.details[key]

    return HistoryEvent(
        event_type=HistoryEventKind.VALIDATION_FINDING_ADDED,
        event_id=f"validation-event:{digest}",
        actor_type=HistoryActorType.SYSTEM,
        actor_id=actor_id,
        message=f"Validation finding added: {finding.code or finding.severity.value}",
        object_ref=_history_ref_payload(event_object_ref),
        field_ref=_history_ref_payload(event_field_ref),
        details=details,
    )


def _history_event_for_resolved_finding(
    *,
    envelope: DomainEnvelope,
    finding: ValidationFinding,
    actor_id: str,
) -> HistoryEvent:
    target = _finding_target_payload(finding)
    seed_payload = {
        "envelope_id": envelope.envelope_id,
        "finding_id": finding.finding_id,
        "target": target,
        "event_type": HistoryEventKind.STATUS_CHANGED.value,
        "new_status": ValidationFindingStatus.RESOLVED.value,
    }
    digest = sha256(
        json.dumps(seed_payload, sort_keys=True).encode("utf-8")
    ).hexdigest()
    field_ref = finding.field_ref
    if field_ref is not None and _finding_field_ref_exists(
        envelope=envelope,
        field_ref=field_ref,
    ):
        event_field_ref = field_ref
        event_object_ref = None
    else:
        event_field_ref = None
        event_object_ref = finding.object_ref or (
            finding.field_ref.object_ref if finding.field_ref is not None else None
        )
    return HistoryEvent(
        event_type=HistoryEventKind.STATUS_CHANGED,
        event_id=f"validation-resolution-event:{digest}",
        actor_type=HistoryActorType.SYSTEM,
        actor_id=actor_id,
        message=f"Validation finding resolved: {finding.code or finding.severity.value}",
        object_ref=_history_ref_payload(event_object_ref),
        field_ref=_history_ref_payload(event_field_ref),
        details={
            "finding_id": finding.finding_id,
            "finding_code": finding.code,
            "previous_status": ValidationFindingStatus.OPEN.value,
            "new_status": ValidationFindingStatus.RESOLVED.value,
            "target": target,
        },
    )


def _finding_field_ref_exists(
    *,
    envelope: DomainEnvelope,
    field_ref: FieldRef,
) -> bool:
    ref_key = field_ref.object_ref.ref_key()
    for domain_object in envelope.extracted_objects:
        if ref_key in domain_object.ref_keys():
            return field_path_exists(domain_object.payload, field_ref.field_path)
    return False


def _finding_target(
    envelope: DomainEnvelope,
    finding: ValidationFinding,
) -> tuple[str | None, str | None]:
    object_ids = _object_id_by_ref(envelope)
    object_ref = finding.object_ref
    field_path = None
    if finding.field_ref is not None:
        object_ref = finding.field_ref.object_ref
        field_path = finding.field_ref.field_path
    if object_ref is None:
        return None, field_path
    return object_ids.get(object_ref.ref_key()), field_path


def _object_id_by_ref(envelope: DomainEnvelope) -> dict[tuple[str, str], str]:
    object_ids: dict[tuple[str, str], str] = {}
    for domain_object in envelope.extracted_objects:
        stable_id = (
            domain_object.object_id
            if domain_object.object_id is not None
            else domain_object.pending_ref_id
        )
        if stable_id is None:
            continue
        if domain_object.object_id is not None:
            object_ids[("object_id", domain_object.object_id)] = stable_id
        if domain_object.pending_ref_id is not None:
            object_ids[("pending_ref_id", domain_object.pending_ref_id)] = stable_id
    return object_ids


def _finding_target_payload(finding: ValidationFinding) -> dict[str, Any]:
    target: dict[str, Any] = {}
    object_ref = finding.object_ref
    if finding.field_ref is not None:
        object_ref = finding.field_ref.object_ref
        target["field_path"] = finding.field_ref.field_path
    if object_ref is not None:
        if object_ref.object_id is not None:
            target["object_id"] = object_ref.object_id
        if object_ref.pending_ref_id is not None:
            target["pending_ref_id"] = object_ref.pending_ref_id
        if object_ref.object_type is not None:
            target["object_type"] = object_ref.object_type
    return target


__all__ = [
    "append_validation_findings_to_envelope",
]
