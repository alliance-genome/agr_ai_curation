"""Phenotype extractor schema for Alliance phenotype domain-envelope output."""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictStr,
    model_validator,
)

from src.lib.openai_agents.models import (
    PhenotypeResultEnvelope as RuntimePhenotypeResultEnvelope,
)
from src.schemas.domain_envelope import (
    CuratableObjectEnvelope,
    DefinitionState,
    SchemaRef,
    field_path_exists,
)


PHENOTYPE_OBJECT_TYPE = "PhenotypeAnnotation"
PHENOTYPE_SUBJECT_OBJECT_TYPE = "PhenotypeSubject"
PHENOTYPE_TERM_OBJECT_TYPE = "PhenotypeTerm"
REFERENCE_OBJECT_TYPE = "Reference"
EVIDENCE_QUOTE_OBJECT_TYPE = "EvidenceQuote"

PHENOTYPE_MODEL_REF = "PhenotypeAnnotationPayload"
PHENOTYPE_SUBJECT_MODEL_REF = "PhenotypeSubjectPayload"
PHENOTYPE_TERM_MODEL_REF = "PhenotypeTermPayload"
REFERENCE_MODEL_REF = "ReferencePayload"
EVIDENCE_QUOTE_MODEL_REF = "EvidenceQuotePayload"

ALLIANCE_LINKML_PROVIDER = "alliance_linkml"
ALLIANCE_LINKML_COMMIT = "1b11d0888f19eba4ca72022200bb7d96b30d4a52"
PHENOTYPE_SCHEMA_ID = "alliance.linkml.PhenotypeAnnotation"
PHENOTYPE_SUBJECT_SCHEMA_ID = "alliance.linkml.BiologicalEntity"
PHENOTYPE_TERM_SCHEMA_ID = "alliance.linkml.PhenotypeTerm"
REFERENCE_SCHEMA_ID = "alliance.linkml.Reference"
PHENOTYPE_SCHEMA_URI = (
    "https://github.com/alliance-genome/agr_curation_schema/blob/"
    f"{ALLIANCE_LINKML_COMMIT}/model/schema/phenotypeAndDiseaseAnnotation.yaml"
)
PHENOTYPE_SUBJECT_SCHEMA_URI = (
    "https://github.com/alliance-genome/agr_curation_schema/blob/"
    f"{ALLIANCE_LINKML_COMMIT}/model/schema/core.yaml"
)
PHENOTYPE_TERM_SCHEMA_URI = (
    "https://github.com/alliance-genome/agr_curation_schema/blob/"
    f"{ALLIANCE_LINKML_COMMIT}/model/schema/ontologyTerm.yaml"
)
REFERENCE_SCHEMA_URI = (
    "https://github.com/alliance-genome/agr_curation_schema/blob/"
    f"{ALLIANCE_LINKML_COMMIT}/model/schema/reference.yaml"
)

_EXPECTED_OBJECT_REF_TYPES = frozenset(
    {
        PHENOTYPE_SUBJECT_OBJECT_TYPE,
        PHENOTYPE_TERM_OBJECT_TYPE,
        REFERENCE_OBJECT_TYPE,
        EVIDENCE_QUOTE_OBJECT_TYPE,
    }
)
_SUBJECT_RESOLUTION_STATES = frozenset(
    {"resolved", "pending_entity_resolution", "blocked_missing_subject"}
)
_TERM_RESOLUTION_STATES = frozenset(
    {"resolved", "pending_ontology_resolution"}
)


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
        "provider": ALLIANCE_LINKML_PROVIDER,
        "name": name,
        "version": ALLIANCE_LINKML_COMMIT,
        "uri": uri,
        "definition_state": definition_state.value,
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
        if not isinstance(existing, dict):
            continue
        if (
            existing.get("pending_ref_id") == pending_ref_id
            and existing.get("object_type") in {None, object_type}
        ):
            if existing.get("object_type") is None:
                existing["object_type"] = object_type
            return
    refs.append({"pending_ref_id": pending_ref_id, "object_type": object_type})


def _has_object_ref_type(obj: Mapping[str, Any], object_type: str) -> bool:
    refs = obj.get("object_refs")
    if not isinstance(refs, list):
        return False
    return any(
        isinstance(ref, Mapping) and ref.get("object_type") == object_type
        for ref in refs
    )


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


def _blocked_export_behavior() -> dict[str, Any]:
    return {
        "status": "blocked",
        "exportable": False,
        "submit": False,
        "reason": (
            "Phenotype export is blocked until subject subtype, reference "
            "materialization, ontology term resolution, and write targets are verified."
        ),
    }


def _blocked_write_behavior() -> dict[str, Any]:
    return {
        "status": "blocked",
        "reason": (
            "Pending phenotype assertions cannot be written until subject, "
            "reference, ontology, and adapter targets are verified."
        ),
    }


def _normalize_subject_payload_scaffold(payload: dict[str, Any]) -> None:
    if payload.get("resolution_state") != "resolved":
        return
    missing = [
        field_name
        for field_name in ("subject_identifier", "subject_type", "taxon")
        if _is_missing(payload.get(field_name))
    ]
    if not missing:
        return
    if "subject_identifier" not in missing:
        return
    payload["resolution_state"] = "pending_entity_resolution"
    payload.setdefault(
        "resolution_note",
        "Subject was marked resolved but is missing required resolved identifiers.",
    )


