"""Deterministic validator input selector resolution."""

from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Mapping, Sequence

from pydantic import ValidationError

from src.lib.domain_packs.validation_registry import ValidatorBindingMatch
from src.schemas.domain_envelope import (
    CuratableObjectEnvelope,
    DomainEnvelope,
    FieldRef,
    ObjectRef,
    ValidationFinding,
    ValidationFindingSeverity,
    parse_field_path,
)
from src.schemas.domain_pack_metadata import DomainPackInputSelector
from src.schemas.domain_validator import (
    DomainValidationRequest,
    ValidationTarget,
    ValidatorAgentRef,
)


@dataclass(frozen=True)
class SelectorBuildResult:
    """Result of resolving selectors for one validator binding match."""

    request: DomainValidationRequest | None
    findings: tuple[ValidationFinding, ...]
    selected_inputs: dict[str, Any]
    input_selectors: dict[str, dict[str, Any]]
    evidence: list[dict[str, Any]]


@dataclass(frozen=True)
class _SelectorProblem:
    code: str
    input_name: str
    message: str
    selector: DomainPackInputSelector
    details: dict[str, Any]
    field_path: str | None = None


_OPTIONAL_INPUT_MISSING = object()


def build_domain_validation_request(
    match: ValidatorBindingMatch,
) -> SelectorBuildResult:
    """Build a validator request or structured selector findings for one match."""

    binding = match.binding
    problems: list[_SelectorProblem] = []
    selected_inputs: dict[str, Any] = {}
    selectors: dict[str, dict[str, Any]] = {}
    declared_non_literal_inputs = False
    selected_non_literal_inputs = False
    missing_optional_non_literal_inputs = False

    for input_name, selector in binding.input_fields.items():
        selectors[input_name] = _selector_payload(selector)
        if selector.source != "literal" and not selector.context_only:
            declared_non_literal_inputs = True
        value, problem = _resolve_selector(match, input_name, selector)
        if problem is not None:
            problems.append(problem)
            continue
        if value is _OPTIONAL_INPUT_MISSING:
            if selector.source != "literal" and not selector.context_only:
                missing_optional_non_literal_inputs = True
            continue
        if selector.source != "literal" and not selector.context_only:
            selected_non_literal_inputs = True
        selected_inputs[input_name] = value

    if problems:
        return SelectorBuildResult(
            request=None,
            findings=tuple(_problem_finding(match, problem) for problem in problems),
            selected_inputs=selected_inputs,
            input_selectors=selectors,
            evidence=_evidence_records_for_target(match),
        )

    if (
        declared_non_literal_inputs
        and missing_optional_non_literal_inputs
        and not selected_non_literal_inputs
    ):
        return SelectorBuildResult(
            request=None,
            findings=(),
            selected_inputs=selected_inputs,
            input_selectors=selectors,
            evidence=_evidence_records_for_target(match),
        )

    if binding.validator_agent is None:
        return SelectorBuildResult(
            request=None,
            findings=(),
            selected_inputs=selected_inputs,
            input_selectors=selectors,
            evidence=_evidence_records_for_target(match),
        )

    target = _validation_target(match, selected_inputs)
    request_payload = {
        "validator_binding_id": binding.binding_id,
        "validator_agent": binding.validator_agent.to_dict(),
        "target": target.model_dump(mode="json", exclude_none=True),
        "selected_inputs": selected_inputs,
        "expected_result_fields": binding.expected_result_fields,
    }
    request_id = (
        "domain-validation:"
        + sha256(
            json.dumps(request_payload, sort_keys=True).encode("utf-8")
        ).hexdigest()
    )

    return SelectorBuildResult(
        request=DomainValidationRequest(
            request_id=request_id,
            validator_binding_id=binding.binding_id,
            validator_agent=ValidatorAgentRef.model_validate(
                binding.validator_agent.to_dict()
            ),
            target=target,
            selected_inputs=selected_inputs,
            input_selectors=selectors,
            evidence=_evidence_records_for_target(match),
            expected_result_fields=dict(binding.expected_result_fields),
        ),
        findings=(),
        selected_inputs=selected_inputs,
        input_selectors=selectors,
        evidence=_evidence_records_for_target(match),
    )


