"""Allele domain-pack helpers for pending paper/evidence envelopes."""

from __future__ import annotations

from collections.abc import Mapping

from src.lib.domain_packs.resolvable_values import (
    RESOLVED,
    effective_resolution,
    value_covered_by_validator,
)
from src.schemas.domain_envelope import (
    DomainEnvelope,
    ObjectRef,
    ValidationFinding,
    ValidationFindingSeverity,
)
from .constants import (
    ALLELE_ASSOCIATION_IDENTITY_KEYS,
    ALLELE_ASSOCIATION_KIND,
    ALLELE_ASSOCIATION_SPEC,
    ALLELE_ASSOCIATION_MODEL_ID,
    ALLELE_ASSOCIATION_OBJECT_ROLE,
    ALLELE_ASSOCIATION_OBJECT_TYPE,
    ALLELE_DOMAIN_PACK_ID,
    ALLELE_DOMAIN_PACK_VERSION,
    ALLELE_EVIDENCE_QUOTE_OBJECT_TYPE,
    ALLELE_MATERIALIZER_ID,
    ALLELE_MENTION_OBJECT_TYPE,
    ALLELE_MENTION_REFERENCE_VALIDATOR_BINDING_ID,
    ALLELE_REFERENCE_OBJECT_TYPE,
)
from .conversion import (
    AlleleBuilderExtractionOutput,
    AlleleMaterializationResult,
    materialize_allele_builder_state,
    validate_allele_builder_objects,
)
from .export import (
    AllelePaperEvidenceExportAdapter,
    build_allele_association_export,
)
from .submit import (
    ALLELE_ASSOCIATION_SUBMISSION_TARGET_KEY,
    AllelePaperEvidenceSubmissionAdapter,
    VERIFIED_ALLELE_ASSOCIATION_TARGETS,
    build_allele_association_submission_plan,
)

_FORBIDDEN_LEGACY_COLLECTIONS = frozenset(
    {
        "items",
        "annotations",
        "genes",
        "alleles",
        "diseases",
        "chemicals",
        "phenotypes",
        "CurationPrepCandidate",
        "NormalizedCandidate",
        "normalized_payload",
        "annotation_drafts",
    }
)


