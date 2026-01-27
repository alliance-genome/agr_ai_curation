"""Flow CRUD API endpoints for managing curation flows.

Section 3 of the Curation Flows implementation.
Provides endpoints to create, read, update, delete, and list user curation flows.

All endpoints require Okta JWT authentication via Security(get_auth_dependency()).
Flow ownership is enforced - users can only access their own flows.
"""

import logging
from typing import Any, Dict
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from .auth import get_auth_dependency
from ..models.api_schemas import OperationResult
from ..models.sql import get_db, CurationFlow
from ..schemas.flows import (
    CreateFlowRequest,
    UpdateFlowRequest,
    FlowResponse,
    FlowListResponse,
    FlowSummaryResponse,
)
from ..services.user_service import set_global_user_from_cognito


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/flows")


def verify_flow_ownership(
    db: Session,
    flow_id: UUID,
    okta_user: Dict[str, Any]
) -> CurationFlow:
    """Verify flow ownership and return flow if authorized.

    Args:
        db: Database session
        flow_id: Flow UUID to check
        okta_user: Authenticated Okta user from JWT

    Returns:
        CurationFlow if user owns it

    Raises:
        HTTPException: 404 if flow not found (including soft-deleted), 403 if not owned by user
    """
    # Get database user (creates if first login)
    db_user = set_global_user_from_cognito(db, okta_user)

    # Query flow - only active flows (is_active=True)
    flow = db.query(CurationFlow).filter(
        CurationFlow.id == flow_id,
        CurationFlow.is_active == True  # noqa: E712 - SQLAlchemy requires == for SQL
    ).first()

    if not flow:
        raise HTTPException(
            status_code=404,
            detail=f"Flow with ID {flow_id} not found"
        )

    # Verify ownership - return 403 for cross-user access
    if flow.user_id != db_user.id:
        raise HTTPException(
            status_code=403,
            detail="You do not have permission to access this flow"
        )

    return flow


def _flow_to_summary_response(flow: CurationFlow) -> FlowSummaryResponse:
    """Convert CurationFlow to FlowSummaryResponse with step_count.

    The step_count is computed from the number of nodes in flow_definition.
    """
    # Count nodes in flow_definition JSONB
    nodes = flow.flow_definition.get("nodes", []) if flow.flow_definition else []
    step_count = len(nodes)

    return FlowSummaryResponse(
        id=flow.id,
        user_id=flow.user_id,
        name=flow.name,
        description=flow.description,
        step_count=step_count,
        execution_count=flow.execution_count,
        last_executed_at=flow.last_executed_at,
        created_at=flow.created_at,
        updated_at=flow.updated_at,
    )


