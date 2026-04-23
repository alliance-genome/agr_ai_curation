"""Query and mapping helpers for curation workspace session endpoints."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from math import ceil
from typing import Any, Iterable, Mapping, Protocol, Sequence
from uuid import UUID, uuid4

from fastapi import HTTPException, status
from sqlalchemy import String, asc, case, delete, desc, exists, func, or_, select
from sqlalchemy.orm import Session, selectinload

from src.lib.curation_adapters.entity_tag_bridge import (
    ENTITY_FIELD_KEYS,
    ENTITY_TYPE_FIELD_KEYS,
    SPECIES_FIELD_KEYS,
    TOPIC_FIELD_KEYS,
)
from src.lib.http_errors import raise_sanitized_http_exception
from src.lib.curation_workspace.export_adapters import build_default_export_adapter_registry
from src.lib.curation_workspace.evidence_quality import summarize_evidence_records
from src.lib.curation_workspace.submission_adapters import (
    DIRECT_SUBMISSION_RESULT_STATUSES,
    SubmissionTransportAdapter,
    SubmissionTransportError,
    SubmissionTransportResult,
    build_default_submission_adapter_registry,
    coerce_submission_transport_result,
    normalize_submission_transport_result,
)
from src.lib.curation_workspace.validation_runtime import (
    dedupe,
    field_validation_status,
    increment_validation_count,
)
from src.lib.curation_workspace.models import (
    CurationActionLogEntry as SessionActionLogModel,
    CurationCandidate,
    CurationDraft as DraftModel,
    CurationEvidenceRecord as EvidenceRecordModel,
    CurationExtractionResultRecord as ExtractionResultModel,
    CurationReviewSession as ReviewSessionModel,
    CurationSubmissionRecord as SubmissionModel,
    CurationValidationSnapshot as ValidationSnapshotModel,
)
from src.models.sql.pdf_document import PDFDocument
from src.models.sql.user import User
from src.schemas.curation_workspace import (
    CurationActionLogEntry,
    CurationActionType,
    CurationActorRef,
    CurationActorType,
    CurationAdapterRef,
    CurationCandidate as CurationCandidatePayload,
    CurationCandidateAction,
    CurationCandidateDeleteResponse,
    CurationCandidateDecisionRequest,
    CurationCandidateDecisionResponse,
    CurationCandidateSubmissionReadiness,
    CurationCandidateDraftUpdateRequest,
    CurationCandidateDraftUpdateResponse,
    CurationCandidateValidationRequest,
    CurationCandidateValidationResponse,
    CurationCandidateSource,
    CurationCandidateStatus,
    CurationDraft as CurationDraftPayload,
    CurationDraftField as CurationDraftFieldSchema,
    CurationDocumentRef,
    CurationEntityTag as CurationEntityTagPayload,
    CurationEntityTagDbValidationStatus,
    CurationEntityTagEvidence as CurationEntityTagEvidencePayload,
    CurationEntityTagSource,
    CurationEntityTypeCode,
    CurationEvidenceRecord as CurationEvidenceRecordPayload,
    CurationEvidenceSource,
    CurationEvidenceSummary,
    CurationExtractionResultRecord,
    CurationFlowRunListRequest,
    CurationFlowRunListResponse,
    CurationFlowRunSessionsRequest,
    CurationFlowRunSessionsResponse,
    CurationFlowRunSummary,
    CurationManualCandidateCreateRequest,
    CurationManualCandidateCreateResponse,
    CurationNextSessionRequest,
    CurationNextSessionResponse,
    CurationPageInfo,
    CurationQueueContext,
    CurationQueueNavigationDirection,
    CurationReviewSession,
    CurationSessionFilters,
    CurationSessionListRequest,
    CurationSessionListResponse,
    CurationSessionProgress,
    CurationSessionSortField,
    CurationSessionStats,
    CurationSessionStatsRequest,
    CurationSessionStatsResponse,
    CurationSessionStatus,
    CurationSessionSummary,
    CurationSubmissionExecuteRequest,
    CurationSubmissionExecuteResponse,
    CurationSubmissionHistoryResponse,
    CurationSessionValidationRequest,
    CurationSessionValidationResponse,
    CurationSessionUpdateRequest,
    CurationSessionUpdateResponse,
    CurationSortDirection,
    CurationSubmissionPreviewRequest,
    CurationSubmissionPreviewResponse,
    CurationSubmissionRecord,
    CurationSubmissionRetryRequest,
    CurationSubmissionRetryResponse,
    CurationSubmissionStatus,
    CurationValidationCounts,
    CurationValidationSnapshot as CurationValidationSnapshotSchema,
    CurationValidationScope,
    CurationValidationSnapshotState,
    CurationValidationSummary,
    CurationWorkspace as CurationWorkspacePayload,
    CurationWorkspaceResponse,
    EvidenceAnchor,
    FieldValidationStatus,
    FieldValidationResult,
    SubmissionDomainAdapter,
    SubmissionMode,
    SubmissionPayloadContract,
)


SUMMARY_LOAD_OPTIONS = (
    selectinload(ReviewSessionModel.candidates).selectinload(CurationCandidate.evidence_anchors),
    selectinload(ReviewSessionModel.validation_snapshots),
)

DETAIL_LOAD_OPTIONS = (
    *SUMMARY_LOAD_OPTIONS,
    selectinload(ReviewSessionModel.action_log_entries),
    selectinload(ReviewSessionModel.candidates).selectinload(CurationCandidate.draft),
    selectinload(ReviewSessionModel.candidates).selectinload(CurationCandidate.extraction_result),
    selectinload(ReviewSessionModel.candidates).selectinload(CurationCandidate.validation_snapshots),
    selectinload(ReviewSessionModel.submissions),
)

PREPARED_SESSION_LOAD_OPTIONS = (
    selectinload(ReviewSessionModel.candidates),
    selectinload(ReviewSessionModel.submissions),
    selectinload(ReviewSessionModel.action_log_entries),
)

CANDIDATE_DETAIL_LOAD_OPTIONS = (
    selectinload(CurationCandidate.draft),
    selectinload(CurationCandidate.evidence_anchors),
    selectinload(CurationCandidate.validation_snapshots),
)

logger = logging.getLogger(__name__)

ENTITY_TAG_FIELD_KEYS: tuple[str, ...] = (
    *ENTITY_FIELD_KEYS,
    *ENTITY_TYPE_FIELD_KEYS,
    *SPECIES_FIELD_KEYS,
    *TOPIC_FIELD_KEYS,
)


@lru_cache(maxsize=1)
def _export_adapter_registry():
    return build_default_export_adapter_registry()


@lru_cache(maxsize=1)
def _submission_adapter_registry():
    return build_default_submission_adapter_registry()


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

STATUS_SORT_ORDER = case(
    (ReviewSessionModel.status == CurationSessionStatus.NEW, 1),
    (ReviewSessionModel.status == CurationSessionStatus.IN_PROGRESS, 2),
    (ReviewSessionModel.status == CurationSessionStatus.PAUSED, 3),
    (ReviewSessionModel.status == CurationSessionStatus.READY_FOR_SUBMISSION, 4),
    (ReviewSessionModel.status == CurationSessionStatus.SUBMITTED, 5),
    (ReviewSessionModel.status == CurationSessionStatus.REJECTED, 6),
    else_=99,
)

LATEST_VALIDATION_STATE_SUBQUERY = (
    select(ValidationSnapshotModel.state)
    .where(ValidationSnapshotModel.session_id == ReviewSessionModel.id)
    .order_by(
        ValidationSnapshotModel.completed_at.desc().nulls_last(),
        ValidationSnapshotModel.requested_at.desc().nulls_last(),
        ValidationSnapshotModel.id.desc(),
    )
    .limit(1)
    .scalar_subquery()
)

VALIDATION_STATE_SORT_ORDER = case(
    (LATEST_VALIDATION_STATE_SUBQUERY == CurationValidationSnapshotState.NOT_REQUESTED, 1),
    (LATEST_VALIDATION_STATE_SUBQUERY == CurationValidationSnapshotState.PENDING, 2),
    (LATEST_VALIDATION_STATE_SUBQUERY == CurationValidationSnapshotState.COMPLETED, 3),
    (LATEST_VALIDATION_STATE_SUBQUERY == CurationValidationSnapshotState.FAILED, 4),
    (LATEST_VALIDATION_STATE_SUBQUERY == CurationValidationSnapshotState.STALE, 5),
    else_=99,
)

EVIDENCE_COUNT_SORT_ORDER = (
    select(func.count(EvidenceRecordModel.id))
    .select_from(EvidenceRecordModel)
    .join(CurationCandidate, EvidenceRecordModel.candidate_id == CurationCandidate.id)
    .where(CurationCandidate.session_id == ReviewSessionModel.id)
    .correlate(ReviewSessionModel)
    .scalar_subquery()
)

LIKE_ESCAPE_CHAR = "\\"
SUBMISSION_TRANSPORT_FAILURE_MESSAGE = "Submission failed unexpectedly. Please try again."


def _viewer_url(file_path: str | None) -> str | None:
    if not file_path:
        return None
    return f"/uploads/{file_path.lstrip('/')}"


def _normalize_uuid(value: str | UUID, *, field_name: str) -> UUID:
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid {field_name}: {value}",
        ) from exc


def normalize_uuid(value: str | UUID, *, field_name: str) -> UUID:
    """Public UUID normalization helper shared across curation workspace services."""

    return _normalize_uuid(value, field_name=field_name)


def _normalized_optional_string(value: str | None, *, field_name: str) -> str | None:
    if value is None:
        return None

    normalized = str(value).strip()
    if not normalized:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"{field_name} must not be empty",
        )

    return normalized


def _normalized_required_string(value: str, *, field_name: str) -> str:
    normalized = _normalized_optional_string(value, field_name=field_name)
    if normalized is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"{field_name} is required",
        )
    return normalized


def _ordered_clause(expression: Any, direction: CurationSortDirection, *, nulls_last: bool = False) -> Any:
    ordered = asc(expression) if direction == CurationSortDirection.ASC else desc(expression)
    if nulls_last:
        ordered = ordered.nulls_last()
    return ordered


def _escape_like_pattern(value: str) -> str:
    escaped = value.replace(LIKE_ESCAPE_CHAR, LIKE_ESCAPE_CHAR * 2)
    escaped = escaped.replace("%", f"{LIKE_ESCAPE_CHAR}%")
    escaped = escaped.replace("_", f"{LIKE_ESCAPE_CHAR}_")
    return escaped


def _stable_serialize(value: Any) -> str:
    if value is None:
        return "null"
    return json.dumps(
        value,
        # Draft comparisons should remain stable for unexpected passthrough values
        # rather than failing during dirty-field detection.
        default=str,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _draft_values_equal(left: Any, right: Any) -> bool:
    return _stable_serialize(left) == _stable_serialize(right)


def _latest_snapshot_record(
    snapshots: Sequence[ValidationSnapshotModel],
) -> ValidationSnapshotModel | None:
    if not snapshots:
        return None

    ordered_snapshots = sorted(
        snapshots,
        key=lambda snapshot: snapshot.completed_at
        or snapshot.requested_at
        or datetime.min.replace(tzinfo=timezone.utc),
    )
    return ordered_snapshots[-1]


def _apply_filters(statement: Any, filters: CurationSessionFilters) -> Any:
    if filters.statuses:
        statement = statement.where(ReviewSessionModel.status.in_(filters.statuses))

    if filters.adapter_keys:
        statement = statement.where(ReviewSessionModel.adapter_key.in_(filters.adapter_keys))

    if filters.curator_ids:
        statement = statement.where(ReviewSessionModel.assigned_curator_id.in_(filters.curator_ids))

    if filters.flow_run_id:
        statement = statement.where(ReviewSessionModel.flow_run_id == filters.flow_run_id)

    if filters.origin_session_id:
        statement = statement.where(
            exists(
                select(1)
                .select_from(CurationCandidate)
                .join(
                    ExtractionResultModel,
                    CurationCandidate.extraction_result_id == ExtractionResultModel.id,
                )
                .where(CurationCandidate.session_id == ReviewSessionModel.id)
                .where(ExtractionResultModel.origin_session_id == filters.origin_session_id)
            )
        )

    if filters.document_id:
        statement = statement.where(
            ReviewSessionModel.document_id == _normalize_uuid(
                filters.document_id,
                field_name="document_id",
            )
        )

    if filters.tags:
        statement = statement.where(ReviewSessionModel.tags.contains(filters.tags))

    if filters.prepared_between:
        if filters.prepared_between.from_at is not None:
            statement = statement.where(ReviewSessionModel.prepared_at >= filters.prepared_between.from_at)
        if filters.prepared_between.to_at is not None:
            statement = statement.where(ReviewSessionModel.prepared_at <= filters.prepared_between.to_at)

    if filters.last_worked_between:
        if filters.last_worked_between.from_at is not None:
            statement = statement.where(ReviewSessionModel.last_worked_at >= filters.last_worked_between.from_at)
        if filters.last_worked_between.to_at is not None:
            statement = statement.where(ReviewSessionModel.last_worked_at <= filters.last_worked_between.to_at)

    if filters.search:
        search_value = filters.search.strip()
        if not search_value:
            return statement

        search_term = f"%{_escape_like_pattern(search_value)}%"
        candidate_search = (
            select(CurationCandidate.id)
            .where(CurationCandidate.session_id == ReviewSessionModel.id)
            .where(
                or_(
                    CurationCandidate.display_label.ilike(search_term, escape=LIKE_ESCAPE_CHAR),
                    CurationCandidate.secondary_label.ilike(search_term, escape=LIKE_ESCAPE_CHAR),
                )
            )
        )
        statement = statement.where(
            or_(
                func.cast(ReviewSessionModel.id, String).ilike(search_term, escape=LIKE_ESCAPE_CHAR),
                func.cast(ReviewSessionModel.document_id, String).ilike(
                    search_term,
                    escape=LIKE_ESCAPE_CHAR,
                ),
                func.coalesce(PDFDocument.title, PDFDocument.filename).ilike(
                    search_term,
                    escape=LIKE_ESCAPE_CHAR,
                ),
                PDFDocument.filename.ilike(search_term, escape=LIKE_ESCAPE_CHAR),
                ReviewSessionModel.flow_run_id.ilike(search_term, escape=LIKE_ESCAPE_CHAR),
                exists(candidate_search),
            )
        )

    return statement


def _sort_order_clauses(
    sort_by: CurationSessionSortField,
    sort_direction: CurationSortDirection,
) -> tuple[Any, ...]:
    if sort_by == CurationSessionSortField.PREPARED_AT:
        return (
            _ordered_clause(ReviewSessionModel.prepared_at, sort_direction, nulls_last=True),
            ReviewSessionModel.id.asc(),
        )
    if sort_by == CurationSessionSortField.LAST_WORKED_AT:
        return (
            _ordered_clause(ReviewSessionModel.last_worked_at, sort_direction, nulls_last=True),
            ReviewSessionModel.prepared_at.desc(),
            ReviewSessionModel.id.asc(),
        )
    if sort_by == CurationSessionSortField.STATUS:
        return (
            _ordered_clause(STATUS_SORT_ORDER, sort_direction),
            ReviewSessionModel.prepared_at.desc(),
            ReviewSessionModel.id.asc(),
        )
    if sort_by == CurationSessionSortField.DOCUMENT_TITLE:
        return (
            _ordered_clause(
                func.lower(func.coalesce(PDFDocument.title, PDFDocument.filename)),
                sort_direction,
            ),
            ReviewSessionModel.prepared_at.desc(),
            ReviewSessionModel.id.asc(),
        )
    if sort_by == CurationSessionSortField.ADAPTER:
        return (
            _ordered_clause(func.lower(func.coalesce(ReviewSessionModel.adapter_key, "")), sort_direction),
            ReviewSessionModel.prepared_at.desc(),
            ReviewSessionModel.id.asc(),
        )
    if sort_by == CurationSessionSortField.CANDIDATE_COUNT:
        return (
            _ordered_clause(ReviewSessionModel.total_candidates, sort_direction),
            ReviewSessionModel.prepared_at.desc(),
            ReviewSessionModel.id.asc(),
        )
    if sort_by == CurationSessionSortField.VALIDATION:
        return (
            _ordered_clause(VALIDATION_STATE_SORT_ORDER, sort_direction, nulls_last=True),
            ReviewSessionModel.prepared_at.desc(),
            ReviewSessionModel.id.asc(),
        )
    if sort_by == CurationSessionSortField.EVIDENCE:
        return (
            _ordered_clause(EVIDENCE_COUNT_SORT_ORDER, sort_direction, nulls_last=True),
            ReviewSessionModel.prepared_at.desc(),
            ReviewSessionModel.id.asc(),
        )
    if sort_by == CurationSessionSortField.CURATOR:
        return (
            _ordered_clause(
                func.lower(func.coalesce(ReviewSessionModel.assigned_curator_id, "")),
                sort_direction,
            ),
            ReviewSessionModel.prepared_at.desc(),
            ReviewSessionModel.id.asc(),
        )
    return (ReviewSessionModel.prepared_at.desc(), ReviewSessionModel.id.asc())


def _filtered_session_id_select(filters: CurationSessionFilters) -> Any:
    statement = (
        select(ReviewSessionModel.id)
        .select_from(ReviewSessionModel)
        .join(PDFDocument, PDFDocument.id == ReviewSessionModel.document_id)
    )
    return _apply_filters(statement, filters)


def _ordered_session_id_select(
    filters: CurationSessionFilters,
    sort_by: CurationSessionSortField,
    sort_direction: CurationSortDirection,
) -> Any:
    return _filtered_session_id_select(filters).order_by(
        *_sort_order_clauses(sort_by, sort_direction)
    )


def _ordered_session_queue_subquery(
    filters: CurationSessionFilters,
    sort_by: CurationSessionSortField,
    sort_direction: CurationSortDirection,
) -> Any:
    order_by = _sort_order_clauses(sort_by, sort_direction)
    return (
        _filtered_session_id_select(filters)
        .add_columns(
            func.row_number().over(order_by=order_by).label("position"),
            func.count().over().label("total_sessions"),
            func.cast(
                func.lag(ReviewSessionModel.id).over(order_by=order_by),
                String,
            ).label("previous_session_id"),
            func.cast(
                func.lead(ReviewSessionModel.id).over(order_by=order_by),
                String,
            ).label("next_session_id"),
        )
        .subquery()
    )


def _load_documents(db: Session, document_ids: Iterable[UUID]) -> dict[UUID, PDFDocument]:
    ids = list({document_id for document_id in document_ids})
    if not ids:
        return {}
    documents = db.scalars(select(PDFDocument).where(PDFDocument.id.in_(ids))).all()
    return {document.id: document for document in documents}


def _load_users(db: Session, actor_ids: Iterable[str | None]) -> dict[str, User]:
    ids = sorted({actor_id for actor_id in actor_ids if actor_id})
    if not ids:
        return {}
    users = db.scalars(select(User).where(User.auth_sub.in_(ids))).all()
    return {user.auth_sub: user for user in users}


def _actor_ref(user_map: dict[str, User], actor_id: str | None) -> CurationActorRef | None:
    if not actor_id:
        return None
    user = user_map.get(actor_id)
    if user is None:
        return CurationActorRef(actor_id=actor_id)
    return CurationActorRef(
        actor_id=user.auth_sub,
        display_name=user.display_name or user.email or user.auth_sub,
        email=user.email,
    )


def _adapter_ref(
    session: ReviewSessionModel,
    *,
    metadata: Mapping[str, Any] | None = None,
) -> CurationAdapterRef:
    display_label = session.adapter_key.replace("_", " ").title()
    return CurationAdapterRef(
        adapter_key=session.adapter_key,
        display_label=display_label,
        metadata=dict(metadata or {}),
    )


def _document_ref(document: PDFDocument | None) -> CurationDocumentRef:
    if document is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Session document metadata is missing",
        )

    viewer_url = _viewer_url(document.file_path)
    return CurationDocumentRef(
        document_id=str(document.id),
        title=document.title or document.filename,
        pdf_url=viewer_url,
        viewer_url=viewer_url,
        page_count=document.page_count,
    )


def _session_progress(session: ReviewSessionModel) -> CurationSessionProgress:
    return CurationSessionProgress(
        total_candidates=session.total_candidates,
        reviewed_candidates=session.reviewed_candidates,
        pending_candidates=session.pending_candidates,
        accepted_candidates=session.accepted_candidates,
        rejected_candidates=session.rejected_candidates,
        manual_candidates=session.manual_candidates,
    )


def _validation_summary(
    snapshots: Sequence[ValidationSnapshotModel],
) -> CurationValidationSummary | None:
    snapshot = _latest_snapshot_record(snapshots)
    if snapshot is None:
        return None

    summary_payload = dict(snapshot.summary or {})
    summary_payload.setdefault("state", snapshot.state)
    summary_payload.setdefault("counts", {})
    summary_payload.setdefault("warnings", list(snapshot.warnings or []))
    summary_payload.setdefault("stale_field_keys", [])
    summary_payload.setdefault("last_validated_at", snapshot.completed_at)
    try:
        return CurationValidationSummary.model_validate(summary_payload)
    except Exception:
        return None


def _latest_validation_summary(session: ReviewSessionModel) -> CurationValidationSummary | None:
    session_level_snapshots = [
        snapshot
        for snapshot in session.validation_snapshots
        if snapshot.candidate_id is None
    ]
    return _validation_summary(session_level_snapshots or list(session.validation_snapshots))


def _evidence_summary_from_records(
    records: Sequence[EvidenceRecordModel],
) -> CurationEvidenceSummary | None:
    return summarize_evidence_records(records)
def _evidence_summary(session: ReviewSessionModel) -> CurationEvidenceSummary | None:
    return _evidence_summary_from_records(
        [
            evidence_anchor
            for candidate in session.candidates
            for evidence_anchor in candidate.evidence_anchors
        ]
    )


def _draft_detail(record: DraftModel | None) -> CurationDraftPayload | None:
    if record is None:
        return None

    return CurationDraftPayload(
        draft_id=str(record.id),
        candidate_id=str(record.candidate_id),
        adapter_key=record.adapter_key,
        version=record.version,
        title=record.title,
        summary=record.summary,
        fields=[
            CurationDraftFieldSchema.model_validate(field_payload)
            for field_payload in (record.fields or [])
        ],
        notes=record.notes,
        created_at=record.created_at,
        updated_at=record.updated_at,
        last_saved_at=record.last_saved_at,
        metadata=dict(record.draft_metadata or {}),
    )


def _evidence_record(record: EvidenceRecordModel) -> CurationEvidenceRecordPayload:
    return CurationEvidenceRecordPayload(
        anchor_id=str(record.id),
        candidate_id=str(record.candidate_id),
        source=record.source,
        field_keys=list(record.field_keys or []),
        field_group_keys=list(record.field_group_keys or []),
        is_primary=record.is_primary,
        anchor=EvidenceAnchor.model_validate(record.anchor or {}),
        created_at=record.created_at,
        updated_at=record.updated_at,
        warnings=list(record.warnings or []),
    )

def build_evidence_record(record: EvidenceRecordModel) -> CurationEvidenceRecordPayload:
    """Public evidence-record serializer shared across curation workspace services."""

    return _evidence_record(record)


def _validation_snapshot(record: ValidationSnapshotModel) -> CurationValidationSnapshotSchema:
    summary_payload = dict(record.summary or {})
    summary_payload.setdefault("state", record.state)
    summary_payload.setdefault("counts", {})
    summary_payload.setdefault("warnings", list(record.warnings or []))
    summary_payload.setdefault("stale_field_keys", [])
    summary_payload.setdefault("last_validated_at", record.completed_at)

    return CurationValidationSnapshotSchema(
        snapshot_id=str(record.id),
        scope=record.scope,
        session_id=str(record.session_id),
        candidate_id=str(record.candidate_id) if record.candidate_id else None,
        adapter_key=record.adapter_key,
        state=record.state,
        field_results={
            field_key: FieldValidationResult.model_validate(result_payload)
            for field_key, result_payload in (record.field_results or {}).items()
        },
        summary=CurationValidationSummary.model_validate(summary_payload),
        requested_at=record.requested_at,
        completed_at=record.completed_at,
        warnings=list(record.warnings or []),
    )


def _candidate_detail(candidate: CurationCandidate) -> CurationCandidatePayload:
    draft = _draft_detail(candidate.draft)
    if draft is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Candidate {candidate.id} draft is missing",
        )

    ordered_evidence = sorted(
        candidate.evidence_anchors,
        key=lambda evidence_record: (
            evidence_record.created_at,
            evidence_record.updated_at,
            evidence_record.id,
        ),
    )

    return CurationCandidatePayload(
        candidate_id=str(candidate.id),
        session_id=str(candidate.session_id),
        source=candidate.source,
        status=candidate.status,
        order=candidate.order,
        adapter_key=candidate.adapter_key,
        display_label=candidate.display_label,
        secondary_label=candidate.secondary_label,
        conversation_summary=candidate.conversation_summary,
        extraction_result_id=(
            str(candidate.extraction_result_id) if candidate.extraction_result_id else None
        ),
        normalized_payload=dict(candidate.normalized_payload or {}),
        draft=draft,
        evidence_anchors=[_evidence_record(record) for record in ordered_evidence],
        validation=_validation_summary(candidate.validation_snapshots),
        evidence_summary=_evidence_summary_from_records(candidate.evidence_anchors),
        created_at=candidate.created_at,
        updated_at=candidate.updated_at,
        last_reviewed_at=candidate.last_reviewed_at,
        metadata=dict(candidate.candidate_metadata or {}),
    )


def _submission_payload(record: SubmissionModel) -> SubmissionPayloadContract | None:
    if record.payload is None:
        return None

    if _stored_submission_payload_contract(record.payload):
        payload_json = record.payload.get("payload_json")
        payload_text = record.payload.get("payload_text")
        candidate_ids = [
            candidate_id
            for candidate_id in record.payload.get("candidate_ids", [])
            if isinstance(candidate_id, str) and candidate_id
        ]
        content_type = record.payload.get("content_type")
        filename = record.payload.get("filename")
        payload_warnings = [
            warning
            for warning in record.payload.get("warnings", [])
            if isinstance(warning, str) and warning
        ]
    else:
        payload_json = record.payload if not isinstance(record.payload, str) else None
        payload_text = record.payload if isinstance(record.payload, str) else None
        candidate_ids = [
            readiness.get("candidate_id")
            for readiness in record.readiness or []
            if isinstance(readiness, dict) and readiness.get("candidate_id")
        ]
        content_type = None
        filename = None
        payload_warnings = list(record.warnings or [])

    return SubmissionPayloadContract(
        mode=record.mode,
        target_key=record.target_key,
        adapter_key=record.adapter_key,
        candidate_ids=candidate_ids,
        payload_json=payload_json,
        payload_text=payload_text,
        content_type=content_type,
        filename=filename,
        warnings=payload_warnings,
    )


def _stored_submission_payload_contract(payload: object) -> bool:
    return isinstance(payload, dict) and payload.get("storage_kind") == "submission_payload_contract"


def _serialize_submission_payload_contract(payload: SubmissionPayloadContract) -> dict[str, Any]:
    return {
        "storage_kind": "submission_payload_contract",
        "candidate_ids": list(payload.candidate_ids),
        "payload_json": payload.payload_json,
        "payload_text": payload.payload_text,
        "content_type": payload.content_type,
        "filename": payload.filename,
        "warnings": list(payload.warnings),
    }


def _submission_payload_model_input(
    payload: SubmissionPayloadContract | None,
) -> dict[str, Any] | None:
    """Normalize nested payload models before embedding them in larger response schemas."""

    if payload is None:
        return None
    return payload.model_dump()


def _submission_record(record: SubmissionModel) -> CurationSubmissionRecord:
    return CurationSubmissionRecord(
        submission_id=str(record.id),
        session_id=str(record.session_id),
        adapter_key=record.adapter_key,
        mode=record.mode,
        target_key=record.target_key,
        status=record.status,
        readiness=list(record.readiness or []),
        payload=_submission_payload_model_input(_submission_payload(record)),
        requested_at=record.requested_at,
        completed_at=record.completed_at,
        external_reference=record.external_reference,
        response_message=record.response_message,
        validation_errors=list(record.validation_errors or []),
        warnings=list(record.warnings or []),
    )


def _extraction_records(session: ReviewSessionModel) -> list[CurationExtractionResultRecord]:
    extraction_results: list[CurationExtractionResultRecord] = []
    seen_ids: set[UUID] = set()

    for candidate in session.candidates:
        extraction_result = candidate.extraction_result
        if extraction_result is None or extraction_result.id in seen_ids:
            continue
        seen_ids.add(extraction_result.id)
        extraction_results.append(
            CurationExtractionResultRecord(
                extraction_result_id=str(extraction_result.id),
                document_id=str(extraction_result.document_id),
                adapter_key=extraction_result.adapter_key,
                agent_key=extraction_result.agent_key,
                source_kind=extraction_result.source_kind,
                origin_session_id=extraction_result.origin_session_id,
                trace_id=extraction_result.trace_id,
                flow_run_id=extraction_result.flow_run_id,
                user_id=extraction_result.user_id,
                candidate_count=extraction_result.candidate_count,
                conversation_summary=extraction_result.conversation_summary,
                payload_json=extraction_result.payload_json,
                created_at=extraction_result.created_at,
                metadata=dict(extraction_result.extraction_metadata or {}),
            )
        )

    return extraction_results


def _session_summary(
    session: ReviewSessionModel,
    document_map: dict[UUID, PDFDocument],
    user_map: dict[str, User],
) -> CurationSessionSummary:
    return CurationSessionSummary(
        session_id=str(session.id),
        status=session.status,
        adapter=_adapter_ref(session),
        document=_document_ref(document_map.get(session.document_id)),
        flow_run_id=session.flow_run_id,
        progress=_session_progress(session),
        validation=_latest_validation_summary(session),
        evidence=_evidence_summary(session),
        current_candidate_id=str(session.current_candidate_id) if session.current_candidate_id else None,
        assigned_curator=_actor_ref(user_map, session.assigned_curator_id),
        created_by=_actor_ref(user_map, session.created_by_id),
        prepared_at=session.prepared_at,
        last_worked_at=session.last_worked_at,
        notes=session.notes,
        warnings=list(session.warnings or []),
        tags=list(session.tags or []),
    )


def _session_detail(
    db: Session,
    session: ReviewSessionModel,
    document_map: dict[UUID, PDFDocument],
    user_map: dict[str, User],
) -> CurationReviewSession:
    summary = _session_summary(session, document_map, user_map)
    latest_submission = _submission_record(session.submissions[-1]) if session.submissions else None
    summary_payload = summary.model_dump()
    summary_payload["adapter"] = _adapter_ref(session)

    return CurationReviewSession(
        **summary_payload,
        session_version=session.session_version,
        extraction_results=_extraction_records(session),
        latest_submission=latest_submission,
        submitted_at=session.submitted_at,
        paused_at=session.paused_at,
        rejection_reason=session.rejection_reason,
    )


def _action_log_entry(record: SessionActionLogModel | None) -> CurationActionLogEntry | None:
    if record is None:
        return None

    actor = None
    if record.actor:
        try:
            actor = CurationActorRef.model_validate(record.actor)
        except Exception:
            actor = CurationActorRef(actor_id=record.actor.get("actor_id"))

    return CurationActionLogEntry(
        action_id=str(record.id),
        session_id=str(record.session_id),
        candidate_id=str(record.candidate_id) if record.candidate_id else None,
        draft_id=str(record.draft_id) if record.draft_id else None,
        action_type=record.action_type,
        actor_type=record.actor_type,
        actor=actor,
        occurred_at=record.occurred_at,
        previous_session_status=record.previous_session_status,
        new_session_status=record.new_session_status,
        previous_candidate_status=record.previous_candidate_status,
        new_candidate_status=record.new_candidate_status,
        changed_field_keys=list(record.changed_field_keys or []),
        evidence_anchor_ids=[str(anchor_id) for anchor_id in record.evidence_anchor_ids or []],
        reason=record.reason,
        message=record.message,
        metadata=dict(record.action_metadata or {}),
    )


def build_action_log_entry(
    record: SessionActionLogModel | None,
) -> CurationActionLogEntry | None:
    """Public action-log serializer shared across curation workspace services."""

    return _action_log_entry(record)

def _draft_payload(candidate: CurationCandidate) -> CurationDraftPayload:
    if candidate.draft is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Curation candidate {candidate.id} is missing its draft payload",
        )

    return CurationDraftPayload(
        draft_id=str(candidate.draft.id),
        candidate_id=str(candidate.draft.candidate_id),
        adapter_key=candidate.draft.adapter_key,
        version=candidate.draft.version,
        title=candidate.draft.title,
        summary=candidate.draft.summary,
        fields=list(candidate.draft.fields or []),
        notes=candidate.draft.notes,
        created_at=candidate.draft.created_at,
        updated_at=candidate.draft.updated_at,
        last_saved_at=candidate.draft.last_saved_at,
        metadata=dict(candidate.draft.draft_metadata or {}),
    )


def _candidate_validation_summary(candidate: CurationCandidate) -> CurationValidationSummary | None:
    return _validation_summary(candidate.validation_snapshots)


def _candidate_evidence_record(record: EvidenceRecordModel) -> CurationEvidenceRecordPayload:
    return CurationEvidenceRecordPayload(
        anchor_id=str(record.id),
        candidate_id=str(record.candidate_id),
        source=record.source,
        field_keys=list(record.field_keys or []),
        field_group_keys=list(record.field_group_keys or []),
        is_primary=record.is_primary,
        anchor=dict(record.anchor or {}),
        created_at=record.created_at,
        updated_at=record.updated_at,
        warnings=list(record.warnings or []),
    )


def _normalize_entity_field_key(value: str) -> str:
    return value.strip().lower()


def _matches_entity_field(
    draft_field: CurationDraftFieldSchema,
    accepted_keys: Sequence[str],
) -> bool:
    field_key = _normalize_entity_field_key(draft_field.field_key)
    field_label = _normalize_entity_field_key(draft_field.label)
    return any(
        field_key == _normalize_entity_field_key(accepted_key)
        or field_label == _normalize_entity_field_key(accepted_key)
        for accepted_key in accepted_keys
    )


def _find_entity_field(
    fields: Sequence[CurationDraftFieldSchema],
    accepted_keys: Sequence[str],
) -> CurationDraftFieldSchema | None:
    for draft_field in fields:
        if _matches_entity_field(draft_field, accepted_keys):
            return draft_field
    return None


def _candidate_has_entity_tag_fields(candidate: CurationCandidatePayload) -> bool:
    return any(
        _matches_entity_field(draft_field, ENTITY_TAG_FIELD_KEYS)
        for draft_field in candidate.draft.fields
    )


def _read_required_entity_string(
    field: CurationDraftFieldSchema,
    candidate_id: str,
) -> str:
    if not isinstance(field.value, str) or not field.value.strip():
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                f"Curation candidate {candidate_id} is missing a required string value "
                f"for {field.field_key} in the entity-tag payload"
            ),
        )

    return field.value.strip()


def _read_optional_entity_string(
    field: CurationDraftFieldSchema | None,
    candidate_id: str,
) -> str:
    if field is None or field.value is None:
        return ""

    if not isinstance(field.value, str):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                f"Curation candidate {candidate_id} has a non-string value "
                f"for {field.field_key} in the entity-tag payload"
            ),
        )

    return field.value.strip()


def _resolve_entity_name_field(candidate: CurationCandidatePayload) -> CurationDraftFieldSchema:
    entity_field = _find_entity_field(candidate.draft.fields, ENTITY_FIELD_KEYS)
    if entity_field is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                f"Curation candidate {candidate.candidate_id} is missing an entity-name field "
                "required for the entity-tag payload"
            ),
        )

    return entity_field


def _entity_type_code(
    candidate: CurationCandidatePayload,
) -> CurationEntityTypeCode:
    type_field = _find_entity_field(candidate.draft.fields, ENTITY_TYPE_FIELD_KEYS)
    if type_field is not None and type_field.value is not None:
        if not isinstance(type_field.value, str):
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=(
                    f"Curation candidate {candidate.candidate_id} has a non-string entity type "
                    "value in the entity-tag payload"
                ),
            )

        normalized_type = type_field.value.strip()
        if not normalized_type:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=(
                    f"Curation candidate {candidate.candidate_id} has a blank entity type "
                    "identifier in the entity-tag payload"
                ),
            )

        return normalized_type

    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail=(
            f"Curation candidate {candidate.candidate_id} is missing an entity type "
            "required for the entity-tag payload"
        ),
    )


def _entity_db_status(
    candidate: CurationCandidatePayload,
    entity_field: CurationDraftFieldSchema,
) -> CurationEntityTagDbValidationStatus:
    field_status = entity_field.validation_result.status if entity_field.validation_result else None
    if field_status is FieldValidationStatus.VALIDATED:
        return CurationEntityTagDbValidationStatus.VALIDATED
    if field_status is FieldValidationStatus.AMBIGUOUS:
        return CurationEntityTagDbValidationStatus.AMBIGUOUS
    if field_status is FieldValidationStatus.NOT_FOUND:
        return CurationEntityTagDbValidationStatus.NOT_FOUND
    if field_status is FieldValidationStatus.SKIPPED:
        return CurationEntityTagDbValidationStatus.NOT_FOUND
    if field_status is not None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                f"Curation candidate {candidate.candidate_id} has unsupported validation status "
                f"{field_status.value!r} for the entity-tag payload"
            ),
        )

    summary = candidate.validation
    if summary is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                f"Curation candidate {candidate.candidate_id} is missing validation data "
                "required for the entity-tag payload"
            ),
        )

    if summary.counts.not_found > 0 or summary.counts.invalid_format > 0:
        return CurationEntityTagDbValidationStatus.NOT_FOUND
    if summary.counts.ambiguous > 0 or summary.counts.conflict > 0:
        return CurationEntityTagDbValidationStatus.AMBIGUOUS
    if summary.counts.validated > 0:
        return CurationEntityTagDbValidationStatus.VALIDATED
    if summary.counts.skipped > 0:
        return CurationEntityTagDbValidationStatus.NOT_FOUND

    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail=(
            f"Curation candidate {candidate.candidate_id} does not have a usable validation "
            "status for the entity-tag payload"
        ),
    )


def _entity_db_identifier(entity_field: CurationDraftFieldSchema) -> str | None:
    validation_result = entity_field.validation_result
    if validation_result is None or not validation_result.candidate_matches:
        return None

    identifier = validation_result.candidate_matches[0].identifier
    if not isinstance(identifier, str) or not identifier.strip():
        return None

    return identifier.strip()


def _entity_evidence(
    candidate: CurationCandidatePayload,
) -> CurationEntityTagEvidencePayload | None:
    primary_evidence = next(
        (anchor for anchor in candidate.evidence_anchors if anchor.is_primary),
        None,
    )
    evidence_record = primary_evidence or (candidate.evidence_anchors[0] if candidate.evidence_anchors else None)
    if evidence_record is None:
        return None

    anchor = evidence_record.anchor
    sentence_text = None
    if isinstance(anchor.sentence_text, str) and anchor.sentence_text.strip():
        sentence_text = anchor.sentence_text.strip()
    elif isinstance(anchor.snippet_text, str) and anchor.snippet_text.strip():
        sentence_text = anchor.snippet_text.strip()

    if sentence_text is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                f"Curation candidate {candidate.candidate_id} has evidence without sentence or "
                "snippet text required for the entity-tag payload"
            ),
        )

    return CurationEntityTagEvidencePayload(
        sentence_text=sentence_text,
        page_number=anchor.page_number,
        section_title=anchor.section_title,
        chunk_ids=list(anchor.chunk_ids or []),
    )


def _entity_tag_payload(candidate: CurationCandidatePayload) -> CurationEntityTagPayload:
    entity_field = _resolve_entity_name_field(candidate)

    return CurationEntityTagPayload(
        tag_id=candidate.candidate_id,
        entity_name=_read_required_entity_string(entity_field, candidate.candidate_id),
        entity_type=_entity_type_code(candidate),
        species=_read_optional_entity_string(
            _find_entity_field(candidate.draft.fields, SPECIES_FIELD_KEYS),
            candidate.candidate_id,
        ),
        topic=_read_optional_entity_string(
            _find_entity_field(candidate.draft.fields, TOPIC_FIELD_KEYS),
            candidate.candidate_id,
        ),
        db_status=_entity_db_status(candidate, entity_field),
        db_entity_id=_entity_db_identifier(entity_field),
        source=(
            CurationEntityTagSource.MANUAL
            if candidate.source is CurationCandidateSource.MANUAL
            else CurationEntityTagSource.AI
        ),
        decision=candidate.status,
        evidence=_entity_evidence(candidate),
        notes=candidate.draft.notes,
    )


def _candidate_payload(candidate: CurationCandidate) -> CurationCandidatePayload:
    evidence_records = [
        _candidate_evidence_record(record)
        for record in candidate.evidence_anchors
    ]
    return CurationCandidatePayload(
        candidate_id=str(candidate.id),
        session_id=str(candidate.session_id),
        source=candidate.source,
        status=candidate.status,
        order=candidate.order,
        adapter_key=candidate.adapter_key,
        display_label=candidate.display_label,
        secondary_label=candidate.secondary_label,
        conversation_summary=candidate.conversation_summary,
        extraction_result_id=(
            str(candidate.extraction_result_id)
            if candidate.extraction_result_id is not None
            else None
        ),
        draft=_draft_payload(candidate),
        evidence_anchors=evidence_records,
        validation=_candidate_validation_summary(candidate),
        evidence_summary=_evidence_summary_from_records(candidate.evidence_anchors),
        created_at=candidate.created_at,
        updated_at=candidate.updated_at,
        last_reviewed_at=candidate.last_reviewed_at,
        metadata=dict(candidate.candidate_metadata or {}),
    )
def _page_info(*, page: int, page_size: int, total_items: int) -> CurationPageInfo:
    total_pages = ceil(total_items / page_size) if total_items else 0
    return CurationPageInfo(
        page=page,
        page_size=page_size,
        total_items=total_items,
        total_pages=total_pages,
        has_next_page=page < total_pages,
        has_previous_page=page > 1 and total_pages > 0,
    )


def _load_sessions_by_ids(
    db: Session,
    session_ids: Sequence[UUID],
    *,
    detailed: bool,
) -> list[ReviewSessionModel]:
    if not session_ids:
        return []

    load_options = DETAIL_LOAD_OPTIONS if detailed else SUMMARY_LOAD_OPTIONS
    sessions = db.scalars(
        select(ReviewSessionModel)
        .where(ReviewSessionModel.id.in_(session_ids))
        .options(*load_options)
    ).all()
    session_map = {session.id: session for session in sessions}
    return [session_map[session_id] for session_id in session_ids if session_id in session_map]


def _session_context_maps(
    db: Session,
    sessions: Sequence[ReviewSessionModel],
) -> tuple[dict[UUID, PDFDocument], dict[str, User]]:
    document_map = _load_documents(db, [session.document_id for session in sessions])
    user_map = _load_users(
        db,
        [
            actor_id
            for session in sessions
            for actor_id in (session.assigned_curator_id, session.created_by_id)
        ],
    )
    return document_map, user_map


def _flow_run_summary_statement(filters: CurationSessionFilters) -> Any:
    filtered_ids_subquery = _filtered_session_id_select(filters).subquery()
    last_activity_at = func.max(
        func.coalesce(ReviewSessionModel.last_worked_at, ReviewSessionModel.prepared_at)
    ).label("last_activity_at")
    return (
        select(
            ReviewSessionModel.flow_run_id.label("flow_run_id"),
            func.count(ReviewSessionModel.id).label("session_count"),
            func.count(ReviewSessionModel.id)
            .filter(ReviewSessionModel.reviewed_candidates > 0)
            .label("reviewed_count"),
            func.count(ReviewSessionModel.id)
            .filter(ReviewSessionModel.pending_candidates > 0)
            .label("pending_count"),
            func.count(ReviewSessionModel.id)
            .filter(ReviewSessionModel.status == CurationSessionStatus.SUBMITTED)
            .label("submitted_count"),
            last_activity_at,
        )
        .where(ReviewSessionModel.id.in_(select(filtered_ids_subquery.c.id)))
        .where(ReviewSessionModel.flow_run_id.is_not(None))
        .group_by(ReviewSessionModel.flow_run_id)
        .order_by(last_activity_at.desc(), ReviewSessionModel.flow_run_id.asc())
    )


def _flow_run_summaries(db: Session, filters: CurationSessionFilters) -> list[CurationFlowRunSummary]:
    group_rows = db.execute(_flow_run_summary_statement(filters)).mappings().all()
    return [_flow_run_summary_from_row(row) for row in group_rows]


def _flow_run_summary_from_row(row: Mapping[str, Any]) -> CurationFlowRunSummary:
    return CurationFlowRunSummary(
        flow_run_id=row["flow_run_id"],
        display_label=row["flow_run_id"],
        session_count=row["session_count"],
        reviewed_count=row["reviewed_count"],
        pending_count=row["pending_count"],
        submitted_count=row["submitted_count"],
        last_activity_at=row["last_activity_at"],
    )


def _flow_run_summary(db: Session, filters: CurationSessionFilters) -> CurationFlowRunSummary | None:
    if not filters.flow_run_id:
        return None

    filtered_ids_subquery = _filtered_session_id_select(filters).subquery()
    last_activity_at = func.max(
        func.coalesce(ReviewSessionModel.last_worked_at, ReviewSessionModel.prepared_at)
    ).label("last_activity_at")
    row = db.execute(
        select(
            func.max(ReviewSessionModel.flow_run_id).label("flow_run_id"),
            func.count(ReviewSessionModel.id).label("session_count"),
            func.count(ReviewSessionModel.id)
            .filter(ReviewSessionModel.reviewed_candidates > 0)
            .label("reviewed_count"),
            func.count(ReviewSessionModel.id)
            .filter(ReviewSessionModel.pending_candidates > 0)
            .label("pending_count"),
            func.count(ReviewSessionModel.id)
            .filter(ReviewSessionModel.status == CurationSessionStatus.SUBMITTED)
            .label("submitted_count"),
            last_activity_at,
        )
        .where(ReviewSessionModel.id.in_(select(filtered_ids_subquery.c.id)))
        .where(ReviewSessionModel.flow_run_id.is_not(None))
    ).mappings().one()

    if row["session_count"] == 0 or row["flow_run_id"] is None:
        return None

    return _flow_run_summary_from_row(row)


def _list_session_summaries(
    db: Session,
    *,
    filters: CurationSessionFilters,
    sort_by: CurationSessionSortField,
    sort_direction: CurationSortDirection,
    page: int,
    page_size: int,
    total_items: int | None = None,
) -> tuple[list[CurationSessionSummary], CurationPageInfo]:
    filtered_ids_select = _filtered_session_id_select(filters)
    if total_items is None:
        total_items = db.scalar(select(func.count()).select_from(filtered_ids_select.subquery())) or 0
    ordered_id_select = _ordered_session_id_select(filters, sort_by, sort_direction)
    offset = (page - 1) * page_size
    session_ids = list(db.scalars(ordered_id_select.offset(offset).limit(page_size)).all())
    sessions = _load_sessions_by_ids(db, session_ids, detailed=False)
    document_map, user_map = _session_context_maps(db, sessions)
    summaries = [_session_summary(session, document_map, user_map) for session in sessions]
    return summaries, _page_info(page=page, page_size=page_size, total_items=total_items)


def list_sessions(db: Session, request: CurationSessionListRequest) -> CurationSessionListResponse:
    summaries, page_info = _list_session_summaries(
        db,
        filters=request.filters,
        sort_by=request.sort_by,
        sort_direction=request.sort_direction,
        page=request.page,
        page_size=request.page_size,
    )

    flow_run_groups: list[CurationFlowRunSummary] = []
    if request.group_by_flow_run:
        flow_run_groups = _flow_run_summaries(db, request.filters)

    return CurationSessionListResponse(
        sessions=summaries,
        page_info=page_info,
        applied_filters=request.filters,
        sort_by=request.sort_by,
        sort_direction=request.sort_direction,
        flow_run_groups=flow_run_groups,
    )


def list_flow_runs(db: Session, request: CurationFlowRunListRequest) -> CurationFlowRunListResponse:
    return CurationFlowRunListResponse(
        flow_runs=_flow_run_summaries(db, request.filters),
        applied_filters=request.filters,
    )


def list_flow_run_sessions(
    db: Session,
    request: CurationFlowRunSessionsRequest,
) -> CurationFlowRunSessionsResponse:
    filters = request.filters.model_copy(update={"flow_run_id": request.flow_run_id})
    flow_run = _flow_run_summary(db, filters)
    if flow_run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Flow run {request.flow_run_id} not found",
        )

    summaries, page_info = _list_session_summaries(
        db,
        filters=filters,
        sort_by=CurationSessionSortField.PREPARED_AT,
        sort_direction=CurationSortDirection.DESC,
        page=request.page,
        page_size=request.page_size,
        total_items=flow_run.session_count,
    )

    return CurationFlowRunSessionsResponse(
        flow_run=flow_run,
        sessions=summaries,
        page_info=page_info,
    )


def get_session_detail(db: Session, session_id: str | UUID) -> CurationReviewSession:
    normalized_session_id = _normalize_uuid(session_id, field_name="session_id")
    sessions = _load_sessions_by_ids(db, [normalized_session_id], detailed=True)
    if not sessions:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Curation review session {normalized_session_id} not found",
        )
    session = sessions[0]
    document_map, user_map = _session_context_maps(db, [session])
    return _session_detail(db, session, document_map, user_map)


def get_session_workspace(db: Session, session_id: str | UUID) -> CurationWorkspaceResponse:
    normalized_session_id = _normalize_uuid(session_id, field_name="session_id")
    sessions = _load_sessions_by_ids(db, [normalized_session_id], detailed=True)
    if not sessions:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Curation review session {normalized_session_id} not found",
        )

    session = sessions[0]
    document_map, user_map = _session_context_maps(db, [session])
    candidate_payloads = [_candidate_payload(candidate) for candidate in session.candidates]
    workspace = CurationWorkspacePayload(
        session=_session_detail(db, session, document_map, user_map),
        entity_tags=[
            _entity_tag_payload(candidate)
            for candidate in candidate_payloads
            if _candidate_has_entity_tag_fields(candidate)
        ],
        candidates=candidate_payloads,
        active_candidate_id=(
            str(session.current_candidate_id)
            if session.current_candidate_id is not None
            else None
        ),
        queue_context=None,
        action_log=[
            action_log_entry
            for action_log_entry in (
                _action_log_entry(record) for record in session.action_log_entries
            )
            if action_log_entry is not None
        ],
        submission_history=[
            _submission_record(record) for record in session.submissions
        ],
        saved_view_context=None,
    )
    return CurationWorkspaceResponse(workspace=workspace)


def get_candidate_detail(
    db: Session,
    candidate_id: str | UUID,
    *,
    session_id: str | UUID | None = None,
) -> CurationCandidatePayload:
    normalized_candidate_id = _normalize_uuid(candidate_id, field_name="candidate_id")
    statement = (
        select(CurationCandidate)
        .where(CurationCandidate.id == normalized_candidate_id)
        .options(*CANDIDATE_DETAIL_LOAD_OPTIONS)
    )

    if session_id is not None:
        normalized_session_id = _normalize_uuid(session_id, field_name="session_id")
        statement = statement.where(CurationCandidate.session_id == normalized_session_id)

    candidate = db.scalars(statement).first()
    if candidate is None:
        if session_id is not None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(
                    f"Curation candidate {normalized_candidate_id} not found in session "
                    f"{normalized_session_id}"
                ),
            )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Curation candidate {normalized_candidate_id} not found",
        )

    return _candidate_detail(candidate)


def find_reusable_prepared_session(
    db: Session,
    *,
    document_id: str,
    adapter_key: str,
    flow_run_id: str | None,
    prep_extraction_result_id: str | UUID,
) -> ReusablePreparedSessionContext | None:
    """Return the newest untouched NEW session that can be refreshed in place."""

    target_extraction_result_id = _normalize_uuid(
        prep_extraction_result_id,
        field_name="prep_extraction_result_id",
    )
    statement = (
        select(ReviewSessionModel)
        .where(ReviewSessionModel.document_id == _normalize_uuid(document_id, field_name="document_id"))
        .where(ReviewSessionModel.adapter_key == adapter_key)
        .where(ReviewSessionModel.status == CurationSessionStatus.NEW)
        .options(*PREPARED_SESSION_LOAD_OPTIONS)
        .order_by(
            ReviewSessionModel.prepared_at.desc(),
            ReviewSessionModel.created_at.desc(),
            ReviewSessionModel.id.desc(),
        )
    )

    if flow_run_id is None:
        statement = statement.where(ReviewSessionModel.flow_run_id.is_(None))
    else:
        statement = statement.where(ReviewSessionModel.flow_run_id == flow_run_id)

    for session_row in db.scalars(statement).all():
        if session_row.reviewed_candidates > 0 or session_row.submissions:
            continue
        if any(
            action_log_entry.candidate_id is not None or action_log_entry.draft_id is not None
            for action_log_entry in session_row.action_log_entries
        ):
            continue
        candidate_extraction_result_ids = {
            candidate.extraction_result_id
            for candidate in session_row.candidates
        }
        if candidate_extraction_result_ids != {target_extraction_result_id}:
            continue
        return ReusablePreparedSessionContext(
            session_id=str(session_row.id),
            created_by_id=session_row.created_by_id,
            assigned_curator_id=session_row.assigned_curator_id,
            notes=session_row.notes,
            tags=list(session_row.tags or []),
        )

    return None


def update_session(
    db: Session,
    session_id: str | UUID,
    request: CurationSessionUpdateRequest,
    actor_claims: dict[str, Any],
) -> CurationSessionUpdateResponse:
    normalized_session_id = _normalize_uuid(session_id, field_name="session_id")
    request_session_id = _normalize_uuid(request.session_id, field_name="session_id")
    if normalized_session_id != request_session_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Path session_id does not match request body session_id",
        )

    sessions = _load_sessions_by_ids(db, [normalized_session_id], detailed=True)
    if not sessions:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Curation review session {normalized_session_id} not found",
        )
    session = sessions[0]

    now = datetime.now(timezone.utc)
    action_log_row: SessionActionLogModel | None = None
    changed = False

    if "current_candidate_id" in request.model_fields_set:
        if request.current_candidate_id is None:
            if session.current_candidate_id is not None:
                session.current_candidate_id = None
                changed = True
        else:
            candidate_id = _normalize_uuid(request.current_candidate_id, field_name="current_candidate_id")
            if not any(candidate.id == candidate_id for candidate in session.candidates):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="current_candidate_id does not belong to this session",
                )
            if session.current_candidate_id != candidate_id:
                session.current_candidate_id = candidate_id
                session.last_worked_at = now
                changed = True

    if "notes" in request.model_fields_set and request.notes != session.notes:
        session.notes = request.notes
        changed = True

    if "curator_id" in request.model_fields_set and request.curator_id != session.assigned_curator_id:
        session.assigned_curator_id = request.curator_id
        changed = True
        if action_log_row is None:
            action_log_row = SessionActionLogModel(
                session_id=session.id,
                action_type=CurationActionType.SESSION_ASSIGNED,
                actor_type=CurationActorType.USER,
                actor=_actor_claims_payload(actor_claims),
                occurred_at=now,
                message="Session curator updated",
            )

    if "status" in request.model_fields_set and request.status != session.status:
        previous_status = session.status
        session.status = request.status
        if request.status == CurationSessionStatus.IN_PROGRESS:
            session.last_worked_at = now
        elif request.status == CurationSessionStatus.PAUSED:
            session.paused_at = now
        elif request.status == CurationSessionStatus.SUBMITTED:
            session.submitted_at = now
        changed = True
        action_log_row = SessionActionLogModel(
            session_id=session.id,
            action_type=CurationActionType.SESSION_STATUS_UPDATED,
            actor_type=CurationActorType.USER,
            actor=_actor_claims_payload(actor_claims),
            occurred_at=now,
            previous_session_status=previous_status,
            new_session_status=request.status,
            message=f"Session status updated from {previous_status.value} to {request.status.value}",
        )

    if changed:
        session.session_version += 1
        db.add(session)
        if action_log_row is not None:
            db.add(action_log_row)
        db.commit()

    updated_sessions = _load_sessions_by_ids(db, [normalized_session_id], detailed=True)
    updated_session = updated_sessions[0]
    document_map, user_map = _session_context_maps(db, [updated_session])
    return CurationSessionUpdateResponse(
        session=_session_detail(db, updated_session, document_map, user_map),
        action_log_entry=_action_log_entry(action_log_row),
    )


def _dedupe_non_empty_strings(values: Sequence[str], *, field_name: str) -> list[str]:
    normalized_values: list[str] = []
    seen: set[str] = set()

    for value in values:
        normalized = _normalized_optional_string(value, field_name=field_name)
        if normalized is None or normalized in seen:
            continue
        normalized_values.append(normalized)
        seen.add(normalized)

    return normalized_values


def _manual_candidate_field_inputs(
    fields: Sequence[CurationDraftFieldSchema],
) -> list[PreparedDraftFieldInput]:
    return [
        PreparedDraftFieldInput(
            field_key=_normalized_required_string(field.field_key, field_name="draft.fields.field_key"),
            label=_normalized_required_string(field.label, field_name="draft.fields.label"),
            value=field.value,
            seed_value=field.value,
            field_type=_normalized_optional_string(field.field_type, field_name="draft.fields.field_type"),
            group_key=_normalized_optional_string(field.group_key, field_name="draft.fields.group_key"),
            group_label=_normalized_optional_string(
                field.group_label,
                field_name="draft.fields.group_label",
            ),
            order=field.order,
            required=field.required,
            read_only=field.read_only,
            dirty=False,
            stale_validation=False,
            metadata=dict(field.metadata),
        )
        for field in fields
    ]


def _manual_candidate_evidence_inputs(
    evidence_records: Sequence[CurationEvidenceRecordPayload],
) -> list[PreparedEvidenceRecordInput]:
    prepared_records: list[PreparedEvidenceRecordInput] = []

    for evidence_record in evidence_records:
        field_keys = _dedupe_non_empty_strings(
            evidence_record.field_keys,
            field_name="evidence_anchors.field_keys",
        )
        field_group_keys = _dedupe_non_empty_strings(
            evidence_record.field_group_keys,
            field_name="evidence_anchors.field_group_keys",
        )

        if not field_keys and not field_group_keys:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Manual candidate evidence must include at least one field_key "
                    "or field_group_key"
                ),
            )

        prepared_records.append(
            PreparedEvidenceRecordInput(
                source=evidence_record.source,
                field_keys=field_keys,
                field_group_keys=field_group_keys,
                is_primary=evidence_record.is_primary,
                anchor=evidence_record.anchor.model_dump(mode="json"),
                warnings=list(evidence_record.warnings or []),
            )
        )

    return prepared_records


def _manual_candidate_normalized_payload(
    fields: Sequence[PreparedDraftFieldInput],
) -> dict[str, Any]:
    return {
        field.field_key: field.value
        for field in sorted(fields, key=lambda item: (item.order, item.field_key))
    }


def create_manual_candidate(
    db: Session,
    session_id: str | UUID,
    request: CurationManualCandidateCreateRequest,
    *,
    actor_claims: dict[str, Any],
) -> CurationManualCandidateCreateResponse:
    normalized_session_id = _normalize_uuid(session_id, field_name="session_id")
    request_session_id = _normalize_uuid(request.session_id, field_name="session_id")
    if normalized_session_id != request_session_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Path session_id does not match request body session_id",
        )

    if request.source != CurationCandidateSource.MANUAL:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Manual candidate creation only supports source=manual",
        )

    adapter_key = _normalized_required_string(request.adapter_key, field_name="adapter_key")
    display_label = _normalized_optional_string(request.display_label, field_name="display_label")
    draft_adapter_key = _normalized_required_string(
        request.draft.adapter_key,
        field_name="draft.adapter_key",
    )

    if draft_adapter_key != adapter_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="draft.adapter_key must match adapter_key",
        )

    sessions = _load_sessions_by_ids(db, [normalized_session_id], detailed=True)
    if not sessions:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Curation review session {normalized_session_id} not found",
        )
    session_row = sessions[0]

    if session_row.adapter_key != adapter_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="adapter_key must match the session adapter",
        )

    field_inputs = _manual_candidate_field_inputs(request.draft.fields)
    evidence_inputs = _manual_candidate_evidence_inputs(request.evidence_anchors)
    available_field_keys = {
        field_input.field_key
        for field_input in field_inputs
    }
    missing_evidence_field_keys = sorted(
        {
            field_key
            for evidence_input in evidence_inputs
            for field_key in evidence_input.field_keys
            if field_key not in available_field_keys
        }
    )
    if missing_evidence_field_keys:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Manual candidate evidence references unknown field(s): "
                f"{', '.join(missing_evidence_field_keys)}"
            ),
        )

    now = datetime.now(timezone.utc)
    next_order = max((candidate.order for candidate in session_row.candidates), default=-1) + 1
    resolved_display_label = (
        display_label
        or _normalized_optional_string(request.draft.title, field_name="draft.title")
        or f"Manual candidate {next_order + 1}"
    )

    candidate_row = CurationCandidate(
        session_id=session_row.id,
        source=CurationCandidateSource.MANUAL,
        status=CurationCandidateStatus.PENDING,
        order=next_order,
        adapter_key=adapter_key,
        display_label=resolved_display_label,
        secondary_label=None,
        conversation_summary=None,
        extraction_result_id=None,
        normalized_payload=_manual_candidate_normalized_payload(field_inputs),
        candidate_metadata={},
        created_at=now,
        updated_at=now,
    )
    db.add(candidate_row)
    db.flush()

    evidence_anchor_ids_by_field = _persist_candidate_evidence_records(
        db,
        candidate_row,
        evidence_inputs,
        created_at=now,
    )

    draft_row = DraftModel(
        candidate_id=candidate_row.id,
        adapter_key=adapter_key,
        version=1,
        title=resolved_display_label,
        summary=_normalized_optional_string(request.draft.summary, field_name="draft.summary"),
        fields=[
            _draft_field_payload(
                field_input,
                evidence_anchor_ids_by_field=evidence_anchor_ids_by_field,
                field_results={},
            )
            for field_input in field_inputs
        ],
        notes=_normalized_optional_string(request.draft.notes, field_name="draft.notes"),
        created_at=now,
        updated_at=now,
        last_saved_at=now,
        draft_metadata=dict(request.draft.metadata),
    )
    db.add(draft_row)
    db.flush()

    session_row.total_candidates += 1
    session_row.pending_candidates += 1
    session_row.manual_candidates += 1
    session_row.current_candidate_id = candidate_row.id
    session_row.session_version += 1
    session_row.updated_at = now
    session_row.last_worked_at = now
    db.add(session_row)

    evidence_anchor_ids = [
        anchor_id
        for anchor_ids in evidence_anchor_ids_by_field.values()
        for anchor_id in anchor_ids
    ]
    action_log_row = SessionActionLogModel(
        session_id=session_row.id,
        candidate_id=candidate_row.id,
        draft_id=draft_row.id,
        action_type=CurationActionType.CANDIDATE_CREATED,
        actor_type=CurationActorType.USER,
        actor=_actor_claims_payload(actor_claims),
        occurred_at=now,
        new_candidate_status=CurationCandidateStatus.PENDING,
        changed_field_keys=[field_input.field_key for field_input in field_inputs],
        evidence_anchor_ids=evidence_anchor_ids,
        message="Manual candidate created",
        action_metadata={
            "adapter_key": adapter_key,
            "source": CurationCandidateSource.MANUAL.value,
            "display_label": resolved_display_label,
            "evidence_count": len(request.evidence_anchors),
        },
    )
    db.add(action_log_row)
    db.flush()

    response = CurationManualCandidateCreateResponse(
        candidate=get_candidate_detail(
            db,
            candidate_row.id,
            session_id=session_row.id,
        ),
        session=get_session_detail(db, session_row.id),
        action_log_entry=build_action_log_entry(action_log_row),
    )
    db.commit()
    return response


def _load_candidate_for_write(
    db: Session,
    *,
    session_id: str | UUID,
    candidate_id: str | UUID,
) -> CurationCandidate:
    normalized_session_id = _normalize_uuid(session_id, field_name="session_id")
    normalized_candidate_id = _normalize_uuid(candidate_id, field_name="candidate_id")
    candidate = db.scalars(
        select(CurationCandidate)
        .where(CurationCandidate.id == normalized_candidate_id)
        .where(CurationCandidate.session_id == normalized_session_id)
        .options(
            selectinload(CurationCandidate.session).selectinload(
                ReviewSessionModel.validation_snapshots
            ),
            selectinload(CurationCandidate.draft),
            selectinload(CurationCandidate.evidence_anchors),
            selectinload(CurationCandidate.validation_snapshots),
        )
    ).first()
    if candidate is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Curation candidate {normalized_candidate_id} not found in session "
                f"{normalized_session_id}"
            ),
        )
    if candidate.draft is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Curation candidate {normalized_candidate_id} is missing its draft payload",
        )
    return candidate


def _load_session_for_validation(
    db: Session,
    *,
    session_id: str | UUID,
) -> ReviewSessionModel:
    normalized_session_id = _normalize_uuid(session_id, field_name="session_id")
    session_row = db.scalars(
        select(ReviewSessionModel)
        .where(ReviewSessionModel.id == normalized_session_id)
        .options(*DETAIL_LOAD_OPTIONS)
    ).first()
    if session_row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Curation review session {normalized_session_id} not found",
        )
    return session_row


def _load_submission_record(
    db: Session,
    *,
    session_id: str | UUID,
    submission_id: str | UUID,
) -> SubmissionModel:
    normalized_session_id = _normalize_uuid(session_id, field_name="session_id")
    normalized_submission_id = _normalize_uuid(submission_id, field_name="submission_id")
    submission_row = db.scalars(
        select(SubmissionModel)
        .where(SubmissionModel.id == normalized_submission_id)
        .where(SubmissionModel.session_id == normalized_session_id)
    ).first()
    if submission_row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Curation submission {normalized_submission_id} not found in session "
                f"{normalized_session_id}"
            ),
        )
    return submission_row


def _validation_result_for_field(field: CurationDraftFieldSchema) -> FieldValidationResult:
    if field.dirty:
        return FieldValidationResult(
            status="overridden",
            resolver="curator_override",
            warnings=["Curator value differs from the AI-seeded draft."],
        )

    field_status, field_warnings = field_validation_status(field.value)
    return FieldValidationResult(
        status=field_status,
        resolver="deterministic_structural_validation",
        warnings=field_warnings,
    )


def _compute_candidate_validation(
    candidate: CurationCandidate,
    *,
    force: bool,
    validated_at: datetime,
    field_keys: Sequence[str] | None = None,
) -> CandidateValidationComputation:
    requested_field_keys = set(field_keys or [])
    draft_fields = [
        CurationDraftFieldSchema.model_validate(field_payload)
        for field_payload in (candidate.draft.fields or [])
    ]
    latest_snapshot = _latest_snapshot_record(candidate.validation_snapshots)
    latest_results = (
        {
            field_key: FieldValidationResult.model_validate(result_payload)
            for field_key, result_payload in (latest_snapshot.field_results or {}).items()
        }
        if latest_snapshot is not None
        else {}
    )

    def _existing_result(field: CurationDraftFieldSchema) -> FieldValidationResult | None:
        return latest_results.get(field.field_key) or field.validation_result

    if (
        not requested_field_keys
        and not force
        and latest_snapshot is not None
        and latest_snapshot.state == CurationValidationSnapshotState.COMPLETED
        and all(
            not field.stale_validation and _existing_result(field) is not None
            for field in draft_fields
        )
    ):
        return CandidateValidationComputation(existing_snapshot=latest_snapshot)

    counts = CurationValidationCounts()
    warnings: list[str] = []
    field_results: dict[str, FieldValidationResult] = {}
    updated_fields: list[dict[str, Any]] = []
    stale_field_keys: list[str] = []
    snapshot_missing_or_incomplete = (
        latest_snapshot is None
        or latest_snapshot.state != CurationValidationSnapshotState.COMPLETED
    )

    for draft_field in draft_fields:
        existing_result = _existing_result(draft_field)
        field_is_targeted = (
            not requested_field_keys
            or draft_field.field_key in requested_field_keys
        )
        should_refresh = (
            field_is_targeted
            and (
                force
                or draft_field.stale_validation
                or existing_result is None
                or snapshot_missing_or_incomplete
            )
        )
        next_result = (
            _validation_result_for_field(draft_field)
            if should_refresh
            else existing_result
        )
        if next_result is None:
            next_result = _validation_result_for_field(draft_field)

        field_results[draft_field.field_key] = next_result
        increment_validation_count(counts, next_result.status)
        warnings.extend(next_result.warnings)
        next_field = (
            draft_field.model_copy(
                update={
                    "stale_validation": False,
                    "validation_result": next_result,
                }
            )
            if field_is_targeted
            else draft_field
        )
        if next_field.stale_validation:
            stale_field_keys.append(next_field.field_key)
        updated_fields.append(next_field.model_dump(mode="json"))

    snapshot = PreparedValidationSnapshotInput(
        scope=CurationValidationScope.CANDIDATE,
        state=CurationValidationSnapshotState.COMPLETED,
        summary=CurationValidationSummary(
            state=CurationValidationSnapshotState.COMPLETED,
            counts=counts,
            last_validated_at=validated_at,
            stale_field_keys=stale_field_keys,
            warnings=dedupe(warnings),
        ),
        field_results=field_results,
        warnings=dedupe(warnings),
        requested_at=validated_at,
        completed_at=validated_at,
        adapter_key=candidate.adapter_key,
    )

    return CandidateValidationComputation(
        snapshot=snapshot,
        updated_fields=updated_fields,
    )


def _apply_candidate_validation(
    db: Session,
    candidate: CurationCandidate,
    *,
    force: bool,
    validated_at: datetime,
    field_keys: Sequence[str] | None = None,
) -> tuple[CurationValidationSnapshotSchema, bool]:
    computation = _compute_candidate_validation(
        candidate,
        force=force,
        validated_at=validated_at,
        field_keys=field_keys,
    )
    if computation.existing_snapshot is not None:
        return _validation_snapshot(computation.existing_snapshot), False

    if computation.snapshot is None or computation.updated_fields is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Candidate {candidate.id} validation could not be materialized",
        )

    candidate.draft.fields = computation.updated_fields
    candidate.draft.updated_at = validated_at
    candidate.updated_at = validated_at

    snapshot_row = _validation_snapshot_row(
        session_row=candidate.session,
        candidate_id=candidate.id,
        snapshot=computation.snapshot,
    )
    db.add(snapshot_row)
    db.flush()
    candidate.validation_snapshots.append(snapshot_row)
    return _validation_snapshot(snapshot_row), True


def _aggregate_session_validation_snapshot(
    *,
    session_row: ReviewSessionModel,
    candidate_validations: Sequence[CurationValidationSnapshotSchema],
    validated_at: datetime,
) -> PreparedValidationSnapshotInput:
    counts = CurationValidationCounts()
    warnings: list[str] = []

    for candidate_validation in candidate_validations:
        candidate_counts = candidate_validation.summary.counts
        counts.validated += candidate_counts.validated
        counts.ambiguous += candidate_counts.ambiguous
        counts.not_found += candidate_counts.not_found
        counts.invalid_format += candidate_counts.invalid_format
        counts.conflict += candidate_counts.conflict
        counts.skipped += candidate_counts.skipped
        counts.overridden += candidate_counts.overridden
        warnings.extend(candidate_validation.warnings)
        warnings.extend(candidate_validation.summary.warnings)

    deduped_warnings = dedupe(warnings)
    return PreparedValidationSnapshotInput(
        scope=CurationValidationScope.SESSION,
        state=CurationValidationSnapshotState.COMPLETED,
        summary=CurationValidationSummary(
            state=CurationValidationSnapshotState.COMPLETED,
            counts=counts,
            last_validated_at=validated_at,
            stale_field_keys=[],
            warnings=deduped_warnings,
        ),
        field_results={},
        warnings=deduped_warnings,
        requested_at=validated_at,
        completed_at=validated_at,
        adapter_key=session_row.adapter_key,
    )


def _prepared_validation_snapshot_schema(
    *,
    session_id: UUID,
    candidate_id: UUID | None,
    snapshot: PreparedValidationSnapshotInput,
) -> CurationValidationSnapshotSchema:
    return CurationValidationSnapshotSchema(
        snapshot_id=str(uuid4()),
        scope=snapshot.scope,
        session_id=str(session_id),
        candidate_id=str(candidate_id) if candidate_id is not None else None,
        adapter_key=snapshot.adapter_key,
        state=snapshot.state,
        field_results=snapshot.field_results,
        summary=snapshot.summary,
        requested_at=snapshot.requested_at,
        completed_at=snapshot.completed_at,
        warnings=list(snapshot.warnings),
    )


def _submission_validation_blocking_reason(
    field: CurationDraftFieldSchema | None,
    validation_result: FieldValidationResult,
) -> str | None:
    if _field_validation_is_warning_only(field):
        return None

    field_label = field.label if field is not None else "A submission field"

    if validation_result.status == "invalid_format":
        return f"{field_label} is empty or invalid."
    if validation_result.status == "ambiguous":
        return f"{field_label} is still ambiguous."
    if validation_result.status == "not_found":
        return f"{field_label} could not be resolved."
    if validation_result.status == "conflict":
        return f"{field_label} has conflicting validation results."

    return None


def _field_validation_is_warning_only(
    field: CurationDraftFieldSchema | None,
) -> bool:
    if field is None:
        return False

    validation_config = field.metadata.get("validation")
    if not isinstance(validation_config, Mapping):
        return False

    severity = validation_config.get("severity")
    return isinstance(severity, str) and severity.strip().lower() == "warning"


def _candidate_submission_readiness(
    candidate: CurationCandidate,
    validation_snapshot: CurationValidationSnapshotSchema | None,
) -> CurationCandidateSubmissionReadiness:
    draft = _draft_detail(candidate.draft)
    if draft is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Curation candidate {candidate.id} is missing its draft payload",
        )

    blocking_reasons: list[str] = []
    warnings: list[str] = []

    if candidate.status == CurationCandidateStatus.PENDING:
        blocking_reasons.append("Candidate is still pending curator review.")
    elif candidate.status == CurationCandidateStatus.REJECTED:
        blocking_reasons.append("Candidate was rejected and is excluded from submission.")
    elif candidate.status != CurationCandidateStatus.ACCEPTED:
        blocking_reasons.append(
            f"Candidate status {candidate.status.value} is not eligible for submission."
        )

    field_map = {
        field.field_key: field
        for field in draft.fields
    }
    field_results = (
        validation_snapshot.field_results
        if validation_snapshot is not None
        else {}
    )
    for field_key, validation_result in field_results.items():
        blocking_reason = _submission_validation_blocking_reason(
            field_map.get(field_key),
            validation_result,
        )
        if blocking_reason is not None:
            blocking_reasons.append(blocking_reason)
        warnings.extend(validation_result.warnings)

    return CurationCandidateSubmissionReadiness(
        candidate_id=str(candidate.id),
        ready=candidate.status == CurationCandidateStatus.ACCEPTED and not blocking_reasons,
        blocking_reasons=dedupe(blocking_reasons),
        warnings=dedupe(warnings),
    )


def _submission_candidate_bundle(
    candidate: CurationCandidate,
) -> dict[str, Any]:
    draft = _draft_detail(candidate.draft)
    if draft is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Curation candidate {candidate.id} is missing its draft payload",
        )

    return {
        "candidate_id": str(candidate.id),
        "adapter_key": candidate.adapter_key,
        "display_label": candidate.display_label,
        "secondary_label": candidate.secondary_label,
        "fields": {
            field.field_key: field.value
            for field in draft.fields
        },
        "draft_fields": [
            field.model_dump(mode="json")
            for field in draft.fields
        ],
        "metadata": dict(candidate.candidate_metadata or {}),
        "normalized_payload": dict(candidate.normalized_payload or {}),
    }


class _SharedSubmissionPreviewAdapter:
    """Default adapter-owned payload builder used when no custom builder is registered yet."""

    def __init__(self, adapter_key: str) -> None:
        self.adapter_key = adapter_key
        self.supported_submission_modes = tuple(SubmissionMode)
        self.supported_target_keys: tuple[str, ...] = ()

    def build_submission_payload(
        self,
        *,
        mode: SubmissionMode,
        target_key: str,
        payload_context: Mapping[str, Any],
    ) -> SubmissionPayloadContract:
        payload_json: dict[str, Any] = {
            "session_id": payload_context["session_id"],
            "adapter_key": self.adapter_key,
            "mode": mode.value,
            "target_key": target_key,
            "candidate_count": payload_context["candidate_count"],
            "candidates": payload_context["candidates"],
        }
        document = payload_context.get("document")
        if document is not None:
            payload_json["document"] = document
        session_validation = payload_context.get("session_validation")
        if session_validation is not None:
            payload_json["session_validation"] = session_validation

        payload_kwargs: dict[str, Any] = {
            "mode": mode,
            "target_key": target_key,
            "adapter_key": self.adapter_key,
            "candidate_ids": payload_context["candidate_ids"],
            "payload_json": payload_json,
            "warnings": payload_context["warnings"],
        }

        return SubmissionPayloadContract(**payload_kwargs)


def _resolve_submission_domain_adapter(adapter_key: str) -> SubmissionDomainAdapter:
    return _SharedSubmissionPreviewAdapter(adapter_key)


def _default_submission_target_key(adapter_key: str) -> str:
    return f"{adapter_key}.default"


def _resolve_submission_preview_target_key(
    *,
    adapter_key: str,
    requested_target_key: str | None,
) -> tuple[SubmissionDomainAdapter, str]:
    submission_adapter = _resolve_submission_domain_adapter(adapter_key)
    supported_target_keys = tuple(submission_adapter.supported_target_keys or ())

    if requested_target_key:
        if supported_target_keys and requested_target_key not in supported_target_keys:
            supported_targets = ", ".join(supported_target_keys)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Unsupported submission target '{requested_target_key}' for "
                    f"adapter '{adapter_key}'. Supported targets: {supported_targets}"
                ),
            )

        return submission_adapter, requested_target_key

    if supported_target_keys:
        return submission_adapter, supported_target_keys[0]

    # Keep the shared substrate target-agnostic even before adapters publish
    # explicit target identifiers for preview/export flows.
    return submission_adapter, _default_submission_target_key(adapter_key)


def _resolve_export_preview_target_key(
    *,
    adapter_key: str,
    requested_target_key: str | None,
):
    export_adapter = _resolve_export_adapter(adapter_key)
    supported_target_keys = tuple(export_adapter.supported_target_keys or ())

    if requested_target_key:
        if supported_target_keys and requested_target_key not in supported_target_keys:
            supported_targets = ", ".join(supported_target_keys)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Unsupported export target '{requested_target_key}' for "
                    f"adapter '{adapter_key}'. Supported targets: {supported_targets}"
                ),
            )

        return export_adapter, requested_target_key

    if supported_target_keys:
        return export_adapter, supported_target_keys[0]

    return export_adapter, _default_submission_target_key(adapter_key)


def _resolve_export_adapter(adapter_key: str):
    export_adapter = _export_adapter_registry().get(adapter_key)
    if export_adapter is not None:
        return export_adapter

    # Keep direct-submit payload building aligned with the shared submission
    # contract while domain-specific export adapters continue to roll out.
    return _resolve_submission_domain_adapter(adapter_key)


def _resolve_submission_transport_adapter(target_key: str) -> SubmissionTransportAdapter:
    try:
        return _submission_adapter_registry().require(target_key)
    except KeyError as exc:
        raise_sanitized_http_exception(
            logger,
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Submission target is not configured",
            log_message=f"Unknown submission target requested: {target_key}",
            exc=exc,
            level=logging.WARNING,
        )


def _base_submission_payload_context(
    *,
    db: Session,
    session_row: ReviewSessionModel,
    ready_candidates: Sequence[CurationCandidate],
    session_validation: CurationValidationSnapshotSchema | None,
) -> dict[str, Any]:
    document = db.get(PDFDocument, session_row.document_id)
    warnings: list[str] = []
    if not ready_candidates:
        warnings.append("No accepted candidates are ready for submission.")

    return {
        "session_id": str(session_row.id),
        "document": (
            _document_ref(document).model_dump(mode="json")
            if document is not None
            else None
        ),
        "session_validation": (
            session_validation.model_dump(mode="json")
            if session_validation is not None
            else None
        ),
        "warnings": dedupe(warnings),
    }


def _submission_payload_context(
    *,
    db: Session,
    session_row: ReviewSessionModel,
    ready_candidates: Sequence[CurationCandidate],
    session_validation: CurationValidationSnapshotSchema | None,
) -> dict[str, Any]:
    payload_context = _base_submission_payload_context(
        db=db,
        session_row=session_row,
        ready_candidates=ready_candidates,
        session_validation=session_validation,
    )

    return {
        **payload_context,
        "candidate_ids": [str(candidate.id) for candidate in ready_candidates],
        "candidate_count": len(ready_candidates),
        "candidates": [
            _submission_candidate_bundle(candidate)
            for candidate in ready_candidates
        ],
    }


def _export_submission_payload_context(
    *,
    db: Session,
    session_row: ReviewSessionModel,
    ready_candidates: Sequence[CurationCandidate],
    session_validation: CurationValidationSnapshotSchema | None,
) -> dict[str, Any]:
    payload_context = _base_submission_payload_context(
        db=db,
        session_row=session_row,
        ready_candidates=ready_candidates,
        session_validation=session_validation,
    )
    export_candidates = [
        _candidate_payload(candidate).model_dump(mode="json")
        for candidate in ready_candidates
    ]

    return {
        **payload_context,
        "candidate_ids": [candidate["candidate_id"] for candidate in export_candidates],
        "candidate_count": len(export_candidates),
        "candidates": export_candidates,
    }


def _build_submission_preview_payload(
    *,
    db: Session,
    session_row: ReviewSessionModel,
    submission_adapter: SubmissionDomainAdapter,
    mode: SubmissionMode,
    target_key: str,
    ready_candidates: Sequence[CurationCandidate],
    session_validation: CurationValidationSnapshotSchema | None,
) -> SubmissionPayloadContract:
    payload_context = _submission_payload_context(
        db=db,
        session_row=session_row,
        ready_candidates=ready_candidates,
        session_validation=session_validation,
    )
    return submission_adapter.build_submission_payload(
        mode=mode,
        target_key=target_key,
        payload_context=payload_context,
    )


def _build_submission_execute_payload(
    *,
    db: Session,
    session_row: ReviewSessionModel,
    mode: SubmissionMode,
    target_key: str,
    ready_candidates: Sequence[CurationCandidate],
    session_validation: CurationValidationSnapshotSchema | None,
    adapter_key: str | None = None,
) -> SubmissionPayloadContract:
    export_adapter = _resolve_export_adapter(adapter_key or session_row.adapter_key)
    payload_context = _export_submission_payload_context(
        db=db,
        session_row=session_row,
        ready_candidates=ready_candidates,
        session_validation=session_validation,
    )

    try:
        return export_adapter.build_submission_payload(
            mode=mode,
            target_key=target_key,
            payload_context=payload_context,
        )
    except ValueError as exc:
        raise_sanitized_http_exception(
            logger,
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Submission payload could not be built",
            log_message=f"Submission payload build failed for target {target_key}",
            exc=exc,
            level=logging.WARNING,
        )


def _coerce_failed_submission_result(
    *,
    adapter: SubmissionTransportAdapter,
    error: Exception,
) -> SubmissionTransportResult:
    if isinstance(error, SubmissionTransportError):
        return error.to_result()

    return normalize_submission_transport_result(
        status=CurationSubmissionStatus.FAILED,
        response_message=SUBMISSION_TRANSPORT_FAILURE_MESSAGE,
    )


def _submission_attempt_marks_session_submitted(status_value: CurationSubmissionStatus) -> bool:
    return status_value in {
        CurationSubmissionStatus.ACCEPTED,
        CurationSubmissionStatus.QUEUED,
        CurationSubmissionStatus.MANUAL_REVIEW_REQUIRED,
    }


def _submission_action_message(
    *,
    result_status: CurationSubmissionStatus,
    target_key: str,
) -> str:
    return (
        f"Submission to target '{target_key}' completed with status "
        f"'{result_status.value}'"
    )


def _submission_candidate_ids(record: SubmissionModel) -> list[str]:
    payload = _submission_payload(record)
    if payload is not None and payload.candidate_ids:
        return dedupe(payload.candidate_ids)

    return dedupe(
        [
            candidate_id
            for readiness_item in (record.readiness or [])
            if isinstance(readiness_item, dict)
            and readiness_item.get("ready") is True
            and isinstance(candidate_id := readiness_item.get("candidate_id"), str)
            and candidate_id
        ]
    )


def _execute_direct_submission_attempt(
    *,
    db: Session,
    session_row: ReviewSessionModel,
    adapter_key: str,
    mode: SubmissionMode,
    target_key: str,
    payload: SubmissionPayloadContract,
    readiness: Sequence[CurationCandidateSubmissionReadiness],
    actor_claims: dict[str, Any],
    action_type: CurationActionType,
    action_metadata: Mapping[str, Any] | None = None,
) -> tuple[CurationSubmissionRecord, CurationActionLogEntry]:
    transport_adapter = _resolve_submission_transport_adapter(target_key)
    requested_at = datetime.now(timezone.utc)
    try:
        result = coerce_submission_transport_result(
            transport_adapter.submit(payload=payload)
        )
    except Exception as exc:
        logger.exception(
            "Submission transport adapter '%s' failed for session '%s' and target '%s'",
            transport_adapter.transport_key,
            str(session_row.id),
            target_key,
        )
        result = _coerce_failed_submission_result(
            adapter=transport_adapter,
            error=exc,
        )

    if result.status not in DIRECT_SUBMISSION_RESULT_STATUSES:
        result = normalize_submission_transport_result(
            status=CurationSubmissionStatus.FAILED,
            response_message=(
                f"Submission adapter '{transport_adapter.transport_key}' returned "
                f"unsupported direct-submit status '{result.status.value}'"
            ),
            warnings=result.warnings,
        )

    completed_at = result.completed_at or requested_at
    combined_warnings = dedupe([*payload.warnings, *result.warnings])
    submission_row = SubmissionModel(
        session_id=session_row.id,
        adapter_key=adapter_key,
        mode=mode,
        target_key=target_key,
        status=result.status,
        readiness=[item.model_dump(mode="json") for item in readiness],
        payload=_serialize_submission_payload_contract(payload),
        external_reference=result.external_reference,
        response_message=result.response_message,
        validation_errors=list(result.validation_errors),
        warnings=combined_warnings,
        requested_at=requested_at,
        completed_at=completed_at,
    )

    previous_session_status = session_row.status
    if _submission_attempt_marks_session_submitted(result.status):
        session_row.status = CurationSessionStatus.SUBMITTED
        if session_row.submitted_at is None:
            session_row.submitted_at = completed_at
    session_row.updated_at = completed_at
    session_row.last_worked_at = completed_at
    session_row.session_version += 1

    action_log_payload = {
        "target_key": target_key,
        "mode": mode.value,
        "submission_status": result.status.value,
        "submitted_candidate_ids": list(payload.candidate_ids),
        "submitted_candidate_count": len(payload.candidate_ids),
        "external_reference": result.external_reference,
        "validation_error_count": len(result.validation_errors),
    }
    if action_metadata:
        action_log_payload.update(dict(action_metadata))

    action_log_row = SessionActionLogModel(
        session_id=session_row.id,
        action_type=action_type,
        actor_type=CurationActorType.USER,
        actor=_actor_claims_payload(actor_claims),
        occurred_at=completed_at,
        previous_session_status=(
            previous_session_status if previous_session_status != session_row.status else None
        ),
        new_session_status=(
            session_row.status if previous_session_status != session_row.status else None
        ),
        message=_submission_action_message(
            result_status=result.status,
            target_key=target_key,
        ),
        action_metadata=action_log_payload,
    )

    db.add(session_row)
    db.add(submission_row)
    db.add(action_log_row)
    db.flush()

    response_submission = _submission_record(submission_row).model_copy(
        update={
            "payload": payload,
            "warnings": combined_warnings,
        }
    )

    db.commit()
    action_log_entry = _action_log_entry(action_log_row)
    if action_log_entry is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Submission action log entry could not be serialized",
        )

    return response_submission, action_log_entry


def update_candidate_draft(
    db: Session,
    session_id: str | UUID,
    candidate_id: str | UUID,
    request: CurationCandidateDraftUpdateRequest,
    actor_claims: dict[str, Any],
) -> CurationCandidateDraftUpdateResponse:
    normalized_session_id = _normalize_uuid(session_id, field_name="session_id")
    normalized_candidate_id = _normalize_uuid(candidate_id, field_name="candidate_id")
    request_session_id = _normalize_uuid(request.session_id, field_name="session_id")
    request_candidate_id = _normalize_uuid(request.candidate_id, field_name="candidate_id")
    if normalized_session_id != request_session_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Path session_id does not match request body session_id",
        )
    if normalized_candidate_id != request_candidate_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Path candidate_id does not match request body candidate_id",
        )

    candidate = _load_candidate_for_write(
        db,
        session_id=normalized_session_id,
        candidate_id=normalized_candidate_id,
    )
    draft_row = candidate.draft
    request_draft_id = _normalize_uuid(request.draft_id, field_name="draft_id")
    if draft_row.id != request_draft_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Request draft_id does not match the candidate draft",
        )
    if request.expected_version is not None and draft_row.version != request.expected_version:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Draft version mismatch: expected {request.expected_version}, "
                f"found {draft_row.version}"
            ),
        )

    draft_fields = [
        CurationDraftFieldSchema.model_validate(field_payload)
        for field_payload in (draft_row.fields or [])
    ]
    field_index = {
        field.field_key: index
        for index, field in enumerate(draft_fields)
    }
    changed_field_keys: list[str] = []

    for field_change in request.field_changes:
        field_position = field_index.get(field_change.field_key)
        if field_position is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unknown draft field {field_change.field_key}",
            )

        current_field = draft_fields[field_position]
        next_value = (
            current_field.seed_value
            if field_change.revert_to_seed
            else field_change.value
        )
        next_dirty = not _draft_values_equal(next_value, current_field.seed_value)
        next_field = current_field.model_copy(
            update={
                "value": next_value,
                "dirty": next_dirty,
                "stale_validation": next_dirty,
            }
        )

        if current_field != next_field:
            draft_fields[field_position] = next_field
            changed_field_keys.append(current_field.field_key)

    notes_changed = (
        "notes" in request.model_fields_set
        and request.notes != draft_row.notes
    )
    if not changed_field_keys and not notes_changed:
        return CurationCandidateDraftUpdateResponse(
            candidate=_candidate_payload(candidate),
            draft=_draft_payload(candidate),
            validation_snapshot=None,
            action_log_entry=None,
        )

    now = datetime.now(timezone.utc)
    draft_row.fields = [
        field.model_dump(mode="json")
        for field in draft_fields
    ]
    if notes_changed:
        draft_row.notes = request.notes
    draft_row.version += 1
    draft_row.updated_at = now
    draft_row.last_saved_at = now
    candidate.updated_at = now
    candidate.session.updated_at = now
    candidate.session.last_worked_at = now

    validation_snapshot: CurationValidationSnapshotSchema | None = None
    if changed_field_keys:
        validation_snapshot, _ = _apply_candidate_validation(
            db,
            candidate,
            force=True,
            validated_at=now,
            field_keys=changed_field_keys,
        )

    action_log_row = SessionActionLogModel(
        session_id=candidate.session_id,
        candidate_id=candidate.id,
        draft_id=draft_row.id,
        action_type=CurationActionType.CANDIDATE_UPDATED,
        actor_type=CurationActorType.USER,
        actor=_actor_claims_payload(actor_claims),
        occurred_at=now,
        changed_field_keys=changed_field_keys,
        message=(
            "Autosaved candidate draft changes"
            if request.autosave
            else "Candidate draft updated"
        ),
    )
    db.add(action_log_row)
    db.add(candidate.session)
    db.add(candidate)
    db.add(draft_row)
    db.commit()

    updated_candidate = _load_candidate_for_write(
        db,
        session_id=normalized_session_id,
        candidate_id=normalized_candidate_id,
    )
    return CurationCandidateDraftUpdateResponse(
        candidate=_candidate_payload(updated_candidate),
        draft=_draft_payload(updated_candidate),
        validation_snapshot=validation_snapshot,
        action_log_entry=_action_log_entry(action_log_row),
    )


def validate_candidate(
    db: Session,
    candidate_id: str | UUID,
    request: CurationCandidateValidationRequest,
) -> CurationCandidateValidationResponse:
    normalized_candidate_id = _normalize_uuid(candidate_id, field_name="candidate_id")
    request_candidate_id = _normalize_uuid(request.candidate_id, field_name="candidate_id")
    if normalized_candidate_id != request_candidate_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Path candidate_id does not match request body candidate_id",
        )

    candidate = _load_candidate_for_write(
        db,
        session_id=request.session_id,
        candidate_id=normalized_candidate_id,
    )
    available_field_keys = {
        field_payload.get("field_key")
        for field_payload in (candidate.draft.fields or [])
        if isinstance(field_payload, dict)
    }
    unknown_field_keys = [
        field_key
        for field_key in request.field_keys
        if field_key not in available_field_keys
    ]
    if unknown_field_keys:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown draft field(s): {', '.join(sorted(unknown_field_keys))}",
        )

    validated_at = datetime.now(timezone.utc)
    validation_snapshot, changed = _apply_candidate_validation(
        db,
        candidate,
        force=request.force,
        validated_at=validated_at,
        field_keys=request.field_keys,
    )
    if changed:
        candidate.session.updated_at = validated_at
        db.add(candidate.session)
        db.add(candidate)
        db.add(candidate.draft)
        db.commit()

    updated_candidate = _load_candidate_for_write(
        db,
        session_id=request.session_id,
        candidate_id=normalized_candidate_id,
    )
    return CurationCandidateValidationResponse(
        candidate=_candidate_payload(updated_candidate),
        validation_snapshot=validation_snapshot,
    )


def validate_session(
    db: Session,
    session_id: str | UUID,
    request: CurationSessionValidationRequest,
) -> CurationSessionValidationResponse:
    normalized_session_id = _normalize_uuid(session_id, field_name="session_id")
    request_session_id = _normalize_uuid(request.session_id, field_name="session_id")
    if normalized_session_id != request_session_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Path session_id does not match request body session_id",
        )
    session_row = _load_session_for_validation(db, session_id=normalized_session_id)
    candidate_map = {str(candidate.id): candidate for candidate in session_row.candidates}
    target_candidate_ids = request.candidate_ids or list(candidate_map.keys())
    unknown_candidate_ids = [
        candidate_id
        for candidate_id in target_candidate_ids
        if candidate_id not in candidate_map
    ]
    if unknown_candidate_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown candidate(s) for session: {', '.join(sorted(unknown_candidate_ids))}",
        )

    validated_at = datetime.now(timezone.utc)
    candidate_validations: list[CurationValidationSnapshotSchema] = []
    changed = False

    for target_candidate_id in target_candidate_ids:
        validation_snapshot, candidate_changed = _apply_candidate_validation(
            db,
            candidate_map[target_candidate_id],
            force=request.force,
            validated_at=validated_at,
        )
        candidate_validations.append(validation_snapshot)
        changed |= candidate_changed

    session_snapshot_input = _aggregate_session_validation_snapshot(
        session_row=session_row,
        candidate_validations=candidate_validations,
        validated_at=validated_at,
    )

    if not request.candidate_ids:
        session_snapshot_row = _validation_snapshot_row(
            session_row=session_row,
            candidate_id=None,
            snapshot=session_snapshot_input,
        )
        session_row.updated_at = validated_at
        db.add(session_snapshot_row)
        db.add(session_row)
        db.commit()
        session_validation = _validation_snapshot(session_snapshot_row)
    else:
        if changed:
            db.commit()
        session_validation = _prepared_validation_snapshot_schema(
            session_id=session_row.id,
            candidate_id=None,
            snapshot=session_snapshot_input,
        )

    updated_session = _load_session_for_validation(db, session_id=normalized_session_id)
    document_map, user_map = _session_context_maps(db, [updated_session])
    return CurationSessionValidationResponse(
        session=_session_detail(db, updated_session, document_map, user_map),
        session_validation=session_validation,
        candidate_validations=candidate_validations,
    )


def delete_candidate(
    db: Session,
    session_id: str | UUID,
    candidate_id: str | UUID,
    *,
    actor_claims: dict[str, Any],
) -> CurationCandidateDeleteResponse:
    normalized_session_id = _normalize_uuid(session_id, field_name="session_id")
    normalized_candidate_id = _normalize_uuid(candidate_id, field_name="candidate_id")
    sessions = _load_sessions_by_ids(db, [normalized_session_id], detailed=True)
    if not sessions:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Curation review session {normalized_session_id} not found",
        )

    session = sessions[0]
    candidate = next(
        (
            session_candidate
            for session_candidate in session.candidates
            if session_candidate.id == normalized_candidate_id
        ),
        None,
    )
    if candidate is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Curation candidate {normalized_candidate_id} not found in session "
                f"{normalized_session_id}"
            ),
        )
    if candidate.draft is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Curation candidate {normalized_candidate_id} is missing its draft payload",
        )

    now = datetime.now(timezone.utc)
    remaining_candidates = [
        session_candidate
        for session_candidate in session.candidates
        if session_candidate.id != normalized_candidate_id
    ]
    next_candidate_uuid = _current_candidate_id_after_delete(
        session=session,
        deleted_candidate=candidate,
        remaining_candidates=remaining_candidates,
    )

    _delete_session_validation_snapshots(db, session_id=session.id)
    _delete_candidate_children(
        db,
        session_id=session.id,
        candidate_ids=[candidate.id],
    )

    session.candidates = list(remaining_candidates)
    session.validation_snapshots = []
    session.action_log_entries = [
        action_log_entry
        for action_log_entry in session.action_log_entries
        if action_log_entry.candidate_id != candidate.id
    ]
    session.current_candidate_id = next_candidate_uuid
    session.last_worked_at = now
    session.updated_at = now
    session.session_version += 1
    _apply_candidate_progress_counts(session, remaining_candidates)

    action_log_row = SessionActionLogModel(
        session_id=session.id,
        action_type=CurationActionType.CANDIDATE_DELETED,
        actor_type=CurationActorType.USER,
        actor=_actor_claims_payload(actor_claims),
        occurred_at=now,
        previous_candidate_status=candidate.status,
        message="Candidate deleted from session",
        evidence_anchor_ids=[str(evidence_row.id) for evidence_row in candidate.evidence_anchors],
        action_metadata={
            "deleted_candidate_id": str(candidate.id),
            "deleted_draft_id": str(candidate.draft.id),
            "deleted_display_label": candidate.display_label,
            "deleted_evidence_anchor_count": len(candidate.evidence_anchors),
            "deleted_validation_snapshot_count": len(candidate.validation_snapshots),
            "next_candidate_id": str(next_candidate_uuid) if next_candidate_uuid else None,
            "session_validation_cleared": True,
        },
    )

    db.add(action_log_row)
    db.commit()

    action_log_entry = build_action_log_entry(action_log_row)
    db.expire_all()

    return CurationCandidateDeleteResponse(
        deleted_candidate_id=str(candidate.id),
        session=get_session_detail(db, session.id),
        action_log_entry=action_log_entry,
    )


def submission_preview(
    db: Session,
    session_id: str | UUID,
    request: CurationSubmissionPreviewRequest,
) -> CurationSubmissionPreviewResponse:
    normalized_session_id = _normalize_uuid(session_id, field_name="session_id")
    request_session_id = _normalize_uuid(request.session_id, field_name="session_id")
    if normalized_session_id != request_session_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Path session_id does not match request body session_id",
        )

    validation_response = validate_session(
        db,
        normalized_session_id,
        CurationSessionValidationRequest(
            session_id=request.session_id,
            candidate_ids=request.candidate_ids,
            force=False,
        ),
    )

    session_row = _load_session_for_validation(db, session_id=normalized_session_id)
    submission_adapter = None
    if request.mode == SubmissionMode.EXPORT:
        _, target_key = _resolve_export_preview_target_key(
            adapter_key=session_row.adapter_key,
            requested_target_key=request.target_key,
        )
    else:
        submission_adapter, target_key = _resolve_submission_preview_target_key(
            adapter_key=session_row.adapter_key,
            requested_target_key=request.target_key,
        )
    candidate_map = {str(candidate.id): candidate for candidate in session_row.candidates}
    target_candidate_ids = request.candidate_ids or list(candidate_map.keys())
    readiness = [
        _candidate_submission_readiness(
            candidate_map[candidate_id],
            next(
                (
                    candidate_validation
                    for candidate_validation in validation_response.candidate_validations
                    if candidate_validation.candidate_id == candidate_id
                ),
                None,
            ),
        )
        for candidate_id in target_candidate_ids
    ]
    ready_candidates = [
        candidate_map[readiness_item.candidate_id]
        for readiness_item in readiness
        if readiness_item.ready
    ]

    payload = (
        (
            _build_submission_execute_payload(
                db=db,
                session_row=session_row,
                mode=request.mode,
                target_key=target_key,
                ready_candidates=ready_candidates,
                session_validation=validation_response.session_validation,
            )
            if request.mode == SubmissionMode.EXPORT
            else _build_submission_preview_payload(
                db=db,
                session_row=session_row,
                submission_adapter=submission_adapter,
                mode=request.mode,
                target_key=target_key,
                ready_candidates=ready_candidates,
                session_validation=validation_response.session_validation,
            )
        )
        if request.include_payload
        else None
    )
    submission_warnings = list(payload.warnings) if payload is not None else []

    return CurationSubmissionPreviewResponse(
        submission=CurationSubmissionRecord(
            submission_id=str(uuid4()),
            session_id=str(session_row.id),
            adapter_key=session_row.adapter_key,
            mode=request.mode,
            target_key=target_key,
            status=(
                CurationSubmissionStatus.EXPORT_READY
                if request.mode == SubmissionMode.EXPORT
                else CurationSubmissionStatus.PREVIEW_READY
            ),
            readiness=readiness,
            payload=_submission_payload_model_input(payload),
            requested_at=datetime.now(timezone.utc),
            completed_at=datetime.now(timezone.utc),
            validation_errors=[],
            warnings=submission_warnings,
        ),
        session_validation=validation_response.session_validation,
    )


def execute_submission(
    db: Session,
    session_id: str | UUID,
    request: CurationSubmissionExecuteRequest,
    actor_claims: dict[str, Any],
) -> CurationSubmissionExecuteResponse:
    normalized_session_id = _normalize_uuid(session_id, field_name="session_id")
    request_session_id = _normalize_uuid(request.session_id, field_name="session_id")
    if normalized_session_id != request_session_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Path session_id does not match request body session_id",
        )
    if request.mode != SubmissionMode.DIRECT_SUBMIT:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Submit endpoint only supports mode 'direct_submit'",
        )

    validation_response = validate_session(
        db,
        normalized_session_id,
        CurationSessionValidationRequest(
            session_id=request.session_id,
            candidate_ids=request.candidate_ids,
            force=False,
        ),
    )

    session_row = _load_session_for_validation(db, session_id=normalized_session_id)
    candidate_map = {str(candidate.id): candidate for candidate in session_row.candidates}
    target_candidate_ids = request.candidate_ids or list(candidate_map.keys())
    readiness = [
        _candidate_submission_readiness(
            candidate_map[candidate_id],
            next(
                (
                    candidate_validation
                    for candidate_validation in validation_response.candidate_validations
                    if candidate_validation.candidate_id == candidate_id
                ),
                None,
            ),
        )
        for candidate_id in target_candidate_ids
    ]
    ready_candidates = [
        candidate_map[readiness_item.candidate_id]
        for readiness_item in readiness
        if readiness_item.ready
    ]
    if not ready_candidates:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No eligible candidates are ready for direct submission",
        )

    payload = _build_submission_execute_payload(
        db=db,
        session_row=session_row,
        mode=request.mode,
        target_key=request.target_key,
        ready_candidates=ready_candidates,
        session_validation=validation_response.session_validation,
    )
    response_submission, action_log_entry = _execute_direct_submission_attempt(
        db=db,
        session_row=session_row,
        adapter_key=payload.adapter_key,
        mode=request.mode,
        target_key=request.target_key,
        payload=payload,
        readiness=readiness,
        actor_claims=actor_claims,
        action_type=CurationActionType.SUBMISSION_EXECUTED,
    )
    db.expire_all()

    response_session = get_session_detail(db, normalized_session_id)
    if (
        response_session.latest_submission is not None
        and response_session.latest_submission.submission_id == response_submission.submission_id
    ):
        response_session = response_session.model_copy(
            update={"latest_submission": response_submission}
        )

    return CurationSubmissionExecuteResponse(
        submission=response_submission,
        session=response_session,
        action_log_entry=action_log_entry,
    )


def retry_submission(
    db: Session,
    session_id: str | UUID,
    submission_id: str | UUID,
    request: CurationSubmissionRetryRequest,
    actor_claims: dict[str, Any],
) -> CurationSubmissionRetryResponse:
    normalized_session_id = _normalize_uuid(session_id, field_name="session_id")
    normalized_submission_id = _normalize_uuid(submission_id, field_name="submission_id")
    request_submission_id = _normalize_uuid(request.submission_id, field_name="submission_id")
    if normalized_submission_id != request_submission_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Path submission_id does not match request body submission_id",
        )

    original_submission = _load_submission_record(
        db,
        session_id=normalized_session_id,
        submission_id=normalized_submission_id,
    )
    if original_submission.mode != SubmissionMode.DIRECT_SUBMIT:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only direct-submit submissions may be retried",
        )
    if original_submission.status != CurationSubmissionStatus.FAILED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only failed submissions may be retried",
        )

    target_candidate_ids = _submission_candidate_ids(original_submission)
    if not target_candidate_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Original submission does not include retriable candidate identifiers",
        )

    validation_response = validate_session(
        db,
        normalized_session_id,
        CurationSessionValidationRequest(
            session_id=str(normalized_session_id),
            candidate_ids=target_candidate_ids,
            force=False,
        ),
    )

    session_row = _load_session_for_validation(db, session_id=normalized_session_id)
    candidate_map = {str(candidate.id): candidate for candidate in session_row.candidates}
    readiness = [
        _candidate_submission_readiness(
            candidate_map[candidate_id],
            next(
                (
                    candidate_validation
                    for candidate_validation in validation_response.candidate_validations
                    if candidate_validation.candidate_id == candidate_id
                ),
                None,
            ),
        )
        for candidate_id in target_candidate_ids
    ]
    ready_candidates = [
        candidate_map[readiness_item.candidate_id]
        for readiness_item in readiness
        if readiness_item.ready
    ]
    if not ready_candidates:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No eligible candidates are ready for direct submission",
        )

    payload = _build_submission_execute_payload(
        db=db,
        session_row=session_row,
        adapter_key=original_submission.adapter_key,
        mode=original_submission.mode,
        target_key=original_submission.target_key,
        ready_candidates=ready_candidates,
        session_validation=validation_response.session_validation,
    )
    retry_reason = _normalized_optional_string(request.reason, field_name="reason")
    response_submission, action_log_entry = _execute_direct_submission_attempt(
        db=db,
        session_row=session_row,
        adapter_key=original_submission.adapter_key,
        mode=original_submission.mode,
        target_key=original_submission.target_key,
        payload=payload,
        readiness=readiness,
        actor_claims=actor_claims,
        action_type=CurationActionType.SUBMISSION_RETRIED,
        action_metadata={
            "original_submission_id": str(original_submission.id),
            "retry_reason": retry_reason,
        },
    )
    db.expire_all()

    return CurationSubmissionRetryResponse(
        submission=response_submission,
        action_log_entry=action_log_entry,
    )


def get_submission(
    db: Session,
    session_id: str | UUID,
    submission_id: str | UUID,
) -> CurationSubmissionHistoryResponse:
    submission_row = _load_submission_record(
        db,
        session_id=session_id,
        submission_id=submission_id,
    )
    return CurationSubmissionHistoryResponse(
        submission=_submission_record(submission_row),
    )


def decide_candidate(
    db: Session,
    candidate_id: str | UUID,
    request: CurationCandidateDecisionRequest,
    actor_claims: dict[str, Any],
) -> CurationCandidateDecisionResponse:
    normalized_candidate_id = _normalize_uuid(candidate_id, field_name="candidate_id")
    normalized_session_id = _normalize_uuid(request.session_id, field_name="session_id")
    sessions = _load_sessions_by_ids(db, [normalized_session_id], detailed=True)
    if not sessions:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Curation review session {normalized_session_id} not found",
        )

    session = sessions[0]
    candidate = next(
        (
            session_candidate
            for session_candidate in session.candidates
            if session_candidate.id == normalized_candidate_id
        ),
        None,
    )
    if candidate is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Curation candidate {normalized_candidate_id} not found in session "
                f"{normalized_session_id}"
            ),
        )

    now = datetime.now(timezone.utc)
    previous_candidate_status = candidate.status
    previous_session_status = session.status
    reason = _normalize_optional_reason(request.reason)
    changed_field_keys: list[str] = []
    removed_manual_evidence_ids: list[str] = []
    notes_reset = False

    if request.action == CurationCandidateAction.RESET:
        (
            changed_field_keys,
            removed_manual_evidence_ids,
            notes_reset,
        ) = _reset_candidate_state(candidate, db, occurred_at=now)
        candidate.status = CurationCandidateStatus.PENDING
    else:
        candidate.status = _decision_candidate_status(request.action)

    if session.status == CurationSessionStatus.NEW:
        session.status = CurationSessionStatus.IN_PROGRESS

    next_candidate_uuid = (
        _next_pending_candidate_id(session, normalized_candidate_id)
        if request.advance_queue and request.action != CurationCandidateAction.RESET
        else None
    )

    session.current_candidate_id = next_candidate_uuid or normalized_candidate_id
    session.last_worked_at = now
    session.updated_at = now
    session.session_version += 1

    candidate.last_reviewed_at = now
    candidate.updated_at = now

    _apply_candidate_progress_counts(session, session.candidates)

    action_log_row = SessionActionLogModel(
        session_id=session.id,
        candidate_id=candidate.id,
        draft_id=candidate.draft.id if candidate.draft is not None else None,
        action_type=_decision_action_type(request.action),
        actor_type=CurationActorType.USER,
        actor=_actor_claims_payload(actor_claims),
        occurred_at=now,
        previous_session_status=(
            previous_session_status if previous_session_status != session.status else None
        ),
        new_session_status=(
            session.status if previous_session_status != session.status else None
        ),
        previous_candidate_status=previous_candidate_status,
        new_candidate_status=candidate.status,
        changed_field_keys=changed_field_keys,
        evidence_anchor_ids=removed_manual_evidence_ids,
        reason=reason,
        message=_decision_action_message(request.action, candidate.status),
        action_metadata={
            "advance_queue": request.advance_queue,
            "next_candidate_id": str(next_candidate_uuid) if next_candidate_uuid else None,
            "manual_evidence_removed_count": len(removed_manual_evidence_ids),
            "notes_reset": notes_reset,
        },
    )

    db.add(session)
    db.add(candidate)
    if candidate.draft is not None:
        db.add(candidate.draft)
    db.add(action_log_row)
    db.flush()

    response = CurationCandidateDecisionResponse(
        candidate=get_candidate_detail(db, candidate.id, session_id=session.id),
        session=get_session_detail(db, session.id),
        next_candidate_id=str(next_candidate_uuid) if next_candidate_uuid else None,
        action_log_entry=build_action_log_entry(action_log_row),
    )
    db.commit()
    return response


def _decision_candidate_status(action: CurationCandidateAction) -> CurationCandidateStatus:
    if action == CurationCandidateAction.ACCEPT:
        return CurationCandidateStatus.ACCEPTED
    if action == CurationCandidateAction.REJECT:
        return CurationCandidateStatus.REJECTED
    return CurationCandidateStatus.PENDING


def _decision_action_type(action: CurationCandidateAction) -> CurationActionType:
    if action == CurationCandidateAction.ACCEPT:
        return CurationActionType.CANDIDATE_ACCEPTED
    if action == CurationCandidateAction.REJECT:
        return CurationActionType.CANDIDATE_REJECTED
    return CurationActionType.CANDIDATE_RESET


def _decision_action_message(
    action: CurationCandidateAction,
    new_status: CurationCandidateStatus,
) -> str:
    if action == CurationCandidateAction.RESET:
        return "Candidate reset to pending"
    return f"Candidate marked as {new_status.value}"


def _normalize_optional_reason(reason: str | None) -> str | None:
    if reason is None:
        return None
    normalized = reason.strip()
    return normalized or None


def _current_candidate_id_after_delete(
    *,
    session: ReviewSessionModel,
    deleted_candidate: CurationCandidate,
    remaining_candidates: Sequence[CurationCandidate],
) -> UUID | None:
    if not remaining_candidates:
        return None

    if (
        session.current_candidate_id is not None
        and session.current_candidate_id != deleted_candidate.id
        and any(candidate.id == session.current_candidate_id for candidate in remaining_candidates)
    ):
        return session.current_candidate_id

    following_candidates = [
        candidate
        for candidate in remaining_candidates
        if candidate.order > deleted_candidate.order
    ]
    if following_candidates:
        return min(following_candidates, key=lambda candidate: candidate.order).id

    preceding_candidates = [
        candidate
        for candidate in remaining_candidates
        if candidate.order < deleted_candidate.order
    ]
    if preceding_candidates:
        return max(preceding_candidates, key=lambda candidate: candidate.order).id

    return remaining_candidates[0].id


def _reset_candidate_state(
    candidate: CurationCandidate,
    db: Session,
    *,
    occurred_at: datetime,
) -> tuple[list[str], list[str], bool]:
    draft = candidate.draft
    changed_field_keys: list[str] = []
    notes_reset = False
    draft_fields_changed = False
    manual_evidence_rows = [
        evidence_row
        for evidence_row in candidate.evidence_anchors
        if evidence_row.source == CurationEvidenceSource.MANUAL
    ]
    manual_evidence_ids = {str(evidence_row.id) for evidence_row in manual_evidence_rows}

    if draft is not None:
        updated_fields: list[dict[str, Any]] = []
        for field_payload in draft.fields or []:
            next_field_payload = dict(field_payload)
            seed_value = field_payload.get("seed_value")
            field_changed = (
                field_payload.get("value") != seed_value
                or bool(field_payload.get("dirty"))
                or bool(field_payload.get("stale_validation"))
            )
            if field_changed:
                changed_field_keys.append(str(field_payload.get("field_key")))

            next_field_payload["value"] = seed_value
            next_field_payload["dirty"] = False
            next_field_payload["stale_validation"] = False
            existing_anchor_ids = [
                str(anchor_id) for anchor_id in field_payload.get("evidence_anchor_ids") or []
            ]
            remaining_anchor_ids = [
                anchor_id
                for anchor_id in existing_anchor_ids
                if anchor_id not in manual_evidence_ids
            ]
            if field_changed or remaining_anchor_ids != existing_anchor_ids:
                next_field_payload["evidence_anchor_ids"] = remaining_anchor_ids
                draft_fields_changed = True
            updated_fields.append(next_field_payload)

        if draft.notes is not None:
            draft.notes = None
            notes_reset = True

        if draft_fields_changed or notes_reset:
            draft.fields = updated_fields
            draft.version += 1
            draft.updated_at = occurred_at
            draft.last_saved_at = occurred_at

    if manual_evidence_ids:
        candidate.evidence_anchors = [
            evidence_row
            for evidence_row in candidate.evidence_anchors
            if str(evidence_row.id) not in manual_evidence_ids
        ]
        for evidence_row in manual_evidence_rows:
            db.delete(evidence_row)

    return changed_field_keys, sorted(manual_evidence_ids), notes_reset


def _next_pending_candidate_id(
    session: ReviewSessionModel,
    current_candidate_id: UUID,
) -> UUID | None:
    ordered_candidates = sorted(session.candidates, key=lambda candidate_row: candidate_row.order)
    current_index = next(
        (
            index
            for index, candidate_row in enumerate(ordered_candidates)
            if candidate_row.id == current_candidate_id
        ),
        None,
    )
    if current_index is None or len(ordered_candidates) <= 1:
        return None

    candidate_count = len(ordered_candidates)
    for offset in range(1, candidate_count):
        next_index = (current_index + offset) % candidate_count
        candidate_row = ordered_candidates[next_index]
        if candidate_row.status == CurationCandidateStatus.PENDING:
            return candidate_row.id

    return None


def get_session_stats(
    db: Session,
    request: CurationSessionStatsRequest,
    *,
    current_user_id: str | None,
) -> CurationSessionStatsResponse:
    filtered_ids_subquery = _filtered_session_id_select(request.filters).subquery()

    count_row = db.execute(
        select(
            func.count(ReviewSessionModel.id),
            func.count(ReviewSessionModel.id).filter(ReviewSessionModel.status == CurationSessionStatus.NEW),
            func.count(ReviewSessionModel.id).filter(
                ReviewSessionModel.status == CurationSessionStatus.IN_PROGRESS
            ),
            func.count(ReviewSessionModel.id).filter(
                ReviewSessionModel.status == CurationSessionStatus.READY_FOR_SUBMISSION
            ),
            func.count(ReviewSessionModel.id).filter(ReviewSessionModel.status == CurationSessionStatus.PAUSED),
            func.count(ReviewSessionModel.id).filter(
                ReviewSessionModel.status == CurationSessionStatus.SUBMITTED
            ),
            func.count(ReviewSessionModel.id).filter(
                ReviewSessionModel.status == CurationSessionStatus.REJECTED
            ),
            func.count(ReviewSessionModel.id).filter(
                ReviewSessionModel.assigned_curator_id == current_user_id
            ),
            func.count(ReviewSessionModel.id).filter(
                ReviewSessionModel.assigned_curator_id.is_not(None)
                & (ReviewSessionModel.assigned_curator_id != current_user_id)
            ),
            func.count(ReviewSessionModel.id).filter(
                ReviewSessionModel.submitted_at >= datetime.now(timezone.utc) - timedelta(days=7)
            ),
        )
        .where(ReviewSessionModel.id.in_(select(filtered_ids_subquery.c.id)))
    ).one()

    adapter_count = db.scalar(
        select(func.count(func.distinct(ReviewSessionModel.adapter_key))).where(
            ReviewSessionModel.id.in_(select(filtered_ids_subquery.c.id))
        )
    ) or 0

    return CurationSessionStatsResponse(
        stats=CurationSessionStats(
            total_sessions=count_row[0],
            adapter_count=adapter_count,
            new_sessions=count_row[1],
            in_progress_sessions=count_row[2],
            ready_for_submission_sessions=count_row[3],
            paused_sessions=count_row[4],
            submitted_sessions=count_row[5],
            rejected_sessions=count_row[6],
            assigned_to_current_user=count_row[7],
            assigned_to_others=count_row[8],
            submitted_last_7_days=count_row[9],
        ),
        applied_filters=request.filters,
    )


def get_next_session(db: Session, request: CurationNextSessionRequest) -> CurationNextSessionResponse:
    ordered_sessions = _ordered_session_queue_subquery(
        request.filters,
        request.sort_by,
        request.sort_direction,
    )
    queue_row_select = select(
        ordered_sessions.c.id,
        ordered_sessions.c.position,
        ordered_sessions.c.total_sessions,
        ordered_sessions.c.previous_session_id,
        ordered_sessions.c.next_session_id,
    )

    if request.current_session_id is None:
        target_row = db.execute(
            queue_row_select.order_by(
                ordered_sessions.c.position.asc()
                if request.direction == CurationQueueNavigationDirection.NEXT
                else ordered_sessions.c.position.desc()
            ).limit(1)
        ).mappings().first()
        if target_row is None:
            return CurationNextSessionResponse(
                session=None,
                queue_context=CurationQueueContext(
                    filters=request.filters,
                    sort_by=request.sort_by,
                    sort_direction=request.sort_direction,
                    total_sessions=0,
                ),
            )
    else:
        normalized_current_session_id = _normalize_uuid(
            request.current_session_id,
            field_name="current_session_id",
        )
        current_row = db.execute(
            queue_row_select.where(ordered_sessions.c.id == normalized_current_session_id)
        ).mappings().first()
        if current_row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Current session is not part of the filtered queue",
            )

        target_position = current_row["position"] + (
            1 if request.direction == CurationQueueNavigationDirection.NEXT else -1
        )
        if target_position < 1 or target_position > current_row["total_sessions"]:
            return CurationNextSessionResponse(
                session=None,
                queue_context=_queue_context_from_row(
                    request=request,
                    row=current_row,
                ),
            )

        target_row = db.execute(
            queue_row_select.where(ordered_sessions.c.position == target_position)
        ).mappings().first()

    if target_row is None:
        return CurationNextSessionResponse(
            session=None,
            queue_context=CurationQueueContext(
                filters=request.filters,
                sort_by=request.sort_by,
                sort_direction=request.sort_direction,
                total_sessions=0,
            ),
        )

    target_session_id = _normalize_uuid(target_row["id"], field_name="session_id")
    session = _load_sessions_by_ids(db, [target_session_id], detailed=False)[0]
    document_map, user_map = _session_context_maps(db, [session])
    return CurationNextSessionResponse(
        session=_session_summary(session, document_map, user_map),
        queue_context=_queue_context_from_row(
            request=request,
            row=target_row,
        ),
    )


def _queue_context_from_row(
    *,
    request: CurationNextSessionRequest,
    row: Mapping[str, Any],
) -> CurationQueueContext:
    return CurationQueueContext(
        filters=request.filters,
        sort_by=request.sort_by,
        sort_direction=request.sort_direction,
        position=row["position"],
        total_sessions=row["total_sessions"],
        previous_session_id=row["previous_session_id"],
        next_session_id=row["next_session_id"],
    )


def _actor_claims_payload(actor_claims: dict[str, Any]) -> dict[str, str]:
    actor_id = actor_claims.get("sub") or actor_claims.get("uid") or "unknown"
    display_name = actor_claims.get("name") or actor_claims.get("email") or actor_id
    payload = {
        "actor_id": actor_id,
        "display_name": display_name,
    }
    if actor_claims.get("email"):
        payload["email"] = actor_claims["email"]
    return payload


def build_actor_claims_payload(actor_claims: dict[str, Any]) -> dict[str, str]:
    """Public actor payload helper shared across curation workspace services."""

    return _actor_claims_payload(actor_claims)


def upsert_prepared_session(
    db: Session,
    request: PreparedSessionUpsertRequest,
    *,
    manage_transaction: bool = True,
) -> PreparedSessionUpsertResult:
    """Create or refresh an unreviewed session with deterministic pipeline output."""

    try:
        session_row, created = _load_or_initialize_prepared_session(db, request)
        _apply_prepared_session_metadata(session_row, request)

        if not created and request.replace_existing_candidates:
            _clear_prepared_session_children(db, session_row)

        candidate_ids = _persist_prepared_candidates(
            db,
            session_row,
            request.candidates,
            prepared_at=request.prepared_at,
        )

        session_row.current_candidate_id = (
            _normalize_uuid(candidate_ids[0], field_name="candidate_id")
            if candidate_ids
            else None
        )
        _apply_progress_counts(session_row, request.candidates)

        validation_log_row = _persist_session_validation_snapshot(
            db,
            session_row,
            request.session_validation_snapshot,
        )

        if created:
            created_actor = dict(request.session_created_actor) or None
            db.add(
                SessionActionLogModel(
                    session_id=session_row.id,
                    action_type=CurationActionType.SESSION_CREATED,
                    actor_type=request.session_created_actor_type,
                    actor=created_actor,
                    occurred_at=request.prepared_at,
                    new_session_status=session_row.status,
                    message=request.session_created_message,
                )
            )

        if not created:
            session_row.session_version += 1

        db.add(session_row)
        if validation_log_row is not None:
            db.add(validation_log_row)
        if manage_transaction:
            db.commit()
        else:
            db.flush()

        return PreparedSessionUpsertResult(
            session_id=str(session_row.id),
            created=created,
            candidate_ids=candidate_ids,
        )
    except Exception:
        if manage_transaction:
            db.rollback()
        raise


def _load_or_initialize_prepared_session(
    db: Session,
    request: PreparedSessionUpsertRequest,
) -> tuple[ReviewSessionModel, bool]:
    if request.review_session_id is None:
        session_row = ReviewSessionModel(
            document_id=_normalize_uuid(request.document_id, field_name="document_id"),
            adapter_key=request.adapter_key,
            prepared_at=request.prepared_at,
            created_at=request.prepared_at,
            updated_at=request.prepared_at,
        )
        db.add(session_row)
        db.flush()
        return session_row, True

    session_id = _normalize_uuid(request.review_session_id, field_name="review_session_id")
    session_row = db.scalars(
        select(ReviewSessionModel)
        .where(ReviewSessionModel.id == session_id)
        .options(*PREPARED_SESSION_LOAD_OPTIONS)
    ).first()

    if session_row is None:
        raise LookupError(f"Curation review session {session_id} not found")

    if session_row.reviewed_candidates > 0 or session_row.submissions:
        raise ValueError(
            "Prepared-session updates require an unreviewed session without submissions"
        )

    if any(
        action_log_entry.candidate_id is not None or action_log_entry.draft_id is not None
        for action_log_entry in session_row.action_log_entries
    ):
        raise ValueError(
            "Prepared-session updates cannot replace candidate data after candidate-level activity"
        )

    return session_row, False


def _apply_prepared_session_metadata(
    session_row: ReviewSessionModel,
    request: PreparedSessionUpsertRequest,
) -> None:
    session_row.status = request.status
    session_row.adapter_key = request.adapter_key
    session_row.profile_key = None
    session_row.document_id = _normalize_uuid(request.document_id, field_name="document_id")
    session_row.flow_run_id = request.flow_run_id
    session_row.assigned_curator_id = request.assigned_curator_id
    session_row.created_by_id = request.created_by_id
    session_row.notes = request.notes
    session_row.tags = list(request.tags)
    session_row.warnings = list(request.warnings)
    session_row.prepared_at = request.prepared_at
    session_row.last_worked_at = None
    session_row.paused_at = None
    session_row.rejection_reason = None
    session_row.updated_at = request.prepared_at
    if request.status != CurationSessionStatus.SUBMITTED:
        session_row.submitted_at = None


def _clear_prepared_session_children(db: Session, session_row: ReviewSessionModel) -> None:
    candidate_ids = [candidate.id for candidate in session_row.candidates]

    _delete_session_validation_snapshots(db, session_id=session_row.id)

    if candidate_ids:
        _delete_candidate_children(
            db,
            session_id=session_row.id,
            candidate_ids=candidate_ids,
        )

    session_row.candidates = []
    session_row.validation_snapshots = []
    session_row.current_candidate_id = None


def _delete_session_validation_snapshots(
    db: Session,
    *,
    session_id: UUID,
) -> None:
    db.execute(
        delete(ValidationSnapshotModel).where(
            ValidationSnapshotModel.session_id == session_id
        )
    )


def _delete_candidate_children(
    db: Session,
    *,
    session_id: UUID,
    candidate_ids: Sequence[UUID],
) -> None:
    if not candidate_ids:
        return

    db.execute(
        delete(SessionActionLogModel).where(
            SessionActionLogModel.session_id == session_id,
            SessionActionLogModel.candidate_id.in_(candidate_ids),
        )
    )
    db.execute(
        delete(EvidenceRecordModel).where(
            EvidenceRecordModel.candidate_id.in_(candidate_ids)
        )
    )
    db.execute(
        delete(DraftModel).where(DraftModel.candidate_id.in_(candidate_ids))
    )
    db.execute(
        delete(CurationCandidate).where(CurationCandidate.id.in_(candidate_ids))
    )


def _persist_prepared_candidates(
    db: Session,
    session_row: ReviewSessionModel,
    candidates: Sequence[PreparedCandidateInput],
    *,
    prepared_at: datetime,
) -> list[str]:
    candidate_ids: list[str] = []

    for candidate_input in sorted(candidates, key=lambda item: item.order):
        candidate_row = CurationCandidate(
            session_id=session_row.id,
            source=candidate_input.source,
            status=candidate_input.status,
            order=candidate_input.order,
            adapter_key=candidate_input.adapter_key,
            profile_key=None,
            display_label=candidate_input.display_label,
            secondary_label=candidate_input.secondary_label,
            conversation_summary=candidate_input.conversation_summary,
            extraction_result_id=(
                _normalize_uuid(
                    candidate_input.extraction_result_id,
                    field_name="extraction_result_id",
                )
                if candidate_input.extraction_result_id is not None
                else None
            ),
            normalized_payload=dict(candidate_input.normalized_payload),
            candidate_metadata=dict(candidate_input.metadata),
            created_at=prepared_at,
            updated_at=prepared_at,
        )
        db.add(candidate_row)
        db.flush()

        evidence_anchor_ids_by_field = _persist_candidate_evidence_records(
            db,
            candidate_row,
            candidate_input.evidence_records,
            created_at=prepared_at,
        )

        draft_row = DraftModel(
            candidate_id=candidate_row.id,
            adapter_key=candidate_input.adapter_key,
            title=candidate_input.draft_title,
            summary=candidate_input.draft_summary,
            fields=[
                _draft_field_payload(
                    field_input,
                    evidence_anchor_ids_by_field=evidence_anchor_ids_by_field,
                    field_results=(
                        candidate_input.validation_snapshot.field_results
                        if candidate_input.validation_snapshot is not None
                        else {}
                    ),
                )
                for field_input in candidate_input.draft_fields
            ],
            notes=candidate_input.draft_notes,
            draft_metadata=dict(candidate_input.draft_metadata),
            created_at=prepared_at,
            updated_at=prepared_at,
        )
        db.add(draft_row)

        if candidate_input.validation_snapshot is not None:
            db.add(
                _validation_snapshot_row(
                    session_row=session_row,
                    candidate_id=candidate_row.id,
                    snapshot=candidate_input.validation_snapshot,
                )
            )

        candidate_ids.append(str(candidate_row.id))

    return candidate_ids


def _persist_candidate_evidence_records(
    db: Session,
    candidate_row: CurationCandidate,
    evidence_records: Sequence[PreparedEvidenceRecordInput],
    *,
    created_at: datetime,
) -> dict[str, list[str]]:
    evidence_anchor_ids_by_field: dict[str, list[str]] = {}

    for evidence_input in evidence_records:
        evidence_row = EvidenceRecordModel(
            candidate_id=candidate_row.id,
            source=evidence_input.source,
            field_keys=list(evidence_input.field_keys),
            field_group_keys=list(evidence_input.field_group_keys),
            is_primary=evidence_input.is_primary,
            anchor=dict(evidence_input.anchor),
            warnings=list(evidence_input.warnings),
            created_at=created_at,
            updated_at=created_at,
        )
        db.add(evidence_row)
        db.flush()

        anchor_id = str(evidence_row.id)
        for field_key in evidence_input.field_keys:
            evidence_anchor_ids_by_field.setdefault(field_key, []).append(anchor_id)

    return evidence_anchor_ids_by_field


def _draft_field_payload(
    field_input: PreparedDraftFieldInput,
    *,
    evidence_anchor_ids_by_field: Mapping[str, Sequence[str]],
    field_results: Mapping[str, FieldValidationResult],
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "field_key": field_input.field_key,
        "label": field_input.label,
        "value": field_input.value,
        "seed_value": field_input.seed_value,
        "field_type": field_input.field_type,
        "group_key": field_input.group_key,
        "group_label": field_input.group_label,
        "order": field_input.order,
        "required": field_input.required,
        "read_only": field_input.read_only,
        "dirty": field_input.dirty,
        "stale_validation": field_input.stale_validation,
        "evidence_anchor_ids": list(
            evidence_anchor_ids_by_field.get(field_input.field_key, [])
        ),
        "metadata": dict(field_input.metadata),
    }

    validation_result = field_results.get(field_input.field_key)
    if validation_result is not None:
        payload["validation_result"] = validation_result.model_dump(mode="json")

    return payload


def _validation_snapshot_row(
    *,
    session_row: ReviewSessionModel,
    candidate_id: UUID | None,
    snapshot: PreparedValidationSnapshotInput,
) -> ValidationSnapshotModel:
    return ValidationSnapshotModel(
        scope=snapshot.scope,
        session_id=session_row.id,
        candidate_id=candidate_id,
        adapter_key=snapshot.adapter_key or session_row.adapter_key,
        state=snapshot.state,
        field_results={
            field_key: result.model_dump(mode="json")
            for field_key, result in snapshot.field_results.items()
        },
        summary=snapshot.summary.model_dump(mode="json"),
        warnings=list(snapshot.warnings),
        requested_at=snapshot.requested_at,
        completed_at=snapshot.completed_at,
    )


def _persist_session_validation_snapshot(
    db: Session,
    session_row: ReviewSessionModel,
    snapshot: PreparedValidationSnapshotInput | None,
) -> SessionActionLogModel | None:
    if snapshot is None:
        return None

    db.add(
        _validation_snapshot_row(
            session_row=session_row,
            candidate_id=None,
            snapshot=snapshot,
        )
    )
    return SessionActionLogModel(
        session_id=session_row.id,
        action_type=CurationActionType.VALIDATION_COMPLETED,
        actor_type=CurationActorType.SYSTEM,
        occurred_at=snapshot.completed_at or snapshot.requested_at or session_row.prepared_at,
        message="Deterministic post-agent validation completed",
        action_metadata={
            "validation_state": snapshot.state.value,
            "validation_scope": snapshot.scope.value,
        },
    )


def _apply_progress_counts(
    session_row: ReviewSessionModel,
    candidates: Sequence[PreparedCandidateInput],
) -> None:
    _apply_candidate_progress_counts(session_row, candidates)
def _apply_candidate_progress_counts(
    session_row: ReviewSessionModel,
    candidates: Sequence[CandidateProgressCountsInput],
) -> None:
    total_candidates = len(candidates)
    pending_candidates = sum(
        1 for candidate in candidates if candidate.status == CurationCandidateStatus.PENDING
    )
    accepted_candidates = sum(
        1 for candidate in candidates if candidate.status == CurationCandidateStatus.ACCEPTED
    )
    rejected_candidates = sum(
        1 for candidate in candidates if candidate.status == CurationCandidateStatus.REJECTED
    )
    manual_candidates = sum(
        1 for candidate in candidates if candidate.source == CurationCandidateSource.MANUAL
    )

    session_row.total_candidates = total_candidates
    session_row.pending_candidates = pending_candidates
    session_row.accepted_candidates = accepted_candidates
    session_row.rejected_candidates = rejected_candidates
    session_row.manual_candidates = manual_candidates
    session_row.reviewed_candidates = total_candidates - pending_candidates


__all__ = [
    "build_action_log_entry",
    "build_actor_claims_payload",
    "build_evidence_record",
    "create_manual_candidate",
    "delete_candidate",
    "execute_submission",
    "get_next_session",
    "get_candidate_detail",
    "get_submission",
    "find_reusable_prepared_session",
    "get_session_detail",
    "get_session_workspace",
    "get_session_stats",
    "list_sessions",
    "normalize_uuid",
    "PreparedCandidateInput",
    "PreparedDraftFieldInput",
    "PreparedEvidenceRecordInput",
    "PreparedSessionUpsertRequest",
    "PreparedSessionUpsertResult",
    "ReusablePreparedSessionContext",
    "PreparedValidationSnapshotInput",
    "retry_submission",
    "submission_preview",
    "upsert_prepared_session",
    "update_candidate_draft",
    "update_session",
    "validate_candidate",
    "validate_session",
]
