"""Shared export verification derived from runtime validator findings."""

from __future__ import annotations

from collections.abc import Mapping

from src.schemas.domain_envelope import (
    DomainEnvelope,
    ValidationFindingStatus,
)

from .materialization import stable_object_id


RUNTIME_VALIDATOR_RESOLVED_FINDING_CODE = "domain_pack.validator_resolved"


def runtime_validator_resolved_object_ids(
    envelope: DomainEnvelope,
    *,
    validator_binding_id: str,
) -> set[str]:
    """Return object IDs verified by one resolved runtime validator binding."""

    object_ids_by_ref = {
        ref_key: stable_object_id(domain_object)
        for domain_object in envelope.extracted_objects
        for ref_key in domain_object.ref_keys()
    }
    resolved_object_ids: set[str] = set()
    for finding in envelope.validation_findings:
        if (
            finding.code != RUNTIME_VALIDATOR_RESOLVED_FINDING_CODE
            or finding.status is not ValidationFindingStatus.RESOLVED
        ):
            continue
        validation_metadata = finding.details.get("validation_metadata")
        if not isinstance(validation_metadata, Mapping):
            continue
        if validation_metadata.get("validator_binding_id") != validator_binding_id:
            continue
        object_ref = (
            finding.field_ref.object_ref
            if finding.field_ref is not None
            else finding.object_ref
        )
        if object_ref is None:
            continue
        object_id = object_ids_by_ref.get(object_ref.ref_key())
        if object_id is not None:
            resolved_object_ids.add(object_id)
    return resolved_object_ids


__all__ = [
    "RUNTIME_VALIDATOR_RESOLVED_FINDING_CODE",
    "runtime_validator_resolved_object_ids",
]
