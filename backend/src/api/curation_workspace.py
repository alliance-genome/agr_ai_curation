"""Inventory-facing curation workspace session endpoints."""

from __future__ import annotations

from datetime import datetime
import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from src.api.auth import get_auth_dependency
from src.lib.curation_workspace.bootstrap_service import (
    bootstrap_document_session,
    create_manual_session,
    get_document_bootstrap_availability,
    prepare_chat_curation_sessions,
)
from src.lib.curation_workspace.curation_prep_invocation import (
    build_chat_curation_prep_preview,
)
from src.lib.curation_workspace.evidence_service import (
    create_manual_evidence,
    recompute_evidence,
    resolve_evidence,
)
from src.lib.curation_workspace.saved_view_service import (
    create_saved_view as create_saved_view_record,
    delete_saved_view as delete_saved_view_record,
    list_saved_views as list_saved_view_records,
)
from src.lib.http_errors import raise_sanitized_http_exception
from src.lib.curation_workspace.session_service import (
    create_manual_candidate,
    delete_candidate,
    decide_candidate,
    execute_submission,
    get_submission,
    get_next_session,
    get_session_detail,
    get_session_workspace,
    get_session_stats,
    list_flow_run_sessions,
    list_flow_runs,
    list_sessions,
    retry_submission,
    submission_preview,
    update_candidate_draft,
    update_session,
    validate_candidate,
    validate_session,
)
from src.models.sql.database import get_db
from src.schemas.curation_prep import (
    CurationPrepChatPreviewResponse,
    CurationPrepChatRunRequest,
    CurationPrepChatRunResponse,
)
from src.schemas.curation_workspace import (
    CurationCandidateDecisionRequest,
    CurationCandidateDecisionResponse,
    CurationCandidateDeleteResponse,
    CurationCandidateDraftUpdateRequest,
    CurationCandidateDraftUpdateResponse,
    CurationCandidateValidationRequest,
    CurationCandidateValidationResponse,
    CurationDateRange,
    CurationDocumentBootstrapAvailabilityResponse,
    CurationDocumentBootstrapRequest,
    CurationDocumentBootstrapResponse,
    CurationEvidenceRecomputeRequest,
    CurationEvidenceRecomputeResponse,
    CurationEvidenceResolveRequest,
    CurationEvidenceResolveResponse,
    CurationFlowRunListRequest,
    CurationFlowRunListResponse,
    CurationFlowRunSessionsRequest,
    CurationFlowRunSessionsResponse,
    CurationManualCandidateCreateRequest,
    CurationManualCandidateCreateResponse,
    CurationNextSessionRequest,
    CurationNextSessionResponse,
    CurationManualEvidenceCreateRequest,
    CurationManualEvidenceCreateResponse,
    CurationQueueNavigationDirection,
    CurationReviewSession,
    CurationSavedViewCreateRequest,
    CurationSavedViewCreateResponse,
    CurationSavedViewDeleteResponse,
    CurationSavedViewListResponse,
    CurationSessionFilters,
    CurationSessionCreateRequest,
    CurationSessionCreateResponse,
    CurationSessionListRequest,
    CurationSessionListResponse,
    CurationSessionSortField,
    CurationSessionStatsRequest,
    CurationSessionStatsResponse,
    CurationSessionStatus,
    CurationSubmissionExecuteRequest,
    CurationSubmissionExecuteResponse,
    CurationSubmissionHistoryResponse,
    CurationSubmissionPreviewRequest,
    CurationSubmissionPreviewResponse,
    CurationSubmissionRetryRequest,
    CurationSubmissionRetryResponse,
    CurationSessionValidationRequest,
    CurationSessionValidationResponse,
    CurationSessionUpdateRequest,
    CurationSessionUpdateResponse,
    CurationSortDirection,
    CurationWorkspaceResponse,
)
from src.services.user_service import set_global_user_from_cognito


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/curation-workspace", tags=["Curation Workspace"])


def _date_range(from_at: datetime | None, to_at: datetime | None) -> CurationDateRange | None:
    if from_at is None and to_at is None:
        return None
    return CurationDateRange(from_at=from_at, to_at=to_at)


