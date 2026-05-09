"""Convert tool-verified disease assertion fixtures into domain envelopes."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator, model_validator

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
)

from ..schema_refs import (
    ALLIANCE_LINKML_COMMIT,
    ALLIANCE_LINKML_PROVIDER_KEY,
    OBJECT_ROLE_METADATA_KEY,
    PROVIDER_REFS_METADATA_KEY,
)
from .constants import (
    DISEASE_DEFINITION_NOTES,
    DISEASE_DOMAIN_PACK_CONVERTER_ID,
    DISEASE_DOMAIN_PACK_ID,
    DISEASE_DOMAIN_PACK_VERSION,
    DISEASE_LINKML_SCHEMA_ID,
    DISEASE_LINKML_SCHEMA_NAME,
    DISEASE_LINKML_SCHEMA_SOURCE_FILE,
    DISEASE_LINKML_SCHEMA_URI,
    DISEASE_OBJECT_TYPE,
    DISEASE_PENDING_ENVELOPE_VALIDATOR_BINDING_ID,
    FORBIDDEN_LEGACY_COLLECTIONS,
    REQUIRED_DISEASE_PAYLOAD_FIELDS,
)


_CORE_SOURCE_FILE = "model/schema/core.yaml"


def _strip_required_string(value: object, field_name: str) -> object:
    if not isinstance(value, str):
        return value
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be empty")
    return normalized


def _strip_optional_string(value: object) -> object:
    if value is None or not isinstance(value, str):
        return value
    normalized = value.strip()
    return normalized or None


class ToolVerifiedDiseaseEvidenceRecord(BaseModel):
    """One disease-supporting quote verified by the document evidence tool."""

    model_config = ConfigDict(extra="forbid")

    evidence_record_id: StrictStr
    entity: StrictStr | None = None
    verified_quote: StrictStr
    page: int = Field(ge=1)
    section: StrictStr
    chunk_id: StrictStr
    subsection: StrictStr | None = None
    figure_reference: StrictStr | None = None

    @field_validator(
        "evidence_record_id",
        "verified_quote",
        "section",
        "chunk_id",
        mode="before",
    )
    @classmethod
    def _validate_required_strings(cls, value: object, info) -> object:
        return _strip_required_string(value, info.field_name)

    @field_validator("entity", "subsection", "figure_reference", mode="before")
    @classmethod
    def _validate_optional_strings(cls, value: object) -> object:
        return _strip_optional_string(value)


class ToolVerifiedDiseaseSubject(BaseModel):
    """Extractor-observed subject context for a pending disease assertion."""

    model_config = ConfigDict(extra="forbid")

    subject_type: Literal["gene", "allele", "agm", "unknown"]
    subject_label: StrictStr | None = None
    subject_identifier: StrictStr | None = None

    @field_validator("subject_label", "subject_identifier", mode="before")
    @classmethod
    def _validate_optional_strings(cls, value: object) -> object:
        return _strip_optional_string(value)


class ToolVerifiedDiseaseCondition(BaseModel):
    """Optional experimental-condition context retained for later materialization."""

    model_config = ConfigDict(extra="forbid")

    condition_relation_type_name: StrictStr | None = None
    condition_class_curie: StrictStr | None = None
    condition_class_name: StrictStr | None = None
    condition_id_curie: StrictStr | None = None
    condition_id_name: StrictStr | None = None
    condition_chemical_curie: StrictStr | None = None
    condition_chemical_name: StrictStr | None = None
    condition_taxon_curie: StrictStr | None = None
    condition_taxon_name: StrictStr | None = None
    condition_free_text: StrictStr | None = None
    condition_quantity: StrictStr | None = None
    condition_summary: StrictStr | None = None

    @field_validator("*", mode="before")
    @classmethod
    def _validate_optional_strings(cls, value: object) -> object:
        return _strip_optional_string(value)

    @model_validator(mode="after")
    def _validate_has_context(self) -> "ToolVerifiedDiseaseCondition":
        if not any(self.model_dump(exclude_none=True).values()):
            raise ValueError("condition entries must include at least one populated field")
        return self


class ToolVerifiedDiseaseAssertion(BaseModel):
    """One normalized disease assertion retained by the extractor."""

    model_config = ConfigDict(extra="forbid")

    mention: StrictStr
    disease_curie: StrictStr
    disease_name: StrictStr
    role: Literal["primary", "background", "comparative", "model_context", "unspecified"]
    confidence: Literal["high", "medium", "low"]
    evidence_record_ids: list[StrictStr] = Field(min_length=1)
    disease_relation_name: StrictStr | None = None
    subject: ToolVerifiedDiseaseSubject | None = None
    conditions: list[ToolVerifiedDiseaseCondition] = Field(default_factory=list)
    evidence_code_curies: list[StrictStr] = Field(default_factory=list)

    @field_validator("mention", "disease_curie", "disease_name", mode="before")
    @classmethod
    def _validate_required_strings(cls, value: object, info) -> object:
        return _strip_required_string(value, info.field_name)

    @field_validator("disease_relation_name", mode="before")
    @classmethod
    def _validate_optional_strings(cls, value: object) -> object:
        return _strip_optional_string(value)

    @field_validator("evidence_record_ids", "evidence_code_curies")
    @classmethod
    def _validate_string_lists(cls, value: list[StrictStr], info) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        duplicates: list[str] = []
        for raw_item in value:
            item = str(raw_item).strip()
            if not item:
                raise ValueError(f"{info.field_name} must not contain empty values")
            if item in seen and item not in duplicates:
                duplicates.append(item)
            seen.add(item)
            normalized.append(item)
        if duplicates:
            raise ValueError(
                f"{info.field_name} contains duplicate entries: "
                + ", ".join(sorted(duplicates))
            )
        return normalized


class ToolVerifiedDiseaseOutput(BaseModel):
    """Canonical fixture input produced after disease lookup and evidence verification."""

    model_config = ConfigDict(extra="forbid")

    envelope_id: StrictStr
    document_id: StrictStr
    produced_by: StrictStr
    produced_at: datetime
    disease_assertions: list[ToolVerifiedDiseaseAssertion] = Field(min_length=1)
    evidence_records: list[ToolVerifiedDiseaseEvidenceRecord] = Field(min_length=1)
    normalization_notes: list[StrictStr] = Field(default_factory=list)

    @field_validator("envelope_id", "document_id", "produced_by", mode="before")
    @classmethod
    def _validate_required_strings(cls, value: object, info) -> object:
        return _strip_required_string(value, info.field_name)

    @field_validator("normalization_notes")
    @classmethod
    def _validate_normalization_notes(cls, value: list[StrictStr]) -> list[str]:
        normalized_notes: list[str] = []
        for item in value:
            normalized = str(item).strip()
            if not normalized:
                raise ValueError("normalization_notes must not contain empty values")
            normalized_notes.append(normalized)
        return normalized_notes

    @model_validator(mode="after")
    def _validate_evidence_links(self) -> "ToolVerifiedDiseaseOutput":
        evidence_ids = [item.evidence_record_id for item in self.evidence_records]
        duplicate_ids = sorted(
            {
                evidence_id
                for evidence_id in evidence_ids
                if evidence_ids.count(evidence_id) > 1
            }
        )
        if duplicate_ids:
            raise ValueError(
                "evidence_records contains duplicate evidence_record_id entries: "
                + ", ".join(duplicate_ids)
            )

        evidence_id_set = set(evidence_ids)
        missing_links = sorted(
            {
                evidence_id
                for assertion in self.disease_assertions
                for evidence_id in assertion.evidence_record_ids
                if evidence_id not in evidence_id_set
            }
        )
        if missing_links:
            raise ValueError(
                "disease_assertions references unknown evidence_record_ids: "
                + ", ".join(missing_links)
            )
        return self


def _disease_schema_ref() -> SchemaRef:
    return SchemaRef(
        schema_id=DISEASE_LINKML_SCHEMA_ID,
        provider=ALLIANCE_LINKML_PROVIDER_KEY,
        name=DISEASE_LINKML_SCHEMA_NAME,
        version=ALLIANCE_LINKML_COMMIT,
        uri=DISEASE_LINKML_SCHEMA_URI,
        definition_state=DefinitionState.IN_DEVELOPMENT,
        definition_notes=list(DISEASE_DEFINITION_NOTES),
        metadata={
            PROVIDER_REFS_METADATA_KEY: {
                ALLIANCE_LINKML_PROVIDER_KEY: {
                    "schema_ref": "alliance.linkml",
                    "commit": ALLIANCE_LINKML_COMMIT,
                    "source_file": DISEASE_LINKML_SCHEMA_SOURCE_FILE,
                    "class": "DiseaseAnnotation",
                }
            }
        },
    )


def _object_metadata() -> dict[str, Any]:
    return {
        OBJECT_ROLE_METADATA_KEY: "curatable_unit",
        "assertion_kind": "pending_disease_assertion",
        "validator_binding_id": DISEASE_PENDING_ENVELOPE_VALIDATOR_BINDING_ID,
        "write_behavior": {
            "status": "blocked",
            "reason": (
                "Subject, reference, evidence-code, and concrete disease annotation "
                "write targets are not yet materialized from disease extractor output."
            ),
        },
        "export_behavior": {
            "status": "blocked",
            "reason": "Disease export/submission adapters are tracked by ALL-425.",
        },
        "provider_refs": {
            ALLIANCE_LINKML_PROVIDER_KEY: {
                "schema_ref": "alliance.linkml",
                "commit": ALLIANCE_LINKML_COMMIT,
                "source_file": DISEASE_LINKML_SCHEMA_SOURCE_FILE,
                "class": "DiseaseAnnotation",
            }
        },
    }


def _evidence_payload(evidence: ToolVerifiedDiseaseEvidenceRecord) -> dict[str, Any]:
    payload = evidence.model_dump(mode="json", exclude_none=True)
    payload["source_tool"] = "record_evidence"
    return payload


def _subject_payload(subject: ToolVerifiedDiseaseSubject) -> dict[str, Any]:
    return subject.model_dump(mode="json", exclude_none=True)


def _condition_payload(condition: ToolVerifiedDiseaseCondition) -> dict[str, Any]:
    source = condition.model_dump(mode="json", exclude_none=True)
    relation: dict[str, Any] = {}
    if "condition_relation_type_name" in source:
        relation["condition_relation_type"] = {
            "name": source.pop("condition_relation_type_name")
        }

    experimental_condition: dict[str, Any] = {}
    term_specs = (
        ("condition_class", "condition_class_curie", "condition_class_name"),
        ("condition_id", "condition_id_curie", "condition_id_name"),
        ("condition_chemical", "condition_chemical_curie", "condition_chemical_name"),
        ("condition_taxon", "condition_taxon_curie", "condition_taxon_name"),
    )
    for target_key, curie_key, name_key in term_specs:
        term_payload: dict[str, Any] = {}
        if curie_key in source:
            term_payload["curie"] = source.pop(curie_key)
        if name_key in source:
            term_payload["name"] = source.pop(name_key)
        if term_payload:
            experimental_condition[target_key] = term_payload

    for key in (
        "condition_free_text",
        "condition_quantity",
        "condition_summary",
    ):
        if key in source:
            experimental_condition[key] = source.pop(key)

    if experimental_condition:
        relation["conditions"] = [experimental_condition]
    return relation


def _payload_for_assertion(
    assertion: ToolVerifiedDiseaseAssertion,
    evidence_by_id: Mapping[str, ToolVerifiedDiseaseEvidenceRecord],
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "mention": assertion.mention,
        "disease_annotation_object": {
            "curie": assertion.disease_curie,
            "name": assertion.disease_name,
        },
        "role": assertion.role,
        "confidence": assertion.confidence,
        "evidence_record_ids": list(assertion.evidence_record_ids),
        "evidence_records": [
            _evidence_payload(evidence_by_id[evidence_id])
            for evidence_id in assertion.evidence_record_ids
        ],
    }

    if assertion.disease_relation_name is not None:
        payload["disease_relation_name"] = assertion.disease_relation_name
    if assertion.subject is not None:
        payload["disease_annotation_subject"] = _subject_payload(assertion.subject)
    if assertion.conditions:
        payload["condition_relations"] = [
            _condition_payload(condition)
            for condition in assertion.conditions
        ]
    if assertion.evidence_code_curies:
        payload["evidence_code_curies"] = list(assertion.evidence_code_curies)
    return payload


def _validation_finding(pending_ref_id: str) -> ValidationFinding:
    return ValidationFinding(
        severity=ValidationFindingSeverity.INFO,
        status=ValidationFindingStatus.RESOLVED,
        code="alliance.disease_assertion.tool_verified",
        message="Disease assertion evidence was verified before envelope conversion.",
        field_ref=FieldRef(
            object_ref=ObjectRef(
                pending_ref_id=pending_ref_id,
                object_type=DISEASE_OBJECT_TYPE,
            ),
            field_path="disease_annotation_object.curie",
        ),
        details={
            "validator_binding_id": DISEASE_PENDING_ENVELOPE_VALIDATOR_BINDING_ID,
            "source_tool": "record_evidence",
            "blocking": False,
            "grounded_slots": {
                "disease_annotation_object": {
                    "source_file": DISEASE_LINKML_SCHEMA_SOURCE_FILE,
                    "attribute": "disease_annotation_object",
                    "range": "DOTerm",
                },
                "curie": {
                    "source_file": _CORE_SOURCE_FILE,
                    "slot": "curie",
                    "range": "uriorcurie",
                },
                "name": {
                    "source_file": _CORE_SOURCE_FILE,
                    "slot": "name",
                    "range": "string",
                },
            },
        },
    )


def _iter_mapping_keys(value: Any) -> Iterator[str]:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if isinstance(key, str):
                yield key
            yield from _iter_mapping_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_mapping_keys(child)


def _legacy_keys_in_envelope(envelope: DomainEnvelope) -> set[str]:
    return FORBIDDEN_LEGACY_COLLECTIONS.intersection(
        _iter_mapping_keys(envelope.model_dump(mode="python"))
    )


def tool_verified_disease_output_to_pending_envelope(
    payload: Mapping[str, Any] | ToolVerifiedDiseaseOutput,
) -> DomainEnvelope:
    """Build a pending disease envelope from canonical tool-verified disease output."""

    source = (
        payload
        if isinstance(payload, ToolVerifiedDiseaseOutput)
        else ToolVerifiedDiseaseOutput.model_validate(payload)
    )
    evidence_by_id = {
        evidence.evidence_record_id: evidence
        for evidence in source.evidence_records
    }

    objects: list[CuratableObjectEnvelope] = []
    validation_findings: list[ValidationFinding] = []
    history: list[HistoryEvent] = [
        HistoryEvent(
            event_type=HistoryEventKind.CREATED,
            timestamp=source.produced_at,
            actor_type=HistoryActorType.SYSTEM,
            actor_id=DISEASE_DOMAIN_PACK_CONVERTER_ID,
            message="Converted tool-verified disease extraction output to a pending domain envelope.",
        )
    ]

    for object_index, assertion in enumerate(source.disease_assertions, start=1):
        pending_ref_id = f"disease-assertion-{object_index}"
        object_ref = ObjectRef(
            pending_ref_id=pending_ref_id,
            object_type=DISEASE_OBJECT_TYPE,
        )
        objects.append(
            CuratableObjectEnvelope(
                object_type=DISEASE_OBJECT_TYPE,
                pending_ref_id=pending_ref_id,
                schema_ref=_disease_schema_ref(),
                status=CuratableObjectStatus.PENDING,
                definition_state=DefinitionState.IN_DEVELOPMENT,
                definition_notes=list(DISEASE_DEFINITION_NOTES),
                payload=_payload_for_assertion(assertion, evidence_by_id),
                metadata=_object_metadata(),
            )
        )
        validation_findings.append(_validation_finding(pending_ref_id))
        history.append(
            HistoryEvent(
                event_type=HistoryEventKind.OBJECT_EXTRACTED,
                timestamp=source.produced_at,
                actor_type=HistoryActorType.SYSTEM,
                actor_id=DISEASE_DOMAIN_PACK_CONVERTER_ID,
                message="Added pending disease assertion.",
                object_ref=object_ref,
                details={
                    "evidence_record_ids": list(assertion.evidence_record_ids),
                    "validator_binding_id": DISEASE_PENDING_ENVELOPE_VALIDATOR_BINDING_ID,
                    "write_behavior": "blocked",
                },
            )
        )

    return DomainEnvelope(
        envelope_id=source.envelope_id,
        domain_pack_id=DISEASE_DOMAIN_PACK_ID,
        domain_pack_version=DISEASE_DOMAIN_PACK_VERSION,
        status=DomainEnvelopeStatus.EXTRACTED,
        schema_ref=_disease_schema_ref(),
        objects=objects,
        validation_findings=validation_findings,
        history=history,
        metadata={
            "source_document_id": source.document_id,
            "source_agent": source.produced_by,
            "conversion": "tool_verified_disease_output_to_pending_envelope",
            "semantic_source": "domain_envelope.objects",
            "normalization_notes": source.normalization_notes,
            "write_behavior": {"status": "blocked"},
        },
    )


def validate_pending_disease_envelope(
    envelope: DomainEnvelope,
) -> tuple[ValidationFinding, ...]:
    """Return domain-pack validation findings for one pending disease envelope."""

    findings: list[ValidationFinding] = []
    if envelope.domain_pack_id != DISEASE_DOMAIN_PACK_ID:
        findings.append(
            ValidationFinding(
                severity=ValidationFindingSeverity.ERROR,
                code="alliance.disease.domain_pack_mismatch",
                message=(
                    f"Expected domain_pack_id {DISEASE_DOMAIN_PACK_ID}, "
                    f"found {envelope.domain_pack_id}."
                ),
            )
        )

    legacy_keys = _legacy_keys_in_envelope(envelope)
    if legacy_keys:
        findings.append(
            ValidationFinding(
                severity=ValidationFindingSeverity.ERROR,
                code="alliance.disease.legacy_semantic_store_present",
                message=(
                    "Disease domain envelopes must use envelope objects as the semantic "
                    "source of truth; legacy semantic collections are not allowed."
                ),
                details={"legacy_keys": sorted(legacy_keys)},
            )
        )

    disease_objects = [
        obj for obj in envelope.objects if obj.object_type == DISEASE_OBJECT_TYPE
    ]
    if not disease_objects:
        findings.append(
            ValidationFinding(
                severity=ValidationFindingSeverity.ERROR,
                code="alliance.disease.missing_assertion",
                message="Envelope must contain at least one DiseaseAnnotation pending assertion.",
            )
        )

    for disease_object in disease_objects:
        object_ref = (
            ObjectRef(
                pending_ref_id=disease_object.pending_ref_id,
                object_type=disease_object.object_type,
            )
            if disease_object.pending_ref_id
            else None
        )
        missing_fields = [
            field_path
            for field_path in REQUIRED_DISEASE_PAYLOAD_FIELDS
            if not field_path_exists(disease_object.payload, field_path)
        ]
        if missing_fields:
            findings.append(
                ValidationFinding(
                    severity=ValidationFindingSeverity.ERROR,
                    code="alliance.disease.required_payload_fields_missing",
                    message=(
                        "DiseaseAnnotation pending assertion is missing required "
                        "payload fields: "
                        + ", ".join(missing_fields)
                    ),
                    object_ref=object_ref,
                    details={"missing_fields": missing_fields},
                )
            )

        evidence_record_ids = disease_object.payload.get("evidence_record_ids")
        evidence_records = disease_object.payload.get("evidence_records")
        if (
            not isinstance(evidence_record_ids, list)
            or not evidence_record_ids
            or not all(isinstance(item, str) and item.strip() for item in evidence_record_ids)
        ):
            findings.append(
                ValidationFinding(
                    severity=ValidationFindingSeverity.ERROR,
                    code="alliance.disease.evidence_record_ids_invalid",
                    message="DiseaseAnnotation pending assertion requires verified evidence_record_ids.",
                    object_ref=object_ref,
                )
            )
        if not isinstance(evidence_records, list) or not evidence_records:
            findings.append(
                ValidationFinding(
                    severity=ValidationFindingSeverity.ERROR,
                    code="alliance.disease.evidence_records_invalid",
                    message="DiseaseAnnotation pending assertion requires verified evidence records.",
                    object_ref=object_ref,
                )
            )

        write_behavior = disease_object.metadata.get("write_behavior")
        if (
            not isinstance(write_behavior, Mapping)
            or write_behavior.get("status") != "blocked"
        ):
            findings.append(
                ValidationFinding(
                    severity=ValidationFindingSeverity.BLOCKER,
                    code="alliance.disease.write_behavior_not_blocked",
                    message="DiseaseAnnotation writes must remain blocked in this pack.",
                    object_ref=object_ref,
                )
            )

    return tuple(findings)


__all__ = [
    "ToolVerifiedDiseaseAssertion",
    "ToolVerifiedDiseaseCondition",
    "ToolVerifiedDiseaseEvidenceRecord",
    "ToolVerifiedDiseaseOutput",
    "ToolVerifiedDiseaseSubject",
    "tool_verified_disease_output_to_pending_envelope",
    "validate_pending_disease_envelope",
]
