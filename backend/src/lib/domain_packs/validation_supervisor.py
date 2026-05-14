"""Domain-pack validation supervisor.

The supervisor consumes provider-owned domain-pack metadata and writes validation
findings back into the domain envelope.  It intentionally keeps provider-specific,
DB, and schema-specific behavior behind domain-pack bindings instead of hard-coding
it in core runtime code.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Iterable, Mapping

from src.schemas.domain_envelope import (
    CuratableObjectEnvelope,
    DomainEnvelope,
    FieldRef,
    HistoryActorType,
    HistoryEvent,
    HistoryEventKind,
    ObjectRef,
    ValidationFinding,
    ValidationFindingSeverity,
    field_path_exists,
)
from src.lib.lookup_status import LOOKUP_STATUS_BLOCKED, LOOKUP_STATUS_UNDER_DEVELOPMENT

from .input_selectors import build_domain_validation_request
from .registry import LoadedDomainPack
from .validation_registry import (
    DomainPackValidationRegistry,
    ValidationBindingState,
    ValidatorBindingMatch,
    ValidatorMetadataEntry,
)


@dataclass(frozen=True)
class ValidationSupervisorResult:
    """Result of one supervisor pass over a domain envelope."""

    envelope: DomainEnvelope
    registry: DomainPackValidationRegistry
    matched_bindings: tuple[ValidatorBindingMatch, ...]
    appended_findings: tuple[ValidationFinding, ...]


def run_validation_supervisor(
    envelope: DomainEnvelope,
    domain_pack: LoadedDomainPack,
    *,
    actor_id: str = "domain_validation_supervisor",
    provider_model_ref: Mapping[str, Any] | None = None,
    registry: DomainPackValidationRegistry | None = None,
    include_active_binding_findings: bool = True,
) -> ValidationSupervisorResult:
    """Run metadata-driven validation and return an updated envelope.

    Under-development bindings are surfaced as visible metadata only. Active
    agent-backed bindings emit explicit dispatch-unavailable findings until the
    package-scoped validator dispatcher owns execution.
    """

    validation_registry = registry or DomainPackValidationRegistry.from_domain_pack(
        domain_pack
    )
    all_matches = validation_registry.match_bindings(envelope)
    new_findings: list[ValidationFinding] = []

    new_findings.extend(
        _metadata_state_findings(
            envelope=envelope,
            entries=validation_registry.validator_metadata,
            provider_model_ref=provider_model_ref,
        )
    )
    new_findings.extend(
        _required_field_findings(
            envelope=envelope,
            registry=validation_registry,
            provider_model_ref=provider_model_ref,
        )
    )

    state_matches = {
        state: tuple(match for match in all_matches if match.binding.state is state)
        for state in ValidationBindingState
    }
    if include_active_binding_findings:
        new_findings.extend(
            _active_binding_findings(
                matches=state_matches[ValidationBindingState.ACTIVE],
                provider_model_ref=provider_model_ref,
            )
        )

    updated_envelope, appended_findings = append_validation_findings_to_envelope(
        envelope,
        new_findings,
        actor_id=actor_id,
    )
    return ValidationSupervisorResult(
        envelope=updated_envelope,
        registry=validation_registry,
        matched_bindings=all_matches,
        appended_findings=appended_findings,
    )


def append_validation_findings_to_envelope(
    envelope: DomainEnvelope,
    findings: Iterable[ValidationFinding],
    *,
    actor_id: str = "domain_validation_supervisor",
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


def _metadata_state_findings(
    *,
    envelope: DomainEnvelope,
    entries: Iterable[ValidatorMetadataEntry],
    provider_model_ref: Mapping[str, Any] | None,
) -> list[ValidationFinding]:
    findings: list[ValidationFinding] = []
    for entry in entries:
        if entry.state is ValidationBindingState.ACTIVE:
            continue
        details = {
            "validation_metadata": _with_provider_model_ref(
                entry.identity_details(),
                provider_model_ref,
            )
        }
        severity = (
            ValidationFindingSeverity.INFO
            if entry.state is ValidationBindingState.PLANNED
            else ValidationFindingSeverity.BLOCKER
        )
        findings.append(
            ValidationFinding(
                severity=severity,
                code=f"domain_pack.validator_{entry.state.value}",
                message=_state_message(
                    state=entry.state,
                    identifier=entry.validator_id,
                    reason=entry.reason or entry.description,
                    blocked_by=entry.blocked_by,
                ),
                details=details,
            )
        )
    return findings


def _required_field_findings(
    *,
    envelope: DomainEnvelope,
    registry: DomainPackValidationRegistry,
    provider_model_ref: Mapping[str, Any] | None,
) -> list[ValidationFinding]:
    object_definitions = registry.object_definitions_by_type
    findings: list[ValidationFinding] = []
    for domain_object in envelope.objects:
        object_definition = object_definitions.get(domain_object.object_type)
        if object_definition is None:
            continue
        for field_definition in object_definition.fields:
            if not field_definition.required:
                continue
            if field_path_exists(domain_object.payload, field_definition.field_path):
                continue
            policy = registry.policy_for(
                domain_object.object_type,
                field_definition.field_path,
            )
            field_policy_details = (
                policy.identity_details()
                if policy is not None
                else _field_definition_details(
                    envelope=envelope,
                    domain_object=domain_object,
                    field_path=field_definition.field_path,
                    field_type=field_definition.field_type.value,
                )
            )
            findings.append(
                ValidationFinding(
                    severity=(
                        ValidationFindingSeverity.BLOCKER
                        if policy is not None and policy.blocking
                        else ValidationFindingSeverity.ERROR
                    ),
                    code="domain_pack.required_field_missing",
                    message=(
                        f"{domain_object.object_type}.{field_definition.field_path} "
                        "is required by the domain pack but missing from the envelope payload."
                    ),
                    field_ref=FieldRef(
                        object_ref=domain_object.to_object_ref(),
                        field_path=field_definition.field_path,
                    ),
                    details={
                        "validation_metadata": _with_provider_model_ref(
                            {
                                "validator_id": "domain_pack.required_field_policy",
                                "binding_state": ValidationBindingState.ACTIVE.value,
                                "metadata_source": (
                                    "field_policy"
                                    if policy is not None
                                    else "field_definition"
                                ),
                                "field_policy": field_policy_details,
                            },
                            provider_model_ref,
                        )
                    },
                )
            )
    return findings


def _active_binding_findings(
    *,
    matches: Iterable[ValidatorBindingMatch],
    provider_model_ref: Mapping[str, Any] | None,
) -> list[ValidationFinding]:
    findings: list[ValidationFinding] = []
    matches_by_binding = _matches_by_binding(matches)
    for binding_id in sorted(matches_by_binding):
        findings.extend(
            _unsupported_active_binding_findings(
                matches=matches_by_binding[binding_id],
                provider_model_ref=provider_model_ref,
            )
        )
    return findings


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _unsupported_active_binding_findings(
    *,
    matches: Iterable[ValidatorBindingMatch],
    provider_model_ref: Mapping[str, Any] | None,
) -> list[ValidationFinding]:
    findings: list[ValidationFinding] = []
    for match in matches:
        selector_result = build_domain_validation_request(match)
        if selector_result.findings:
            findings.extend(selector_result.findings)
            continue
        findings.append(
            _dispatch_unavailable_finding(
                match=match,
                provider_model_ref=provider_model_ref,
                reason="no executable validator is registered for this active binding",
                validation_request=(
                    selector_result.request.model_dump(mode="json", exclude_none=True)
                    if selector_result.request is not None
                    else None
                ),
            )
        )
    return findings


def _dispatch_unavailable_finding(
    *,
    match: ValidatorBindingMatch,
    provider_model_ref: Mapping[str, Any] | None,
    reason: str,
    validation_request: Mapping[str, Any] | None = None,
) -> ValidationFinding:
    binding = match.binding
    details = {
        **_match_details(match, provider_model_ref=provider_model_ref),
        "dispatch_unavailable_reason": reason,
    }
    if validation_request is not None:
        details["validation_request"] = dict(validation_request)
    lookup_status = (
        LOOKUP_STATUS_BLOCKED if binding.blocking else LOOKUP_STATUS_UNDER_DEVELOPMENT
    )
    message = (
        f"Active validator binding '{binding.binding_id}' could not be dispatched: "
        f"{reason}."
    )
    _attach_lookup_attempt_details(
        details,
        match=match,
        lookup_status=lookup_status,
        explanation=message,
        validation_request=validation_request,
    )
    return ValidationFinding(
        severity=(
            ValidationFindingSeverity.BLOCKER
            if binding.blocking
            else ValidationFindingSeverity.WARNING
        ),
        code="domain_pack.validator_dispatch_unavailable",
        message=message,
        object_ref=_match_object_ref(match),
        field_ref=_match_field_ref(match),
        details=details,
    )


def _matches_by_binding(
    matches: Iterable[ValidatorBindingMatch],
) -> dict[str, tuple[ValidatorBindingMatch, ...]]:
    grouped: dict[str, list[ValidatorBindingMatch]] = {}
    for match in matches:
        grouped.setdefault(match.binding.binding_id, []).append(match)
    return {
        binding_id: tuple(binding_matches)
        for binding_id, binding_matches in grouped.items()
    }


def _match_details(
    match: ValidatorBindingMatch,
    *,
    provider_model_ref: Mapping[str, Any] | None,
) -> dict[str, Any]:
    return {
        "validation_metadata": _with_provider_model_ref(
            {
                **match.binding.identity_details(),
                "target": match.target_details(),
            },
            provider_model_ref,
        )
    }


def _attach_lookup_attempt_details(
    details: dict[str, Any],
    *,
    match: ValidatorBindingMatch,
    lookup_status: str,
    explanation: str,
    validation_request: Mapping[str, Any] | None = None,
    selected_input_details: Mapping[str, Any] | None = None,
) -> None:
    attempt = _lookup_attempt_for_match(
        match=match,
        lookup_status=lookup_status,
        explanation=explanation,
        validation_request=validation_request,
        selected_input_details=selected_input_details,
    )
    details["lookup_attempts"] = [attempt]
    details["lookup_explanation"] = explanation
    details["failure_classification"] = lookup_status


def _lookup_attempt_for_match(
    *,
    match: ValidatorBindingMatch,
    lookup_status: str,
    explanation: str,
    validation_request: Mapping[str, Any] | None = None,
    selected_input_details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    binding = match.binding
    attempted_query = {
        "validator_binding_id": binding.binding_id,
    }
    if validation_request is not None:
        attempted_query["request_id"] = validation_request.get("request_id")
        attempted_query["input_fields"] = validation_request.get("selected_inputs", {})
    elif selected_input_details:
        attempted_query["input_fields"] = dict(selected_input_details)
    return {
        "source": {
            "validator_binding_id": binding.binding_id,
            "validator_agent": (
                binding.validator_agent.to_dict()
                if binding.validator_agent is not None
                else None
            ),
        },
        "attempted_query": {
            key: value for key, value in attempted_query.items() if value is not None
        },
        "lookup_status": lookup_status,
        "candidate_count": 0,
        "resolved_id": None,
        "resolved_label": None,
        "explanation": explanation,
    }


def _with_provider_model_ref(
    details: dict[str, Any],
    provider_model_ref: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if provider_model_ref:
        details["provider_model_ref"] = dict(provider_model_ref)
    return details


def _match_object_ref(match: ValidatorBindingMatch) -> ObjectRef | None:
    if match.object_envelope is None or match.field_definition is not None:
        return None
    return match.object_envelope.to_object_ref()


def _match_field_ref(match: ValidatorBindingMatch) -> FieldRef | None:
    if match.object_envelope is None or match.field_definition is None:
        return None
    return FieldRef(
        object_ref=match.object_envelope.to_object_ref(),
        field_path=match.field_definition.field_path,
    )


def _field_definition_details(
    *,
    envelope: DomainEnvelope,
    domain_object: CuratableObjectEnvelope,
    field_path: str,
    field_type: str,
) -> dict[str, Any]:
    return {
        "domain_pack_id": envelope.domain_pack_id,
        "object_type": domain_object.object_type,
        "field_path": field_path,
        "field_type": field_type,
        "policy_source": "field_definition",
        "required": True,
        "blocking": False,
    }


def _state_message(
    *,
    state: ValidationBindingState,
    identifier: str,
    reason: str | None,
    blocked_by: str | None,
) -> str:
    if state is ValidationBindingState.PLANNED:
        return (
            f"Validator '{identifier}' is planned in domain-pack metadata and was "
            "not executed."
        )
    if state is ValidationBindingState.UNDER_DEVELOPMENT:
        if reason:
            return (
                f"Validator '{identifier}' is under development in domain-pack "
                f"metadata and was not executed: {reason}."
            )
        return (
            f"Validator '{identifier}' is under development in domain-pack metadata "
            "and was not executed."
        )
    if blocked_by:
        return (
            f"Validator '{identifier}' is blocked by {blocked_by} and was not executed."
        )
    if reason:
        return f"Validator '{identifier}' is blocked and was not executed: {reason}."
    return f"Validator '{identifier}' is blocked and was not executed."


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


__all__ = [
    "ValidationSupervisorResult",
    "append_validation_findings_to_envelope",
    "run_validation_supervisor",
]