class PhenotypeSubjectPayload(BaseModel):
    """Pending biological entity subject metadata for a phenotype assertion."""

    model_config = ConfigDict(extra="forbid")

    resolution_state: Literal[
        "resolved",
        "pending_entity_resolution",
        "blocked_missing_subject",
    ] = Field(description="Subject resolution state for the phenotype assertion")
    subject_identifier: StrictStr | None = Field(
        default=None,
        description="Durable subject identifier when available from extraction or lookup",
    )
    subject_label: StrictStr | None = Field(
        default=None,
        description="Paper-facing subject label, genotype, allele, gene, or AGM text",
    )
    subject_type: StrictStr | None = Field(
        default=None,
        description="Subject subtype such as gene, allele, or affected_genomic_model",
    )
    taxon: StrictStr | None = Field(
        default=None,
        description="NCBI Taxon CURIE when explicitly supported by the paper or lookup",
    )
    resolution_note: StrictStr | None = Field(
        default=None,
        description="Curator-facing blocker when the subject cannot be resolved yet",
    )

    @model_validator(mode="after")
    def _validate_resolution_state(self) -> "PhenotypeSubjectPayload":
        if self.resolution_state == "resolved":
            missing = [
                field_name
                for field_name in ("subject_identifier", "subject_type", "taxon")
                if _is_missing(getattr(self, field_name))
            ]
            if missing:
                raise ValueError(
                    "resolved phenotype subjects must include "
                    + ", ".join(missing)
                )
        if (
            self.resolution_state == "blocked_missing_subject"
            and _is_missing(self.resolution_note)
        ):
            raise ValueError(
                "blocked_missing_subject phenotype subjects must include resolution_note"
            )
        return self


class OntologyLookupHintPayload(BaseModel):
    """Structured context for phenotype ontology term lookup."""

    model_config = ConfigDict(extra="forbid")

    data_provider: StrictStr | None = Field(
        default=None,
        description="Alliance data provider abbreviation such as WB or MGI",
    )
    taxon_id: StrictStr | None = Field(
        default=None,
        description="NCBI Taxon CURIE for ontology class selection",
    )
    evidence_record_id: StrictStr | None = Field(
        default=None,
        description="Evidence record ID that supplies quote/chunk context",
    )

    @model_validator(mode="after")
    def _validate_hint_values(self) -> "OntologyLookupHintPayload":
        for field_name in ("data_provider", "taxon_id", "evidence_record_id"):
            value = getattr(self, field_name)
            if value is not None and _is_missing(value):
                raise ValueError(f"ontology_lookup_hint.{field_name} must not be empty")
        return self


class PhenotypeTermPayload(BaseModel):
    """Pending phenotype ontology term reference."""

    model_config = ConfigDict(extra="forbid")

    resolution_state: Literal[
        "resolved",
        "pending_ontology_resolution",
    ] = Field(
        default="pending_ontology_resolution",
        description="Phenotype ontology term resolution state",
    )
    curie: StrictStr | None = Field(
        default=None,
        description="Phenotype ontology CURIE when already supplied or resolved",
    )
    label: StrictStr | None = Field(
        default=None,
        description="Curator-facing term label or source label for lookup",
    )
    source_mentions: list[StrictStr] = Field(
        default_factory=list,
        description="Paper text that supported this phenotype term candidate",
    )
    ontology_lookup_hint: OntologyLookupHintPayload | None = Field(
        default=None,
        description="Structured provider/taxon/evidence context for ontology lookup",
    )
    export_state: Literal[
        "blocked_pending_ontology_resolution",
        "ready_after_ontology_resolution",
    ] = Field(
        default="blocked_pending_ontology_resolution",
        description="Export gate for unresolved phenotype term candidates",
    )
    write_blocked_reason: StrictStr | None = Field(
        default="phenotype term CURIE unresolved",
        description="Curator-facing write blocker while the phenotype CURIE is unresolved",
    )

    @model_validator(mode="after")
    def _validate_resolution_state(self) -> "PhenotypeTermPayload":
        if _is_missing(self.curie) and _is_missing(self.label):
            raise ValueError(
                "PhenotypeTerm payload requires curie or label for ontology lookup"
            )
        if _has_missing_strings(self.source_mentions):
            raise ValueError(
                "PhenotypeTerm payload.source_mentions must not contain empty values"
            )
        if self.resolution_state == "resolved" and _is_missing(self.curie):
            raise ValueError("resolved PhenotypeTerm payload.curie is required")
        if self.resolution_state == "pending_ontology_resolution":
            if not self.source_mentions:
                raise ValueError(
                    "pending PhenotypeTerm payload.source_mentions must include "
                    "at least one source mention"
                )
            if self.export_state != "blocked_pending_ontology_resolution":
                raise ValueError(
                    "pending PhenotypeTerm payload.export_state must block export"
                )
            if _is_missing(self.write_blocked_reason):
                raise ValueError(
                    "pending PhenotypeTerm payload.write_blocked_reason is required"
                )
        return self


