"""Domain-pack validation supervisor.

The supervisor consumes provider-owned domain-pack metadata and writes validation
findings back into the domain envelope.  It intentionally keeps provider-specific,
DB, and schema-specific behavior behind domain-pack bindings instead of hard-coding
it in core runtime code.
"""

from __future__ import annotations

import importlib
import json
import logging
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
    ValidationFindingStatus,
    field_path_exists,
    parse_field_path,
)
from src.lib.lookup_status import (
    LOOKUP_STATUS_AMBIGUOUS,
    LOOKUP_STATUS_BLOCKED,
    LOOKUP_STATUS_NOT_FOUND,
    LOOKUP_STATUS_SUCCESS,
    LOOKUP_STATUS_TRANSIENT,
    LOOKUP_STATUS_UNDER_DEVELOPMENT,
)

from .registry import LoadedDomainPack
from .validation_registry import (
    DomainPackValidationRegistry,
    ValidationBindingState,
    ValidatorBinding,
    ValidatorBindingMatch,
    ValidatorMetadataEntry,
)


_CURIE_PATTERN_TEMPLATE = r"^{prefix}:.+"
_LOOKUP_MISSING_EXPECTED_RESULT_FIELD = "missing_expected_result_field"
_LOOKUP_MISSING_EXPECTED_RESULT_CODE = (
    "domain_pack.validator_lookup_projection_missing"
)
_LOOKUP_MISSING_EXPECTED_RESULT_RETRY_LIMIT = 1
_MISSING_LOOKUP_VALUE = object()

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ValidationSupervisorResult:
    """Result of one supervisor pass over a domain envelope."""

    envelope: DomainEnvelope
    registry: DomainPackValidationRegistry
    matched_bindings: tuple[ValidatorBindingMatch, ...]
    appended_findings: tuple[ValidationFinding, ...]


@dataclass(frozen=True)
class MissingExpectedLookupValue:
    """Declared lookup result value that was not present in a successful response."""

    result_field: str
    field_path: str
    observed_value: Any


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
        if _finding_resolves_projection_missing(finding):
            existing_findings, status_events = _supersede_projection_missing_findings(
                envelope=envelope,
                existing_findings=existing_findings,
                superseding_finding=finding,
                actor_id=actor_id,
            )
            history_events.extend(status_events)
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


def _finding_resolves_projection_missing(finding: ValidationFinding) -> bool:
    return (
        finding.status is ValidationFindingStatus.RESOLVED
        and finding.code == "domain_pack.validator_lookup_resolved"
        and _projection_missing_supersede_key(finding) is not None
    )


def _supersede_projection_missing_findings(
    *,
    envelope: DomainEnvelope,
    existing_findings: list[ValidationFinding],
    superseding_finding: ValidationFinding,
    actor_id: str,
) -> tuple[list[ValidationFinding], tuple[HistoryEvent, ...]]:
    superseding_key = _projection_missing_supersede_key(superseding_finding)
    if superseding_key is None:
        return existing_findings, ()

    updated_findings: list[ValidationFinding] = []
    status_events: list[HistoryEvent] = []
    for existing_finding in existing_findings:
        if (
            existing_finding.status is ValidationFindingStatus.OPEN
            and existing_finding.code == _LOOKUP_MISSING_EXPECTED_RESULT_CODE
            and _projection_missing_supersede_key(existing_finding) == superseding_key
        ):
            updated_details = {
                **existing_finding.details,
                "superseded_by_finding_id": superseding_finding.finding_id,
                "superseded_by_code": superseding_finding.code,
                "superseded_reason": "expected_result_field_resolved",
            }
            updated_finding = existing_finding.model_copy(
                update={
                    "status": ValidationFindingStatus.RESOLVED,
                    "details": updated_details,
                }
            )
            updated_findings.append(updated_finding)
            status_events.append(
                _history_event_for_finding_status_change(
                    envelope=envelope,
                    previous_finding=existing_finding,
                    updated_finding=updated_finding,
                    superseding_finding=superseding_finding,
                    actor_id=actor_id,
                )
            )
            continue
        updated_findings.append(existing_finding)

    return updated_findings, tuple(status_events)


