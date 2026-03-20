"""Shared curation workspace contracts for substrate, evidence, validation, and submission."""

from __future__ import annotations

from datetime import datetime
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
    locator_quality: EvidenceLocatorQuality = Field(
        description="Precision of the resolved locator"
    )
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
    target_system: SubmissionTargetSystem = Field(
        description="Destination target for the payload"
    )
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
        """Require at least one payload representation while allowing dual formats."""

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


class CurationWorkspaceBaseModel(BaseModel):
    """Base model for curation workspace substrate contracts."""

    model_config = ConfigDict(extra='forbid')


class CurationSessionStatus(str, Enum):
    """Top-level review-session lifecycle states."""

    NEW = "new"
    IN_PROGRESS = "in_progress"
    PAUSED = "paused"
    READY_FOR_SUBMISSION = "ready_for_submission"
    SUBMITTED = "submitted"
    REJECTED = "rejected"


class CurationCandidateStatus(str, Enum):
    """Curator-facing decision state for an individual candidate."""

    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class CurationCandidateSource(str, Enum):
    """How a workspace candidate entered the session."""

    EXTRACTED = "extracted"
    MANUAL = "manual"
    IMPORTED = "imported"


class CurationCandidateAction(str, Enum):
    """Mutation actions supported by the candidate-review API surface."""

    ACCEPT = "accept"
    REJECT = "reject"
    RESET = "reset"


class CurationValidationSnapshotState(str, Enum):
    """Lifecycle state for a validation snapshot."""

    NOT_REQUESTED = "not_requested"
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    STALE = "stale"


class CurationValidationScope(str, Enum):
    """Scope of a validation snapshot payload."""

    CANDIDATE = "candidate"
    SESSION = "session"


class CurationActionType(str, Enum):
    """Immutable action-log events emitted by workspace mutations."""

    SESSION_CREATED = "session_created"
    SESSION_STATUS_UPDATED = "session_status_updated"
    SESSION_ASSIGNED = "session_assigned"
    CANDIDATE_CREATED = "candidate_created"
    CANDIDATE_UPDATED = "candidate_updated"
    CANDIDATE_ACCEPTED = "candidate_accepted"
    CANDIDATE_REJECTED = "candidate_rejected"
    CANDIDATE_RESET = "candidate_reset"
    VALIDATION_REQUESTED = "validation_requested"
    VALIDATION_COMPLETED = "validation_completed"
    EVIDENCE_RECOMPUTED = "evidence_recomputed"
    EVIDENCE_MANUAL_ADDED = "evidence_manual_added"
    SUBMISSION_PREVIEWED = "submission_previewed"
    SUBMISSION_EXECUTED = "submission_executed"
    SUBMISSION_RETRIED = "submission_retried"


class CurationActorType(str, Enum):
    """Actor classes that can produce curation action-log entries."""

    USER = "user"
    SYSTEM = "system"
    ADAPTER = "adapter"


class CurationEvidenceSource(str, Enum):
    """Origin of a candidate evidence record."""

    EXTRACTED = "extracted"
    MANUAL = "manual"
    RECOMPUTED = "recomputed"


class CurationSessionSortField(str, Enum):
    """Server-side sort surface exposed by inventory and queue APIs."""

    PREPARED_AT = "prepared_at"
    LAST_WORKED_AT = "last_worked_at"
    STATUS = "status"
    DOCUMENT_TITLE = "document_title"
    CANDIDATE_COUNT = "candidate_count"
    VALIDATION = "validation"
    EVIDENCE = "evidence"
    CURATOR = "curator"


class CurationSortDirection(str, Enum):
    """Sort direction for inventory and queue APIs."""

    ASC = "asc"
    DESC = "desc"


class CurationQueueNavigationDirection(str, Enum):
    """Queue navigation direction for next-session APIs."""

    NEXT = "next"
    PREVIOUS = "previous"


class CurationSubmissionStatus(str, Enum):
    """Shared submission-status surface for preview, export, and submit flows."""

    PREVIEW_READY = "preview_ready"
    EXPORT_READY = "export_ready"
    QUEUED = "queued"
    ACCEPTED = "accepted"
    VALIDATION_ERRORS = "validation_errors"
    CONFLICT = "conflict"
    MANUAL_REVIEW_REQUIRED = "manual_review_required"
    FAILED = "failed"


class CurationExtractionSourceKind(str, Enum):
    """Upstream source that produced an extraction envelope."""

    CHAT = "chat"
    FLOW = "flow"
    MANUAL_IMPORT = "manual_import"


class CurationActorRef(CurationWorkspaceBaseModel):
    """Reusable actor reference shared across sessions, views, and action log entries."""

    actor_id: Optional[str] = Field(default=None, description="Stable actor identifier")
    display_name: Optional[str] = Field(default=None, description="Human-friendly name")
    email: Optional[str] = Field(default=None, description="Actor email when available")


class CurationAdapterRef(CurationWorkspaceBaseModel):
    """Adapter or adapter-profile metadata surfaced in inventory and workspace views."""

    adapter_key: str = Field(description="Canonical adapter identifier")
    profile_key: Optional[str] = Field(
        default=None,
        description="Optional adapter-owned profile or subdomain key",
    )
    display_label: Optional[str] = Field(
        default=None,
        description="UI-facing adapter label",
    )
    profile_label: Optional[str] = Field(
        default=None,
        description="UI-facing profile label",
    )
    color_token: Optional[str] = Field(
        default=None,
        description="Optional design-token or semantic color name for adapter pills",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Adapter-owned display metadata that should not be interpreted by the substrate",
    )


class CurationDocumentRef(CurationWorkspaceBaseModel):
    """Canonical document metadata needed by inventory and workspace clients."""

    document_id: str = Field(description="Workspace document identifier")
    title: str = Field(description="Primary document title")
    pmid: Optional[str] = Field(default=None, description="PMID when available")
    doi: Optional[str] = Field(default=None, description="DOI when available")
    citation_label: Optional[str] = Field(
        default=None,
        description="Compact citation text used in inventory rows",
    )
    pdf_url: Optional[str] = Field(
        default=None,
        description="Resolved PDF URL or signed asset URL for the viewer",
    )
    viewer_url: Optional[str] = Field(
        default=None,
        description="Viewer route or PDF asset URL used by the frontend viewer",
    )
    publication_year: Optional[int] = Field(
        default=None,
        description="Publication year when available",
    )


class CurationDateRange(CurationWorkspaceBaseModel):
    """Reusable inclusive date-range filter contract."""

    from_at: Optional[datetime] = Field(default=None, description="Inclusive range start")
    to_at: Optional[datetime] = Field(default=None, description="Inclusive range end")

    @model_validator(mode="after")
    def validate_range(self) -> "CurationDateRange":
        """Require monotonic date ranges when both ends are present."""

        if (
            self.from_at is not None
            and self.to_at is not None
            and self.to_at < self.from_at
        ):
            raise ValueError("Curation date ranges must not end before they start")

        return self


