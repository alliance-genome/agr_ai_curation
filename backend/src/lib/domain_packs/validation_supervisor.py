"""Domain-pack validation supervisor.

The supervisor consumes provider-owned domain-pack metadata and writes validation
findings back into the domain envelope.  It intentionally keeps Alliance, DB, and
schema-specific behavior behind domain-pack bindings instead of hard-coding it in
core runtime code.
"""

from __future__ import annotations

import importlib
import json
import re
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Callable, Iterable, Mapping

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
    parse_field_path,
)

from .registry import LoadedDomainPack
from .validation_registry import (
    DomainPackValidationRegistry,
    FieldValidationPolicy,
    ValidationBindingState,
    ValidatorBinding,
    ValidatorBindingMatch,
    ValidatorMetadataEntry,
)


_CURIE_PATTERN_TEMPLATE = r"^{prefix}:.+"


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
) -> ValidationSupervisorResult:
    """Run metadata-driven validation and return an updated envelope.

    Active callable bindings are executed.  Planned and blocked bindings are
    surfaced as visible findings.  Unsupported active bindings emit explicit
    dispatch-unavailable findings rather than masquerading as successful.
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
    new_findings.extend(
        _planned_or_blocked_binding_findings(
            matches=state_matches[ValidationBindingState.PLANNED],
            provider_model_ref=provider_model_ref,
        )
    )
    new_findings.extend(
        _planned_or_blocked_binding_findings(
            matches=state_matches[ValidationBindingState.BLOCKED],
            provider_model_ref=provider_model_ref,
        )
    )
    new_findings.extend(
        _active_binding_findings(
            envelope=envelope,
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
    existing_finding_ids = {
        finding.finding_id
        for finding in existing_findings
        if finding.finding_id is not None
    }
    appended_findings: list[ValidationFinding] = []
    history_events = list(envelope.history)

    for raw_finding in findings:
        finding = _with_stable_finding_id(envelope.envelope_id, raw_finding)
        if finding.finding_id in existing_finding_ids:
            continue
        existing_finding_ids.add(finding.finding_id)
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
            findings.append(
                ValidationFinding(
                    severity=(
                        ValidationFindingSeverity.BLOCKER
                        if policy is not None and policy.export_blocking
                        else ValidationFindingSeverity.ERROR
                    ),
                    code="domain_pack.required_field_missing",
                    message=(
                        f"{domain_object.object_type}.{field_definition.field_path} "
                        "is required by the domain pack but missing from the envelope payload."
                    ),
                    field_ref=FieldRef(
                        object_ref=_object_ref_for(domain_object),
                        field_path=field_definition.field_path,
                    ),
                    details={
                        "validation_metadata": _with_provider_model_ref(
                            {
                                "validator_id": "domain_pack.required_field_policy",
                                "binding_state": ValidationBindingState.ACTIVE.value,
                                "metadata_source": "field_policy",
                                "field_policy": policy.identity_details()
                                if policy is not None
                                else _fallback_field_policy_details(
                                    envelope=envelope,
                                    domain_object=domain_object,
                                    field_path=field_definition.field_path,
                                    field_type=field_definition.field_type.value,
                                ),
                            },
                            provider_model_ref,
                        )
                    },
                )
            )
    return findings


def _planned_or_blocked_binding_findings(
    *,
    matches: Iterable[ValidatorBindingMatch],
    provider_model_ref: Mapping[str, Any] | None,
) -> list[ValidationFinding]:
    findings: list[ValidationFinding] = []
    for match in matches:
        binding = match.binding
        details = _match_details(match, provider_model_ref=provider_model_ref)
        severity = (
            ValidationFindingSeverity.INFO
            if binding.state is ValidationBindingState.PLANNED
            else ValidationFindingSeverity.BLOCKER
        )
        findings.append(
            ValidationFinding(
                severity=severity,
                code=f"domain_pack.validator_binding_{binding.state.value}",
                message=_state_message(
                    state=binding.state,
                    identifier=binding.binding_id,
                    reason=binding.reason,
                    blocked_by=binding.blocked_by,
                ),
                object_ref=_match_object_ref(match),
                field_ref=_match_field_ref(match),
                details=details,
            )
        )
    return findings


def _active_binding_findings(
    *,
    envelope: DomainEnvelope,
    matches: Iterable[ValidatorBindingMatch],
    provider_model_ref: Mapping[str, Any] | None,
) -> list[ValidationFinding]:
    findings: list[ValidationFinding] = []
    matches_by_binding = _matches_by_binding(matches)
    for binding_id in sorted(matches_by_binding):
        binding_matches = matches_by_binding[binding_id]
        binding = binding_matches[0].binding
        if binding.validator:
            findings.extend(
                _callable_binding_findings(
                    envelope=envelope,
                    binding=binding,
                    provider_model_ref=provider_model_ref,
                )
            )
            continue
        if binding.validation_kind == "curie_prefix_format":
            findings.extend(
                _curie_prefix_findings(
                    matches=binding_matches,
                    provider_model_ref=provider_model_ref,
                )
            )
            continue

        findings.extend(
            _unsupported_active_binding_findings(
                matches=binding_matches,
                provider_model_ref=provider_model_ref,
            )
        )
    return findings


def _callable_binding_findings(
    *,
    envelope: DomainEnvelope,
    binding: ValidatorBinding,
    provider_model_ref: Mapping[str, Any] | None,
) -> list[ValidationFinding]:
    validator = _load_validator_callable(binding.validator or "")
    raw_findings = validator(envelope)
    findings = _coerce_validator_findings(raw_findings, binding)
    enriched_findings: list[ValidationFinding] = []
    for finding in findings:
        enriched_findings.append(
            finding.model_copy(
                update={
                    "details": {
                        **finding.details,
                        "validation_metadata": _with_provider_model_ref(
                            binding.identity_details(),
                            provider_model_ref,
                        ),
                    }
                }
            )
        )
    return enriched_findings


def _curie_prefix_findings(
    *,
    matches: Iterable[ValidatorBindingMatch],
    provider_model_ref: Mapping[str, Any] | None,
) -> list[ValidationFinding]:
    findings: list[ValidationFinding] = []
    for match in matches:
        binding = match.binding
        prefix = str(binding.raw.get("prefix") or "").strip()
        if not prefix or match.object_envelope is None or match.field_definition is None:
            findings.append(
                _dispatch_unavailable_finding(
                    match=match,
                    provider_model_ref=provider_model_ref,
                    reason="curie_prefix_format bindings require prefix, object, and field targets",
                )
            )
            continue

        value = _payload_value(match.object_envelope.payload, match.field_definition.field_path)
        if value is None:
            continue
        if not isinstance(value, str) or re.match(
            _CURIE_PATTERN_TEMPLATE.format(prefix=re.escape(prefix)),
            value,
        ) is None:
            findings.append(
                ValidationFinding(
                    severity=(
                        ValidationFindingSeverity.BLOCKER
                        if binding.blocking
                        else ValidationFindingSeverity.ERROR
                    ),
                    code="domain_pack.curie_prefix_mismatch",
                    message=(
                        f"{match.object_envelope.object_type}."
                        f"{match.field_definition.field_path} must use the {prefix}: CURIE prefix."
                    ),
                    field_ref=FieldRef(
                        object_ref=_object_ref_for(match.object_envelope),
                        field_path=match.field_definition.field_path,
                    ),
                    details={
                        **_match_details(match, provider_model_ref=provider_model_ref),
                        "observed_value": value,
                        "expected_prefix": prefix,
                    },
                )
            )
    return findings


def _unsupported_active_binding_findings(
    *,
    matches: Iterable[ValidatorBindingMatch],
    provider_model_ref: Mapping[str, Any] | None,
) -> list[ValidationFinding]:
    return [
        _dispatch_unavailable_finding(
            match=match,
            provider_model_ref=provider_model_ref,
            reason="no executable validator is registered for this active binding",
        )
        for match in matches
    ]


def _dispatch_unavailable_finding(
    *,
    match: ValidatorBindingMatch,
    provider_model_ref: Mapping[str, Any] | None,
    reason: str,
) -> ValidationFinding:
    binding = match.binding
    return ValidationFinding(
        severity=(
            ValidationFindingSeverity.BLOCKER
            if binding.blocking
            else ValidationFindingSeverity.WARNING
        ),
        code="domain_pack.validator_dispatch_unavailable",
        message=(
            f"Active validator binding '{binding.binding_id}' could not be dispatched: "
            f"{reason}."
        ),
        object_ref=_match_object_ref(match),
        field_ref=_match_field_ref(match),
        details={
            **_match_details(match, provider_model_ref=provider_model_ref),
            "dispatch_unavailable_reason": reason,
        },
    )


def _coerce_validator_findings(
    raw_findings: Any,
    binding: ValidatorBinding,
) -> list[ValidationFinding]:
    if raw_findings is None:
        return []
    if isinstance(raw_findings, ValidationFinding):
        return [raw_findings]
    if not isinstance(raw_findings, Iterable) or isinstance(raw_findings, (str, bytes)):
        raise TypeError(
            f"Validator binding '{binding.binding_id}' returned "
            f"{type(raw_findings).__name__}; expected ValidationFinding iterable"
        )

    findings: list[ValidationFinding] = []
    for raw_finding in raw_findings:
        if isinstance(raw_finding, ValidationFinding):
            findings.append(raw_finding)
        elif isinstance(raw_finding, Mapping):
            findings.append(ValidationFinding.model_validate(raw_finding))
        else:
            raise TypeError(
                f"Validator binding '{binding.binding_id}' returned item "
                f"{type(raw_finding).__name__}; expected ValidationFinding or mapping"
            )
    return findings


def _load_validator_callable(validator_path: str) -> Callable[[DomainEnvelope], Any]:
    if ":" in validator_path:
        module_name, function_name = validator_path.split(":", 1)
    else:
        module_name, _, function_name = validator_path.rpartition(".")
    if not module_name or not function_name:
        raise ValueError(
            "validator must use import syntax like package.module:function "
            "or package.module.function"
        )
    module = importlib.import_module(module_name)
    validator = getattr(module, function_name)
    if not callable(validator):
        raise TypeError(f"Validator '{validator_path}' is not callable")
    return validator


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
    return _object_ref_for(match.object_envelope)


def _match_field_ref(match: ValidatorBindingMatch) -> FieldRef | None:
    if match.object_envelope is None or match.field_definition is None:
        return None
    return FieldRef(
        object_ref=_object_ref_for(match.object_envelope),
        field_path=match.field_definition.field_path,
    )


def _object_ref_for(domain_object: CuratableObjectEnvelope) -> ObjectRef:
    if domain_object.object_id is not None:
        return ObjectRef(
            object_id=domain_object.object_id,
            object_type=domain_object.object_type,
        )
    if domain_object.pending_ref_id is not None:
        return ObjectRef(
            pending_ref_id=domain_object.pending_ref_id,
            object_type=domain_object.object_type,
        )
    raise ValueError("CuratableObjectEnvelope must provide object_id or pending_ref_id")


def _payload_value(payload: Mapping[str, Any], field_path: str) -> Any:
    current: Any = payload
    for part in parse_field_path(field_path):
        if isinstance(part, str):
            if not isinstance(current, Mapping) or part not in current:
                return None
            current = current[part]
            continue
        if (
            not isinstance(current, list)
            or isinstance(current, (str, bytes, bytearray))
            or part >= len(current)
        ):
            return None
        current = current[part]
    return current


def _fallback_field_policy_details(
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
        "required": True,
        "export_blocking": False,
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
    target = _finding_target_payload(finding)
    seed_payload = {
        "envelope_id": envelope_id,
        "code": finding.code,
        "severity": finding.severity.value,
        "message": finding.message,
        "target": target,
        "details": finding.details,
    }
    digest = sha256(json.dumps(seed_payload, sort_keys=True).encode("utf-8")).hexdigest()
    return finding.model_copy(update={"finding_id": f"validation:{digest}"})


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
    digest = sha256(json.dumps(seed_payload, sort_keys=True).encode("utf-8")).hexdigest()
    field_ref = finding.field_ref
    if (
        field_ref is not None
        and _finding_field_ref_exists(envelope=envelope, field_ref=field_ref)
    ):
        event_field_ref = field_ref
        event_object_ref = None
    else:
        event_field_ref = None
        event_object_ref = finding.object_ref or (
            finding.field_ref.object_ref if finding.field_ref is not None else None
        )

    return HistoryEvent(
        event_type=HistoryEventKind.VALIDATION_FINDING_ADDED,
        event_id=f"validation-event:{digest}",
        actor_type=HistoryActorType.SYSTEM,
        actor_id=actor_id,
        message=f"Validation finding added: {finding.code or finding.severity.value}",
        object_ref=event_object_ref,
        field_ref=event_field_ref,
        details={
            "finding_id": finding.finding_id,
            "finding_code": finding.code,
            "finding_severity": finding.severity.value,
            "finding_status": finding.status.value,
            "target": target,
        },
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
