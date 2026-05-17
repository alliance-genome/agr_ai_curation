"""Shared helpers for appending domain-envelope validation findings."""

from __future__ import annotations

import json
from hashlib import sha256
from typing import Any, Iterable, Mapping

from src.schemas.domain_envelope import (
    DomainEnvelope,
    FieldRef,
    HistoryActorType,
    HistoryEvent,
    HistoryEventKind,
    ValidationFinding,
    field_path_exists,
)


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

    return (
        envelope.model_copy(
            update={
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
        details["validation_details"] = finding.details
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
        object_ref=event_object_ref,
        field_ref=event_field_ref,
        details=details,
    )


def _finding_field_ref_exists(
    *,
    envelope: DomainEnvelope,
    field_ref: FieldRef,
) -> bool:
    ref_key = field_ref.object_ref.ref_key()
    for domain_object in envelope.objects:
        if ref_key in domain_object.ref_keys():
            return field_path_exists(domain_object.payload, field_ref.field_path)
    return False


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


__all__ = ["append_validation_findings_to_envelope"]
