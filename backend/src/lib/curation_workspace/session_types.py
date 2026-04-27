"""Shared dataclasses and protocols for curation workspace sessions."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol
from uuid import UUID

from src.lib.curation_workspace.models import (
    CurationValidationSnapshot as ValidationSnapshotModel,
)
from src.schemas.curation_workspace import (
    CurationActorType,
    CurationCandidateSource,
    CurationCandidateStatus,
    CurationEvidenceSource,
    CurationSessionStatus,
    CurationValidationScope,
    CurationValidationSnapshotState,
    CurationValidationSummary,
    FieldValidationResult,
)

@dataclass(frozen=True)
class PreparedDraftFieldInput:
    """Deterministic draft-field payload ready for persistence."""

    field_key: str
    label: str
    value: Any | None = None
    seed_value: Any | None = None
    field_type: str | None = None
    group_key: str | None = None
    group_label: str | None = None
    order: int = 0
    required: bool = False
    read_only: bool = False
    dirty: bool = False
    stale_validation: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PreparedEvidenceRecordInput:
    """Deterministic evidence-anchor payload ready for persistence."""

    source: CurationEvidenceSource
    field_keys: list[str] = field(default_factory=list)
    field_group_keys: list[str] = field(default_factory=list)
    is_primary: bool = False
    anchor: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class PreparedValidationSnapshotInput:
    """Validation snapshot payload produced by the deterministic pipeline."""

    scope: CurationValidationScope
    state: CurationValidationSnapshotState
    summary: CurationValidationSummary
    field_results: dict[str, FieldValidationResult] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    requested_at: datetime | None = None
    completed_at: datetime | None = None
    adapter_key: str | None = None


@dataclass(frozen=True)
class CandidateValidationComputation:
    """Validation computation result for one persisted candidate draft."""

    snapshot: PreparedValidationSnapshotInput | None = None
    updated_fields: list[dict[str, Any]] | None = None
    existing_snapshot: ValidationSnapshotModel | None = None


@dataclass(frozen=True)
class PreparedCandidateInput:
    """Prepared candidate payload emitted by the deterministic pipeline."""

    source: CurationCandidateSource
    status: CurationCandidateStatus
    order: int
    adapter_key: str
    display_label: str | None = None
    secondary_label: str | None = None
    conversation_summary: str | None = None
    extraction_result_id: str | None = None
    normalized_payload: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    draft_fields: list[PreparedDraftFieldInput] = field(default_factory=list)
    draft_title: str | None = None
    draft_summary: str | None = None
    draft_notes: str | None = None
    draft_metadata: dict[str, Any] = field(default_factory=dict)
    evidence_records: list[PreparedEvidenceRecordInput] = field(default_factory=list)
    validation_snapshot: PreparedValidationSnapshotInput | None = None


@dataclass(frozen=True)
class PreparedSessionUpsertRequest:
    """Session-level write payload for deterministic prep-session persistence."""

    document_id: str
    adapter_key: str
    review_session_id: str | UUID | None = None
    flow_run_id: str | None = None
    created_by_id: str | None = None
    assigned_curator_id: str | None = None
    notes: str | None = None
    tags: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    prepared_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    status: CurationSessionStatus = CurationSessionStatus.NEW
    candidates: list[PreparedCandidateInput] = field(default_factory=list)
    session_validation_snapshot: PreparedValidationSnapshotInput | None = None
    replace_existing_candidates: bool = True
    session_created_actor_type: CurationActorType = CurationActorType.SYSTEM
    session_created_actor: dict[str, Any] = field(default_factory=dict)
    session_created_message: str = "Deterministic post-agent pipeline created the review session"


@dataclass(frozen=True)
class PreparedSessionUpsertResult:
    """Identifiers returned after deterministic session persistence."""

    session_id: str
    created: bool
    candidate_ids: list[str] = field(default_factory=list)


class CandidateProgressCountsInput(Protocol):
    """Minimal candidate shape required for session progress counters."""

    source: CurationCandidateSource
    status: CurationCandidateStatus


@dataclass(frozen=True)
class ReusablePreparedSessionContext:
    """Existing unreviewed session metadata that is safe to refresh in place."""

    session_id: str
    created_by_id: str | None = None
    assigned_curator_id: str | None = None
    notes: str | None = None
    tags: list[str] = field(default_factory=list)

__all__ = [
    "CandidateProgressCountsInput",
    "CandidateValidationComputation",
    "PreparedCandidateInput",
    "PreparedDraftFieldInput",
    "PreparedEvidenceRecordInput",
    "PreparedSessionUpsertRequest",
    "PreparedSessionUpsertResult",
    "PreparedValidationSnapshotInput",
    "ReusablePreparedSessionContext",
]
