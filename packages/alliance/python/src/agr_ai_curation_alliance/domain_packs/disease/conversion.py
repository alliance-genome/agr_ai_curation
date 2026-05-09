"""Convert tool-verified disease assertion fixtures into domain envelopes."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from datetime import datetime, timezone
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
    parse_field_path,
)
from src.schemas.models.domain_envelope_extraction import DomainEnvelopeExtractionResult

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
    DISEASE_MODEL_ID,
    DISEASE_OBJECT_TYPE,
    DISEASE_PENDING_ENVELOPE_VALIDATOR_BINDING_ID,
    FORBIDDEN_LEGACY_COLLECTIONS,
    REQUIRED_DISEASE_PAYLOAD_FIELDS,
)


_CORE_SOURCE_FILE = "model/schema/core.yaml"
_DISEASE_OBJECT_ROLE = "curatable_unit"
_DISEASE_ASSERTION_KIND = "pending_disease_assertion"
_FORBIDDEN_DISEASE_HELPER_PAYLOAD_FIELDS = frozenset(
    {
        "normalized_id",
        "normalized_label",
        "disease_curie",
        "disease_name",
    }
)
_DISEASE_ASSERTION_ROLES = frozenset(
    {"primary", "background", "comparative", "model_context", "unspecified"}
)
_DISEASE_ASSERTION_CONFIDENCES = frozenset({"high", "medium", "low"})
_DISEASE_PAYLOAD_ENUM_VALUES = {
    "role": _DISEASE_ASSERTION_ROLES,
    "confidence": _DISEASE_ASSERTION_CONFIDENCES,
}
_REQUIRED_DISEASE_EVIDENCE_SNAPSHOT_FIELDS = (
    "evidence_record_id",
    "verified_quote",
    "page",
    "section",
    "chunk_id",
)
_MISSING_PAYLOAD_VALUE = object()


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


def _object_metadata(source_metadata: Mapping[str, Any] | None = None) -> dict[str, Any]:
    metadata = dict(source_metadata or {})
    metadata.setdefault(OBJECT_ROLE_METADATA_KEY, _DISEASE_OBJECT_ROLE)
    metadata.setdefault("assertion_kind", _DISEASE_ASSERTION_KIND)
    metadata.setdefault(
        "validator_binding_id",
        DISEASE_PENDING_ENVELOPE_VALIDATOR_BINDING_ID,
    )
    metadata.setdefault(
        "write_behavior",
        {
            "status": "blocked",
            "reason": (
                "Subject, reference, evidence-code, and concrete disease annotation "
                "write targets are not yet materialized from disease extractor output."
            ),
        },
    )
    metadata.setdefault(
        "export_behavior",
        {
            "status": "blocked",
            "reason": "Disease export/submission adapters are tracked by ALL-425.",
        },
    )
    metadata.setdefault(
        "provider_refs",
        {
            ALLIANCE_LINKML_PROVIDER_KEY: {
                "schema_ref": "alliance.linkml",
                "commit": ALLIANCE_LINKML_COMMIT,
                "source_file": DISEASE_LINKML_SCHEMA_SOURCE_FILE,
                "class": "DiseaseAnnotation",
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
    raise ValueError("DiseaseAnnotation object is missing an object ref")


def _metadata_evidence_records_by_id(
    output: DomainEnvelopeExtractionResult,
) -> dict[str, Any]:
    return {
        evidence.evidence_record_id: evidence
        for evidence in output.metadata.evidence_records
        if evidence.evidence_record_id
    }


def _payload_value(
    payload: Mapping[str, Any],
    field_path: str,
) -> Any:
    current: Any = payload
    for part in parse_field_path(field_path):
        if isinstance(part, str):
            if not isinstance(current, Mapping) or part not in current:
                return _MISSING_PAYLOAD_VALUE
            current = current[part]
            continue

        if (
            not isinstance(current, Sequence)
            or isinstance(current, (str, bytes, bytearray))
            or part >= len(current)
        ):
            return _MISSING_PAYLOAD_VALUE
        current = current[part]
    return current


def _required_payload_value_present(
    payload: Mapping[str, Any],
    field_path: str,
) -> bool:
    value = _payload_value(payload, field_path)
    if value is _MISSING_PAYLOAD_VALUE or value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return bool(value)
    return True


def _missing_or_empty_payload_fields(payload: Mapping[str, Any]) -> list[str]:
    return [
        field_path
        for field_path in REQUIRED_DISEASE_PAYLOAD_FIELDS
        if not _required_payload_value_present(payload, field_path)
    ]


def _payload_enum_errors(*, location: str, payload: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    for field_path, allowed_values in _DISEASE_PAYLOAD_ENUM_VALUES.items():
        value = _payload_value(payload, field_path)
        if value is _MISSING_PAYLOAD_VALUE or value is None:
            continue
        normalized_value = value.strip() if isinstance(value, str) else value
        if not normalized_value:
            continue
        if not isinstance(normalized_value, str) or normalized_value not in allowed_values:
            errors.append(
                f"{location}.payload.{field_path} must be one of "
                + ", ".join(sorted(allowed_values))
            )
    return errors


def _missing_required_mapping_fields(
    record: Mapping[str, Any],
    field_names: Sequence[str],
) -> list[str]:
    missing_fields: list[str] = []
    for field_name in field_names:
        value = record.get(field_name, _MISSING_PAYLOAD_VALUE)
        if value is _MISSING_PAYLOAD_VALUE or value is None:
            missing_fields.append(field_name)
            continue
        if isinstance(value, str) and not value.strip():
            missing_fields.append(field_name)
            continue
        if field_name == "page" and (
            isinstance(value, bool) or not isinstance(value, int) or value < 1
        ):
            missing_fields.append(field_name)
    return missing_fields


def _required_mapping_field_errors(
    *,
    location: str,
    record: Mapping[str, Any],
    field_names: Sequence[str],
) -> list[str]:
    missing_fields = _missing_required_mapping_fields(record, field_names)
    if not missing_fields:
        return []
    return [
        f"{location} must include non-empty "
        + ", ".join(missing_fields)
    ]


def _required_evidence_field_errors(evidence_id: str, evidence: Any) -> list[str]:
    missing_fields: list[str] = []
    for field_name in _REQUIRED_DISEASE_EVIDENCE_SNAPSHOT_FIELDS:
        value = getattr(evidence, field_name, None)
        if value is None or (isinstance(value, str) and not value.strip()):
            missing_fields.append(field_name)
    if not missing_fields:
        return []
    return [
        "metadata.evidence_records[] entry "
        f"{evidence_id} must include "
        + ", ".join(missing_fields)
    ]


def _validate_payload_evidence_snapshot(
    *,
    location: str,
    obj: CuratableObjectEnvelope,
    evidence_by_id: Mapping[str, Any],
) -> list[str]:
    errors: list[str] = []
    payload_evidence_ids = obj.payload.get("evidence_record_ids")
    if payload_evidence_ids != list(obj.evidence_record_ids):
        errors.append(
            f"{location}.payload.evidence_record_ids must match "
            f"{location}.evidence_record_ids"
        )

    payload_records = obj.payload.get("evidence_records")
    if not isinstance(payload_records, list) or not payload_records:
        errors.append(
            f"{location}.payload.evidence_records must snapshot verified "
            "metadata.evidence_records[] entries"
        )
        return errors

    payload_records_by_id = {
        record.get("evidence_record_id"): record
        for record in payload_records
        if isinstance(record, Mapping) and record.get("evidence_record_id")
    }
    missing_payload_records = sorted(
        evidence_id
        for evidence_id in obj.evidence_record_ids
        if evidence_id not in payload_records_by_id
    )
    if missing_payload_records:
        errors.append(
            f"{location}.payload.evidence_records is missing snapshots for "
            + ", ".join(missing_payload_records)
        )

    for payload_record_index, payload_record in enumerate(payload_records):
        payload_record_location = (
            f"{location}.payload.evidence_records[{payload_record_index}]"
        )
        if not isinstance(payload_record, Mapping):
            errors.append(f"{payload_record_location} must be an object")
            continue
        errors.extend(
            _required_mapping_field_errors(
                location=payload_record_location,
                record=payload_record,
                field_names=_REQUIRED_DISEASE_EVIDENCE_SNAPSHOT_FIELDS,
            )
        )

    for evidence_id in obj.evidence_record_ids:
        metadata_record = evidence_by_id.get(evidence_id)
        if metadata_record is None:
            continue
        errors.extend(_required_evidence_field_errors(evidence_id, metadata_record))

        payload_record = payload_records_by_id.get(evidence_id)
        if not isinstance(payload_record, Mapping):
            continue
        for field_name in (
            "entity",
            "verified_quote",
            "page",
            "section",
            "subsection",
            "chunk_id",
            "figure_reference",
        ):
            metadata_value = getattr(metadata_record, field_name, None)
            payload_value = payload_record.get(field_name)
            if payload_value is not None and metadata_value != payload_value:
                errors.append(
                    f"{location}.payload.evidence_records[{evidence_id}]."
                    f"{field_name} must match metadata.evidence_records[]"
                )
    return errors


def validate_disease_extraction_objects(
    output: DomainEnvelopeExtractionResult,
) -> tuple[str, ...]:
    """Return validation error messages for disease extractor output."""

    errors: list[str] = []
    evidence_by_id = _metadata_evidence_records_by_id(output)
    metadata_payload = output.metadata.model_dump(mode="python")
    repair_field_ref_count = 0

    if output.curatable_objects and not output.metadata.raw_mentions:
        errors.append(
            "disease extractor output must preserve harvested mentions in "
            "metadata.raw_mentions[]"
        )

    for index, obj in enumerate(output.curatable_objects):
        location = f"curatable_objects[{index}]"
        if obj.object_type != DISEASE_OBJECT_TYPE:
            errors.append(f"{location}.object_type must be {DISEASE_OBJECT_TYPE}")
        if obj.object_role != _DISEASE_OBJECT_ROLE:
            errors.append(f"{location}.object_role must be {_DISEASE_OBJECT_ROLE}")
        if obj.model_ref != DISEASE_MODEL_ID:
            errors.append(f"{location}.model_ref must be {DISEASE_MODEL_ID}")
        if obj.definition_state != DefinitionState.IN_DEVELOPMENT:
            errors.append(f"{location}.definition_state must be in_development")
        if obj.schema_ref is None:
            errors.append(f"{location}.schema_ref is required")
        elif (
            obj.schema_ref.schema_id != DISEASE_LINKML_SCHEMA_ID
            or obj.schema_ref.provider != ALLIANCE_LINKML_PROVIDER_KEY
            or obj.schema_ref.name != DISEASE_LINKML_SCHEMA_NAME
            or obj.schema_ref.version != ALLIANCE_LINKML_COMMIT
            or obj.schema_ref.uri != DISEASE_LINKML_SCHEMA_URI
            or obj.schema_ref.definition_state != DefinitionState.IN_DEVELOPMENT
        ):
            errors.append(
                f"{location}.schema_ref must identify pinned "
                f"{DISEASE_LINKML_SCHEMA_ID} at {ALLIANCE_LINKML_COMMIT}"
            )

        forbidden_payload_fields = sorted(
            _FORBIDDEN_DISEASE_HELPER_PAYLOAD_FIELDS.intersection(obj.payload)
        )
        if forbidden_payload_fields:
            errors.append(
                f"{location}.payload uses legacy flat disease helper fields "
                f"{', '.join(forbidden_payload_fields)}; use "
                "disease_annotation_object.curie/name instead"
            )

        missing_payload_fields = _missing_or_empty_payload_fields(obj.payload)
        if missing_payload_fields:
            errors.append(
                f"{location}.payload is missing required non-empty fields: "
                + ", ".join(missing_payload_fields)
            )
        errors.extend(_payload_enum_errors(location=location, payload=obj.payload))

        if not obj.evidence_record_ids:
            errors.append(f"{location}.evidence_record_ids must not be empty")
        missing_evidence_ids = sorted(
            evidence_id
            for evidence_id in obj.evidence_record_ids
            if evidence_id not in evidence_by_id
        )
        if missing_evidence_ids:
            errors.append(
                f"{location}.evidence_record_ids references unknown "
                "metadata.evidence_records IDs: "
                + ", ".join(missing_evidence_ids)
            )
        errors.extend(
            _validate_payload_evidence_snapshot(
                location=location,
                obj=obj,
                evidence_by_id=evidence_by_id,
            )
        )

        write_behavior = obj.metadata.get("write_behavior")
        if (
            not isinstance(write_behavior, Mapping)
            or write_behavior.get("status") != "blocked"
        ):
            errors.append(
                f"{location}.metadata.write_behavior.status must be blocked "
                "while disease subject/reference/evidence-code materialization is incomplete"
            )
        if obj.metadata.get("assertion_kind") != _DISEASE_ASSERTION_KIND:
            errors.append(
                f"{location}.metadata.assertion_kind must be {_DISEASE_ASSERTION_KIND}"
            )

        if not obj.metadata_refs:
            errors.append(f"{location}.metadata_refs must preserve source metadata paths")
        missing_metadata_refs = [
            metadata_ref.metadata_path
            for metadata_ref in obj.metadata_refs
            if not field_path_exists(metadata_payload, metadata_ref.metadata_path)
        ]
        if missing_metadata_refs:
            errors.append(
                f"{location}.metadata_refs must resolve in metadata: "
                + ", ".join(missing_metadata_refs)
            )

        if output.repair_mode:
            repair_field_ref_count += len(obj.field_refs)
            object_ref_keys = set(obj.ref_keys())
            for field_ref_index, field_ref in enumerate(obj.field_refs):
                if field_ref.object_ref.ref_key() not in object_ref_keys:
                    errors.append(
                        f"{location}.field_refs[{field_ref_index}].object_ref "
                        "must point at the repaired object"
                    )
                if not field_path_exists(obj.payload, field_ref.field_path):
                    errors.append(
                        f"{location}.field_refs[{field_ref_index}].field_path "
                        f"'{field_ref.field_path}' does not exist on repaired object "
                        "payload"
                    )

    if output.repair_mode and repair_field_ref_count == 0:
        errors.append(
            "curatable_objects[].field_refs must identify repaired field paths "
            "when repair_mode is true"
        )
    if output.repair_mode and not output.metadata.repair_notes:
        errors.append("metadata.repair_notes must describe repair-mode changes")
    return tuple(errors)


class DiseaseExtractionOutput(DomainEnvelopeExtractionResult):
    """Validated extractor output for one disease domain-envelope run."""

    @model_validator(mode="after")
    def _validate_disease_objects(self) -> "DiseaseExtractionOutput":
        errors = validate_disease_extraction_objects(self)
        if errors:
            raise ValueError("; ".join(errors))
        return self


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
        object_type=DISEASE_OBJECT_TYPE,
        object_role=_DISEASE_OBJECT_ROLE,
        payload=dict(obj.payload),
        object_id=obj.object_id,
        pending_ref_id=obj.pending_ref_id,
        schema_ref=obj.schema_ref,
        model_ref=obj.model_ref,
        status=CuratableObjectStatus.PENDING,
        definition_state=DefinitionState.IN_DEVELOPMENT,
        definition_notes=list(obj.definition_notes or []),
        object_refs=list(obj.object_refs),
        field_refs=list(obj.field_refs),
        evidence_record_ids=list(obj.evidence_record_ids),
        metadata_refs=metadata_refs,
        repair_hints=list(obj.repair_hints),
        metadata=_object_metadata(obj.metadata),
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


def disease_extraction_output_to_pending_envelope(
    payload: Mapping[str, Any] | DiseaseExtractionOutput,
    *,
    envelope_id: str,
    document_id: str | None = None,
    produced_by: str = "disease_extractor",
    produced_at: datetime | None = None,
) -> DomainEnvelope:
    """Build a pending disease envelope from domain-envelope extractor output."""

    source = (
        payload
        if isinstance(payload, DiseaseExtractionOutput)
        else DiseaseExtractionOutput.model_validate(payload)
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
            actor_id=DISEASE_DOMAIN_PACK_CONVERTER_ID,
            message="Converted disease extraction output to a pending domain envelope.",
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
                code="alliance.disease_assertion.domain_envelope_extracted",
                message="DiseaseAnnotation was converted from domain-envelope extractor output.",
                object_ref=object_ref,
                details={
                    "semantic_source": "curatable_objects[]",
                    "validator_binding_id": DISEASE_PENDING_ENVELOPE_VALIDATOR_BINDING_ID,
                },
            )
        )
        history.append(
            HistoryEvent(
                event_type=HistoryEventKind.OBJECT_EXTRACTED,
                timestamp=timestamp,
                actor_type=HistoryActorType.SYSTEM,
                actor_id=DISEASE_DOMAIN_PACK_CONVERTER_ID,
                message="Added pending disease assertion.",
                object_ref=object_ref,
                details={
                    "evidence_record_ids": list(obj.evidence_record_ids),
                    "write_behavior": "blocked",
                },
            )
        )

    metadata: dict[str, Any] = {
        "source_agent": produced_by,
        "conversion": "disease_extraction_output_to_pending_envelope",
        "semantic_source": "domain_envelope.objects",
        "legacy_semantic_lists": [],
        "extraction_summary": source.summary,
        "extraction_metadata": source.metadata.model_dump(mode="python"),
        "run_summary": source.run_summary.model_dump(mode="python"),
        "repair_mode": source.repair_mode,
        "write_behavior": {"status": "blocked"},
    }
    if document_id is not None:
        metadata["source_document_id"] = document_id

    return DomainEnvelope(
        envelope_id=envelope_id,
        domain_pack_id=DISEASE_DOMAIN_PACK_ID,
        domain_pack_version=DISEASE_DOMAIN_PACK_VERSION,
        status=DomainEnvelopeStatus.EXTRACTED,
        schema_ref=_disease_schema_ref(),
        objects=objects,
        validation_findings=validation_findings,
        history=history,
        metadata=metadata,
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
        missing_fields = _missing_or_empty_payload_fields(disease_object.payload)
        if missing_fields:
            findings.append(
                ValidationFinding(
                    severity=ValidationFindingSeverity.ERROR,
                    code="alliance.disease.required_payload_fields_missing",
                    message=(
                        "DiseaseAnnotation pending assertion is missing required "
                        "non-empty payload fields: "
                        + ", ".join(missing_fields)
                    ),
                    object_ref=object_ref,
                    details={"missing_fields": missing_fields},
                )
            )
        for field_path, allowed_values in _DISEASE_PAYLOAD_ENUM_VALUES.items():
            value = _payload_value(disease_object.payload, field_path)
            if value is _MISSING_PAYLOAD_VALUE or value is None:
                continue
            normalized_value = value.strip() if isinstance(value, str) else value
            if not normalized_value:
                continue
            if (
                not isinstance(normalized_value, str)
                or normalized_value not in allowed_values
            ):
                findings.append(
                    ValidationFinding(
                        severity=ValidationFindingSeverity.ERROR,
                        code="alliance.disease.payload_enum_value_invalid",
                        message=(
                            f"DiseaseAnnotation pending assertion field {field_path} "
                            "must be one of "
                            + ", ".join(sorted(allowed_values))
                            + "."
                        ),
                        object_ref=object_ref,
                        field_ref=(
                            FieldRef(
                                object_ref=object_ref,
                                field_path=field_path,
                            )
                            if object_ref is not None
                            else None
                        ),
                        details={
                            "field_path": field_path,
                            "observed_value": value,
                            "allowed_values": sorted(allowed_values),
                        },
                    )
                )

        evidence_record_ids = disease_object.payload.get("evidence_record_ids")
        evidence_records = disease_object.payload.get("evidence_records")
        verified_evidence_ids: list[str] = []
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
        else:
            verified_evidence_ids = [item.strip() for item in evidence_record_ids]
        if not isinstance(evidence_records, list) or not evidence_records:
            findings.append(
                ValidationFinding(
                    severity=ValidationFindingSeverity.ERROR,
                    code="alliance.disease.evidence_records_invalid",
                    message="DiseaseAnnotation pending assertion requires verified evidence records.",
                    object_ref=object_ref,
                )
            )
        else:
            invalid_records: list[dict[str, Any]] = []
            records_by_id: dict[str, Mapping[str, Any]] = {}
            for record_index, record in enumerate(evidence_records):
                if not isinstance(record, Mapping):
                    invalid_records.append(
                        {
                            "record_index": record_index,
                            "reason": "record must be an object",
                        }
                    )
                    continue
                missing_evidence_fields = _missing_required_mapping_fields(
                    record,
                    _REQUIRED_DISEASE_EVIDENCE_SNAPSHOT_FIELDS,
                )
                raw_record_id = record.get("evidence_record_id")
                record_id = raw_record_id.strip() if isinstance(raw_record_id, str) else None
                if record_id:
                    records_by_id[record_id] = record
                if missing_evidence_fields:
                    invalid_records.append(
                        {
                            "record_index": record_index,
                            "evidence_record_id": record_id,
                            "missing_or_invalid_fields": missing_evidence_fields,
                        }
                    )

            missing_snapshots = sorted(
                evidence_id
                for evidence_id in verified_evidence_ids
                if evidence_id not in records_by_id
            )
            if invalid_records or missing_snapshots:
                findings.append(
                    ValidationFinding(
                        severity=ValidationFindingSeverity.ERROR,
                        code="alliance.disease.evidence_records_incomplete",
                        message=(
                            "DiseaseAnnotation pending assertion evidence_records "
                            "must include complete snapshots for every verified "
                            "evidence_record_id."
                        ),
                        object_ref=object_ref,
                        details={
                            "invalid_records": invalid_records,
                            "missing_snapshots": missing_snapshots,
                        },
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
    "DiseaseExtractionOutput",
    "ToolVerifiedDiseaseAssertion",
    "ToolVerifiedDiseaseCondition",
    "ToolVerifiedDiseaseEvidenceRecord",
    "ToolVerifiedDiseaseOutput",
    "ToolVerifiedDiseaseSubject",
    "disease_extraction_output_to_pending_envelope",
    "tool_verified_disease_output_to_pending_envelope",
    "validate_disease_extraction_objects",
    "validate_pending_disease_envelope",
]