class ReferencePayload(BaseModel):
    """Pending source-paper reference metadata."""

    model_config = ConfigDict(extra="forbid")

    reference_id: int | None = Field(
        default=None,
        description="Alliance reference row ID when already resolved",
    )
    title: StrictStr | None = Field(default=None, description="Source paper title")
    filename: StrictStr | None = Field(default=None, description="Source document filename")


class EvidenceQuotePayload(BaseModel):
    """One record_evidence-verified quote with document location."""

    model_config = ConfigDict(extra="forbid")

    evidence_record_id: StrictStr = Field(description="Stable verified evidence record ID")
    entity: StrictStr | None = Field(default=None, description="Entity or assertion label")
    verified_quote: StrictStr = Field(description="Verbatim quote verified by record_evidence")
    page: int = Field(ge=1, description="1-based page containing the quote")
    section: StrictStr = Field(description="Document section containing the quote")
    chunk_id: StrictStr = Field(description="Document chunk ID used for verification")
    subsection: StrictStr | None = Field(default=None, description="Subsection, if present")
    figure_reference: StrictStr | None = Field(
        default=None,
        description="Figure or table locator, if present",
    )

    @model_validator(mode="after")
    def _validate_evidence_fields(self) -> "EvidenceQuotePayload":
        for field_name in (
            "evidence_record_id",
            "verified_quote",
            "section",
            "chunk_id",
        ):
            if _is_missing(getattr(self, field_name)):
                raise ValueError(f"EvidenceQuote payload.{field_name} must not be empty")
        return self


class EvidenceQuoteRefPayload(BaseModel):
    """Reference from a PhenotypeAnnotation payload to an evidence quote object."""

    model_config = ConfigDict(extra="forbid")

    evidence_record_id: StrictStr = Field(description="Referenced evidence record ID")


class PhenotypeAnnotationPayload(BaseModel):
    """Pending PhenotypeAnnotation payload grounded to Alliance LinkML fields."""

    model_config = ConfigDict(extra="forbid")

    annotation_kind: Literal["phenotype_assertion"] = Field(
        description="Pending assertion kind for this extractor output"
    )
    phenotype_annotation_object: StrictStr = Field(
        description="Free-text phenotype statement from the paper"
    )
    phenotype_annotation_subject: PhenotypeSubjectPayload = Field(
        description="Pending subject/context payload for the phenotype carrier"
    )
    phenotype_terms: list[PhenotypeTermPayload] = Field(
        min_length=1,
        description="Phenotype ontology terms for this assertion",
    )
    single_reference: ReferencePayload = Field(
        description="Source paper reference metadata"
    )
    evidence_quote: EvidenceQuoteRefPayload = Field(
        description="Primary supporting evidence quote reference"
    )
    evidence_record_ids: list[StrictStr] = Field(
        min_length=1,
        description="Verified evidence records supporting this assertion",
    )
    source_mentions: list[StrictStr] = Field(
        min_length=1,
        description="Raw phenotype mentions supporting this assertion",
    )
    negated: bool = Field(
        default=False,
        description="True only when the paper explicitly reports the phenotype was not observed",
    )
    condition_relations: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Optional LinkML condition_relations context when explicitly supported",
    )
    related_notes: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Optional LinkML related_notes for severity, penetrance, or curator notes",
    )

    @model_validator(mode="after")
    def _validate_required_values(self) -> "PhenotypeAnnotationPayload":
        if _is_missing(self.phenotype_annotation_object):
            raise ValueError("PhenotypeAnnotation payload.phenotype_annotation_object is required")
        if not self.phenotype_terms or (
            _is_missing(self.phenotype_terms[0].curie)
            and _is_missing(self.phenotype_terms[0].label)
        ):
            raise ValueError(
                "PhenotypeAnnotation payload.phenotype_terms[0] requires curie or label"
            )
        if _is_missing(self.evidence_quote.evidence_record_id):
            raise ValueError("PhenotypeAnnotation payload.evidence_quote.evidence_record_id is required")
        if _has_missing_strings(self.evidence_record_ids):
            raise ValueError("PhenotypeAnnotation payload.evidence_record_ids must not contain empty values")
        if _has_missing_strings(self.source_mentions):
            raise ValueError("PhenotypeAnnotation payload.source_mentions must not contain empty values")
        return self


class PhenotypeAnnotationObject(CuratableObjectEnvelope):
    """One pending PhenotypeAnnotation curatable unit."""

    object_type: Literal["PhenotypeAnnotation"] = PHENOTYPE_OBJECT_TYPE
    object_role: Literal["curatable_unit"] = "curatable_unit"
    model_ref: Literal["PhenotypeAnnotationPayload"] = PHENOTYPE_MODEL_REF
    schema_ref: SchemaRef = Field(description="Alliance LinkML PhenotypeAnnotation schema ref")
    definition_state: Literal[DefinitionState.IN_DEVELOPMENT] = DefinitionState.IN_DEVELOPMENT
    definition_notes: list[StrictStr] = Field(
        min_length=1,
        description="Notes explaining pending/export-blocked phenotype assertion semantics",
    )
    payload: PhenotypeAnnotationPayload


