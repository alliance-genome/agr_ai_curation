"""Reusable curation workspace contract types.

This module intentionally defines only shared data contracts for evidence
anchoring, field validation, and submission planning. Resolver behavior and
target-system implementations belong to later tickets.
"""

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def _strip_required_string(value: str) -> str:
    """Normalize required string values while rejecting blanks."""
    stripped = value.strip()
    if not stripped:
        raise ValueError("Value must not be empty or whitespace only")
    return stripped


def _strip_optional_string(value: Optional[str]) -> Optional[str]:
    """Normalize optional string values while rejecting blank strings."""
    if value is None:
        return None
    return _strip_required_string(value)


def _normalize_string_list(values: List[str]) -> List[str]:
    """Trim, validate, and de-duplicate a string list while preserving order."""
    normalized: List[str] = []
    seen: set[str] = set()

    for raw_value in values:
        stripped = _strip_required_string(raw_value)
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
    SNIPPET = "snippet"
    SENTENCE = "sentence"
    SECTION = "section"
    PAGE = "page"
    DOCUMENT = "document"


class EvidenceLocatorQuality(str, Enum):
    """How precisely the evidence was localized in the source document."""

    EXACT_QUOTE = "exact_quote"
    NORMALIZED_QUOTE = "normalized_quote"
    SECTION_ONLY = "section_only"
    PAGE_ONLY = "page_only"
    DOCUMENT_ONLY = "document_only"
    UNRESOLVED = "unresolved"


class EvidenceDecisionSupport(str, Enum):
    """How an evidence anchor relates to the proposed curation decision."""

    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    CONTEXT_ONLY = "context_only"


class EvidenceAnchor(BaseModel):
    """Reusable text-first evidence reference shared across review and submission."""

    model_config = ConfigDict(extra="forbid")

    anchor_kind: EvidenceAnchorKind = Field(
        ...,
        description="Primary locator strategy for the anchor",
    )
    locator_quality: EvidenceLocatorQuality = Field(
        ...,
        description="Quality of the resolved anchor location",
    )
    supports_decision: EvidenceDecisionSupport = Field(
        ...,
        description="Whether the anchor supports, contradicts, or only contextualizes a decision",
    )
    document_id: str = Field(
        ...,
        description="Workspace document identifier for the cited paper",
    )
    chunk_id: Optional[str] = Field(
        default=None,
        description="Chunk identifier when the anchor resolves to a stored document chunk",
    )
    doc_item_ids: List[str] = Field(
        default_factory=list,
        description="PDFX doc-item identifiers that contributed to the anchor",
    )
    page_number: Optional[int] = Field(
        default=None,
        ge=1,
        description="1-indexed PDF page number when known",
    )
    section_title: Optional[str] = Field(
        default=None,
        description="Best available section heading for viewer fallback",
    )
    section_path: List[str] = Field(
        default_factory=list,
        description="Normalized section breadcrumb when hierarchical headings are available",
    )
    figure_reference: Optional[str] = Field(
        default=None,
        description="Figure or panel reference associated with the anchor when available",
    )
    snippet_text: Optional[str] = Field(
        default=None,
        description="Short evidence excerpt retained for cards, hover previews, and export",
    )
    sentence_text: Optional[str] = Field(
        default=None,
        description="Sentence-level evidence text when the extraction preserved it",
    )
    normalized_text: Optional[str] = Field(
        default=None,
        description="Normalized quote string derived from PDFX markdown matching",
    )
    viewer_search_text: Optional[str] = Field(
        default=None,
        description="Preferred string for PDF text-layer search and highlight attempts",
    )
    pdfx_markdown_start_offset: Optional[int] = Field(
        default=None,
        ge=0,
        description="Inclusive start offset into the normalized PDFX markdown when known",
    )
    pdfx_markdown_end_offset: Optional[int] = Field(
        default=None,
        ge=0,
        description="Exclusive end offset into the normalized PDFX markdown when known",
    )

    @field_validator("document_id")
    @classmethod
    def validate_document_id(cls, value: str) -> str:
        """Reject blank document identifiers."""
        return _strip_required_string(value)

    @field_validator(
        "chunk_id",
        "section_title",
        "figure_reference",
        "snippet_text",
        "sentence_text",
        "normalized_text",
        "viewer_search_text",
    )
    @classmethod
    def validate_optional_strings(cls, value: Optional[str]) -> Optional[str]:
        """Reject blank optional string values."""
        return _strip_optional_string(value)

    @field_validator("doc_item_ids", "section_path")
    @classmethod
    def validate_string_lists(cls, value: List[str]) -> List[str]:
        """Reject blank list values and collapse duplicates."""
        return _normalize_string_list(value)

    @model_validator(mode="after")
    def validate_locator_fields(self) -> "EvidenceAnchor":
        """Ensure the anchor has a coherent text-first locator contract."""
        if (self.pdfx_markdown_start_offset is None) != (
            self.pdfx_markdown_end_offset is None
        ):
            raise ValueError(
                "pdfx_markdown_start_offset and pdfx_markdown_end_offset must be provided together"
            )

        if (
            self.pdfx_markdown_start_offset is not None
            and self.pdfx_markdown_end_offset is not None
            and self.pdfx_markdown_end_offset <= self.pdfx_markdown_start_offset
        ):
            raise ValueError(
                "pdfx_markdown_end_offset must be greater than pdfx_markdown_start_offset"
            )

        required_by_kind = {
            EvidenceAnchorKind.CHUNK: bool(self.chunk_id),
            EvidenceAnchorKind.DOC_ITEM: bool(self.doc_item_ids),
            EvidenceAnchorKind.SNIPPET: bool(self.snippet_text),
            EvidenceAnchorKind.SENTENCE: bool(self.sentence_text),
            EvidenceAnchorKind.SECTION: bool(self.section_title or self.section_path),
            EvidenceAnchorKind.PAGE: self.page_number is not None,
            EvidenceAnchorKind.DOCUMENT: bool(self.document_id),
        }
        if not required_by_kind[self.anchor_kind]:
            raise ValueError(
                f"anchor_kind '{self.anchor_kind.value}' requires its matching locator field to be populated"
            )

        if self.locator_quality in {
            EvidenceLocatorQuality.EXACT_QUOTE,
            EvidenceLocatorQuality.NORMALIZED_QUOTE,
        } and not any(
            [
                bool(self.snippet_text),
                bool(self.sentence_text),
                bool(self.normalized_text),
                bool(self.viewer_search_text),
                self.pdfx_markdown_start_offset is not None,
            ]
        ):
            raise ValueError(
                "quote-based locator_quality requires quote text or PDFX markdown offsets"
            )

        if self.locator_quality == EvidenceLocatorQuality.SECTION_ONLY and not (
            self.section_title or self.section_path
        ):
            raise ValueError(
                "section_only locator_quality requires section_title or section_path"
            )

        if (
            self.locator_quality == EvidenceLocatorQuality.PAGE_ONLY
            and self.page_number is None
        ):
            raise ValueError("page_only locator_quality requires page_number")

        return self


