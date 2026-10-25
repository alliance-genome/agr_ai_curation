"""Inventory-facing curation workspace session endpoints."""

from __future__ import annotations

from datetime import datetime
import logging
from typing import Annotated, Any, Callable, NoReturn, TypeVar
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from src.api.auth import get_auth_dependency
from src.lib.curation_workspace.bootstrap_service import (
    bootstrap_document_session,
    create_manual_session,
    get_document_bootstrap_availability,
    prepare_chat_curation_sessions,
)
from src.lib.curation_workspace.benchmark_snapshots import (
    CurationBenchmarkSnapshotError,
    create_benchmark_snapshot,
    handoff_benchmark_snapshot,
    list_benchmark_handoff_destinations,
    load_benchmark_snapshot_bytes,
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
from src.lib.group_rules import get_groups_from_provider_groups
from src.lib.domain_packs.materialization import (
    DomainEnvelopeMaterializationError,
    DomainEnvelopeRevisionUnavailableError,
    materialize_persisted_envelope_review_rows,
)
from src.lib.curation_workspace.session_service import (
    RejectedEnvelopeFieldPatchError,
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
    patch_envelope_field,
    retry_submission,
    submission_preview,
    update_candidate_draft,
    update_session,
    waive_validation_finding,
)
from src.models.sql.database import get_db
from src.schemas.curation_prep import (
    CurationPrepChatPreviewResponse,
    CurationPrepChatRunRequest,
    CurationPrepChatRunResponse,
)
from src.schemas.workspace_review import WorkspaceReviewRequest, WorkspaceReviewDecisionRequest
from src.schemas.curation_workspace import (
    CurationCandidateDecisionRequest,
    CurationCandidateDecisionResponse,
    CurationCandidateDeleteResponse,
    CurationCandidateDraftUpdateRequest,
    CurationCandidateDraftUpdateResponse,
    CurationBenchmarkErrorResponse,
    CurationBenchmarkDestinationListResponse,
    CurationBenchmarkHandoffRequest,
    CurationBenchmarkHandoffResponse,
    CurationBenchmarkSnapshotBundleV1,
    CurationBenchmarkSnapshotCreateRequest,
    CurationBenchmarkSnapshotCreateResponse,
    CurationDateRange,
    CurationDocumentBootstrapAvailabilityResponse,
    CurationDocumentBootstrapRequest,
    CurationDocumentBootstrapResponse,
    CurationEvidenceRecomputeRequest,
    CurationEvidenceRecomputeResponse,
    CurationEvidenceResolveRequest,
    CurationEvidenceResolveResponse,
    CurationEnvelopeFieldPatchRequest,
    CurationEnvelopeFieldPatchResponse,
    CurationFlowRunListRequest,
    CurationFlowRunListResponse,
    CurationFlowRunSessionsRequest,
    CurationFlowRunSessionsResponse,
    CurationInventoryScope,
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
    CurationSessionUpdateRequest,
    CurationSessionUpdateResponse,
    CurationSortDirection,
    CurationValidationFindingWaiveRequest,
    CurationValidationFindingWaiveResponse,
    CurationWorkspaceResponse,
    DomainEnvelopeReviewRowsResponse,
)
from src.services.user_service import principal_from_claims, provision_user_async
from src.lib.agent_studio.profile_conformance import (
    EnvelopeIntegrityError,
    ProfileConformanceError,
    ProfileIdentityError,
)


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/curation-workspace", tags=["Curation Workspace"])
_MutationResult = TypeVar("_MutationResult")


def _run_curation_mutation(
    db: Session,
    mutation: Callable[[], _MutationResult],
) -> _MutationResult:
    """Own the HTTP transaction around a shared-session mutation helper."""

    try:
        result = mutation()
        db.commit()
        return result
    except EnvelopeIntegrityError as exc:
        # Our canonical envelope is broken, not the curator's Output Structure.
        # Must be caught before the profile handlers so it cannot be reported
        # as a 422 the curator is expected to act on (KANBAN-1773).
        if db.in_transaction():
            db.rollback()
        logger.error(
            "Canonical envelope integrity failure during curation mutation: %s",
            exc.issues,
            exc_info=exc,
        )
        raise HTTPException(
            status_code=500,
            detail={
                "code": exc.code,
                "message": str(exc),
                "findings": exc.issues,
            },
        ) from exc
    except (ProfileConformanceError, ProfileIdentityError) as exc:
        if db.in_transaction():
            db.rollback()
        detail: dict[str, object] = {
            "code": "output_structure_conformance" if isinstance(exc, ProfileConformanceError) else "output_structure_identity",
            "message": str(exc),
        }
        if isinstance(exc, ProfileConformanceError):
            detail["findings"] = exc.issues
        raise HTTPException(status_code=422, detail=detail) from exc
    except RejectedEnvelopeFieldPatchError:
        # Rejected patches intentionally retain their checkpoint and audit row.
        try:
            db.commit()
        except Exception:
            if db.in_transaction():
                db.rollback()
            raise
        raise
    except Exception:
        if db.in_transaction():
            db.rollback()
        raise


def _date_range(from_at: datetime | None, to_at: datetime | None) -> CurationDateRange | None:
    if from_at is None and to_at is None:
        return None
    return CurationDateRange(from_at=from_at, to_at=to_at)


def _session_filters_from_query(
    inventory_scope: CurationInventoryScope = Query(
        default=CurationInventoryScope.MY_INVENTORY,
        alias="inventory_scope",
    ),
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
        inventory_scope=inventory_scope,
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


def _authenticated_active_groups(user: dict) -> list[str]:
    raw_groups = user.get("groups")
    if raw_groups is None:
        raw_groups = user.get("cognito:groups", [])
    if not isinstance(raw_groups, list):
        raw_groups = [str(raw_groups)] if raw_groups else []
    return get_groups_from_provider_groups(raw_groups)


def _require_current_user_id(user: dict) -> str:
    user_id = _current_user_id(user)
    if not user_id:
        raise HTTPException(status_code=401, detail="User identifier not found in token")
    return user_id


def _sanitized_benchmark_error(
    exc: CurationBenchmarkSnapshotError,
) -> CurationBenchmarkSnapshotError:
    try:
        raise CurationBenchmarkSnapshotError(
            exc.status_code,
            exc.error,
            exc.message,
        ) from None
    except CurationBenchmarkSnapshotError as sanitized:
        sanitized.__context__ = None
        sanitized.__cause__ = None
        return sanitized


def _raise_benchmark_error(exc: CurationBenchmarkSnapshotError) -> NoReturn:
    detail = {"error": exc.error, "message": exc.message}
    if exc.status_code >= 500:
        raise_sanitized_http_exception(
            logger,
            status_code=exc.status_code,
            detail=detail,
            log_message="Curation benchmark snapshot operation failed",
            exc=_sanitized_benchmark_error(exc),
        )
    raise HTTPException(status_code=exc.status_code, detail=detail) from None


class _BenchmarkSnapshotPersistenceError(RuntimeError):
    """Sanitized snapshot persistence failure safe for logs and Sentry."""


def _sanitized_snapshot_persistence_error(
    orig_type_name: str,
) -> _BenchmarkSnapshotPersistenceError:
    try:
        raise _BenchmarkSnapshotPersistenceError(
            f"Curation benchmark snapshot persistence failed ({orig_type_name})"
        ) from None
    except _BenchmarkSnapshotPersistenceError as sanitized:
        sanitized.__context__ = None
        sanitized.__cause__ = None
        return sanitized


_BENCHMARK_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    code: {"model": CurationBenchmarkErrorResponse}
    for code in (400, 403, 404, 409, 413, 500, 503)
}


@router.get(
    "/benchmark-destinations",
    response_model=CurationBenchmarkDestinationListResponse,
    responses=_BENCHMARK_ERROR_RESPONSES,
)
async def get_benchmark_destinations(
    user: dict = get_auth_dependency(),
) -> CurationBenchmarkDestinationListResponse:
    _require_current_user_id(user)
    try:
        return list_benchmark_handoff_destinations()
    except CurationBenchmarkSnapshotError as exc:
        _raise_benchmark_error(exc)


@router.post(
    "/sessions/{session_id}/envelopes/{envelope_id}/benchmark-snapshots",
    response_model=CurationBenchmarkSnapshotCreateResponse,
    responses=_BENCHMARK_ERROR_RESPONSES,
)
async def post_benchmark_snapshot(
    session_id: UUID,
    envelope_id: str,
    request: CurationBenchmarkSnapshotCreateRequest,
    user: dict = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> CurationBenchmarkSnapshotCreateResponse:
    await provision_user_async(principal_from_claims(user))
    owner_subject = _require_current_user_id(user)
    try:
        result = create_benchmark_snapshot(
            db,
            session_id=session_id,
            envelope_id=envelope_id,
            expected_revision=request.expected_revision,
            current_user_id=owner_subject,
        )
        db.commit()
        return result
    except CurationBenchmarkSnapshotError as exc:
        db.rollback()
        _raise_benchmark_error(exc)
    except Exception as exc:
        db.rollback()
        sanitized = _sanitized_snapshot_persistence_error(type(exc).__name__)
        raise_sanitized_http_exception(
            logger,
            status_code=500,
            detail={
                "error": "snapshot_persistence_failed",
                "message": "Curation snapshot could not be persisted",
            },
            log_message="Curation benchmark snapshot persistence failed",
            exc=sanitized,
        )


@router.get(
    "/benchmark-snapshots/{snapshot_id}/download",
    response_class=Response,
    responses={
        200: {"model": CurationBenchmarkSnapshotBundleV1},
        **_BENCHMARK_ERROR_RESPONSES,
    },
)
async def download_benchmark_snapshot(
    snapshot_id: UUID,
    user: dict = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> Response:
    await provision_user_async(principal_from_claims(user))
    try:
        content = load_benchmark_snapshot_bytes(
            db, snapshot_id=snapshot_id, current_user_id=_require_current_user_id(user)
        )
    except CurationBenchmarkSnapshotError as exc:
        _raise_benchmark_error(exc)
    return Response(
        content=content,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="curation-benchmark-{snapshot_id}.json"'},
    )


@router.post(
    "/benchmark-snapshots/{snapshot_id}/handoffs",
    response_model=CurationBenchmarkHandoffResponse,
    response_model_exclude_none=True,
    responses=_BENCHMARK_ERROR_RESPONSES,
)
async def post_benchmark_snapshot_handoff(
    snapshot_id: UUID,
    request: CurationBenchmarkHandoffRequest,
    user: dict = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> CurationBenchmarkHandoffResponse:
    await provision_user_async(principal_from_claims(user))
    try:
        return await handoff_benchmark_snapshot(
            db,
            snapshot_id=snapshot_id,
            destination_id=request.destination_id,
            current_user_id=_require_current_user_id(user),
            sender_issuer=user.get("iss"),
            sender_subject=user.get("sub"),
        )
    except CurationBenchmarkSnapshotError as exc:
        if db.in_transaction():
            db.rollback()
        _raise_benchmark_error(exc)


@router.post(
    "/benchmark-snapshots/{snapshot_id}/handoffs/retry",
    response_model=CurationBenchmarkHandoffResponse,
    response_model_exclude_none=True,
    responses=_BENCHMARK_ERROR_RESPONSES,
)
async def retry_benchmark_snapshot_handoff(
    snapshot_id: UUID,
    request: CurationBenchmarkHandoffRequest,
    user: dict = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> CurationBenchmarkHandoffResponse:
    """Explicit recovery of a saved delivery, using its original bytes and identity."""
    await provision_user_async(principal_from_claims(user))
    try:
        return await handoff_benchmark_snapshot(
            db, snapshot_id=snapshot_id, destination_id=request.destination_id,
            current_user_id=_require_current_user_id(user),
            sender_issuer=user.get("iss"), sender_subject=user.get("sub"),
            retry_delivery=True,
        )
    except CurationBenchmarkSnapshotError as exc:
        if db.in_transaction():
            db.rollback()
        _raise_benchmark_error(exc)


@router.get("/sessions", response_model=CurationSessionListResponse)
async def list_review_sessions(
    request: CurationSessionListRequest = Depends(_build_list_request),
    user: dict = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> CurationSessionListResponse:
    await provision_user_async(principal_from_claims(user))
    return list_sessions(db, request, current_user_id=_require_current_user_id(user))


@router.post("/sessions", response_model=CurationSessionCreateResponse)
async def post_review_session(
    request: CurationSessionCreateRequest,
    user: dict = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> CurationSessionCreateResponse:
    await provision_user_async(principal_from_claims(user))
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
    await provision_user_async(principal_from_claims(user))
    return list_saved_view_records(db, current_user_id=user_id)


@router.post("/views", response_model=CurationSavedViewCreateResponse)
async def post_saved_view(
    request: CurationSavedViewCreateRequest,
    user: dict = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> CurationSavedViewCreateResponse:
    user_id = _require_current_user_id(user)
    await provision_user_async(principal_from_claims(user))
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
    await provision_user_async(principal_from_claims(user))
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
    await provision_user_async(principal_from_claims(user))
    return get_session_stats(
        db,
        request,
        current_user_id=_require_current_user_id(user),
    )


@router.get("/flow-runs", response_model=CurationFlowRunListResponse)
async def get_review_flow_runs(
    request: CurationFlowRunListRequest = Depends(_build_flow_run_list_request),
    user: dict = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> CurationFlowRunListResponse:
    await provision_user_async(principal_from_claims(user))
    return list_flow_runs(db, request, current_user_id=_require_current_user_id(user))


@router.get("/flow-runs/{run_id}/sessions", response_model=CurationFlowRunSessionsResponse)
async def get_review_flow_run_sessions(
    request: CurationFlowRunSessionsRequest = Depends(_build_flow_run_sessions_request),
    user: dict = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> CurationFlowRunSessionsResponse:
    await provision_user_async(principal_from_claims(user))
    return list_flow_run_sessions(db, request, current_user_id=_require_current_user_id(user))


@router.get("/sessions/next", response_model=CurationNextSessionResponse)
async def get_next_review_session(
    request: CurationNextSessionRequest = Depends(_build_next_request),
    user: dict = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> CurationNextSessionResponse:
    await provision_user_async(principal_from_claims(user))
    return get_next_session(db, request, current_user_id=_require_current_user_id(user))


@router.get("/sessions/{session_id}", response_model=None)
async def get_review_session(
    session_id: UUID,
    include_workspace: bool = Query(default=False),
    user: dict = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> CurationReviewSession | CurationWorkspaceResponse:
    await provision_user_async(principal_from_claims(user))
    if include_workspace:
        return get_session_workspace(db, session_id)
    return get_session_detail(db, session_id)


@router.get(
    "/domain-envelopes/{envelope_id}/review-rows",
    response_model=DomainEnvelopeReviewRowsResponse,
)
async def get_domain_envelope_review_rows(
    envelope_id: str,
    revision: int | None = Query(default=None, ge=1),
    user: dict = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> DomainEnvelopeReviewRowsResponse:
    acting_user = await provision_user_async(principal_from_claims(user))
    try:
        return materialize_persisted_envelope_review_rows(
            db,
            envelope_id,
            revision=revision,
            active_group_ids=_authenticated_active_groups(user), user_id=acting_user.id,
        )
    except DomainEnvelopeRevisionUnavailableError as exc:
        raise_sanitized_http_exception(
            logger,
            status_code=409,
            detail="Requested domain envelope revision is not available",
            log_message="Domain envelope review-row revision request rejected",
            exc=exc,
            level=logging.WARNING,
        )
    except DomainEnvelopeMaterializationError as exc:
        raise_sanitized_http_exception(
            logger,
            status_code=404,
            detail="Domain envelope review rows were not found",
            log_message="Domain envelope review-row materialization failed",
            exc=exc,
            level=logging.WARNING,
        )


@router.patch("/sessions/{session_id}", response_model=CurationSessionUpdateResponse)
async def patch_review_session(
    session_id: UUID,
    request: CurationSessionUpdateRequest,
    user: dict = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> CurationSessionUpdateResponse:
    await provision_user_async(principal_from_claims(user))
    return _run_curation_mutation(
        db,
        lambda: update_session(db, session_id, request, user),
    )


@router.post("/evidence/recompute", response_model=CurationEvidenceRecomputeResponse)
async def post_evidence_recompute(
    request: CurationEvidenceRecomputeRequest,
    user: dict = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> CurationEvidenceRecomputeResponse:
    await provision_user_async(principal_from_claims(user))
    user_id = _require_current_user_id(user)
    return _run_curation_mutation(
        db,
        lambda: recompute_evidence(
            request,
            current_user_id=user_id,
            actor_claims=user,
            db=db,
        ),
    )


@router.post("/evidence/manual", response_model=CurationManualEvidenceCreateResponse)
async def post_manual_evidence(
    request: CurationManualEvidenceCreateRequest,
    user: dict = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> CurationManualEvidenceCreateResponse:
    await provision_user_async(principal_from_claims(user))
    _require_current_user_id(user)
    return _run_curation_mutation(
        db,
        lambda: create_manual_evidence(
            request,
            actor_claims=user,
            db=db,
        ),
    )


@router.post("/evidence/resolve", response_model=CurationEvidenceResolveResponse)
async def post_evidence_resolve(
    request: CurationEvidenceResolveRequest,
    user: dict = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> CurationEvidenceResolveResponse:
    await provision_user_async(principal_from_claims(user))
    user_id = _require_current_user_id(user)
    return _run_curation_mutation(
        db,
        lambda: resolve_evidence(
            request,
            current_user_id=user_id,
            db=db,
        ),
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
    await provision_user_async(principal_from_claims(user))
    user_id = _require_current_user_id(user)
    return await bootstrap_document_session(
        document_id,
        request,
        current_user_id=user_id,
        active_groups=tuple(_authenticated_active_groups(user)),
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
    await provision_user_async(principal_from_claims(user))
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
    await provision_user_async(principal_from_claims(user))
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
    await provision_user_async(principal_from_claims(user))
    user_id = _require_current_user_id(user)

    try:
        return await prepare_chat_curation_sessions(
            request,
            current_user_id=user_id,
            active_groups=tuple(_authenticated_active_groups(user)),
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
    await provision_user_async(principal_from_claims(user))
    _require_current_user_id(user)
    return _run_curation_mutation(
        db,
        lambda: create_manual_candidate(
            db,
            session_id,
            request,
            actor_claims=user,
        ),
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
    await provision_user_async(principal_from_claims(user))
    return _run_curation_mutation(
        db,
        lambda: update_candidate_draft(
            db,
            session_id,
            candidate_id,
            request,
            user,
        ),
    )


@router.patch(
    "/sessions/{session_id}/envelopes/{envelope_id}/field",
    response_model=CurationEnvelopeFieldPatchResponse,
)
async def patch_review_envelope_field(
    session_id: UUID,
    envelope_id: str,
    request: CurationEnvelopeFieldPatchRequest,
    user: dict = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> CurationEnvelopeFieldPatchResponse:
    await provision_user_async(principal_from_claims(user))
    if request.envelope_id != envelope_id:
        raise HTTPException(
            status_code=400,
            detail="Path envelope_id does not match request body envelope_id",
        )
    return _run_curation_mutation(
        db,
        lambda: patch_envelope_field(
            db,
            session_id,
            request,
            user,
        ),
    )


@router.post(
    "/sessions/{session_id}/envelopes/{envelope_id}/validation-findings/{finding_id}/waive",
    response_model=CurationValidationFindingWaiveResponse,
)
async def post_review_validation_finding_waiver(
    session_id: UUID,
    envelope_id: str,
    finding_id: str,
    request: CurationValidationFindingWaiveRequest,
    user: dict = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> CurationValidationFindingWaiveResponse:
    await provision_user_async(principal_from_claims(user))
    if request.envelope_id != envelope_id:
        raise HTTPException(
            status_code=400,
            detail="Path envelope_id does not match request body envelope_id",
        )
    if request.finding_id != finding_id:
        raise HTTPException(
            status_code=400,
            detail="Path finding_id does not match request body finding_id",
        )
    return _run_curation_mutation(
        db,
        lambda: waive_validation_finding(
            db,
            session_id,
            request,
            user,
        ),
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
    await provision_user_async(principal_from_claims(user))
    _require_current_user_id(user)
    return _run_curation_mutation(
        db,
        lambda: delete_candidate(
            db,
            session_id,
            candidate_id,
            actor_claims=user,
        ),
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
    await provision_user_async(principal_from_claims(user))
    _require_current_user_id(user)
    return _run_curation_mutation(
        db,
        lambda: decide_candidate(
            db,
            candidate_id,
            request,
            user,
        ),
    )






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
    await provision_user_async(principal_from_claims(user))
    return _run_curation_mutation(
        db,
        lambda: submission_preview(db, session_id, request, actor_claims=user),
    )


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
    await provision_user_async(principal_from_claims(user))
    _require_current_user_id(user)
    return _run_curation_mutation(
        db,
        lambda: execute_submission(
            db,
            session_id,
            request,
            actor_claims=user,
        ),
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
    await provision_user_async(principal_from_claims(user))
    _require_current_user_id(user)
    return _run_curation_mutation(
        db,
        lambda: retry_submission(
            db,
            session_id,
            submission_id,
            request,
            actor_claims=user,
        ),
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
    await provision_user_async(principal_from_claims(user))
    return get_submission(db, session_id, submission_id)


__all__ = ["router"]


@router.get("/candidates/{candidate_id}/validation-history")
async def get_candidate_validation_history(
    candidate_id: UUID,
    after: Annotated[int, Query(ge=0)] = 0,
    user: dict = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> dict:
    from src.lib.curation_workspace.review_history_service import history_page
    from src.lib.validation_history.store import HistoryAccessError
    await provision_user_async(principal_from_claims(user))
    try:
        return history_page(db, candidate_id, owner_id=_require_current_user_id(user), after=after)
    except HistoryAccessError as exc:
        raise HTTPException(403, "Validation history unavailable") from exc


@router.get("/candidates/{candidate_id}/validation-history/{fact_id}")
async def get_candidate_validation_history_detail(
    candidate_id: UUID,
    fact_id: str,
    sha256: Annotated[str, Query(pattern=r"^[0-9a-f]{64}$")],
    offset: Annotated[int, Query(ge=0)] = 0,
    user: dict = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> dict:
    from src.lib.curation_workspace.review_history_service import history_detail
    from src.lib.validation_history.store import HistoryAccessError, HistoryConflict
    await provision_user_async(principal_from_claims(user))
    try:
        return history_detail(db, candidate_id, owner_id=_require_current_user_id(user),
                              fact_id=fact_id, sha256=sha256, offset=offset)
    except HistoryAccessError as exc:
        raise HTTPException(403, "Validation history unavailable") from exc
    except (HistoryConflict, ValueError) as exc:
        raise HTTPException(409, "Historical reference is unavailable; refresh the history") from exc




def _workspace_review_error(error):
    from src.lib.validation_history.store import HistoryAccessError, HistoryConflict
    if isinstance(error, HistoryAccessError):
        raise HTTPException(403, 'Workspace review unavailable') from error
    if isinstance(error, HistoryConflict):
        raise HTTPException(409, str(error)) from error
    if isinstance(error, ValueError):
        raise HTTPException(422, str(error)) from error
    raise error


@router.post('/sessions/{session_id}/review-operations', status_code=202)
async def post_workspace_review_operation(
    session_id: UUID, request: WorkspaceReviewRequest,
    user: dict = get_auth_dependency(), db: Session = Depends(get_db),
) -> dict:
    from src.lib.curation_workspace import review_operations
    await provision_user_async(principal_from_claims(user))
    try:
        return _run_curation_mutation(db, lambda: review_operations.submit(db,
            owner_id=_require_current_user_id(user), session_id=str(session_id),
            request_id=str(request.request_id), candidate_ids=[str(value) for value in request.candidate_ids],
            guidance=request.guidance, groups=_authenticated_active_groups(user)))
    except ValueError as error:
        _workspace_review_error(error)


@router.get('/sessions/{session_id}/review-operation')
async def get_session_review_operation(
    session_id: UUID, user: dict = get_auth_dependency(), db: Session = Depends(get_db),
) -> dict | None:
    from src.lib.curation_workspace import review_operations
    try:
        return review_operations.latest_for_session(db, str(session_id), owner_id=_require_current_user_id(user))
    except ValueError as error:
        _workspace_review_error(error)


@router.get('/candidates/{candidate_id}/review-operation')
async def get_candidate_review_operation(
    candidate_id: UUID, user: dict = get_auth_dependency(), db: Session = Depends(get_db),
) -> dict | None:
    from src.lib.curation_workspace import review_operations
    await provision_user_async(principal_from_claims(user))
    try:
        return review_operations.latest_for_candidate(db, str(candidate_id), owner_id=_require_current_user_id(user))
    except ValueError as error:
        _workspace_review_error(error)


@router.get('/review-operations/{operation_id}')
async def get_workspace_review_operation(
    operation_id: UUID, user: dict = get_auth_dependency(), db: Session = Depends(get_db),
) -> dict:
    from src.lib.curation_workspace import review_operations
    await provision_user_async(principal_from_claims(user))
    try:
        return review_operations.response(review_operations.get(db, str(operation_id), owner_id=_require_current_user_id(user)))
    except ValueError as error:
        _workspace_review_error(error)


@router.post('/review-operations/{operation_id}/cancel')
async def cancel_workspace_review_operation(operation_id: UUID, user: dict = get_auth_dependency()) -> dict:
    from src.lib.curation_workspace import review_operations
    import asyncio
    await provision_user_async(principal_from_claims(user))
    try:
        return await asyncio.to_thread(review_operations.cancel, str(operation_id), owner_id=_require_current_user_id(user))
    except ValueError as error:
        _workspace_review_error(error)


@router.post('/review-operations/{operation_id}/resume')
async def resume_workspace_review_operation(
    operation_id: UUID, user: dict = get_auth_dependency(), db: Session = Depends(get_db),
) -> dict:
    from src.lib.curation_workspace import review_operations
    await provision_user_async(principal_from_claims(user))
    try:
        return _run_curation_mutation(db, lambda: review_operations.resume(db, str(operation_id),
            owner_id=_require_current_user_id(user), groups=_authenticated_active_groups(user)))
    except ValueError as error:
        _workspace_review_error(error)


@router.post('/candidates/{candidate_id}/validation-decisions')
async def post_workspace_validation_decision(
    candidate_id: UUID, request: WorkspaceReviewDecisionRequest,
    user: dict = get_auth_dependency(),
) -> dict:
    import asyncio
    from src.lib.curation_workspace.review_history_service import decide
    await provision_user_async(principal_from_claims(user))
    try:
        return await asyncio.to_thread(decide, candidate_id,
            owner_id=_require_current_user_id(user), request=request,
            groups=_authenticated_active_groups(user))
    except ValueError as error:
        _workspace_review_error(error)
