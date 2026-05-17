"""Deterministic structural checks for domain envelopes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from src.schemas.domain_envelope import (
    CuratableObjectEnvelope,
    DomainEnvelope,
    FieldRef,
    ValidationFinding,
    ValidationFindingSeverity,
    field_path_exists,
)

from .registry import LoadedDomainPack
from .validation_findings import append_validation_findings_to_envelope
from .validation_registry import (
    DomainPackValidationRegistry,
    ValidationBindingState,
)


@dataclass(frozen=True)
class DomainEnvelopeStructuralCheckResult:
    """Result of deterministic structural checks for one domain envelope."""

    envelope: DomainEnvelope
    registry: DomainPackValidationRegistry
    appended_findings: tuple[ValidationFinding, ...]


def run_domain_envelope_structural_checks(
    envelope: DomainEnvelope,
    domain_pack: LoadedDomainPack,
    *,
    actor_id: str = "domain_envelope_structural_checks",
    provider_model_ref: Mapping[str, Any] | None = None,
    registry: DomainPackValidationRegistry | None = None,
) -> DomainEnvelopeStructuralCheckResult:
    """Append findings for required domain-pack fields missing from the envelope."""

    validation_registry = registry or DomainPackValidationRegistry.from_domain_pack(
        domain_pack
    )
    updated_envelope, appended_findings = append_validation_findings_to_envelope(
        envelope,
        _required_field_findings(
            envelope=envelope,
            registry=validation_registry,
            provider_model_ref=provider_model_ref,
        ),
        actor_id=actor_id,
    )
    return DomainEnvelopeStructuralCheckResult(
        envelope=updated_envelope,
        registry=validation_registry,
        appended_findings=appended_findings,
    )


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


def _with_provider_model_ref(
    details: dict[str, Any],
    provider_model_ref: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if provider_model_ref:
        details["provider_model_ref"] = dict(provider_model_ref)
    return details


__all__ = [
    "DomainEnvelopeStructuralCheckResult",
    "run_domain_envelope_structural_checks",
]