# =============================================================================
# Field validation contracts
# =============================================================================


class FieldValidationStatus(str, Enum):
    """Shared validation lifecycle states for curated field values."""

    VALIDATED = "validated"
    AMBIGUOUS = "ambiguous"
    NOT_FOUND = "not_found"
    INVALID_FORMAT = "invalid_format"
    CONFLICT = "conflict"
    SKIPPED = "skipped"
    OVERRIDDEN = "overridden"


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
        """Enforce the minimal metadata required for shared validation statuses."""
        if self.status not in {
            FieldValidationStatus.SKIPPED,
            FieldValidationStatus.OVERRIDDEN,
        } and self.resolver is None:
            raise ValueError(
                "resolver is required for validated, ambiguous, not_found, invalid_format, and conflict statuses"
            )

        if self.status == FieldValidationStatus.AMBIGUOUS and not self.candidate_matches:
            raise ValueError("ambiguous validation results must include candidate_matches")

        return self


# =============================================================================
# Submission contracts
# =============================================================================


class SubmissionMode(str, Enum):
    """Submission execution mode."""

    PREVIEW = "preview"
    EXPORT = "export"
    DIRECT_SUBMIT = "direct_submit"


class SubmissionTargetSystem(str, Enum):
    """Supported target systems for curation submission payloads."""

    ALLIANCE_CURATION_API = "alliance_curation_api"
    ABC_API = "abc_api"
    INGEST_BULK_SUBMISSION = "ingest_bulk_submission"
    FILE_EXPORT_UPLOAD = "file_export_upload"


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
    target_schema: Optional[str] = Field(
        default=None,
        description="Target schema or export shape produced by the domain adapter",
    )
    payload: Dict[str, Any] = Field(
        default_factory=dict,
        description="Domain-specific payload passed through to the adapter implementation",
    )

    @field_validator("domain", "adapter_name")
    @classmethod
    def validate_required_adapter_strings(cls, value: str) -> str:
        """Reject blank adapter identifiers."""
        return _strip_required_string(value)

    @field_validator("adapter_version", "target_schema")
    @classmethod
    def validate_optional_adapter_strings(
        cls, value: Optional[str]
    ) -> Optional[str]:
        """Reject blank optional adapter metadata."""
        return _strip_optional_string(value)


class SubmissionPayload(BaseModel):
    """Submission request contract shared by preview, export, and submit flows."""

    model_config = ConfigDict(extra="forbid")

    mode: SubmissionMode = Field(
        ...,
        description="Whether the payload is being previewed, exported, or submitted",
    )
    target_system: SubmissionTargetSystem = Field(
        ...,
        description="External target system or submission surface for the adapted payload",
    )
    domain_adapter: SubmissionDomainAdapterContract = Field(
        ...,
        description="Domain-specific adapter payload for the chosen target system",
    )

    @model_validator(mode="after")
    def validate_payload_requirements(self) -> "SubmissionPayload":
        """Require a concrete adapter payload for export and direct-submit flows."""
        if self.mode in {
            SubmissionMode.EXPORT,
            SubmissionMode.DIRECT_SUBMIT,
        } and not self.domain_adapter.payload:
            raise ValueError(
                "domain_adapter.payload must be populated when mode is 'export' or 'direct_submit'"
            )
        return self
