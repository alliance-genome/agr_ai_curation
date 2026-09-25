"""Phenotype domain-pack helpers for pending phenotype assertion envelopes."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from src.schemas.domain_envelope import (
    DomainEnvelope,
    ObjectRef,
    ValidationFinding,
    ValidationFindingSeverity,
)

from .constants import (
    PHENOTYPE_ANNOTATION_KIND,
    PHENOTYPE_ANNOTATION_MODEL_ID,
    PHENOTYPE_ANNOTATION_OBJECT_ROLE,
    PHENOTYPE_DOMAIN_PACK_DIR_NAME,
    PHENOTYPE_DOMAIN_PACK_ID,
    PHENOTYPE_DOMAIN_PACK_VERSION,
    PHENOTYPE_FIXTURE_PACK_ID,
    PHENOTYPE_MATERIALIZER_ID,
    PHENOTYPE_OBJECT_TYPE,
    PHENOTYPE_SUBJECT_OBJECT_TYPE,
    PHENOTYPE_SUBJECT_VALIDATOR_BINDING_ID,
    PHENOTYPE_TERM_OBJECT_TYPE,
    PHENOTYPE_TERM_VALIDATOR_BINDING_ID,
    get_phenotype_domain_pack_metadata_path,
)
from .conversion import (
    PhenotypeBuilderExtractionOutput,
    PhenotypeMaterializationResult,
    materialize_phenotype_builder_state,
    normalize_phenotype_extraction_payload,
    validate_phenotype_builder_objects,
)
from .export import (
    PHENOTYPE_EXPORT_SCHEMA_VERSION,
    PHENOTYPE_EXPORT_TARGET_ID,
    PhenotypeAnnotationExportAdapter,
    build_phenotype_annotation_export_payload,
)
from .submit import (
    PHENOTYPE_REQUIRED_BEFORE_WRITE,
    PHENOTYPE_SUBMISSION_BLOCKED_OPERATIONS,
    PhenotypeAnnotationSubmissionBlockerAdapter,
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


def validate_pending_phenotype_envelope(
    envelope: DomainEnvelope,
) -> tuple[ValidationFinding, ...]:
    """Return domain-pack validation findings for one pending phenotype envelope."""

    findings: list[ValidationFinding] = []
    if envelope.domain_pack_id != PHENOTYPE_DOMAIN_PACK_ID:
        findings.append(
            ValidationFinding(
                severity=ValidationFindingSeverity.ERROR,
                code="alliance.phenotype.domain_pack_mismatch",
                message=(
                    f"Expected domain_pack_id {PHENOTYPE_DOMAIN_PACK_ID}, "
                    f"found {envelope.domain_pack_id}."
                ),
            )
        )

    legacy_keys = _legacy_keys_in_envelope(envelope)
    if legacy_keys:
        findings.append(
            ValidationFinding(
                severity=ValidationFindingSeverity.ERROR,
                code="alliance.phenotype.legacy_semantic_store_present",
                message=(
                    "Phenotype domain envelopes must use envelope objects as the semantic "
                    "source of truth; legacy semantic collections are not allowed."
                ),
                details={"legacy_keys": sorted(legacy_keys)},
            )
        )

    annotations = [
        obj for obj in envelope.extracted_objects if obj.object_type == PHENOTYPE_OBJECT_TYPE
    ]
    if not annotations:
        findings.append(
            ValidationFinding(
                severity=ValidationFindingSeverity.ERROR,
                code="alliance.phenotype.missing_annotation",
                message="Envelope must contain at least one PhenotypeAnnotation object.",
            )
        )

    for annotation in annotations:
        annotation_ref = ObjectRef(
            pending_ref_id=annotation.pending_ref_id,
            object_type=annotation.object_type,
        )
        ref_types = {ref.object_type for ref in annotation.object_refs}
        missing_ref_types = {
            PHENOTYPE_SUBJECT_OBJECT_TYPE,
            PHENOTYPE_TERM_OBJECT_TYPE,
            "Reference",
            "EvidenceQuote",
        } - ref_types
        if missing_ref_types:
            findings.append(
                ValidationFinding(
                    severity=ValidationFindingSeverity.ERROR,
                    code="alliance.phenotype.annotation_refs_missing",
                    message=(
                        "PhenotypeAnnotation is missing object refs: "
                        + ", ".join(sorted(missing_ref_types))
                    ),
                    object_ref=annotation_ref,
                )
            )

        if not _optional_string(
            annotation.payload.get("phenotype_annotation_object"),
            "phenotype_annotation_object",
        ):
            findings.append(
                ValidationFinding(
                    severity=ValidationFindingSeverity.ERROR,
                    code="alliance.phenotype.missing_statement",
                    message="PhenotypeAnnotation requires phenotype_annotation_object.",
                    object_ref=annotation_ref,
                )
            )

        if not _first_phenotype_term_mention(annotation.payload):
            findings.append(
                ValidationFinding(
                    severity=ValidationFindingSeverity.ERROR,
                    code="alliance.phenotype.missing_phenotype_term",
                    message=(
                        "PhenotypeAnnotation requires a first phenotype term with its "
                        "paper wording for ontology resolution."
                    ),
                    object_ref=annotation_ref,
                )
            )

        if not _metadata_status_is_blocked(annotation.metadata, "export_behavior"):
            findings.append(
                ValidationFinding(
                    severity=ValidationFindingSeverity.BLOCKER,
                    code="alliance.phenotype.export_behavior_not_blocked",
                    message="Phenotype annotation export behavior must remain blocked in this pack.",
                    object_ref=annotation_ref,
                )
            )

        if not _metadata_status_is_blocked(annotation.metadata, "write_behavior"):
            findings.append(
                ValidationFinding(
                    severity=ValidationFindingSeverity.BLOCKER,
                    code="alliance.phenotype.write_behavior_not_blocked",
                    message="Phenotype annotation write behavior must remain blocked in this pack.",
                    object_ref=annotation_ref,
                )
            )

        if not _has_finding(
            envelope,
            "alliance.phenotype.export_blocked",
            annotation.pending_ref_id,
        ):
            findings.append(
                ValidationFinding(
                    severity=ValidationFindingSeverity.BLOCKER,
                    code="alliance.phenotype.export_blocker_missing",
                    message="PhenotypeAnnotation must carry an explicit export blocker finding.",
                    object_ref=annotation_ref,
                )
            )

        subject_state = _optional_string(
            annotation.metadata.get("validation_state"),
            "metadata.validation_state",
        )
        if subject_state in {
            "blocked_missing_subject",
            "pending_entity_resolution",
        } and not _has_finding(
            envelope,
            "alliance.phenotype.subject_resolution_required",
            annotation.pending_ref_id,
        ):
            findings.append(
                ValidationFinding(
                    severity=ValidationFindingSeverity.BLOCKER,
                    code="alliance.phenotype.subject_resolution_blocker_missing",
                    message=(
                        "Pending phenotype subject resolution must be represented by "
                        "a blocker finding."
                    ),
                    object_ref=annotation_ref,
                )
            )

    phenotype_terms = [
        obj for obj in envelope.extracted_objects if obj.object_type == PHENOTYPE_TERM_OBJECT_TYPE
    ]
    for phenotype_term in phenotype_terms:
        term_ref = ObjectRef(
            pending_ref_id=phenotype_term.pending_ref_id,
            object_type=phenotype_term.object_type,
        )
        if _optional_string(
            phenotype_term.metadata.get("validation_state"),
            "metadata.validation_state",
        ) != "pending_ontology_resolution":
            continue
        if phenotype_term.metadata.get("export_state") != (
            "blocked_pending_ontology_resolution"
        ):
            findings.append(
                ValidationFinding(
                    severity=ValidationFindingSeverity.BLOCKER,
                    code="alliance.phenotype.term_export_state_not_blocked",
                    message=(
                        "Pending phenotype term export state must block export "
                        "until ontology resolution succeeds."
                    ),
                    object_ref=term_ref,
                )
            )
        write_blocked_reason = phenotype_term.metadata.get("write_blocked_reason")
        if not isinstance(write_blocked_reason, str) or not write_blocked_reason.strip():
            findings.append(
                ValidationFinding(
                    severity=ValidationFindingSeverity.BLOCKER,
                    code="alliance.phenotype.term_write_blocker_missing",
                    message="Pending phenotype term must carry a write blocker reason.",
                    object_ref=term_ref,
                )
            )
        if (
            _optional_string(phenotype_term.payload.get("curie"), "payload.curie")
            is None
            and not _has_finding(
                envelope,
                "alliance.phenotype.ontology_resolution_required",
                phenotype_term.pending_ref_id,
            )
        ):
            findings.append(
                ValidationFinding(
                    severity=ValidationFindingSeverity.BLOCKER,
                    code="alliance.phenotype.ontology_resolution_blocker_missing",
                    message=(
                        "Pending phenotype ontology resolution must be represented "
                        "by a blocker finding."
                    ),
                    object_ref=term_ref,
                )
            )

    return tuple(findings)


def _legacy_keys_in_envelope(envelope: DomainEnvelope) -> set[str]:
    return set(_FORBIDDEN_LEGACY_COLLECTIONS.intersection(envelope.metadata))


def _metadata_status_is_blocked(metadata: Mapping[str, Any], key: str) -> bool:
    behavior = metadata.get(key)
    return isinstance(behavior, Mapping) and behavior.get("status") == "blocked"


def _has_finding(
    envelope: DomainEnvelope,
    code: str,
    pending_ref_id: str | None,
) -> bool:
    for finding in envelope.validation_findings:
        if finding.code != code:
            continue
        if pending_ref_id is None:
            return True
        if (
            finding.object_ref is not None
            and finding.object_ref.pending_ref_id == pending_ref_id
        ):
            return True
    return False


def _first_phenotype_term_mention(payload: Mapping[str, Any]) -> str | None:
    """The paper wording of the first phenotype term (its validated identity may be absent)."""

    terms = payload.get("phenotype_terms")
    if not isinstance(terms, Sequence) or isinstance(terms, (str, bytes, bytearray)):
        return None
    if not terms:
        return None
    first_term = terms[0]
    if not isinstance(first_term, Mapping):
        return None
    return _optional_string(first_term.get("mention"), "phenotype_terms[0].mention")


def _optional_string(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    normalized = value.strip()
    return normalized or None


__all__ = [
    "PHENOTYPE_ANNOTATION_KIND",
    "PHENOTYPE_ANNOTATION_MODEL_ID",
    "PHENOTYPE_ANNOTATION_OBJECT_ROLE",
    "PHENOTYPE_DOMAIN_PACK_DIR_NAME",
    "PHENOTYPE_DOMAIN_PACK_ID",
    "PHENOTYPE_DOMAIN_PACK_VERSION",
    "PHENOTYPE_EXPORT_SCHEMA_VERSION",
    "PHENOTYPE_EXPORT_TARGET_ID",
    "PHENOTYPE_FIXTURE_PACK_ID",
    "PHENOTYPE_MATERIALIZER_ID",
    "PHENOTYPE_OBJECT_TYPE",
    "PHENOTYPE_REQUIRED_BEFORE_WRITE",
    "PHENOTYPE_SUBMISSION_BLOCKED_OPERATIONS",
    "PHENOTYPE_SUBJECT_OBJECT_TYPE",
    "PHENOTYPE_SUBJECT_VALIDATOR_BINDING_ID",
    "PHENOTYPE_TERM_OBJECT_TYPE",
    "PHENOTYPE_TERM_VALIDATOR_BINDING_ID",
    "PhenotypeAnnotationExportAdapter",
    "PhenotypeAnnotationSubmissionBlockerAdapter",
    "PhenotypeBuilderExtractionOutput",
    "PhenotypeMaterializationResult",
    "build_phenotype_annotation_export_payload",
    "get_phenotype_domain_pack_metadata_path",
    "materialize_phenotype_builder_state",
    "normalize_phenotype_extraction_payload",
    "validate_phenotype_builder_objects",
    "validate_pending_phenotype_envelope",
]