class CurationEvidenceQualityCounts(CurationWorkspaceBaseModel):
    """Counts of evidence anchors by locator quality."""

    exact_quote: int = Field(default=0, ge=0)
    normalized_quote: int = Field(default=0, ge=0)
    section_only: int = Field(default=0, ge=0)
    page_only: int = Field(default=0, ge=0)
    document_only: int = Field(default=0, ge=0)
    unresolved: int = Field(default=0, ge=0)


class CurationEvidenceSummary(CurationWorkspaceBaseModel):
    """Aggregated evidence quality and resolution metrics for candidates or sessions."""

    total_anchor_count: int = Field(
        default=0,
        ge=0,
        description="Total evidence anchors in scope",
    )
    resolved_anchor_count: int = Field(
        default=0,
        ge=0,
        description="Anchors resolved to an exact, normalized, section, or page location",
    )
    viewer_highlightable_anchor_count: int = Field(
        default=0,
        ge=0,
        description="Anchors likely highlightable in the real PDF text layer",
    )
    quality_counts: CurationEvidenceQualityCounts = Field(
        default_factory=CurationEvidenceQualityCounts,
        description="Per-quality aggregate counts",
    )
    degraded: bool = Field(
        default=False,
        description="Whether degraded evidence quality warnings should be surfaced to the curator",
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Curator-facing evidence quality warnings",
    )

    @model_validator(mode="after")
    def validate_counts(self) -> "CurationEvidenceSummary":
        """Keep evidence count aggregates internally consistent."""

        if self.resolved_anchor_count > self.total_anchor_count:
            raise ValueError("Resolved anchor count cannot exceed total anchor count")
        if self.viewer_highlightable_anchor_count > self.total_anchor_count:
            raise ValueError(
                "Viewer-highlightable anchor count cannot exceed total anchor count"
            )

        return self


class CurationValidationCounts(CurationWorkspaceBaseModel):
    """Aggregated validation counts across all relevant fields."""

    validated: int = Field(default=0, ge=0)
    ambiguous: int = Field(default=0, ge=0)
    not_found: int = Field(default=0, ge=0)
    invalid_format: int = Field(default=0, ge=0)
    conflict: int = Field(default=0, ge=0)
    skipped: int = Field(default=0, ge=0)
    overridden: int = Field(default=0, ge=0)


class CurationValidationSummary(CurationWorkspaceBaseModel):
    """Summary validation state reused in inventory rows, candidates, and session payloads."""

    state: CurationValidationSnapshotState = Field(
        default=CurationValidationSnapshotState.NOT_REQUESTED,
        description="Current validation lifecycle state",
    )
    counts: CurationValidationCounts = Field(
        default_factory=CurationValidationCounts,
        description="Per-status validation counts",
    )
    last_validated_at: Optional[datetime] = Field(
        default=None,
        description="Timestamp of the latest completed validation snapshot",
    )
    stale_field_keys: list[str] = Field(
        default_factory=list,
        description="Fields whose validation results are stale after draft edits",
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Validation warnings that should be surfaced in inventory or workspace views",
    )


class CurationSessionProgress(CurationWorkspaceBaseModel):
    """Candidate review progress surfaced in inventory and workspace headers."""

    total_candidates: int = Field(default=0, ge=0)
    reviewed_candidates: int = Field(default=0, ge=0)
    pending_candidates: int = Field(default=0, ge=0)
    accepted_candidates: int = Field(default=0, ge=0)
    rejected_candidates: int = Field(default=0, ge=0)
    manual_candidates: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_progress(self) -> "CurationSessionProgress":
        """Require reviewed counts to stay within the total candidate count."""

        if self.reviewed_candidates > self.total_candidates:
            raise ValueError("Reviewed candidate count cannot exceed total candidates")
        if self.pending_candidates > self.total_candidates:
            raise ValueError("Pending candidate count cannot exceed total candidates")

        return self


class CurationCandidateSubmissionReadiness(CurationWorkspaceBaseModel):
    """Per-candidate submission readiness emitted by preview and submit endpoints."""

    candidate_id: str = Field(description="Candidate identifier")
    ready: bool = Field(description="Whether the candidate is ready for submission")
    blocking_reasons: list[str] = Field(
        default_factory=list,
        description="Blocking reasons preventing preview or submission",
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Non-blocking submission warnings",
    )


class CurationDraftField(CurationWorkspaceBaseModel):
    """Generic editable field state shared by workspace editors and autosave flows."""

    field_key: str = Field(description="Stable adapter-owned field identifier")
    label: str = Field(description="Curator-facing field label")
    value: Optional[Any] = Field(
        default=None,
        description="Current curator-visible value",
    )
    seed_value: Optional[Any] = Field(
        default=None,
        description="AI-seeded value before curator edits",
    )
    field_type: Optional[str] = Field(
        default=None,
        description="Adapter-owned widget or data-type hint",
    )
    group_key: Optional[str] = Field(
        default=None,
        description="Adapter-owned section or field-group identifier",
    )
    group_label: Optional[str] = Field(
        default=None,
        description="Curator-facing section label",
    )
    order: int = Field(default=0, ge=0, description="Sort order inside the draft")
    required: bool = Field(default=False, description="Whether the field is required")
    read_only: bool = Field(
        default=False,
        description="Whether the field should be rendered as read-only",
    )
    dirty: bool = Field(
        default=False,
        description="Whether the curator changed the field from its AI seed value",
    )
    stale_validation: bool = Field(
        default=False,
        description="Whether validation should be refreshed after draft edits",
    )
    evidence_anchor_ids: list[str] = Field(
        default_factory=list,
        description="Evidence anchor identifiers linked to this field",
    )
    validation_result: Optional[FieldValidationResult] = Field(
        default=None,
        description="Latest field-level validation result",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Adapter-owned field metadata for rendering or validation hints",
    )


class CurationDraft(CurationWorkspaceBaseModel):
    """Editable candidate draft with adapter-owned fields and autosave metadata."""

    draft_id: str = Field(description="Draft identifier")
    candidate_id: str = Field(description="Owning candidate identifier")
    adapter_key: str = Field(description="Adapter responsible for the draft shape")
    version: int = Field(default=1, ge=1, description="Monotonic draft version")
    title: Optional[str] = Field(
        default=None,
        description="Optional draft-level title surfaced in the editor header",
    )
    summary: Optional[str] = Field(
        default=None,
        description="Optional adapter-owned summary or context text",
    )
    fields: list[CurationDraftField] = Field(
        default_factory=list,
        description="Ordered editable field states",
    )
    notes: Optional[str] = Field(
        default=None,
        description="Curator notes stored with the draft",
    )
    created_at: datetime = Field(description="Draft creation timestamp")
    updated_at: datetime = Field(description="Latest draft mutation timestamp")
    last_saved_at: Optional[datetime] = Field(
        default=None,
        description="Latest successful autosave or manual save timestamp",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Adapter-owned draft metadata",
    )


