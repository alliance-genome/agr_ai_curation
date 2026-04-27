"""Read/query behavior for curation workspace sessions."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from math import ceil
from typing import Any, Mapping, Sequence
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import String, case, exists, func, or_, select
from sqlalchemy.orm import Session

from src.lib.curation_workspace.models import (
    CurationCandidate,
    CurationEvidenceRecord as EvidenceRecordModel,
    CurationExtractionResultRecord as ExtractionResultModel,
    CurationReviewSession as ReviewSessionModel,
    CurationValidationSnapshot as ValidationSnapshotModel,
)
from src.lib.curation_workspace.session_common import (
    LIKE_ESCAPE_CHAR,
    _escape_like_pattern,
    _normalize_uuid,
    _ordered_clause,
)
from src.lib.curation_workspace.session_loading import (
    CANDIDATE_DETAIL_LOAD_OPTIONS,
    DETAIL_LOAD_OPTIONS,
    SUMMARY_LOAD_OPTIONS,
)
from src.lib.curation_workspace.session_serializers import (
    _action_log_entry,
    _candidate_detail,
    _candidate_has_entity_tag_fields,
    _candidate_payload,
    _entity_tag_payload,
    _load_documents,
    _load_users,
    _session_detail,
    _session_summary,
    _submission_record,
)
from src.models.sql.pdf_document import PDFDocument
from src.models.sql.user import User
from src.schemas.curation_workspace import (
    CurationFlowRunListRequest,
    CurationFlowRunListResponse,
    CurationFlowRunSessionsRequest,
    CurationFlowRunSessionsResponse,
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
    CurationSessionSortField,
    CurationSessionStats,
    CurationSessionStatsRequest,
    CurationSessionStatsResponse,
    CurationSessionStatus,
    CurationSessionSummary,
    CurationSortDirection,
    CurationWorkspace as CurationWorkspacePayload,
    CurationWorkspaceResponse,
    CurationValidationSnapshotState,
)

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

__all__ = [
    "get_candidate_detail",
    "get_next_session",
    "get_session_detail",
    "get_session_stats",
    "get_session_workspace",
    "list_flow_run_sessions",
    "list_flow_runs",
    "list_sessions",
]