def _selector_payload(selector: DomainPackInputSelector) -> dict[str, Any]:
    return selector.model_dump(mode="json", exclude_none=True)


def _resolve_selector(
    match: ValidatorBindingMatch,
    input_name: str,
    selector: DomainPackInputSelector,
) -> tuple[Any, _SelectorProblem | None]:
    if selector.source == "literal":
        return selector.value, None

    if selector.source == "payload":
        if match.object_envelope is None:
            return _missing(
                input_name, selector, "payload selectors require an object target"
            )
        value, exists = _value_at_path(
            match.object_envelope.payload, selector.path or ""
        )
        if not exists:
            return _missing_field(
                input_name,
                selector,
                f"Payload path '{selector.path}' is missing from the target object.",
                field_path=selector.path,
            )
        return _single_value(input_name, selector, value, field_path=selector.path)

    if selector.source == "object_metadata":
        if match.object_envelope is None:
            return _missing(
                input_name,
                selector,
                "object_metadata selectors require an object target",
            )
        value, exists = _value_at_path(
            match.object_envelope.metadata, selector.path or ""
        )
        if not exists:
            return _missing_field(
                input_name,
                selector,
                f"Object metadata path '{selector.path}' is missing from the target object.",
            )
        return _single_value(input_name, selector, value)

    if selector.source == "envelope_metadata":
        value, exists = _value_at_path(match.envelope.metadata, selector.path or "")
        if not exists:
            return _missing_field(
                input_name,
                selector,
                f"Envelope metadata path '{selector.path}' is missing.",
            )
        return _single_value(input_name, selector, value)

    if selector.source == "evidence_record":
        return _resolve_evidence_record_selector(match, input_name, selector)

    if selector.source == "object_ref":
        return _resolve_object_ref_selector(match, input_name, selector)

    return _missing(
        input_name, selector, f"Unsupported selector source '{selector.source}'"
    )


def _resolve_evidence_record_selector(
    match: ValidatorBindingMatch,
    input_name: str,
    selector: DomainPackInputSelector,
) -> tuple[Any, _SelectorProblem | None]:
    if match.object_envelope is None:
        return _missing(
            input_name, selector, "evidence_record selectors require an object target"
        )

    records = _candidate_evidence_records(match.envelope, match.object_envelope)
    if selector.record_id is not None:
        records = tuple(
            record for record in records if _record_id(record) == selector.record_id
        )

    if not records:
        return _missing(
            input_name,
            selector,
            "No evidence record resolved for selector.",
            details={"record_id": selector.record_id},
        )
    if len(records) > 1:
        return _ambiguous(
            input_name,
            selector,
            "Evidence selector matched multiple records.",
            details={"record_ids": [_record_id(record) for record in records]},
        )

    value, exists = _value_at_path(records[0], selector.path or "")
    if not exists:
        return _missing_field(
            input_name,
            selector,
            f"Evidence record path '{selector.path}' is missing.",
        )
    return _single_value(input_name, selector, value)


def _resolve_object_ref_selector(
    match: ValidatorBindingMatch,
    input_name: str,
    selector: DomainPackInputSelector,
) -> tuple[Any, _SelectorProblem | None]:
    if match.object_envelope is None:
        return _missing(
            input_name, selector, "object_ref selectors require an object target"
        )

    ref_candidates = _object_ref_candidates(match.object_envelope, selector)
    if not ref_candidates:
        return _missing(
            input_name,
            selector,
            "No object ref resolved for selector.",
            details={
                "object_type": selector.object_type,
                "field_path": selector.field_path,
            },
            field_path=selector.field_path,
        )
    if len(ref_candidates) > 1:
        return _ambiguous(
            input_name,
            selector,
            "Object ref selector matched multiple refs.",
            details={
                "refs": [
                    ref.model_dump(mode="json", exclude_none=True)
                    for ref in ref_candidates
                ]
            },
            field_path=selector.field_path,
        )

    ref = ref_candidates[0]
    referenced_object = _object_for_ref(match.envelope, ref)
    if referenced_object is None:
        return _unresolved_ref(
            input_name,
            selector,
            "Object ref selector resolved a ref that is not present in the envelope.",
            details={"ref": ref.model_dump(mode="json", exclude_none=True)},
            field_path=selector.field_path,
        )

    if selector.path is None:
        return ref.model_dump(mode="json", exclude_none=True), None

    value, exists = _value_at_path(referenced_object.payload, selector.path)
    if not exists:
        return _missing_field(
            input_name,
            selector,
            f"Referenced object payload path '{selector.path}' is missing.",
            field_path=selector.field_path,
        )
    return _single_value(input_name, selector, value, field_path=selector.field_path)