class CurationEvidenceRecord(CurationWorkspaceBaseModel):
    """Workspace-owned wrapper around the reusable evidence anchor contract."""

    anchor_id: str = Field(description="Stable workspace evidence identifier")
    candidate_id: str = Field(description="Owning candidate identifier")
    source: CurationEvidenceSource = Field(
        description="How the evidence record entered the candidate workspace",
    )
    field_keys: list[str] = Field(
        default_factory=list,
        description="Field identifiers supported by this evidence anchor",
    )
    field_group_keys: list[str] = Field(
        default_factory=list,
        description="Field-group or section identifiers supported by this evidence anchor",
    )
    is_primary: bool = Field(
        default=False,
        description="Whether the anchor is the primary evidence record for its target field(s)",
    )
    anchor: EvidenceAnchor = Field(description="Resolved evidence anchor payload")
    created_at: datetime = Field(description="Evidence record creation timestamp")
    updated_at: datetime = Field(description="Latest evidence record mutation timestamp")
    warnings: list[str] = Field(
        default_factory=list,
        description="Warnings or degraded-mode notes for this evidence record",
    )


class CurationValidationSnapshot(CurationWorkspaceBaseModel):
    """Detailed validation snapshot reused by candidate and session validation endpoints."""

    snapshot_id: str = Field(description="Validation snapshot identifier")
    scope: CurationValidationScope = Field(description="Validation scope")
    session_id: str = Field(description="Owning session identifier")
    candidate_id: Optional[str] = Field(
        default=None,
        description="Candidate identifier when scope is candidate",
    )
    adapter_key: Optional[str] = Field(
        default=None,
        description="Adapter key responsible for the validation plan",
    )
    state: CurationValidationSnapshotState = Field(
        description="Lifecycle state of this validation snapshot",
    )
    field_results: dict[str, FieldValidationResult] = Field(
        default_factory=dict,
        description="Latest validation result for each field key in scope",
    )
    summary: CurationValidationSummary = Field(
        description="Aggregated validation summary for the snapshot",
    )
    requested_at: Optional[datetime] = Field(
        default=None,
        description="Timestamp when validation was requested",
    )
    completed_at: Optional[datetime] = Field(
        default=None,
        description="Timestamp when validation completed",
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Snapshot-level warnings or degraded-mode notes",
    )


class CurationCandidate(CurationWorkspaceBaseModel):
    """Curator-reviewable candidate with draft, evidence, and validation state."""

    candidate_id: str = Field(description="Candidate identifier")
    session_id: str = Field(description="Owning session identifier")
    source: CurationCandidateSource = Field(description="Candidate origin")
    status: CurationCandidateStatus = Field(description="Current curator decision state")
    order: int = Field(default=0, ge=0, description="Display order in the candidate queue")
    adapter_key: str = Field(description="Adapter responsible for the candidate shape")
    profile_key: Optional[str] = Field(
        default=None,
        description="Optional adapter-owned profile or subdomain key",
    )
    display_label: Optional[str] = Field(
        default=None,
        description="Primary queue label shown to curators",
    )
    secondary_label: Optional[str] = Field(
        default=None,
        description="Secondary queue label or summary value",
    )
    confidence: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="AI confidence or ranking score when available",
    )
    conversation_summary: Optional[str] = Field(
        default=None,
        description="Condensed upstream conversation or extraction context",
    )
    unresolved_ambiguities: list[str] = Field(
        default_factory=list,
        description="Open ambiguity notes the curator should resolve",
    )
    extraction_result_id: Optional[str] = Field(
        default=None,
        description="Extraction result that seeded this candidate when available",
    )
    draft: CurationDraft = Field(description="Editable draft state")
    evidence_anchors: list[CurationEvidenceRecord] = Field(
        default_factory=list,
        description="Evidence anchors linked to this candidate",
    )
    validation: Optional[CurationValidationSummary] = Field(
        default=None,
        description="Current validation summary for the candidate",
    )
    evidence_summary: Optional[CurationEvidenceSummary] = Field(
        default=None,
        description="Current evidence summary for the candidate",
    )
    created_at: datetime = Field(description="Candidate creation timestamp")
    updated_at: datetime = Field(description="Latest candidate mutation timestamp")
    last_reviewed_at: Optional[datetime] = Field(
        default=None,
        description="Latest time the candidate was actively reviewed",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Adapter-owned candidate metadata",
    )


class CurationActionLogEntry(CurationWorkspaceBaseModel):
    """Immutable action log record for session and candidate mutations."""

    action_id: str = Field(description="Immutable action-log identifier")
    session_id: str = Field(description="Owning session identifier")
    candidate_id: Optional[str] = Field(
        default=None,
        description="Affected candidate identifier when the action is candidate-scoped",
    )
    draft_id: Optional[str] = Field(
        default=None,
        description="Affected draft identifier when the action mutated a draft",
    )
    action_type: CurationActionType = Field(description="Kind of logged action")
    actor_type: CurationActorType = Field(description="Actor category")
    actor: Optional[CurationActorRef] = Field(
        default=None,
        description="Expanded actor reference when available",
    )
    occurred_at: datetime = Field(description="Timestamp of the logged action")
    previous_session_status: Optional[CurationSessionStatus] = Field(
        default=None,
        description="Previous session status when the action changed the session state",
    )
    new_session_status: Optional[CurationSessionStatus] = Field(
        default=None,
        description="New session status when the action changed the session state",
    )
    previous_candidate_status: Optional[CurationCandidateStatus] = Field(
        default=None,
        description="Previous candidate status when the action changed the candidate state",
    )
    new_candidate_status: Optional[CurationCandidateStatus] = Field(
        default=None,
        description="New candidate status when the action changed the candidate state",
    )
    changed_field_keys: list[str] = Field(
        default_factory=list,
        description="Draft field keys mutated by the action",
    )
    evidence_anchor_ids: list[str] = Field(
        default_factory=list,
        description="Evidence anchor identifiers touched by the action",
    )
    reason: Optional[str] = Field(
        default=None,
        description="Optional curator or system reason attached to the action",
    )
    message: Optional[str] = Field(
        default=None,
        description="Optional human-readable action summary",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Transport or adapter metadata for the action entry",
    )


class CurationSessionFilters(CurationWorkspaceBaseModel):
    """Reusable inventory and queue filter contract for session list APIs and saved views."""

    statuses: list[CurationSessionStatus] = Field(default_factory=list)
    adapter_keys: list[str] = Field(default_factory=list)
    profile_keys: list[str] = Field(default_factory=list)
    domain_keys: list[str] = Field(default_factory=list)
    curator_ids: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    flow_run_id: Optional[str] = Field(
        default=None,
        description="Optional flow-run filter for grouped inventory views",
    )
    document_id: Optional[str] = Field(
        default=None,
        description="Optional document filter",
    )
    search: Optional[str] = Field(
        default=None,
        description="Free-text query across title, identifiers, and primary candidate labels",
    )
    prepared_between: Optional[CurationDateRange] = Field(
        default=None,
        description="Prepared-at date range filter",
    )
    last_worked_between: Optional[CurationDateRange] = Field(
        default=None,
        description="Last-worked date range filter",
    )
    saved_view_id: Optional[str] = Field(
        default=None,
        description="Saved view applied to the current inventory query",
    )


