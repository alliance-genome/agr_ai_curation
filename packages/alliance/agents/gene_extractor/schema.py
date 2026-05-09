"""Gene extractor schema for Alliance gene mention evidence envelopes."""

from __future__ import annotations

from typing import Literal

from src.lib.openai_agents.models import (
    GeneExtractionResultEnvelope as RuntimeGeneExtractionResultEnvelope,
)
from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator, model_validator
from src.schemas.domain_envelope import CuratableObjectEnvelope, DefinitionState, SchemaRef


# Keep these values synchronized with the Alliance gene domain-pack constants.
# Agent schema discovery loads this file directly from the agent bundle, so this
# module cannot assume the package python/src tree is importable in every runtime.
GENE_MENTION_EVIDENCE_OBJECT_TYPE = "gene_mention_evidence"
GENE_MENTION_EVIDENCE_MODEL_REF = "GeneMentionEvidencePayload"
GENE_LINKML_SCHEMA_ID = "alliance.linkml.Gene"
GENE_LINKML_SCHEMA_PROVIDER = "alliance_linkml"
GENE_LINKML_SCHEMA_NAME = "Gene"
GENE_LINKML_COMMIT = "1b11d0888f19eba4ca72022200bb7d96b30d4a52"
GENE_LINKML_SCHEMA_URI = (
    "https://github.com/alliance-genome/agr_curation_schema/blob/"
    f"{GENE_LINKML_COMMIT}/model/schema/gene.yaml"
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


class GeneMentionEvidencePayload(BaseModel):
    """Payload for one verified gene mention evidence object."""

    model_config = ConfigDict(extra="forbid")

    mention: StrictStr = Field(description="Gene mention exactly as written in the paper")
    primary_external_id: StrictStr = Field(
        description="Alliance Gene primary external identifier resolved by validation tooling"
    )
    gene_symbol: StrictStr = Field(description="Current accepted symbol for the resolved gene")
    taxon: StrictStr = Field(description="NCBI Taxon CURIE for the resolved gene")
    species: StrictStr | None = Field(
        default=None,
        description="Curator-facing species label when available from extraction context",
    )
    confidence: Literal["high", "medium", "low"] = Field(
        description="Extractor confidence in the normalized gene reference and evidence match"
    )
    evidence_record_id: StrictStr = Field(
        description="Stable ID returned by the paper evidence verification tool"
    )
    verified_quote: StrictStr = Field(description="Verbatim paper text verified by record_evidence")
    page: int = Field(ge=1, description="1-based page containing the verified quote")
    section: StrictStr = Field(description="Document section containing the verified quote")
    chunk_id: StrictStr = Field(description="Document chunk identifier used to verify the quote")
    subsection: StrictStr | None = Field(default=None, description="Subsection heading, if available")
    figure_reference: StrictStr | None = Field(
        default=None,
        description="Figure or table locator literal, if available",
    )

    @field_validator(
        "mention",
        "primary_external_id",
        "gene_symbol",
        "taxon",
        "evidence_record_id",
        "verified_quote",
        "section",
        "chunk_id",
        mode="before",
    )
    @classmethod
    def _validate_required_strings(cls, value: object, info) -> object:
        return _strip_required_string(value, info.field_name)

    @field_validator("species", "subsection", "figure_reference", mode="before")
    @classmethod
    def _validate_optional_strings(cls, value: object) -> object:
        return _strip_optional_string(value)

    @field_validator("page", mode="before")
    @classmethod
    def _validate_page(cls, value: object) -> object:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("page must be an integer")
        return value


class GeneMentionEvidenceObjectEnvelope(CuratableObjectEnvelope):
    """One gene_mention_evidence object in the gene extractor output."""

    object_type: Literal["gene_mention_evidence"] = GENE_MENTION_EVIDENCE_OBJECT_TYPE
    object_role: Literal["validated_reference"] = "validated_reference"
    payload: GeneMentionEvidencePayload
    schema_ref: SchemaRef = Field(
        description="Alliance LinkML Gene schema ref for this validated-reference payload"
    )
    model_ref: Literal["GeneMentionEvidencePayload"] = GENE_MENTION_EVIDENCE_MODEL_REF
    definition_state: Literal[DefinitionState.IN_DEVELOPMENT] = DefinitionState.IN_DEVELOPMENT
    definition_notes: list[StrictStr] = Field(
        min_length=1,
        description="Notes explaining the non-exporting, in-development object contract",
    )

    @model_validator(mode="after")
    def _validate_evidence_ref_alignment(self) -> "GeneMentionEvidenceObjectEnvelope":
        expected_ids = [self.payload.evidence_record_id]
        if self.evidence_record_ids != expected_ids:
            raise ValueError(
                "gene_mention_evidence objects must reference exactly their payload.evidence_record_id"
            )
        if self.schema_ref.schema_id != GENE_LINKML_SCHEMA_ID:
            raise ValueError("gene_mention_evidence schema_ref must be alliance.linkml.Gene")
        if self.schema_ref.provider != GENE_LINKML_SCHEMA_PROVIDER:
            raise ValueError("gene_mention_evidence schema_ref provider must be alliance_linkml")
        if self.schema_ref.name != GENE_LINKML_SCHEMA_NAME:
            raise ValueError("gene_mention_evidence schema_ref name must be Gene")
        if self.schema_ref.version != GENE_LINKML_COMMIT:
            raise ValueError(
                "gene_mention_evidence schema_ref version must match the pinned LinkML commit"
            )
        if self.schema_ref.uri is not None and self.schema_ref.uri != GENE_LINKML_SCHEMA_URI:
            raise ValueError("gene_mention_evidence schema_ref uri must target the pinned Gene schema")
        return self


class GeneExtractionResultEnvelope(RuntimeGeneExtractionResultEnvelope):
    """Config-discovered schema for gene_mention_evidence extractor output."""

    __envelope_class__ = True

    curatable_objects: list[GeneMentionEvidenceObjectEnvelope] = Field(
        default_factory=list,
        description=(
            "The only semantic object list for new gene extractor runs. Each object "
            "is one verified gene mention/evidence reference, not a paper-gene write target."
        ),
    )

    @model_validator(mode="after")
    def _validate_gene_evidence_metadata(self) -> "GeneExtractionResultEnvelope":
        if self.curatable_objects and not self.metadata.raw_mentions:
            raise ValueError(
                "gene extractor output must preserve harvested mentions in metadata.raw_mentions[]"
            )

        evidence_by_id = {
            record.evidence_record_id: record
            for record in self.metadata.evidence_records
            if record.evidence_record_id is not None
        }
        for obj in self.curatable_objects:
            evidence_id = obj.payload.evidence_record_id
            evidence_record = evidence_by_id.get(evidence_id)
            if evidence_record is None:
                raise ValueError(
                    "gene_mention_evidence payload.evidence_record_id must resolve in "
                    "metadata.evidence_records[]"
                )

            required_evidence_fields = {
                "verified_quote": evidence_record.verified_quote,
                "page": evidence_record.page,
                "section": evidence_record.section,
                "chunk_id": evidence_record.chunk_id,
            }
            missing_evidence_fields = sorted(
                field_name
                for field_name, value in required_evidence_fields.items()
                if value is None
            )
            if missing_evidence_fields:
                raise ValueError(
                    "metadata.evidence_records[] entries referenced by gene_mention_evidence "
                    "must include verified_quote, page, section, and chunk_id"
                )

            if evidence_record.verified_quote != obj.payload.verified_quote:
                raise ValueError(
                    "gene_mention_evidence payload.verified_quote must match metadata evidence"
                )
            if evidence_record.page != obj.payload.page:
                raise ValueError("gene_mention_evidence payload.page must match metadata evidence")
            if evidence_record.section != obj.payload.section:
                raise ValueError("gene_mention_evidence payload.section must match metadata evidence")
            if evidence_record.chunk_id != obj.payload.chunk_id:
                raise ValueError("gene_mention_evidence payload.chunk_id must match metadata evidence")

        if self.repair_mode:
            has_repair_context = bool(self.metadata.repair_notes) or any(
                obj.repair_hints for obj in self.curatable_objects
            )
            if not has_repair_context:
                raise ValueError(
                    "repair-mode gene extractor output must include metadata.repair_notes[] "
                    "or curatable_objects[].repair_hints[]"
                )

        return self


__all__ = [
    "GeneExtractionResultEnvelope",
    "GeneMentionEvidenceObjectEnvelope",
    "GeneMentionEvidencePayload",
]
