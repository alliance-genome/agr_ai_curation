"""Deterministic structural checks for domain envelopes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, TYPE_CHECKING

if TYPE_CHECKING:
    from .profile_validation import ProfileValidationContext

from src.schemas.domain_envelope import (
    CuratableObjectEnvelope,
    DomainEnvelope,
    FieldRef,
    ValidationFinding,
    ValidationFindingSeverity,
    field_path_exists,
)

from src.lib.domain_packs.evidence_snapshot import evidence_records
from src.schemas.evidence_workspace import evidence_record_page, evidence_record_section

from .not_validatable import is_not_validatable, not_validatable_object_keys
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
    profile_context: ProfileValidationContext | None = None,
) -> DomainEnvelopeStructuralCheckResult:
    """Append findings for required domain-pack fields missing from the envelope."""

    if profile_context is not None:
        # Required nested properties apply only within their containing object
        # or array element. The closed profile validator owns that semantics;
        # the flat packaged required-field checker below does not.
        from src.lib.curation_workspace.execution_contracts import require_resolved_profile_conformance
        require_resolved_profile_conformance(
            profile_context.profile, profile_context.receipt, envelope.model_dump(mode="json"),
        )
        return DomainEnvelopeStructuralCheckResult(envelope, profile_context.registry, ())
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
    not_validatable = not_validatable_object_keys(envelope)
    findings: list[ValidationFinding] = []
    for domain_object in envelope.extracted_objects:
        object_definition = object_definitions.get(domain_object.object_type)
        if object_definition is None or is_not_validatable(domain_object, not_validatable):
            # A package validator's one "not validatable" finding speaks for the object.
            continue
        for field_definition in object_definition.fields:
            if not field_definition.required:
                continue
            if field_path_exists(domain_object.payload, field_definition.field_path):
                continue
            if _locator_may_be_empty(envelope, domain_object, field_definition):
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


# Evidence-locator fields that the one evidence rule lets be empty when the
# evidence names its file (ALL-1460): a page for a file without page numbers, a
# section for text before a file's first heading.
_FILE_OPTIONAL_LOCATOR_RULES = {
    "page": evidence_record_page,
    "section": evidence_record_section,
}


def _locator_may_be_empty(
    envelope: DomainEnvelope,
    domain_object: CuratableObjectEnvelope,
    field_definition: Any,
) -> bool:
    """A required evidence-locator field may be absent when its evidence names a file.

    Structural: keyed on the field's ``evidence_locator_field`` metadata and the
    stored ``file_id`` of the object's evidence record, the same rule as
    ``evidence_record_page`` / ``evidence_record_section`` everywhere else.
    """

    rule = _FILE_OPTIONAL_LOCATOR_RULES.get(field_definition.field_path)
    if rule is None or field_definition.metadata.get("evidence_locator_field") is not True:
        return False
    evidence_ids = set(domain_object.evidence_record_ids)
    payload_evidence_id = domain_object.payload.get("evidence_record_id")
    if isinstance(payload_evidence_id, str):
        evidence_ids.add(payload_evidence_id)
    records = [
        record
        for record in evidence_records({"metadata": envelope.metadata})
        if record.get("evidence_record_id") in evidence_ids
    ]
    return bool(records) and all(
        rule({field_definition.field_path: None, "file_id": record.get("file_id")})[0]
        for record in records
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