class CurationSavedView(CurationWorkspaceBaseModel):
    """Named saved filter view for inventory and queue navigation."""

    view_id: str = Field(description="Saved-view identifier")
    name: str = Field(description="Curator-facing saved-view name")
    description: Optional[str] = Field(
        default=None,
        description="Optional saved-view description",
    )
    filters: CurationSessionFilters = Field(description="Persisted filter contract")
    sort_by: CurationSessionSortField = Field(description="Persisted sort field")
    sort_direction: CurationSortDirection = Field(description="Persisted sort direction")
    is_default: bool = Field(
        default=False,
        description="Whether the saved view should be selected by default",
    )
    created_by: Optional[CurationActorRef] = Field(
        default=None,
        description="Curator who created the saved view",
    )
    created_at: datetime = Field(description="Saved-view creation timestamp")
    updated_at: datetime = Field(description="Latest saved-view update timestamp")


class CurationPageInfo(CurationWorkspaceBaseModel):
    """Standard page metadata for list-style curation APIs."""

    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=25, ge=1, le=200)
    total_items: int = Field(default=0, ge=0)
    total_pages: int = Field(default=0, ge=0)
    has_next_page: bool = Field(default=False)
    has_previous_page: bool = Field(default=False)


class CurationFlowRunSummary(CurationWorkspaceBaseModel):
    """Aggregate session-group summary for flow-run grouping inventory views."""

    flow_run_id: str = Field(description="Flow-run identifier")
    display_label: Optional[str] = Field(
        default=None,
        description="Optional curator-facing flow-run label",
    )
    session_count: int = Field(default=0, ge=0)
    reviewed_count: int = Field(default=0, ge=0)
    pending_count: int = Field(default=0, ge=0)
    submitted_count: int = Field(default=0, ge=0)
    last_activity_at: Optional[datetime] = Field(
        default=None,
        description="Most recent session activity within the flow run",
    )


class CurationQueueContext(CurationWorkspaceBaseModel):
    """Filtered queue-navigation context for inventory and workspace shells."""

    filters: CurationSessionFilters = Field(description="Active filter context")
    sort_by: CurationSessionSortField = Field(description="Active sort field")
    sort_direction: CurationSortDirection = Field(description="Active sort direction")
    position: Optional[int] = Field(
        default=None,
        ge=1,
        description="1-based position of the current session inside the filtered queue",
    )
    total_sessions: Optional[int] = Field(
        default=None,
        ge=0,
        description="Total sessions in the filtered queue",
    )
    previous_session_id: Optional[str] = Field(
        default=None,
        description="Previous session identifier in queue order",
    )
    next_session_id: Optional[str] = Field(
        default=None,
        description="Next session identifier in queue order",
    )


class CurationSubmissionRecord(CurationWorkspaceBaseModel):
    """Submission preview, export, or execution result for a workspace session."""

    submission_id: str = Field(description="Submission record identifier")
    session_id: str = Field(description="Owning session identifier")
    adapter_key: str = Field(description="Adapter responsible for the payload")
    mode: SubmissionMode = Field(description="Submission mode used for this record")
    target_system: SubmissionTargetSystem = Field(
        description="Target system used for this record"
    )
    status: CurationSubmissionStatus = Field(description="Submission lifecycle status")
    readiness: list[CurationCandidateSubmissionReadiness] = Field(
        default_factory=list,
        description="Per-candidate readiness used for preview or submission gating",
    )
    payload: Optional[SubmissionPayloadContract] = Field(
        default=None,
        description="Generated payload snapshot when preview/export is available",
    )
    requested_at: datetime = Field(description="Timestamp when preview or submission was requested")
    completed_at: Optional[datetime] = Field(
        default=None,
        description="Timestamp when preview/export/submission completed",
    )
    external_reference: Optional[str] = Field(
        default=None,
        description="External job, submission, or export reference when available",
    )
    response_message: Optional[str] = Field(
        default=None,
        description="Human-readable response summary from the target system",
    )
    validation_errors: list[str] = Field(
        default_factory=list,
        description="Submission validation errors returned by the adapter or target system",
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Non-fatal submission warnings",
    )


class CurationExtractionResultRecord(CurationWorkspaceBaseModel):
    """Persisted extraction envelope contract used for replay and session bootstrap."""

    extraction_result_id: str = Field(description="Extraction result identifier")
    document_id: str = Field(description="Document identifier")
    adapter_key: Optional[str] = Field(
        default=None,
        description="Adapter that will consume or produced the extraction envelope",
    )
    profile_key: Optional[str] = Field(
        default=None,
        description="Optional adapter profile or subdomain key",
    )
    domain_key: Optional[str] = Field(
        default=None,
        description="Optional domain key persisted alongside the extraction",
    )
    agent_key: str = Field(description="Agent or pipeline key that produced the envelope")
    source_kind: CurationExtractionSourceKind = Field(
        description="Originating execution surface for the extraction envelope",
    )
    origin_session_id: Optional[str] = Field(
        default=None,
        description="Upstream chat or flow session identifier",
    )
    trace_id: Optional[str] = Field(
        default=None,
        description="Trace identifier linking this envelope to observability data",
    )
    flow_run_id: Optional[str] = Field(
        default=None,
        description="Flow-run identifier when the extraction originated from a flow",
    )
    user_id: Optional[str] = Field(
        default=None,
        description="User identifier associated with the extraction run",
    )
    candidate_count: int = Field(
        default=0,
        ge=0,
        description="Candidate count inside the structured extraction envelope",
    )
    conversation_summary: Optional[str] = Field(
        default=None,
        description="Condensed upstream conversation summary",
    )
    payload_json: dict[str, Any] | list[Any] = Field(
        description="Persisted extraction envelope payload",
    )
    created_at: datetime = Field(description="Persistence timestamp")
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Storage or transport metadata for the persisted extraction envelope",
    )


class CurationSessionSummary(CurationWorkspaceBaseModel):
    """Inventory-facing session summary shared by list, next, and grouped responses."""

    session_id: str = Field(description="Session identifier")
    status: CurationSessionStatus = Field(description="Session lifecycle status")
    adapter: CurationAdapterRef = Field(description="Adapter/profile metadata")
    document: CurationDocumentRef = Field(description="Document metadata")
    flow_run_id: Optional[str] = Field(
        default=None,
        description="Flow-run identifier when the session came from a batch flow",
    )
    progress: CurationSessionProgress = Field(description="Candidate review progress")
    validation: Optional[CurationValidationSummary] = Field(
        default=None,
        description="Aggregated validation status for the session",
    )
    evidence: Optional[CurationEvidenceSummary] = Field(
        default=None,
        description="Aggregated evidence quality summary for the session",
    )
    current_candidate_id: Optional[str] = Field(
        default=None,
        description="Current candidate selected within the session",
    )
    assigned_curator: Optional[CurationActorRef] = Field(
        default=None,
        description="Curator currently assigned to the session",
    )
    created_by: Optional[CurationActorRef] = Field(
        default=None,
        description="Actor that created the session",
    )
    prepared_at: datetime = Field(description="Timestamp when the session became available")
    last_worked_at: Optional[datetime] = Field(
        default=None,
        description="Most recent curator interaction timestamp",
    )
    notes: Optional[str] = Field(
        default=None,
        description="Session-level curator notes",
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Curator-facing session warnings",
    )
    tags: list[str] = Field(
        default_factory=list,
        description="Generic labels used by inventory filters or saved views",
    )