def _object_ref_candidates(
    domain_object: CuratableObjectEnvelope,
    selector: DomainPackInputSelector,
) -> tuple[ObjectRef, ...]:
    refs: list[ObjectRef] = []
    if selector.field_path is not None:
        raw_ref, exists = _value_at_path(domain_object.payload, selector.field_path)
        if exists:
            raw_refs = raw_ref if isinstance(raw_ref, list) else [raw_ref]
            for item in raw_refs:
                ref = _coerce_object_ref(item)
                if ref is not None:
                    refs.append(ref)
    else:
        refs.extend(domain_object.object_refs)

    if selector.object_type is not None:
        refs = [ref for ref in refs if ref.object_type == selector.object_type]
    return tuple(refs)


def _coerce_object_ref(raw_ref: Any) -> ObjectRef | None:
    if isinstance(raw_ref, ObjectRef):
        return raw_ref
    if not isinstance(raw_ref, Mapping):
        return None
    try:
        return ObjectRef.model_validate(raw_ref)
    except ValidationError:
        return None


def _object_for_ref(
    envelope: DomainEnvelope,
    object_ref: ObjectRef,
) -> CuratableObjectEnvelope | None:
    ref_key = object_ref.ref_key()
    for domain_object in envelope.objects:
        if ref_key in domain_object.ref_keys():
            return domain_object
    return None


def _single_value(
    input_name: str,
    selector: DomainPackInputSelector,
    value: Any,
    *,
    field_path: str | None = None,
) -> tuple[Any, _SelectorProblem | None]:
    if value is None:
        if not selector.required:
            return _OPTIONAL_INPUT_MISSING, None
        return _missing(
            input_name,
            selector,
            "Selector resolved a null value.",
            field_path=field_path,
        )
    if isinstance(value, list):
        if not value:
            return _missing(
                input_name,
                selector,
                "Selector resolved an empty list.",
                field_path=field_path,
            )
        if selector.allow_multiple is True:
            return value, None
        if len(value) > 1:
            return _ambiguous(
                input_name,
                selector,
                "Selector resolved multiple values.",
                details={"value_count": len(value)},
                field_path=field_path,
            )
        return value[0], None
    return value, None


def _missing(
    input_name: str,
    selector: DomainPackInputSelector,
    message: str,
    *,
    details: dict[str, Any] | None = None,
    field_path: str | None = None,
) -> tuple[Any, _SelectorProblem | None]:
    if not selector.required:
        return _OPTIONAL_INPUT_MISSING, None
    return (
        None,
        _SelectorProblem(
            code="selector_missing",
            input_name=input_name,
            message=message,
            selector=selector,
            details=details or {},
            field_path=field_path,
        ),
    )


def _missing_field(
    input_name: str,
    selector: DomainPackInputSelector,
    message: str,
    *,
    field_path: str | None = None,
) -> tuple[Any, _SelectorProblem | None]:
    if not selector.required:
        return _OPTIONAL_INPUT_MISSING, None
    return (
        None,
        _SelectorProblem(
            code="selector_missing_field",
            input_name=input_name,
            message=message,
            selector=selector,
            details={},
            field_path=field_path,
        ),
    )


