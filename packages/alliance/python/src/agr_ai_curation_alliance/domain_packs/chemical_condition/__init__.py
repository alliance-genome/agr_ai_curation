"""Chemical-condition domain-pack helpers for pending envelope objects."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
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
    field_path_exists,
)

from ..paths import get_alliance_domain_packs_dir
from ..schema_refs import (
    ALLIANCE_LINKML_COMMIT,
    ALLIANCE_LINKML_PROVIDER_KEY,
    OBJECT_ROLE_METADATA_KEY,
    PROVIDER_REFS_METADATA_KEY,
)


CHEMICAL_CONDITION_DOMAIN_PACK_ID = "agr.alliance.chemical_condition"
CHEMICAL_CONDITION_DOMAIN_PACK_DIR_NAME = "chemical_condition"
CHEMICAL_CONDITION_DOMAIN_PACK_VERSION = "0.1.0"
CHEMICAL_CONDITION_MODEL_ID = "ChemicalConditionPayload"
CHEMICAL_CONDITION_OBJECT_TYPE = "ChemicalCondition"
CHEMICAL_TERM_OBJECT_TYPE = "ChemicalTerm"
REFERENCE_OBJECT_TYPE = "Reference"
EVIDENCE_QUOTE_OBJECT_TYPE = "EvidenceQuote"
CHEMICAL_CONDITION_VALIDATOR_STATES = ("active", "planned", "blocked")
CHEMICAL_CONDITION_PENDING_VALIDATOR_ID = (
    "chemical_condition.pending_envelope_validator"
)
CHEMICAL_CONDITION_CHEBI_FORMAT_VALIDATOR_ID = "chemical_condition.chebi_curie_format"
CHEMICAL_CONDITION_CONVERTER_ID = (
    "agr_ai_curation_alliance.domain_packs.chemical_condition"
)
CHEMICAL_CONDITION_EXPORT_CONTEXT_FIELDS = (
    "host_annotation_type",
    "host_annotation_id",
    "source_reference.reference_id",
)

_CHEBI_CURIE_PATTERN = re.compile(r"^CHEBI:\d+$")
_PHENOTYPE_DISEASE_SOURCE_FILE = "model/schema/phenotypeAndDiseaseAnnotation.yaml"
_ONTOLOGY_TERM_SOURCE_FILE = "model/schema/ontologyTerm.yaml"
_CONTROLLED_VOCABULARY_SOURCE_FILE = "model/schema/controlledVocabulary.yaml"
_REFERENCE_SOURCE_FILE = "model/schema/reference.yaml"
_CORE_SOURCE_FILE = "model/schema/core.yaml"


def get_chemical_condition_domain_pack_metadata_path() -> Path:
    """Return the bundled chemical-condition domain-pack metadata path."""

    return (
        get_alliance_domain_packs_dir()
        / CHEMICAL_CONDITION_DOMAIN_PACK_DIR_NAME
        / "domain_pack.yaml"
    )


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


def _schema_ref(
    *,
    schema_id: str,
    name: str,
    source_file: str,
    class_name: str,
    definition_state: DefinitionState = DefinitionState.STABLE,
    definition_notes: list[str] | None = None,
) -> SchemaRef:
    return SchemaRef(
        schema_id=schema_id,
        provider=ALLIANCE_LINKML_PROVIDER_KEY,
        name=name,
        version=ALLIANCE_LINKML_COMMIT,
        uri=(
            "https://github.com/alliance-genome/agr_curation_schema/blob/"
            f"{ALLIANCE_LINKML_COMMIT}/{source_file}"
        ),
        definition_state=definition_state,
        definition_notes=definition_notes or [],
        metadata={
            PROVIDER_REFS_METADATA_KEY: {
                ALLIANCE_LINKML_PROVIDER_KEY: {
                    "schema_ref": "alliance.linkml",
                    "commit": ALLIANCE_LINKML_COMMIT,
                    "source_file": source_file,
                    "class": class_name,
                }
            }
        },
    )


_CHEMICAL_CONDITION_SCHEMA_REF = _schema_ref(
    schema_id="alliance.linkml.ExperimentalCondition",
    name="ExperimentalCondition",
    source_file=_PHENOTYPE_DISEASE_SOURCE_FILE,
    class_name="ExperimentalCondition",
    definition_state=DefinitionState.IN_DEVELOPMENT,
    definition_notes=[
        "Pending chemical-condition envelopes block export until host annotation "
        "context and source reference materialization are provided."
    ],
)
_CHEMICAL_TERM_SCHEMA_REF = _schema_ref(
    schema_id="alliance.linkml.ChemicalTerm",
    name="ChemicalTerm",
    source_file=_ONTOLOGY_TERM_SOURCE_FILE,
    class_name="ChemicalTerm",
)
_REFERENCE_SCHEMA_REF = _schema_ref(
    schema_id="alliance.linkml.Reference",
    name="Reference",
    source_file=_REFERENCE_SOURCE_FILE,
    class_name="Reference",
)


class ToolVerifiedChemicalReference(BaseModel):
    """Source paper reference metadata carried with a tool-verified fixture."""

    model_config = ConfigDict(extra="forbid")

    title: StrictStr
    filename: StrictStr | None = None
    reference_id: int | None = Field(default=None, ge=1)

    @field_validator("title", mode="before")
    @classmethod
    def _validate_title(cls, value: object) -> object:
        return _strip_required_string(value, "title")

    @field_validator("filename", mode="before")
    @classmethod
    def _validate_filename(cls, value: object) -> object:
        return _strip_optional_string(value)


class ToolVerifiedChemicalEvidenceRecord(BaseModel):
    """One paper quote verified by the document evidence tool."""

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


class ToolVerifiedChemicalCondition(BaseModel):
    """One retained chemical condition from a tool-verified chemical extraction."""

    model_config = ConfigDict(extra="forbid")

    mention: StrictStr
    normalized_label: StrictStr
    normalized_id: StrictStr
    condition_class_curie: StrictStr
    condition_class_label: StrictStr
    role: Literal[
        "treatment",
        "assay_reagent",
        "buffer",
        "control",
        "other",
        "unspecified",
    ]
    confidence: Literal["high", "medium", "low"]
    evidence_record_ids: list[StrictStr] = Field(min_length=1)
    source_mentions: list[StrictStr] = Field(default_factory=list)
    condition_quantity: StrictStr | None = None
    condition_free_text: StrictStr | None = None
    condition_summary: StrictStr | None = None
    timing: StrictStr | None = None
    host_annotation_type: StrictStr | None = None
    host_annotation_id: StrictStr | None = None

    @field_validator(
        "mention",
        "normalized_label",
        "normalized_id",
        "condition_class_curie",
        "condition_class_label",
        mode="before",
    )
    @classmethod
    def _validate_required_strings(cls, value: object, info) -> object:
        return _strip_required_string(value, info.field_name)

    @field_validator(
        "condition_quantity",
        "condition_free_text",
        "condition_summary",
        "timing",
        "host_annotation_type",
        "host_annotation_id",
        mode="before",
    )
    @classmethod
    def _validate_optional_strings(cls, value: object) -> object:
        return _strip_optional_string(value)

    @field_validator("source_mentions", "evidence_record_ids")
    @classmethod
    def _validate_string_list(cls, value: list[StrictStr], info) -> list[str]:
        normalized_values: list[str] = []
        seen: set[str] = set()
        duplicates: list[str] = []
        for raw_item in value:
            item = str(raw_item).strip()
            if not item:
                raise ValueError(f"{info.field_name} must not contain empty values")
            if item in seen and item not in duplicates:
                duplicates.append(item)
            seen.add(item)
            normalized_values.append(item)
        if duplicates:
            raise ValueError(
                f"{info.field_name} contains duplicate entries: "
                + ", ".join(sorted(duplicates))
            )
        return normalized_values

    @model_validator(mode="after")
    def _default_source_mentions(self) -> "ToolVerifiedChemicalCondition":
        if not self.source_mentions:
            self.source_mentions = [self.mention]
        return self


class ToolVerifiedChemicalConditionOutput(BaseModel):
    """Canonical fixture input produced after chemical lookup and evidence verification."""

    model_config = ConfigDict(extra="forbid")

    envelope_id: StrictStr
    document_id: StrictStr
    produced_by: StrictStr
    produced_at: datetime
    reference: ToolVerifiedChemicalReference
    chemical_conditions: list[ToolVerifiedChemicalCondition] = Field(min_length=1)
    evidence_records: list[ToolVerifiedChemicalEvidenceRecord] = Field(min_length=1)
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
    def _validate_evidence_links(self) -> "ToolVerifiedChemicalConditionOutput":
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
                for condition in self.chemical_conditions
                for evidence_id in condition.evidence_record_ids
                if evidence_id not in evidence_id_set
            }
        )
        if missing_links:
            raise ValueError(
                "chemical_conditions references unknown evidence_record_ids: "
                + ", ".join(missing_links)
            )
        return self


def _drop_none(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if value is not None}


def _object_ref(pending_ref_id: str, object_type: str) -> ObjectRef:
    return ObjectRef(pending_ref_id=pending_ref_id, object_type=object_type)


def _ref_for_object(obj: CuratableObjectEnvelope) -> ObjectRef:
    if obj.pending_ref_id is not None:
        return ObjectRef(pending_ref_id=obj.pending_ref_id, object_type=obj.object_type)
    if obj.object_id is not None:
        return ObjectRef(object_id=obj.object_id, object_type=obj.object_type)
    raise ValueError("CuratableObjectEnvelope is missing object_id or pending_ref_id")


def _chemical_term_payload(condition: ToolVerifiedChemicalCondition) -> dict[str, Any]:
    return {
        "curie": condition.normalized_id,
        "name": condition.normalized_label,
        "source_mentions": condition.source_mentions,
    }


def _chemical_condition_payload(
    condition: ToolVerifiedChemicalCondition,
) -> dict[str, Any]:
    payload = {
        "condition_relation_type": {"name": "has_condition"},
        "condition_class": {
            "curie": condition.condition_class_curie,
            "name": condition.condition_class_label,
        },
        "condition_chemical": {
            "curie": condition.normalized_id,
            "name": condition.normalized_label,
        },
        "source_chemical_mention": condition.mention,
        "source_mentions": condition.source_mentions,
        "role": condition.role,
        "confidence": condition.confidence,
        "evidence_record_ids": condition.evidence_record_ids,
        "condition_quantity": condition.condition_quantity,
        "condition_free_text": condition.condition_free_text,
        "condition_summary": condition.condition_summary,
        "timing": condition.timing,
        "host_annotation_type": condition.host_annotation_type,
        "host_annotation_id": condition.host_annotation_id,
    }
    return _drop_none(payload)


def _evidence_quote_payload(
    evidence: ToolVerifiedChemicalEvidenceRecord,
) -> dict[str, Any]:
    return _drop_none(
        {
            "evidence_record_id": evidence.evidence_record_id,
            "entity": evidence.entity,
            "verified_quote": evidence.verified_quote,
            "page": evidence.page,
            "section": evidence.section,
            "subsection": evidence.subsection,
            "chunk_id": evidence.chunk_id,
            "figure_reference": evidence.figure_reference,
        }
    )


def _chemical_condition_metadata() -> dict[str, Any]:
    return {
        OBJECT_ROLE_METADATA_KEY: "curatable_unit",
        "condition_kind": "chemical_condition",
        "semantic_source": "domain_envelope.objects",
        "validator_binding_ids": [
            CHEMICAL_CONDITION_PENDING_VALIDATOR_ID,
            CHEMICAL_CONDITION_CHEBI_FORMAT_VALIDATOR_ID,
        ],
        "export_behavior": {
            "status": "blocked",
            "exportable": False,
            "submit": False,
            "reason": (
                "Chemical condition export requires a host annotation, "
                "materialized reference, and downstream submission adapter."
            ),
            "required_export_context_fields": list(
                CHEMICAL_CONDITION_EXPORT_CONTEXT_FIELDS
            ),
        },
        PROVIDER_REFS_METADATA_KEY: {
            ALLIANCE_LINKML_PROVIDER_KEY: {
                "schema_ref": "alliance.linkml",
                "commit": ALLIANCE_LINKML_COMMIT,
                "source_file": _PHENOTYPE_DISEASE_SOURCE_FILE,
                "class": "ExperimentalCondition",
            }
        },
    }


def _chemical_term_metadata() -> dict[str, Any]:
    return {
        OBJECT_ROLE_METADATA_KEY: "validated_reference",
        "validation_state": "pending_chebi_lookup",
        "validator_binding_id": CHEMICAL_CONDITION_CHEBI_FORMAT_VALIDATOR_ID,
        PROVIDER_REFS_METADATA_KEY: {
            ALLIANCE_LINKML_PROVIDER_KEY: {
                "schema_ref": "alliance.linkml",
                "commit": ALLIANCE_LINKML_COMMIT,
                "source_file": _ONTOLOGY_TERM_SOURCE_FILE,
                "class": "ChemicalTerm",
            }
        },
    }


def _reference_metadata() -> dict[str, Any]:
    return {
        OBJECT_ROLE_METADATA_KEY: "validated_reference",
        "validation_state": "pending_reference_materialization",
    }


def _evidence_quote_metadata() -> dict[str, Any]:
    return {OBJECT_ROLE_METADATA_KEY: "metadata_only"}


def build_pending_chemical_condition_envelope_from_tool_verified_output(
    payload: Mapping[str, Any] | ToolVerifiedChemicalConditionOutput,
    *,
    created_at: datetime | None = None,
) -> DomainEnvelope:
    """Convert tool-verified chemical extraction output into pending objects."""

    source = (
        payload
        if isinstance(payload, ToolVerifiedChemicalConditionOutput)
        else ToolVerifiedChemicalConditionOutput.model_validate(payload)
    )
    timestamp = created_at or source.produced_at

    reference_ref_id = "source-reference-1"
    objects: list[CuratableObjectEnvelope] = [
        CuratableObjectEnvelope(
            object_type=REFERENCE_OBJECT_TYPE,
            pending_ref_id=reference_ref_id,
            schema_ref=_REFERENCE_SCHEMA_REF,
            status=CuratableObjectStatus.PENDING,
            definition_state=DefinitionState.IN_DEVELOPMENT,
            payload=_drop_none(
                {
                    "title": source.reference.title,
                    "filename": source.reference.filename,
                    "reference_id": source.reference.reference_id,
                }
            ),
            metadata=_reference_metadata(),
        )
    ]

    evidence_by_id = {
        evidence.evidence_record_id: evidence for evidence in source.evidence_records
    }
    chemical_refs_by_curie: dict[str, str] = {}
    evidence_refs_by_id: dict[str, str] = {}
    retained_condition_count = 0

    for condition in source.chemical_conditions:
        retained_condition_count += 1
        chemical_ref_id = chemical_refs_by_curie.get(condition.normalized_id)
        if chemical_ref_id is None:
            chemical_ref_id = f"chemical-reference-{len(chemical_refs_by_curie) + 1}"
            chemical_refs_by_curie[condition.normalized_id] = chemical_ref_id
            objects.append(
                CuratableObjectEnvelope(
                    object_type=CHEMICAL_TERM_OBJECT_TYPE,
                    pending_ref_id=chemical_ref_id,
                    schema_ref=_CHEMICAL_TERM_SCHEMA_REF,
                    status=CuratableObjectStatus.PENDING,
                    definition_state=DefinitionState.IN_DEVELOPMENT,
                    payload=_chemical_term_payload(condition),
                    metadata=_chemical_term_metadata(),
                )
            )

        condition_evidence_refs: list[ObjectRef] = []
        for evidence_record_id in condition.evidence_record_ids:
            evidence_ref_id = evidence_refs_by_id.get(evidence_record_id)
            if evidence_ref_id is None:
                evidence = evidence_by_id[evidence_record_id]
                evidence_ref_id = f"evidence-quote-{len(evidence_refs_by_id) + 1}"
                evidence_refs_by_id[evidence_record_id] = evidence_ref_id
                objects.append(
                    CuratableObjectEnvelope(
                        object_type=EVIDENCE_QUOTE_OBJECT_TYPE,
                        pending_ref_id=evidence_ref_id,
                        status=CuratableObjectStatus.PENDING,
                        definition_state=DefinitionState.IN_DEVELOPMENT,
                        payload=_evidence_quote_payload(evidence),
                        metadata=_evidence_quote_metadata(),
                    )
                )
            condition_evidence_refs.append(
                _object_ref(evidence_ref_id, EVIDENCE_QUOTE_OBJECT_TYPE)
            )

        condition_ref_id = f"chemical-condition-{retained_condition_count}"
        objects.append(
            CuratableObjectEnvelope(
                object_type=CHEMICAL_CONDITION_OBJECT_TYPE,
                pending_ref_id=condition_ref_id,
                schema_ref=_CHEMICAL_CONDITION_SCHEMA_REF,
                status=CuratableObjectStatus.PENDING,
                definition_state=DefinitionState.IN_DEVELOPMENT,
                definition_notes=[
                    "Pending only; export is blocked until host annotation "
                    "context and reference materialization are supplied."
                ],
                payload=_chemical_condition_payload(condition),
                object_refs=[
                    _object_ref(chemical_ref_id, CHEMICAL_TERM_OBJECT_TYPE),
                    _object_ref(reference_ref_id, REFERENCE_OBJECT_TYPE),
                    *condition_evidence_refs,
                ],
                metadata=_chemical_condition_metadata(),
            )
        )

    envelope = DomainEnvelope(
        envelope_id=source.envelope_id,
        domain_pack_id=CHEMICAL_CONDITION_DOMAIN_PACK_ID,
        domain_pack_version=CHEMICAL_CONDITION_DOMAIN_PACK_VERSION,
        status=DomainEnvelopeStatus.EXTRACTED,
        schema_ref=SchemaRef(
            schema_id="agr.alliance.chemical_condition.domain_pack",
            provider="domain-pack",
            name="Alliance Chemical Condition Domain Pack",
            version=CHEMICAL_CONDITION_DOMAIN_PACK_VERSION,
            definition_state=DefinitionState.IN_DEVELOPMENT,
        ),
        objects=objects,
        history=[
            HistoryEvent(
                event_type=HistoryEventKind.CREATED,
                timestamp=timestamp,
                actor_type=HistoryActorType.SYSTEM,
                actor_id=CHEMICAL_CONDITION_CONVERTER_ID,
                message=(
                    "Converted tool-verified chemical extraction output to a "
                    "pending chemical-condition domain envelope."
                ),
                details={
                    "retained_condition_count": retained_condition_count,
                    "source_tool": source.produced_by,
                },
            )
        ],
        metadata={
            "document_id": source.document_id,
            "source_tool": source.produced_by,
            "normalization_notes": source.normalization_notes,
            "semantic_source": "domain_envelope.objects",
            "legacy_semantic_lists": [],
            "export_behavior": {"status": "blocked"},
        },
    )
    return envelope.model_copy(
        update={
            "validation_findings": list(
                validate_pending_chemical_condition_envelope(envelope)
            )
        }
    )


def validate_pending_chemical_condition_envelope(
    envelope: DomainEnvelope,
) -> tuple[ValidationFinding, ...]:
    """Return domain-pack validation findings for one chemical-condition envelope."""

    findings: list[ValidationFinding] = []
    if envelope.domain_pack_id != CHEMICAL_CONDITION_DOMAIN_PACK_ID:
        findings.append(
            ValidationFinding(
                severity=ValidationFindingSeverity.ERROR,
                code="alliance.chemical_condition.domain_pack_mismatch",
                message=(
                    f"Expected domain_pack_id {CHEMICAL_CONDITION_DOMAIN_PACK_ID}, "
                    f"found {envelope.domain_pack_id}."
                ),
            )
        )

    conditions = [
        obj
        for obj in envelope.objects
        if obj.object_type == CHEMICAL_CONDITION_OBJECT_TYPE
    ]
    chemical_terms = [
        obj for obj in envelope.objects if obj.object_type == CHEMICAL_TERM_OBJECT_TYPE
    ]
    evidence_quotes = [
        obj for obj in envelope.objects if obj.object_type == EVIDENCE_QUOTE_OBJECT_TYPE
    ]
    if not conditions:
        findings.append(
            ValidationFinding(
                severity=ValidationFindingSeverity.ERROR,
                code="alliance.chemical_condition.missing_condition",
                message="Envelope must contain at least one ChemicalCondition object.",
            )
        )

    objects_by_ref = {
        ref_key: obj for obj in envelope.objects for ref_key in obj.ref_keys()
    }
    for condition in conditions:
        findings.extend(_validate_condition_object(condition, objects_by_ref))
    for chemical_term in chemical_terms:
        findings.extend(_validate_chemical_term_object(chemical_term))
    for evidence_quote in evidence_quotes:
        findings.extend(_validate_evidence_quote_object(evidence_quote))

    return tuple(findings)


def _validate_condition_object(
    condition: CuratableObjectEnvelope,
    objects_by_ref: Mapping[tuple[str, str], CuratableObjectEnvelope],
) -> list[ValidationFinding]:
    findings: list[ValidationFinding] = []
    condition_ref = _ref_for_object(condition)

    required_payload_fields = {
        "condition_relation_type.name",
        "condition_class.curie",
        "condition_class.name",
        "condition_chemical.curie",
        "condition_chemical.name",
        "source_chemical_mention",
        "evidence_record_ids[0]",
        "confidence",
    }
    missing_payload_fields = sorted(
        field_path
        for field_path in required_payload_fields
        if not field_path_exists(condition.payload, field_path)
    )
    if missing_payload_fields:
        findings.append(
            ValidationFinding(
                severity=ValidationFindingSeverity.ERROR,
                code="alliance.chemical_condition.required_payload_missing",
                message=(
                    "ChemicalCondition is missing required payload fields: "
                    + ", ".join(missing_payload_fields)
                ),
                object_ref=condition_ref,
                details={"missing_payload_fields": missing_payload_fields},
            )
        )

    chemical_curie = _payload_string(condition.payload, "condition_chemical.curie")
    if chemical_curie is not None and _CHEBI_CURIE_PATTERN.match(chemical_curie) is None:
        findings.append(
            ValidationFinding(
                severity=ValidationFindingSeverity.ERROR,
                code="alliance.chemical_condition.invalid_chebi_curie",
                message=(
                    "ChemicalCondition.condition_chemical.curie must be a CHEBI CURIE "
                    "for this chemical-condition pack."
                ),
                field_ref=FieldRef(
                    object_ref=condition_ref,
                    field_path="condition_chemical.curie",
                ),
                details={
                    "validator_binding_id": CHEMICAL_CONDITION_CHEBI_FORMAT_VALIDATOR_ID,
                    "observed_value": chemical_curie,
                },
            )
        )

    ref_types = {ref.object_type for ref in condition.object_refs}
    missing_ref_types = {
        CHEMICAL_TERM_OBJECT_TYPE,
        REFERENCE_OBJECT_TYPE,
        EVIDENCE_QUOTE_OBJECT_TYPE,
    } - ref_types
    if missing_ref_types:
        findings.append(
            ValidationFinding(
                severity=ValidationFindingSeverity.ERROR,
                code="alliance.chemical_condition.object_refs_missing",
                message=(
                    "ChemicalCondition is missing object refs: "
                    + ", ".join(sorted(missing_ref_types))
                ),
                object_ref=condition_ref,
                details={"missing_object_ref_types": sorted(missing_ref_types)},
            )
        )

    missing_export_context = _missing_export_context(condition, objects_by_ref)
    if missing_export_context:
        findings.append(
            ValidationFinding(
                severity=ValidationFindingSeverity.BLOCKER,
                code="alliance.chemical_condition.export_context_missing",
                message=(
                    "Chemical condition export is blocked until host annotation "
                    "context and source reference materialization are supplied."
                ),
                object_ref=condition_ref,
                details={
                    "missing_export_context_fields": missing_export_context,
                    "write_behavior": "blocked",
                },
            )
        )

    export_behavior = condition.metadata.get("export_behavior")
    if (
        not isinstance(export_behavior, Mapping)
        or export_behavior.get("status") != "blocked"
        or export_behavior.get("exportable") is not False
    ):
        findings.append(
            ValidationFinding(
                severity=ValidationFindingSeverity.BLOCKER,
                code="alliance.chemical_condition.export_behavior_not_blocked",
                message="ChemicalCondition export behavior must remain blocked in this pack.",
                object_ref=condition_ref,
            )
        )

    return findings


def _validate_chemical_term_object(
    chemical_term: CuratableObjectEnvelope,
) -> list[ValidationFinding]:
    findings: list[ValidationFinding] = []
    chemical_term_ref = _ref_for_object(chemical_term)

    missing_payload_fields = sorted(
        field_path
        for field_path in ("curie", "name")
        if not field_path_exists(chemical_term.payload, field_path)
    )
    if missing_payload_fields:
        findings.append(
            ValidationFinding(
                severity=ValidationFindingSeverity.ERROR,
                code="alliance.chemical_condition.chemical_term_required_payload_missing",
                message=(
                    "ChemicalTerm is missing required payload fields: "
                    + ", ".join(missing_payload_fields)
                ),
                object_ref=chemical_term_ref,
                details={"missing_payload_fields": missing_payload_fields},
            )
        )

    chemical_curie = _payload_string(chemical_term.payload, "curie")
    if chemical_curie is not None and _CHEBI_CURIE_PATTERN.match(chemical_curie) is None:
        findings.append(
            ValidationFinding(
                severity=ValidationFindingSeverity.ERROR,
                code="alliance.chemical_condition.invalid_chebi_curie",
                message=(
                    "ChemicalTerm.curie must be a CHEBI CURIE for this "
                    "chemical-condition pack."
                ),
                field_ref=FieldRef(
                    object_ref=chemical_term_ref,
                    field_path="curie",
                ),
                details={
                    "validator_binding_id": CHEMICAL_CONDITION_CHEBI_FORMAT_VALIDATOR_ID,
                    "observed_value": chemical_curie,
                },
            )
        )

    return findings


def _validate_evidence_quote_object(
    evidence_quote: CuratableObjectEnvelope,
) -> list[ValidationFinding]:
    findings: list[ValidationFinding] = []
    evidence_quote_ref = _ref_for_object(evidence_quote)

    missing_payload_fields = sorted(
        field_path
        for field_path in ("verified_quote",)
        if not field_path_exists(evidence_quote.payload, field_path)
    )
    if missing_payload_fields:
        findings.append(
            ValidationFinding(
                severity=ValidationFindingSeverity.ERROR,
                code=(
                    "alliance.chemical_condition."
                    "evidence_quote_required_payload_missing"
                ),
                message=(
                    "EvidenceQuote is missing required payload fields: "
                    + ", ".join(missing_payload_fields)
                ),
                object_ref=evidence_quote_ref,
                details={"missing_payload_fields": missing_payload_fields},
            )
        )

    return findings


def _missing_export_context(
    condition: CuratableObjectEnvelope,
    objects_by_ref: Mapping[tuple[str, str], CuratableObjectEnvelope],
) -> list[str]:
    missing: list[str] = []
    for field_path in ("host_annotation_type", "host_annotation_id"):
        if not field_path_exists(condition.payload, field_path):
            missing.append(field_path)

    reference_objects = [
        objects_by_ref.get(ref.ref_key())
        for ref in condition.object_refs
        if ref.object_type == REFERENCE_OBJECT_TYPE
    ]
    if not any(
        reference is not None and field_path_exists(reference.payload, "reference_id")
        for reference in reference_objects
    ):
        missing.append("source_reference.reference_id")
    return missing


def _payload_string(payload: Mapping[str, Any], field_path: str) -> str | None:
    current: Any = payload
    for part in field_path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return None
        current = current[part]
    return current if isinstance(current, str) else None


__all__ = [
    "CHEMICAL_CONDITION_CHEBI_FORMAT_VALIDATOR_ID",
    "CHEMICAL_CONDITION_CONVERTER_ID",
    "CHEMICAL_CONDITION_DOMAIN_PACK_DIR_NAME",
    "CHEMICAL_CONDITION_DOMAIN_PACK_ID",
    "CHEMICAL_CONDITION_DOMAIN_PACK_VERSION",
    "CHEMICAL_CONDITION_EXPORT_CONTEXT_FIELDS",
    "CHEMICAL_CONDITION_MODEL_ID",
    "CHEMICAL_CONDITION_OBJECT_TYPE",
    "CHEMICAL_CONDITION_PENDING_VALIDATOR_ID",
    "CHEMICAL_CONDITION_VALIDATOR_STATES",
    "CHEMICAL_TERM_OBJECT_TYPE",
    "EVIDENCE_QUOTE_OBJECT_TYPE",
    "REFERENCE_OBJECT_TYPE",
    "ToolVerifiedChemicalCondition",
    "ToolVerifiedChemicalConditionOutput",
    "ToolVerifiedChemicalEvidenceRecord",
    "ToolVerifiedChemicalReference",
    "build_pending_chemical_condition_envelope_from_tool_verified_output",
    "get_chemical_condition_domain_pack_metadata_path",
    "validate_pending_chemical_condition_envelope",
]
