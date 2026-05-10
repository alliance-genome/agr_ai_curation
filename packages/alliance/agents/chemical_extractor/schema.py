"""Chemical extractor schema for Alliance chemical-condition envelopes."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    RootModel,
    StrictStr,
    field_validator,
    model_validator,
)

from src.lib.domain_packs.repair_patches import (
    DomainEnvelopeExtractorFinalClassification,
    DomainEnvelopeRepairPatch,
)
from src.lib.openai_agents.models import (
    ChemicalExtractionResultEnvelope as RuntimeChemicalExtractionResultEnvelope,
)
from src.schemas.domain_envelope import CuratableObjectEnvelope, DefinitionState, SchemaRef


# Keep these values synchronized with the Alliance chemical-condition domain pack.
# Agent schema discovery loads this file directly from the agent bundle, so this
# module cannot assume the package python/src tree is importable in every runtime.
CHEMICAL_CONDITION_OBJECT_TYPE = "ChemicalCondition"
CHEMICAL_TERM_OBJECT_TYPE = "ChemicalTerm"
REFERENCE_OBJECT_TYPE = "Reference"
EVIDENCE_QUOTE_OBJECT_TYPE = "EvidenceQuote"

CHEMICAL_CONDITION_MODEL_REF = "ChemicalConditionPayload"
CHEMICAL_TERM_MODEL_REF = "ChemicalTermPayload"
REFERENCE_MODEL_REF = "ReferencePayload"
EVIDENCE_QUOTE_MODEL_REF = "EvidenceQuotePayload"

ALLIANCE_LINKML_SCHEMA_PROVIDER = "alliance_linkml"
ALLIANCE_LINKML_COMMIT = "1b11d0888f19eba4ca72022200bb7d96b30d4a52"
CHEMICAL_CONDITION_SCHEMA_ID = "alliance.linkml.ExperimentalCondition"
CHEMICAL_CONDITION_SCHEMA_NAME = "ExperimentalCondition"
CHEMICAL_CONDITION_SCHEMA_URI = (
    "https://github.com/alliance-genome/agr_curation_schema/blob/"
    f"{ALLIANCE_LINKML_COMMIT}/model/schema/phenotypeAndDiseaseAnnotation.yaml"
)
CHEMICAL_TERM_SCHEMA_ID = "alliance.linkml.ChemicalTerm"
CHEMICAL_TERM_SCHEMA_NAME = "ChemicalTerm"
CHEMICAL_TERM_SCHEMA_URI = (
    "https://github.com/alliance-genome/agr_curation_schema/blob/"
    f"{ALLIANCE_LINKML_COMMIT}/model/schema/ontologyTerm.yaml"
)
REFERENCE_SCHEMA_ID = "alliance.linkml.Reference"
REFERENCE_SCHEMA_NAME = "Reference"
REFERENCE_SCHEMA_URI = (
    "https://github.com/alliance-genome/agr_curation_schema/blob/"
    f"{ALLIANCE_LINKML_COMMIT}/model/schema/reference.yaml"
)

_CHEBI_CURIE_PATTERN = re.compile(r"^CHEBI:\d+$")
_REQUIRED_CONDITION_REF_TYPES = {
    CHEMICAL_TERM_OBJECT_TYPE,
    REFERENCE_OBJECT_TYPE,
    EVIDENCE_QUOTE_OBJECT_TYPE,
}


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


def _validate_string_list(value: list[StrictStr], field_name: str) -> list[str]:
    normalized_values: list[str] = []
    seen: set[str] = set()
    duplicates: list[str] = []
    for raw_item in value:
        item = str(raw_item).strip()
        if not item:
            raise ValueError(f"{field_name} must not contain empty values")
        if item in seen and item not in duplicates:
            duplicates.append(item)
        seen.add(item)
        normalized_values.append(item)
    if duplicates:
        raise ValueError(
            f"{field_name} contains duplicate entries: "
            + ", ".join(sorted(duplicates))
        )
    return normalized_values


class ChemicalExtractorPayloadModel(BaseModel):
    """Strict base model for chemical extractor object payloads."""

    model_config = ConfigDict(extra="forbid")


class VocabularyTermSnapshotPayload(ChemicalExtractorPayloadModel):
    """Embedded condition-relation vocabulary term snapshot."""

    name: StrictStr

    @field_validator("name", mode="before")
    @classmethod
    def _validate_name(cls, value: object) -> object:
        return _strip_required_string(value, "name")


class OntologyTermSnapshotPayload(ChemicalExtractorPayloadModel):
    """Embedded ontology term snapshot for condition classes."""

    curie: StrictStr
    name: StrictStr

    @field_validator("curie", "name", mode="before")
    @classmethod
    def _validate_required_strings(cls, value: object, info) -> object:
        return _strip_required_string(value, info.field_name)


class ChemicalTermPayload(ChemicalExtractorPayloadModel):
    """Payload for one validated chemical ontology term reference."""

    curie: StrictStr
    name: StrictStr
    source_mentions: list[StrictStr] = Field(default_factory=list)

    @field_validator("curie", "name", mode="before")
    @classmethod
    def _validate_required_strings(cls, value: object, info) -> object:
        return _strip_required_string(value, info.field_name)

    @field_validator("source_mentions")
    @classmethod
    def _validate_source_mentions(cls, value: list[StrictStr]) -> list[str]:
        return _validate_string_list(value, "source_mentions")


class ReferencePayload(ChemicalExtractorPayloadModel):
    """Payload for the pending source-paper reference."""

    title: StrictStr | None = None
    filename: StrictStr | None = None
    reference_id: int | None = Field(default=None, ge=1)

    @field_validator("title", "filename", mode="before")
    @classmethod
    def _validate_optional_strings(cls, value: object) -> object:
        return _strip_optional_string(value)

    @model_validator(mode="after")
    def _validate_reference_identity(self) -> "ReferencePayload":
        if self.reference_id is None and self.title is None and self.filename is None:
            raise ValueError(
                "Reference payload must include title, filename, or reference_id"
            )
        return self


class EvidenceQuotePayload(ChemicalExtractorPayloadModel):
    """Payload preserving one record_evidence-verified quote."""

    evidence_record_id: StrictStr
    verified_quote: StrictStr
    page: int = Field(ge=1)
    section: StrictStr
    chunk_id: StrictStr
    entity: StrictStr | None = None
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

    @field_validator("page", mode="before")
    @classmethod
    def _validate_page(cls, value: object) -> object:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("page must be an integer")
        return value


class ChemicalConditionPayload(ChemicalExtractorPayloadModel):
    """Payload for one pending chemical experimental condition."""

    condition_relation_type: VocabularyTermSnapshotPayload
    condition_class: OntologyTermSnapshotPayload
    condition_chemical: ChemicalTermPayload
    source_chemical_mention: StrictStr
    confidence: Literal["high", "medium", "low"]
    evidence_record_ids: list[StrictStr] = Field(min_length=1)
    source_mentions: list[StrictStr] = Field(min_length=1)
    role: Literal[
        "treatment",
        "assay_reagent",
        "buffer",
        "control",
        "other",
        "unspecified",
    ]
    condition_quantity: StrictStr | None = None
    condition_free_text: StrictStr | None = None
    condition_summary: StrictStr | None = None
    timing: StrictStr | None = None
    host_annotation_type: StrictStr | None = None
    host_annotation_id: StrictStr | None = None

    @field_validator("source_chemical_mention", mode="before")
    @classmethod
    def _validate_source_chemical_mention(cls, value: object) -> object:
        return _strip_required_string(value, "source_chemical_mention")

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

    @field_validator("evidence_record_ids", "source_mentions")
    @classmethod
    def _validate_string_lists(cls, value: list[StrictStr], info) -> list[str]:
        return _validate_string_list(value, info.field_name)

    @model_validator(mode="after")
    def _validate_condition_semantics(self) -> "ChemicalConditionPayload":
        if self.condition_relation_type.name != "has_condition":
            raise ValueError(
                "ChemicalCondition payload.condition_relation_type.name must be "
                "'has_condition'"
            )
        return self


class ChemicalConditionCuratableObject(CuratableObjectEnvelope):
    """One ChemicalCondition curatable unit in chemical extractor output."""

    object_type: Literal["ChemicalCondition"] = CHEMICAL_CONDITION_OBJECT_TYPE
    object_role: Literal["curatable_unit"] = "curatable_unit"
    payload: ChemicalConditionPayload
    schema_ref: SchemaRef = Field(
        description="Alliance LinkML ExperimentalCondition schema ref"
    )
    model_ref: Literal["ChemicalConditionPayload"] = CHEMICAL_CONDITION_MODEL_REF
    definition_state: Literal[DefinitionState.IN_DEVELOPMENT] = (
        DefinitionState.IN_DEVELOPMENT
    )
    definition_notes: list[StrictStr] = Field(
        min_length=1,
        description="Notes explaining pending export/submission blockers",
    )

    @model_validator(mode="after")
    def _validate_condition_schema_ref(self) -> "ChemicalConditionCuratableObject":
        _validate_schema_ref(
            self.schema_ref,
            expected_schema_id=CHEMICAL_CONDITION_SCHEMA_ID,
            expected_name=CHEMICAL_CONDITION_SCHEMA_NAME,
            expected_uri=CHEMICAL_CONDITION_SCHEMA_URI,
            expected_definition_state=DefinitionState.IN_DEVELOPMENT,
            object_label=CHEMICAL_CONDITION_OBJECT_TYPE,
        )
        return self


class ChemicalTermCuratableObject(CuratableObjectEnvelope):
    """One ChemicalTerm validated-reference object."""

    object_type: Literal["ChemicalTerm"] = CHEMICAL_TERM_OBJECT_TYPE
    object_role: Literal["validated_reference"] = "validated_reference"
    payload: ChemicalTermPayload
    schema_ref: SchemaRef = Field(description="Alliance LinkML ChemicalTerm schema ref")
    model_ref: Literal["ChemicalTermPayload"] = CHEMICAL_TERM_MODEL_REF
    definition_state: Literal[DefinitionState.IN_DEVELOPMENT] = (
        DefinitionState.IN_DEVELOPMENT
    )

    @model_validator(mode="after")
    def _validate_chemical_term_schema_ref(self) -> "ChemicalTermCuratableObject":
        _validate_schema_ref(
            self.schema_ref,
            expected_schema_id=CHEMICAL_TERM_SCHEMA_ID,
            expected_name=CHEMICAL_TERM_SCHEMA_NAME,
            expected_uri=CHEMICAL_TERM_SCHEMA_URI,
            expected_definition_state=DefinitionState.STABLE,
            object_label=CHEMICAL_TERM_OBJECT_TYPE,
        )
        return self


class ReferenceCuratableObject(CuratableObjectEnvelope):
    """One source-paper Reference validated-reference object."""

    object_type: Literal["Reference"] = REFERENCE_OBJECT_TYPE
    object_role: Literal["validated_reference"] = "validated_reference"
    payload: ReferencePayload
    schema_ref: SchemaRef = Field(description="Alliance LinkML Reference schema ref")
    model_ref: Literal["ReferencePayload"] = REFERENCE_MODEL_REF
    definition_state: Literal[DefinitionState.IN_DEVELOPMENT] = (
        DefinitionState.IN_DEVELOPMENT
    )

    @model_validator(mode="after")
    def _validate_reference_schema_ref(self) -> "ReferenceCuratableObject":
        _validate_schema_ref(
            self.schema_ref,
            expected_schema_id=REFERENCE_SCHEMA_ID,
            expected_name=REFERENCE_SCHEMA_NAME,
            expected_uri=REFERENCE_SCHEMA_URI,
            expected_definition_state=DefinitionState.STABLE,
            object_label=REFERENCE_OBJECT_TYPE,
        )
        return self


class EvidenceQuoteCuratableObject(CuratableObjectEnvelope):
    """One metadata-only EvidenceQuote object."""

    object_type: Literal["EvidenceQuote"] = EVIDENCE_QUOTE_OBJECT_TYPE
    object_role: Literal["metadata_only"] = "metadata_only"
    payload: EvidenceQuotePayload
    model_ref: Literal["EvidenceQuotePayload"] = EVIDENCE_QUOTE_MODEL_REF
    definition_state: Literal[DefinitionState.IN_DEVELOPMENT] = (
        DefinitionState.IN_DEVELOPMENT
    )


ChemicalExtractorCuratableObject = Annotated[
    ChemicalConditionCuratableObject
    | ChemicalTermCuratableObject
    | ReferenceCuratableObject
    | EvidenceQuoteCuratableObject,
    Field(discriminator="object_type"),
]


class ChemicalExtractionResultEnvelope(RuntimeChemicalExtractionResultEnvelope):
    """Config-discovered schema for chemical-condition extractor output."""

    __envelope_class__ = True

    curatable_objects: list[ChemicalExtractorCuratableObject] = Field(
        default_factory=list,
        description=(
            "The only semantic object list for new chemical extractor runs. "
            "Objects must be ChemicalCondition, ChemicalTerm, Reference, or "
            "EvidenceQuote domain-pack envelopes."
        ),
    )

    @model_validator(mode="after")
    def _validate_chemical_condition_output(self) -> "ChemicalExtractionResultEnvelope":
        if self.curatable_objects and not self.metadata.raw_mentions:
            raise ValueError(
                "chemical extractor output must preserve harvested mentions in "
                "metadata.raw_mentions[]"
            )

        evidence_by_id = {
            record.evidence_record_id: record
            for record in self.metadata.evidence_records
            if record.evidence_record_id is not None
        }
        objects_by_ref = {
            ref_key: obj
            for obj in self.curatable_objects
            for ref_key in obj.ref_keys()
        }

        for obj in self.curatable_objects:
            if isinstance(obj, ChemicalTermCuratableObject):
                _validate_chebi_curie(obj.payload.curie, "ChemicalTerm.payload.curie")
            elif isinstance(obj, EvidenceQuoteCuratableObject):
                self._validate_evidence_quote_object(obj, evidence_by_id)
            elif isinstance(obj, ChemicalConditionCuratableObject):
                self._validate_condition_object(obj, evidence_by_id, objects_by_ref)

        if self.repair_mode:
            has_repair_context = bool(self.metadata.repair_notes) or any(
                obj.repair_hints for obj in self.curatable_objects
            )
            if not has_repair_context:
                raise ValueError(
                    "repair-mode chemical extractor output must include "
                    "metadata.repair_notes[] or curatable_objects[].repair_hints[]"
                )

        return self

    @classmethod
    def _validate_evidence_quote_object(
        cls,
        obj: EvidenceQuoteCuratableObject,
        evidence_by_id: Mapping[str, Any],
    ) -> None:
        evidence_record = evidence_by_id.get(obj.payload.evidence_record_id)
        if evidence_record is None:
            raise ValueError(
                "EvidenceQuote payload.evidence_record_id must resolve in "
                "metadata.evidence_records[]"
            )
        _validate_evidence_payload_alignment(obj.payload, evidence_record)

    @classmethod
    def _validate_condition_object(
        cls,
        obj: ChemicalConditionCuratableObject,
        evidence_by_id: Mapping[str, Any],
        objects_by_ref: Mapping[tuple[str, str], ChemicalExtractorCuratableObject],
    ) -> None:
        _validate_chebi_curie(
            obj.payload.condition_chemical.curie,
            "ChemicalCondition.payload.condition_chemical.curie",
        )

        if obj.evidence_record_ids != obj.payload.evidence_record_ids:
            raise ValueError(
                "ChemicalCondition payload.evidence_record_ids must match curatable "
                "object evidence_record_ids"
            )
        for evidence_record_id in obj.evidence_record_ids:
            evidence_record = evidence_by_id.get(evidence_record_id)
            if evidence_record is None:
                raise ValueError(
                    "ChemicalCondition evidence_record_ids must resolve in "
                    f"metadata.evidence_records[]: {evidence_record_id}"
                )
            _validate_metadata_evidence_completeness(evidence_record)

        resolved_ref_types: set[str] = set()
        referenced_evidence_ids: set[str] = set()
        referenced_chemical_curies: set[str] = set()
        unknown_refs: list[str] = []

        for object_ref in obj.object_refs:
            referenced_object = objects_by_ref.get(object_ref.ref_key())
            if referenced_object is None:
                unknown_refs.append(object_ref.pending_ref_id or object_ref.object_id or "")
                continue
            if (
                object_ref.object_type is not None
                and object_ref.object_type != referenced_object.object_type
            ):
                raise ValueError(
                    "ChemicalCondition object_refs[] object_type must match the "
                    "referenced object"
                )

            resolved_ref_types.add(referenced_object.object_type)
            if isinstance(referenced_object, EvidenceQuoteCuratableObject):
                referenced_evidence_ids.add(referenced_object.payload.evidence_record_id)
            elif isinstance(referenced_object, ChemicalTermCuratableObject):
                referenced_chemical_curies.add(referenced_object.payload.curie)

        if unknown_refs:
            raise ValueError(
                "ChemicalCondition object_refs[] references unknown objects: "
                + ", ".join(sorted(unknown_refs))
            )

        missing_ref_types = sorted(_REQUIRED_CONDITION_REF_TYPES - resolved_ref_types)
        if missing_ref_types:
            raise ValueError(
                "ChemicalCondition must reference supporting objects: "
                + ", ".join(missing_ref_types)
            )

        missing_evidence_refs = sorted(
            set(obj.payload.evidence_record_ids) - referenced_evidence_ids
        )
        if missing_evidence_refs:
            raise ValueError(
                "ChemicalCondition object_refs[] must include EvidenceQuote objects "
                "for evidence_record_ids: "
                + ", ".join(missing_evidence_refs)
            )

        if obj.payload.condition_chemical.curie not in referenced_chemical_curies:
            raise ValueError(
                "ChemicalCondition object_refs[] must include a ChemicalTerm whose "
                "payload.curie matches payload.condition_chemical.curie"
            )

        export_behavior = obj.metadata.get("export_behavior")
        if not isinstance(export_behavior, Mapping):
            raise ValueError(
                "ChemicalCondition metadata.export_behavior must be a mapping"
            )
        if (
            export_behavior.get("status") != "blocked"
            or export_behavior.get("exportable") is not False
            or export_behavior.get("submit") is not False
        ):
            raise ValueError(
                "ChemicalCondition metadata.export_behavior must declare blocked, "
                "non-exportable, non-submitting state"
            )


def _validate_schema_ref(
    schema_ref: SchemaRef,
    *,
    expected_schema_id: str,
    expected_name: str,
    expected_uri: str,
    expected_definition_state: DefinitionState,
    object_label: str,
) -> None:
    if schema_ref.schema_id != expected_schema_id:
        raise ValueError(f"{object_label} schema_ref must be {expected_schema_id}")
    if schema_ref.provider != ALLIANCE_LINKML_SCHEMA_PROVIDER:
        raise ValueError(f"{object_label} schema_ref provider must be alliance_linkml")
    if schema_ref.name != expected_name:
        raise ValueError(f"{object_label} schema_ref name must be {expected_name}")
    if schema_ref.version != ALLIANCE_LINKML_COMMIT:
        raise ValueError(
            f"{object_label} schema_ref version must match the pinned LinkML commit"
        )
    if schema_ref.uri is not None and schema_ref.uri != expected_uri:
        raise ValueError(f"{object_label} schema_ref uri must target the pinned schema")
    if schema_ref.definition_state != expected_definition_state:
        raise ValueError(
            f"{object_label} schema_ref definition_state must be "
            f"'{expected_definition_state.value}'"
        )


def _validate_chebi_curie(value: str, field_label: str) -> None:
    if _CHEBI_CURIE_PATTERN.match(value) is None:
        raise ValueError(f"{field_label} must be a CHEBI CURIE")


def _validate_metadata_evidence_completeness(evidence_record: Any) -> None:
    missing_fields = sorted(
        field_name
        for field_name in ("verified_quote", "page", "section", "chunk_id")
        if _is_missing_value(getattr(evidence_record, field_name))
    )
    if missing_fields:
        raise ValueError(
            "metadata.evidence_records[] entries referenced by ChemicalCondition "
            "must include verified_quote, page, section, and chunk_id"
        )


def _validate_evidence_payload_alignment(
    payload: EvidenceQuotePayload,
    evidence_record: Any,
) -> None:
    _validate_metadata_evidence_completeness(evidence_record)
    comparisons = {
        "verified_quote": payload.verified_quote,
        "page": payload.page,
        "section": payload.section,
        "chunk_id": payload.chunk_id,
        "subsection": payload.subsection,
        "figure_reference": payload.figure_reference,
    }
    for field_name, payload_value in comparisons.items():
        metadata_value = getattr(evidence_record, field_name)
        if payload_value != metadata_value:
            raise ValueError(
                f"EvidenceQuote payload.{field_name} must match "
                "metadata.evidence_records[]"
            )


def _is_missing_value(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    return False


class ChemicalExtractorRepairResponse(
    RootModel[
        ChemicalExtractionResultEnvelope
        | DomainEnvelopeRepairPatch
        | DomainEnvelopeExtractorFinalClassification
    ]
):
    """Chemical first-pass extraction or repair_action response schema."""

    __envelope_class__ = True
    __domain_envelope_extractor_repair_response__ = True


__all__ = [
    "ChemicalConditionCuratableObject",
    "ChemicalConditionPayload",
    "ChemicalExtractionResultEnvelope",
    "ChemicalExtractorRepairResponse",
    "ChemicalTermCuratableObject",
    "ChemicalTermPayload",
    "EvidenceQuoteCuratableObject",
    "EvidenceQuotePayload",
    "ReferenceCuratableObject",
    "ReferencePayload",
    "VocabularyTermSnapshotPayload",
    "OntologyTermSnapshotPayload",
]
