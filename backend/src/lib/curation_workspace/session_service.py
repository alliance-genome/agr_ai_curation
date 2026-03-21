"""Query and mapping helpers for curation workspace session endpoints."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from math import ceil
from typing import Any, Iterable, Mapping, Sequence
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import String, asc, case, delete, desc, exists, func, or_, select
from sqlalchemy.orm import Session, selectinload

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
    CurationCandidateSource,
    CurationCandidateStatus,
    CurationDocumentRef,
    CurationEvidenceSource,
    CurationEvidenceQualityCounts,
    CurationEvidenceSummary,
    CurationExtractionResultRecord,
    CurationFlowRunSummary,
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
    CurationSessionUpdateRequest,
    CurationSessionUpdateResponse,
    CurationSortDirection,
    CurationSubmissionRecord,
    CurationValidationScope,
    CurationValidationSnapshotState,
    CurationValidationSummary,
    FieldValidationResult,
    SubmissionPayloadContract,
)


SUMMARY_LOAD_OPTIONS = (
    selectinload(ReviewSessionModel.candidates).selectinload(CurationCandidate.evidence_anchors),
    selectinload(ReviewSessionModel.validation_snapshots),
)

DETAIL_LOAD_OPTIONS = (
    *SUMMARY_LOAD_OPTIONS,
    selectinload(ReviewSessionModel.candidates).selectinload(CurationCandidate.extraction_result),
    selectinload(ReviewSessionModel.submissions),
)

PREPARED_SESSION_LOAD_OPTIONS = (
    selectinload(ReviewSessionModel.candidates),
    selectinload(ReviewSessionModel.submissions),
    selectinload(ReviewSessionModel.action_log_entries),
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
class PreparedCandidateInput:
    """Prepared candidate payload emitted by the deterministic pipeline."""

    source: CurationCandidateSource
    status: CurationCandidateStatus
    order: int
    adapter_key: str
    profile_key: str | None = None
    display_label: str | None = None
    secondary_label: str | None = None
    confidence: float | None = None
    conversation_summary: str | None = None
    unresolved_ambiguities: list[str] = field(default_factory=list)
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
    profile_key: str | None = None
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


def _apply_filters(statement: Any, filters: CurationSessionFilters) -> Any:
    if filters.statuses:
        statement = statement.where(ReviewSessionModel.status.in_(filters.statuses))

    if filters.adapter_keys:
        statement = statement.where(ReviewSessionModel.adapter_key.in_(filters.adapter_keys))

    if filters.profile_keys:
        statement = statement.where(ReviewSessionModel.profile_key.in_(filters.profile_keys))

    if filters.curator_ids:
        statement = statement.where(ReviewSessionModel.assigned_curator_id.in_(filters.curator_ids))

    if filters.flow_run_id:
        statement = statement.where(ReviewSessionModel.flow_run_id == filters.flow_run_id)

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

    if filters.domain_keys:
        domain_exists = (
            select(ExtractionResultModel.id)
            .select_from(CurationCandidate)
            .join(
                ExtractionResultModel,
                CurationCandidate.extraction_result_id == ExtractionResultModel.id,
            )
            .where(CurationCandidate.session_id == ReviewSessionModel.id)
            .where(ExtractionResultModel.domain_key.in_(filters.domain_keys))
        )
        statement = statement.where(exists(domain_exists))

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


def _adapter_ref(session: ReviewSessionModel) -> CurationAdapterRef:
    display_label = session.adapter_key.replace("_", " ").title()
    profile_label = (
        session.profile_key.replace("_", " ").title()
        if session.profile_key
        else None
    )
    return CurationAdapterRef(
        adapter_key=session.adapter_key,
        profile_key=session.profile_key,
        display_label=display_label,
        profile_label=profile_label,
        metadata={},
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


def _latest_validation_summary(session: ReviewSessionModel) -> CurationValidationSummary | None:
    if not session.validation_snapshots:
        return None

    session_level_snapshots = [
        snapshot
        for snapshot in session.validation_snapshots
        if snapshot.candidate_id is None
    ]
    snapshots = sorted(
        session_level_snapshots or list(session.validation_snapshots),
        key=lambda snapshot: snapshot.completed_at
        or snapshot.requested_at
        or datetime.min.replace(tzinfo=timezone.utc),
    )
    snapshot = snapshots[-1]
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


def _evidence_summary(session: ReviewSessionModel) -> CurationEvidenceSummary | None:
    anchors = [
        evidence_anchor
        for candidate in session.candidates
        for evidence_anchor in candidate.evidence_anchors
    ]
    if not anchors:
        return None

    quality_counts = CurationEvidenceQualityCounts()
    warnings: list[str] = []

    for evidence_anchor in anchors:
        locator_quality = str((evidence_anchor.anchor or {}).get("locator_quality") or "")
        if locator_quality == "exact_quote":
            quality_counts.exact_quote += 1
        elif locator_quality == "normalized_quote":
            quality_counts.normalized_quote += 1
        elif locator_quality == "section_only":
            quality_counts.section_only += 1
        elif locator_quality == "page_only":
            quality_counts.page_only += 1
        elif locator_quality == "document_only":
            quality_counts.document_only += 1
        else:
            quality_counts.unresolved += 1
        warnings.extend(evidence_anchor.warnings or [])

    total_anchor_count = len(anchors)
    resolved_anchor_count = (
        quality_counts.exact_quote
        + quality_counts.normalized_quote
        + quality_counts.section_only
        + quality_counts.page_only
    )
    viewer_highlightable_anchor_count = (
        quality_counts.exact_quote + quality_counts.normalized_quote
    )

    return CurationEvidenceSummary(
        total_anchor_count=total_anchor_count,
        resolved_anchor_count=resolved_anchor_count,
        viewer_highlightable_anchor_count=viewer_highlightable_anchor_count,
        quality_counts=quality_counts,
        degraded=bool(quality_counts.document_only or quality_counts.unresolved or warnings),
        warnings=sorted(set(warnings)),
    )


def _submission_payload(record: SubmissionModel) -> SubmissionPayloadContract | None:
    if record.payload is None:
        return None
    payload_json = record.payload if not isinstance(record.payload, str) else None
    payload_text = record.payload if isinstance(record.payload, str) else None
    candidate_ids = [
        readiness.get("candidate_id")
        for readiness in record.readiness or []
        if isinstance(readiness, dict) and readiness.get("candidate_id")
    ]
    return SubmissionPayloadContract(
        mode=record.mode,
        target_key=record.target_key,
        adapter_key=record.adapter_key,
        candidate_ids=candidate_ids,
        payload_json=payload_json,
        payload_text=payload_text,
        warnings=list(record.warnings or []),
    )


def _submission_record(record: SubmissionModel) -> CurationSubmissionRecord:
    return CurationSubmissionRecord(
        submission_id=str(record.id),
        session_id=str(record.session_id),
        adapter_key=record.adapter_key,
        mode=record.mode,
        target_key=record.target_key,
        status=record.status,
        readiness=list(record.readiness or []),
        payload=_submission_payload(record),
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
                profile_key=extraction_result.profile_key,
                domain_key=extraction_result.domain_key,
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
    session: ReviewSessionModel,
    document_map: dict[UUID, PDFDocument],
    user_map: dict[str, User],
) -> CurationReviewSession:
    summary = _session_summary(session, document_map, user_map)
    latest_submission = _submission_record(session.submissions[-1]) if session.submissions else None
    return CurationReviewSession(
        **summary.model_dump(),
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


def list_sessions(db: Session, request: CurationSessionListRequest) -> CurationSessionListResponse:
    ordered_id_select = _ordered_session_id_select(
        request.filters,
        request.sort_by,
        request.sort_direction,
    )
    total_items = db.scalar(select(func.count()).select_from(_filtered_session_id_select(request.filters).subquery())) or 0

    offset = (request.page - 1) * request.page_size
    session_ids = list(db.scalars(ordered_id_select.offset(offset).limit(request.page_size)).all())
    sessions = _load_sessions_by_ids(db, session_ids, detailed=False)
    document_map, user_map = _session_context_maps(db, sessions)
    summaries = [_session_summary(session, document_map, user_map) for session in sessions]

    flow_run_groups: list[CurationFlowRunSummary] = []
    if request.group_by_flow_run:
        filtered_ids_subquery = _filtered_session_id_select(request.filters).subquery()
        group_rows = db.execute(
            select(
                ReviewSessionModel.flow_run_id,
                func.count(ReviewSessionModel.id),
                func.count(ReviewSessionModel.id).filter(ReviewSessionModel.reviewed_candidates > 0),
                func.count(ReviewSessionModel.id).filter(ReviewSessionModel.pending_candidates > 0),
                func.count(ReviewSessionModel.id).filter(
                    ReviewSessionModel.status == CurationSessionStatus.SUBMITTED
                ),
                func.max(func.coalesce(ReviewSessionModel.last_worked_at, ReviewSessionModel.prepared_at)),
            )
            .where(ReviewSessionModel.id.in_(select(filtered_ids_subquery.c.id)))
            .where(ReviewSessionModel.flow_run_id.is_not(None))
            .group_by(ReviewSessionModel.flow_run_id)
            .order_by(func.max(func.coalesce(ReviewSessionModel.last_worked_at, ReviewSessionModel.prepared_at)).desc())
        ).all()
        flow_run_groups = [
            CurationFlowRunSummary(
                flow_run_id=row[0],
                display_label=row[0],
                session_count=row[1],
                reviewed_count=row[2],
                pending_count=row[3],
                submitted_count=row[4],
                last_activity_at=row[5],
            )
            for row in group_rows
        ]

    return CurationSessionListResponse(
        sessions=summaries,
        page_info=_page_info(
            page=request.page,
            page_size=request.page_size,
            total_items=total_items,
        ),
        applied_filters=request.filters,
        sort_by=request.sort_by,
        sort_direction=request.sort_direction,
        flow_run_groups=flow_run_groups,
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
    return _session_detail(session, document_map, user_map)


def find_reusable_prepared_session(
    db: Session,
    *,
    document_id: str,
    adapter_key: str,
    profile_key: str | None,
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

    if profile_key is None:
        statement = statement.where(ReviewSessionModel.profile_key.is_(None))
    else:
        statement = statement.where(ReviewSessionModel.profile_key == profile_key)

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
        session=_session_detail(updated_session, document_map, user_map),
        action_log_entry=_action_log_entry(action_log_row),
    )


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

    domain_count = db.scalar(
        select(func.count(func.distinct(ExtractionResultModel.domain_key)))
        .select_from(CurationCandidate)
        .join(
            ExtractionResultModel,
            CurationCandidate.extraction_result_id == ExtractionResultModel.id,
        )
        .where(CurationCandidate.session_id.in_(select(filtered_ids_subquery.c.id)))
        .where(ExtractionResultModel.domain_key.is_not(None))
    ) or 0

    return CurationSessionStatsResponse(
        stats=CurationSessionStats(
            total_sessions=count_row[0],
            domain_count=domain_count,
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
            profile_key=request.profile_key,
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
    session_row.profile_key = request.profile_key
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

    db.execute(
        delete(ValidationSnapshotModel).where(
            ValidationSnapshotModel.session_id == session_row.id
        )
    )

    if candidate_ids:
        db.execute(
            delete(SessionActionLogModel).where(
                SessionActionLogModel.session_id == session_row.id,
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

    session_row.candidates = []
    session_row.validation_snapshots = []
    session_row.current_candidate_id = None


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
            profile_key=candidate_input.profile_key,
            display_label=candidate_input.display_label,
            secondary_label=candidate_input.secondary_label,
            confidence=candidate_input.confidence,
            conversation_summary=candidate_input.conversation_summary,
            unresolved_ambiguities=list(candidate_input.unresolved_ambiguities),
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
    "get_next_session",
    "find_reusable_prepared_session",
    "get_session_detail",
    "get_session_stats",
    "list_sessions",
    "PreparedCandidateInput",
    "PreparedDraftFieldInput",
    "PreparedEvidenceRecordInput",
    "PreparedSessionUpsertRequest",
    "PreparedSessionUpsertResult",
    "ReusablePreparedSessionContext",
    "PreparedValidationSnapshotInput",
    "upsert_prepared_session",
    "update_session",
]
