"""Chemical extractor schema for Alliance chemical-condition envelopes."""

from __future__ import annotations

import re
import copy
from collections.abc import Mapping
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictStr,
    field_validator,
    model_validator,
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


def _optional_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _slug(value: object, *, fallback: str) -> str:
    text = _optional_text(value) or fallback
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return slug or fallback


def _next_pending_ref(base: str, used_refs: set[str]) -> str:
    candidate = base
    suffix = 2
    while candidate in used_refs:
        candidate = f"{base}-{suffix}"
        suffix += 1
    used_refs.add(candidate)
    return candidate


def _object_ref_value(obj: Mapping[str, Any]) -> str | None:
    return _optional_text(obj.get("pending_ref_id")) or _optional_text(
        obj.get("object_id")
    )


def _ensure_pending_ref(
    obj: dict[str, Any],
    *,
    fallback_base: str,
    used_refs: set[str],
) -> str:
    existing_ref = _optional_text(obj.get("pending_ref_id")) or _optional_text(
        obj.get("object_id")
    )
    if existing_ref:
        used_refs.add(existing_ref)
        return existing_ref
    pending_ref_id = _next_pending_ref(fallback_base, used_refs)
    obj["pending_ref_id"] = pending_ref_id
    return pending_ref_id


def _schema_ref_payload(
    *,
    schema_id: str,
    name: str,
    uri: str,
    definition_state: DefinitionState = DefinitionState.STABLE,
) -> dict[str, Any]:
    return {
        "schema_id": schema_id,
        "provider": ALLIANCE_LINKML_SCHEMA_PROVIDER,
        "name": name,
        "version": ALLIANCE_LINKML_COMMIT,
        "uri": uri,
        "definition_state": definition_state.value,
    }


def _chemical_term_payload_from_condition(
    condition_payload: Mapping[str, Any],
) -> dict[str, Any] | None:
    term = condition_payload.get("condition_chemical")
    if not isinstance(term, Mapping):
        return None
    curie = _optional_text(term.get("curie"))
    name = _optional_text(term.get("name"))
    if curie is None and name is None:
        return None
    source_mentions = condition_payload.get("source_mentions")
    mentions = [
        str(item).strip()
        for item in source_mentions
        if isinstance(item, str) and item.strip()
    ] if isinstance(source_mentions, list) else []
    source_mention = _optional_text(condition_payload.get("source_chemical_mention"))
    if source_mention and source_mention not in mentions:
        mentions.append(source_mention)
    return {
        "curie": curie,
        "name": name or curie,
        "source_mentions": mentions,
    }


def _evidence_payload_from_metadata(record: Mapping[str, Any]) -> dict[str, Any] | None:
    evidence_record_id = _optional_text(record.get("evidence_record_id"))
    verified_quote = _optional_text(record.get("verified_quote"))
    section = _optional_text(record.get("section"))
    chunk_id = _optional_text(record.get("chunk_id"))
    page = record.get("page")
    if (
        evidence_record_id is None
        or verified_quote is None
        or section is None
        or chunk_id is None
        or isinstance(page, bool)
        or not isinstance(page, int)
    ):
        return None
    return {
        "evidence_record_id": evidence_record_id,
        "entity": _optional_text(record.get("entity")),
        "verified_quote": verified_quote,
        "page": page,
        "section": section,
        "subsection": _optional_text(record.get("subsection")),
        "chunk_id": chunk_id,
        "figure_reference": _optional_text(record.get("figure_reference")),
    }


def _append_object_ref(
    obj: dict[str, Any],
    *,
    pending_ref_id: str,
    object_type: str,
) -> None:
    refs = obj.setdefault("object_refs", [])
    if not isinstance(refs, list):
        refs = []
        obj["object_refs"] = refs
    for existing in refs:
        if not isinstance(existing, Mapping):
            continue
        if (
            existing.get("pending_ref_id") == pending_ref_id
            and existing.get("object_type") in {None, object_type}
        ):
            if existing.get("object_type") is None:
                existing["object_type"] = object_type
            return
    refs.append({"pending_ref_id": pending_ref_id, "object_type": object_type})


