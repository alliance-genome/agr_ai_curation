"""Inventory-facing curation workspace session endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from src.api.auth import get_auth_dependency
from src.lib.conversation_manager import SessionAccessError
from src.lib.curation_workspace.bootstrap_service import (
    bootstrap_document_session,
    create_manual_session,
)
from src.lib.curation_workspace.curation_prep_invocation import (
    build_chat_curation_prep_preview,
    run_chat_curation_prep,
)
from src.lib.curation_workspace.session_service import (
    get_next_session,
    get_session_detail,
    get_session_workspace,
    get_session_stats,
    list_sessions,
    update_session,
)
from src.models.sql.database import get_db
from src.schemas.curation_prep import (
    CurationPrepChatPreviewResponse,
    CurationPrepChatRunRequest,
    CurationPrepChatRunResponse,
)
from src.schemas.curation_workspace import (
    CurationDateRange,
    CurationDocumentBootstrapRequest,
    CurationDocumentBootstrapResponse,
    CurationNextSessionRequest,
    CurationNextSessionResponse,
    CurationQueueNavigationDirection,
    CurationReviewSession,
    CurationSessionFilters,
    CurationSessionCreateRequest,
    CurationSessionCreateResponse,
    CurationSessionListRequest,
    CurationSessionListResponse,
    CurationSessionSortField,
    CurationSessionStatsRequest,
    CurationSessionStatsResponse,
    CurationSessionStatus,
    CurationSessionUpdateRequest,
    CurationSessionUpdateResponse,
    CurationSortDirection,
    CurationWorkspaceResponse,
)
from src.services.user_service import set_global_user_from_cognito


router = APIRouter(prefix="/api/curation-workspace", tags=["Curation Workspace"])


def _date_range(from_at: datetime | None, to_at: datetime | None) -> CurationDateRange | None:
    if from_at is None and to_at is None:
        return None
    return CurationDateRange(from_at=from_at, to_at=to_at)


def _session_filters_from_query(
    statuses: Annotated[list[CurationSessionStatus] | None, Query(alias="status")] = None,
    adapter_keys: Annotated[list[str] | None, Query(alias="adapter_key")] = None,
    profile_keys: Annotated[list[str] | None, Query(alias="profile_key")] = None,
    domain_keys: Annotated[list[str] | None, Query(alias="domain_key")] = None,
    curator_ids: Annotated[list[str] | None, Query(alias="curator_id")] = None,
    tags: Annotated[list[str] | None, Query(alias="tag")] = None,
    flow_run_id: str | None = Query(default=None),
    document_id: str | None = Query(default=None),
    search: str | None = Query(default=None),
    prepared_from: datetime | None = Query(default=None, alias="prepared_from"),
    prepared_to: datetime | None = Query(default=None, alias="prepared_to"),
    last_worked_from: datetime | None = Query(default=None, alias="last_worked_from"),
    last_worked_to: datetime | None = Query(default=None, alias="last_worked_to"),
) -> CurationSessionFilters:
    return CurationSessionFilters(
        statuses=statuses or [],
        adapter_keys=adapter_keys or [],
        profile_keys=profile_keys or [],
        domain_keys=domain_keys or [],
        curator_ids=curator_ids or [],
        tags=tags or [],
        flow_run_id=flow_run_id,
        document_id=document_id,
        search=search,
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
    except SessionAccessError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/prep", response_model=CurationPrepChatRunResponse)
async def trigger_chat_prep(
    request: CurationPrepChatRunRequest,
    user: dict = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> CurationPrepChatRunResponse:
    set_global_user_from_cognito(db, user)
    user_id = _require_current_user_id(user)

    try:
        return await run_chat_curation_prep(
            request,
            user_id=user_id,
            db=db,
        )
    except SessionAccessError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


__all__ = ["router"]