def _session_filters_from_query(
    statuses: Annotated[list[CurationSessionStatus] | None, Query(alias="status")] = None,
    adapter_keys: Annotated[list[str] | None, Query(alias="adapter_key")] = None,
    curator_ids: Annotated[list[str] | None, Query(alias="curator_id")] = None,
    tags: Annotated[list[str] | None, Query(alias="tag")] = None,
    flow_run_id: str | None = Query(default=None),
    origin_session_id: str | None = Query(default=None),
    document_id: str | None = Query(default=None),
    search: str | None = Query(default=None),
    saved_view_id: str | None = Query(default=None, alias="saved_view_id"),
    prepared_from: datetime | None = Query(default=None, alias="prepared_from"),
    prepared_to: datetime | None = Query(default=None, alias="prepared_to"),
    last_worked_from: datetime | None = Query(default=None, alias="last_worked_from"),
    last_worked_to: datetime | None = Query(default=None, alias="last_worked_to"),
) -> CurationSessionFilters:
    return CurationSessionFilters(
        statuses=statuses or [],
        adapter_keys=adapter_keys or [],
        curator_ids=curator_ids or [],
        tags=tags or [],
        flow_run_id=flow_run_id,
        origin_session_id=origin_session_id,
        document_id=document_id,
        search=search,
        saved_view_id=saved_view_id,
        prepared_between=_date_range(prepared_from, prepared_to),
        last_worked_between=_date_range(last_worked_from, last_worked_to),
    )


def _build_list_request(
    filters: CurationSessionFilters = Depends(_session_filters_from_query),
    sort_by: CurationSessionSortField = Query(default=CurationSessionSortField.PREPARED_AT),
    sort_direction: CurationSortDirection = Query(default=CurationSortDirection.DESC),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=200),
    group_by_flow_run: bool = Query(default=False),
) -> CurationSessionListRequest:
    return CurationSessionListRequest(
        filters=filters,
        sort_by=sort_by,
        sort_direction=sort_direction,
        page=page,
        page_size=page_size,
        group_by_flow_run=group_by_flow_run,
    )


def _build_stats_request(
    filters: CurationSessionFilters = Depends(_session_filters_from_query),
) -> CurationSessionStatsRequest:
    return CurationSessionStatsRequest(filters=filters)


def _build_flow_run_list_request(
    filters: CurationSessionFilters = Depends(_session_filters_from_query),
) -> CurationFlowRunListRequest:
    return CurationFlowRunListRequest(filters=filters)


def _build_flow_run_sessions_request(
    run_id: str,
    filters: CurationSessionFilters = Depends(_session_filters_from_query),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=200),
) -> CurationFlowRunSessionsRequest:
    return CurationFlowRunSessionsRequest(
        flow_run_id=run_id,
        filters=filters,
        page=page,
        page_size=page_size,
    )


def _bootstrap_request_from_query(
    adapter_key: str | None = Query(default=None),
    flow_run_id: str | None = Query(default=None),
    origin_session_id: str | None = Query(default=None),
) -> CurationDocumentBootstrapRequest:
    return CurationDocumentBootstrapRequest(
        adapter_key=adapter_key,
        flow_run_id=flow_run_id,
        origin_session_id=origin_session_id,
    )


def _build_next_request(
    filters: CurationSessionFilters = Depends(_session_filters_from_query),
    current_session_id: str | None = Query(default=None),
    direction: CurationQueueNavigationDirection = Query(
        default=CurationQueueNavigationDirection.NEXT
    ),
    sort_by: CurationSessionSortField = Query(default=CurationSessionSortField.PREPARED_AT),
    sort_direction: CurationSortDirection = Query(default=CurationSortDirection.DESC),
) -> CurationNextSessionRequest:
    return CurationNextSessionRequest(
        current_session_id=current_session_id,
        direction=direction,
        filters=filters,
        sort_by=sort_by,
        sort_direction=sort_direction,
    )


