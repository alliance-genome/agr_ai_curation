"""Shared domain-envelope output schema for extraction agents."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.schemas.domain_envelope import CuratableObjectEnvelope, SchemaRef

from .base import (
    AmbiguityRecord,
    EvidenceRecord,
    ExclusionRecord,
    ExtractionRunSummary,
    MentionCandidate,
)


LEGACY_SEMANTIC_LIST_FIELDS = frozenset(
    {
        "items",
        "annotations",
        "genes",
        "alleles",
        "diseases",
        "chemicals",
        "phenotypes",
    }
)


class ExtractionEnvelopeMetadata(BaseModel):
    """Non-semantic extraction metadata preserved alongside curatable objects."""

    model_config = ConfigDict(extra="forbid")

    raw_mentions: list[MentionCandidate] = Field(
        default_factory=list,
        description="Raw candidates harvested before retention/exclusion decisions",
    )
    evidence_records: list[EvidenceRecord] = Field(
        default_factory=list,
        description="Canonical verified evidence registry for this extraction run",
    )
    normalization_notes: list[str] = Field(
        default_factory=list,
        description="Normalization and interpretation notes that are not semantic objects",
    )
    exclusions: list[ExclusionRecord] = Field(
        default_factory=list,
        description="Candidates excluded by policy with explicit reason codes",
    )
    ambiguities: list[AmbiguityRecord] = Field(
        default_factory=list,
        description="Candidates requiring curator follow-up",
    )
    notes: list[str] = Field(
        default_factory=list,
        description="Curator-facing run notes that are not semantic objects",
    )
    repair_notes: list[str] = Field(
        default_factory=list,
        description="Instructions or observations useful for repair-mode reruns",
    )
    provenance: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Provider-owned provenance metadata. Legacy semantic list names may be "
            "stored here only as non-semantic provenance, never as top-level output lists."
        ),
    )


class DomainEnvelopeExtractionResult(BaseModel):
    """Shared structured output for first-pass domain-envelope extraction agents."""

    model_config = ConfigDict(extra="forbid")

    summary: Optional[str] = Field(
        default=None,
        description="Brief curator-facing summary of the extraction run",
    )
    curatable_objects: list[CuratableObjectEnvelope] = Field(
        default_factory=list,
        description=(
            "The only semantic object list for domain-envelope extraction outputs. "
            "Each object carries identity, role/type, schema/model refs, payload JSON, "
            "evidence refs, metadata refs, definition state, and repair hints."
        ),
    )
    metadata: ExtractionEnvelopeMetadata = Field(
        default_factory=ExtractionEnvelopeMetadata,
        description="Non-semantic run metadata for evidence, raw mentions, exclusions, ambiguities, and notes",
    )
    run_summary: ExtractionRunSummary = Field(
        default_factory=ExtractionRunSummary,
        description="Run-level extraction counters and warnings",
    )
    schema_ref: Optional[SchemaRef] = Field(
        default=None,
        description="Optional top-level schema ref for the shared extraction output contract",
    )
    repair_mode: bool = Field(
        default=False,
        description="True when the result repairs or amends a previous domain-envelope extraction",
    )

    @model_validator(mode="before")
    @classmethod
    def _reject_legacy_semantic_lists(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value

        legacy_keys = sorted(LEGACY_SEMANTIC_LIST_FIELDS.intersection(value))
        if legacy_keys:
            raise ValueError(
                "Domain-envelope extraction outputs must use curatable_objects[] as "
                "the only semantic object list; top-level legacy semantic lists are "
                f"not allowed: {', '.join(legacy_keys)}"
            )
        return value


__all__ = [
    "DomainEnvelopeExtractionResult",
    "ExtractionEnvelopeMetadata",
    "LEGACY_SEMANTIC_LIST_FIELDS",
]