class PhenotypeSubjectObject(CuratableObjectEnvelope):
    """Pending subject reference object for a phenotype annotation."""

    object_type: Literal["PhenotypeSubject"] = PHENOTYPE_SUBJECT_OBJECT_TYPE
    object_role: Literal["validated_reference"] = "validated_reference"
    model_ref: Literal["PhenotypeSubjectPayload"] = PHENOTYPE_SUBJECT_MODEL_REF
    schema_ref: SchemaRef = Field(description="Alliance LinkML BiologicalEntity schema ref")
    definition_state: Literal[DefinitionState.IN_DEVELOPMENT] = DefinitionState.IN_DEVELOPMENT
    payload: PhenotypeSubjectPayload


class PhenotypeTermObject(CuratableObjectEnvelope):
    """Pending phenotype ontology term reference object."""

    object_type: Literal["PhenotypeTerm"] = PHENOTYPE_TERM_OBJECT_TYPE
    object_role: Literal["validated_reference"] = "validated_reference"
    model_ref: Literal["PhenotypeTermPayload"] = PHENOTYPE_TERM_MODEL_REF
    schema_ref: SchemaRef = Field(description="Alliance LinkML PhenotypeTerm schema ref")
    definition_state: Literal[DefinitionState.IN_DEVELOPMENT] = DefinitionState.IN_DEVELOPMENT
    payload: PhenotypeTermPayload


class ReferenceObject(CuratableObjectEnvelope):
    """Pending source paper reference object."""

    object_type: Literal["Reference"] = REFERENCE_OBJECT_TYPE
    object_role: Literal["validated_reference"] = "validated_reference"
    model_ref: Literal["ReferencePayload"] = REFERENCE_MODEL_REF
    schema_ref: SchemaRef = Field(description="Alliance LinkML Reference schema ref")
    definition_state: Literal[DefinitionState.IN_DEVELOPMENT] = DefinitionState.IN_DEVELOPMENT
    payload: ReferencePayload


class EvidenceQuoteObject(CuratableObjectEnvelope):
    """Metadata-only verified evidence quote object."""

    object_type: Literal["EvidenceQuote"] = EVIDENCE_QUOTE_OBJECT_TYPE
    object_role: Literal["metadata_only"] = "metadata_only"
    model_ref: Literal["EvidenceQuotePayload"] = EVIDENCE_QUOTE_MODEL_REF
    definition_state: Literal[DefinitionState.IN_DEVELOPMENT] = DefinitionState.IN_DEVELOPMENT
    payload: EvidenceQuotePayload


PhenotypeCuratableObject = Annotated[
    PhenotypeAnnotationObject
    | PhenotypeSubjectObject
    | PhenotypeTermObject
    | ReferenceObject
    | EvidenceQuoteObject,
    Field(discriminator="object_type"),
]