def _align_metadata_evidence_record_from_quote_payload(
    payload: Mapping[str, Any],
    metadata_record: dict[str, Any],
) -> None:
    """Keep duplicated quote-location scaffold in sync when the quote matches."""

    if _optional_text(payload.get("evidence_record_id")) != _optional_text(
        metadata_record.get("evidence_record_id")
    ):
        return
    for field_name in ("verified_quote", "section"):
        if _optional_text(payload.get(field_name)) != _optional_text(
            metadata_record.get(field_name)
        ):
            return
    if payload.get("page") != metadata_record.get("page"):
        return

    for field_name in ("chunk_id", "subsection", "figure_reference"):
        payload_value = payload.get(field_name)
        if payload_value is not None:
            metadata_record[field_name] = payload_value


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

    curie: StrictStr | None = None
    name: StrictStr

    @field_validator("curie", mode="before")
    @classmethod
    def _validate_optional_curie(cls, value: object) -> object:
        return _strip_optional_string(value)

    @field_validator("name", mode="before")
    @classmethod
    def _validate_required_strings(cls, value: object, info) -> object:
        return _strip_required_string(value, info.field_name)


class ChemicalTermPayload(ChemicalExtractorPayloadModel):
    """Payload for one chemical ontology term candidate or reference."""

    curie: StrictStr | None = None
    name: StrictStr
    source_mentions: list[StrictStr] = Field(default_factory=list)

    @field_validator("curie", mode="before")
    @classmethod
    def _validate_optional_curie(cls, value: object) -> object:
        return _strip_optional_string(value)

    @field_validator("name", mode="before")
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

    @model_validator(mode="before")
    @classmethod
    def _canonicalize_chemical_scaffold(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value

        normalized = copy.deepcopy(dict(value))
        curatable_objects = normalized.get("curatable_objects")
        if not isinstance(curatable_objects, list):
            return normalized

        metadata = normalized.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
            normalized["metadata"] = metadata
        evidence_records = metadata.get("evidence_records")
        if not isinstance(evidence_records, list):
            evidence_records = []
        evidence_by_id = {
            evidence_id: record
            for record in evidence_records
            if isinstance(record, Mapping)
            for evidence_id in [_optional_text(record.get("evidence_record_id"))]
            if evidence_id
        }

        used_refs = {
            ref
            for obj in curatable_objects
            if isinstance(obj, Mapping)
            for ref in [_object_ref_value(obj)]
            if ref
        }

        for index, obj in enumerate(curatable_objects):
            if not isinstance(obj, dict):
                continue
            object_type = _optional_text(obj.get("object_type")) or "object"
            base = f"{_slug(object_type, fallback='object')}-{index + 1}"
            _ensure_pending_ref(obj, fallback_base=base, used_refs=used_refs)
            if obj.get("object_type") == EVIDENCE_QUOTE_OBJECT_TYPE:
                obj.setdefault("model_ref", EVIDENCE_QUOTE_MODEL_REF)
                obj.setdefault("object_role", "metadata_only")
                obj.setdefault("definition_state", DefinitionState.IN_DEVELOPMENT.value)
                payload = obj.get("payload")
                if isinstance(payload, Mapping):
                    evidence_id = _optional_text(payload.get("evidence_record_id"))
                    metadata_record = evidence_by_id.get(evidence_id or "")
                    if isinstance(metadata_record, dict):
                        _align_metadata_evidence_record_from_quote_payload(
                            payload,
                            metadata_record,
                        )
            elif obj.get("object_type") == REFERENCE_OBJECT_TYPE:
                obj["schema_ref"] = _schema_ref_payload(
                    schema_id=REFERENCE_SCHEMA_ID,
                    name=REFERENCE_SCHEMA_NAME,
                    uri=REFERENCE_SCHEMA_URI,
                )
                obj.setdefault("model_ref", REFERENCE_MODEL_REF)
                obj.setdefault("object_role", "validated_reference")
            elif obj.get("object_type") == CHEMICAL_TERM_OBJECT_TYPE:
                obj["schema_ref"] = _schema_ref_payload(
                    schema_id=CHEMICAL_TERM_SCHEMA_ID,
                    name=CHEMICAL_TERM_SCHEMA_NAME,
                    uri=CHEMICAL_TERM_SCHEMA_URI,
                )
                obj.setdefault("model_ref", CHEMICAL_TERM_MODEL_REF)
                obj.setdefault("object_role", "validated_reference")
            elif obj.get("object_type") == CHEMICAL_CONDITION_OBJECT_TYPE:
                obj["schema_ref"] = _schema_ref_payload(
                    schema_id=CHEMICAL_CONDITION_SCHEMA_ID,
                    name=CHEMICAL_CONDITION_SCHEMA_NAME,
                    uri=CHEMICAL_CONDITION_SCHEMA_URI,
                    definition_state=DefinitionState.IN_DEVELOPMENT,
                )

        existing_by_type: dict[str, list[dict[str, Any]]] = {}
        for obj in curatable_objects:
            if isinstance(obj, dict) and isinstance(obj.get("object_type"), str):
                existing_by_type.setdefault(str(obj["object_type"]), []).append(obj)

        has_condition_object = any(
            obj.get("object_type") == CHEMICAL_CONDITION_OBJECT_TYPE
            for obj in curatable_objects
            if isinstance(obj, Mapping)
        )
        reference_ref = None
        reference_objects = existing_by_type.get(REFERENCE_OBJECT_TYPE, [])
        if reference_objects:
            reference_ref = _object_ref_value(reference_objects[0])
        elif has_condition_object:
            reference_ref = _next_pending_ref("source-reference-1", used_refs)
            curatable_objects.insert(
                0,
                {
                    "object_type": REFERENCE_OBJECT_TYPE,
                    "object_role": "validated_reference",
                    "pending_ref_id": reference_ref,
                    "model_ref": REFERENCE_MODEL_REF,
                    "schema_ref": _schema_ref_payload(
                        schema_id=REFERENCE_SCHEMA_ID,
                        name=REFERENCE_SCHEMA_NAME,
                        uri=REFERENCE_SCHEMA_URI,
                    ),
                    "definition_state": DefinitionState.IN_DEVELOPMENT.value,
                    "payload": {},
                    "metadata": {
                        "validation_state": "pending_reference_materialization"
                    },
                },
            )

        chemical_ref_by_key: dict[tuple[str | None, str | None], str] = {}
        for obj in existing_by_type.get(CHEMICAL_TERM_OBJECT_TYPE, []):
            payload = obj.get("payload")
            if not isinstance(payload, Mapping):
                continue
            key = (_optional_text(payload.get("curie")), _optional_text(payload.get("name")))
            ref = _object_ref_value(obj)
            if ref:
                chemical_ref_by_key[key] = ref

        evidence_ref_by_id: dict[str, str] = {}
        for obj in existing_by_type.get(EVIDENCE_QUOTE_OBJECT_TYPE, []):
            payload = obj.get("payload")
            if not isinstance(payload, Mapping):
                continue
            evidence_id = _optional_text(payload.get("evidence_record_id"))
            ref = _object_ref_value(obj)
            if evidence_id and ref:
                evidence_ref_by_id[evidence_id] = ref

        for condition_index, obj in enumerate(list(curatable_objects), start=1):
            if not isinstance(obj, dict):
                continue
            if obj.get("object_type") != CHEMICAL_CONDITION_OBJECT_TYPE:
                continue

            payload = obj.get("payload")
            if not isinstance(payload, dict):
                continue
            obj.setdefault("model_ref", CHEMICAL_CONDITION_MODEL_REF)
            obj.setdefault("definition_state", DefinitionState.IN_DEVELOPMENT.value)
            if not obj.get("definition_notes"):
                obj["definition_notes"] = [
                    "Pending only; export is blocked until host annotation context "
                    "and reference materialization are supplied."
                ]
            if not obj.get("schema_ref"):
                obj["schema_ref"] = _schema_ref_payload(
                    schema_id=CHEMICAL_CONDITION_SCHEMA_ID,
                    name=CHEMICAL_CONDITION_SCHEMA_NAME,
                    uri=CHEMICAL_CONDITION_SCHEMA_URI,
                    definition_state=DefinitionState.IN_DEVELOPMENT,
                )
            obj.setdefault(
                "evidence_record_ids",
                list(payload.get("evidence_record_ids") or []),
            )
            metadata_payload = obj.setdefault("metadata", {})
            if isinstance(metadata_payload, dict):
                metadata_payload.setdefault(
                    "export_behavior",
                    {
                        "status": "blocked",
                        "exportable": False,
                        "submit": False,
                        "reason": (
                            "Chemical condition export requires a host annotation, "
                            "materialized reference, and downstream submission adapter."
                        ),
                    },
                )

            term_payload = _chemical_term_payload_from_condition(payload)
            if term_payload is not None:
                term_key = (
                    _optional_text(term_payload.get("curie")),
                    _optional_text(term_payload.get("name")),
                )
                chemical_ref = chemical_ref_by_key.get(term_key)
                if chemical_ref is None:
                    chemical_ref = _next_pending_ref(
                        f"chemical-reference-{condition_index}", used_refs
                    )
                    chemical_ref_by_key[term_key] = chemical_ref
                    curatable_objects.append(
                        {
                            "object_type": CHEMICAL_TERM_OBJECT_TYPE,
                            "object_role": "validated_reference",
                            "pending_ref_id": chemical_ref,
                            "model_ref": CHEMICAL_TERM_MODEL_REF,
                            "schema_ref": _schema_ref_payload(
                                schema_id=CHEMICAL_TERM_SCHEMA_ID,
                                name=CHEMICAL_TERM_SCHEMA_NAME,
                                uri=CHEMICAL_TERM_SCHEMA_URI,
                            ),
                            "definition_state": DefinitionState.IN_DEVELOPMENT.value,
                            "payload": term_payload,
                        }
                    )
                _append_object_ref(
                    obj,
                    pending_ref_id=chemical_ref,
                    object_type=CHEMICAL_TERM_OBJECT_TYPE,
                )

            if reference_ref:
                _append_object_ref(
                    obj,
                    pending_ref_id=reference_ref,
                    object_type=REFERENCE_OBJECT_TYPE,
                )

            for evidence_record_id in list(payload.get("evidence_record_ids") or []):
                evidence_ref = evidence_ref_by_id.get(evidence_record_id)
                if evidence_ref is None:
                    evidence_payload = _evidence_payload_from_metadata(
                        evidence_by_id.get(evidence_record_id, {})
                    )
                    if evidence_payload is None:
                        continue
                    evidence_ref = _next_pending_ref(
                        f"evidence-quote-{len(evidence_ref_by_id) + 1}", used_refs
                    )
                    evidence_ref_by_id[evidence_record_id] = evidence_ref
                    curatable_objects.append(
                        {
                            "object_type": EVIDENCE_QUOTE_OBJECT_TYPE,
                            "object_role": "metadata_only",
                            "pending_ref_id": evidence_ref,
                            "model_ref": EVIDENCE_QUOTE_MODEL_REF,
                            "definition_state": DefinitionState.IN_DEVELOPMENT.value,
                            "payload": evidence_payload,
                        }
                    )
                _append_object_ref(
                    obj,
                    pending_ref_id=evidence_ref,
                    object_type=EVIDENCE_QUOTE_OBJECT_TYPE,
                )

        return normalized

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
                if obj.payload.curie is not None:
                    _validate_chebi_curie(
                        obj.payload.curie, "ChemicalTerm.payload.curie"
                    )
            elif isinstance(obj, EvidenceQuoteCuratableObject):
                self._validate_evidence_quote_object(obj, evidence_by_id)
            elif isinstance(obj, ChemicalConditionCuratableObject):
                self._validate_condition_object(obj, evidence_by_id, objects_by_ref)

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
        if obj.payload.condition_chemical.curie is not None:
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
        referenced_chemical_names: list[str] = []
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
                if referenced_object.payload.curie is not None:
                    referenced_chemical_curies.add(referenced_object.payload.curie)
                referenced_chemical_names.append(referenced_object.payload.name)

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

        if obj.payload.condition_chemical.curie is not None:
            chemical_ref_matches = (
                obj.payload.condition_chemical.curie in referenced_chemical_curies
            )
            match_label = "payload.curie matches payload.condition_chemical.curie"
        else:
            matching_chemical_names = [
                name
                for name in referenced_chemical_names
                if name == obj.payload.condition_chemical.name
            ]
            if len(matching_chemical_names) > 1:
                raise ValueError(
                    "ChemicalCondition object_refs[] must not include multiple "
                    "ChemicalTerm objects with the same payload.name when "
                    "payload.condition_chemical.curie is absent"
                )
            chemical_ref_matches = len(matching_chemical_names) == 1
            match_label = "payload.name matches payload.condition_chemical.name"

        if not chemical_ref_matches:
            raise ValueError(
                "ChemicalCondition object_refs[] must include a ChemicalTerm whose "
                f"{match_label}"
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


__all__ = [
    "ChemicalConditionCuratableObject",
    "ChemicalConditionPayload",
    "ChemicalExtractionResultEnvelope",
    "ChemicalTermCuratableObject",
    "ChemicalTermPayload",
    "EvidenceQuoteCuratableObject",
    "EvidenceQuotePayload",
    "ReferenceCuratableObject",
    "ReferencePayload",
    "VocabularyTermSnapshotPayload",
    "OntologyTermSnapshotPayload",
]