def _ambiguous(
    input_name: str,
    selector: DomainPackInputSelector,
    message: str,
    *,
    details: dict[str, Any],
    field_path: str | None = None,
) -> tuple[Any, _SelectorProblem]:
    return (
        None,
        _SelectorProblem(
            code="selector_ambiguous",
            input_name=input_name,
            message=message,
            selector=selector,
            details=details,
            field_path=field_path,
        ),
    )


def _unresolved_ref(
    input_name: str,
    selector: DomainPackInputSelector,
    message: str,
    *,
    details: dict[str, Any],
    field_path: str | None = None,
) -> tuple[Any, _SelectorProblem]:
    return (
        None,
        _SelectorProblem(
            code="selector_unresolved_ref",
            input_name=input_name,
            message=message,
            selector=selector,
            details=details,
            field_path=field_path,
        ),
    )


def _problem_finding(
    match: ValidatorBindingMatch,
    problem: _SelectorProblem,
) -> ValidationFinding:
    object_ref = (
        match.object_envelope.to_object_ref() if match.object_envelope else None
    )
    field_ref = (
        FieldRef(object_ref=object_ref, field_path=problem.field_path)
        if object_ref is not None and problem.field_path is not None
        else None
    )
    return ValidationFinding(
        severity=ValidationFindingSeverity.ERROR,
        code=problem.code,
        message=(
            f"Validator binding '{match.binding.binding_id}' input "
            f"'{problem.input_name}' could not be selected: {problem.message}"
        ),
        object_ref=object_ref if field_ref is None else None,
        field_ref=field_ref,
        details={
            "validation_metadata": {
                **match.binding.identity_details(),
                "target": match.target_details(),
            },
            "selector_problem": {
                "code": problem.code,
                "input_name": problem.input_name,
                "selector": problem.selector.model_dump(mode="json", exclude_none=True),
                **problem.details,
            },
        },
    )


def _validation_target(
    match: ValidatorBindingMatch,
    selected_inputs: dict[str, Any],
) -> ValidationTarget:
    details = match.target_details()
    object_id = details.get("object_id") or details.get("pending_ref_id")
    return ValidationTarget(
        domain_pack_id=match.envelope.domain_pack_id,
        object_type=match.object_type,
        object_id=object_id,
        object_role=details.get("object_role"),
        field_path=match.field_path,
        expected_fields=list(match.binding.expected_result_fields),
        input_values=selected_inputs,
    )


def _evidence_records_for_target(match: ValidatorBindingMatch) -> list[dict[str, Any]]:
    if match.object_envelope is None:
        return []
    return [
        dict(record)
        for record in _candidate_evidence_records(match.envelope, match.object_envelope)
    ]


def _candidate_evidence_records(
    envelope: DomainEnvelope,
    domain_object: CuratableObjectEnvelope,
) -> tuple[Mapping[str, Any], ...]:
    raw_records = [
        *_records_from_mapping(domain_object.payload),
        *_records_from_mapping(domain_object.metadata),
        *_records_from_mapping(envelope.metadata),
    ]
    if not domain_object.evidence_record_ids:
        return tuple(raw_records)

    by_id = {
        record_id: record
        for record in raw_records
        if (record_id := _record_id(record)) is not None
    }
    return tuple(
        by_id[record_id]
        for record_id in domain_object.evidence_record_ids
        if record_id in by_id
    )


def _records_from_mapping(container: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    raw_records = container.get("evidence_records")
    if not isinstance(raw_records, list):
        return []
    return [record for record in raw_records if isinstance(record, Mapping)]


def _record_id(record: Mapping[str, Any]) -> str | None:
    value = record.get("evidence_record_id")
    return value if isinstance(value, str) and value else None


def _value_at_path(container: Mapping[str, Any], field_path: str) -> tuple[Any, bool]:
    current: Any = container
    for part in parse_field_path(field_path):
        if isinstance(part, str):
            if not isinstance(current, Mapping) or part not in current:
                return None, False
            current = current[part]
            continue
        if (
            not isinstance(current, Sequence)
            or isinstance(current, (str, bytes, bytearray))
            or part >= len(current)
        ):
            return None, False
        current = current[part]
    return current, True