class CurationReviewSession(CurationSessionSummary):
    """Detailed review session payload used by workspace responses and mutations."""

    session_version: int = Field(default=1, ge=1, description="Monotonic session version")
    extraction_results: list[CurationExtractionResultRecord] = Field(
        default_factory=list,
        description="Extraction envelopes that seeded or influenced this session",
    )
    latest_submission: Optional[CurationSubmissionRecord] = Field(
        default=None,
        description="Latest preview, export, or submission record for the session",
    )
    submitted_at: Optional[datetime] = Field(
        default=None,
        description="Timestamp when the session was submitted",
    )
    paused_at: Optional[datetime] = Field(
        default=None,
        description="Timestamp when the session was paused",
    )
    rejection_reason: Optional[str] = Field(
        default=None,
        description="Optional session-level rejection reason",
    )


class CurationWorkspace(CurationWorkspaceBaseModel):
    """Hydrated workspace payload consumed by page shells and detail endpoints."""

    session: CurationReviewSession = Field(description="Detailed review session payload")
    candidates: list[CurationCandidate] = Field(
        default_factory=list,
        description="Ordered candidate queue for the session",
    )
    active_candidate_id: Optional[str] = Field(
        default=None,
        description="Currently focused candidate in the workspace",
    )
    queue_context: Optional[CurationQueueContext] = Field(
        default=None,
        description="Filtered queue navigation context when the workspace was opened from inventory",
    )
    action_log: list[CurationActionLogEntry] = Field(
        default_factory=list,
        description="Immutable session and candidate action-log records",
    )
    submission_history: list[CurationSubmissionRecord] = Field(
        default_factory=list,
        description="Submission preview, export, and execution history",
    )
    saved_view_context: Optional[CurationSavedView] = Field(
        default=None,
        description="Saved-view context that opened the workspace when applicable",
    )


class CurationSessionStats(CurationWorkspaceBaseModel):
    """Aggregate counts powering inventory summary cards and dashboards."""

    total_sessions: int = Field(default=0, ge=0)
    domain_count: int = Field(default=0, ge=0)
    new_sessions: int = Field(default=0, ge=0)
    in_progress_sessions: int = Field(default=0, ge=0)
    ready_for_submission_sessions: int = Field(default=0, ge=0)
    paused_sessions: int = Field(default=0, ge=0)
    submitted_sessions: int = Field(default=0, ge=0)
    rejected_sessions: int = Field(default=0, ge=0)
    assigned_to_current_user: int = Field(default=0, ge=0)
    assigned_to_others: int = Field(default=0, ge=0)
    submitted_last_7_days: int = Field(default=0, ge=0)


class CurationSessionListRequest(CurationWorkspaceBaseModel):
    """Inventory list query contract for paginated session browsing."""

    filters: CurationSessionFilters = Field(default_factory=CurationSessionFilters)
    sort_by: CurationSessionSortField = Field(
        default=CurationSessionSortField.PREPARED_AT
    )
    sort_direction: CurationSortDirection = Field(
        default=CurationSortDirection.DESC
    )
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=25, ge=1, le=200)
    group_by_flow_run: bool = Field(
        default=False,
        description="Whether the response should include grouped flow-run metadata",
    )


class CurationSessionListResponse(CurationWorkspaceBaseModel):
    """Paginated inventory response for session lists."""

    sessions: list[CurationSessionSummary] = Field(default_factory=list)
    page_info: CurationPageInfo = Field(default_factory=CurationPageInfo)
    applied_filters: CurationSessionFilters = Field(
        default_factory=CurationSessionFilters
    )
    sort_by: CurationSessionSortField = Field(
        default=CurationSessionSortField.PREPARED_AT
    )
    sort_direction: CurationSortDirection = Field(
        default=CurationSortDirection.DESC
    )
    flow_run_groups: list[CurationFlowRunSummary] = Field(default_factory=list)


class CurationSessionStatsRequest(CurationWorkspaceBaseModel):
    """Stats query contract for inventory summary cards."""

    filters: CurationSessionFilters = Field(default_factory=CurationSessionFilters)


class CurationSessionStatsResponse(CurationWorkspaceBaseModel):
    """Inventory stats response."""

    stats: CurationSessionStats = Field(description="Aggregate session counts")
    applied_filters: CurationSessionFilters = Field(
        default_factory=CurationSessionFilters
    )


class CurationFlowRunListRequest(CurationWorkspaceBaseModel):
    """Request contract for grouped flow-run summaries."""

    filters: CurationSessionFilters = Field(default_factory=CurationSessionFilters)


class CurationFlowRunListResponse(CurationWorkspaceBaseModel):
    """Response contract for grouped flow-run summaries."""

    flow_runs: list[CurationFlowRunSummary] = Field(default_factory=list)
    applied_filters: CurationSessionFilters = Field(
        default_factory=CurationSessionFilters
    )


class CurationFlowRunSessionsRequest(CurationWorkspaceBaseModel):
    """Request contract for sessions under a specific flow run."""

    flow_run_id: str = Field(description="Flow-run identifier")
    filters: CurationSessionFilters = Field(default_factory=CurationSessionFilters)
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=25, ge=1, le=200)


class CurationFlowRunSessionsResponse(CurationWorkspaceBaseModel):
    """Response contract for grouped sessions under a specific flow run."""

    flow_run: CurationFlowRunSummary = Field(description="Selected flow-run summary")
    sessions: list[CurationSessionSummary] = Field(default_factory=list)
    page_info: CurationPageInfo = Field(default_factory=CurationPageInfo)


class CurationSavedViewListResponse(CurationWorkspaceBaseModel):
    """Response contract for listing saved inventory views."""

    views: list[CurationSavedView] = Field(default_factory=list)


class CurationSavedViewCreateRequest(CurationWorkspaceBaseModel):
    """Request contract for creating or updating a saved inventory view."""

    name: str = Field(description="Saved-view name")
    description: Optional[str] = Field(default=None, description="Optional description")
    filters: CurationSessionFilters = Field(description="Persisted filter contract")
    sort_by: CurationSessionSortField = Field(description="Persisted sort field")
    sort_direction: CurationSortDirection = Field(description="Persisted sort direction")
    is_default: bool = Field(default=False)