def validate_pending_allele_envelope(
    envelope: DomainEnvelope,
) -> tuple[ValidationFinding, ...]:
    """Return domain-pack validation findings for one pending allele envelope."""

    findings: list[ValidationFinding] = []
    if envelope.domain_pack_id != ALLELE_DOMAIN_PACK_ID:
        findings.append(
            ValidationFinding(
                severity=ValidationFindingSeverity.ERROR,
                code="alliance.allele.domain_pack_mismatch",
                message=(
                    f"Expected domain_pack_id {ALLELE_DOMAIN_PACK_ID}, "
                    f"found {envelope.domain_pack_id}."
                ),
            )
        )

    legacy_keys = _legacy_keys_in_envelope(envelope)
    if legacy_keys:
        findings.append(
            ValidationFinding(
                severity=ValidationFindingSeverity.ERROR,
                code="alliance.allele.legacy_semantic_store_present",
                message=(
                    "Allele domain envelopes must use envelope objects as the semantic "
                    "source of truth; legacy semantic collections are not allowed."
                ),
                details={"legacy_keys": sorted(legacy_keys)},
            )
        )

    associations = [
        obj
        for obj in envelope.extracted_objects
        if obj.object_type == "AllelePaperEvidenceAssociation"
    ]
    if not associations:
        findings.append(
            ValidationFinding(
                severity=ValidationFindingSeverity.ERROR,
                code="alliance.allele.missing_association",
                message="Envelope must contain at least one AllelePaperEvidenceAssociation object.",
            )
        )

    for association in associations:
        ref_types = {ref.object_type for ref in association.object_refs}
        missing_ref_types = {
            "Reference",
            "AlleleMention",
            "EvidenceQuote",
        } - ref_types
        if missing_ref_types:
            findings.append(
                ValidationFinding(
                    severity=ValidationFindingSeverity.ERROR,
                    code="alliance.allele.association_refs_missing",
                    message=(
                        "AllelePaperEvidenceAssociation is missing object refs: "
                        + ", ".join(sorted(missing_ref_types))
                    ),
                    object_ref=ObjectRef(
                        pending_ref_id=association.pending_ref_id,
                        object_type=association.object_type,
                    )
                    if association.pending_ref_id
                    else None,
                )
            )

        # An identity the validator did not write (it is resolved only through validator
        # write-back, or, for an association stored before ALL-1283, a covering write-back event).
        if association.payload.get("allele_identifier") and effective_resolution(
            association.payload,
            identity_keys=ALLELE_ASSOCIATION_IDENTITY_KEYS,
            covered_by_validator=value_covered_by_validator(
            association.metadata, "", ALLELE_ASSOCIATION_SPEC
        ),
        )[0] != RESOLVED:
            findings.append(
                ValidationFinding(
                    severity=ValidationFindingSeverity.ERROR,
                    code="alliance.allele.extractor_owned_identity_present",
                    message=(
                        "Pending allele associations must leave allele_identifier "
                        "for the active allele validator to resolve."
                    ),
                    object_ref=ObjectRef(
                        pending_ref_id=association.pending_ref_id,
                        object_type=association.object_type,
                    )
                    if association.pending_ref_id
                    else None,
                )
            )

        write_behavior = association.metadata.get("write_behavior")
        if (
            not isinstance(write_behavior, Mapping)
            or write_behavior.get("status") != "blocked"
        ):
            findings.append(
                ValidationFinding(
                    severity=ValidationFindingSeverity.BLOCKER,
                    code="alliance.allele.write_behavior_not_blocked",
                    message="Allele association write behavior must remain blocked in this pack.",
                    object_ref=ObjectRef(
                        pending_ref_id=association.pending_ref_id,
                        object_type=association.object_type,
                    )
                    if association.pending_ref_id
                    else None,
                )
            )

        export_behavior = association.metadata.get("export_behavior")
        if (
            not isinstance(export_behavior, Mapping)
            or export_behavior.get("status") != "blocked"
        ):
            findings.append(
                ValidationFinding(
                    severity=ValidationFindingSeverity.BLOCKER,
                    code="alliance.allele.export_behavior_not_blocked",
                    message="Allele association export behavior must remain blocked until targets resolve.",
                    object_ref=ObjectRef(
                        pending_ref_id=association.pending_ref_id,
                        object_type=association.object_type,
                    )
                    if association.pending_ref_id
                    else None,
                )
            )

    return tuple(findings)


def _legacy_keys_in_envelope(envelope: DomainEnvelope) -> set[str]:
    return set(_FORBIDDEN_LEGACY_COLLECTIONS.intersection(envelope.metadata))


__all__ = [
    "ALLELE_ASSOCIATION_KIND",
    "ALLELE_ASSOCIATION_MODEL_ID",
    "ALLELE_ASSOCIATION_OBJECT_ROLE",
    "ALLELE_ASSOCIATION_OBJECT_TYPE",
    "ALLELE_ASSOCIATION_SUBMISSION_TARGET_KEY",
    "ALLELE_DOMAIN_PACK_ID",
    "ALLELE_DOMAIN_PACK_VERSION",
    "ALLELE_EVIDENCE_QUOTE_OBJECT_TYPE",
    "ALLELE_MATERIALIZER_ID",
    "ALLELE_MENTION_OBJECT_TYPE",
    "ALLELE_MENTION_REFERENCE_VALIDATOR_BINDING_ID",
    "ALLELE_REFERENCE_OBJECT_TYPE",
    "AlleleBuilderExtractionOutput",
    "AlleleMaterializationResult",
    "AllelePaperEvidenceExportAdapter",
    "AllelePaperEvidenceSubmissionAdapter",
    "VERIFIED_ALLELE_ASSOCIATION_TARGETS",
    "build_allele_association_export",
    "build_allele_association_submission_plan",
    "materialize_allele_builder_state",
    "validate_allele_builder_objects",
    "validate_pending_allele_envelope",
]
