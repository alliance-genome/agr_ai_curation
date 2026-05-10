"""Provider-neutral workspace projections for domain envelope materialization."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from hashlib import sha256
import json
from typing import Any

from src.schemas.curation_workspace import (
    DomainEnvelopeEvidenceAnchorProjection,
    DomainEnvelopeValidationFindingProjection,
    DomainEnvelopeValidationStatus,
    DomainEnvelopeValidationSummaryProjection,
    EvidenceAnchor,
    EvidenceAnchorKind,
    EvidenceLocatorQuality,
    EvidenceSupportsDecision,
)
from src.schemas.domain_envelope import (
    CuratableObjectEnvelope,
    DomainEnvelope,
    ObjectRef,
    ValidationFinding,
    ValidationFindingSeverity,
    ValidationFindingStatus,
)


VALIDATION_STATUS_RANK: dict[DomainEnvelopeValidationStatus, int] = {
    DomainEnvelopeValidationStatus.RESOLVED: 0,
    DomainEnvelopeValidationStatus.WAIVED: 0,
    DomainEnvelopeValidationStatus.PLANNED: 1,
    DomainEnvelopeValidationStatus.UNDER_DEVELOPMENT: 2,
    DomainEnvelopeValidationStatus.UNRESOLVED: 3,
    DomainEnvelopeValidationStatus.BLOCKED: 4,
}

SEVERITY_RANK: dict[str, int] = {
    ValidationFindingSeverity.INFO.value: 0,
    ValidationFindingSeverity.WARNING.value: 1,
    ValidationFindingSeverity.ERROR.value: 2,
    ValidationFindingSeverity.BLOCKER.value: 3,
}


def project_evidence_anchor_projections(
    envelope: DomainEnvelope,
    *,
    envelope_revision: int,
    document_id: str | None = None,
    object_id: str | None = None,
) -> list[DomainEnvelopeEvidenceAnchorProjection]:
    """Project curator evidence navigation anchors from envelope metadata records."""

    records_by_id, record_ids_by_metadata_path = _evidence_record_indexes(
        envelope.metadata
    )
    projections: list[DomainEnvelopeEvidenceAnchorProjection] = []

    for domain_object in envelope.objects:
        stable_object_id = _stable_object_id(domain_object)
        if object_id is not None and stable_object_id != object_id:
            continue

        seen_projection_keys: set[tuple[str, str | None]] = set()
        for evidence_record_id in _object_evidence_record_ids(
            domain_object,
            records_by_id,
            record_ids_by_metadata_path,
        ):
            evidence_record = records_by_id.get(evidence_record_id)
            if evidence_record is None:
                continue
            for field_path in _projection_field_paths(evidence_record):
                projection_key = (evidence_record_id, field_path)
                if projection_key in seen_projection_keys:
                    continue
                seen_projection_keys.add(projection_key)
                projections.append(
                    _evidence_anchor_projection(
                        envelope=envelope,
                        envelope_revision=envelope_revision,
                        domain_object=domain_object,
                        evidence_record_id=evidence_record_id,
                        evidence_record=evidence_record,
                        field_path=field_path,
                        document_id=document_id,
                    )
                )

    return sorted(
        projections,
        key=lambda projection: (
            projection.object_id,
            projection.field_path or "",
            projection.evidence_record_id,
            projection.anchor_id,
        ),
    )


def project_validation_summary_projections(
    envelope: DomainEnvelope,
    *,
    envelope_revision: int,
    object_id: str | None = None,
) -> list[DomainEnvelopeValidationSummaryProjection]:
    """Project validation state summaries grouped by envelope object and field path."""

    object_id_by_ref = _object_id_by_ref(envelope)
    object_type_by_id = {
        _stable_object_id(domain_object): domain_object.object_type
        for domain_object in envelope.objects
    }
    grouped: dict[
        tuple[str | None, str | None],
        list[DomainEnvelopeValidationFindingProjection],
    ] = {}

    for finding_index, finding in enumerate(envelope.validation_findings):
        target_object_id, field_path = _finding_target(finding, object_id_by_ref)
        if object_id is not None and target_object_id != object_id:
            continue
        finding_projection = _validation_finding_projection(
            envelope=envelope,
            envelope_revision=envelope_revision,
            finding=finding,
            finding_index=finding_index,
            object_id=target_object_id,
            object_type=object_type_by_id.get(target_object_id or ""),
            field_path=field_path,
        )
        grouped.setdefault((target_object_id, field_path), []).append(finding_projection)

    summaries = [
        _validation_summary_projection(
            envelope_id=envelope.envelope_id,
            envelope_revision=envelope_revision,
            object_id=group_key[0],
            object_type=object_type_by_id.get(group_key[0] or ""),
            field_path=group_key[1],
            findings=findings,
        )
        for group_key, findings in grouped.items()
    ]
    return sorted(
        summaries,
        key=lambda summary: (
            summary.object_id or "",
            summary.field_path or "",
            summary.summary_id,
        ),
    )


def _evidence_record_indexes(
    metadata: Mapping[str, Any],
) -> tuple[dict[str, Mapping[str, Any]], dict[str, str]]:
    records_by_id: dict[str, Mapping[str, Any]] = {}
    record_ids_by_metadata_path: dict[str, str] = {}

    for metadata_path, raw_records in _metadata_evidence_record_lists(metadata):
        for record_index, raw_record in enumerate(raw_records):
            if not isinstance(raw_record, Mapping):
                continue
            evidence_record_id = _optional_string(raw_record.get("evidence_record_id"))
            if evidence_record_id is None:
                continue
            records_by_id[evidence_record_id] = raw_record
            record_ids_by_metadata_path[
                f"{metadata_path}[{record_index}]"
            ] = evidence_record_id

    return records_by_id, record_ids_by_metadata_path


def _metadata_evidence_record_lists(
    metadata: Mapping[str, Any],
) -> list[tuple[str, list[Any]]]:
    evidence_record_lists: list[tuple[str, list[Any]]] = []

    raw_records = metadata.get("evidence_records")
    if isinstance(raw_records, list):
        evidence_record_lists.append(("evidence_records", raw_records))

    extraction_metadata = metadata.get("extraction_metadata")
    if isinstance(extraction_metadata, Mapping):
        raw_nested_records = extraction_metadata.get("evidence_records")
        if isinstance(raw_nested_records, list):
            evidence_record_lists.append(
                ("extraction_metadata.evidence_records", raw_nested_records)
            )

    return evidence_record_lists


def _object_evidence_record_ids(
    domain_object: CuratableObjectEnvelope,
    records_by_id: Mapping[str, Mapping[str, Any]],
    record_ids_by_metadata_path: Mapping[str, str],
) -> list[str]:
    evidence_record_ids = _unique_strings(domain_object.evidence_record_ids)
    for metadata_ref in domain_object.metadata_refs:
        evidence_record_id = record_ids_by_metadata_path.get(metadata_ref.metadata_path)
        if (
            evidence_record_id is not None
            and evidence_record_id not in evidence_record_ids
        ):
            evidence_record_ids.append(evidence_record_id)
    for evidence_record_id, evidence_record in records_by_id.items():
        if evidence_record_id in evidence_record_ids:
            continue
        if _evidence_record_targets_object(evidence_record, domain_object):
            evidence_record_ids.append(evidence_record_id)
    return evidence_record_ids


def _evidence_record_targets_object(
    evidence_record: Mapping[str, Any],
    domain_object: CuratableObjectEnvelope,
) -> bool:
    stable_object_id = _stable_object_id(domain_object)
    if _optional_string(evidence_record.get("object_id")) == stable_object_id:
        return True
    if (
        domain_object.pending_ref_id is not None
        and _optional_string(evidence_record.get("pending_ref_id"))
        == domain_object.pending_ref_id
    ):
        return True

    raw_object_ref = evidence_record.get("object_ref")
    if not isinstance(raw_object_ref, Mapping):
        return False
    if _optional_string(raw_object_ref.get("object_id")) == stable_object_id:
        return True
    return (
        domain_object.pending_ref_id is not None
        and _optional_string(raw_object_ref.get("pending_ref_id"))
        == domain_object.pending_ref_id
    )


def _evidence_anchor_projection(
    *,
    envelope: DomainEnvelope,
    envelope_revision: int,
    domain_object: CuratableObjectEnvelope,
    evidence_record_id: str,
    evidence_record: Mapping[str, Any],
    field_path: str | None,
    document_id: str | None,
) -> DomainEnvelopeEvidenceAnchorProjection:
    anchor = _evidence_anchor(evidence_record)
    stable_object_id = _stable_object_id(domain_object)
    source_document_id = _first_string(
        evidence_record,
        "document_id",
        "source_document_id",
        "pdf_document_id",
    )
    envelope_document_id = _first_string(
        envelope.metadata,
        "source_document_id",
        "document_id",
    )
    projection_document_id = source_document_id or envelope_document_id or document_id
    chunk_ids = list(anchor.chunk_ids)
    return DomainEnvelopeEvidenceAnchorProjection(
        anchor_id=_projection_id(
            "evidence",
            envelope.envelope_id,
            envelope_revision,
            stable_object_id,
            field_path,
            evidence_record_id,
        ),
        evidence_record_id=evidence_record_id,
        envelope_id=envelope.envelope_id,
        object_id=stable_object_id,
        object_type=domain_object.object_type,
        field_path=field_path,
        envelope_revision=envelope_revision,
        document_id=projection_document_id,
        quote=_quote_from_anchor(anchor),
        page_number=anchor.page_number,
        page_label=anchor.page_label,
        chunk_id=chunk_ids[0] if chunk_ids else None,
        chunk_ids=chunk_ids,
        section_title=anchor.section_title,
        subsection_title=anchor.subsection_title,
        figure_reference=anchor.figure_reference,
        table_reference=anchor.table_reference,
        source_id=_first_string(evidence_record, "source_id", "source"),
        source_title=_first_string(evidence_record, "source_title", "title"),
        source_url=_first_string(evidence_record, "source_url", "url", "uri"),
        anchor=anchor,
        metadata={
            "object_evidence_record_ids": list(domain_object.evidence_record_ids),
            "source_record": dict(evidence_record),
        },
    )


def _evidence_anchor(evidence_record: Mapping[str, Any]) -> EvidenceAnchor:
    raw_anchor = evidence_record.get("anchor")
    if isinstance(raw_anchor, Mapping):
        try:
            return EvidenceAnchor.model_validate(dict(raw_anchor))
        except Exception:
            pass

    quote = _first_string(
        evidence_record,
        "verified_quote",
        "quote",
        "snippet_text",
        "sentence_text",
        "text",
    )
    page_number = _page_number(
        evidence_record.get("page_number", evidence_record.get("page"))
    )
    section_title = _first_string(evidence_record, "section_title", "section")
    subsection_title = _first_string(evidence_record, "subsection_title", "subsection")
    chunk_ids = _chunk_ids(evidence_record)

    if quote:
        anchor_kind = EvidenceAnchorKind.SNIPPET
        locator_quality = EvidenceLocatorQuality.EXACT_QUOTE
    elif page_number is not None:
        anchor_kind = EvidenceAnchorKind.PAGE
        locator_quality = EvidenceLocatorQuality.PAGE_ONLY
    else:
        anchor_kind = EvidenceAnchorKind.DOCUMENT
        locator_quality = EvidenceLocatorQuality.DOCUMENT_ONLY

    return EvidenceAnchor(
        anchor_kind=anchor_kind,
        locator_quality=locator_quality,
        supports_decision=EvidenceSupportsDecision.SUPPORTS,
        snippet_text=quote,
        sentence_text=quote,
        normalized_text=_first_string(evidence_record, "normalized_text"),
        viewer_search_text=quote,
        viewer_highlightable=bool(quote),
        page_number=page_number,
        page_label=_first_string(evidence_record, "page_label"),
        section_title=section_title,
        subsection_title=subsection_title,
        figure_reference=_first_string(evidence_record, "figure_reference"),
        table_reference=_first_string(evidence_record, "table_reference"),
        chunk_ids=chunk_ids,
    )


def _validation_finding_projection(
    *,
    envelope: DomainEnvelope,
    envelope_revision: int,
    finding: ValidationFinding,
    finding_index: int,
    object_id: str | None,
    object_type: str | None,
    field_path: str | None,
) -> DomainEnvelopeValidationFindingProjection:
    summary_status = _validation_status(finding)
    finding_id = finding.finding_id or _projection_id(
        "validation-finding",
        envelope.envelope_id,
        envelope_revision,
        finding_index,
        finding.model_dump(mode="json"),
    )
    return DomainEnvelopeValidationFindingProjection(
        finding_id=finding_id,
        envelope_id=envelope.envelope_id,
        object_id=object_id,
        object_type=object_type,
        field_path=field_path,
        envelope_revision=envelope_revision,
        severity=finding.severity.value,
        finding_status=finding.status.value,
        summary_status=summary_status,
        code=finding.code,
        message=finding.message,
        details=dict(finding.details),
    )


def _validation_summary_projection(
    *,
    envelope_id: str,
    envelope_revision: int,
    object_id: str | None,
    object_type: str | None,
    field_path: str | None,
    findings: Sequence[DomainEnvelopeValidationFindingProjection],
) -> DomainEnvelopeValidationSummaryProjection:
    status = _highest_status(finding.summary_status for finding in findings)
    highest_severity = _highest_severity(finding.severity for finding in findings)
    ordered_findings = sorted(
        findings,
        key=lambda finding: (
            -SEVERITY_RANK.get(finding.severity, -1),
            -VALIDATION_STATUS_RANK[finding.summary_status],
            finding.finding_id,
        ),
    )
    return DomainEnvelopeValidationSummaryProjection(
        summary_id=_projection_id(
            "validation-summary",
            envelope_id,
            envelope_revision,
            object_id,
            field_path,
        ),
        envelope_id=envelope_id,
        object_id=object_id,
        object_type=object_type,
        field_path=field_path,
        envelope_revision=envelope_revision,
        status=status,
        highest_severity=highest_severity,
        finding_count=len(ordered_findings),
        open_finding_count=sum(
            1
            for finding in ordered_findings
            if finding.finding_status == ValidationFindingStatus.OPEN.value
        ),
        finding_ids=[finding.finding_id for finding in ordered_findings],
        codes=_unique_strings(finding.code for finding in ordered_findings),
        messages=_unique_strings(finding.message for finding in ordered_findings),
        findings=list(ordered_findings),
    )


def _validation_status(finding: ValidationFinding) -> DomainEnvelopeValidationStatus:
    if finding.status is ValidationFindingStatus.RESOLVED:
        return DomainEnvelopeValidationStatus.RESOLVED
    if finding.status is ValidationFindingStatus.WAIVED:
        return DomainEnvelopeValidationStatus.WAIVED

    details = dict(finding.details)
    validation_metadata = details.get("validation_metadata")
    if isinstance(validation_metadata, Mapping):
        binding_state = _optional_string(validation_metadata.get("binding_state"))
        if binding_state == DomainEnvelopeValidationStatus.BLOCKED.value:
            return DomainEnvelopeValidationStatus.BLOCKED
        if binding_state == DomainEnvelopeValidationStatus.PLANNED.value:
            return DomainEnvelopeValidationStatus.PLANNED
        if _optional_string(validation_metadata.get("definition_state")) == "in_development":
            return DomainEnvelopeValidationStatus.UNDER_DEVELOPMENT

    if _optional_string(details.get("failure_classification")) == "blocked":
        return DomainEnvelopeValidationStatus.BLOCKED
    if _optional_string(details.get("failure_classification")) == "under_development":
        return DomainEnvelopeValidationStatus.UNDER_DEVELOPMENT

    lookup_attempts = details.get("lookup_attempts")
    if isinstance(lookup_attempts, list):
        lookup_statuses = {
            _optional_string(attempt.get("lookup_status"))
            for attempt in lookup_attempts
            if isinstance(attempt, Mapping)
        }
        if "blocked" in lookup_statuses:
            return DomainEnvelopeValidationStatus.BLOCKED
        if "under_development" in lookup_statuses:
            return DomainEnvelopeValidationStatus.UNDER_DEVELOPMENT

    if finding.severity is ValidationFindingSeverity.BLOCKER:
        return DomainEnvelopeValidationStatus.BLOCKED
    return DomainEnvelopeValidationStatus.UNRESOLVED


def _finding_target(
    finding: ValidationFinding,
    object_id_by_ref: Mapping[tuple[str, str], str],
) -> tuple[str | None, str | None]:
    if finding.field_ref is not None:
        return (
            _resolve_object_ref(finding.field_ref.object_ref, object_id_by_ref),
            finding.field_ref.field_path,
        )
    if finding.object_ref is not None:
        return _resolve_object_ref(finding.object_ref, object_id_by_ref), None
    return None, None


def _projection_field_paths(evidence_record: Mapping[str, Any]) -> list[str | None]:
    field_paths = _unique_strings(evidence_record.get("field_paths"))
    if not field_paths:
        field_path = _optional_string(evidence_record.get("field_path"))
        if field_path is not None:
            field_paths = [field_path]
    return list(field_paths) if field_paths else [None]


def _quote_from_anchor(anchor: EvidenceAnchor) -> str | None:
    for value in (anchor.snippet_text, anchor.sentence_text, anchor.viewer_search_text):
        normalized = _optional_string(value)
        if normalized is not None:
            return normalized
    return None


def _chunk_ids(evidence_record: Mapping[str, Any]) -> list[str]:
    raw_chunk_ids = evidence_record.get("chunk_ids")
    chunk_ids = _unique_strings(raw_chunk_ids)
    chunk_id = _optional_string(evidence_record.get("chunk_id"))
    if chunk_id is not None and chunk_id not in chunk_ids:
        chunk_ids.append(chunk_id)
    return chunk_ids


def _object_id_by_ref(envelope: DomainEnvelope) -> dict[tuple[str, str], str]:
    object_id_by_ref: dict[tuple[str, str], str] = {}
    for domain_object in envelope.objects:
        stable_object_id = _stable_object_id(domain_object)
        if domain_object.object_id is not None:
            object_id_by_ref[("object_id", domain_object.object_id)] = stable_object_id
        if domain_object.pending_ref_id is not None:
            object_id_by_ref[("pending_ref_id", domain_object.pending_ref_id)] = (
                stable_object_id
            )
    return object_id_by_ref


def _resolve_object_ref(
    object_ref: ObjectRef,
    object_id_by_ref: Mapping[tuple[str, str], str],
) -> str | None:
    return object_id_by_ref.get(object_ref.ref_key())


def _stable_object_id(domain_object: CuratableObjectEnvelope) -> str:
    if domain_object.object_id is not None:
        return domain_object.object_id
    if domain_object.pending_ref_id is not None:
        return domain_object.pending_ref_id
    raise ValueError("CuratableObjectEnvelope must provide object_id or pending_ref_id")


def _highest_status(
    statuses: Sequence[DomainEnvelopeValidationStatus] | Any,
) -> DomainEnvelopeValidationStatus:
    return max(
        statuses,
        key=lambda status: VALIDATION_STATUS_RANK[status],
        default=DomainEnvelopeValidationStatus.RESOLVED,
    )


def _highest_severity(severities: Sequence[str] | Any) -> str | None:
    highest: str | None = None
    for severity in severities:
        if severity not in SEVERITY_RANK:
            continue
        if highest is None or SEVERITY_RANK[severity] > SEVERITY_RANK[highest]:
            highest = severity
    return highest


def _projection_id(*parts: Any) -> str:
    payload = json.dumps(parts, sort_keys=True, default=str)
    digest = sha256(payload.encode("utf-8")).hexdigest()
    return f"domain-projection:{digest}"


def _first_string(record: Mapping[str, Any], *keys: str) -> str | None:
    for key in keys:
        normalized = _optional_string(record.get(key))
        if normalized is not None:
            return normalized
    return None


def _optional_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _unique_strings(values: Any) -> list[str]:
    if isinstance(values, str):
        iterable: Sequence[Any] = [values]
    elif isinstance(values, Iterable):
        iterable = values
    else:
        return []

    unique_values: list[str] = []
    seen: set[str] = set()
    for value in iterable:
        normalized = _optional_string(value)
        if normalized is None or normalized in seen:
            continue
        seen.add(normalized)
        unique_values.append(normalized)
    return unique_values


def _page_number(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 1:
        return value
    return None


__all__ = [
    "project_evidence_anchor_projections",
    "project_validation_summary_projections",
]