class CurationSavedViewCreateResponse(CurationWorkspaceBaseModel):
    """Response contract for saved-view create or update mutations."""

    view: CurationSavedView = Field(description="Saved-view payload")


class CurationSavedViewDeleteResponse(CurationWorkspaceBaseModel):
    """Response contract for deleting a saved inventory view."""

    deleted_view_id: str = Field(description="Deleted saved-view identifier")


class CurationNextSessionRequest(CurationWorkspaceBaseModel):
    """Request contract for queue-mode next or previous session navigation."""

    current_session_id: Optional[str] = Field(
        default=None,
        description="Current session identifier used as the navigation anchor",
    )
    direction: CurationQueueNavigationDirection = Field(
        default=CurationQueueNavigationDirection.NEXT
    )
    filters: CurationSessionFilters = Field(default_factory=CurationSessionFilters)
    sort_by: CurationSessionSortField = Field(
        default=CurationSessionSortField.PREPARED_AT
    )
    sort_direction: CurationSortDirection = Field(
        default=CurationSortDirection.DESC
    )


class CurationNextSessionResponse(CurationWorkspaceBaseModel):
    """Response contract for queue-mode navigation."""

    session: Optional[CurationSessionSummary] = Field(
        default=None,
        description="Resolved next or previous session summary",
    )
    queue_context: CurationQueueContext = Field(description="Updated queue context")


class CurationWorkspaceRequest(CurationWorkspaceBaseModel):
    """Request contract for hydrating a workspace session detail payload."""

    session_id: str = Field(description="Session identifier")
    candidate_id: Optional[str] = Field(
        default=None,
        description="Optional candidate identifier to focus during hydration",
    )
    include_action_log: bool = Field(default=True)
    include_submission_history: bool = Field(default=False)


class CurationWorkspaceResponse(CurationWorkspaceBaseModel):
    """Hydrated workspace response contract."""

    workspace: CurationWorkspace = Field(description="Hydrated workspace payload")


class CurationSessionCreateRequest(CurationWorkspaceBaseModel):
    """Request contract for manual review-session creation."""

    document_id: str = Field(description="Document identifier")
    adapter_key: str = Field(description="Adapter that will own the session")
    profile_key: Optional[str] = Field(
        default=None,
        description="Optional adapter profile or subdomain key",
    )
    curator_id: Optional[str] = Field(
        default=None,
        description="Curator who should own the new session",
    )
    seed_extraction_result_ids: list[str] = Field(
        default_factory=list,
        description="Extraction results to use when bootstrapping the session",
    )
    notes: Optional[str] = Field(default=None, description="Initial session notes")


class CurationSessionCreateResponse(CurationWorkspaceBaseModel):
    """Response contract for manual review-session creation."""

    created: bool = Field(description="Whether the request created a new session")
    workspace: CurationWorkspace = Field(description="Hydrated workspace payload")


class CurationDocumentBootstrapRequest(CurationWorkspaceBaseModel):
    """Request contract for document-to-session bootstrap."""

    document_id: str = Field(description="Document identifier")
    adapter_key: Optional[str] = Field(
        default=None,
        description="Optional adapter key to scope bootstrap candidate generation",
    )
    profile_key: Optional[str] = Field(
        default=None,
        description="Optional adapter profile or subdomain key",
    )
    domain_key: Optional[str] = Field(
        default=None,
        description="Optional domain filter for bootstrap selection",
    )
    source_extraction_result_id: Optional[str] = Field(
        default=None,
        description="Specific extraction result to replay when bootstrapping",
    )
    curator_id: Optional[str] = Field(
        default=None,
        description="Curator who should own the bootstrapped session",
    )
    force_rebuild: bool = Field(
        default=False,
        description="Whether an existing session should be refreshed rather than reused",
    )


class CurationDocumentBootstrapResponse(CurationWorkspaceBaseModel):
    """Response contract for document bootstrap mutations."""

    created: bool = Field(
        description="Whether the bootstrap created a new session instead of reusing an existing one"
    )
    workspace: CurationWorkspace = Field(description="Hydrated workspace payload")


class CurationSessionUpdateRequest(CurationWorkspaceBaseModel):
    """Request contract for patching session status, ownership, or notes."""

    session_id: str = Field(description="Session identifier")
    status: Optional[CurationSessionStatus] = Field(
        default=None,
        description="New session status",
    )
    notes: Optional[str] = Field(default=None, description="Updated session notes")
    curator_id: Optional[str] = Field(
        default=None,
        description="New assigned curator identifier",
    )
    current_candidate_id: Optional[str] = Field(
        default=None,
        description="Candidate to mark as currently active for resume flows",
    )


class CurationSessionUpdateResponse(CurationWorkspaceBaseModel):
    """Response contract for session patch mutations."""

    session: CurationReviewSession = Field(description="Updated session payload")
    action_log_entry: Optional[CurationActionLogEntry] = Field(
        default=None,
        description="Action-log entry emitted by the patch mutation",
    )


class CurationDraftFieldChange(CurationWorkspaceBaseModel):
    """Patch payload for mutating one or more draft fields."""

    field_key: str = Field(description="Field identifier to mutate")
    value: Optional[Any] = Field(default=None, description="New field value")
    revert_to_seed: bool = Field(
        default=False,
        description="Whether the field should be reset back to its AI-seeded value",
    )


class CurationCandidateDraftUpdateRequest(CurationWorkspaceBaseModel):
    """Request contract for autosave or explicit draft updates."""

    session_id: str = Field(description="Owning session identifier")
    candidate_id: str = Field(description="Candidate identifier")
    draft_id: str = Field(description="Draft identifier")
    expected_version: Optional[int] = Field(
        default=None,
        ge=1,
        description="Optimistic-concurrency draft version check",
    )
    field_changes: list[CurationDraftFieldChange] = Field(
        default_factory=list,
        description="Field-level changes to apply to the draft",
    )
    notes: Optional[str] = Field(
        default=None,
        description="Optional draft-level notes update",
    )
    autosave: bool = Field(
        default=True,
        description="Whether the update originated from autosave versus an explicit save action",
    )


class CurationCandidateDraftUpdateResponse(CurationWorkspaceBaseModel):
    """Response contract for draft updates."""

    candidate: CurationCandidate = Field(description="Updated candidate payload")
    draft: CurationDraft = Field(description="Updated draft payload")
    validation_snapshot: Optional[CurationValidationSnapshot] = Field(
        default=None,
        description="Validation snapshot when the update triggered revalidation",
    )
    action_log_entry: Optional[CurationActionLogEntry] = Field(
        default=None,
        description="Action-log entry emitted by the draft update",
    )


class CurationCandidateDecisionRequest(CurationWorkspaceBaseModel):
    """Request contract for candidate accept, reject, or reset actions."""

    session_id: str = Field(description="Owning session identifier")
    candidate_id: str = Field(description="Candidate identifier")
    action: CurationCandidateAction = Field(description="Decision action to perform")
    reason: Optional[str] = Field(
        default=None,
        description="Optional curator reason, especially for reject or reset flows",
    )
    advance_queue: bool = Field(
        default=True,
        description="Whether the response should include next-candidate navigation hints",
    )


