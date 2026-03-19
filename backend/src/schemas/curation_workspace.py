"""Shared Pydantic schemas for the curation workspace API surface.

This module defines the core session, inventory, workspace, candidate, draft,
extraction, and action-log contracts used by later curation tickets.

Evidence-anchor, field-validation, and submission payload details intentionally
stop at summary/reference models here so the deeper subtype ownership can land
cleanly in ALL-93.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


CURATION_WORKSPACE_SCHEMA_VERSION = "1.0"


class CurationWorkspaceBaseModel(BaseModel):
    """Base model for curation workspace contracts."""

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
    READY = "ready"
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
    """Submission summary referenced by session/candidate payloads."""

    submission_id: UUID
    status: CurationSubmissionStatus
    target_system: Optional[str] = Field(default=None, max_length=100)
    external_reference: Optional[str] = Field(default=None, max_length=255)
    submitted_at: Optional[datetime] = None
    last_attempted_at: Optional[datetime] = None
    last_error: Optional[str] = Field(default=None, max_length=2000)


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
    panel_layout: Dict[str, float] = Field(default_factory=dict)
    updated_at: Optional[datetime] = None


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
    options: List[CurationDraftFieldOption] = Field(default_factory=list)
    evidence_anchor_ids: List[UUID] = Field(default_factory=list)
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
    fields: List[CurationDraftField] = Field(..., min_length=1)
    collapsed: bool = False

    @field_validator("fields")
    @classmethod
    def validate_unique_field_keys(cls, fields: List[CurationDraftField]) -> List[CurationDraftField]:
        """Keep field keys unique within a section."""
        field_keys = [field.field_key for field in fields]
        if len(field_keys) != len(set(field_keys)):
            raise ValueError("Draft field keys must be unique within a section")
        return fields


class CurationDraft(CurationWorkspaceBaseModel):
    """Curator-editable draft seeded from the AI candidate."""

    draft_id: UUID
    candidate_id: UUID
    sections: List[CurationDraftSection] = Field(..., min_length=1)
    is_dirty: bool = False
    dirty_field_keys: List[str] = Field(default_factory=list)
    last_saved_at: Optional[datetime] = None
    last_saved_by: Optional[CurationUserSummary] = None
    validation_stale: bool = False

    @field_validator("sections")
    @classmethod
    def validate_unique_section_keys(
        cls,
        sections: List[CurationDraftSection],
    ) -> List[CurationDraftSection]:
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
    """Detailed candidate payload used by the workspace/editor."""

    draft: CurationDraft
    source_extraction: Optional[CurationExtractionResultSummary] = None
    evidence_anchor_ids: List[UUID] = Field(default_factory=list)
    validation_snapshot_ids: List[UUID] = Field(default_factory=list)
    context_summary: Optional[str] = Field(default=None, max_length=4000)
    unresolved_ambiguities: List[str] = Field(default_factory=list)
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
    previous_state: Optional[Dict[str, Any]] = None
    new_state: Optional[Dict[str, Any]] = None
    reason: Optional[str] = Field(default=None, max_length=2000)
    metadata: Dict[str, Any] = Field(default_factory=dict)
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
    """Detail payload for session resume/bootstrap endpoints."""

    active_candidate_id: Optional[UUID] = None
    notes: Optional[str] = Field(default=None, max_length=4000)
    hydration: Optional[CurationWorkspaceHydrationState] = None
    latest_extraction: Optional[CurationExtractionResultSummary] = None


class CurationSessionStatsResponse(CurationWorkspaceBaseModel):
    """Aggregate counts for inventory dashboard cards."""

    total_sessions: int = Field(default=0, ge=0)
    new_sessions: int = Field(default=0, ge=0)
    in_progress_sessions: int = Field(default=0, ge=0)
    ready_sessions: int = Field(default=0, ge=0)
    submitted_sessions: int = Field(default=0, ge=0)
    paused_sessions: int = Field(default=0, ge=0)
    rejected_sessions: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_counts(self) -> "CurationSessionStatsResponse":
        """Ensure status buckets add up to total_sessions."""
        bucket_total = (
            self.new_sessions
            + self.in_progress_sessions
            + self.ready_sessions
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
    statuses: List[CurationSessionStatus] = Field(default_factory=list)
    domains: List[CurationDomain] = Field(default_factory=list)
    curator_ids: List[str] = Field(default_factory=list)
    flow_run_id: Optional[str] = Field(default=None, max_length=255)
    prepared_from: Optional[datetime] = None
    prepared_to: Optional[datetime] = None
    last_worked_from: Optional[datetime] = None
    last_worked_to: Optional[datetime] = None
    sort_by: CurationSessionSortBy = CurationSessionSortBy.PREPARED_AT
    sort_order: CurationSortOrder = CurationSortOrder.DESC

    @model_validator(mode="after")
    def validate_ranges(self) -> "CurationSessionListFilters":
        """Reject inverted prepared/last-worked date ranges."""
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

    sessions: List[CurationSessionSummary] = Field(default_factory=list)
    total: int = Field(default=0, ge=0)
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1)


class CurationSessionNavigation(CurationWorkspaceBaseModel):
    """Prev/next navigation metadata for the workspace header."""

    previous_session_id: Optional[UUID] = None
    next_session_id: Optional[UUID] = None
    queue_position: Optional[int] = Field(default=None, ge=1)
    total_sessions: Optional[int] = Field(default=None, ge=0)


class CurationWorkspaceResponse(CurationWorkspaceBaseModel):
    """Hydrated workspace payload consumed by the page shell and editor."""

    schema_version: Literal["1.0"] = CURATION_WORKSPACE_SCHEMA_VERSION
    session: CurationSessionDetail
    candidates: List[CurationCandidate] = Field(default_factory=list)
    action_log: List[CurationActionLogEntry] = Field(default_factory=list)
    navigation: Optional[CurationSessionNavigation] = None


class CreateCurationSessionRequest(CurationWorkspaceBaseModel):
    """Manual session creation request."""

    document_id: UUID
    domain: CurationDomain
    source_kind: CurationSessionSourceKind = CurationSessionSourceKind.MANUAL
    extraction_result_id: Optional[UUID] = None
    notes: Optional[str] = Field(default=None, max_length=4000)


class BootstrapCurationSessionRequest(CurationWorkspaceBaseModel):
    """Document bootstrap request for Review & Curate entry points."""

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
    """Candidate review/update contract used by later decision endpoints."""

    decision: CurationCandidateDecision
    draft: Optional[CurationDraft] = None
    reason: Optional[str] = Field(default=None, max_length=2000)
    advance_queue: bool = True


class CurationCandidateReviewResponse(CurationWorkspaceBaseModel):
    """Candidate review response including updated candidate and queue state."""

    candidate: CurationCandidate
    session: CurationSessionDetail
    next_candidate_id: Optional[UUID] = None


class CurationNextSessionResponse(CurationWorkspaceBaseModel):
    """Queue-mode navigation response for `/sessions/next`."""

    session: Optional[CurationSessionSummary] = None
    navigation: Optional[CurationSessionNavigation] = None


__all__ = [
    "BootstrapCurationSessionRequest",
    "CURATION_WORKSPACE_SCHEMA_VERSION",
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
    "CurationEvidenceSummary",
    "CurationExtractionResultSummary",
    "CurationNextSessionResponse",
    "CurationPagination",
    "CurationReviewProgress",
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
    "CurationSubmissionStatus",
    "CurationSubmissionSummary",
    "CurationUserSummary",
    "CurationValidationSummary",
    "CurationWorkspaceHydrationState",
    "CurationWorkspaceResponse",
    "UpdateCurationSessionRequest",
]
