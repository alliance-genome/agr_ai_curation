"""Objects a package validator has marked as not validatable.

A package validator (``resolve_curation_domain_envelope_validator_by_id``)
may decide that an object cannot be validated at all, for example a record
stored in a format the pack no longer reads. It says so with one open
finding on the object whose ``details`` carry ``not_validatable: true``.
Structural checks and validator-binding dispatch then skip that object, so
the curator sees that one clear finding instead of a pile of
missing-field and missing-selector findings for the same object.
"""

from __future__ import annotations

from collections.abc import Sequence

from src.lib.domain_packs.validation_findings import _history_event_for_resolved_finding
from src.schemas.domain_envelope import (
    CuratableObjectEnvelope,
    DomainEnvelope,
    ValidationFinding,
    ValidationFindingStatus,
)


NOT_VALIDATABLE_DETAIL_KEY = "not_validatable"


def not_validatable_object_keys(envelope: DomainEnvelope) -> frozenset[tuple[str, str]]:
    """Reference keys of objects an open finding marks as not validatable."""

    keys: set[tuple[str, str]] = set()
    for finding in envelope.validation_findings:
        if finding.status is not ValidationFindingStatus.OPEN:
            continue
        if finding.details.get(NOT_VALIDATABLE_DETAIL_KEY) is not True:
            continue
        object_ref = finding.object_ref
        if object_ref is None and finding.field_ref is not None:
            object_ref = finding.field_ref.object_ref
        if object_ref is not None:
            keys.add(object_ref.ref_key())
    return frozenset(keys)


def supersede_not_validatable_findings(
    envelope: DomainEnvelope,
    new_findings: Sequence[ValidationFinding],
    *,
    actor_id: str,
) -> DomainEnvelope:
    """Resolve earlier not-validatable flags a package validator no longer raises.

    Call it with the package validator's fresh findings before appending
    them: an object it no longer flags (e.g. a record re-saved in the current
    format) loses its stale flag, so structural checks and dispatch run for
    it again. An object it still flags keeps its open flag.
    """

    flagged_now = not_validatable_object_keys(
        envelope.model_copy(update={"validation_findings": list(new_findings)})
    )
    findings = []
    resolved = []
    for finding in envelope.validation_findings:
        object_ref = finding.object_ref or (finding.field_ref.object_ref if finding.field_ref else None)
        if (
            finding.status is ValidationFindingStatus.OPEN
            and finding.details.get(NOT_VALIDATABLE_DETAIL_KEY) is True
            and object_ref is not None
            and object_ref.ref_key() not in flagged_now
        ):
            finding = finding.model_copy(update={"status": ValidationFindingStatus.RESOLVED})
            resolved.append(finding)
        findings.append(finding)
    if not resolved:
        return envelope
    return envelope.model_copy(update={
        "validation_findings": findings,
        "history": [
            *envelope.history,
            *(
                _history_event_for_resolved_finding(envelope=envelope, finding=finding, actor_id=actor_id)
                for finding in resolved
            ),
        ],
    })


def is_not_validatable(
    domain_object: CuratableObjectEnvelope | None,
    keys: frozenset[tuple[str, str]],
) -> bool:
    return domain_object is not None and bool(keys.intersection(domain_object.ref_keys()))


__all__ = [
    "NOT_VALIDATABLE_DETAIL_KEY",
    "is_not_validatable",
    "not_validatable_object_keys",
    "supersede_not_validatable_findings",
]