class CurationCandidateDecisionResponse(CurationWorkspaceBaseModel):
    """Response contract for candidate decision mutations."""

    candidate: CurationCandidate = Field(description="Updated candidate payload")
    session: CurationReviewSession = Field(description="Updated session payload")
    next_candidate_id: Optional[str] = Field(
        default=None,
        description="Next candidate to focus after the decision when queue advance was requested",
    )
    action_log_entry: CurationActionLogEntry = Field(
        description="Action-log entry emitted by the decision",
    )


class CurationManualCandidateCreateRequest(CurationWorkspaceBaseModel):
    """Request contract for creating manual candidates inside a session."""

    session_id: str = Field(description="Owning session identifier")
    adapter_key: str = Field(description="Adapter that will own the manual candidate")
    profile_key: Optional[str] = Field(
        default=None,
        description="Optional adapter profile or subdomain key",
    )
    source: CurationCandidateSource = Field(
        default=CurationCandidateSource.MANUAL,
        description="Candidate source, defaulting to manual",
    )
    display_label: Optional[str] = Field(
        default=None,
        description="Primary queue label for the new candidate",
    )
    draft: CurationDraft = Field(description="Initial draft payload for the new candidate")
    evidence_anchors: list[CurationEvidenceRecord] = Field(
        default_factory=list,
        description="Optional evidence records linked at creation time",
    )


class CurationManualCandidateCreateResponse(CurationWorkspaceBaseModel):
    """Response contract for manual candidate creation."""

    candidate: CurationCandidate = Field(description="Created candidate payload")
    session: CurationReviewSession = Field(description="Updated session payload")
    action_log_entry: CurationActionLogEntry = Field(
        description="Action-log entry emitted by the create mutation",
    )


class CurationEvidenceResolveRequest(CurationWorkspaceBaseModel):
    """Request contract for on-demand evidence resolution."""

    session_id: str = Field(description="Owning session identifier")
    candidate_id: str = Field(description="Candidate identifier")
    field_key: Optional[str] = Field(
        default=None,
        description="Field key whose evidence should be resolved",
    )
    anchor: EvidenceAnchor = Field(
        description="Anchor payload to resolve or enrich against workspace context",
    )
    replace_existing: bool = Field(
        default=False,
        description="Whether matching existing evidence should be replaced",
    )


class CurationEvidenceResolveResponse(CurationWorkspaceBaseModel):
    """Response contract for on-demand evidence resolution."""

    evidence_record: CurationEvidenceRecord = Field(description="Resolved evidence record")
    candidate: CurationCandidate = Field(description="Updated candidate payload")


class CurationManualEvidenceCreateRequest(CurationWorkspaceBaseModel):
    """Request contract for curator-created manual evidence links."""

    session_id: str = Field(description="Owning session identifier")
    candidate_id: str = Field(description="Candidate identifier")
    field_keys: list[str] = Field(
        default_factory=list,
        description="Field identifiers linked to the manual evidence",
    )
    field_group_keys: list[str] = Field(
        default_factory=list,
        description="Field-group identifiers linked to the manual evidence",
    )
    anchor: EvidenceAnchor = Field(description="Manual evidence anchor payload")
    is_primary: bool = Field(default=False)


class CurationManualEvidenceCreateResponse(CurationWorkspaceBaseModel):
    """Response contract for manual evidence creation."""

    evidence_record: CurationEvidenceRecord = Field(description="Created evidence record")
    candidate: CurationCandidate = Field(description="Updated candidate payload")
    action_log_entry: CurationActionLogEntry = Field(
        description="Action-log entry emitted by the manual evidence mutation",
    )


class CurationEvidenceRecomputeRequest(CurationWorkspaceBaseModel):
    """Request contract for bulk evidence re-resolution."""

    session_id: str = Field(description="Owning session identifier")
    candidate_ids: list[str] = Field(
        default_factory=list,
        description="Optional candidate subset to recompute; empty means all candidates in the session",
    )
    force: bool = Field(
        default=False,
        description="Whether to recompute even when evidence is not currently marked stale",
    )


class CurationEvidenceRecomputeResponse(CurationWorkspaceBaseModel):
    """Response contract for bulk evidence re-resolution."""

    session: CurationReviewSession = Field(description="Updated session payload")
    updated_evidence_records: list[CurationEvidenceRecord] = Field(default_factory=list)
    action_log_entry: CurationActionLogEntry = Field(
        description="Action-log entry emitted by the recompute mutation",
    )


class CurationCandidateValidationRequest(CurationWorkspaceBaseModel):
    """Request contract for candidate-level validation."""

    session_id: str = Field(description="Owning session identifier")
    candidate_id: str = Field(description="Candidate identifier")
    field_keys: list[str] = Field(
        default_factory=list,
        description="Optional field subset to validate; empty means validate the whole candidate",
    )
    force: bool = Field(default=False, description="Whether to bypass snapshot freshness checks")


class CurationCandidateValidationResponse(CurationWorkspaceBaseModel):
    """Response contract for candidate-level validation."""

    candidate: CurationCandidate = Field(description="Updated candidate payload")
    validation_snapshot: CurationValidationSnapshot = Field(
        description="Completed validation snapshot",
    )


class CurationSessionValidationRequest(CurationWorkspaceBaseModel):
    """Request contract for validate-all session operations."""

    session_id: str = Field(description="Session identifier")
    candidate_ids: list[str] = Field(
        default_factory=list,
        description="Optional candidate subset to validate; empty means validate the whole session",
    )
    force: bool = Field(default=False, description="Whether to bypass snapshot freshness checks")


class CurationSessionValidationResponse(CurationWorkspaceBaseModel):
    """Response contract for validate-all session operations."""

    session: CurationReviewSession = Field(description="Updated session payload")
    session_validation: CurationValidationSnapshot = Field(
        description="Aggregated session-level validation snapshot",
    )
    candidate_validations: list[CurationValidationSnapshot] = Field(default_factory=list)


class CurationSubmissionPreviewRequest(CurationWorkspaceBaseModel):
    """Request contract for submission preview and export payload generation."""

    session_id: str = Field(description="Session identifier")
    mode: SubmissionMode = Field(description="Preview or export mode")
    target_system: SubmissionTargetSystem = Field(description="Submission target system")
    candidate_ids: list[str] = Field(
        default_factory=list,
        description="Optional candidate subset; empty means include all eligible candidates",
    )
    include_payload: bool = Field(
        default=True,
        description="Whether to include the generated payload in the response",
    )


class CurationSubmissionPreviewResponse(CurationWorkspaceBaseModel):
    """Response contract for submission preview and export payload generation."""

    submission: CurationSubmissionRecord = Field(
        description="Preview or export submission record",
    )
    session_validation: Optional[CurationValidationSnapshot] = Field(
        default=None,
        description="Validation snapshot evaluated before preview generation",
    )


