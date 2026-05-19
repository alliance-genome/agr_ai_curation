"""Gene extractor schema for Alliance gene mention evidence envelopes."""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from typing import Any, Literal

from src.lib.openai_agents.models import (
    GeneExtractionResultEnvelope as RuntimeGeneExtractionResultEnvelope,
)
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictStr,
    field_validator,
    model_validator,
)
from src.schemas.domain_envelope import CuratableObjectEnvelope, DefinitionState, SchemaRef
from src.schemas.models.base import MentionCandidate


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
ZFIN_TAXON_CURIE = "NCBITaxon:7955"


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


def _is_unsupported_zfin_drug_like_payload(payload: Mapping[str, Any]) -> bool:
    zfin_context = (
        payload.get("data_provider_hint") == "ZFIN"
        or payload.get("taxon_hint") == ZFIN_TAXON_CURIE
        or payload.get("proposed_taxon") == ZFIN_TAXON_CURIE
        or (_optional_text(payload.get("species")) or "").lower()
        in {"danio rerio", "zebrafish"}
    )
    if not zfin_context:
        return False

    proposed_symbol = _optional_text(payload.get("proposed_gene_symbol"))
    has_gene_identity_hint = bool(
        _optional_text(payload.get("proposed_primary_external_id"))
        or (proposed_symbol and proposed_symbol == proposed_symbol.lower())
    )
    if has_gene_identity_hint:
        return False

    mention = _optional_text(payload.get("mention")) or ""
    return bool(re.search(r"[A-Z]", mention) and re.search(r"\d", mention))


class GeneMentionEvidencePayload(BaseModel):
    """Payload for one verified gene mention evidence object."""

    model_config = ConfigDict(extra="forbid")

    mention: StrictStr = Field(description="Gene mention exactly as written in the paper")
    species: StrictStr | None = Field(
        default=None,
        description="Curator-facing species label when available from extraction context",
    )
    taxon_hint: StrictStr | None = Field(
        default=None,
        description="Paper-backed NCBI Taxon CURIE hint for validator input",
    )
    data_provider_hint: StrictStr | None = Field(
        default=None,
        description="Paper-backed Alliance provider hint, such as FB, WB, MGI, HGNC, ZFIN, RGD, or SGD",
    )
    proposed_primary_external_id: StrictStr | None = Field(
        default=None,
        description="Extractor-proposed Alliance Gene identifier for validator confirmation",
    )
    proposed_gene_symbol: StrictStr | None = Field(
        default=None,
        description="Extractor-proposed current gene symbol for validator confirmation",
    )
    proposed_taxon: StrictStr | None = Field(
        default=None,
        description="Extractor-proposed NCBI Taxon CURIE for validator confirmation",
    )
    identity_resolution_notes: list[StrictStr] = Field(
        default_factory=list,
        description="Auditable notes describing extractor-side species or identity hints",
    )
    confidence: Literal["high", "medium", "low"] = Field(
        description="Extractor confidence in the gene mention, species context, and evidence match"
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
        "evidence_record_id",
        "verified_quote",
        "section",
        "chunk_id",
        mode="before",
    )
    @classmethod
    def _validate_required_strings(cls, value: object, info) -> object:
        return _strip_required_string(value, info.field_name)

    @field_validator(
        "species",
        "taxon_hint",
        "data_provider_hint",
        "proposed_primary_external_id",
        "proposed_gene_symbol",
        "proposed_taxon",
        "subsection",
        "figure_reference",
        mode="before",
    )
    @classmethod
    def _validate_optional_strings(cls, value: object) -> object:
        return _strip_optional_string(value)

    @field_validator("identity_resolution_notes")
    @classmethod
    def _validate_identity_resolution_notes(cls, value: list[StrictStr]) -> list[StrictStr]:
        normalized_notes: list[str] = []
        for item in value:
            normalized = str(item).strip()
            if not normalized:
                raise ValueError("identity_resolution_notes must not contain empty values")
            normalized_notes.append(normalized)
        return normalized_notes

    @field_validator("page", mode="before")
    @classmethod
    def _validate_page(cls, value: object) -> object:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("page must be an integer")
        return value

    @model_validator(mode="after")
    def _reject_unresolved_zfin_drug_like_mentions(self) -> "GeneMentionEvidencePayload":
        zfin_context = (
            self.data_provider_hint == "ZFIN"
            or self.taxon_hint == ZFIN_TAXON_CURIE
            or self.proposed_taxon == ZFIN_TAXON_CURIE
            or (self.species or "").strip().lower() in {"danio rerio", "zebrafish"}
        )
        if not zfin_context:
            return self

        proposed_symbol = (self.proposed_gene_symbol or "").strip()
        has_gene_identity_hint = bool(
            self.proposed_primary_external_id
            or (proposed_symbol and proposed_symbol == proposed_symbol.lower())
        )
        if has_gene_identity_hint:
            return self

        mention = self.mention.strip()
        if re.search(r"[A-Z]", mention) and re.search(r"\d", mention):
            raise ValueError(
                "ZFIN gene_mention_evidence payloads must not retain uppercase "
                "drug-like compound codes as genes unless a lowercase proposed_gene_symbol "
                "or proposed_primary_external_id is present"
            )
        return self


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

    @model_validator(mode="before")
    @classmethod
    def _exclude_unsupported_gene_like_compounds(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value

        normalized = copy.deepcopy(dict(value))
        curatable_objects = normalized.get("curatable_objects")
        if not isinstance(curatable_objects, list):
            return normalized

        kept_objects: list[object] = []
        exclusions: list[dict[str, Any]] = []
        for obj in curatable_objects:
            if not isinstance(obj, Mapping):
                kept_objects.append(obj)
                continue
            payload = obj.get("payload")
            if (
                obj.get("object_type") == GENE_MENTION_EVIDENCE_OBJECT_TYPE
                and isinstance(payload, Mapping)
                and _is_unsupported_zfin_drug_like_payload(payload)
            ):
                evidence_record_id = _optional_text(payload.get("evidence_record_id"))
                exclusions.append(
                    {
                        "mention": _optional_text(payload.get("mention"))
                        or "unsupported ZFIN-like code",
                        "reason_code": "unsupported_entity_type",
                        "evidence_record_ids": (
                            [evidence_record_id] if evidence_record_id else []
                        ),
                        "details": (
                            "Dropped uppercase/digit ZFIN-context mention before "
                            "schema validation because no lowercase gene symbol or "
                            "primary external ID was present."
                        ),
                    }
                )
                continue
            kept_objects.append(obj)

        if exclusions:
            normalized["curatable_objects"] = kept_objects
            metadata = normalized.get("metadata")
            if not isinstance(metadata, dict):
                metadata = {}
                normalized["metadata"] = metadata
            existing_exclusions = metadata.get("exclusions")
            if not isinstance(existing_exclusions, list):
                existing_exclusions = []
            metadata["exclusions"] = [*existing_exclusions, *exclusions]

        return normalized

    @model_validator(mode="after")
    def _validate_gene_evidence_metadata(self) -> "GeneExtractionResultEnvelope":
        if self.curatable_objects and not self.metadata.raw_mentions:
            self.metadata.raw_mentions = [
                MentionCandidate(
                    mention=obj.payload.mention,
                    entity_type="gene",
                    evidence_record_ids=list(
                        obj.evidence_record_ids or [obj.payload.evidence_record_id]
                    ),
                )
                for obj in self.curatable_objects
            ]

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

        return self


__all__ = [
    "GeneExtractionResultEnvelope",
    "GeneMentionEvidenceObjectEnvelope",
    "GeneMentionEvidencePayload",
]
