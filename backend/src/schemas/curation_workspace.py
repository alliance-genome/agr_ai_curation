"""Shared curation workspace contracts for evidence, validation, and submission."""

from __future__ import annotations

from enum import Enum
from typing import Any, Mapping, Optional, Protocol, Sequence, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EvidenceAnchorKind(str, Enum):
    """Primary locator shape carried by an evidence anchor."""

    SNIPPET = "snippet"
    SENTENCE = "sentence"
    SECTION = "section"
    FIGURE = "figure"
    TABLE = "table"
    PAGE = "page"
    DOCUMENT = "document"


class EvidenceLocatorQuality(str, Enum):
    """Durability and precision of an evidence locator."""

    EXACT_QUOTE = "exact_quote"
    NORMALIZED_QUOTE = "normalized_quote"
    SECTION_ONLY = "section_only"
    PAGE_ONLY = "page_only"
    DOCUMENT_ONLY = "document_only"
    UNRESOLVED = "unresolved"


class EvidenceSupportsDecision(str, Enum):
    """How an anchor relates to the curator-facing decision it is attached to."""

    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    NEUTRAL = "neutral"


class EvidenceAnchor(BaseModel):
    """Reusable evidence anchor contract shared across resolver, UI, and submission flows."""

    model_config = ConfigDict(extra='forbid')

    anchor_kind: EvidenceAnchorKind = Field(description="Primary anchor locator kind")
    locator_quality: EvidenceLocatorQuality = Field(description="Precision of the resolved locator")
    supports_decision: EvidenceSupportsDecision = Field(
        description="Whether the anchor supports, contradicts, or only contextualizes the decision"
    )
    snippet_text: Optional[str] = Field(
        default=None,
        description="Primary evidence snippet sourced from PDFX markdown when available",
    )
    sentence_text: Optional[str] = Field(
        default=None,
        description="Sentence-level evidence text when available",
    )
    normalized_text: Optional[str] = Field(
        default=None,
        description="Normalized text used for matching when exact quote text diverges",
    )
    viewer_search_text: Optional[str] = Field(
        default=None,
        description="Best available text-layer search string for the real PDF viewer",
    )
    pdfx_markdown_offset_start: Optional[int] = Field(
        default=None,
        ge=0,
        description="Inclusive character offset into PDFX markdown for the anchor start",
    )
    pdfx_markdown_offset_end: Optional[int] = Field(
        default=None,
        ge=0,
        description="Exclusive character offset into PDFX markdown for the anchor end",
    )
    page_number: Optional[int] = Field(
        default=None,
        ge=1,
        description="1-based PDF page number for the best available anchor location",
    )
    page_label: Optional[str] = Field(
        default=None,
        description="Viewer-facing page label when the PDF page label differs from page_number",
    )
    section_title: Optional[str] = Field(
        default=None,
        description="Primary section title associated with the anchor",
    )
    subsection_title: Optional[str] = Field(
        default=None,
        description="Secondary section locator associated with the anchor",
    )
    figure_reference: Optional[str] = Field(
        default=None,
        description="Figure reference associated with the anchor when present",
    )
    table_reference: Optional[str] = Field(
        default=None,
        description="Table reference associated with the anchor when present",
    )
    chunk_ids: list[str] = Field(
        default_factory=list,
        description="Resolved PDFX chunk identifiers contributing to this anchor",
    )

    @model_validator(mode="after")
    def validate_markdown_offsets(self) -> "EvidenceAnchor":
        """Require complete, monotonic markdown offsets when offsets are present."""

        start = self.pdfx_markdown_offset_start
        end = self.pdfx_markdown_offset_end

        if (start is None) != (end is None):
            raise ValueError("PDFX markdown offsets must include both start and end values")
        if start is not None and end is not None and end < start:
            raise ValueError("PDFX markdown offset end must be greater than or equal to start")

        return self