def _current_user_id(user: dict) -> str | None:
    return user.get("sub") or user.get("uid")


def _require_current_user_id(user: dict) -> str:
    user_id = _current_user_id(user)
    if not user_id:
        raise HTTPException(status_code=401, detail="User identifier not found in token")
    return user_id


@router.get("/sessions", response_model=CurationSessionListResponse)
async def list_review_sessions(
    request: CurationSessionListRequest = Depends(_build_list_request),
    user: dict = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> CurationSessionListResponse:
    set_global_user_from_cognito(db, user)
    return list_sessions(db, request)


@router.post("/sessions", response_model=CurationSessionCreateResponse)
async def post_review_session(
    request: CurationSessionCreateRequest,
    user: dict = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> CurationSessionCreateResponse:
    set_global_user_from_cognito(db, user)
    user_id = _require_current_user_id(user)
    return create_manual_session(
        request,
        current_user_id=user_id,
        actor_claims=user,
        db=db,
    )


@router.get("/views", response_model=CurationSavedViewListResponse)
async def get_saved_views(
    user: dict = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> CurationSavedViewListResponse:
    user_id = _require_current_user_id(user)
    set_global_user_from_cognito(db, user)
    return list_saved_view_records(db, current_user_id=user_id)


@router.post("/views", response_model=CurationSavedViewCreateResponse)
async def post_saved_view(
    request: CurationSavedViewCreateRequest,
    user: dict = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> CurationSavedViewCreateResponse:
    user_id = _require_current_user_id(user)
    set_global_user_from_cognito(db, user)
    return create_saved_view_record(
        db,
        request,
        current_user_id=user_id,
    )


@router.delete("/views/{view_id}", response_model=CurationSavedViewDeleteResponse)
async def delete_saved_view(
    view_id: UUID,
    user: dict = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> CurationSavedViewDeleteResponse:
    user_id = _require_current_user_id(user)
    set_global_user_from_cognito(db, user)
    return delete_saved_view_record(
        db,
        view_id,
        current_user_id=user_id,
    )


@router.get("/sessions/stats", response_model=CurationSessionStatsResponse)
async def get_review_session_stats(
    request: CurationSessionStatsRequest = Depends(_build_stats_request),
    user: dict = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> CurationSessionStatsResponse:
    set_global_user_from_cognito(db, user)
    return get_session_stats(
        db,
        request,
        current_user_id=_current_user_id(user),
    )


@router.get("/flow-runs", response_model=CurationFlowRunListResponse)
async def get_review_flow_runs(
    request: CurationFlowRunListRequest = Depends(_build_flow_run_list_request),
    user: dict = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> CurationFlowRunListResponse:
    set_global_user_from_cognito(db, user)
    return list_flow_runs(db, request)


@router.get("/flow-runs/{run_id}/sessions", response_model=CurationFlowRunSessionsResponse)
async def get_review_flow_run_sessions(
    request: CurationFlowRunSessionsRequest = Depends(_build_flow_run_sessions_request),
    user: dict = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> CurationFlowRunSessionsResponse:
    set_global_user_from_cognito(db, user)
    return list_flow_run_sessions(db, request)


@router.get("/sessions/next", response_model=CurationNextSessionResponse)
async def get_next_review_session(
    request: CurationNextSessionRequest = Depends(_build_next_request),
    user: dict = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> CurationNextSessionResponse:
    set_global_user_from_cognito(db, user)
    return get_next_session(db, request)


@router.get("/sessions/{session_id}", response_model=None)
async def get_review_session(
    session_id: UUID,
    include_workspace: bool = Query(default=False),
    user: dict = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> CurationReviewSession | CurationWorkspaceResponse:
    set_global_user_from_cognito(db, user)
    if include_workspace:
        return get_session_workspace(db, session_id)
    return get_session_detail(db, session_id)


@router.patch("/sessions/{session_id}", response_model=CurationSessionUpdateResponse)
async def patch_review_session(
    session_id: UUID,
    request: CurationSessionUpdateRequest,
    user: dict = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> CurationSessionUpdateResponse:
    set_global_user_from_cognito(db, user)
    return update_session(db, session_id, request, user)


@router.post("/evidence/recompute", response_model=CurationEvidenceRecomputeResponse)
async def post_evidence_recompute(
    request: CurationEvidenceRecomputeRequest,
    user: dict = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> CurationEvidenceRecomputeResponse:
    set_global_user_from_cognito(db, user)
    user_id = _require_current_user_id(user)
    return recompute_evidence(
        request,
        current_user_id=user_id,
        actor_claims=user,
        db=db,
    )


@router.post("/evidence/manual", response_model=CurationManualEvidenceCreateResponse)
async def post_manual_evidence(
    request: CurationManualEvidenceCreateRequest,
    user: dict = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> CurationManualEvidenceCreateResponse:
    set_global_user_from_cognito(db, user)
    _require_current_user_id(user)
    return create_manual_evidence(
        request,
        actor_claims=user,
        db=db,
    )


@router.post("/evidence/resolve", response_model=CurationEvidenceResolveResponse)
async def post_evidence_resolve(
    request: CurationEvidenceResolveRequest,
    user: dict = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> CurationEvidenceResolveResponse:
    set_global_user_from_cognito(db, user)
    user_id = _require_current_user_id(user)
    return resolve_evidence(
        request,
        current_user_id=user_id,
        db=db,
    )


@router.post(
    "/documents/{document_id}/bootstrap",
    response_model=CurationDocumentBootstrapResponse,
)
async def post_document_bootstrap(
    document_id: str,
    request: CurationDocumentBootstrapRequest,
    user: dict = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> CurationDocumentBootstrapResponse:
    set_global_user_from_cognito(db, user)
    user_id = _require_current_user_id(user)
    return await bootstrap_document_session(
        document_id,
        request,
        current_user_id=user_id,
        db=db,
    )


@router.get(
    "/documents/{document_id}/bootstrap-availability",
    response_model=CurationDocumentBootstrapAvailabilityResponse,
)
async def get_document_bootstrap_status(
    document_id: str,
    request: CurationDocumentBootstrapRequest = Depends(_bootstrap_request_from_query),
    user: dict = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> CurationDocumentBootstrapAvailabilityResponse:
    set_global_user_from_cognito(db, user)
    user_id = _require_current_user_id(user)
    return get_document_bootstrap_availability(
        document_id,
        request,
        current_user_id=user_id,
        db=db,
    )


@router.get("/prep/preview", response_model=CurationPrepChatPreviewResponse)
async def get_chat_prep_preview(
    session_id: str = Query(..., min_length=1),
    user: dict = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> CurationPrepChatPreviewResponse:
    set_global_user_from_cognito(db, user)
    user_id = _require_current_user_id(user)

    try:
        return build_chat_curation_prep_preview(
            session_id=session_id,
            user_id=user_id,
            db=db,
        )
    except ValueError as exc:
        raise_sanitized_http_exception(
            logger,
            status_code=400,
            detail="Invalid curation prep preview request",
            log_message="Curation prep preview request rejected",
            exc=exc,
            level=logging.WARNING,
        )


@router.post("/prep", response_model=CurationPrepChatRunResponse)
async def trigger_chat_prep(
    request: CurationPrepChatRunRequest,
    user: dict = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> CurationPrepChatRunResponse:
    set_global_user_from_cognito(db, user)
    user_id = _require_current_user_id(user)

    try:
        return await prepare_chat_curation_sessions(
            request,
            current_user_id=user_id,
            db=db,
        )
    except ValueError as exc:
        raise_sanitized_http_exception(
            logger,
            status_code=400,
            detail="Invalid curation prep request",
            log_message="Curation prep request rejected",
            exc=exc,
            level=logging.WARNING,
        )


@router.post(
    "/sessions/{session_id}/candidates",
    response_model=CurationManualCandidateCreateResponse,
)
async def post_manual_candidate(
    session_id: UUID,
    request: CurationManualCandidateCreateRequest,
    user: dict = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> CurationManualCandidateCreateResponse:
    set_global_user_from_cognito(db, user)
    _require_current_user_id(user)
    return create_manual_candidate(
        db,
        session_id,
        request,
        actor_claims=user,
    )


@router.patch(
    "/sessions/{session_id}/candidates/{candidate_id}/draft",
    response_model=CurationCandidateDraftUpdateResponse,
)
async def patch_review_candidate_draft(
    session_id: UUID,
    candidate_id: UUID,
    request: CurationCandidateDraftUpdateRequest,
    user: dict = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> CurationCandidateDraftUpdateResponse:
    set_global_user_from_cognito(db, user)
    return update_candidate_draft(
        db,
        session_id,
        candidate_id,
        request,
        user,
    )


@router.delete(
    "/sessions/{session_id}/candidates/{candidate_id}",
    response_model=CurationCandidateDeleteResponse,
)
async def delete_review_candidate(
    session_id: UUID,
    candidate_id: UUID,
    user: dict = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> CurationCandidateDeleteResponse:
    set_global_user_from_cognito(db, user)
    _require_current_user_id(user)
    return delete_candidate(
        db,
        session_id,
        candidate_id,
        actor_claims=user,
    )


@router.post(
    "/candidates/{candidate_id}/decision",
    response_model=CurationCandidateDecisionResponse,
)
async def post_candidate_decision(
    candidate_id: UUID,
    request: CurationCandidateDecisionRequest,
    user: dict = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> CurationCandidateDecisionResponse:
    set_global_user_from_cognito(db, user)
    _require_current_user_id(user)
    return decide_candidate(
        db,
        candidate_id,
        request,
        user,
    )


@router.post(
    "/candidates/{candidate_id}/validate",
    response_model=CurationCandidateValidationResponse,
)
async def post_candidate_validation(
    candidate_id: UUID,
    request: CurationCandidateValidationRequest,
    user: dict = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> CurationCandidateValidationResponse:
    set_global_user_from_cognito(db, user)
    return validate_candidate(db, candidate_id, request)


@router.post(
    "/sessions/{session_id}/validate-all",
    response_model=CurationSessionValidationResponse,
)
async def post_session_validation(
    session_id: UUID,
    request: CurationSessionValidationRequest,
    user: dict = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> CurationSessionValidationResponse:
    set_global_user_from_cognito(db, user)
    return validate_session(db, session_id, request)


@router.post(
    "/sessions/{session_id}/submission-preview",
    response_model=CurationSubmissionPreviewResponse,
)
async def post_submission_preview(
    session_id: UUID,
    request: CurationSubmissionPreviewRequest,
    user: dict = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> CurationSubmissionPreviewResponse:
    set_global_user_from_cognito(db, user)
    return submission_preview(db, session_id, request)


@router.post(
    "/sessions/{session_id}/submit",
    response_model=CurationSubmissionExecuteResponse,
)
async def post_submission_execute(
    session_id: UUID,
    request: CurationSubmissionExecuteRequest,
    user: dict = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> CurationSubmissionExecuteResponse:
    set_global_user_from_cognito(db, user)
    _require_current_user_id(user)
    return execute_submission(
        db,
        session_id,
        request,
        actor_claims=user,
    )


@router.post(
    "/sessions/{session_id}/submissions/{submission_id}/retry",
    response_model=CurationSubmissionRetryResponse,
)
async def post_submission_retry(
    session_id: UUID,
    submission_id: UUID,
    request: CurationSubmissionRetryRequest,
    user: dict = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> CurationSubmissionRetryResponse:
    set_global_user_from_cognito(db, user)
    _require_current_user_id(user)
    return retry_submission(
        db,
        session_id,
        submission_id,
        request,
        actor_claims=user,
    )


@router.get(
    "/sessions/{session_id}/submissions/{submission_id}",
    response_model=CurationSubmissionHistoryResponse,
)
async def get_submission_history(
    session_id: UUID,
    submission_id: UUID,
    user: dict = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> CurationSubmissionHistoryResponse:
    set_global_user_from_cognito(db, user)
    return get_submission(db, session_id, submission_id)


__all__ = ["router"]
