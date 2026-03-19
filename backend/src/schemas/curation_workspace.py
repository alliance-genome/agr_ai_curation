"""Reusable curation workspace contract types.

This module intentionally defines only shared data contracts for evidence
anchoring, field validation, and submission planning. Resolver behavior and
target-system implementations belong to later tickets.
"""

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.models.chunk import ChunkBoundingBox


def _strip_optional_string(value: Optional[str]) -> Optional[str]:
    """Normalize optional string values while rejecting blank strings."""
    if value is None:
        return None

    stripped = value.strip()
    if not stripped:
        raise ValueError("Value must not be empty or whitespace only")
    return stripped


def _normalize_string_list(values: List[str]) -> List[str]:
    """Trim, validate, and de-duplicate a string list while preserving order."""
    normalized: List[str] = []
    seen: set[str] = set()

    for raw_value in values:
        stripped = raw_value.strip()
        if not stripped:
            raise ValueError("List values must not be empty or whitespace only")
        if stripped not in seen:
            normalized.append(stripped)
            seen.add(stripped)

    return normalized


# =============================================================================
# Evidence anchor contracts
# =============================================================================


class EvidenceAnchorKind(str, Enum):
    """Primary locator type for an evidence anchor."""

    CHUNK = "chunk"
    DOC_ITEM = "doc_item"
    BBOX = "bbox"
    SENTENCE = "sentence"
    SNIPPET = "snippet"


class EvidenceLocatorQuality(str, Enum):
    """How precisely the evidence was localized in the source document."""

    EXACT = "exact"
    APPROXIMATE = "approximate"
    DEGRADED = "degraded"
    UNKNOWN = "unknown"


class EvidenceDecisionSupport(str, Enum):
    """How an evidence anchor relates to the proposed curation decision."""

    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    CONTEXT_ONLY = "context_only"


class EvidenceAnchor(BaseModel):
    """Reusable evidence reference shared across review and submission phases."""

    model_config = ConfigDict(extra="forbid")

    anchor_kind: EvidenceAnchorKind = Field(
        ...,
        description="Primary locator strategy for the anchor",
    )
    locator_quality: EvidenceLocatorQuality = Field(
        ...,
        description="Quality of the evidence location",
    )
    supports_decision: EvidenceDecisionSupport = Field(
        ...,
        description="Whether the anchor supports, contradicts, or only contextualizes a decision",
    )
    document_id: Optional[str] = Field(
        default=None,
        description="Workspace document identifier when the evidence is PDF-backed",
    )
    page_number: Optional[int] = Field(
        default=None,
        ge=1,
        description="1-indexed page number when known",
    )
    chunk_id: Optional[str] = Field(
        default=None,
        description="Chunk identifier for chunk-backed anchors",
    )
    doc_item_ids: List[str] = Field(
        default_factory=list,
        description="PDFX doc-item or element identifiers used to localize the evidence",
    )
    bbox: Optional[ChunkBoundingBox] = Field(
        default=None,
        description="Page-level bounding box when direct PDF localization is available",
    )
    snippet: Optional[str] = Field(
        default=None,
        description="Short evidence excerpt for hover cards and previews",
    )
    sentence: Optional[str] = Field(
        default=None,
        description="Sentence-level evidence text when available",
    )

    @field_validator("document_id", "chunk_id", "snippet", "sentence")
    @classmethod
    def validate_optional_strings(cls, value: Optional[str]) -> Optional[str]:
        """Reject blank optional string values."""
        return _strip_optional_string(value)

    @field_validator("doc_item_ids")
    @classmethod
    def validate_doc_item_ids(cls, value: List[str]) -> List[str]:
        """Reject blank doc-item identifiers and collapse duplicates."""
        return _normalize_string_list(value)

    @model_validator(mode="after")
    def validate_locator_fields(self) -> "EvidenceAnchor":
        """Ensure the primary anchor kind has the required supporting fields."""
        if not any([self.chunk_id, self.doc_item_ids, self.bbox, self.snippet, self.sentence]):
            raise ValueError(
                "EvidenceAnchor requires at least one locator field: chunk_id, doc_item_ids, bbox, snippet, or sentence"
            )

        if self.bbox is not None and self.page_number is None:
            raise ValueError("page_number is required when bbox is provided")

        required_by_kind = {
            EvidenceAnchorKind.CHUNK: bool(self.chunk_id),
            EvidenceAnchorKind.DOC_ITEM: bool(self.doc_item_ids),
            EvidenceAnchorKind.BBOX: self.bbox is not None,
            EvidenceAnchorKind.SENTENCE: self.sentence is not None,
            EvidenceAnchorKind.SNIPPET: self.snippet is not None,
        }
        if not required_by_kind[self.anchor_kind]:
            raise ValueError(
                f"anchor_kind '{self.anchor_kind.value}' requires its matching locator field to be populated"
            )

        return self


