"""Allele domain-pack helpers for pending paper/evidence envelopes."""

from __future__ import annotations

import re
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

ALLELE_DOMAIN_PACK_ID = "agr.alliance.allele"
ALLELE_DOMAIN_PACK_VERSION = "0.1.0"
ALLELE_LINKML_COMMIT = "1b11d0888f19eba4ca72022200bb7d96b30d4a52"

_ALLELE_SCHEMA_REF = SchemaRef(
    schema_id="alliance.linkml.Allele",
    provider="alliance_linkml",
    name="Allele",
    version=ALLELE_LINKML_COMMIT,
    uri=(
        "https://github.com/alliance-genome/agr_curation_schema/blob/"
        f"{ALLELE_LINKML_COMMIT}/model/schema/allele.yaml"
    ),
)
_REFERENCE_SCHEMA_REF = SchemaRef(
    schema_id="alliance.linkml.Reference",
    provider="alliance_linkml",
    name="Reference",
    version=ALLELE_LINKML_COMMIT,
    uri=(
        "https://github.com/alliance-genome/agr_curation_schema/blob/"
        f"{ALLELE_LINKML_COMMIT}/model/schema/reference.yaml"
    ),
)
_ASSOCIATION_SCHEMA_REF = SchemaRef(
    schema_id="alliance.linkml.AlleleAssociation",
    provider="alliance_linkml",
    name="AlleleAssociation",
    version=ALLELE_LINKML_COMMIT,
    uri=(
        "https://github.com/alliance-genome/agr_curation_schema/blob/"
        f"{ALLELE_LINKML_COMMIT}/model/schema/allele.yaml"
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
    extraction = _as_mapping(fixture.get("extraction"))
    paper = _as_mapping(fixture.get("paper"))
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
            "title": _optional_string(paper.get("title")),
            "filename": _optional_string(paper.get("filename")),
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
        item = _as_mapping(raw_item)
        evidence_records = _evidence_records_for_item(item, case_lookup)
        if not evidence_records:
            skipped_without_evidence += 1
            continue

        retained_count += 1
        label = _required_string(item.get("label") or item.get("mention"), "allele label")
        normalized_id = _optional_string(item.get("normalized_id"))
        source_mentions = [
            value
            for value in (
                _optional_string(value)
                for value in _as_sequence(item.get("source_mentions"))
            )
            if value is not None
        ] or [label]
        slug = _slug(label, fallback=f"allele-{retained_count}")

        mention_ref_id = f"allele-mention-{retained_count}"
        allele_ref_id = f"allele-reference-{retained_count}"
        association_ref_id = f"allele-paper-evidence-association-{retained_count}"
        evidence_refs: list[ObjectRef] = []
        evidence_record_ids: list[str] = []

        mention_object = CuratableObjectEnvelope(
            object_type="AlleleMention",
            pending_ref_id=mention_ref_id,
            status=CuratableObjectStatus.PENDING,
            definition_state=DefinitionState.IN_DEVELOPMENT,
            payload={
                "mention_text": label,
                "normalized_id": normalized_id,
                "source_mentions": source_mentions,
            },
            metadata={"object_role": "metadata_only"},
        )
        allele_object = CuratableObjectEnvelope(
            object_type="Allele",
            pending_ref_id=allele_ref_id,
            schema_ref=_ALLELE_SCHEMA_REF,
            status=CuratableObjectStatus.PENDING,
            definition_state=DefinitionState.IN_DEVELOPMENT,
            payload={
                "primary_external_id": normalized_id,
                "allele_symbol": label,
                "source_mentions": source_mentions,
            },
            metadata={
                "object_role": "validated_reference",
                "validation_state": "pending_materialization",
            },
        )
        objects.extend([mention_object, allele_object])

        for evidence_index, evidence_record in enumerate(evidence_records, start=1):
            record = _as_mapping(evidence_record)
            evidence_ref_id = f"evidence-quote-{retained_count}-{evidence_index}"
            evidence_record_id = (
                _optional_string(record.get("evidence_record_id"))
                or f"{slug}-evidence-{evidence_index}"
            )
            evidence_record_ids.append(evidence_record_id)
            evidence_refs.append(
                ObjectRef(
                    pending_ref_id=evidence_ref_id,
                    object_type="EvidenceQuote",
                )
            )
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

        association_refs = [
            ObjectRef(pending_ref_id=allele_ref_id, object_type="Allele"),
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
                "allele_identifier": normalized_id,
                "allele_label": label,
                "reference_title": _optional_string(paper.get("title")),
                "evidence_record_ids": evidence_record_ids,
            },
            object_refs=association_refs,
            metadata={
                "object_role": "curatable_unit",
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
            "source_fixture_id": _optional_string(fixture.get("fixture_id")),
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
            "Allele",
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

        write_behavior = _as_mapping(association.metadata.get("write_behavior"))
        if write_behavior.get("status") != "blocked":
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

    return tuple(findings)


def _iter_allele_items(extraction: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    allele_findings = _as_sequence(extraction.get("alleles"))
    if allele_findings:
        return tuple(
            {
                "label": _optional_string(item.get("mention"))
                or _optional_string(item.get("normalized_symbol")),
                "normalized_id": item.get("normalized_id"),
                "source_mentions": [_optional_string(item.get("mention"))],
                "evidence_record_ids": item.get("evidence_record_ids"),
            }
            for item in (_as_mapping(raw_item) for raw_item in allele_findings)
        )
    return tuple(_as_mapping(item) for item in _as_sequence(extraction.get("items")))


def _evidence_records_for_item(
    item: Mapping[str, Any],
    case_lookup: Mapping[str, Mapping[str, Any]],
) -> tuple[Mapping[str, Any], ...]:
    direct_evidence = tuple(
        _as_mapping(record) for record in _as_sequence(item.get("evidence"))
    )
    if direct_evidence:
        return direct_evidence

    records_by_id = {
        _optional_string(record.get("evidence_record_id")): _as_mapping(record)
        for record in _as_sequence(item.get("evidence_records"))
        if _optional_string(_as_mapping(record).get("evidence_record_id")) is not None
    }
    evidence_record_ids = [
        value
        for value in (
            _optional_string(raw_id)
            for raw_id in _as_sequence(item.get("evidence_record_ids"))
        )
        if value is not None
    ]
    if evidence_record_ids and records_by_id:
        return tuple(
            records_by_id[record_id]
            for record_id in evidence_record_ids
            if record_id in records_by_id
        )

    evidence_case_ids = [
        value
        for value in (
            _optional_string(raw_id)
            for raw_id in _as_sequence(item.get("evidence_case_ids"))
        )
        if value is not None
    ]
    return tuple(
        record
        for record in (
            _verified_evidence_record(case_lookup.get(case_id))
            for case_id in evidence_case_ids
        )
        if record is not None
    )


def _verified_evidence_record(
    tool_case: Mapping[str, Any] | None,
) -> Mapping[str, Any] | None:
    if tool_case is None:
        return None

    tool_input = _as_mapping(tool_case.get("tool_input"))
    tool_result = _as_mapping(tool_case.get("expected_tool_result"))
    if _optional_string(tool_result.get("status")) != "verified":
        return None

    record: dict[str, Any] = {
        "entity": tool_input.get("entity"),
        "chunk_id": tool_input.get("chunk_id"),
        "verified_quote": tool_result.get("verified_quote"),
        "page": tool_result.get("page"),
        "section": tool_result.get("section"),
    }
    for optional_key in ("subsection", "figure_reference"):
        optional_value = _optional_string(tool_result.get(optional_key))
        if optional_value is not None:
            record[optional_key] = optional_value
    return record


def _evidence_quote_payload(
    record: Mapping[str, Any],
    evidence_record_id: str,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "evidence_record_id": evidence_record_id,
        "entity": _optional_string(record.get("entity")),
        "verified_quote": _required_string(
            record.get("verified_quote"),
            "verified_quote",
        ),
        "page": record.get("page"),
        "section": _optional_string(record.get("section")),
        "chunk_id": _optional_string(record.get("chunk_id")),
    }
    for optional_key in ("subsection", "figure_reference"):
        optional_value = _optional_string(record.get(optional_key))
        if optional_value is not None:
            payload[optional_key] = optional_value
    return payload


def _tool_cases_by_id(fixture: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {
        str(case["case_id"]): deepcopy(case)
        for case in _as_sequence(fixture.get("tool_cases"))
        if isinstance(case, Mapping) and case.get("case_id")
    }


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _as_sequence(value: Any) -> Sequence[Any]:
    return value if isinstance(value, (list, tuple)) else ()


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _required_string(value: Any, field_name: str) -> str:
    normalized = _optional_string(value)
    if normalized is None:
        raise ValueError(f"{field_name} must be a non-empty string")
    return normalized


def _slug(value: str, *, fallback: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or fallback


__all__ = [
    "ALLELE_DOMAIN_PACK_ID",
    "ALLELE_DOMAIN_PACK_VERSION",
    "build_pending_allele_envelope_from_tool_verified_fixture",
    "validate_pending_allele_envelope",
]
