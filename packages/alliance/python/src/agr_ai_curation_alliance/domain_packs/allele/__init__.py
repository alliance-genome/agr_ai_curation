"""Allele domain-pack helpers for pending paper/evidence envelopes."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from agr_ai_curation_alliance.domain_packs.schema_refs import ALLIANCE_LINKML_COMMIT
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
from .constants import (
    ALLELE_ASSOCIATION_KIND,
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

_REFERENCE_SCHEMA_REF = SchemaRef(
    schema_id="alliance.linkml.Reference",
    provider="alliance_linkml",
    name="Reference",
    version=ALLIANCE_LINKML_COMMIT,
    uri=(
        "https://github.com/alliance-genome/agr_curation_schema/blob/"
        f"{ALLIANCE_LINKML_COMMIT}/model/schema/reference.yaml"
    ),
)
_ASSOCIATION_SCHEMA_REF = SchemaRef(
    schema_id="alliance.linkml.AlleleAssociation",
    provider="alliance_linkml",
    name="AlleleAssociation",
    version=ALLIANCE_LINKML_COMMIT,
    uri=(
        "https://github.com/alliance-genome/agr_curation_schema/blob/"
        f"{ALLIANCE_LINKML_COMMIT}/model/schema/allele.yaml"
    ),
    definition_state=DefinitionState.IN_DEVELOPMENT,
    definition_notes=[
        "Abstract LinkML target used only for grounded pending-envelope metadata."
    ],
)


def build_pending_allele_envelope_from_tool_verified_fixture(
    fixture: Mapping[str, Any],
    *,
    envelope_id: str = "allele-tool-verified-envelope",
    created_at: datetime | None = None,
) -> DomainEnvelope:
    """Convert the tool-verified allele fixture into pending envelope objects."""

    timestamp = created_at or datetime.now(timezone.utc)
    extraction = _required_mapping(fixture.get("extraction"), "extraction")
    paper = _required_mapping(fixture.get("paper"), "paper")
    case_lookup = _tool_cases_by_id(fixture)

    objects: list[CuratableObjectEnvelope] = []
    validation_findings: list[ValidationFinding] = []

    reference_ref_id = "paper-reference-1"
    reference_object = CuratableObjectEnvelope(
        object_type="Reference",
        pending_ref_id=reference_ref_id,
        schema_ref=_REFERENCE_SCHEMA_REF,
        status=CuratableObjectStatus.PENDING,
        definition_state=DefinitionState.IN_DEVELOPMENT,
        payload={
            "title": _optional_string(paper.get("title"), "paper.title"),
            "filename": _optional_string(paper.get("filename"), "paper.filename"),
        },
        metadata={
            "object_role": "validated_reference",
            "validation_state": "pending_reference_resolution",
        },
    )
    objects.append(reference_object)

    retained_count = 0
    skipped_without_evidence = 0
    for raw_item in _iter_allele_items(extraction):
        item = _required_mapping(raw_item, "extraction.alleles[]")
        evidence_records = _evidence_records_for_item(item, case_lookup)
        if not evidence_records:
            skipped_without_evidence += 1
            continue

        retained_count += 1
        label = _required_string(item.get("label") or item.get("mention"), "allele label")
        normalized_hint = _optional_string(
            item.get("normalized_id"),
            "extraction.alleles[].normalized_id",
        )
        associated_gene = _optional_string(
            item.get("associated_gene"),
            "extraction.alleles[].associated_gene",
        )
        taxon = _required_string(
            item.get("taxon"),
            "extraction.alleles[].taxon",
        )
        source_mentions = [
            value
            for value in (
                _optional_string(value, "extraction.alleles[].source_mentions[]")
                for value in _optional_sequence(
                    item.get("source_mentions"),
                    "extraction.alleles[].source_mentions",
                )
            )
            if value is not None
        ] or [label]

        mention_ref_id = f"allele-mention-{retained_count}"
        association_ref_id = f"allele-paper-evidence-association-{retained_count}"
        evidence_refs: list[ObjectRef] = []
        evidence_record_ids: list[str] = []

        mention_payload: dict[str, Any] = {
            "mention": {
                "text": label,
            },
            "source_mentions": source_mentions,
        }
        if normalized_hint is not None:
            mention_payload["mention"]["normalized_hint"] = normalized_hint
        if associated_gene is not None:
            mention_payload["associated_gene"] = {"symbol": associated_gene}
        mention_payload["taxon"] = {"curie": taxon}

        evidence_objects: list[CuratableObjectEnvelope] = []
        for evidence_index, evidence_record in enumerate(evidence_records, start=1):
            record = _required_mapping(evidence_record, "evidence_records[]")
            evidence_ref_id = f"evidence-quote-{retained_count}-{evidence_index}"
            evidence_record_id = _required_string(
                record.get("evidence_record_id"),
                "evidence_record_id",
            )
            evidence_record_ids.append(evidence_record_id)
            evidence_refs.append(
                ObjectRef(
                    pending_ref_id=evidence_ref_id,
                    object_type="EvidenceQuote",
                )
            )
            evidence_objects.append(
                CuratableObjectEnvelope(
                    object_type="EvidenceQuote",
                    pending_ref_id=evidence_ref_id,
                    status=CuratableObjectStatus.PENDING,
                    definition_state=DefinitionState.IN_DEVELOPMENT,
                    payload=_evidence_quote_payload(record, evidence_record_id),
                    metadata={"object_role": "metadata_only"},
                )
            )

        mention_object = CuratableObjectEnvelope(
            object_type="AlleleMention",
            pending_ref_id=mention_ref_id,
            status=CuratableObjectStatus.PENDING,
            definition_state=DefinitionState.IN_DEVELOPMENT,
            payload=mention_payload,
            evidence_record_ids=evidence_record_ids,
            metadata={"object_role": "metadata_only"},
        )
        objects.append(mention_object)
        objects.extend(evidence_objects)

        association_refs = [
            ObjectRef(pending_ref_id=reference_ref_id, object_type="Reference"),
            ObjectRef(pending_ref_id=mention_ref_id, object_type="AlleleMention"),
            *evidence_refs,
        ]
        association_object = CuratableObjectEnvelope(
            object_type="AllelePaperEvidenceAssociation",
            pending_ref_id=association_ref_id,
            schema_ref=_ASSOCIATION_SCHEMA_REF,
            status=CuratableObjectStatus.PENDING,
            definition_state=DefinitionState.IN_DEVELOPMENT,
            definition_notes=[
                "Pending only; write behavior is blocked until reference IDs and write targets are verified."
            ],
            payload={
                "association_kind": "allele_paper_evidence",
                "allele_label": label,
                "reference_title": _optional_string(paper.get("title"), "paper.title"),
                "evidence_record_ids": evidence_record_ids,
            },
            object_refs=association_refs,
            metadata={
                "object_role": "curatable_unit",
                "export_behavior": {
                    "status": "blocked",
                    "mode": "verified_association_targets_only",
                    "reason": (
                        "Allele association export requires durable allele, "
                        "reference, and evidence IDs before any verified target "
                        "operation can be emitted."
                    ),
                    "verified_targets": [
                        "public.allele_reference",
                        "public.allelegeneassociation",
                        "public.allelegeneassociation_informationcontententity",
                    ],
                    "blocked_targets": [
                        "public.allele_reference",
                        "public.allelegeneassociation_informationcontententity",
                    ],
                },
                "write_behavior": {
                    "status": "blocked",
                    "reason": (
                        "Reference materialization and non-mutating allele association "
                        "writes are not verified for this pack."
                    ),
                },
            },
        )
        objects.append(association_object)
        validation_findings.append(
            ValidationFinding(
                severity=ValidationFindingSeverity.BLOCKER,
                code="alliance.allele.write_blocked",
                message=(
                    "Allele paper/evidence association is pending only; write behavior "
                    "is blocked until reference_id and non-mutating submission targets "
                    "are verified."
                ),
                object_ref=ObjectRef(
                    pending_ref_id=association_ref_id,
                    object_type="AllelePaperEvidenceAssociation",
                ),
                details={
                    "write_behavior": "blocked",
                    "blocked_targets": [
                        "public.allele_reference",
                        "public.allelegeneassociation",
                        "public.allelegeneassociation_informationcontententity",
                    ],
                    "verified_targets": [
                        "public.allele_reference",
                        "public.allelegeneassociation",
                        "public.allelegeneassociation_informationcontententity",
                    ],
                    "mutates_base_rows": {
                        "public.allele": False,
                        "public.gene": False,
                    },
                },
            )
        )

    if skipped_without_evidence:
        validation_findings.append(
            ValidationFinding(
                severity=ValidationFindingSeverity.WARNING,
                code="alliance.allele.skipped_without_verified_evidence",
                message=(
                    f"Skipped {skipped_without_evidence} allele candidate(s) without "
                    "verified evidence records."
                ),
                details={"skipped_without_evidence": skipped_without_evidence},
            )
        )

    return DomainEnvelope(
        envelope_id=envelope_id,
        domain_pack_id=ALLELE_DOMAIN_PACK_ID,
        domain_pack_version=ALLELE_DOMAIN_PACK_VERSION,
        status=DomainEnvelopeStatus.EXTRACTED,
        schema_ref=SchemaRef(
            schema_id="agr.alliance.allele.domain_pack",
            provider="domain-pack",
            name="Alliance Allele Paper Evidence Domain Pack",
            version=ALLELE_DOMAIN_PACK_VERSION,
            definition_state=DefinitionState.IN_DEVELOPMENT,
        ),
        objects=objects,
        validation_findings=validation_findings,
        history=[
            HistoryEvent(
                event_type=HistoryEventKind.CREATED,
                timestamp=timestamp,
                actor_type=HistoryActorType.SYSTEM,
                message="Converted tool-verified allele fixture to a pending domain envelope.",
                details={
                    "retained_allele_count": retained_count,
                    "skipped_without_evidence": skipped_without_evidence,
                },
            )
        ],
        metadata={
            "source_fixture_id": _optional_string(
                fixture.get("fixture_id"),
                "fixture_id",
            ),
            "write_behavior": {"status": "blocked"},
        },
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
        for obj in envelope.objects
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

        if association.payload.get("allele_identifier"):
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


def _iter_allele_items(extraction: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    return tuple(
        {
            "label": _optional_string(item.get("label"), "extraction.alleles[].label")
            or _optional_string(item.get("mention"), "extraction.alleles[].mention")
            or _optional_string(
                item.get("normalized_symbol"),
                "extraction.alleles[].normalized_symbol",
            ),
            "normalized_id": item.get("normalized_id"),
            "associated_gene": item.get("associated_gene"),
            "taxon": item.get("taxon"),
            "source_mentions": item.get("source_mentions")
            if item.get("source_mentions") is not None
            else [_optional_string(item.get("mention"), "extraction.alleles[].mention")],
            "evidence": item.get("evidence"),
            "evidence_records": item.get("evidence_records"),
            "evidence_record_ids": item.get("evidence_record_ids"),
            "evidence_case_ids": item.get("evidence_case_ids"),
        }
        for item in (
            _required_mapping(raw_item, "extraction.alleles[]")
            for raw_item in _required_sequence(
                extraction.get("alleles"),
                "extraction.alleles",
            )
        )
    )


def _evidence_records_for_item(
    item: Mapping[str, Any],
    case_lookup: Mapping[str, Mapping[str, Any]],
) -> tuple[Mapping[str, Any], ...]:
    direct_evidence = tuple(
        _required_mapping(record, "extraction.alleles[].evidence[]")
        for record in _optional_sequence(
            item.get("evidence"),
            "extraction.alleles[].evidence",
        )
    )
    if direct_evidence:
        return direct_evidence

    records_by_id: dict[str, Mapping[str, Any]] = {}
    for record in (
        _required_mapping(record, "extraction.alleles[].evidence_records[]")
        for record in _optional_sequence(
            item.get("evidence_records"),
            "extraction.alleles[].evidence_records",
        )
    ):
        record_id = _optional_string(
            record.get("evidence_record_id"),
            "extraction.alleles[].evidence_records[].evidence_record_id",
        )
        if record_id is not None:
            records_by_id[record_id] = record
    evidence_record_ids = [
        value
        for value in (
            _optional_string(
                raw_id,
                "extraction.alleles[].evidence_record_ids[]",
            )
            for raw_id in _optional_sequence(
                item.get("evidence_record_ids"),
                "extraction.alleles[].evidence_record_ids",
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
                "extraction.alleles[].evidence_record_ids references unknown "
                f"evidence record(s): {', '.join(missing_record_ids)}"
            )
        return tuple(
            records_by_id[record_id]
            for record_id in evidence_record_ids
        )

    evidence_case_ids = [
        value
        for value in (
            _optional_string(
                raw_id,
                "extraction.alleles[].evidence_case_ids[]",
            )
            for raw_id in _optional_sequence(
                item.get("evidence_case_ids"),
                "extraction.alleles[].evidence_case_ids",
            )
        )
        if value is not None
    ]
    records: list[Mapping[str, Any]] = []
    for case_id in evidence_case_ids:
        tool_case = case_lookup.get(case_id)
        if tool_case is None:
            raise ValueError(
                "extraction.alleles[].evidence_case_ids references unknown "
                f"tool case: {case_id}"
            )
        record = _verified_evidence_record(tool_case)
        if record is not None:
            records.append(record)
    return tuple(records)


def _verified_evidence_record(
    tool_case: Mapping[str, Any] | None,
) -> Mapping[str, Any] | None:
    if tool_case is None:
        return None

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


def _evidence_quote_payload(
    record: Mapping[str, Any],
    evidence_record_id: str,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "evidence_record_id": evidence_record_id,
        "entity": _optional_string(record.get("entity"), "evidence_records[].entity"),
        "verified_quote": _required_string(
            record.get("verified_quote"),
            "verified_quote",
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
    "VERIFIED_ALLELE_ASSOCIATION_TARGETS",
    "build_allele_association_export",
    "build_allele_association_submission_plan",
    "build_pending_allele_envelope_from_tool_verified_fixture",
    "materialize_allele_builder_state",
    "validate_allele_builder_objects",
    "validate_pending_allele_envelope",
]
