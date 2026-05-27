"""Convert gene-expression extraction envelopes into pending domain envelopes."""

from __future__ import annotations

import copy
import logging
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
    FieldRef,
    HistoryActorType,
    HistoryEvent,
    HistoryEventKind,
    ObjectRef,
    SchemaRef,
    ValidationFinding,
    ValidationFindingSeverity,
    ValidationFindingStatus,
    field_path_exists,
    parse_field_path,
)
from src.schemas.models.domain_envelope_extraction import DomainEnvelopeExtractionResult

from ..schema_refs import (
    ALLIANCE_LINKML_COMMIT,
    ALLIANCE_LINKML_PROVIDER_KEY,
    OBJECT_ROLE_METADATA_KEY,
    PROVIDER_REFS_METADATA_KEY,
)
from ._payload_terms import has_term_selector as _has_term_selector
from ._payload_terms import value_missing_or_blank as _value_missing_or_blank
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
        "expression_experiment.single_reference",
        "expression_experiment.single_reference.reference_id",
        "expression_experiment.entity_assayed",
        "expression_experiment.entity_assayed.primary_external_id",
        "expression_experiment.entity_assayed.gene_symbol",
        "expression_experiment.expression_assay_used",
        "expression_experiment.expression_assay_used.curie",
        "when_expressed_stage_name",
        "where_expressed_statement",
        "expression_pattern",
        "expression_pattern.where_expressed",
    }
)
MATERIALIZER_RESOLVABLE_EXTRACTION_FIELDS = frozenset(
    {
        "expression_experiment.expression_assay_used.curie",
    }
)
FIELD_SPECIFIC_GENE_EXPRESSION_PAYLOAD_FIELDS = frozenset(
    {
        "data_provider.abbreviation",
        "expression_annotation_subject.primary_external_id",
        "expression_annotation_subject.gene_symbol",
        "relation.name",
        "single_reference.reference_id",
        "expression_experiment.single_reference.reference_id",
        "expression_experiment.expression_assay_used.curie",
        "expression_experiment.entity_assayed.primary_external_id",
        "expression_experiment.entity_assayed.gene_symbol",
        "when_expressed_stage_name",
        "where_expressed_statement",
        "expression_pattern.where_expressed",
    }
)
GENE_EXPRESSION_LINKML_CONTRACT_VALIDATOR_ID = (
    "gene_expression.linkml_extraction_contract"
)
VALID_GENE_EXPRESSION_RELATION_NAMES = frozenset({"is_expressed_in"})
EXPRESSION_RELATION_VOCABULARY = "Expression Relation"
CONTROLLED_FIELD_HELPER_TOOL_NAME = "get_domain_field_term_options"
LOGGER = logging.getLogger(__name__)
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


def _has_anatomical_site_slot(where_expressed: Any) -> bool:
    if not isinstance(where_expressed, Mapping):
        return False
    return (
        _has_term_selector(where_expressed.get("anatomical_structure"))
        or _has_term_selector(where_expressed.get("cellular_component"))
    )


def _payload_value(payload: Mapping[str, Any], field_path: str) -> Any:
    current: Any = payload
    for part in parse_field_path(field_path):
        if isinstance(part, str):
            if not isinstance(current, Mapping) or part not in current:
                return None
            current = current[part]
            continue
        if not isinstance(current, list) or part >= len(current):
            return None
        current = current[part]
    return current


def _payload_value_missing_or_blank(payload: Mapping[str, Any], field_path: str) -> bool:
    if not field_path_exists(payload, field_path):
        return True
    return _value_missing_or_blank(_payload_value(payload, field_path))


def _helper_selections(output: DomainEnvelopeExtractionResult) -> list[Mapping[str, Any]]:
    selections = output.metadata.provenance.get("helper_selections")
    if not isinstance(selections, list):
        return []
    valid_selections: list[Mapping[str, Any]] = []
    dropped_count = 0
    for entry in selections:
        if isinstance(entry, Mapping):
            valid_selections.append(entry)
        else:
            dropped_count += 1
    if dropped_count:
        LOGGER.warning(
            "Dropped %s malformed gene expression helper_selections entries",
            dropped_count,
        )
    return valid_selections


