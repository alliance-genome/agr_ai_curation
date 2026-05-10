"""Convert gene-expression extraction envelopes into pending domain envelopes."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from pydantic import model_validator

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
    ValidationFindingStatus,
    field_path_exists,
)
from src.schemas.models.domain_envelope_extraction import DomainEnvelopeExtractionResult

from ..schema_refs import (
    ALLIANCE_LINKML_COMMIT,
    ALLIANCE_LINKML_PROVIDER_KEY,
    OBJECT_ROLE_METADATA_KEY,
    PROVIDER_REFS_METADATA_KEY,
)
from .constants import (
    GENE_EXPRESSION_DOMAIN_PACK_CONVERTER_ID,
    GENE_EXPRESSION_DOMAIN_PACK_ID,
    GENE_EXPRESSION_DOMAIN_PACK_VERSION,
    GENE_EXPRESSION_LINKML_SCHEMA_ID,
    GENE_EXPRESSION_LINKML_SCHEMA_NAME,
    GENE_EXPRESSION_LINKML_SCHEMA_URI,
    GENE_EXPRESSION_MODEL_ID,
    GENE_EXPRESSION_OBJECT_ROLE,
    GENE_EXPRESSION_OBJECT_TYPE,
)


REQUIRED_GENE_EXPRESSION_PAYLOAD_FIELDS = frozenset(
    {
        "date_created",
        "internal",
        "data_provider",
        "data_provider.abbreviation",
        "expression_annotation_subject",
        "expression_annotation_subject.primary_external_id",
        "expression_annotation_subject.gene_symbol",
        "relation",
        "relation.name",
        "single_reference",
        "single_reference.reference_id",
        "expression_experiment",
        "expression_experiment.unique_id",
        "expression_experiment.expression_assay_used",
        "expression_experiment.expression_assay_used.curie",
        "when_expressed_stage_name",
        "where_expressed_statement",
        "expression_pattern",
        "expression_pattern.where_expressed",
    }
)
FORBIDDEN_PAYLOAD_EVIDENCE_FIELDS = frozenset(
    {
        "evidence_text",
        "evidence_page_numbers",
        "evidence_figure_references",
        "evidence_internal_citations",
    }
)
FORBIDDEN_LEGACY_COLLECTIONS = frozenset(
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


class GeneExpressionExtractionOutput(DomainEnvelopeExtractionResult):
    """Validated extractor output for one gene-expression domain-envelope run."""

    @model_validator(mode="after")
    def _validate_gene_expression_objects(self) -> "GeneExpressionExtractionOutput":
        findings = validate_gene_expression_extraction_objects(self)
        if findings:
            raise ValueError("; ".join(findings))
        return self


def validate_gene_expression_extraction_objects(
    output: DomainEnvelopeExtractionResult,
) -> tuple[str, ...]:
    """Return validation error messages for gene-expression extractor output."""

    errors: list[str] = []
    evidence_ids = {
        evidence.evidence_record_id
        for evidence in output.metadata.evidence_records
        if evidence.evidence_record_id
    }
    for index, obj in enumerate(output.curatable_objects):
        location = f"curatable_objects[{index}]"
        if obj.object_type != GENE_EXPRESSION_OBJECT_TYPE:
            errors.append(f"{location}.object_type must be {GENE_EXPRESSION_OBJECT_TYPE}")
        if obj.object_role != GENE_EXPRESSION_OBJECT_ROLE:
            errors.append(f"{location}.object_role must be {GENE_EXPRESSION_OBJECT_ROLE}")
        if obj.model_ref != GENE_EXPRESSION_MODEL_ID:
            errors.append(f"{location}.model_ref must be {GENE_EXPRESSION_MODEL_ID}")
        if obj.schema_ref is None:
            errors.append(f"{location}.schema_ref is required")
        elif obj.schema_ref.schema_id != GENE_EXPRESSION_LINKML_SCHEMA_ID:
            errors.append(
                f"{location}.schema_ref.schema_id must be "
                f"{GENE_EXPRESSION_LINKML_SCHEMA_ID}"
            )

        forbidden_payload_fields = sorted(
            FORBIDDEN_PAYLOAD_EVIDENCE_FIELDS.intersection(obj.payload)
        )
        if forbidden_payload_fields:
            errors.append(
                f"{location}.payload stores evidence fields "
                f"{', '.join(forbidden_payload_fields)}; use "
                "metadata.evidence_records[] plus evidence_record_ids[]"
            )

        missing_payload_fields = sorted(
            field_path
            for field_path in REQUIRED_GENE_EXPRESSION_PAYLOAD_FIELDS
            if not field_path_exists(obj.payload, field_path)
        )
        if missing_payload_fields:
            errors.append(
                f"{location}.payload is missing required fields: "
                + ", ".join(missing_payload_fields)
            )

        if not obj.evidence_record_ids:
            errors.append(f"{location}.evidence_record_ids must not be empty")
        missing_evidence_ids = sorted(
            evidence_id
            for evidence_id in obj.evidence_record_ids
            if evidence_id not in evidence_ids
        )
        if missing_evidence_ids:
            errors.append(
                f"{location}.evidence_record_ids references unknown "
                "metadata.evidence_records IDs: "
                + ", ".join(missing_evidence_ids)
            )

        if output.repair_mode:
            if not obj.field_refs:
                errors.append(
                    f"{location}.field_refs must identify repaired field paths "
                    "when repair_mode is true"
                )
            object_ref_keys = set(obj.ref_keys())
            for field_ref_index, field_ref in enumerate(obj.field_refs):
                if field_ref.object_ref.ref_key() not in object_ref_keys:
                    errors.append(
                        f"{location}.field_refs[{field_ref_index}].object_ref "
                        "must point at the repaired object"
                    )

    if output.repair_mode and not output.metadata.repair_notes:
        errors.append("metadata.repair_notes must describe repair-mode changes")
    return tuple(errors)


def _gene_expression_schema_ref() -> SchemaRef:
    return SchemaRef(
        schema_id=GENE_EXPRESSION_LINKML_SCHEMA_ID,
        provider=ALLIANCE_LINKML_PROVIDER_KEY,
        name=GENE_EXPRESSION_LINKML_SCHEMA_NAME,
        version=ALLIANCE_LINKML_COMMIT,
        uri=GENE_EXPRESSION_LINKML_SCHEMA_URI,
        metadata={
            PROVIDER_REFS_METADATA_KEY: {
                ALLIANCE_LINKML_PROVIDER_KEY: {
                    "schema_ref": "alliance.linkml",
                    "commit": ALLIANCE_LINKML_COMMIT,
                    "source_file": "model/schema/expression.yaml",
                    "class": GENE_EXPRESSION_OBJECT_TYPE,
                }
            }
        },
    )


def _object_metadata(source_metadata: Mapping[str, Any] | None = None) -> dict[str, Any]:
    metadata = dict(source_metadata or {})
    metadata.setdefault(OBJECT_ROLE_METADATA_KEY, GENE_EXPRESSION_OBJECT_ROLE)
    metadata.setdefault(
        PROVIDER_REFS_METADATA_KEY,
        {
            ALLIANCE_LINKML_PROVIDER_KEY: {
                "schema_ref": "alliance.linkml",
                "commit": ALLIANCE_LINKML_COMMIT,
                "source_file": "model/schema/expression.yaml",
                "class": GENE_EXPRESSION_OBJECT_TYPE,
            }
        },
    )
    return metadata


def _object_ref(obj: CuratableObjectEnvelope) -> ObjectRef:
    if obj.pending_ref_id:
        return ObjectRef(
            pending_ref_id=obj.pending_ref_id,
            object_type=obj.object_type,
        )
    if obj.object_id:
        return ObjectRef(object_id=obj.object_id, object_type=obj.object_type)
    raise ValueError("GeneExpressionAnnotation object is missing an object ref")


def _pending_object_from_extraction_object(
    obj: CuratableObjectEnvelope,
) -> CuratableObjectEnvelope:
    metadata_refs = [
        ref.model_copy(
            update={"metadata_path": f"extraction_metadata.{ref.metadata_path}"}
        )
        for ref in obj.metadata_refs
    ]
    return CuratableObjectEnvelope(
        object_type=GENE_EXPRESSION_OBJECT_TYPE,
        object_role=GENE_EXPRESSION_OBJECT_ROLE,
        payload=dict(obj.payload),
        object_id=obj.object_id,
        pending_ref_id=obj.pending_ref_id,
        schema_ref=obj.schema_ref,
        model_ref=obj.model_ref,
        status=CuratableObjectStatus.PENDING,
        definition_state=DefinitionState.STABLE,
        definition_notes=list(obj.definition_notes or []),
        object_refs=list(obj.object_refs),
        field_refs=list(obj.field_refs),
        evidence_record_ids=list(obj.evidence_record_ids),
        metadata_refs=metadata_refs,
        repair_hints=list(obj.repair_hints),
        metadata=_object_metadata(obj.metadata),
    )


def _iter_mapping_keys(value: Any):
    if isinstance(value, Mapping):
        yield from value.keys()
        for child in value.values():
            yield from _iter_mapping_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_mapping_keys(child)


def _legacy_keys_in_envelope(envelope: DomainEnvelope) -> set[str]:
    return set(
        FORBIDDEN_LEGACY_COLLECTIONS.intersection(
            _iter_mapping_keys(envelope.model_dump(mode="python"))
        )
    )


def gene_expression_extraction_output_to_pending_envelope(
    payload: Mapping[str, Any] | GeneExpressionExtractionOutput,
    *,
    envelope_id: str,
    document_id: str | None = None,
    produced_by: str = "gene_expression_extraction",
    produced_at: datetime | None = None,
) -> DomainEnvelope:
    """Build a pending gene-expression envelope from extractor output."""

    source = (
        payload
        if isinstance(payload, GeneExpressionExtractionOutput)
        else GeneExpressionExtractionOutput.model_validate(payload)
    )
    timestamp = produced_at or datetime.now(timezone.utc)

    objects = [
        _pending_object_from_extraction_object(obj)
        for obj in source.curatable_objects
    ]
    history: list[HistoryEvent] = [
        HistoryEvent(
            event_type=HistoryEventKind.CREATED,
            timestamp=timestamp,
            actor_type=HistoryActorType.SYSTEM,
            actor_id=GENE_EXPRESSION_DOMAIN_PACK_CONVERTER_ID,
            message=(
                "Converted gene-expression extraction output to a pending "
                "domain envelope."
            ),
            details={"source_agent": produced_by},
        )
    ]
    validation_findings: list[ValidationFinding] = []
    for obj in objects:
        object_ref = _object_ref(obj)
        validation_findings.append(
            ValidationFinding(
                severity=ValidationFindingSeverity.INFO,
                status=ValidationFindingStatus.RESOLVED,
                code="alliance.gene_expression.tool_verified",
                message=(
                    "GeneExpressionAnnotation was converted from domain-envelope "
                    "extractor output."
                ),
                object_ref=object_ref,
                details={"semantic_source": "curatable_objects[]"},
            )
        )
        history.append(
            HistoryEvent(
                event_type=HistoryEventKind.OBJECT_EXTRACTED,
                timestamp=timestamp,
                actor_type=HistoryActorType.SYSTEM,
                actor_id=GENE_EXPRESSION_DOMAIN_PACK_CONVERTER_ID,
                message="Added pending GeneExpressionAnnotation.",
                object_ref=object_ref,
                details={"evidence_record_ids": list(obj.evidence_record_ids)},
            )
        )

    metadata: dict[str, Any] = {
        "source_agent": produced_by,
        "conversion": "gene_expression_extraction_output_to_pending_envelope",
        "semantic_source": "domain_envelope.objects",
        "legacy_semantic_lists": [],
        "extraction_summary": source.summary,
        "extraction_metadata": source.metadata.model_dump(mode="python"),
        "run_summary": source.run_summary.model_dump(mode="python"),
        "repair_mode": source.repair_mode,
    }
    if document_id is not None:
        metadata["source_document_id"] = document_id

    return DomainEnvelope(
        envelope_id=envelope_id,
        domain_pack_id=GENE_EXPRESSION_DOMAIN_PACK_ID,
        domain_pack_version=GENE_EXPRESSION_DOMAIN_PACK_VERSION,
        status=DomainEnvelopeStatus.EXTRACTED,
        schema_ref=_gene_expression_schema_ref(),
        objects=objects,
        validation_findings=validation_findings,
        history=history,
        metadata=metadata,
    )


def validate_pending_gene_expression_envelope(
    envelope: DomainEnvelope,
) -> tuple[ValidationFinding, ...]:
    """Return domain-pack validation findings for one gene-expression envelope."""

    findings: list[ValidationFinding] = []
    if envelope.domain_pack_id != GENE_EXPRESSION_DOMAIN_PACK_ID:
        findings.append(
            ValidationFinding(
                severity=ValidationFindingSeverity.ERROR,
                code="alliance.gene_expression.domain_pack_mismatch",
                message=(
                    f"Expected domain_pack_id {GENE_EXPRESSION_DOMAIN_PACK_ID}, "
                    f"found {envelope.domain_pack_id}."
                ),
            )
        )

    legacy_keys = _legacy_keys_in_envelope(envelope)
    if legacy_keys:
        findings.append(
            ValidationFinding(
                severity=ValidationFindingSeverity.ERROR,
                code="alliance.gene_expression.legacy_semantic_store_present",
                message=(
                    "Gene-expression domain envelopes must use envelope objects "
                    "as the semantic source of truth; legacy semantic collections "
                    "are not allowed."
                ),
                details={"legacy_keys": sorted(legacy_keys)},
            )
        )

    expression_objects = [
        obj for obj in envelope.objects if obj.object_type == GENE_EXPRESSION_OBJECT_TYPE
    ]
    if not expression_objects:
        findings.append(
            ValidationFinding(
                severity=ValidationFindingSeverity.ERROR,
                code="alliance.gene_expression.missing_annotation",
                message="Envelope must contain at least one GeneExpressionAnnotation.",
            )
        )

    extraction_metadata = envelope.metadata.get("extraction_metadata")
    evidence_records = (
        extraction_metadata.get("evidence_records")
        if isinstance(extraction_metadata, Mapping)
        else None
    )
    evidence_ids = (
        {
            record.get("evidence_record_id")
            for record in evidence_records
            if isinstance(record, Mapping) and record.get("evidence_record_id")
        }
        if isinstance(evidence_records, list)
        else set()
    )

    for expression_object in expression_objects:
        object_ref = _object_ref(expression_object)
        if expression_object.status != CuratableObjectStatus.PENDING:
            findings.append(
                ValidationFinding(
                    severity=ValidationFindingSeverity.ERROR,
                    code="alliance.gene_expression.object_not_pending",
                    message="GeneExpressionAnnotation objects must be pending after conversion.",
                    object_ref=object_ref,
                )
            )

        missing_fields = [
            field_path
            for field_path in sorted(REQUIRED_GENE_EXPRESSION_PAYLOAD_FIELDS)
            if not field_path_exists(expression_object.payload, field_path)
        ]
        if missing_fields:
            findings.append(
                ValidationFinding(
                    severity=ValidationFindingSeverity.ERROR,
                    code="alliance.gene_expression.required_payload_fields_missing",
                    message=(
                        "GeneExpressionAnnotation is missing required payload fields: "
                        + ", ".join(missing_fields)
                    ),
                    object_ref=object_ref,
                    details={"missing_fields": missing_fields},
                )
            )

        forbidden_payload_fields = sorted(
            FORBIDDEN_PAYLOAD_EVIDENCE_FIELDS.intersection(expression_object.payload)
        )
        if forbidden_payload_fields:
            findings.append(
                ValidationFinding(
                    severity=ValidationFindingSeverity.ERROR,
                    code="alliance.gene_expression.payload_evidence_present",
                    message=(
                        "Verified evidence belongs in envelope metadata, not payload: "
                        + ", ".join(forbidden_payload_fields)
                    ),
                    object_ref=object_ref,
                    details={"payload_fields": forbidden_payload_fields},
                )
            )

        where_expressed = (
            expression_object.payload.get("expression_pattern", {})
            .get("where_expressed", {})
            if isinstance(expression_object.payload.get("expression_pattern"), Mapping)
            else {}
        )
        if not (
            isinstance(where_expressed, Mapping)
            and (
                "anatomical_structure" in where_expressed
                or "cellular_component" in where_expressed
            )
        ):
            findings.append(
                ValidationFinding(
                    severity=ValidationFindingSeverity.ERROR,
                    code="alliance.gene_expression.anatomical_site_missing",
                    message=(
                        "expression_pattern.where_expressed must include "
                        "anatomical_structure or cellular_component."
                    ),
                    object_ref=object_ref,
                )
            )

        if not expression_object.evidence_record_ids:
            findings.append(
                ValidationFinding(
                    severity=ValidationFindingSeverity.ERROR,
                    code="alliance.gene_expression.evidence_record_ids_missing",
                    message="GeneExpressionAnnotation requires verified evidence_record_ids.",
                    object_ref=object_ref,
                )
            )
        missing_evidence_ids = sorted(
            evidence_id
            for evidence_id in expression_object.evidence_record_ids
            if evidence_id not in evidence_ids
        )
        if missing_evidence_ids:
            findings.append(
                ValidationFinding(
                    severity=ValidationFindingSeverity.ERROR,
                    code="alliance.gene_expression.evidence_records_missing",
                    message=(
                        "GeneExpressionAnnotation references evidence IDs missing "
                        "from envelope metadata: "
                        + ", ".join(missing_evidence_ids)
                    ),
                    object_ref=object_ref,
                    details={"missing_evidence_record_ids": missing_evidence_ids},
                )
            )

        missing_metadata_refs = [
            metadata_ref.metadata_path
            for metadata_ref in expression_object.metadata_refs
            if not field_path_exists(envelope.metadata, metadata_ref.metadata_path)
        ]
        if missing_metadata_refs:
            findings.append(
                ValidationFinding(
                    severity=ValidationFindingSeverity.ERROR,
                    code="alliance.gene_expression.metadata_refs_missing",
                    message=(
                        "GeneExpressionAnnotation metadata_refs must resolve inside "
                        "envelope metadata: "
                        + ", ".join(missing_metadata_refs)
                    ),
                    object_ref=object_ref,
                    details={"missing_metadata_refs": missing_metadata_refs},
                )
            )

    return tuple(findings)


__all__ = [
    "FORBIDDEN_LEGACY_COLLECTIONS",
    "FORBIDDEN_PAYLOAD_EVIDENCE_FIELDS",
    "GeneExpressionExtractionOutput",
    "REQUIRED_GENE_EXPRESSION_PAYLOAD_FIELDS",
    "gene_expression_extraction_output_to_pending_envelope",
    "validate_gene_expression_extraction_objects",
    "validate_pending_gene_expression_envelope",
]