def _projection_missing_supersede_key(
    finding: ValidationFinding,
) -> tuple[tuple[str, str], str, str, str] | None:
    field_ref = finding.field_ref
    if field_ref is None:
        return None
    details = finding.details
    validation_metadata = details.get("validation_metadata")
    if not isinstance(validation_metadata, Mapping):
        validation_metadata = {}
    binding_id = _optional_string(validation_metadata.get("validator_binding_id"))
    expected_result_field = _optional_string(details.get("expected_result_field"))
    if binding_id is None or expected_result_field is None:
        return None
    return (
        field_ref.object_ref.ref_key(),
        field_ref.field_path,
        binding_id,
        expected_result_field,
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


def _planned_or_blocked_binding_findings(
    *,
    matches: Iterable[ValidatorBindingMatch],
    provider_model_ref: Mapping[str, Any] | None,
) -> list[ValidationFinding]:
    findings: list[ValidationFinding] = []
    for match in matches:
        binding = match.binding
        details = _match_details(match, provider_model_ref=provider_model_ref)
        lookup_status = (
            LOOKUP_STATUS_UNDER_DEVELOPMENT
            if binding.state is ValidationBindingState.PLANNED
            else LOOKUP_STATUS_BLOCKED
        )
        severity = (
            ValidationFindingSeverity.INFO
            if binding.state is ValidationBindingState.PLANNED
            else ValidationFindingSeverity.BLOCKER
        )
        message = _state_message(
            state=binding.state,
            identifier=binding.binding_id,
            reason=binding.reason,
            blocked_by=binding.blocked_by,
        )
        _attach_lookup_attempt_details(
            details,
            match=match,
            lookup_status=lookup_status,
            explanation=message,
        )
        findings.append(
            ValidationFinding(
                severity=severity,
                code=f"domain_pack.validator_binding_{binding.state.value}",
                message=message,
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
        if binding.is_executable:
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
        if _binding_uses_agr_curation_lookup(binding):
            findings.extend(
                _agr_curation_lookup_findings(
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


def _binding_uses_agr_curation_lookup(binding: ValidatorBinding) -> bool:
    return (
        binding.tool_name == "agr_curation_query"
        and binding.tool_method is not None
    )


def _agr_curation_lookup_findings(
    *,
    matches: Iterable[ValidatorBindingMatch],
    provider_model_ref: Mapping[str, Any] | None,
) -> list[ValidationFinding]:
    findings: list[ValidationFinding] = []
    for binding_matches in _lookup_match_groups(matches):
        representative = binding_matches[0]
        binding = representative.binding
        if representative.object_envelope is None:
            findings.append(
                _dispatch_unavailable_finding(
                    match=representative,
                    provider_model_ref=provider_model_ref,
                    reason="AGR curation lookup bindings require an envelope object target",
                )
            )
            continue

        response_payload = _execute_agr_curation_lookup(
            binding=binding,
            match=representative,
        )
        target_matches = [
            match for match in binding_matches if match.field_definition is not None
        ] or [representative]
        missing_values = _missing_expected_lookup_values(
            matches=target_matches,
            response_payload=response_payload,
        )
        if missing_values:
            retry_prompt = _missing_expected_lookup_retry_prompt(
                binding=binding,
                missing_values=missing_values,
            )
            retry_context = _missing_expected_lookup_retry_context(
                binding=binding,
                retry_prompt=retry_prompt,
                missing_values=missing_values,
                response_payload=response_payload,
            )
            logger.warning(
                "AGR lookup partially succeeded but failed for these values; "
                "retrying once. binding_id=%s object_ref=%s "
                "missing=%s",
                binding.binding_id,
                _object_ref_for(representative.object_envelope).ref_key(),
                [
                    {
                        "result_field": missing.result_field,
                        "field_path": missing.field_path,
                    }
                    for missing in missing_values
                ],
            )
            retry_payload = _execute_agr_curation_lookup(
                binding=binding,
                match=representative,
                validation_retry_context=retry_context,
            )
            response_payload = _merge_lookup_retry_payload(
                initial_payload=response_payload,
                retry_payload=retry_payload,
                match=representative,
                target_matches=target_matches,
                retry_prompt=retry_prompt,
                missing_values=missing_values,
                retry_context=retry_context,
            )
        for match in target_matches:
            findings.append(
                _agr_lookup_finding_for_match(
                    match=match,
                    response_payload=response_payload,
                    provider_model_ref=provider_model_ref,
                )
            )
    return findings


def _lookup_match_groups(
    matches: Iterable[ValidatorBindingMatch],
) -> list[tuple[ValidatorBindingMatch, ...]]:
    grouped: dict[tuple[str, str], list[ValidatorBindingMatch]] = {}
    pack_level: list[ValidatorBindingMatch] = []
    for match in matches:
        if match.object_envelope is None:
            pack_level.append(match)
            continue
        ref_key = _object_ref_for(match.object_envelope).ref_key()
        grouped.setdefault(ref_key, []).append(match)
    groups = [tuple(items) for _key, items in sorted(grouped.items())]
    groups.extend((match,) for match in pack_level)
    return groups


def _execute_agr_curation_lookup(
    *,
    binding: ValidatorBinding,
    match: ValidatorBindingMatch,
    validation_retry_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    method = binding.tool_method or ""
    kwargs = _agr_lookup_kwargs(binding=binding, match=match)
    if validation_retry_context is not None:
        kwargs["validation_retry_context"] = dict(validation_retry_context)
    try:
        raw_response = _agr_curation_query_callable(method=method, **kwargs)
    except Exception as exc:  # pragma: no cover - defensive around runtime tool wiring
        explanation = (
            f"AGR curation lookup {method!r} failed while dispatching validator "
            f"binding {binding.binding_id!r}."
        )
        return {
            "status": "error",
            "lookup_status": LOOKUP_STATUS_TRANSIENT,
            "failure_classification": LOOKUP_STATUS_TRANSIENT,
            "explanation": explanation,
            "message": explanation,
            "lookup_attempts": [
                _lookup_attempt_for_match(
                    match=match,
                    lookup_status=LOOKUP_STATUS_TRANSIENT,
                    explanation=explanation,
                )
            ],
            "error": {
                "type": type(exc).__name__,
                "message": str(exc),
            },
        }
    return _coerce_lookup_response_payload(raw_response)


def _agr_lookup_kwargs(
    *,
    binding: ValidatorBinding,
    match: ValidatorBindingMatch,
) -> dict[str, Any]:
    if match.object_envelope is None:
        return {}
    kwargs: dict[str, Any] = {}
    for input_name, raw_field_path in binding.input_fields.items():
        field_path = str(raw_field_path)
        kwargs[str(input_name)] = _payload_value(
            match.object_envelope.payload,
            _relative_field_path(
                field_path,
                object_type=match.object_envelope.object_type,
            ),
        )
    return kwargs


def _agr_curation_query_callable(method: str, **kwargs: Any) -> Any:
    from src.lib.openai_agents.tools import agr_curation

    return agr_curation._AGR_QUERY_CALLABLE(method=method, **kwargs)


def _coerce_lookup_response_payload(raw_response: Any) -> dict[str, Any]:
    if hasattr(raw_response, "model_dump"):
        payload = raw_response.model_dump(mode="json")
    elif isinstance(raw_response, Mapping):
        payload = dict(raw_response)
    else:
        payload = {
            key: getattr(raw_response, key)
            for key in (
                "status",
                "data",
                "count",
                "warnings",
                "message",
                "lookup_status",
                "failure_classification",
                "explanation",
                "lookup_attempts",
                "candidate_matches",
                "result_projections",
            )
            if hasattr(raw_response, key)
        }
    return {key: value for key, value in payload.items() if value is not None}


def _missing_expected_lookup_values(
    *,
    matches: Iterable[ValidatorBindingMatch],
    response_payload: Mapping[str, Any],
) -> tuple[MissingExpectedLookupValue, ...]:
    if _lookup_status_from_response(response_payload) != LOOKUP_STATUS_SUCCESS:
        return ()

    missing: list[MissingExpectedLookupValue] = []
    for match in matches:
        if match.field_definition is None or match.object_envelope is None:
            continue
        expected_result_field = _expected_result_field_for_match(match)
        if expected_result_field is None:
            continue
        resolved_value = _lookup_response_result_value(
            response_payload,
            expected_result_field,
        )
        if _usable_lookup_result_value(resolved_value):
            continue
        missing.append(
            MissingExpectedLookupValue(
                result_field=expected_result_field,
                field_path=match.field_definition.field_path,
                observed_value=_payload_value(
                    match.object_envelope.payload,
                    match.field_definition.field_path,
                ),
            )
        )
    return tuple(missing)


def _missing_expected_lookup_retry_prompt(
    *,
    binding: ValidatorBinding,
    missing_values: tuple[MissingExpectedLookupValue, ...],
) -> str:
    missing_summary = ", ".join(
        f"{missing.result_field!r} for envelope field {missing.field_path!r}"
        for missing in missing_values
    )
    return (
        "AGR curation lookup partially succeeded but failed for these declared "
        f"validation values for binding {binding.binding_id!r}: {missing_summary}. "
        "Re-run the same validator once and require the result projection to include "
        "the missing values before treating the field as resolved. If the re-lookup "
        "still cannot provide them, keep an open finding for curator review."
    )


def _missing_expected_lookup_retry_context(
    *,
    binding: ValidatorBinding,
    retry_prompt: str,
    missing_values: tuple[MissingExpectedLookupValue, ...],
    response_payload: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "reason": _LOOKUP_MISSING_EXPECTED_RESULT_FIELD,
        "validator_binding_id": binding.binding_id,
        "tool_method": binding.tool_method,
        "prompt": retry_prompt,
        "original_lookup_status": _lookup_status_from_response(response_payload),
        "missing_expected_result_fields": [
            _missing_expected_lookup_value_details(missing)
            for missing in missing_values
        ],
    }


def _merge_lookup_retry_payload(
    *,
    initial_payload: Mapping[str, Any],
    retry_payload: Mapping[str, Any],
    match: ValidatorBindingMatch,
    target_matches: Iterable[ValidatorBindingMatch],
    retry_prompt: str,
    missing_values: tuple[MissingExpectedLookupValue, ...],
    retry_context: Mapping[str, Any],
) -> dict[str, Any]:
    final_payload = dict(initial_payload)
    final_payload["retry_lookup_status"] = _lookup_status_from_response(retry_payload)
    final_payload["retry_explanation"] = (
        _optional_string(retry_payload.get("explanation"))
        or _optional_string(retry_payload.get("message"))
    )
    final_payload["data"] = _merge_lookup_data_values(
        initial_payload=initial_payload,
        retry_payload=retry_payload,
    )
    final_payload["candidate_matches"] = _merged_lookup_list(
        initial_payload.get("candidate_matches"),
        retry_payload.get("candidate_matches"),
    )
    final_payload["result_projections"] = _merged_lookup_list(
        initial_payload.get("result_projections"),
        retry_payload.get("result_projections"),
    )
    final_payload["result_value_evidence"] = _result_value_evidence_for_matches(
        target_matches=target_matches,
        initial_payload=initial_payload,
        retry_payload=retry_payload,
    )
    remaining_missing_values = tuple(
        missing
        for missing in missing_values
        if not _usable_lookup_result_value(
            _lookup_response_result_value(final_payload, missing.result_field)
        )
    )
    initial_attempts = _annotated_lookup_attempts(
        response_payload=initial_payload,
        match=match,
        retry_index=0,
    )
    retry_attempts = _annotated_lookup_attempts(
        response_payload=retry_payload,
        match=match,
        retry_index=1,
        retry_prompt=retry_prompt,
    )
    final_payload["lookup_attempts"] = [*initial_attempts, *retry_attempts]
    final_payload["supervisor_retries"] = [
        {
            "reason": _LOOKUP_MISSING_EXPECTED_RESULT_FIELD,
            "prompt": retry_prompt,
            "retry_context": dict(retry_context),
            "max_attempts": _LOOKUP_MISSING_EXPECTED_RESULT_RETRY_LIMIT,
            "used_attempts": 1,
            "exhausted": bool(remaining_missing_values),
            "missing_expected_result_fields": [
                _missing_expected_lookup_value_details(missing)
                for missing in missing_values
            ],
            "remaining_missing_expected_result_fields": [
                _missing_expected_lookup_value_details(missing)
                for missing in remaining_missing_values
            ],
        }
    ]
    return final_payload


def _merge_lookup_data_values(
    *,
    initial_payload: Mapping[str, Any],
    retry_payload: Mapping[str, Any],
) -> dict[str, Any] | list[Any] | None:
    initial_data = initial_payload.get("data")
    retry_data = retry_payload.get("data")
    if isinstance(initial_data, Mapping) and isinstance(retry_data, Mapping):
        merged = dict(initial_data)
        for key, value in retry_data.items():
            existing_value = merged.get(key, _MISSING_LOOKUP_VALUE)
            if (
                _usable_lookup_result_value(value)
                or not _usable_lookup_result_value(existing_value)
            ):
                merged[key] = value
        return merged
    if isinstance(initial_data, Mapping):
        return dict(initial_data)
    if isinstance(retry_data, Mapping):
        return dict(retry_data)
    return initial_data if initial_data is not None else retry_data


def _merged_lookup_list(*values: Any) -> list[Any] | None:
    merged: list[Any] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, list):
            continue
        for item in value:
            key = json.dumps(item, sort_keys=True, default=str)
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)
    return merged or None


def _result_value_evidence_for_matches(
    *,
    target_matches: Iterable[ValidatorBindingMatch],
    initial_payload: Mapping[str, Any],
    retry_payload: Mapping[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    evidence: dict[str, list[dict[str, Any]]] = {}
    for match in target_matches:
        expected_result_field = _expected_result_field_for_match(match)
        if expected_result_field is None:
            continue
        for retry_index, payload in (
            (0, initial_payload),
            (1, retry_payload),
        ):
            value = _lookup_response_result_value(payload, expected_result_field)
            if not _usable_lookup_result_value(value):
                continue
            evidence.setdefault(expected_result_field, []).append(
                {
                    "value": value,
                    "supervisor_retry_index": retry_index,
                    "lookup_status": _lookup_status_from_response(payload),
                }
            )
    return evidence


def _annotated_lookup_attempts(
    *,
    response_payload: Mapping[str, Any],
    match: ValidatorBindingMatch,
    retry_index: int,
    retry_prompt: str | None = None,
) -> list[dict[str, Any]]:
    lookup_status = _lookup_status_from_response(response_payload)
    explanation = (
        _optional_string(response_payload.get("explanation"))
        or _optional_string(response_payload.get("message"))
        or f"AGR curation lookup returned {lookup_status}."
    )
    attempts = _lookup_attempts_from_response(
        response_payload=response_payload,
        match=match,
        lookup_status=lookup_status,
        explanation=explanation,
    )
    annotated: list[dict[str, Any]] = []
    for attempt in attempts:
        next_attempt = dict(attempt)
        next_attempt["supervisor_retry_index"] = retry_index
        if retry_prompt is not None:
            next_attempt["supervisor_retry_prompt"] = retry_prompt
        annotated.append(next_attempt)
    return annotated


def _missing_expected_lookup_value_details(
    missing: MissingExpectedLookupValue,
) -> dict[str, Any]:
    details: dict[str, Any] = {
        "result_field": missing.result_field,
        "field_path": missing.field_path,
    }
    if missing.observed_value is not None:
        details["observed_value"] = missing.observed_value
    return details


def _agr_lookup_finding_for_match(
    *,
    match: ValidatorBindingMatch,
    response_payload: Mapping[str, Any],
    provider_model_ref: Mapping[str, Any] | None,
) -> ValidationFinding:
    lookup_status = _lookup_status_from_response(response_payload)
    if lookup_status == LOOKUP_STATUS_SUCCESS:
        return _agr_lookup_success_finding(
            match=match,
            response_payload=response_payload,
            provider_model_ref=provider_model_ref,
        )
    return _agr_lookup_open_finding(
        match=match,
        response_payload=response_payload,
        lookup_status=lookup_status,
        provider_model_ref=provider_model_ref,
    )


def _agr_lookup_success_finding(
    *,
    match: ValidatorBindingMatch,
    response_payload: Mapping[str, Any],
    provider_model_ref: Mapping[str, Any] | None,
) -> ValidationFinding:
    binding = match.binding
    field_path = match.field_definition.field_path if match.field_definition else None
    expected_result_field = _expected_result_field_for_match(match)
    resolved_values = (
        _lookup_response_result_values(response_payload, expected_result_field)
        if expected_result_field is not None
        else []
    )
    resolved_value = (
        resolved_values[0] if resolved_values else _MISSING_LOOKUP_VALUE
    )
    observed_value = (
        _payload_value(match.object_envelope.payload, field_path)
        if match.object_envelope is not None and field_path is not None
        else None
    )
    details = _agr_lookup_details(
        match=match,
        response_payload=response_payload,
        provider_model_ref=provider_model_ref,
    )
    if expected_result_field is not None:
        details["expected_result_field"] = expected_result_field
    if observed_value is not None:
        details["observed_value"] = observed_value
    if _usable_lookup_result_value(resolved_value):
        details["resolved_value"] = resolved_value
    if len(resolved_values) > 1:
        details["resolved_values"] = resolved_values

    if expected_result_field is not None and not _usable_lookup_result_value(
        resolved_value
    ):
        details["failure_classification"] = _LOOKUP_MISSING_EXPECTED_RESULT_FIELD
        details["missing_expected_result_fields"] = [
            _missing_expected_lookup_value_details(
                MissingExpectedLookupValue(
                    result_field=expected_result_field,
                    field_path=field_path or "",
                    observed_value=observed_value,
                )
            )
        ]
        retry_prompt = _latest_supervisor_retry_prompt(details)
        if retry_prompt is not None:
            details["retry_prompt"] = retry_prompt
        return ValidationFinding(
            severity=(
                ValidationFindingSeverity.BLOCKER
                if binding.blocking
                else ValidationFindingSeverity.ERROR
            ),
            status=ValidationFindingStatus.OPEN,
            code=_LOOKUP_MISSING_EXPECTED_RESULT_CODE,
            message=(
                f"Validator binding '{binding.binding_id}' lookup partially "
                f"succeeded but failed for declared result value "
                f"{expected_result_field!r} needed to validate envelope field "
                f"{field_path!r}."
            ),
            object_ref=_match_object_ref(match),
            field_ref=_match_field_ref(match),
            details=details,
        )

    if (
        expected_result_field is not None
        and observed_value is not None
        and any(
            _normalized_lookup_value(observed_value) != _normalized_lookup_value(value)
            for value in resolved_values
        )
    ):
        conflict_value = next(
            value
            for value in resolved_values
            if _normalized_lookup_value(observed_value) != _normalized_lookup_value(value)
        )
        details["failure_classification"] = "conflict"
        details["conflicting_resolved_values"] = [
            value
            for value in resolved_values
            if _normalized_lookup_value(observed_value) != _normalized_lookup_value(value)
        ]
        return ValidationFinding(
            severity=(
                ValidationFindingSeverity.BLOCKER
                if binding.blocking
                else ValidationFindingSeverity.ERROR
            ),
            status=ValidationFindingStatus.OPEN,
            code="domain_pack.validator_lookup_conflict",
            message=(
                f"Validator binding '{binding.binding_id}' resolved "
                f"{expected_result_field!r} to {conflict_value!r}, but the envelope "
                f"field contains {observed_value!r}."
            ),
            object_ref=_match_object_ref(match),
            field_ref=_match_field_ref(match),
            details=details,
        )

    return ValidationFinding(
        severity=ValidationFindingSeverity.INFO,
        status=ValidationFindingStatus.RESOLVED,
        code="domain_pack.validator_lookup_resolved",
        message=(
            f"Validator binding '{binding.binding_id}' resolved through "
            f"AGR curation lookup method '{binding.tool_method}'."
        ),
        object_ref=_match_object_ref(match),
        field_ref=_match_field_ref(match),
        details=details,
    )


def _agr_lookup_open_finding(
    *,
    match: ValidatorBindingMatch,
    response_payload: Mapping[str, Any],
    lookup_status: str,
    provider_model_ref: Mapping[str, Any] | None,
) -> ValidationFinding:
    binding = match.binding
    details = _agr_lookup_details(
        match=match,
        response_payload=response_payload,
        provider_model_ref=provider_model_ref,
    )
    details["failure_classification"] = (
        _optional_string(response_payload.get("failure_classification"))
        or lookup_status
    )
    return ValidationFinding(
        severity=_lookup_failure_severity(binding=binding, lookup_status=lookup_status),
        status=ValidationFindingStatus.OPEN,
        code=_lookup_failure_code(lookup_status),
        message=(
            _optional_string(response_payload.get("explanation"))
            or _optional_string(response_payload.get("message"))
            or (
                f"Validator binding '{binding.binding_id}' returned "
                f"{lookup_status!r} from AGR curation lookup method "
                f"'{binding.tool_method}'."
            )
        ),
        object_ref=_match_object_ref(match),
        field_ref=_match_field_ref(match),
        details=details,
    )


def _agr_lookup_details(
    *,
    match: ValidatorBindingMatch,
    response_payload: Mapping[str, Any],
    provider_model_ref: Mapping[str, Any] | None,
) -> dict[str, Any]:
    details = _match_details(match, provider_model_ref=provider_model_ref)
    lookup_status = _lookup_status_from_response(response_payload)
    explanation = (
        _optional_string(response_payload.get("explanation"))
        or _optional_string(response_payload.get("message"))
        or f"AGR curation lookup returned {lookup_status}."
    )
    details["lookup_status"] = lookup_status
    details["lookup_explanation"] = explanation
    details["lookup_attempts"] = _lookup_attempts_from_response(
        response_payload=response_payload,
        match=match,
        lookup_status=lookup_status,
        explanation=explanation,
    )
    for key in (
        "candidate_matches",
        "result_projections",
        "supervisor_retries",
        "warnings",
        "error",
    ):
        value = response_payload.get(key)
        if value is not None:
            details[key if key != "warnings" else "lookup_warnings"] = value
    provider_projection = match.binding.provider_projection
    if provider_projection:
        details["provider_projections"] = [dict(provider_projection)]
    return details


def _lookup_attempts_from_response(
    *,
    response_payload: Mapping[str, Any],
    match: ValidatorBindingMatch,
    lookup_status: str,
    explanation: str,
) -> list[dict[str, Any]]:
    lookup_attempts = response_payload.get("lookup_attempts")
    if isinstance(lookup_attempts, list) and all(
        isinstance(item, Mapping) for item in lookup_attempts
    ):
        return [dict(item) for item in lookup_attempts]
    return [
        _lookup_attempt_for_match(
            match=match,
            lookup_status=lookup_status,
            explanation=explanation,
        )
    ]


def _lookup_status_from_response(response_payload: Mapping[str, Any]) -> str:
    lookup_status = _optional_string(response_payload.get("lookup_status"))
    if lookup_status is not None:
        return lookup_status
    failure_classification = _optional_string(response_payload.get("failure_classification"))
    if failure_classification is not None:
        return failure_classification
    status = _optional_string(response_payload.get("status"))
    if status == "ok":
        count = response_payload.get("count")
        if isinstance(count, int):
            return LOOKUP_STATUS_SUCCESS if count > 0 else LOOKUP_STATUS_NOT_FOUND
        if response_payload.get("data") is not None:
            return LOOKUP_STATUS_SUCCESS
    if status in {"error", "validation_warning"}:
        return LOOKUP_STATUS_BLOCKED
    return LOOKUP_STATUS_NOT_FOUND


def _lookup_failure_code(lookup_status: str) -> str:
    return {
        LOOKUP_STATUS_NOT_FOUND: "domain_pack.validator_lookup_not_found",
        LOOKUP_STATUS_AMBIGUOUS: "domain_pack.validator_lookup_ambiguous",
        LOOKUP_STATUS_TRANSIENT: "domain_pack.validator_lookup_transient",
        LOOKUP_STATUS_BLOCKED: "domain_pack.validator_lookup_blocked",
        LOOKUP_STATUS_UNDER_DEVELOPMENT: (
            "domain_pack.validator_lookup_under_development"
        ),
    }.get(lookup_status, "domain_pack.validator_lookup_unresolved")


def _lookup_failure_severity(
    *,
    binding: ValidatorBinding,
    lookup_status: str,
) -> ValidationFindingSeverity:
    if binding.blocking or lookup_status == LOOKUP_STATUS_BLOCKED:
        return ValidationFindingSeverity.BLOCKER
    if lookup_status in {LOOKUP_STATUS_TRANSIENT, LOOKUP_STATUS_UNDER_DEVELOPMENT}:
        return ValidationFindingSeverity.WARNING
    return ValidationFindingSeverity.ERROR


def _expected_result_field_for_match(match: ValidatorBindingMatch) -> str | None:
    if match.field_definition is None:
        return None
    match_field_path = match.field_definition.field_path
    object_type = match.object_envelope.object_type if match.object_envelope else ""
    for result_field, raw_field_path in match.binding.expected_result_fields.items():
        field_path = _relative_field_path(str(raw_field_path), object_type=object_type)
        if field_path == match_field_path:
            return str(result_field)
    return None


def _lookup_response_result_value(
    response_payload: Mapping[str, Any],
    result_field: str,
) -> Any:
    values = _lookup_response_result_values(response_payload, result_field)
    if values:
        return values[0]
    return _MISSING_LOOKUP_VALUE


def _lookup_response_result_values(
    response_payload: Mapping[str, Any],
    result_field: str,
) -> list[Any]:
    result_value_evidence = response_payload.get("result_value_evidence")
    if isinstance(result_value_evidence, Mapping):
        raw_evidence = result_value_evidence.get(result_field)
        if isinstance(raw_evidence, list):
            values = [
                evidence.get("value")
                for evidence in raw_evidence
                if isinstance(evidence, Mapping)
                and _usable_lookup_result_value(evidence.get("value"))
            ]
            if values:
                return values

    data = response_payload.get("data")
    if isinstance(data, Mapping):
        value = data.get(result_field, _MISSING_LOOKUP_VALUE)
        return [value] if _usable_lookup_result_value(value) else []
    if isinstance(data, list) and len(data) == 1 and isinstance(data[0], Mapping):
        value = data[0].get(result_field, _MISSING_LOOKUP_VALUE)
        return [value] if _usable_lookup_result_value(value) else []
    result_projections = response_payload.get("result_projections")
    if isinstance(result_projections, list) and len(result_projections) == 1:
        projection = result_projections[0]
        if isinstance(projection, Mapping):
            if result_field in projection:
                value = projection[result_field]
                return [value] if _usable_lookup_result_value(value) else []
            resolved_key = f"resolved_{result_field}"
            if resolved_key in projection:
                value = projection[resolved_key]
                return [value] if _usable_lookup_result_value(value) else []
    return []


def _usable_lookup_result_value(value: Any) -> bool:
    return value is not _MISSING_LOOKUP_VALUE and _optional_string(value) is not None


def _latest_supervisor_retry_prompt(details: Mapping[str, Any]) -> str | None:
    retries = details.get("supervisor_retries")
    if not isinstance(retries, list) or not retries:
        return None
    latest_retry = retries[-1]
    if not isinstance(latest_retry, Mapping):
        return None
    return _optional_string(latest_retry.get("prompt"))


def _normalized_lookup_value(value: Any) -> str:
    return str(value).strip()


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
    details = {
        **_match_details(match, provider_model_ref=provider_model_ref),
        "dispatch_unavailable_reason": reason,
    }
    lookup_status = (
        LOOKUP_STATUS_BLOCKED
        if binding.blocking
        else LOOKUP_STATUS_UNDER_DEVELOPMENT
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


def _attach_lookup_attempt_details(
    details: dict[str, Any],
    *,
    match: ValidatorBindingMatch,
    lookup_status: str,
    explanation: str,
) -> None:
    attempt = _lookup_attempt_for_match(
        match=match,
        lookup_status=lookup_status,
        explanation=explanation,
    )
    details["lookup_attempts"] = [attempt]
    details["lookup_explanation"] = explanation
    details["failure_classification"] = lookup_status
    provider_projection = match.binding.provider_projection
    if provider_projection:
        details["provider_projections"] = [dict(provider_projection)]


def _lookup_attempt_for_match(
    *,
    match: ValidatorBindingMatch,
    lookup_status: str,
    explanation: str,
) -> dict[str, Any]:
    binding = match.binding
    attempted_query = {
        "validator_binding_id": binding.binding_id,
        "validation_kind": binding.validation_kind,
    }
    input_fields = _attempt_input_fields(match)
    if input_fields:
        attempted_query["input_fields"] = input_fields
    provider_projection = dict(binding.provider_projection)
    return {
        "source": {
            "validator_binding_id": binding.binding_id,
            "tool_name": binding.tool_name,
            "tool_method": binding.tool_method,
            "validator": binding.validator,
        },
        "provider": provider_projection.get("provider"),
        "target_projection": provider_projection or None,
        "attempted_query": {
            key: value for key, value in attempted_query.items() if value is not None
        },
        "lookup_status": lookup_status,
        "candidate_count": 0,
        "resolved_id": None,
        "resolved_label": None,
        "explanation": explanation,
    }


def _attempt_input_fields(match: ValidatorBindingMatch) -> dict[str, Any]:
    if not match.binding.input_fields:
        return {}
    fields: dict[str, Any] = {}
    for input_name, raw_field_path in match.binding.input_fields.items():
        field_path = str(raw_field_path)
        value = None
        if match.object_envelope is not None:
            value = _payload_value(
                match.object_envelope.payload,
                _relative_field_path(
                    field_path,
                    object_type=match.object_envelope.object_type,
                ),
            )
        fields[str(input_name)] = {
            "field_path": field_path,
            "value": value,
        }
    return fields


def _relative_field_path(field_path: str, *, object_type: str) -> str:
    prefix = f"{object_type}."
    if field_path.startswith(prefix):
        return field_path[len(prefix):]
    return field_path


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


def _history_event_for_finding_status_change(
    *,
    envelope: DomainEnvelope,
    previous_finding: ValidationFinding,
    updated_finding: ValidationFinding,
    superseding_finding: ValidationFinding,
    actor_id: str,
) -> HistoryEvent:
    target = _finding_target_payload(updated_finding)
    seed_payload = {
        "envelope_id": envelope.envelope_id,
        "finding_id": updated_finding.finding_id,
        "previous_status": previous_finding.status.value,
        "updated_status": updated_finding.status.value,
        "superseding_finding_id": superseding_finding.finding_id,
        "event_type": HistoryEventKind.STATUS_CHANGED.value,
        "target": target,
    }
    digest = sha256(json.dumps(seed_payload, sort_keys=True).encode("utf-8")).hexdigest()
    field_ref = updated_finding.field_ref
    if (
        field_ref is not None
        and _finding_field_ref_exists(envelope=envelope, field_ref=field_ref)
    ):
        event_field_ref = field_ref
        event_object_ref = None
    else:
        event_field_ref = None
        event_object_ref = updated_finding.object_ref or (
            updated_finding.field_ref.object_ref
            if updated_finding.field_ref is not None
            else None
        )

    return HistoryEvent(
        event_type=HistoryEventKind.STATUS_CHANGED,
        event_id=f"validation-status-event:{digest}",
        actor_type=HistoryActorType.SYSTEM,
        actor_id=actor_id,
        message=f"Validation finding resolved: {updated_finding.code}",
        object_ref=event_object_ref,
        field_ref=event_field_ref,
        details={
            "finding_id": updated_finding.finding_id,
            "finding_code": updated_finding.code,
            "previous_status": previous_finding.status.value,
            "finding_status": updated_finding.status.value,
            "superseded_by_finding_id": superseding_finding.finding_id,
            "superseded_by_code": superseding_finding.code,
            "superseded_reason": "expected_result_field_resolved",
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
