"""Shared curation workspace contracts.

This module consolidates the ALL-93 evidence, validation, and submission
contracts with the ALL-92 workspace-domain API schemas so downstream tickets
can import a single stable contract surface.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal, Mapping, Optional, Protocol, Sequence, runtime_checkable
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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

    model_config = ConfigDict(extra="forbid")

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

    model_config = ConfigDict(extra="forbid")

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

    model_config = ConfigDict(extra="forbid")

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

    model_config = ConfigDict(extra="forbid")

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


CURATION_WORKSPACE_SCHEMA_VERSION = "1.0"


class CurationWorkspaceBaseModel(BaseModel):
    """Base model for workspace-domain contracts."""

    model_config = ConfigDict(extra="forbid", from_attributes=True)


class CurationDomain(str, Enum):
    """Supported curation domains."""

    DISEASE = "disease"
    EXPRESSION = "expression"
    ALLELE = "allele"
    GENE = "gene"
    CHEMICAL = "chemical"
    PHENOTYPE = "phenotype"


class CurationSessionStatus(str, Enum):
    """Lifecycle states for review sessions."""

    NEW = "new"
    IN_PROGRESS = "in_progress"
    READY_FOR_SUBMISSION = "ready_for_submission"
    PAUSED = "paused"
    SUBMITTED = "submitted"
    REJECTED = "rejected"


class CurationSessionSourceKind(str, Enum):
    """How a review session entered the workspace."""

    CHAT = "chat"
    FLOW = "flow"
    BOOTSTRAP = "bootstrap"
    MANUAL = "manual"


class CurationCandidateStatus(str, Enum):
    """Workflow state for an individual candidate in the queue."""

    PENDING = "pending"
    EDITING = "editing"
    REVIEWED = "reviewed"
    SUBMITTED = "submitted"


class CurationCandidateDecision(str, Enum):
    """Curator outcome for an individual candidate."""

    PENDING = "pending"
    ACCEPTED = "accepted"
    ACCEPTED_WITH_CHANGES = "accepted_with_changes"
    REJECTED = "rejected"


class CurationDraftFieldInputKind(str, Enum):
    """Shared field widget kinds for the domain-agnostic editor."""

    TEXT = "text"
    TEXTAREA = "textarea"
    SELECT = "select"
    AUTOCOMPLETE = "autocomplete"
    MULTISELECT = "multiselect"
    BOOLEAN = "boolean"
    NUMBER = "number"
    DATE = "date"


class CurationDraftValueSource(str, Enum):
    """Where the current draft value originated."""

    AI_SEED = "ai_seed"
    CURATOR_EDIT = "curator_edit"
    MANUAL_ANNOTATION = "manual_annotation"
    SYSTEM_UPDATE = "system_update"


class CurationSessionSortBy(str, Enum):
    """Session list sort keys used by the inventory endpoints."""

    PREPARED_AT = "prepared_at"
    LAST_WORKED_AT = "last_worked_at"
    STATUS = "status"
    DOMAIN = "domain"
    PAPER_TITLE = "paper_title"
    PMID = "pmid"


class CurationSortOrder(str, Enum):
    """Sort direction for inventory requests."""

    ASC = "asc"
    DESC = "desc"


class CurationSubmissionStatus(str, Enum):
    """High-level submission lifecycle summary."""

    NOT_STARTED = "not_started"
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class CurationSavedViewScope(str, Enum):
    """Saved view scopes shared by inventory and workspace experiences."""

    INVENTORY = "inventory"
    WORKSPACE = "workspace"


class CurationActionType(str, Enum):
    """Immutable audit log actions."""

    SESSION_CREATED = "session_created"
    SESSION_STATUS_CHANGED = "session_status_changed"
    CANDIDATE_SELECTED = "candidate_selected"
    CANDIDATE_DECISION_CHANGED = "candidate_decision_changed"
    DRAFT_AUTOSAVED = "draft_autosaved"
    DRAFT_RESET = "draft_reset"
    FIELD_UPDATED = "field_updated"
    VALIDATION_REQUESTED = "validation_requested"
    VALIDATION_COMPLETED = "validation_completed"
    SUBMISSION_REQUESTED = "submission_requested"
    SUBMISSION_COMPLETED = "submission_completed"


class CurationActionActorKind(str, Enum):
    """Actors that can produce audit-log entries."""

    SYSTEM = "system"
    CURATOR = "curator"
    AGENT = "agent"


class CurationUserSummary(CurationWorkspaceBaseModel):
    """Minimal user identity shared across session and audit payloads."""

    user_id: str = Field(..., min_length=1, max_length=255)
    display_name: Optional[str] = Field(default=None, max_length=255)
    email: Optional[str] = Field(default=None, max_length=255)


class CurationDocumentSummary(CurationWorkspaceBaseModel):
    """Paper/document context shown in inventory and workspace headers."""

    document_id: UUID
    pmid: Optional[str] = Field(default=None, max_length=32)
    title: str = Field(..., min_length=1, max_length=1000)
    journal: Optional[str] = Field(default=None, max_length=255)
    published_at: Optional[datetime] = None


class CurationSessionOrigin(CurationWorkspaceBaseModel):
    """Origin metadata linking a session to chat, flow, or manual creation."""

    source_kind: CurationSessionSourceKind
    flow_run_id: Optional[str] = Field(default=None, max_length=255)
    chat_session_id: Optional[str] = Field(default=None, max_length=255)
    trace_id: Optional[str] = Field(default=None, max_length=255)
    label: Optional[str] = Field(default=None, max_length=255)


class CurationExtractionResultSummary(CurationWorkspaceBaseModel):
    """Reference to persisted extraction results used for replay/bootstrap."""

    extraction_result_id: UUID
    document_id: UUID
    domain: CurationDomain
    source_kind: CurationSessionSourceKind
    agent_key: Optional[str] = Field(default=None, max_length=255)
    schema_key: Optional[str] = Field(default=None, max_length=255)
    schema_version: Optional[str] = Field(default=None, max_length=50)
    flow_run_id: Optional[str] = Field(default=None, max_length=255)
    trace_id: Optional[str] = Field(default=None, max_length=255)
    created_at: datetime


class CurationEvidenceSummary(CurationWorkspaceBaseModel):
    """Aggregate evidence resolution counts for sessions and candidates."""

    total_count: int = Field(default=0, ge=0)
    resolved_count: int = Field(default=0, ge=0)
    unresolved_count: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_counts(self) -> "CurationEvidenceSummary":
        """Ensure summary counts stay internally consistent."""
        if self.total_count != self.resolved_count + self.unresolved_count:
            raise ValueError("Evidence summary counts must add up to total_count")
        return self


class CurationValidationSummary(CurationWorkspaceBaseModel):
    """Aggregate validation counts without owning field-level result details."""

    total_count: int = Field(default=0, ge=0)
    validated_count: int = Field(default=0, ge=0)
    warning_count: int = Field(default=0, ge=0)
    error_count: int = Field(default=0, ge=0)
    stale_count: int = Field(default=0, ge=0)
    unvalidated_count: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_counts(self) -> "CurationValidationSummary":
        """Ensure validation buckets add up to total_count."""
        bucket_total = (
            self.validated_count
            + self.warning_count
            + self.error_count
            + self.stale_count
            + self.unvalidated_count
        )
        if self.total_count != bucket_total:
            raise ValueError("Validation summary counts must add up to total_count")
        return self


class CurationSubmissionSummary(CurationWorkspaceBaseModel):
    """Submission summary referenced by session and candidate payloads."""

    submission_id: UUID
    status: CurationSubmissionStatus
    target_system: Optional[str] = Field(default=None, max_length=100)
    external_reference: Optional[str] = Field(default=None, max_length=255)
    submitted_at: Optional[datetime] = None
    last_attempted_at: Optional[datetime] = None
    last_error: Optional[str] = Field(default=None, max_length=2000)


class CurationValidationSnapshotSummary(CurationWorkspaceBaseModel):
    """Metadata for a persisted validation snapshot referenced by candidates."""

    validation_snapshot_id: UUID
    session_id: UUID
    candidate_id: UUID
    summary: CurationValidationSummary
    created_at: datetime
    created_by: Optional[CurationUserSummary] = None
    stale: bool = False


class CurationReviewProgress(CurationWorkspaceBaseModel):
    """Session-level review progress used in headers and inventory summaries."""

    total_candidates: int = Field(default=0, ge=0)
    pending_candidates: int = Field(default=0, ge=0)
    editing_candidates: int = Field(default=0, ge=0)
    reviewed_candidates: int = Field(default=0, ge=0)
    accepted_candidates: int = Field(default=0, ge=0)
    modified_candidates: int = Field(default=0, ge=0)
    rejected_candidates: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_counts(self) -> "CurationReviewProgress":
        """Ensure progress rollups are internally consistent."""
        reviewed_total = (
            self.accepted_candidates
            + self.modified_candidates
            + self.rejected_candidates
        )
        if self.reviewed_candidates != reviewed_total:
            raise ValueError(
                "reviewed_candidates must equal accepted + modified + rejected candidates"
            )

        queue_total = self.pending_candidates + self.editing_candidates + self.reviewed_candidates
        if self.total_candidates != queue_total:
            raise ValueError(
                "total_candidates must equal pending + editing + reviewed candidates"
            )
        return self


class CurationWorkspaceHydrationState(CurationWorkspaceBaseModel):
    """Persisted UI resume state for workspace hydration."""

    selected_candidate_id: Optional[UUID] = None
    active_field_key: Optional[str] = Field(default=None, max_length=255)
    active_evidence_anchor_id: Optional[UUID] = None
    pdf_page: Optional[int] = Field(default=None, ge=1)
    editor_scroll_top: Optional[int] = Field(default=None, ge=0)
    panel_layout: dict[str, float] = Field(default_factory=dict)
    updated_at: Optional[datetime] = None


class CurationSavedViewState(CurationWorkspaceBaseModel):
    """Persisted view state reusable for inventory filters and workspace layout."""

    filters: Optional[CurationSessionListFilters] = None
    pagination: Optional[CurationPagination] = None
    selected_candidate_id: Optional[UUID] = None
    hydration: Optional[CurationWorkspaceHydrationState] = None

    @model_validator(mode="after")
    def validate_non_empty_state(self) -> "CurationSavedViewState":
        """Require at least one persisted state payload."""
        if not any(
            [
                self.filters is not None,
                self.pagination is not None,
                self.selected_candidate_id is not None,
                self.hydration is not None,
            ]
        ):
            raise ValueError("Saved view state must include filters, pagination, or hydration")
        return self


class CurationSavedViewSummary(CurationWorkspaceBaseModel):
    """Saved-view metadata surfaced in inventory and workspace responses."""

    saved_view_id: UUID
    scope: CurationSavedViewScope
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = Field(default=None, max_length=1000)
    is_default: bool = False
    shared: bool = False
    session_id: Optional[UUID] = None
    created_by: Optional[CurationUserSummary] = None
    created_at: datetime
    updated_at: datetime


class CurationSavedView(CurationSavedViewSummary):
    """Full saved-view contract with the persisted state payload."""

    state: CurationSavedViewState

    @model_validator(mode="after")
    def validate_scope_state_compatibility(self) -> "CurationSavedView":
        """Ensure inventory and workspace views only carry their owned state."""
        if self.scope == CurationSavedViewScope.INVENTORY:
            if self.session_id is not None:
                raise ValueError("Inventory saved views must not include session_id")
            if self.state.hydration is not None or self.state.selected_candidate_id is not None:
                raise ValueError(
                    "Inventory saved views cannot include workspace hydration state"
                )

        if self.scope == CurationSavedViewScope.WORKSPACE:
            if self.session_id is None:
                raise ValueError("Workspace saved views require session_id")
            if self.state.filters is not None or self.state.pagination is not None:
                raise ValueError("Workspace saved views cannot include inventory filters")

        return self


class CreateCurationSavedViewRequest(CurationWorkspaceBaseModel):
    """Create a reusable inventory or workspace saved view."""

    scope: CurationSavedViewScope
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = Field(default=None, max_length=1000)
    is_default: bool = False
    shared: bool = False
    session_id: Optional[UUID] = None
    state: CurationSavedViewState

    @model_validator(mode="after")
    def validate_scope_state_compatibility(self) -> "CreateCurationSavedViewRequest":
        """Reuse saved-view scope rules for create requests."""
        CurationSavedView(
            saved_view_id=UUID(int=0),
            scope=self.scope,
            name=self.name,
            description=self.description,
            is_default=self.is_default,
            shared=self.shared,
            session_id=self.session_id,
            created_by=None,
            created_at=datetime(1970, 1, 1),
            updated_at=datetime(1970, 1, 1),
            state=self.state,
        )
        return self


class UpdateCurationSavedViewRequest(CurationWorkspaceBaseModel):
    """Patch an existing saved view."""

    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    description: Optional[str] = Field(default=None, max_length=1000)
    is_default: Optional[bool] = None
    shared: Optional[bool] = None
    state: Optional[CurationSavedViewState] = None


class CurationSavedViewListResponse(CurationWorkspaceBaseModel):
    """Saved views available to the current curator."""

    views: list[CurationSavedView] = Field(default_factory=list)


class CurationDraftFieldOption(CurationWorkspaceBaseModel):
    """Selectable option for draft fields."""

    value: str = Field(..., min_length=1, max_length=255)
    label: str = Field(..., min_length=1, max_length=255)
    disabled: bool = False


class CurationDraftField(CurationWorkspaceBaseModel):
    """Domain-agnostic draft field used by the shared annotation editor."""

    field_key: str = Field(
        ...,
        min_length=1,
        max_length=255,
        pattern=r"^[A-Za-z][A-Za-z0-9_.-]*$",
    )
    label: str = Field(..., min_length=1, max_length=255)
    input_kind: CurationDraftFieldInputKind
    value: Any = None
    ai_value: Any = None
    placeholder: Optional[str] = Field(default=None, max_length=255)
    help_text: Optional[str] = Field(default=None, max_length=1000)
    required: bool = False
    dirty: bool = False
    value_source: CurationDraftValueSource = CurationDraftValueSource.AI_SEED
    options: list[CurationDraftFieldOption] = Field(default_factory=list)
    evidence_anchor_ids: list[UUID] = Field(default_factory=list)
    validation_snapshot_id: Optional[UUID] = None
    validation_stale: bool = False
    last_updated_at: Optional[datetime] = None
    updated_by: Optional[CurationUserSummary] = None


class CurationDraftSection(CurationWorkspaceBaseModel):
    """Logical section of the annotation editor."""

    section_key: str = Field(
        ...,
        min_length=1,
        max_length=255,
        pattern=r"^[A-Za-z][A-Za-z0-9_.-]*$",
    )
    label: str = Field(..., min_length=1, max_length=255)
    fields: list[CurationDraftField] = Field(..., min_length=1)
    collapsed: bool = False

    @field_validator("fields")
    @classmethod
    def validate_unique_field_keys(cls, fields: list[CurationDraftField]) -> list[CurationDraftField]:
        """Keep field keys unique within a section."""
        field_keys = [field.field_key for field in fields]
        if len(field_keys) != len(set(field_keys)):
            raise ValueError("Draft field keys must be unique within a section")
        return fields


class CurationDraft(CurationWorkspaceBaseModel):
    """Curator-editable draft seeded from the AI candidate."""

    draft_id: UUID
    candidate_id: UUID
    sections: list[CurationDraftSection] = Field(..., min_length=1)
    is_dirty: bool = False
    dirty_field_keys: list[str] = Field(default_factory=list)
    last_saved_at: Optional[datetime] = None
    last_saved_by: Optional[CurationUserSummary] = None
    validation_stale: bool = False

    @field_validator("sections")
    @classmethod
    def validate_unique_section_keys(
        cls,
        sections: list[CurationDraftSection],
    ) -> list[CurationDraftSection]:
        """Keep section keys unique inside a draft."""
        section_keys = [section.section_key for section in sections]
        if len(section_keys) != len(set(section_keys)):
            raise ValueError("Draft section keys must be unique")
        return sections

    @model_validator(mode="after")
    def validate_dirty_field_keys(self) -> "CurationDraft":
        """Ensure dirty_field_keys reference real fields in the draft."""
        all_field_keys = {
            field.field_key
            for section in self.sections
            for field in section.fields
        }
        unknown_keys = sorted(set(self.dirty_field_keys) - all_field_keys)
        if unknown_keys:
            raise ValueError(
                f"dirty_field_keys contains unknown field keys: {', '.join(unknown_keys)}"
            )
        return self


class CurationCandidateSummary(CurationWorkspaceBaseModel):
    """Queue-card summary for a candidate inside the workspace."""

    candidate_id: UUID
    session_id: UUID
    queue_position: int = Field(..., ge=1)
    display_label: str = Field(..., min_length=1, max_length=255)
    summary: Optional[str] = Field(default=None, max_length=2000)
    status: CurationCandidateStatus = CurationCandidateStatus.PENDING
    decision: CurationCandidateDecision = CurationCandidateDecision.PENDING
    confidence_score: Optional[float] = Field(default=None, ge=0, le=1)
    has_curator_edits: bool = False
    unresolved_ambiguity_count: int = Field(default=0, ge=0)
    evidence_summary: CurationEvidenceSummary = Field(default_factory=CurationEvidenceSummary)
    validation_summary: CurationValidationSummary = Field(default_factory=CurationValidationSummary)
    submission_summary: Optional[CurationSubmissionSummary] = None
    last_reviewed_at: Optional[datetime] = None


class CurationCandidate(CurationCandidateSummary):
    """Detailed candidate payload used by the workspace editor."""

    draft: CurationDraft
    source_extraction: Optional[CurationExtractionResultSummary] = None
    evidence_anchor_ids: list[UUID] = Field(default_factory=list)
    validation_snapshot_ids: list[UUID] = Field(default_factory=list)
    latest_validation_snapshot: Optional[CurationValidationSnapshotSummary] = None
    context_summary: Optional[str] = Field(default=None, max_length=4000)
    unresolved_ambiguities: list[str] = Field(default_factory=list)
    notes: Optional[str] = Field(default=None, max_length=4000)


class CurationActionLogEntry(CurationWorkspaceBaseModel):
    """Immutable action log entry for audit and diff views."""

    action_id: UUID
    session_id: UUID
    candidate_id: Optional[UUID] = None
    action_type: CurationActionType
    actor_kind: CurationActionActorKind
    actor_id: Optional[str] = Field(default=None, max_length=255)
    actor_display_name: Optional[str] = Field(default=None, max_length=255)
    field_key: Optional[str] = Field(default=None, max_length=255)
    previous_state: Optional[dict[str, Any]] = None
    new_state: Optional[dict[str, Any]] = None
    reason: Optional[str] = Field(default=None, max_length=2000)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class CurationSessionSummary(CurationWorkspaceBaseModel):
    """Inventory/session summary shared by list and detail endpoints."""

    session_id: UUID
    status: CurationSessionStatus
    domain: CurationDomain
    document: CurationDocumentSummary
    origin: CurationSessionOrigin
    curator: Optional[CurationUserSummary] = None
    candidate_count: int = Field(default=0, ge=0)
    reviewed_candidate_count: int = Field(default=0, ge=0)
    review_progress: CurationReviewProgress
    evidence_summary: CurationEvidenceSummary = Field(default_factory=CurationEvidenceSummary)
    validation_summary: CurationValidationSummary = Field(default_factory=CurationValidationSummary)
    submission_summary: Optional[CurationSubmissionSummary] = None
    prepared_at: datetime
    last_worked_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def validate_candidate_counts(self) -> "CurationSessionSummary":
        """Ensure candidate rollups are sane."""
        if self.reviewed_candidate_count > self.candidate_count:
            raise ValueError("reviewed_candidate_count cannot exceed candidate_count")
        if self.review_progress.total_candidates != self.candidate_count:
            raise ValueError("review_progress.total_candidates must match candidate_count")
        return self


class CurationSessionDetail(CurationSessionSummary):
    """Detail payload for session resume and bootstrap endpoints."""

    active_candidate_id: Optional[UUID] = None
    notes: Optional[str] = Field(default=None, max_length=4000)
    hydration: Optional[CurationWorkspaceHydrationState] = None
    latest_extraction: Optional[CurationExtractionResultSummary] = None


class CurationSessionStatsResponse(CurationWorkspaceBaseModel):
    """Aggregate counts for inventory dashboard cards."""

    total_sessions: int = Field(default=0, ge=0)
    new_sessions: int = Field(default=0, ge=0)
    in_progress_sessions: int = Field(default=0, ge=0)
    ready_for_submission_sessions: int = Field(default=0, ge=0)
    submitted_sessions: int = Field(default=0, ge=0)
    paused_sessions: int = Field(default=0, ge=0)
    rejected_sessions: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_counts(self) -> "CurationSessionStatsResponse":
        """Ensure status buckets add up to total_sessions."""
        bucket_total = (
            self.new_sessions
            + self.in_progress_sessions
            + self.ready_for_submission_sessions
            + self.submitted_sessions
            + self.paused_sessions
            + self.rejected_sessions
        )
        if self.total_sessions != bucket_total:
            raise ValueError("Session stats counts must add up to total_sessions")
        return self


class CurationSessionListFilters(CurationWorkspaceBaseModel):
    """Filter and sort criteria for the session inventory."""

    search: Optional[str] = Field(default=None, max_length=255)
    statuses: list[CurationSessionStatus] = Field(default_factory=list)
    domains: list[CurationDomain] = Field(default_factory=list)
    curator_ids: list[str] = Field(default_factory=list)
    flow_run_id: Optional[str] = Field(default=None, max_length=255)
    prepared_from: Optional[datetime] = None
    prepared_to: Optional[datetime] = None
    last_worked_from: Optional[datetime] = None
    last_worked_to: Optional[datetime] = None
    sort_by: CurationSessionSortBy = CurationSessionSortBy.PREPARED_AT
    sort_order: CurationSortOrder = CurationSortOrder.DESC

    @model_validator(mode="after")
    def validate_ranges(self) -> "CurationSessionListFilters":
        """Reject inverted prepared and last-worked date ranges."""
        if self.prepared_from and self.prepared_to and self.prepared_from > self.prepared_to:
            raise ValueError("prepared_from cannot be after prepared_to")
        if (
            self.last_worked_from
            and self.last_worked_to
            and self.last_worked_from > self.last_worked_to
        ):
            raise ValueError("last_worked_from cannot be after last_worked_to")
        return self


class CurationPagination(CurationWorkspaceBaseModel):
    """Shared pagination contract for list endpoints."""

    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)


class CurationSessionListRequest(CurationWorkspaceBaseModel):
    """Inventory request payload used by frontend service layers."""

    filters: CurationSessionListFilters = Field(default_factory=CurationSessionListFilters)
    pagination: CurationPagination = Field(default_factory=CurationPagination)


class CurationSessionListResponse(CurationWorkspaceBaseModel):
    """Paginated list of session summaries for the inventory page."""

    sessions: list[CurationSessionSummary] = Field(default_factory=list)
    total: int = Field(default=0, ge=0)
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1)


class CurationInventoryResponse(CurationSessionListResponse):
    """Hydrated inventory payload including filters, stats, and saved views."""

    applied_filters: CurationSessionListFilters = Field(default_factory=CurationSessionListFilters)
    stats: CurationSessionStatsResponse
    saved_views: list[CurationSavedView] = Field(default_factory=list)


class CurationSessionNavigation(CurationWorkspaceBaseModel):
    """Prev/next navigation metadata for the workspace header."""

    previous_session_id: Optional[UUID] = None
    next_session_id: Optional[UUID] = None
    queue_position: Optional[int] = Field(default=None, ge=1)
    total_sessions: Optional[int] = Field(default=None, ge=0)


class CurationWorkspaceRequest(CurationWorkspaceBaseModel):
    """Workspace detail query contract for later bootstrap and resume endpoints."""

    session_id: UUID
    candidate_id: Optional[UUID] = None
    include_action_log: bool = True
    include_navigation: bool = True
    include_saved_views: bool = True


class CurationWorkspaceResponse(CurationWorkspaceBaseModel):
    """Hydrated workspace payload consumed by the page shell and editor."""

    schema_version: Literal["1.0"] = CURATION_WORKSPACE_SCHEMA_VERSION
    session: CurationSessionDetail
    candidates: list[CurationCandidate] = Field(default_factory=list)
    action_log: list[CurationActionLogEntry] = Field(default_factory=list)
    navigation: Optional[CurationSessionNavigation] = None
    saved_views: list[CurationSavedView] = Field(default_factory=list)


class CreateCurationSessionRequest(CurationWorkspaceBaseModel):
    """Manual session creation request."""

    document_id: UUID
    domain: CurationDomain
    source_kind: CurationSessionSourceKind = CurationSessionSourceKind.MANUAL
    extraction_result_id: Optional[UUID] = None
    notes: Optional[str] = Field(default=None, max_length=4000)


class BootstrapCurationSessionRequest(CurationWorkspaceBaseModel):
    """Document bootstrap request for Review and Curate entry points."""

    domain: Optional[CurationDomain] = None
    extraction_result_id: Optional[UUID] = None
    force_refresh: bool = False


class UpdateCurationSessionRequest(CurationWorkspaceBaseModel):
    """Session patch contract for status, notes, and hydration updates."""

    status: Optional[CurationSessionStatus] = None
    notes: Optional[str] = Field(default=None, max_length=4000)
    active_candidate_id: Optional[UUID] = None
    hydration: Optional[CurationWorkspaceHydrationState] = None


class CurationCandidateReviewRequest(CurationWorkspaceBaseModel):
    """Candidate review and update contract used by later decision endpoints."""

    decision: CurationCandidateDecision
    draft: Optional[CurationDraft] = None
    reason: Optional[str] = Field(default=None, max_length=2000)
    advance_queue: bool = True


class CurationCandidateReviewResponse(CurationWorkspaceBaseModel):
    """Candidate review response including updated candidate and queue state."""

    candidate: CurationCandidate
    session: CurationSessionDetail
    next_candidate_id: Optional[UUID] = None


class CurationEvidenceRequest(CurationWorkspaceBaseModel):
    """Evidence query contract for a candidate or a specific draft field."""

    session_id: UUID
    candidate_id: UUID
    field_key: Optional[str] = Field(default=None, max_length=255)
    include_resolved: bool = True
    include_unresolved: bool = True

    @model_validator(mode="after")
    def validate_resolution_scope(self) -> "CurationEvidenceRequest":
        """Require at least one evidence resolution bucket."""
        if not self.include_resolved and not self.include_unresolved:
            raise ValueError("Evidence request must include resolved or unresolved anchors")
        return self


class CurationEvidenceResponse(CurationWorkspaceBaseModel):
    """Evidence endpoint envelope using the canonical ALL-93 anchor type."""

    session_id: UUID
    candidate_id: UUID
    field_key: Optional[str] = None
    summary: CurationEvidenceSummary = Field(default_factory=CurationEvidenceSummary)
    evidence_anchors: list[EvidenceAnchor] = Field(default_factory=list)


class CurationValidationRequest(CurationWorkspaceBaseModel):
    """Validation request contract for snapshot creation or refresh."""

    session_id: UUID
    candidate_id: UUID
    draft: Optional[CurationDraft] = None
    field_keys: list[str] = Field(default_factory=list)
    force_refresh: bool = False

    @model_validator(mode="after")
    def validate_field_keys(self) -> "CurationValidationRequest":
        """Reject unknown field keys when draft-scoped validation is requested."""
        if self.draft is None or not self.field_keys:
            return self

        all_field_keys = {
            field.field_key
            for section in self.draft.sections
            for field in section.fields
        }
        unknown_keys = sorted(set(self.field_keys) - all_field_keys)
        if unknown_keys:
            raise ValueError(
                "field_keys contains unknown draft fields: "
                + ", ".join(unknown_keys)
            )
        return self


class CurationValidationResponse(CurationWorkspaceBaseModel):
    """Validation endpoint envelope using the canonical ALL-93 field result type."""

    session_id: UUID
    candidate_id: UUID
    snapshot: CurationValidationSnapshotSummary
    results: list[FieldValidationResult] = Field(default_factory=list)


class CurationSubmissionRequest(CurationWorkspaceBaseModel):
    """Submission request envelope using the canonical ALL-93 payload type."""

    session_id: UUID
    candidate_ids: list[UUID] = Field(..., min_length=1)
    submission_payload: SubmissionPayloadContract

    @field_validator("candidate_ids")
    @classmethod
    def validate_unique_candidate_ids(cls, candidate_ids: list[UUID]) -> list[UUID]:
        """Require each candidate to be submitted at most once per request."""
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("candidate_ids must be unique")
        return candidate_ids


class CurationSubmissionResponse(CurationWorkspaceBaseModel):
    """Submission response returning updated session and summary state."""

    session: CurationSessionDetail
    submitted_candidate_ids: list[UUID] = Field(default_factory=list)
    submission_summary: CurationSubmissionSummary


class CurationExtractionPersistenceRequest(CurationWorkspaceBaseModel):
    """Persist an extraction result and seed candidate queue metadata."""

    document_id: UUID
    domain: CurationDomain
    source_kind: CurationSessionSourceKind
    extraction_payload: Any
    agent_key: Optional[str] = Field(default=None, max_length=255)
    schema_key: Optional[str] = Field(default=None, max_length=255)
    schema_version: Optional[str] = Field(default=None, max_length=50)
    flow_run_id: Optional[str] = Field(default=None, max_length=255)
    trace_id: Optional[str] = Field(default=None, max_length=255)


class CurationExtractionPersistenceResponse(CurationWorkspaceBaseModel):
    """Persisted extraction metadata plus seeded candidate summaries."""

    extraction_result: CurationExtractionResultSummary
    seeded_candidates: list[CurationCandidateSummary] = Field(default_factory=list)


class CurationNextSessionResponse(CurationWorkspaceBaseModel):
    """Queue-mode navigation response for `/sessions/next`."""

    session: Optional[CurationSessionSummary] = None
    navigation: Optional[CurationSessionNavigation] = None


__all__ = [
    "BootstrapCurationSessionRequest",
    "CURATION_WORKSPACE_SCHEMA_VERSION",
    "CreateCurationSavedViewRequest",
    "CreateCurationSessionRequest",
    "CurationActionActorKind",
    "CurationActionLogEntry",
    "CurationActionType",
    "CurationCandidate",
    "CurationCandidateDecision",
    "CurationCandidateReviewRequest",
    "CurationCandidateReviewResponse",
    "CurationCandidateStatus",
    "CurationCandidateSummary",
    "CurationDocumentSummary",
    "CurationDomain",
    "CurationDraft",
    "CurationDraftField",
    "CurationDraftFieldInputKind",
    "CurationDraftFieldOption",
    "CurationDraftSection",
    "CurationDraftValueSource",
    "CurationEvidenceRequest",
    "CurationEvidenceResponse",
    "CurationEvidenceSummary",
    "CurationExtractionPersistenceRequest",
    "CurationExtractionPersistenceResponse",
    "CurationExtractionResultSummary",
    "CurationInventoryResponse",
    "CurationNextSessionResponse",
    "CurationPagination",
    "CurationReviewProgress",
    "CurationSavedView",
    "CurationSavedViewListResponse",
    "CurationSavedViewScope",
    "CurationSavedViewState",
    "CurationSavedViewSummary",
    "CurationSessionDetail",
    "CurationSessionListFilters",
    "CurationSessionListRequest",
    "CurationSessionListResponse",
    "CurationSessionNavigation",
    "CurationSessionOrigin",
    "CurationSessionSortBy",
    "CurationSessionStatsResponse",
    "CurationSessionSourceKind",
    "CurationSessionStatus",
    "CurationSessionSummary",
    "CurationSortOrder",
    "CurationSubmissionRequest",
    "CurationSubmissionResponse",
    "CurationSubmissionStatus",
    "CurationSubmissionSummary",
    "CurationUserSummary",
    "CurationValidationRequest",
    "CurationValidationResponse",
    "CurationValidationSnapshotSummary",
    "CurationValidationSummary",
    "CurationWorkspaceHydrationState",
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
    "UpdateCurationSavedViewRequest",
    "UpdateCurationSessionRequest",
    "ValidationCandidateMatch",
]