class ValidationCandidateMatch(BaseModel):
    """Candidate match surfaced by a field validator for ambiguous or conflicting values."""

    model_config = ConfigDict(extra='forbid')

    label: str = Field(description="Display label for the candidate match")
    identifier: Optional[str] = Field(
        default=None,
        description="Stable identifier or CURIE for the candidate match when available",
    )
    matched_value: Optional[str] = Field(
        default=None,
        description="Submitted value, synonym, or alias that produced the match",
    )
    score: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Optional resolver confidence or ranking score",
    )


class FieldValidationStatus(str, Enum):
    """Per-field validation states surfaced to curator-facing editing flows."""

    VALIDATED = "validated"
    AMBIGUOUS = "ambiguous"
    NOT_FOUND = "not_found"
    INVALID_FORMAT = "invalid_format"
    CONFLICT = "conflict"
    SKIPPED = "skipped"
    OVERRIDDEN = "overridden"


class FieldValidationResult(BaseModel):
    """Reusable field validation result contract."""

    model_config = ConfigDict(extra='forbid')

    status: FieldValidationStatus = Field(description="Field validation outcome")
    resolver: Optional[str] = Field(
        default=None,
        description="Resolver key or service name that produced the validation result",
    )
    candidate_matches: list[ValidationCandidateMatch] = Field(
        default_factory=list,
        description="Candidate matches returned by the resolver for ambiguous or conflicting fields",
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Non-fatal warnings surfaced during validation",
    )


class SubmissionMode(str, Enum):
    """Submission lane requested by the curator workflow."""

    PREVIEW = "preview"
    EXPORT = "export"
    DIRECT_SUBMIT = "direct_submit"


class SubmissionTargetSystem(str, Enum):
    """Supported submission or export targets."""

    ALLIANCE_CURATION_API = "alliance_curation_api"
    ABC_API = "abc_api"
    BULK_INGEST = "bulk_ingest"
    FILE_EXPORT = "file_export"
    FILE_UPLOAD = "file_upload"


class SubmissionPayloadContract(BaseModel):
    """Transport-agnostic submission payload emitted by a domain adapter."""

    model_config = ConfigDict(extra='forbid')

    mode: SubmissionMode = Field(description="Requested submission mode")
    target_system: SubmissionTargetSystem = Field(description="Destination target for the payload")
    adapter_key: str = Field(description="Domain adapter key that produced the payload")
    candidate_ids: list[str] = Field(
        default_factory=list,
        description="Candidate identifiers included in this payload",
    )
    payload_json: Optional[dict[str, Any] | list[Any]] = Field(
        default=None,
        description="Structured payload body for API or JSON-like export targets",
    )
    payload_text: Optional[str] = Field(
        default=None,
        description="Serialized text payload for XML, TSV, or similar export targets",
    )
    content_type: Optional[str] = Field(
        default=None,
        description="Content type for downstream preview, export, or submission consumers",
    )
    filename: Optional[str] = Field(
        default=None,
        description="Filename hint for export or upload targets",
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Warnings emitted while constructing the submission payload",
    )

    @model_validator(mode="after")
    def validate_payload_variant(self) -> "SubmissionPayloadContract":
        """Require at least one payload representation."""

        if self.payload_json is None and self.payload_text is None:
            raise ValueError("Submission payloads must provide payload_json or payload_text")

        return self


@runtime_checkable
class SubmissionDomainAdapter(Protocol):
    """Domain adapter contract for building reusable submission payloads."""

    adapter_key: str
    supported_submission_modes: Sequence[SubmissionMode]
    supported_target_systems: Sequence[SubmissionTargetSystem]

    def build_submission_payload(
        self,
        *,
        mode: SubmissionMode,
        target_system: SubmissionTargetSystem,
        payload_context: Mapping[str, Any],
    ) -> SubmissionPayloadContract:
        """Build a submission payload for preview, export, or direct submission."""


__all__ = [
    "EvidenceAnchor",
    "EvidenceAnchorKind",
    "EvidenceLocatorQuality",
    "EvidenceSupportsDecision",
    "FieldValidationResult",
    "FieldValidationStatus",
    "SubmissionDomainAdapter",
    "SubmissionMode",
    "SubmissionPayloadContract",
    "SubmissionTargetSystem",
    "ValidationCandidateMatch",
]