class PhenotypeResultEnvelope(RuntimePhenotypeResultEnvelope):
    """Config-discovered Alliance phenotype extraction envelope."""

    __envelope_class__ = True

    curatable_objects: list[PhenotypeCuratableObject] = Field(
        default_factory=list,
        description=(
            "The only semantic object list for new phenotype extractor runs. "
            "Retained assertions are PhenotypeAnnotation objects with supporting "
            "subject, phenotype term, reference, and evidence quote objects."
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def _canonicalize_phenotype_scaffold(cls, value: object) -> object:
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
            if obj.get("object_type") == PHENOTYPE_OBJECT_TYPE:
                obj["schema_ref"] = _schema_ref_payload(
                    schema_id=PHENOTYPE_SCHEMA_ID,
                    name="PhenotypeAnnotation",
                    uri=PHENOTYPE_SCHEMA_URI,
                    definition_state=DefinitionState.IN_DEVELOPMENT,
                )
                payload = obj.get("payload")
                if isinstance(payload, dict):
                    subject_payload = payload.get("phenotype_annotation_subject")
                    if isinstance(subject_payload, dict):
                        _normalize_subject_payload_scaffold(subject_payload)
            elif obj.get("object_type") == PHENOTYPE_SUBJECT_OBJECT_TYPE:
                obj["schema_ref"] = _schema_ref_payload(
                    schema_id=PHENOTYPE_SUBJECT_SCHEMA_ID,
                    name="BiologicalEntity",
                    uri=PHENOTYPE_SUBJECT_SCHEMA_URI,
                    definition_state=DefinitionState.IN_DEVELOPMENT,
                )
                payload = obj.get("payload")
                if isinstance(payload, dict):
                    _normalize_subject_payload_scaffold(payload)
                    metadata_payload = obj.setdefault("metadata", {})
                    if isinstance(metadata_payload, dict):
                        metadata_payload.setdefault(
                            "validation_state",
                            payload.get("resolution_state"),
                        )
            elif obj.get("object_type") == PHENOTYPE_TERM_OBJECT_TYPE:
                obj["schema_ref"] = _schema_ref_payload(
                    schema_id=PHENOTYPE_TERM_SCHEMA_ID,
                    name="PhenotypeTerm",
                    uri=PHENOTYPE_TERM_SCHEMA_URI,
                )
            elif obj.get("object_type") == REFERENCE_OBJECT_TYPE:
                obj["schema_ref"] = _schema_ref_payload(
                    schema_id=REFERENCE_SCHEMA_ID,
                    name="Reference",
                    uri=REFERENCE_SCHEMA_URI,
                )

        has_annotation_object = any(
            obj.get("object_type") == PHENOTYPE_OBJECT_TYPE
            for obj in curatable_objects
            if isinstance(obj, Mapping)
        )
        raw_mentions = metadata.get("raw_mentions")
        if has_annotation_object and not raw_mentions:
            inferred_mentions: list[dict[str, Any]] = []
            for obj in curatable_objects:
                if not isinstance(obj, Mapping):
                    continue
                if obj.get("object_type") != PHENOTYPE_OBJECT_TYPE:
                    continue
                payload = obj.get("payload")
                if not isinstance(payload, Mapping):
                    continue
                source_mentions = payload.get("source_mentions")
                mention = None
                if isinstance(source_mentions, list):
                    mention = next(
                        (
                            item.strip()
                            for item in source_mentions
                            if isinstance(item, str) and item.strip()
                        ),
                        None,
                    )
                mention = mention or _optional_text(
                    payload.get("phenotype_annotation_object")
                )
                if mention:
                    inferred_mentions.append(
                        {
                            "mention": mention,
                            "entity_type": "phenotype",
                            "evidence_record_ids": list(
                                payload.get("evidence_record_ids") or []
                            ),
                        }
                    )
            metadata["raw_mentions"] = inferred_mentions

        existing_by_type: dict[str, list[dict[str, Any]]] = {}
        for obj in curatable_objects:
            if isinstance(obj, dict) and isinstance(obj.get("object_type"), str):
                existing_by_type.setdefault(str(obj["object_type"]), []).append(obj)

        reference_ref = None
        reference_objects = existing_by_type.get(REFERENCE_OBJECT_TYPE, [])
        if reference_objects:
            reference_ref = _object_ref_value(reference_objects[0])
        elif has_annotation_object:
            reference_ref = _next_pending_ref("paper-reference-1", used_refs)
            curatable_objects.insert(
                0,
                {
                    "object_type": REFERENCE_OBJECT_TYPE,
                    "object_role": "validated_reference",
                    "pending_ref_id": reference_ref,
                    "model_ref": REFERENCE_MODEL_REF,
                    "schema_ref": _schema_ref_payload(
                        schema_id=REFERENCE_SCHEMA_ID,
                        name="Reference",
                        uri=REFERENCE_SCHEMA_URI,
                    ),
                    "definition_state": DefinitionState.IN_DEVELOPMENT.value,
                    "payload": {},
                    "metadata": {
                        "validation_state": "pending_reference_resolution",
                        "validator_binding_id": "phenotype_reference_validator",
                    },
                },
            )

        evidence_ref_by_id: dict[str, str] = {}
        for obj in existing_by_type.get(EVIDENCE_QUOTE_OBJECT_TYPE, []):
            payload = obj.get("payload")
            if not isinstance(payload, Mapping):
                continue
            evidence_id = _optional_text(payload.get("evidence_record_id"))
            ref = _object_ref_value(obj)
            if evidence_id and ref:
                evidence_ref_by_id[evidence_id] = ref

        for annotation_index, obj in enumerate(list(curatable_objects), start=1):
            if not isinstance(obj, dict):
                continue
            if obj.get("object_type") != PHENOTYPE_OBJECT_TYPE:
                continue

            payload = obj.get("payload")
            if not isinstance(payload, dict):
                continue

            obj.setdefault("model_ref", PHENOTYPE_MODEL_REF)
            obj.setdefault("definition_state", DefinitionState.IN_DEVELOPMENT.value)
            if not isinstance(payload.get("single_reference"), Mapping):
                payload["single_reference"] = {}
            if not obj.get("definition_notes"):
                obj["definition_notes"] = [
                    "Pending only; export is blocked until subject, reference, "
                    "ontology, and write targets are resolved."
                ]
            payload_evidence_ids = payload.get("evidence_record_ids")
            object_evidence_ids = obj.get("evidence_record_ids")
            if (
                (payload_evidence_ids is None or payload_evidence_ids == [])
                and isinstance(object_evidence_ids, list)
                and object_evidence_ids
            ):
                payload["evidence_record_ids"] = list(object_evidence_ids)
            obj.setdefault(
                "evidence_record_ids",
                list(payload.get("evidence_record_ids") or []),
            )
            metadata_payload = obj.setdefault("metadata", {})
            if isinstance(metadata_payload, dict):
                subject_payload = payload.get("phenotype_annotation_subject")
                subject_state = (
                    subject_payload.get("resolution_state")
                    if isinstance(subject_payload, Mapping)
                    else None
                )
                if isinstance(subject_state, str) and subject_state.strip():
                    metadata_payload.setdefault("validation_state", subject_state)
                metadata_payload.setdefault("export_behavior", _blocked_export_behavior())
                metadata_payload.setdefault("write_behavior", _blocked_write_behavior())

            subject_payload = payload.get("phenotype_annotation_subject")
            if (
                isinstance(subject_payload, Mapping)
                and not _has_object_ref_type(obj, PHENOTYPE_SUBJECT_OBJECT_TYPE)
            ):
                subject_ref = _next_pending_ref(
                    f"phenotype-subject-{annotation_index}", used_refs
                )
                curatable_objects.append(
                    {
                        "object_type": PHENOTYPE_SUBJECT_OBJECT_TYPE,
                        "object_role": "validated_reference",
                        "pending_ref_id": subject_ref,
                        "model_ref": PHENOTYPE_SUBJECT_MODEL_REF,
                        "schema_ref": _schema_ref_payload(
                            schema_id=PHENOTYPE_SUBJECT_SCHEMA_ID,
                            name="BiologicalEntity",
                            uri=PHENOTYPE_SUBJECT_SCHEMA_URI,
                            definition_state=DefinitionState.IN_DEVELOPMENT,
                        ),
                        "definition_state": DefinitionState.IN_DEVELOPMENT.value,
                        "payload": dict(subject_payload),
                        "metadata": {
                            "validation_state": subject_payload.get(
                                "resolution_state"
                            ),
                            "validator_binding_id": "phenotype_subject_entity_validator",
                        },
                    }
                )
                _append_object_ref(
                    obj,
                    pending_ref_id=subject_ref,
                    object_type=PHENOTYPE_SUBJECT_OBJECT_TYPE,
                )

            phenotype_terms = payload.get("phenotype_terms")
            if (
                isinstance(phenotype_terms, list)
                and not _has_object_ref_type(obj, PHENOTYPE_TERM_OBJECT_TYPE)
            ):
                for term_index, term_payload in enumerate(phenotype_terms, start=1):
                    if not isinstance(term_payload, Mapping):
                        continue
                    term_ref = _next_pending_ref(
                        f"phenotype-term-{annotation_index}-{term_index}",
                        used_refs,
                    )
                    term_evidence_ids: list[str] = []
                    lookup_hint = term_payload.get("ontology_lookup_hint")
                    if isinstance(lookup_hint, Mapping):
                        hint_evidence_id = _optional_text(
                            lookup_hint.get("evidence_record_id")
                        )
                        if hint_evidence_id:
                            term_evidence_ids.append(hint_evidence_id)
                    curatable_objects.append(
                        {
                            "object_type": PHENOTYPE_TERM_OBJECT_TYPE,
                            "object_role": "validated_reference",
                            "pending_ref_id": term_ref,
                            "model_ref": PHENOTYPE_TERM_MODEL_REF,
                            "schema_ref": _schema_ref_payload(
                                schema_id=PHENOTYPE_TERM_SCHEMA_ID,
                                name="PhenotypeTerm",
                                uri=PHENOTYPE_TERM_SCHEMA_URI,
                            ),
                            "definition_state": DefinitionState.IN_DEVELOPMENT.value,
                            "payload": dict(term_payload),
                            "evidence_record_ids": term_evidence_ids,
                            "metadata": {
                                "validation_state": term_payload.get(
                                    "resolution_state",
                                    "pending_ontology_resolution",
                                ),
                                "validator_binding_id": (
                                    "phenotype_term_ontology_validator"
                                ),
                                "export_state": term_payload.get(
                                    "export_state",
                                    "blocked_pending_ontology_resolution",
                                ),
                                "write_blocked_reason": term_payload.get(
                                    "write_blocked_reason",
                                    "phenotype term CURIE unresolved",
                                ),
                            },
                        }
                    )
                    _append_object_ref(
                        obj,
                        pending_ref_id=term_ref,
                        object_type=PHENOTYPE_TERM_OBJECT_TYPE,
                    )

            if reference_ref and not _has_object_ref_type(obj, REFERENCE_OBJECT_TYPE):
                _append_object_ref(
                    obj,
                    pending_ref_id=reference_ref,
                    object_type=REFERENCE_OBJECT_TYPE,
                )

            for evidence_record_id in list(payload.get("evidence_record_ids") or []):
                if _has_object_ref_type(obj, EVIDENCE_QUOTE_OBJECT_TYPE):
                    break
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
    def _validate_phenotype_domain_contract(self) -> "PhenotypeResultEnvelope":
        errors: list[str] = []
        metadata_payload = self.metadata.model_dump(mode="python")
        evidence_by_id = {
            record.evidence_record_id: record
            for record in self.metadata.evidence_records
            if record.evidence_record_id
        }
        objects_by_type = _objects_by_type(self.curatable_objects)
        objects_by_ref, duplicate_ref_errors = _objects_by_ref(self.curatable_objects)
        errors.extend(duplicate_ref_errors)

        if self.curatable_objects and not self.metadata.raw_mentions:
            errors.append(
                "phenotype extractor output must preserve harvested mentions in "
                "metadata.raw_mentions[]"
            )

        for index, obj in enumerate(self.curatable_objects):
            location = f"curatable_objects[{index}]"
            errors.extend(_schema_ref_errors(obj, location))
            errors.extend(_metadata_ref_errors(obj, location, metadata_payload))
            if isinstance(obj, EvidenceQuoteObject):
                errors.extend(_evidence_quote_errors(obj, location, evidence_by_id))
            elif isinstance(obj, PhenotypeSubjectObject):
                errors.extend(_subject_errors(obj, location))
            elif isinstance(obj, PhenotypeTermObject):
                errors.extend(_term_errors(obj, location, evidence_by_id))
            elif isinstance(obj, PhenotypeAnnotationObject):
                errors.extend(
                    _annotation_errors(
                        obj,
                        location,
                        evidence_by_id,
                        objects_by_ref,
                    )
                )

        if self.curatable_objects and not objects_by_type.get(PHENOTYPE_OBJECT_TYPE):
            errors.append(
                "phenotype extractor curatable_objects[] must include at least one "
                "PhenotypeAnnotation when retained objects are present"
            )

        if errors:
            raise ValueError("; ".join(errors))
        return self


def _schema_ref_errors(obj: CuratableObjectEnvelope, location: str) -> list[str]:
    expected = {
        PHENOTYPE_OBJECT_TYPE: (
            PHENOTYPE_SCHEMA_ID,
            "PhenotypeAnnotation",
            PHENOTYPE_SCHEMA_URI,
        ),
        PHENOTYPE_SUBJECT_OBJECT_TYPE: (
            PHENOTYPE_SUBJECT_SCHEMA_ID,
            "BiologicalEntity",
            PHENOTYPE_SUBJECT_SCHEMA_URI,
        ),
        PHENOTYPE_TERM_OBJECT_TYPE: (
            PHENOTYPE_TERM_SCHEMA_ID,
            "PhenotypeTerm",
            PHENOTYPE_TERM_SCHEMA_URI,
        ),
        REFERENCE_OBJECT_TYPE: (
            REFERENCE_SCHEMA_ID,
            "Reference",
            REFERENCE_SCHEMA_URI,
        ),
    }.get(obj.object_type)
    if expected is None:
        return []
    schema_id, schema_name, schema_uri = expected
    errors: list[str] = []
    if obj.schema_ref is None:
        return [f"{location}.schema_ref is required"]
    if obj.schema_ref.schema_id != schema_id:
        errors.append(f"{location}.schema_ref.schema_id must be {schema_id}")
    if obj.schema_ref.provider != ALLIANCE_LINKML_PROVIDER:
        errors.append(
            f"{location}.schema_ref.provider must be {ALLIANCE_LINKML_PROVIDER}"
        )
    if obj.schema_ref.name != schema_name:
        errors.append(f"{location}.schema_ref.name must be {schema_name}")
    if obj.schema_ref.version != ALLIANCE_LINKML_COMMIT:
        errors.append(
            f"{location}.schema_ref.version must match the pinned LinkML commit"
        )
    if obj.schema_ref.uri is not None and obj.schema_ref.uri != schema_uri:
        errors.append(f"{location}.schema_ref.uri must target the pinned LinkML file")
    return errors


def _metadata_ref_errors(
    obj: CuratableObjectEnvelope,
    location: str,
    metadata_payload: dict[str, Any],
) -> list[str]:
    missing_refs = [
        metadata_ref.metadata_path
        for metadata_ref in obj.metadata_refs
        if not field_path_exists(metadata_payload, metadata_ref.metadata_path)
    ]
    if not missing_refs:
        return []
    return [
        f"{location}.metadata_refs references missing metadata paths: "
        + ", ".join(sorted(missing_refs))
    ]


def _evidence_quote_errors(
    obj: EvidenceQuoteObject,
    location: str,
    evidence_by_id: dict[str, Any],
) -> list[str]:
    evidence_id = obj.payload.evidence_record_id
    record = evidence_by_id.get(evidence_id)
    if record is None:
        return [
            f"{location}.payload.evidence_record_id must resolve in "
            f"metadata.evidence_records[]: {evidence_id}"
        ]
    errors: list[str] = []
    for field_name in ("verified_quote", "page", "section", "chunk_id"):
        if getattr(record, field_name) != getattr(obj.payload, field_name):
            errors.append(
                f"{location}.payload.{field_name} must match metadata.evidence_records[]"
            )
    return errors


def _subject_errors(obj: PhenotypeSubjectObject, location: str) -> list[str]:
    errors: list[str] = []
    validation_state = obj.metadata.get("validation_state")
    if validation_state not in _SUBJECT_RESOLUTION_STATES:
        errors.append(
            f"{location}.metadata.validation_state must describe phenotype subject resolution"
        )
    if validation_state != obj.payload.resolution_state:
        errors.append(
            f"{location}.metadata.validation_state must match payload.resolution_state"
        )
    return errors


def _term_errors(
    obj: PhenotypeTermObject,
    location: str,
    evidence_by_id: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    validation_state = obj.metadata.get("validation_state")
    if validation_state not in _TERM_RESOLUTION_STATES:
        errors.append(
            f"{location}.metadata.validation_state must describe phenotype term resolution"
        )
    if validation_state != obj.payload.resolution_state:
        errors.append(
            f"{location}.metadata.validation_state must match payload.resolution_state"
        )

    if obj.payload.resolution_state == "pending_ontology_resolution":
        if obj.metadata.get("export_state") != "blocked_pending_ontology_resolution":
            errors.append(
                f"{location}.metadata.export_state must block pending ontology terms"
            )
        write_blocked_reason = obj.metadata.get("write_blocked_reason")
        if not isinstance(write_blocked_reason, str) or not write_blocked_reason.strip():
            errors.append(
                f"{location}.metadata.write_blocked_reason is required for pending ontology terms"
            )

    hint = obj.payload.ontology_lookup_hint
    if hint is not None and hint.evidence_record_id is not None:
        if hint.evidence_record_id not in obj.evidence_record_ids:
            errors.append(
                f"{location}.evidence_record_ids must include "
                "payload.ontology_lookup_hint.evidence_record_id"
            )
        if hint.evidence_record_id not in evidence_by_id:
            errors.append(
                f"{location}.payload.ontology_lookup_hint.evidence_record_id must "
                f"resolve in metadata.evidence_records[]: {hint.evidence_record_id}"
            )
    return errors


def _annotation_errors(
    obj: PhenotypeAnnotationObject,
    location: str,
    evidence_by_id: dict[str, Any],
    objects_by_ref: dict[tuple[str, str], CuratableObjectEnvelope],
) -> list[str]:
    errors: list[str] = []
    payload_evidence_ids = list(obj.payload.evidence_record_ids)
    object_evidence_ids = list(obj.evidence_record_ids)
    if object_evidence_ids != payload_evidence_ids:
        errors.append(
            f"{location}.payload.evidence_record_ids must match object evidence_record_ids"
        )
    missing_evidence_ids = sorted(
        evidence_id
        for evidence_id in object_evidence_ids
        if evidence_id not in evidence_by_id
    )
    if missing_evidence_ids:
        errors.append(
            f"{location}.evidence_record_ids references unknown metadata.evidence_records[]: "
            + ", ".join(missing_evidence_ids)
        )
    if obj.payload.evidence_quote.evidence_record_id not in object_evidence_ids:
        errors.append(
            f"{location}.payload.evidence_quote.evidence_record_id must be one of "
            "object evidence_record_ids"
        )

    resolved_ref_types: set[str] = set()
    referenced_evidence_ids: set[str] = set()
    unknown_refs: list[str] = []
    for ref_index, object_ref in enumerate(obj.object_refs):
        referenced_object = objects_by_ref.get(object_ref.ref_key())
        if referenced_object is None:
            unknown_refs.append(_object_ref_label(object_ref))
            continue
        if (
            object_ref.object_type is not None
            and object_ref.object_type != referenced_object.object_type
        ):
            errors.append(
                f"{location}.object_refs[{ref_index}].object_type must match "
                "the referenced object"
            )
        resolved_ref_types.add(referenced_object.object_type)
        if isinstance(referenced_object, EvidenceQuoteObject):
            referenced_evidence_ids.add(referenced_object.payload.evidence_record_id)

    if unknown_refs:
        errors.append(
            f"{location}.object_refs references unknown objects: "
            + ", ".join(sorted(unknown_refs))
        )

    missing_ref_types = sorted(_EXPECTED_OBJECT_REF_TYPES - resolved_ref_types)
    if missing_ref_types:
        errors.append(
            f"{location}.object_refs must include supporting objects: "
            + ", ".join(missing_ref_types)
        )

    missing_quote_ids = sorted(set(object_evidence_ids) - referenced_evidence_ids)
    if missing_quote_ids:
        errors.append(
            f"{location}.object_refs must have EvidenceQuote objects for evidence IDs: "
            + ", ".join(missing_quote_ids)
        )

    for metadata_key in ("export_behavior", "write_behavior"):
        behavior = obj.metadata.get(metadata_key)
        if not isinstance(behavior, dict) or behavior.get("status") != "blocked":
            errors.append(f"{location}.metadata.{metadata_key}.status must be blocked")

    subject_state = obj.payload.phenotype_annotation_subject.resolution_state
    if obj.metadata.get("validation_state") != subject_state:
        errors.append(
            f"{location}.metadata.validation_state must match "
            "payload.phenotype_annotation_subject.resolution_state"
        )
    return errors


def _objects_by_type(
    objects: list[PhenotypeCuratableObject],
) -> dict[str, list[CuratableObjectEnvelope]]:
    grouped: dict[str, list[CuratableObjectEnvelope]] = {}
    for obj in objects:
        grouped.setdefault(obj.object_type, []).append(obj)
    return grouped


def _objects_by_ref(
    objects: list[PhenotypeCuratableObject],
) -> tuple[dict[tuple[str, str], CuratableObjectEnvelope], list[str]]:
    objects_by_ref: dict[tuple[str, str], CuratableObjectEnvelope] = {}
    errors: list[str] = []
    for index, obj in enumerate(objects):
        for ref_key in obj.ref_keys():
            if ref_key in objects_by_ref:
                errors.append(
                    f"curatable_objects[{index}] duplicates object reference "
                    f"{ref_key[0]}={ref_key[1]}"
                )
                continue
            objects_by_ref[ref_key] = obj
    return objects_by_ref, errors


def _object_ref_label(object_ref: Any) -> str:
    ref_kind, ref_value = object_ref.ref_key()
    if object_ref.object_type:
        return f"{ref_kind}={ref_value} ({object_ref.object_type})"
    return f"{ref_kind}={ref_value}"


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    return False


def _has_missing_strings(values: list[str]) -> bool:
    return any(_is_missing(value) for value in values)


__all__ = [
    "EvidenceQuoteObject",
    "EvidenceQuotePayload",
    "OntologyLookupHintPayload",
    "PhenotypeAnnotationObject",
    "PhenotypeAnnotationPayload",
    "PhenotypeResultEnvelope",
    "PhenotypeSubjectObject",
    "PhenotypeSubjectPayload",
    "PhenotypeTermObject",
    "PhenotypeTermPayload",
    "ReferenceObject",
    "ReferencePayload",
]