@router.get("", response_model=FlowListResponse)
async def list_flows(
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page (max 100)"),
    user: Dict[str, Any] = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> FlowListResponse:
    """List user's flows with pagination.

    Returns only active flows owned by the authenticated user,
    ordered by updated_at descending (most recently modified first).
    """
    # Get database user
    db_user = set_global_user_from_cognito(db, user)

    # Count total flows for user
    total_query = select(func.count(CurationFlow.id)).where(
        CurationFlow.user_id == db_user.id,
        CurationFlow.is_active == True  # noqa: E712
    )
    total = db.scalar(total_query) or 0

    # Paginate query
    offset = (page - 1) * page_size
    flows_query = (
        select(CurationFlow)
        .where(
            CurationFlow.user_id == db_user.id,
            CurationFlow.is_active == True  # noqa: E712
        )
        .order_by(CurationFlow.updated_at.desc())
        .offset(offset)
        .limit(page_size)
    )
    flows = db.scalars(flows_query).all()

    # Convert to summary responses (excludes full flow_definition)
    flow_summaries = [_flow_to_summary_response(flow) for flow in flows]

    logger.info(f"Listed {len(flow_summaries)} flows for user {db_user.id} (page {page})")

    return FlowListResponse(
        flows=flow_summaries,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{flow_id}", response_model=FlowResponse)
async def get_flow(
    flow_id: UUID,
    user: Dict[str, Any] = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> FlowResponse:
    """Get a single flow by ID.

    Returns the full flow including flow_definition.
    """
    flow = verify_flow_ownership(db, flow_id, user)

    logger.info(f"Retrieved flow {flow_id} for user {flow.user_id}")

    return FlowResponse.model_validate(flow)


@router.post("", response_model=FlowResponse, status_code=201)
async def create_flow(
    request: CreateFlowRequest,
    user: Dict[str, Any] = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> FlowResponse:
    """Create a new flow.

    The flow name must be unique for the user (among active flows).
    """
    # Get database user
    db_user = set_global_user_from_cognito(db, user)

    # Create flow model
    flow = CurationFlow(
        user_id=db_user.id,
        name=request.name,
        description=request.description,
        flow_definition=request.flow_definition.model_dump(),
    )

    try:
        db.add(flow)
        db.commit()
        db.refresh(flow)
    except IntegrityError as e:
        db.rollback()
        # Check if it's a unique constraint violation on name
        if "uq_user_flow_name_active" in str(e.orig).lower():
            raise HTTPException(
                status_code=409,
                detail="A flow with this name already exists"
            )
        # Wrap other integrity errors to avoid exposing database internals
        logger.error(f"Unexpected IntegrityError creating flow: {e}")
        raise HTTPException(
            status_code=500,
            detail="Database error while creating flow"
        )

    logger.info(f"Created flow {flow.id} '{flow.name}' for user {db_user.id}")

    return FlowResponse.model_validate(flow)


@router.put("/{flow_id}", response_model=FlowResponse)
async def update_flow(
    flow_id: UUID,
    request: UpdateFlowRequest,
    user: Dict[str, Any] = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> FlowResponse:
    """Update an existing flow (partial update).

    Only provided fields are updated. Flow name must remain unique for the user.
    """
    logger.info(f"[Flow Update] Starting update for flow {flow_id}")
    logger.debug(f"[Flow Update] Request payload: name={request.name is not None}, "
                 f"description={request.description is not None}, "
                 f"flow_definition={request.flow_definition is not None}")

    flow = verify_flow_ownership(db, flow_id, user)
    logger.debug(f"[Flow Update] Current flow state: name='{flow.name}', "
                 f"updated_at={flow.updated_at}")

    # Track what was updated for logging
    updates = []

    # Update name if provided
    if request.name is not None:
        logger.debug(f"[Flow Update] Changing name: '{flow.name}' -> '{request.name}'")
        flow.name = request.name
        updates.append("name")

    # Update description if provided (empty string clears it)
    if request.description is not None:
        flow.description = request.description if request.description else None
        updates.append("description")

    # Update flow_definition if provided
    if request.flow_definition is not None:
        # Log node count for visibility without dumping entire definition
        node_count = len(request.flow_definition.nodes) if request.flow_definition.nodes else 0
        edge_count = len(request.flow_definition.edges) if request.flow_definition.edges else 0
        logger.debug(f"[Flow Update] Updating flow_definition: {node_count} nodes, {edge_count} edges")
        flow.flow_definition = request.flow_definition.model_dump()
        # CRITICAL: SQLAlchemy doesn't detect changes to mutable JSONB fields
        # We must explicitly flag it as modified for the UPDATE to be emitted
        flag_modified(flow, "flow_definition")
        updates.append("flow_definition")

    # Only commit if something changed
    if updates:
        logger.info(f"[Flow Update] Committing changes to flow {flow_id}: {', '.join(updates)}")
        try:
            db.commit()
            logger.debug(f"[Flow Update] Commit completed, refreshing flow object")
            db.refresh(flow)
            logger.info(f"[Flow Update] Success - flow {flow_id} updated_at now: {flow.updated_at}")
        except IntegrityError as e:
            logger.error(f"[Flow Update] IntegrityError during commit: {e}")
            db.rollback()
            # Check if it's a unique constraint violation on name
            if "uq_user_flow_name_active" in str(e.orig).lower():
                raise HTTPException(
                    status_code=409,
                    detail="A flow with this name already exists"
                )
            # Wrap other integrity errors to avoid exposing database internals
            logger.error(f"Unexpected IntegrityError updating flow {flow_id}: {e}")
            raise HTTPException(
                status_code=500,
                detail="Database error while updating flow"
            )
    else:
        logger.info(f"[Flow Update] No changes detected for flow {flow_id}")

    return FlowResponse.model_validate(flow)


@router.delete("/{flow_id}", response_model=OperationResult)
async def delete_flow(
    flow_id: UUID,
    user: Dict[str, Any] = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> OperationResult:
    """Soft delete a flow by setting is_active=False.

    The flow is not removed from the database, just marked as inactive.
    Deleted flows no longer appear in list queries or can be accessed.
    """
    flow = verify_flow_ownership(db, flow_id, user)

    # Soft delete - set is_active to False
    flow.is_active = False
    db.commit()

    logger.info(f"Soft-deleted flow {flow_id} '{flow.name}'")

    return OperationResult(
        success=True,
        message=f"Flow '{flow.name}' has been deleted",
        operation="delete_flow",
    )