# =============================================================================
# Field validation contracts
# =============================================================================


class FieldValidationStatus(str, Enum):
    """Shared validation lifecycle states for curated field values."""

    PENDING = "pending"
    VALID = "valid"
    AMBIGUOUS = "ambiguous"
    INVALID = "invalid"


class FieldValidationCandidateMatch(BaseModel):
    """Candidate returned by a resolver during field validation."""

    model_config = ConfigDict(extra="forbid")

    matched_value: str = Field(
        ...,
        description="Candidate value surfaced by the resolver",
    )
    candidate_id: Optional[str] = Field(
        default=None,
        description="Stable external identifier for the candidate when available",
    )
    display_label: Optional[str] = Field(
        default=None,
        description="Human-readable display label for the candidate",
    )
    confidence: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Optional resolver-reported confidence score",
    )
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Resolver-specific details to preserve alongside the candidate",
    )

    @field_validator("matched_value", "candidate_id", "display_label")
    @classmethod
    def validate_candidate_strings(cls, value: Optional[str]) -> Optional[str]:
        """Reject blank candidate fields while allowing omitted optional values."""
        return _strip_optional_string(value)


class FieldValidationResult(BaseModel):
    """Validation outcome for a single curated field."""

    model_config = ConfigDict(extra="forbid")

    status: FieldValidationStatus = Field(
        ...,
        description="Current validation state for the field",
    )
    resolver: Optional[str] = Field(
        default=None,
        description="Resolver identifier responsible for the latest validation attempt",
    )
    candidate_matches: List[FieldValidationCandidateMatch] = Field(
        default_factory=list,
        description="Resolver-returned candidates that can be shown to a curator",
    )
    warnings: List[str] = Field(
        default_factory=list,
        description="Non-blocking caveats emitted while validating the field",
    )

    @field_validator("resolver")
    @classmethod
    def validate_resolver(cls, value: Optional[str]) -> Optional[str]:
        """Reject blank resolver identifiers."""
        return _strip_optional_string(value)

    @field_validator("warnings")
    @classmethod
    def validate_warnings(cls, value: List[str]) -> List[str]:
        """Normalize warnings while preserving their original order."""
        return _normalize_string_list(value)

    @model_validator(mode="after")
    def validate_status_requirements(self) -> "FieldValidationResult":
        """Enforce the minimal metadata required for resolved statuses."""
        if self.status != FieldValidationStatus.PENDING and self.resolver is None:
            raise ValueError("resolver is required once validation has run")

        if self.status == FieldValidationStatus.AMBIGUOUS and not self.candidate_matches:
            raise ValueError("ambiguous validation results must include candidate_matches")

        return self


# =============================================================================
# Submission contracts
# =============================================================================


class SubmissionMode(str, Enum):
    """Submission execution mode."""

    PREVIEW = "preview"
    SUBMIT = "submit"


class SubmissionTargetSystem(str, Enum):
    """Supported target systems for curation submission payloads."""

    ALLIANCE_CURATION_API = "alliance_curation_api"
    ABC_API = "abc_api"


class SubmissionDomainAdapterContract(BaseModel):
    """Domain-specific adapter payload handed to a target-system integration."""

    model_config = ConfigDict(extra="forbid")

    domain: str = Field(
        ...,
        min_length=1,
        description="Domain key that owns the adapter payload",
    )
    adapter_name: str = Field(
        ...,
        min_length=1,
        description="Canonical adapter identifier for the domain payload",
    )
    adapter_version: Optional[str] = Field(
        default=None,
        description="Optional adapter contract version",
    )
    payload: Dict[str, Any] = Field(
        default_factory=dict,
        description="Domain-specific payload passed through to the adapter implementation",
    )

    @field_validator("domain", "adapter_name", "adapter_version")
    @classmethod
    def validate_adapter_strings(cls, value: Optional[str]) -> Optional[str]:
        """Reject blank adapter identifiers."""
        return _strip_optional_string(value)


class SubmissionPayload(BaseModel):
    """Submission request contract shared by preview and submit workflows."""

    model_config = ConfigDict(extra="forbid")

    mode: SubmissionMode = Field(
        ...,
        description="Whether the payload is being previewed or submitted",
    )
    target_system: SubmissionTargetSystem = Field(
        ...,
        description="External system that will receive the adapted payload",
    )
    domain_adapter: SubmissionDomainAdapterContract = Field(
        ...,
        description="Domain-specific adapter payload for the chosen target system",
    )

    @model_validator(mode="after")
    def validate_submit_payload(self) -> "SubmissionPayload":
        """Require a concrete adapter payload for live submissions."""
        if self.mode == SubmissionMode.SUBMIT and not self.domain_adapter.payload:
            raise ValueError("domain_adapter.payload must be populated when mode='submit'")
        return self

