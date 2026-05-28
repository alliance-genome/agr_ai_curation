"""Phenotype domain-pack helpers for pending phenotype assertion envelopes."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from src.schemas.domain_envelope import (
    CuratableObjectEnvelope,
    CuratableObjectStatus,
    DefinitionState,
    DomainEnvelope,
    DomainEnvelopeStatus,
    HistoryActorType,
    HistoryEvent,
    HistoryEventKind,
    ObjectRef,
    SchemaRef,
    ValidationFinding,
    ValidationFindingSeverity,
)

from ..schema_refs import ALLIANCE_LINKML_COMMIT, ALLIANCE_LINKML_PROVIDER_KEY
from .constants import (
    PHENOTYPE_CORE_SCHEMA_SOURCE_FILE,
    PHENOTYPE_DOMAIN_PACK_DIR_NAME,
    PHENOTYPE_DOMAIN_PACK_ID,
    PHENOTYPE_DOMAIN_PACK_VERSION,
    PHENOTYPE_FIXTURE_PACK_ID,
    PHENOTYPE_LINKML_SCHEMA_SOURCE_FILE,
    PHENOTYPE_OBJECT_TYPE,
    PHENOTYPE_ONTOLOGY_TERM_SCHEMA_SOURCE_FILE,
    PHENOTYPE_PENDING_ENVELOPE_VALIDATOR_BINDING_ID,
    PHENOTYPE_REFERENCE_SCHEMA_SOURCE_FILE,
    PHENOTYPE_SUBJECT_OBJECT_TYPE,
    PHENOTYPE_SUBJECT_VALIDATOR_BINDING_ID,
    PHENOTYPE_TERM_OBJECT_TYPE,
    PHENOTYPE_TERM_VALIDATOR_BINDING_ID,
    get_phenotype_domain_pack_metadata_path,
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


def build_pending_phenotype_envelope_from_tool_verified_fixture(
    fixture: Mapping[str, Any],
    *,
    envelope_id: str = "phenotype-tool-verified-envelope",
    created_at: datetime | None = None,
) -> DomainEnvelope:
    """Convert the tool-verified phenotype fixture into pending envelope objects."""

    timestamp = created_at or datetime.now(timezone.utc)
    extraction = _required_mapping(fixture.get("extraction"), "extraction")
    paper = _required_mapping(fixture.get("paper"), "paper")
    case_lookup = _tool_cases_by_id(fixture)

    reference_ref_id = "paper-reference-1"
    reference_object = CuratableObjectEnvelope(
        object_type="Reference",
        pending_ref_id=reference_ref_id,
        schema_ref=_reference_schema_ref(),
        status=CuratableObjectStatus.PENDING,
        definition_state=DefinitionState.IN_DEVELOPMENT,
        payload={
            "title": _optional_string(paper.get("title"), "paper.title"),
            "filename": _optional_string(paper.get("filename"), "paper.filename"),
        },
        metadata={
            "object_role": "validated_reference",
            "validation_state": "pending_reference_resolution",
            "validator_binding_id": "phenotype_reference_validator",
        },
    )
    objects: list[CuratableObjectEnvelope] = [reference_object]
    validation_findings: list[ValidationFinding] = []
    evidence_records_by_id: dict[str, Mapping[str, Any]] = {}

    retained_count = 0
    skipped_without_evidence = 0
    for raw_item in _required_sequence(extraction.get("items"), "extraction.items"):
        item = _required_mapping(raw_item, "extraction.items[]")
        evidence_records = _evidence_records_for_item(item, case_lookup)
        if not evidence_records:
            skipped_without_evidence += 1
            continue

        retained_count += 1
        label = _required_string(item.get("label"), "extraction.items[].label")
        normalized_id = _optional_string(
            item.get("normalized_id"),
            "extraction.items[].normalized_id",
        )
        source_mentions = _source_mentions(item)
        negated = _optional_bool(item.get("negated"), "extraction.items[].negated")
        subject_payload = _subject_payload(item)
        subject_resolution_state = subject_payload["resolution_state"]
        ontology_lookup_hint = _ontology_lookup_hint(item, evidence_records)
        primary_evidence_record_id = ontology_lookup_hint["evidence_record_id"]
        term_evidence_record_ids = [primary_evidence_record_id]
        for raw_record in evidence_records:
            record_id = _required_string(
                raw_record.get("evidence_record_id"),
                "evidence_records[].evidence_record_id",
            )
            evidence_records_by_id.setdefault(record_id, raw_record)

        subject_ref_id = f"phenotype-subject-{retained_count}"
        phenotype_term_ref_id = f"phenotype-term-{retained_count}"
        annotation_ref_id = f"phenotype-annotation-{retained_count}"
        evidence_refs: list[ObjectRef] = []
        evidence_record_ids: list[str] = []
        evidence_payload_refs: list[dict[str, str]] = []

        objects.append(
            CuratableObjectEnvelope(
                object_type=PHENOTYPE_SUBJECT_OBJECT_TYPE,
                pending_ref_id=subject_ref_id,
                schema_ref=_phenotype_subject_schema_ref(),
                status=CuratableObjectStatus.PENDING,
                definition_state=DefinitionState.IN_DEVELOPMENT,
                definition_notes=[
                    "Pending subject reference; concrete Gene, Allele, or AGM subtype must be resolved before export."
                ],
                payload=subject_payload,
                metadata={
                    "object_role": "validated_reference",
                    "validation_state": subject_resolution_state,
                    "validator_binding_id": PHENOTYPE_SUBJECT_VALIDATOR_BINDING_ID,
                },
            )
        )
        objects.append(
            CuratableObjectEnvelope(
                object_type=PHENOTYPE_TERM_OBJECT_TYPE,
                pending_ref_id=phenotype_term_ref_id,
                schema_ref=_phenotype_term_schema_ref(),
                status=CuratableObjectStatus.PENDING,
                definition_state=DefinitionState.IN_DEVELOPMENT,
                payload={
                    "resolution_state": "pending_ontology_resolution",
                    "curie": normalized_id,
                    "label": label,
                    "source_mentions": source_mentions,
                    "ontology_lookup_hint": ontology_lookup_hint,
                    "export_state": "blocked_pending_ontology_resolution",
                    "write_blocked_reason": "phenotype term CURIE unresolved",
                },
                evidence_record_ids=term_evidence_record_ids,
                metadata={
                    "object_role": "validated_reference",
                    "validation_state": "pending_ontology_resolution",
                    "validator_binding_id": PHENOTYPE_TERM_VALIDATOR_BINDING_ID,
                    "export_state": "blocked_pending_ontology_resolution",
                    "write_blocked_reason": "phenotype term CURIE unresolved",
                },
            )
        )

        for evidence_index, raw_record in enumerate(evidence_records, start=1):
            record = _required_mapping(raw_record, "evidence_records[]")
            evidence_record_id = _required_string(
                record.get("evidence_record_id"),
                "evidence_records[].evidence_record_id",
            )
            evidence_ref_id = f"evidence-quote-{retained_count}-{evidence_index}"
            evidence_record_ids.append(evidence_record_id)
            evidence_refs.append(
                ObjectRef(pending_ref_id=evidence_ref_id, object_type="EvidenceQuote")
            )
            evidence_payload_refs.append({"evidence_record_id": evidence_record_id})
            objects.append(
                CuratableObjectEnvelope(
                    object_type="EvidenceQuote",
                    pending_ref_id=evidence_ref_id,
                    status=CuratableObjectStatus.PENDING,
                    definition_state=DefinitionState.IN_DEVELOPMENT,
                    payload=_evidence_quote_payload(record, evidence_record_id),
                    metadata={"object_role": "metadata_only"},
                )
            )

        annotation_ref = ObjectRef(
            pending_ref_id=annotation_ref_id,
            object_type=PHENOTYPE_OBJECT_TYPE,
        )
        annotation_object = CuratableObjectEnvelope(
            object_type=PHENOTYPE_OBJECT_TYPE,
            pending_ref_id=annotation_ref_id,
            schema_ref=_phenotype_annotation_schema_ref(),
            status=CuratableObjectStatus.PENDING,
            definition_state=DefinitionState.IN_DEVELOPMENT,
            definition_notes=[
                "Pending only; export is blocked until subject, reference, ontology, and write targets are resolved."
            ],
            payload={
                "annotation_kind": "phenotype_assertion",
                "phenotype_annotation_object": label,
                "phenotype_annotation_subject": deepcopy(subject_payload),
                "phenotype_terms": [
                    {
                        "resolution_state": "pending_ontology_resolution",
                        "curie": normalized_id,
                        "label": label,
                        "source_mentions": source_mentions,
                        "ontology_lookup_hint": ontology_lookup_hint,
                        "export_state": "blocked_pending_ontology_resolution",
                        "write_blocked_reason": "phenotype term CURIE unresolved",
                    }
                ],
                "single_reference": {
                    "title": _optional_string(paper.get("title"), "paper.title"),
                    "filename": _optional_string(paper.get("filename"), "paper.filename"),
                },
                "evidence_quote": evidence_payload_refs[0],
                "evidence_record_ids": evidence_record_ids,
                "source_mentions": source_mentions,
                "negated": bool(negated) if negated is not None else False,
            },
            object_refs=[
                ObjectRef(
                    pending_ref_id=subject_ref_id,
                    object_type=PHENOTYPE_SUBJECT_OBJECT_TYPE,
                ),
                ObjectRef(
                    pending_ref_id=phenotype_term_ref_id,
                    object_type=PHENOTYPE_TERM_OBJECT_TYPE,
                ),
                ObjectRef(pending_ref_id=reference_ref_id, object_type="Reference"),
                *evidence_refs,
            ],
            metadata={
                "object_role": "curatable_unit",
                "validation_state": subject_resolution_state,
                "export_behavior": _blocked_export_behavior(),
                "write_behavior": _blocked_write_behavior(),
            },
        )
        objects.append(annotation_object)
        validation_findings.extend(
            _blocker_findings_for_annotation(
                annotation_ref=annotation_ref,
                phenotype_term_ref=ObjectRef(
                    pending_ref_id=phenotype_term_ref_id,
                    object_type=PHENOTYPE_TERM_OBJECT_TYPE,
                ),
                subject_resolution_state=subject_resolution_state,
                phenotype_term_curie=normalized_id,
            )
        )

    if skipped_without_evidence:
        validation_findings.append(
            ValidationFinding(
                severity=ValidationFindingSeverity.WARNING,
                code="alliance.phenotype.skipped_without_verified_evidence",
                message=(
                    f"Skipped {skipped_without_evidence} phenotype candidate(s) "
                    "without verified evidence records."
                ),
                details={"skipped_without_evidence": skipped_without_evidence},
            )
        )

    return DomainEnvelope(
        envelope_id=envelope_id,
        domain_pack_id=PHENOTYPE_DOMAIN_PACK_ID,
        domain_pack_version=PHENOTYPE_DOMAIN_PACK_VERSION,
        status=DomainEnvelopeStatus.EXTRACTED,
        schema_ref=SchemaRef(
            schema_id="agr.alliance.phenotype.domain_pack",
            provider="domain-pack",
            name="Alliance Phenotype Domain Pack",
            version=PHENOTYPE_DOMAIN_PACK_VERSION,
            definition_state=DefinitionState.IN_DEVELOPMENT,
        ),
        objects=objects,
        validation_findings=validation_findings,
        history=[
            HistoryEvent(
                event_type=HistoryEventKind.CREATED,
                timestamp=timestamp,
                actor_type=HistoryActorType.SYSTEM,
                message=(
                    "Converted tool-verified phenotype fixture to a pending domain envelope."
                ),
                details={
                    "retained_phenotype_count": retained_count,
                    "skipped_without_evidence": skipped_without_evidence,
                },
            )
        ],
        metadata={
            "source_fixture_id": _optional_string(
                fixture.get("fixture_id"),
                "fixture_id",
            ),
            "semantic_source": "domain_envelope.objects",
            "export_behavior": {"status": "blocked"},
            "write_behavior": {"status": "blocked"},
            "evidence_records": [
                _evidence_quote_payload(record, record_id)
                for record_id, record in evidence_records_by_id.items()
            ],
        },
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
        obj for obj in envelope.objects if obj.object_type == PHENOTYPE_OBJECT_TYPE
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

        if not _first_phenotype_term_identifier(annotation.payload):
            findings.append(
                ValidationFinding(
                    severity=ValidationFindingSeverity.ERROR,
                    code="alliance.phenotype.missing_phenotype_term",
                    message=(
                        "PhenotypeAnnotation requires a first phenotype term CURIE "
                        "or label for ontology resolution."
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
        obj for obj in envelope.objects if obj.object_type == PHENOTYPE_TERM_OBJECT_TYPE
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


def _phenotype_annotation_schema_ref() -> SchemaRef:
    return SchemaRef(
        schema_id="alliance.linkml.PhenotypeAnnotation",
        provider=ALLIANCE_LINKML_PROVIDER_KEY,
        name="PhenotypeAnnotation",
        version=ALLIANCE_LINKML_COMMIT,
        uri=(
            "https://github.com/alliance-genome/agr_curation_schema/blob/"
            f"{ALLIANCE_LINKML_COMMIT}/{PHENOTYPE_LINKML_SCHEMA_SOURCE_FILE}"
        ),
        definition_state=DefinitionState.IN_DEVELOPMENT,
        definition_notes=[
            "Pending envelope target; concrete phenotype annotation subtype is unresolved."
        ],
    )


def _phenotype_subject_schema_ref() -> SchemaRef:
    return SchemaRef(
        schema_id="alliance.linkml.BiologicalEntity",
        provider=ALLIANCE_LINKML_PROVIDER_KEY,
        name="BiologicalEntity",
        version=ALLIANCE_LINKML_COMMIT,
        uri=(
            "https://github.com/alliance-genome/agr_curation_schema/blob/"
            f"{ALLIANCE_LINKML_COMMIT}/{PHENOTYPE_CORE_SCHEMA_SOURCE_FILE}"
        ),
        definition_state=DefinitionState.IN_DEVELOPMENT,
        definition_notes=[
            "Generic subject placeholder until the Gene, Allele, or AGM subtype is resolved."
        ],
    )


def _phenotype_term_schema_ref() -> SchemaRef:
    return SchemaRef(
        schema_id="alliance.linkml.PhenotypeTerm",
        provider=ALLIANCE_LINKML_PROVIDER_KEY,
        name="PhenotypeTerm",
        version=ALLIANCE_LINKML_COMMIT,
        uri=(
            "https://github.com/alliance-genome/agr_curation_schema/blob/"
            f"{ALLIANCE_LINKML_COMMIT}/{PHENOTYPE_ONTOLOGY_TERM_SCHEMA_SOURCE_FILE}"
        ),
    )


def _reference_schema_ref() -> SchemaRef:
    return SchemaRef(
        schema_id="alliance.linkml.Reference",
        provider=ALLIANCE_LINKML_PROVIDER_KEY,
        name="Reference",
        version=ALLIANCE_LINKML_COMMIT,
        uri=(
            "https://github.com/alliance-genome/agr_curation_schema/blob/"
            f"{ALLIANCE_LINKML_COMMIT}/{PHENOTYPE_REFERENCE_SCHEMA_SOURCE_FILE}"
        ),
    )


def _blocked_export_behavior() -> dict[str, Any]:
    return {
        "status": "blocked",
        "exportable": False,
        "submit": False,
        "reason": (
            "Phenotype export is blocked until subject subtype, reference "
            "materialization, ontology term resolution, and write targets are verified."
        ),
        "required_before_export": [
            "Resolve phenotype_annotation_subject to exactly one Gene, Allele, or AGM row.",
            "Resolve the source paper to a durable Alliance reference/information content row.",
            "Validate phenotype term CURIEs against public.ontologyterm.",
            "Prove the exporter can create phenotype rows without mutating canonical entity rows.",
        ],
    }


def _blocked_write_behavior() -> dict[str, Any]:
    return {
        "status": "blocked",
        "reason": (
            "The curation DB phenotype target shape is verified, but this pack "
            "emits pending envelopes only."
        ),
        "blocked_targets": [
            "public.phenotypeannotation",
            "public.genephenotypeannotation",
            "public.allelephenotypeannotation",
            "public.agmphenotypeannotation",
            "public.phenotypeannotation_ontologyterm",
        ],
    }


def _blocker_findings_for_annotation(
    *,
    annotation_ref: ObjectRef,
    phenotype_term_ref: ObjectRef,
    subject_resolution_state: str,
    phenotype_term_curie: str | None,
) -> list[ValidationFinding]:
    findings = [
        ValidationFinding(
            severity=ValidationFindingSeverity.BLOCKER,
            code="alliance.phenotype.export_blocked",
            message=(
                "Phenotype assertion is pending only; export is blocked until "
                "subject, reference, ontology, and non-mutating write targets are resolved."
            ),
            object_ref=annotation_ref,
            details={
                "export_behavior": "blocked",
                "blocked_targets": _blocked_write_behavior()["blocked_targets"],
            },
        )
    ]
    if phenotype_term_curie is None:
        findings.append(
            ValidationFinding(
                severity=ValidationFindingSeverity.BLOCKER,
                code="alliance.phenotype.ontology_resolution_required",
                message=(
                    "Phenotype term is pending ontology resolution; export and write "
                    "remain blocked until the validator resolves a CURIE."
                ),
                object_ref=phenotype_term_ref,
                details={
                    "validator_binding_id": PHENOTYPE_TERM_VALIDATOR_BINDING_ID,
                    "resolution_state": "pending_ontology_resolution",
                    "export_state": "blocked_pending_ontology_resolution",
                    "write_blocked_reason": "phenotype term CURIE unresolved",
                },
            )
        )
    if subject_resolution_state in {
        "blocked_missing_subject",
        "pending_entity_resolution",
    }:
        findings.append(
            ValidationFinding(
                severity=ValidationFindingSeverity.BLOCKER,
                code="alliance.phenotype.subject_resolution_required",
                message=(
                    "Phenotype assertion requires a resolved Gene, Allele, or AGM "
                    "subject before export."
                ),
                object_ref=annotation_ref,
                details={
                    "validator_binding_id": PHENOTYPE_SUBJECT_VALIDATOR_BINDING_ID,
                    "subject_resolution_state": subject_resolution_state,
                    "required_before_export": [
                        "Resolve phenotype_annotation_subject to a concrete curation DB row.",
                        "Select the concrete phenotype annotation subtype for export.",
                    ],
                },
            )
        )
    return findings


def _ontology_lookup_hint(
    item: Mapping[str, Any],
    evidence_records: Sequence[Mapping[str, Any]],
) -> dict[str, str]:
    hint: dict[str, str] = {}
    data_provider = _optional_string(
        item.get("data_provider"),
        "extraction.items[].data_provider",
    )
    taxon_id = _optional_string(item.get("taxon"), "extraction.items[].taxon")
    evidence_record_id = None
    if evidence_records:
        evidence_record_id = _required_string(
            evidence_records[0].get("evidence_record_id"),
            "evidence_records[].evidence_record_id",
        )
    if data_provider:
        hint["data_provider"] = data_provider
    if taxon_id:
        hint["taxon_id"] = taxon_id
    if evidence_record_id:
        hint["evidence_record_id"] = evidence_record_id
    return hint


def _subject_payload(item: Mapping[str, Any]) -> dict[str, Any]:
    subject_identifier = _optional_string(
        item.get("subject_identifier"),
        "extraction.items[].subject_identifier",
    )
    subject_label = _optional_string(
        item.get("subject_label"),
        "extraction.items[].subject_label",
    )
    subject_type = _optional_string(
        item.get("subject_type"),
        "extraction.items[].subject_type",
    )
    taxon = _optional_string(item.get("taxon"), "extraction.items[].taxon")

    if subject_identifier and subject_type:
        resolution_state = "pending_entity_resolution"
    else:
        resolution_state = "blocked_missing_subject"

    payload: dict[str, Any] = {"resolution_state": resolution_state}
    if subject_identifier:
        payload["subject_identifier"] = subject_identifier
    if subject_label:
        payload["subject_label"] = subject_label
    if subject_type:
        payload["subject_type"] = subject_type
    if taxon:
        payload["taxon"] = taxon
    if resolution_state == "blocked_missing_subject":
        payload["resolution_note"] = (
            "Tool-verified phenotype extraction did not provide a durable "
            "phenotype_annotation_subject identifier and subtype."
        )
    return payload


def _legacy_keys_in_envelope(envelope: DomainEnvelope) -> set[str]:
    return set(_FORBIDDEN_LEGACY_COLLECTIONS.intersection(envelope.metadata))


def _source_mentions(item: Mapping[str, Any]) -> list[str]:
    mentions = [
        _required_string(value, "extraction.items[].source_mentions[]")
        for value in _required_sequence(
            item.get("source_mentions"),
            "extraction.items[].source_mentions",
        )
    ]
    if not mentions:
        raise ValueError(
            "extraction.items[].source_mentions must include at least one "
            "non-empty string"
        )
    return mentions


def _evidence_records_for_item(
    item: Mapping[str, Any],
    case_lookup: Mapping[str, Mapping[str, Any]],
) -> tuple[Mapping[str, Any], ...]:
    direct_evidence = tuple(
        _record_with_required_id(
            _required_mapping(record, "extraction.items[].evidence[]"),
            "extraction.items[].evidence[].evidence_record_id",
        )
        for record in _optional_sequence(
            item.get("evidence"),
            "extraction.items[].evidence",
        )
    )
    if direct_evidence:
        return direct_evidence

    records_by_id: dict[str, Mapping[str, Any]] = {}
    for record in (
        _required_mapping(record, "extraction.items[].evidence_records[]")
        for record in _optional_sequence(
            item.get("evidence_records"),
            "extraction.items[].evidence_records",
        )
    ):
        normalized = _record_with_required_id(
            record,
            "extraction.items[].evidence_records[].evidence_record_id",
        )
        records_by_id[str(normalized["evidence_record_id"])] = normalized
    evidence_record_ids = [
        value
        for value in (
            _optional_string(raw_id, "extraction.items[].evidence_record_ids[]")
            for raw_id in _optional_sequence(
                item.get("evidence_record_ids"),
                "extraction.items[].evidence_record_ids",
            )
        )
        if value is not None
    ]
    if evidence_record_ids and records_by_id:
        missing_record_ids = [
            record_id for record_id in evidence_record_ids if record_id not in records_by_id
        ]
        if missing_record_ids:
            raise ValueError(
                "extraction.items[].evidence_record_ids references unknown evidence "
                f"record(s): {', '.join(missing_record_ids)}"
            )
        return tuple(records_by_id[record_id] for record_id in evidence_record_ids)

    evidence_case_ids = [
        value
        for value in (
            _optional_string(raw_id, "extraction.items[].evidence_case_ids[]")
            for raw_id in _optional_sequence(
                item.get("evidence_case_ids"),
                "extraction.items[].evidence_case_ids",
            )
        )
        if value is not None
    ]
    records: list[Mapping[str, Any]] = []
    for case_id in evidence_case_ids:
        tool_case = case_lookup.get(case_id)
        if tool_case is None:
            raise ValueError(
                "extraction.items[].evidence_case_ids references unknown "
                f"tool case: {case_id}"
            )
        record = _verified_evidence_record(tool_case)
        if record is not None:
            records.append(record)
    return tuple(records)


def _verified_evidence_record(
    tool_case: Mapping[str, Any],
) -> Mapping[str, Any] | None:
    tool_input = _required_mapping(tool_case.get("tool_input"), "tool_cases[].tool_input")
    tool_result = _required_mapping(
        tool_case.get("expected_tool_result"),
        "tool_cases[].expected_tool_result",
    )
    if (
        _optional_string(
            tool_result.get("status"),
            "tool_cases[].expected_tool_result.status",
        )
        != "verified"
    ):
        return None

    record: dict[str, Any] = {
        "evidence_record_id": _required_string(
            tool_result.get("evidence_record_id"),
            "tool_cases[].expected_tool_result.evidence_record_id",
        ),
        "entity": tool_input.get("entity"),
        "chunk_id": tool_result.get("chunk_id"),
        "verified_quote": tool_result.get("verified_quote"),
        "page": tool_result.get("page"),
        "section": tool_result.get("section"),
    }
    source_span_ids = _required_sequence(
        tool_result.get("source_span_ids"),
        "tool_cases[].expected_tool_result.source_span_ids",
    )
    record["source_span_ids"] = list(source_span_ids)
    for optional_key in ("subsection", "figure_reference"):
        optional_value = _optional_string(
            tool_result.get(optional_key),
            f"tool_cases[].expected_tool_result.{optional_key}",
        )
        if optional_value is not None:
            record[optional_key] = optional_value
    return record


def _record_with_required_id(
    record: Mapping[str, Any],
    field_name: str,
) -> Mapping[str, Any]:
    normalized = dict(record)
    normalized["evidence_record_id"] = _required_string(
        normalized.get("evidence_record_id"),
        field_name,
    )
    return normalized


def _evidence_quote_payload(
    record: Mapping[str, Any],
    evidence_record_id: str,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "evidence_record_id": evidence_record_id,
        "entity": _optional_string(record.get("entity"), "evidence_records[].entity"),
        "verified_quote": _required_string(
            record.get("verified_quote"),
            "evidence_records[].verified_quote",
        ),
        "page": record.get("page"),
        "section": _optional_string(record.get("section"), "evidence_records[].section"),
        "chunk_id": _optional_string(
            record.get("chunk_id"),
            "evidence_records[].chunk_id",
        ),
    }
    for optional_key in ("subsection", "figure_reference"):
        optional_value = _optional_string(
            record.get(optional_key),
            f"evidence_records[].{optional_key}",
        )
        if optional_value is not None:
            payload[optional_key] = optional_value
    return payload


def _tool_cases_by_id(fixture: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    cases: dict[str, Mapping[str, Any]] = {}
    for raw_case in _optional_sequence(fixture.get("tool_cases"), "tool_cases"):
        case = _required_mapping(raw_case, "tool_cases[]")
        case_id = _required_string(case.get("case_id"), "tool_cases[].case_id")
        cases[case_id] = deepcopy(case)
    return cases


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


def _first_phenotype_term_identifier(payload: Mapping[str, Any]) -> str | None:
    terms = payload.get("phenotype_terms")
    if not isinstance(terms, Sequence) or isinstance(terms, (str, bytes, bytearray)):
        return None
    if not terms:
        return None
    first_term = terms[0]
    if not isinstance(first_term, Mapping):
        return None
    return _optional_string(
        first_term.get("curie"),
        "phenotype_terms[0].curie",
    ) or _optional_string(first_term.get("label"), "phenotype_terms[0].label")


def _required_mapping(value: Any, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be an object")
    return value


def _required_sequence(value: Any, field_name: str) -> Sequence[Any]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{field_name} must be a list")
    return value


def _optional_sequence(value: Any, field_name: str) -> Sequence[Any]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{field_name} must be a list")
    return value


def _optional_string(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    normalized = value.strip()
    return normalized or None


def _required_string(value: Any, field_name: str) -> str:
    normalized = _optional_string(value, field_name)
    if normalized is None:
        raise ValueError(f"{field_name} must be a non-empty string")
    return normalized


def _optional_bool(value: Any, field_name: str) -> bool | None:
    if value is None:
        return None
    if not isinstance(value, bool):
        raise ValueError(f"{field_name} must be a boolean")
    return value


__all__ = [
    "PHENOTYPE_DOMAIN_PACK_DIR_NAME",
    "PHENOTYPE_DOMAIN_PACK_ID",
    "PHENOTYPE_DOMAIN_PACK_VERSION",
    "PHENOTYPE_EXPORT_SCHEMA_VERSION",
    "PHENOTYPE_EXPORT_TARGET_ID",
    "PHENOTYPE_FIXTURE_PACK_ID",
    "PHENOTYPE_OBJECT_TYPE",
    "PHENOTYPE_PENDING_ENVELOPE_VALIDATOR_BINDING_ID",
    "PHENOTYPE_REQUIRED_BEFORE_WRITE",
    "PHENOTYPE_SUBMISSION_BLOCKED_OPERATIONS",
    "PHENOTYPE_SUBJECT_OBJECT_TYPE",
    "PHENOTYPE_SUBJECT_VALIDATOR_BINDING_ID",
    "PHENOTYPE_TERM_OBJECT_TYPE",
    "PHENOTYPE_TERM_VALIDATOR_BINDING_ID",
    "PhenotypeAnnotationExportAdapter",
    "PhenotypeAnnotationSubmissionBlockerAdapter",
    "build_phenotype_annotation_export_payload",
    "build_pending_phenotype_envelope_from_tool_verified_fixture",
    "get_phenotype_domain_pack_metadata_path",
    "validate_pending_phenotype_envelope",
]
