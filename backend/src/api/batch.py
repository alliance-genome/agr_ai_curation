"""Batch processing API endpoints.

Enables curators to run saved flows on multiple documents sequentially.
Provides batch creation, status tracking, streaming progress, and cancellation.

All endpoints require Cognito JWT authentication via Security(get_auth_dependency()).
Batch ownership is enforced - users can only access their own batches.
"""

from datetime import datetime
import logging
from typing import Any, Dict
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from .auth import get_auth_dependency
from ..lib.http_errors import raise_sanitized_http_exception
from ..lib.batch.service import BatchService
from ..lib.batch.download import prepare_batch_zip
from ..lib.batch.summaries import BatchFilter, BatchQueryError, terminal_summaries, lookup_summaries
from ..lib.batch.validation import validate_flow_for_batch
from ..lib.batch.processor import process_batch_task
from ..lib.batch.streaming import BatchProgressResponse, verify_batch_owner
from ..models.sql import get_db, CurationFlow, PDFDocument
from ..lib.observability.background_tasks import add_observed_background_task
from ..lib.group_rules import get_groups_from_provider_groups
from ..lib.agent_studio.agent_service import inaccessible_flow_agent_keys
from ..lib.flows.execution_revisions import flow_execution_revision_findings, resolve_flow_execution_revisions
from ..schemas.flows import FlowDefinition
from ..schemas.batch import (
    BatchCreateRequest,
    BatchResponse,
    BatchListResponse,
    BatchValidationResponse,
    BatchTerminalResponse, BatchLookupRequest, BatchLookupResponse,
)
from ..services.user_service import principal_from_claims, provision_user_async


logger = logging.getLogger(__name__)

# Main batch router - handles /api/batches endpoints
router = APIRouter(prefix="/api/batches")

# Secondary router for flow validation - handles /api/flows/{id}/validate-batch
flow_validation_router = APIRouter(prefix="/api/flows")