class CurationSubmissionExecuteRequest(CurationWorkspaceBaseModel):
    """Request contract for executing a direct submission."""

    session_id: str = Field(description="Session identifier")
    target_system: SubmissionTargetSystem = Field(description="Submission target system")
    candidate_ids: list[str] = Field(
        default_factory=list,
        description="Optional candidate subset; empty means submit all eligible candidates",
    )
    mode: SubmissionMode = Field(
        default=SubmissionMode.DIRECT_SUBMIT,
        description="Submission mode, defaulting to direct submission",
    )


class CurationSubmissionExecuteResponse(CurationWorkspaceBaseModel):
    """Response contract for direct submission execution."""

    submission: CurationSubmissionRecord = Field(description="Completed submission record")
    session: CurationReviewSession = Field(description="Updated session payload")
    action_log_entry: CurationActionLogEntry = Field(
        description="Action-log entry emitted by the submit mutation",
    )


class CurationSubmissionRetryRequest(CurationWorkspaceBaseModel):
    """Request contract for retrying a prior submission attempt."""

    submission_id: str = Field(description="Submission identifier")
    reason: Optional[str] = Field(
        default=None,
        description="Optional human-readable retry note",
    )


class CurationSubmissionRetryResponse(CurationWorkspaceBaseModel):
    """Response contract for submission retry mutations."""

    submission: CurationSubmissionRecord = Field(description="Retried submission record")
    action_log_entry: CurationActionLogEntry = Field(
        description="Action-log entry emitted by the retry mutation",
    )


class CurationSubmissionHistoryResponse(CurationWorkspaceBaseModel):
    """Response contract for fetching a single submission history record."""

    submission: CurationSubmissionRecord = Field(description="Submission history payload")


class CurationExtractionPersistenceRequest(CurationWorkspaceBaseModel):
    """Request contract for persisting structured extraction envelopes."""

    document_id: str = Field(description="Document identifier")
    agent_key: str = Field(description="Agent or pipeline key that produced the envelope")
    source_kind: CurationExtractionSourceKind = Field(
        description="Execution surface that produced the envelope",
    )
    adapter_key: Optional[str] = Field(
        default=None,
        description="Adapter key associated with the envelope",
    )
    profile_key: Optional[str] = Field(
        default=None,
        description="Optional adapter profile or subdomain key",
    )
    domain_key: Optional[str] = Field(
        default=None,
        description="Optional domain key persisted alongside the envelope",
    )
    origin_session_id: Optional[str] = Field(
        default=None,
        description="Originating chat or flow session identifier",
    )
    trace_id: Optional[str] = Field(
        default=None,
        description="Trace identifier linking persistence back to observability data",
    )
    flow_run_id: Optional[str] = Field(
        default=None,
        description="Flow-run identifier when the envelope came from a flow",
    )
    user_id: Optional[str] = Field(
        default=None,
        description="User identifier associated with the extraction run",
    )
    candidate_count: int = Field(default=0, ge=0)
    conversation_summary: Optional[str] = Field(
        default=None,
        description="Condensed upstream conversation summary",
    )
    payload_json: dict[str, Any] | list[Any] = Field(
        description="Structured extraction envelope payload",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Storage or transport metadata for persistence",
    )


class CurationExtractionPersistenceResponse(CurationWorkspaceBaseModel):
    """Response contract for persisted extraction envelopes."""

    extraction_result: CurationExtractionResultRecord = Field(
        description="Persisted extraction result record",
    )


__all__ = [
    "CurationActionLogEntry",
    "CurationActionType",
    "CurationActorRef",
    "CurationActorType",
    "CurationAdapterRef",
    "CurationCandidate",
    "CurationCandidateAction",
    "CurationCandidateDecisionRequest",
    "CurationCandidateDecisionResponse",
    "CurationCandidateDraftUpdateRequest",
    "CurationCandidateDraftUpdateResponse",
    "CurationCandidateSource",
    "CurationCandidateStatus",
    "CurationCandidateSubmissionReadiness",
    "CurationCandidateValidationRequest",
    "CurationCandidateValidationResponse",
    "CurationDateRange",
    "CurationDocumentBootstrapRequest",
    "CurationDocumentBootstrapResponse",
    "CurationDocumentRef",
    "CurationDraft",
    "CurationDraftField",
    "CurationDraftFieldChange",
    "CurationEvidenceQualityCounts",
    "CurationEvidenceRecord",
    "CurationEvidenceRecomputeRequest",
    "CurationEvidenceRecomputeResponse",
    "CurationEvidenceResolveRequest",
    "CurationEvidenceResolveResponse",
    "CurationEvidenceSource",
    "CurationEvidenceSummary",
    "CurationExtractionPersistenceRequest",
    "CurationExtractionPersistenceResponse",
    "CurationExtractionResultRecord",
    "CurationExtractionSourceKind",
    "CurationFlowRunListRequest",
    "CurationFlowRunListResponse",
    "CurationFlowRunSessionsRequest",
    "CurationFlowRunSessionsResponse",
    "CurationFlowRunSummary",
    "CurationManualCandidateCreateRequest",
    "CurationManualCandidateCreateResponse",
    "CurationManualEvidenceCreateRequest",
    "CurationManualEvidenceCreateResponse",
    "CurationNextSessionRequest",
    "CurationNextSessionResponse",
    "CurationPageInfo",
    "CurationQueueContext",
    "CurationQueueNavigationDirection",
    "CurationReviewSession",
    "CurationSavedView",
    "CurationSavedViewCreateRequest",
    "CurationSavedViewCreateResponse",
    "CurationSavedViewDeleteResponse",
    "CurationSavedViewListResponse",
    "CurationSessionCreateRequest",
    "CurationSessionCreateResponse",
    "CurationSessionFilters",
    "CurationSessionListRequest",
    "CurationSessionListResponse",
    "CurationSessionProgress",
    "CurationSessionSortField",
    "CurationSessionStats",
    "CurationSessionStatsRequest",
    "CurationSessionStatsResponse",
    "CurationSessionStatus",
    "CurationSessionSummary",
    "CurationSessionUpdateRequest",
    "CurationSessionUpdateResponse",
    "CurationSortDirection",
    "CurationSubmissionExecuteRequest",
    "CurationSubmissionExecuteResponse",
    "CurationSubmissionHistoryResponse",
    "CurationSubmissionPreviewRequest",
    "CurationSubmissionPreviewResponse",
    "CurationSubmissionRecord",
    "CurationSubmissionRetryRequest",
    "CurationSubmissionRetryResponse",
    "CurationSubmissionStatus",
    "CurationValidationCounts",
    "CurationValidationScope",
    "CurationValidationSnapshot",
    "CurationValidationSnapshotState",
    "CurationValidationSummary",
    "CurationWorkspace",
    "CurationWorkspaceRequest",
    "CurationWorkspaceResponse",
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
