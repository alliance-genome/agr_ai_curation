"""Convert tool-verified gene extraction fixtures into domain envelopes."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any, Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator, model_validator

from src.schemas.domain_envelope import (
    CuratableObjectEnvelope,
    DefinitionState,
    DomainEnvelope,
    FieldRef,
    HistoryActorType,
    HistoryEvent,
    HistoryEventKind,
    ObjectRef,
    SchemaRef,
    ValidationFinding,
    ValidationFindingSeverity,
    ValidationFindingStatus,
)

from ..schema_refs import (
    ALLIANCE_LINKML_COMMIT,
    ALLIANCE_LINKML_PROVIDER_KEY,
    OBJECT_ROLE_METADATA_KEY,
    PROVIDER_REFS_METADATA_KEY,
)
from .constants import (
    GENE_DOMAIN_PACK_CONVERTER_ID,
    GENE_DOMAIN_PACK_ID,
    GENE_DOMAIN_PACK_VERSION,
    GENE_LINKML_SCHEMA_ID,
    GENE_LINKML_SCHEMA_NAME,
    GENE_LINKML_SCHEMA_URI,
    GENE_MENTION_EVIDENCE_DEFINITION_NOTES,
    GENE_MENTION_EVIDENCE_OBJECT_TYPE,
    GENE_REFERENCE_TOOL_METHOD,
    GENE_REFERENCE_TOOL_NAME,
    GENE_REFERENCE_VALIDATOR_BINDING_ID,
)
from .export import GENE_VALIDATED_REFERENCE_EXPORT_TARGET_KEY


_GENE_SOURCE_FILE = "model/schema/gene.yaml"
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


class ToolVerifiedGeneEvidenceRecord(BaseModel):
    """One quote verified by the document evidence tool."""

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


class ToolVerifiedGeneMention(BaseModel):
    """One normalized gene mention retained by the extractor."""

    model_config = ConfigDict(extra="forbid")

    mention: StrictStr
    primary_external_id: StrictStr
    gene_symbol: StrictStr
    taxon: StrictStr
    species: StrictStr | None = None
    confidence: Literal["high", "medium", "low"]
    evidence_record_ids: list[StrictStr] = Field(min_length=1)
    identity_resolution_notes: list[StrictStr] = Field(default_factory=list)

    @field_validator("mention", "primary_external_id", "gene_symbol", "taxon", mode="before")
    @classmethod
    def _validate_required_strings(cls, value: object, info) -> object:
        return _strip_required_string(value, info.field_name)

    @field_validator("species", mode="before")
    @classmethod
    def _validate_optional_strings(cls, value: object) -> object:
        return _strip_optional_string(value)

    @field_validator("evidence_record_ids")
    @classmethod
    def _validate_evidence_ids(cls, value: list[StrictStr]) -> list[StrictStr]:
        normalized: list[str] = []
        seen: set[str] = set()
        duplicates: list[str] = []
        for raw_item in value:
            item = str(raw_item).strip()
            if not item:
                raise ValueError("evidence_record_ids must not contain empty values")
            if item in seen and item not in duplicates:
                duplicates.append(item)
            seen.add(item)
            normalized.append(item)
        if duplicates:
            raise ValueError(
                "evidence_record_ids contains duplicate entries: "
                + ", ".join(sorted(duplicates))
            )
        return normalized

    @field_validator("identity_resolution_notes")
    @classmethod
    def _validate_identity_resolution_notes(
        cls,
        value: list[StrictStr],
    ) -> list[StrictStr]:
        normalized_notes: list[str] = []
        for item in value:
            normalized = str(item).strip()
            if not normalized:
                raise ValueError(
                    "identity_resolution_notes must not contain empty values"
                )
            normalized_notes.append(normalized)
        return normalized_notes


class ToolVerifiedGeneOutput(BaseModel):
    """Canonical fixture input produced after gene lookup and evidence verification."""

    model_config = ConfigDict(extra="forbid")

    envelope_id: StrictStr
    document_id: StrictStr
    produced_by: StrictStr
    produced_at: datetime
    gene_mentions: list[ToolVerifiedGeneMention] = Field(min_length=1)
    evidence_records: list[ToolVerifiedGeneEvidenceRecord] = Field(min_length=1)
    normalization_notes: list[StrictStr] = Field(default_factory=list)

    @field_validator("envelope_id", "document_id", "produced_by", mode="before")
    @classmethod
    def _validate_required_strings(cls, value: object, info) -> object:
        return _strip_required_string(value, info.field_name)

    @field_validator("normalization_notes")
    @classmethod
    def _validate_normalization_notes(cls, value: list[StrictStr]) -> list[StrictStr]:
        normalized_notes: list[str] = []
        for item in value:
            normalized = str(item).strip()
            if not normalized:
                raise ValueError("normalization_notes must not contain empty values")
            normalized_notes.append(normalized)
        return normalized_notes

    @model_validator(mode="after")
    def _validate_evidence_links(self) -> "ToolVerifiedGeneOutput":
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
                for gene in self.gene_mentions
                for evidence_id in gene.evidence_record_ids
                if evidence_id not in evidence_id_set
            }
        )
        if missing_links:
            raise ValueError(
                "gene_mentions references unknown evidence_record_ids: "
                + ", ".join(missing_links)
            )
        return self


def _gene_schema_ref() -> SchemaRef:
    return SchemaRef(
        schema_id=GENE_LINKML_SCHEMA_ID,
        provider=ALLIANCE_LINKML_PROVIDER_KEY,
        name=GENE_LINKML_SCHEMA_NAME,
        version=ALLIANCE_LINKML_COMMIT,
        uri=GENE_LINKML_SCHEMA_URI,
        metadata={
            PROVIDER_REFS_METADATA_KEY: {
                ALLIANCE_LINKML_PROVIDER_KEY: {
                    "schema_ref": "alliance.linkml",
                    "commit": ALLIANCE_LINKML_COMMIT,
                    "source_file": _GENE_SOURCE_FILE,
                    "class": "Gene",
                }
            }
        },
    )


def _object_metadata() -> dict[str, Any]:
    return {
        OBJECT_ROLE_METADATA_KEY: "validated_reference",
        "evidence_role": GENE_MENTION_EVIDENCE_OBJECT_TYPE,
        "validator_binding_id": GENE_REFERENCE_VALIDATOR_BINDING_ID,
        "blocking_validation": False,
        "export_behavior": {
            "status": "ready",
            "mode": "validated_reference_evidence",
            "target_key": GENE_VALIDATED_REFERENCE_EXPORT_TARGET_KEY,
            "exportable": True,
            "mutates_base_gene": False,
            "creates_paper_gene_association": False,
        },
        "write_behavior": "envelope_only",
        "provider_refs": {
            ALLIANCE_LINKML_PROVIDER_KEY: {
                "schema_ref": "alliance.linkml",
                "commit": ALLIANCE_LINKML_COMMIT,
                "source_file": _GENE_SOURCE_FILE,
                "class": "Gene",
            }
        },
    }


def _payload_for_gene_evidence(
    gene: ToolVerifiedGeneMention,
    evidence: ToolVerifiedGeneEvidenceRecord,
    *,
    normalization_notes: Sequence[str] = (),
) -> dict[str, Any]:
    identity_resolution_notes = (
        list(gene.identity_resolution_notes)
        or [str(note).strip() for note in normalization_notes if str(note).strip()]
    )
    payload: dict[str, Any] = {
        "mention": gene.mention,
        "primary_external_id": gene.primary_external_id,
        "gene_symbol": gene.gene_symbol,
        "taxon": gene.taxon,
        "confidence": gene.confidence,
        "evidence_record_id": evidence.evidence_record_id,
        "verified_quote": evidence.verified_quote,
        "page": evidence.page,
        "section": evidence.section,
        "chunk_id": evidence.chunk_id,
    }
    if identity_resolution_notes:
        payload["identity_resolution_notes"] = identity_resolution_notes
    if gene.species is not None:
        payload["species"] = gene.species
    if evidence.subsection is not None:
        payload["subsection"] = evidence.subsection
    if evidence.figure_reference is not None:
        payload["figure_reference"] = evidence.figure_reference
    return payload


def _validation_finding(pending_ref_id: str) -> ValidationFinding:
    return ValidationFinding(
        severity=ValidationFindingSeverity.INFO,
        status=ValidationFindingStatus.RESOLVED,
        code="alliance.gene_reference.tool_verified",
        message=f"Gene reference resolved by {GENE_REFERENCE_TOOL_NAME} before envelope conversion.",
        field_ref=FieldRef(
            object_ref=ObjectRef(
                pending_ref_id=pending_ref_id,
                object_type=GENE_MENTION_EVIDENCE_OBJECT_TYPE,
            ),
            field_path="primary_external_id",
        ),
        details={
            "validator_binding_id": GENE_REFERENCE_VALIDATOR_BINDING_ID,
            "source_tool": GENE_REFERENCE_TOOL_NAME,
            "source_method": GENE_REFERENCE_TOOL_METHOD,
            "blocking": False,
            "grounded_slots": {
                "primary_external_id": {
                    "source_file": _CORE_SOURCE_FILE,
                    "slot": "primary_external_id",
                    "range": "string",
                },
                "gene_symbol": {
                    "source_file": _GENE_SOURCE_FILE,
                    "slot": "gene_symbol",
                    "range": "GeneSymbolSlotAnnotation",
                },
                "taxon": {
                    "source_file": _CORE_SOURCE_FILE,
                    "slot": "taxon",
                    "range": "NCBITaxonTerm",
                },
            },
        },
    )


def tool_verified_gene_output_to_pending_envelope(
    payload: Mapping[str, Any] | ToolVerifiedGeneOutput,
) -> DomainEnvelope:
    """Build a pending-ref envelope from canonical tool-verified gene output."""

    source = (
        payload
        if isinstance(payload, ToolVerifiedGeneOutput)
        else ToolVerifiedGeneOutput.model_validate(payload)
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
            actor_id=GENE_DOMAIN_PACK_CONVERTER_ID,
            message="Converted tool-verified gene extraction output to pending domain envelope.",
        )
    ]

    object_index = 1
    for gene in source.gene_mentions:
        for evidence_id in gene.evidence_record_ids:
            evidence = evidence_by_id[evidence_id]
            pending_ref_id = f"gene-mention-evidence-{object_index}"
            object_ref = ObjectRef(
                pending_ref_id=pending_ref_id,
                object_type=GENE_MENTION_EVIDENCE_OBJECT_TYPE,
            )
            objects.append(
                CuratableObjectEnvelope(
                    object_type=GENE_MENTION_EVIDENCE_OBJECT_TYPE,
                    pending_ref_id=pending_ref_id,
                    schema_ref=_gene_schema_ref(),
                    definition_state=DefinitionState.STABLE,
                    definition_notes=list(GENE_MENTION_EVIDENCE_DEFINITION_NOTES),
                    payload=_payload_for_gene_evidence(
                        gene,
                        evidence,
                        normalization_notes=source.normalization_notes,
                    ),
                    metadata=_object_metadata(),
                )
            )
            validation_findings.append(_validation_finding(pending_ref_id))
            history.append(
                HistoryEvent(
                    event_type=HistoryEventKind.OBJECT_EXTRACTED,
                    timestamp=source.produced_at,
                    actor_type=HistoryActorType.SYSTEM,
                    actor_id=GENE_DOMAIN_PACK_CONVERTER_ID,
                    message="Added non-blocking gene mention evidence.",
                    object_ref=object_ref,
                    details={
                        "evidence_record_id": evidence.evidence_record_id,
                        "validator_binding_id": GENE_REFERENCE_VALIDATOR_BINDING_ID,
                    },
                )
            )
            object_index += 1

    return DomainEnvelope(
        envelope_id=source.envelope_id,
        domain_pack_id=GENE_DOMAIN_PACK_ID,
        domain_pack_version=GENE_DOMAIN_PACK_VERSION,
        schema_ref=_gene_schema_ref(),
        objects=objects,
        validation_findings=validation_findings,
        history=history,
        metadata={
            "source_document_id": source.document_id,
            "source_agent": source.produced_by,
            "conversion": "tool_verified_gene_output_to_pending_envelope",
            "non_blocking_validation": True,
            "normalization_notes": source.normalization_notes,
        },
    )


__all__ = [
    "ToolVerifiedGeneEvidenceRecord",
    "ToolVerifiedGeneMention",
    "ToolVerifiedGeneOutput",
    "tool_verified_gene_output_to_pending_envelope",
]