def _has_helper_selection(
    output: DomainEnvelopeExtractionResult,
    *,
    field_path: str,
    selected_value: str,
) -> bool:
    for selection in _helper_selections(output):
        if selection.get("field_path") != field_path:
            continue
        if selection.get("source_tool") != CONTROLLED_FIELD_HELPER_TOOL_NAME:
            continue
        value = selection.get("selected_value")
        if isinstance(value, str) and value.strip() == selected_value:
            return True
    return False


def _diagnostic_details(
    *,
    blocking: bool = True,
    classification: str = "repairable_extraction_error",
    **details: Any,
) -> dict[str, Any]:
    payload = {
        "validator_id": GENE_EXPRESSION_LINKML_CONTRACT_VALIDATOR_ID,
        "validation_stage": "extraction",
        "blocking": blocking,
        "classification": classification,
        "repairable": classification == "repairable_extraction_error",
    }
    payload.update({key: value for key, value in details.items() if value is not None})
    return payload


def _validation_finding(
    *,
    object_ref: ObjectRef,
    field_path: str | None,
    code: str,
    message: str,
    severity: ValidationFindingSeverity = ValidationFindingSeverity.BLOCKER,
    details: Mapping[str, Any] | None = None,
) -> ValidationFinding:
    field_ref = (
        FieldRef(object_ref=object_ref, field_path=field_path)
        if field_path is not None
        else None
    )
    return ValidationFinding(
        severity=severity,
        code=code,
        message=message,
        object_ref=None if field_ref is not None else object_ref,
        field_ref=field_ref,
        details=dict(details or {}),
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
            if field_path not in MATERIALIZER_RESOLVABLE_EXTRACTION_FIELDS
            if _payload_value_missing_or_blank(obj.payload, field_path)
        )
        if missing_payload_fields:
            errors.append(
                f"{location}.payload is missing required fields: "
                + ", ".join(missing_payload_fields)
            )
        relation = obj.payload.get("relation")
        relation_name = (
            relation.get("name") if isinstance(relation, Mapping) else None
        )
        if not isinstance(relation_name, str) or not relation_name.strip():
            errors.append(
                f"{location}.payload relation.name must be selected explicitly "
                "from domain-pack term helper options"
            )
        elif relation_name.strip() not in VALID_GENE_EXPRESSION_RELATION_NAMES:
            errors.append(
                f"{location}.payload relation.name must be a valid "
                f"{EXPRESSION_RELATION_VOCABULARY} option"
            )
        elif not _has_helper_selection(
            output,
            field_path="relation.name",
            selected_value=relation_name.strip(),
        ):
            errors.append(
                f"{location}.payload relation.name must include "
                "metadata.provenance.helper_selections[] evidence from "
                f"{CONTROLLED_FIELD_HELPER_TOOL_NAME}"
            )
        data_provider = obj.payload.get("data_provider")
        provider_abbreviation = (
            data_provider.get("abbreviation")
            if isinstance(data_provider, Mapping)
            else None
        )
        if (
            not isinstance(provider_abbreviation, str)
            or not provider_abbreviation.strip()
        ):
            errors.append(
                f"{location}.payload data_provider.abbreviation must be a "
                "non-empty Alliance provider abbreviation"
            )

        where_expressed = (
            obj.payload.get("expression_pattern", {}).get("where_expressed", {})
            if isinstance(obj.payload.get("expression_pattern"), Mapping)
            else {}
        )
        if not _has_anatomical_site_slot(where_expressed):
            errors.append(
                f"{location}.payload expression_pattern.where_expressed must "
                "include anatomical_structure or cellular_component"
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
        payload=copy.deepcopy(dict(obj.payload)),
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


def _required_payload_field_findings(
    expression_object: CuratableObjectEnvelope,
    object_ref: ObjectRef,
) -> list[ValidationFinding]:
    findings: list[ValidationFinding] = []
    for field_path in sorted(REQUIRED_GENE_EXPRESSION_PAYLOAD_FIELDS):
        if field_path in FIELD_SPECIFIC_GENE_EXPRESSION_PAYLOAD_FIELDS:
            continue
        if not _payload_value_missing_or_blank(expression_object.payload, field_path):
            continue
        findings.append(
            _validation_finding(
                object_ref=object_ref,
                field_path=field_path,
                code="alliance.gene_expression.required_field_missing",
                message=(
                    "GeneExpressionAnnotation payload is missing a required "
                    f"LinkML field: {field_path}."
                ),
                details=_diagnostic_details(
                    submitted_value=_payload_value(expression_object.payload, field_path),
                    required_field=field_path,
                    source_schema=GENE_EXPRESSION_LINKML_SCHEMA_URI,
                ),
            )
        )
    return findings


def _required_selector_finding(
    *,
    expression_object: CuratableObjectEnvelope,
    object_ref: ObjectRef,
    field_path: str,
    code: str,
    message: str,
    expected_selector: str | None = None,
) -> ValidationFinding | None:
    if not _payload_value_missing_or_blank(expression_object.payload, field_path):
        return None
    return _validation_finding(
        object_ref=object_ref,
        field_path=field_path,
        code=code,
        message=message,
        details=_diagnostic_details(
            submitted_value=_payload_value(expression_object.payload, field_path),
            expected_selector=expected_selector,
            required_field=field_path,
        ),
    )


def _selector_integrity_findings(
    expression_object: CuratableObjectEnvelope,
    object_ref: ObjectRef,
) -> list[ValidationFinding]:
    findings: list[ValidationFinding] = []
    for candidate in (
        _required_selector_finding(
            expression_object=expression_object,
            object_ref=object_ref,
            field_path="data_provider.abbreviation",
            code="alliance.gene_expression.data_provider_abbreviation_missing",
            message=(
                "GeneExpressionAnnotation requires a non-empty "
                "data_provider.abbreviation selector."
            ),
            expected_selector="Alliance data provider abbreviation",
        ),
        _required_selector_finding(
            expression_object=expression_object,
            object_ref=object_ref,
            field_path="expression_annotation_subject.primary_external_id",
            code="alliance.gene_expression.subject_gene_missing",
            message=(
                "GeneExpressionAnnotation requires a subject gene "
                "primary_external_id selector."
            ),
            expected_selector="Alliance gene primary_external_id",
        ),
        _required_selector_finding(
            expression_object=expression_object,
            object_ref=object_ref,
            field_path="expression_annotation_subject.gene_symbol",
            code="alliance.gene_expression.subject_gene_missing",
            message="GeneExpressionAnnotation requires a subject gene symbol selector.",
            expected_selector="Alliance gene symbol",
        ),
        _required_selector_finding(
            expression_object=expression_object,
            object_ref=object_ref,
            field_path="single_reference.reference_id",
            code="alliance.gene_expression.reference_missing",
            message="GeneExpressionAnnotation requires a source reference selector.",
            expected_selector="PMID or Alliance reference identifier",
        ),
        _required_selector_finding(
            expression_object=expression_object,
            object_ref=object_ref,
            field_path="when_expressed_stage_name",
            code="alliance.gene_expression.expression_context_missing",
            message="GeneExpressionAnnotation requires when_expressed_stage_name.",
            expected_selector="paper-supported stage label",
        ),
        _required_selector_finding(
            expression_object=expression_object,
            object_ref=object_ref,
            field_path="where_expressed_statement",
            code="alliance.gene_expression.expression_context_missing",
            message="GeneExpressionAnnotation requires where_expressed_statement.",
            expected_selector="paper-supported expression site statement",
        ),
    ):
        if candidate is not None:
            findings.append(candidate)

    findings.extend(_relation_name_findings(expression_object, object_ref))
    findings.extend(_assay_method_findings(expression_object, object_ref))
    findings.extend(_experiment_projection_findings(expression_object, object_ref))
    findings.extend(_where_expressed_findings(expression_object, object_ref))
    return findings


def _relation_name_findings(
    expression_object: CuratableObjectEnvelope,
    object_ref: ObjectRef,
) -> list[ValidationFinding]:
    relation_name = _payload_value(expression_object.payload, "relation.name")
    if not isinstance(relation_name, str) or not relation_name.strip():
        return [
            _validation_finding(
                object_ref=object_ref,
                field_path="relation.name",
                code="alliance.gene_expression.relation_name_missing",
                message=(
                    "GeneExpressionAnnotation relation.name must be selected "
                    "explicitly from Expression Relation options."
                ),
                details=_diagnostic_details(
                    submitted_value=relation_name,
                    expected_vocabulary=EXPRESSION_RELATION_VOCABULARY,
                    expected_values=sorted(VALID_GENE_EXPRESSION_RELATION_NAMES),
                ),
            )
        ]
    normalized = relation_name.strip()
    if normalized in VALID_GENE_EXPRESSION_RELATION_NAMES:
        return []
    return [
        _validation_finding(
            object_ref=object_ref,
            field_path="relation.name",
            code="alliance.gene_expression.relation_name_invalid",
            message=(
                "GeneExpressionAnnotation relation.name must be a valid "
                "Expression Relation option."
            ),
            details=_diagnostic_details(
                submitted_value=relation_name,
                expected_vocabulary=EXPRESSION_RELATION_VOCABULARY,
                expected_values=sorted(VALID_GENE_EXPRESSION_RELATION_NAMES),
            ),
        )
    ]


def _assay_method_findings(
    expression_object: CuratableObjectEnvelope,
    object_ref: ObjectRef,
) -> list[ValidationFinding]:
    assay = _payload_value(
        expression_object.payload,
        "expression_experiment.expression_assay_used",
    )
    candidates = assay.get("candidates") if isinstance(assay, Mapping) else None
    if isinstance(candidates, list) and len(candidates) > 1:
        return [
            _validation_finding(
                object_ref=object_ref,
                field_path="expression_experiment.expression_assay_used",
                code="alliance.gene_expression.assay_method_ambiguous",
                message=(
                    "GeneExpressionAnnotation expression_assay_used must resolve "
                    "to one assay or method selector."
                ),
                details=_diagnostic_details(
                    submitted_value=assay,
                    candidate_count=len(candidates),
                    expected_selector="single MMO assay/method selector",
                ),
            )
        ]

    assay_curie = _payload_value(
        expression_object.payload,
        "expression_experiment.expression_assay_used.curie",
    )
    if not isinstance(assay_curie, str) or not assay_curie.strip():
        return [
            _validation_finding(
                object_ref=object_ref,
                field_path="expression_experiment.expression_assay_used.curie",
                code="alliance.gene_expression.assay_method_missing",
                message=(
                    "GeneExpressionAnnotation requires an assay or method CURIE "
                    "selector."
                ),
                details=_diagnostic_details(
                    submitted_value=assay_curie,
                    expected_selector="MMO assay/method CURIE",
                ),
            )
        ]
    if ":" not in assay_curie.strip():
        return [
            _validation_finding(
                object_ref=object_ref,
                field_path="expression_experiment.expression_assay_used.curie",
                code="alliance.gene_expression.assay_method_invalid",
                message=(
                    "GeneExpressionAnnotation expression_assay_used.curie must "
                    "be a CURIE-like selector."
                ),
                details=_diagnostic_details(
                    submitted_value=assay_curie,
                    expected_selector="MMO assay/method CURIE",
                ),
            )
        ]
    return []


def _experiment_projection_findings(
    expression_object: CuratableObjectEnvelope,
    object_ref: ObjectRef,
) -> list[ValidationFinding]:
    findings: list[ValidationFinding] = []
    equivalent_paths = (
        (
            "single_reference.reference_id",
            "expression_experiment.single_reference.reference_id",
            "alliance.gene_expression.experiment_reference_mismatch",
            "GeneExpressionExperiment single_reference must match the annotation single_reference for Gene Expression 0.7.0.",
        ),
        (
            "expression_annotation_subject.primary_external_id",
            "expression_experiment.entity_assayed.primary_external_id",
            "alliance.gene_expression.entity_assayed_mismatch",
            "GeneExpressionExperiment entity_assayed must match expression_annotation_subject for Gene Expression 0.7.0.",
        ),
        (
            "expression_annotation_subject.gene_symbol",
            "expression_experiment.entity_assayed.gene_symbol",
            "alliance.gene_expression.entity_assayed_mismatch",
            "GeneExpressionExperiment entity_assayed must match expression_annotation_subject for Gene Expression 0.7.0.",
        ),
    )
    for source_path, experiment_path, code, message in equivalent_paths:
        source_value = _payload_value(expression_object.payload, source_path)
        experiment_value = _payload_value(expression_object.payload, experiment_path)
        if _value_missing_or_blank(source_value) or _value_missing_or_blank(
            experiment_value
        ):
            continue
        if source_value == experiment_value:
            continue
        findings.append(
            _validation_finding(
                object_ref=object_ref,
                field_path=experiment_path,
                code=code,
                message=message,
                details=_diagnostic_details(
                    submitted_value=experiment_value,
                    expected_value=source_value,
                    equivalent_field_path=source_path,
                    source_schema=GENE_EXPRESSION_LINKML_SCHEMA_URI,
                ),
            )
        )
    return findings


def _where_expressed_findings(
    expression_object: CuratableObjectEnvelope,
    object_ref: ObjectRef,
) -> list[ValidationFinding]:
    where_expressed = _payload_value(
        expression_object.payload,
        "expression_pattern.where_expressed",
    )
    if _has_anatomical_site_slot(where_expressed):
        return []
    return [
        _validation_finding(
            object_ref=object_ref,
            field_path="expression_pattern.where_expressed",
            code="alliance.gene_expression.anatomical_site_missing",
            message=(
                "expression_pattern.where_expressed must include "
                "anatomical_structure or cellular_component."
            ),
            details=_diagnostic_details(
                submitted_value=where_expressed,
                expected_selector=(
                    "anatomical_structure or cellular_component term selector"
                ),
                source_schema=GENE_EXPRESSION_LINKML_SCHEMA_URI,
            ),
        )
    ]


def _evidence_record_findings(
    *,
    expression_object: CuratableObjectEnvelope,
    object_ref: ObjectRef,
    evidence_records_by_id: Mapping[str, Mapping[str, Any]],
) -> list[ValidationFinding]:
    if not expression_object.evidence_record_ids:
        return [
            _validation_finding(
                object_ref=object_ref,
                field_path="evidence_record_ids",
                code="alliance.gene_expression.evidence_record_ids_missing",
                message="GeneExpressionAnnotation requires verified evidence_record_ids.",
                details=_diagnostic_details(
                    submitted_value=[],
                    classification="non_repairable_extraction_error",
                    expected_selector="metadata.evidence_records[].evidence_record_id",
                ),
            )
        ]

    findings: list[ValidationFinding] = []
    missing_evidence_ids = sorted(
        evidence_id
        for evidence_id in expression_object.evidence_record_ids
        if evidence_id not in evidence_records_by_id
    )
    if missing_evidence_ids:
        findings.append(
            _validation_finding(
                object_ref=object_ref,
                field_path="evidence_record_ids",
                code="alliance.gene_expression.evidence_records_missing",
                message=(
                    "GeneExpressionAnnotation references evidence IDs missing "
                    "from envelope metadata: "
                    + ", ".join(missing_evidence_ids)
                ),
                details=_diagnostic_details(
                    submitted_value=list(expression_object.evidence_record_ids),
                    classification="non_repairable_extraction_error",
                    missing_evidence_record_ids=missing_evidence_ids,
                ),
            )
        )

    incomplete_evidence_ids = sorted(
        evidence_id
        for evidence_id in expression_object.evidence_record_ids
        if (
            evidence_id in evidence_records_by_id
            and _value_missing_or_blank(
                evidence_records_by_id[evidence_id].get("verified_quote")
            )
        )
    )
    if incomplete_evidence_ids:
        findings.append(
            _validation_finding(
                object_ref=object_ref,
                field_path="evidence_record_ids",
                code="alliance.gene_expression.evidence_record_incomplete",
                message=(
                    "GeneExpressionAnnotation evidence records must include "
                    "verified reference-backed quote text."
                ),
                details=_diagnostic_details(
                    submitted_value=incomplete_evidence_ids,
                    classification="non_repairable_extraction_error",
                    missing_fields=["verified_quote"],
                ),
            )
        )
    return findings


def _evidence_records_by_id(envelope: DomainEnvelope) -> dict[str, Mapping[str, Any]]:
    extraction_metadata = envelope.metadata.get("extraction_metadata")
    evidence_records = (
        extraction_metadata.get("evidence_records")
        if isinstance(extraction_metadata, Mapping)
        else None
    )
    if not isinstance(evidence_records, list):
        return {}
    return {
        evidence_record_id: record
        for record in evidence_records
        if (
            isinstance(record, Mapping)
            and isinstance(
                evidence_record_id := record.get("evidence_record_id"),
                str,
            )
            and evidence_record_id.strip()
        )
    }


def _metadata_ref_findings(
    *,
    envelope: DomainEnvelope,
    expression_object: CuratableObjectEnvelope,
    object_ref: ObjectRef,
) -> list[ValidationFinding]:
    missing_metadata_refs = [
        metadata_ref.metadata_path
        for metadata_ref in expression_object.metadata_refs
        if not field_path_exists(envelope.metadata, metadata_ref.metadata_path)
    ]
    if not missing_metadata_refs:
        return []
    return [
        _validation_finding(
            object_ref=object_ref,
            field_path="metadata_refs",
            code="alliance.gene_expression.metadata_refs_missing",
            message=(
                "GeneExpressionAnnotation metadata_refs must resolve inside "
                "envelope metadata: "
                + ", ".join(missing_metadata_refs)
            ),
            details=_diagnostic_details(
                classification="non_repairable_extraction_error",
                missing_metadata_refs=missing_metadata_refs,
            ),
        )
    ]


def _context_preservation_findings(
    expression_object: CuratableObjectEnvelope,
    object_ref: ObjectRef,
) -> list[ValidationFinding]:
    """Warn when optional LinkML-backed context expected by extraction is absent."""

    expectations = expression_object.metadata.get("expected_context_payload_paths")
    if not isinstance(expectations, list):
        return []

    findings: list[ValidationFinding] = []
    for expectation in expectations:
        if not isinstance(expectation, Mapping):
            continue
        field_path = expectation.get("field_path")
        if not isinstance(field_path, str) or not field_path.strip():
            continue
        normalized_path = field_path.strip()
        if not _payload_value_missing_or_blank(expression_object.payload, normalized_path):
            continue
        findings.append(
            _validation_finding(
                object_ref=object_ref,
                field_path=normalized_path,
                severity=ValidationFindingSeverity.WARNING,
                code="alliance.gene_expression.experiment_context_dropped",
                message=(
                    "GeneExpressionAnnotation extracted optional experiment "
                    f"context for {normalized_path}, but the LinkML-backed "
                    "payload field is missing."
                ),
                details=_diagnostic_details(
                    blocking=False,
                    classification="context_preservation_required",
                    expected_context_field=normalized_path,
                    source_metadata_path=expectation.get("source_metadata_path"),
                    reason_code=expectation.get("reason_code"),
                ),
            )
        )
    return findings


def validate_pending_gene_expression_envelope(
    envelope: DomainEnvelope,
) -> tuple[ValidationFinding, ...]:
    """Return domain-pack validation findings for one gene-expression envelope."""

    findings: list[ValidationFinding] = []
    if envelope.domain_pack_id != GENE_EXPRESSION_DOMAIN_PACK_ID:
        findings.append(
            ValidationFinding(
                severity=ValidationFindingSeverity.BLOCKER,
                code="alliance.gene_expression.domain_pack_mismatch",
                message=(
                    f"Expected domain_pack_id {GENE_EXPRESSION_DOMAIN_PACK_ID}, "
                    f"found {envelope.domain_pack_id}."
                ),
                details=_diagnostic_details(
                    classification="non_repairable_extraction_error",
                    submitted_value=envelope.domain_pack_id,
                    expected_value=GENE_EXPRESSION_DOMAIN_PACK_ID,
                ),
            )
        )

    legacy_keys = _legacy_keys_in_envelope(envelope)
    if legacy_keys:
        findings.append(
            ValidationFinding(
                severity=ValidationFindingSeverity.BLOCKER,
                code="alliance.gene_expression.legacy_semantic_store_present",
                message=(
                    "Gene-expression domain envelopes must use envelope objects "
                    "as the semantic source of truth; legacy semantic collections "
                    "are not allowed."
                ),
                details=_diagnostic_details(
                    classification="non_repairable_extraction_error",
                    legacy_keys=sorted(legacy_keys),
                ),
            )
        )

    expression_objects = [
        obj for obj in envelope.objects if obj.object_type == GENE_EXPRESSION_OBJECT_TYPE
    ]
    if not expression_objects:
        findings.append(
            ValidationFinding(
                severity=ValidationFindingSeverity.BLOCKER,
                code="alliance.gene_expression.missing_annotation",
                message="Envelope must contain at least one GeneExpressionAnnotation.",
                details=_diagnostic_details(
                    classification="non_repairable_extraction_error",
                    expected_object_type=GENE_EXPRESSION_OBJECT_TYPE,
                ),
            )
        )

    evidence_records_by_id = _evidence_records_by_id(envelope)

    for expression_object in expression_objects:
        object_ref = _object_ref(expression_object)
        if expression_object.status != CuratableObjectStatus.PENDING:
            findings.append(
                _validation_finding(
                    object_ref=object_ref,
                    field_path=None,
                    severity=ValidationFindingSeverity.BLOCKER,
                    code="alliance.gene_expression.object_not_pending",
                    message="GeneExpressionAnnotation objects must be pending after conversion.",
                    details=_diagnostic_details(
                        submitted_value=expression_object.status.value,
                        expected_value=CuratableObjectStatus.PENDING.value,
                    ),
                )
            )

        findings.extend(
            _required_payload_field_findings(expression_object, object_ref)
        )
        findings.extend(_selector_integrity_findings(expression_object, object_ref))

        forbidden_payload_fields = sorted(
            FORBIDDEN_PAYLOAD_EVIDENCE_FIELDS.intersection(expression_object.payload)
        )
        for field_path in forbidden_payload_fields:
            findings.append(
                _validation_finding(
                    object_ref=object_ref,
                    field_path=field_path,
                    code="alliance.gene_expression.payload_evidence_present",
                    message=(
                        "Verified evidence belongs in envelope metadata, not "
                        f"payload field {field_path}."
                    ),
                    details=_diagnostic_details(
                        classification="non_repairable_extraction_error",
                        submitted_value=expression_object.payload.get(field_path),
                        metadata_target="metadata.extraction_metadata.evidence_records",
                    ),
                )
            )

        findings.extend(
            _evidence_record_findings(
                expression_object=expression_object,
                object_ref=object_ref,
                evidence_records_by_id=evidence_records_by_id,
            )
        )
        findings.extend(
            _metadata_ref_findings(
                envelope=envelope,
                expression_object=expression_object,
                object_ref=object_ref,
            )
        )
        findings.extend(
            _context_preservation_findings(
                expression_object=expression_object,
                object_ref=object_ref,
            )
        )

    return tuple(findings)


__all__ = [
    "FORBIDDEN_LEGACY_COLLECTIONS",
    "FORBIDDEN_PAYLOAD_EVIDENCE_FIELDS",
    "GENE_EXPRESSION_LINKML_CONTRACT_VALIDATOR_ID",
    "GeneExpressionExtractionOutput",
    "REQUIRED_GENE_EXPRESSION_PAYLOAD_FIELDS",
    "VALID_GENE_EXPRESSION_RELATION_NAMES",
    "gene_expression_extraction_output_to_pending_envelope",
    "validate_gene_expression_extraction_objects",
    "validate_pending_gene_expression_envelope",
]