@router.post("", response_model=BatchResponse, status_code=201)
async def create_batch(
    request: BatchCreateRequest,
    background_tasks: BackgroundTasks,
    user: Dict[str, Any] = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> BatchResponse:
    """Create and start a new batch processing job.

    Validates the flow exists and is batch-compatible, creates batch records,
    and begins sequential document processing in the background.

    Args:
        request: Batch creation request with flow_id and document_ids
        background_tasks: FastAPI background tasks for async processing
        user: Authenticated user from Cognito JWT
        db: Database session

    Returns:
        Created batch with initial pending status

    Raises:
        404: If flow not found or not owned by user
        400: If flow is not batch-compatible
    """
    db_user = await provision_user_async(principal_from_claims(user))

    # Verify flow exists and user owns it
    flow = db.query(CurationFlow).filter(
        CurationFlow.id == request.flow_id,
        CurationFlow.user_id == db_user.id,
        CurationFlow.is_active == True,  # noqa: E712
    ).first()

    if not flow:
        raise HTTPException(status_code=404, detail="Flow not found")

    active_group_ids = get_groups_from_provider_groups(
        user.get("cognito:groups", [])
    )
    contract_findings = flow_execution_revision_findings(
        db, flow.flow_definition or {}, user_id=db_user.id, active_group_ids=active_group_ids,
    )
    if contract_findings:
        raise HTTPException(status_code=422, detail={
            "code": "flow_execution_contract_invalid", "findings": contract_findings,
        })
    if inaccessible_flow_agent_keys(
        db,
        flow.flow_definition or {},
        user_id=db_user.id,
        active_group_ids=active_group_ids,
    ):
        raise HTTPException(status_code=403, detail="Flow contains unavailable agents")

    # Validate flow is batch-compatible
    revision_entries = (
        resolve_flow_execution_revisions(db, FlowDefinition.model_validate(flow.flow_definition),
            user_id=db_user.id, active_group_ids=active_group_ids).entries_by_node
        if any(str(node.get("data", {}).get("agent_id", "")).startswith("ca_")
               for node in flow.flow_definition.get("nodes", [])) else {}
    )
    validation = validate_flow_for_batch(flow.flow_definition, entries_by_node=revision_entries)
    if not validation.valid:
        raise HTTPException(
            status_code=400,
            detail=f"Flow not compatible with batch processing: {', '.join(validation.errors)}"
        )

    # Validate document IDs exist and belong to the user
    if not request.document_ids:
        raise HTTPException(
            status_code=400,
            detail="At least one document ID is required"
        )

    # Query for all requested documents owned by the user
    found_docs = db.query(PDFDocument).filter(
        PDFDocument.id.in_(request.document_ids),
        PDFDocument.user_id == db_user.id,
        PDFDocument.viewer_mode.is_distinct_from("benchmark_frozen"),
    ).all()

    found_ids = {doc.id for doc in found_docs}
    missing_ids = [str(doc_id) for doc_id in request.document_ids if doc_id not in found_ids]

    if missing_ids:
        raise HTTPException(
            status_code=400,
            detail=f"Documents not found or not accessible: {', '.join(missing_ids[:5])}"
            + (f" and {len(missing_ids) - 5} more" if len(missing_ids) > 5 else "")
        )

    # Create batch and batch_document records
    service = BatchService(db)
    batch = service.create_batch(
        user_id=db_user.id,
        flow_id=request.flow_id,
        document_ids=request.document_ids,
        active_group_ids=active_group_ids,
    )

    # Start background processing
    logger.info("Starting background task for batch_id=%s", batch.id)
    add_observed_background_task(
        background_tasks,
        process_batch_task,
        batch.id,
        task_name="batch.process_batch",
        tags={
            "component": "batch",
            "batch_id": batch.id,
        },
    )

    # Return created batch (flow name already known from validation)
    return service.batch_to_response(batch, flow_name=flow.name)


@router.get("/running-count")
async def get_running_batch_count(
    user: Dict[str, Any] = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> Dict[str, int]:
    """Get count of running/pending batches and documents for the current user.

    Lightweight endpoint for nav badge polling.
    Returns:
        - running_count: Number of batches in PENDING or RUNNING state
        - pending_documents: Number of documents remaining to process across all running batches
    """
    db_user = await provision_user_async(principal_from_claims(user))
    service = BatchService(db)
    return service.active_totals(db_user.id)


@router.get("", response_model=BatchListResponse)
async def list_batches(
    user: Dict[str, Any] = get_auth_dependency(),
    db: Session = Depends(get_db),
    limit: int | None = None,
    cursor: str | None = None,
    status: BatchFilter | None = None,
) -> BatchListResponse:
    """Bounded history; total counts all owned rows matching the status filter."""
    db_user = await provision_user_async(principal_from_claims(user))
    try:
        return BatchService(db).list_batches(db_user.id, limit, cursor, status)
    except BatchQueryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/terminals", response_model=BatchTerminalResponse)
async def list_terminal_batches(
    user: Dict[str, Any] = get_auth_dependency(),
    db: Session = Depends(get_db),
    limit: int | None = None,
    cursor: str | None = None,
    since: datetime | None = None,
) -> BatchTerminalResponse:
    """Bounded completion-time discovery, with explicit coverage reset."""
    db_user = await provision_user_async(principal_from_claims(user))
    try:
        return terminal_summaries(db, db_user.id, limit, cursor, since)
    except BatchQueryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/lookup", response_model=BatchLookupResponse)
async def lookup_batches(
    body: BatchLookupRequest,
    user: Dict[str, Any] = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> BatchLookupResponse:
    """Reconcile observed IDs; missing and unauthorized IDs are indistinguishable."""
    db_user = await provision_user_async(principal_from_claims(user))
    try:
        return lookup_summaries(db, db_user.id, body.ids)
    except BatchQueryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/{batch_id}", response_model=BatchResponse)
async def get_batch(
    batch_id: UUID,
    user: Dict[str, Any] = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> BatchResponse:
    """Get batch details by ID.

    Returns full batch information including per-document status.
    """
    db_user = await provision_user_async(principal_from_claims(user))
    service = BatchService(db)
    batch = service.get_batch(batch_id, db_user.id)

    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")

    return service.batch_to_response(batch)


@router.delete("/{batch_id}")
async def delete_batch(
    batch_id: UUID,
    user: Dict[str, Any] = get_auth_dependency(),
    db: Session = Depends(get_db),
):
    """Delete a batch and its associated documents.

    FUTURE FEATURE: This endpoint is a placeholder for batch deletion functionality.
    When implemented, it should:
    - Verify batch belongs to the requesting user
    - Only allow deletion of completed or cancelled batches
    - Delete associated batch_documents records
    - Delete result files from storage (file_outputs/)
    - Delete the batch record itself
    - Handle cleanup of any related Weaviate documents if applicable

    Args:
        batch_id: UUID of the batch to delete

    Returns:
        Success message on deletion

    Raises:
        HTTPException 501: This feature is not yet implemented
        HTTPException 404: Batch not found (when implemented)
        HTTPException 400: Batch cannot be deleted (in progress)
    """
    raise HTTPException(
        status_code=501,
        detail="Batch deletion not yet implemented. Batches cannot currently be removed."
    )


@router.get("/{batch_id}/download-zip")
async def download_batch_zip(
    batch_id: UUID,
    user: Dict[str, Any] = get_auth_dependency(),
) -> StreamingResponse:
    """Download authorized completed results without blocking the event loop."""
    db_user = await provision_user_async(principal_from_claims(user))
    return await prepare_batch_zip(batch_id, db_user.id, user.get("sub") or user.get("uid", "unknown"))


@router.get("/{batch_id}/stream")
async def stream_batch_progress(
    batch_id: UUID,
    user: Dict[str, Any] = get_auth_dependency(),
) -> StreamingResponse:
    """Stream authorized audit events and durable batch/document snapshots."""
    db_user = await provision_user_async(principal_from_claims(user))
    user_id = db_user.id
    if not await run_in_threadpool(verify_batch_owner, batch_id, user_id):
        raise HTTPException(status_code=404, detail="Batch not found")

    return BatchProgressResponse(batch_id, user_id)


@router.post("/{batch_id}/cancel", response_model=BatchResponse)
async def cancel_batch(
    batch_id: UUID,
    user: Dict[str, Any] = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> BatchResponse:
    """Cancel a running batch.

    Stops processing after the current document completes.
    Already-completed documents retain their results.

    Args:
        batch_id: UUID of the batch to cancel
        user: Authenticated user from Cognito JWT
        db: Database session

    Returns:
        Updated batch with CANCELLED status

    Raises:
        404: If batch not found or not owned by user
        400: If batch is not in a cancellable state
    """
    db_user = await provision_user_async(principal_from_claims(user))
    service = BatchService(db)

    try:
        batch = service.cancel_batch(batch_id, db_user.id)
    except ValueError as e:
        raise_sanitized_http_exception(
            logger,
            status_code=400,
            detail="Batch cannot be cancelled",
            log_message=f"Failed to cancel batch {batch_id}",
            exc=e,
            level=logging.WARNING,
        )

    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")

    logger.info("Batch cancelled via API: batch_id=%s, user_id=%s", batch_id, db_user.id)

    return service.batch_to_response(batch)


@flow_validation_router.get("/{flow_id}/validate-batch", response_model=BatchValidationResponse)
async def validate_flow_for_batch_endpoint(
    flow_id: UUID,
    user: Dict[str, Any] = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> BatchValidationResponse:
    """Validate whether a flow is compatible with batch processing.

    Checks that the flow:
    - Exists and is owned by the user
    - Contains only batch-compatible agents/tools
    - Has a valid structure for sequential execution

    Returns validation result with any incompatibility errors.
    """
    db_user = await provision_user_async(principal_from_claims(user))

    # Get flow from database
    flow = db.query(CurationFlow).filter(
        CurationFlow.id == flow_id,
        CurationFlow.user_id == db_user.id,
        CurationFlow.is_active == True,  # noqa: E712
    ).first()

    if not flow:
        raise HTTPException(status_code=404, detail="Flow not found")

    active_group_ids = get_groups_from_provider_groups(user.get("cognito:groups", []))
    contract_findings = flow_execution_revision_findings(
        db, flow.flow_definition or {}, user_id=db_user.id, active_group_ids=active_group_ids,
    )
    if contract_findings:
        raise HTTPException(status_code=422, detail={
            "code": "flow_execution_contract_invalid", "findings": contract_findings,
        })
    if inaccessible_flow_agent_keys(
        db, flow.flow_definition or {}, user_id=db_user.id,
        active_group_ids=active_group_ids,
    ):
        raise HTTPException(status_code=403, detail="Flow contains unavailable agents")

    revision_entries = (
        resolve_flow_execution_revisions(db, FlowDefinition.model_validate(flow.flow_definition),
            user_id=db_user.id, active_group_ids=active_group_ids).entries_by_node
        if any(str(node.get("data", {}).get("agent_id", "")).startswith("ca_")
               for node in flow.flow_definition.get("nodes", [])) else {}
    )
    return validate_flow_for_batch(flow.flow_definition, entries_by_node=revision_entries)
