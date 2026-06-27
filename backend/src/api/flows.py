"""Flow CRUD API endpoints for managing curation flows.

Section 3 of the Curation Flows implementation.
Provides endpoints to create, read, update, delete, and list user curation flows.

All endpoints require AWS Cognito JWT authentication via Security(get_auth_dependency()).
Flow ownership is enforced - users can only access their own flows.
"""

import logging
from typing import Any, Dict
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from .auth import get_auth_dependency
from ..lib.http_errors import raise_sanitized_http_exception
from ..lib.flows.evidence_export import (
    FlowEvidenceExportFormat,
    FlowRunEvidenceExportDataError,
    FlowRunEvidenceExportNotFoundError,
    FlowRunEvidenceExportPermissionError,
    build_flow_evidence_export_artifact,
    resolve_authorized_flow_run_extraction_results,
)
from ..lib.flows.validation_attachments import (
    FlowValidationAttachmentError,
    apply_flow_validation_attachment_defaults,
)
from ..lib.agent_studio.catalog_service import AGENT_REGISTRY, get_agent_metadata
from ..lib.agent_studio.flow_agent_policy import (
    agent_allows_ordinary_flow_step,
    attachment_only_validator_reason,
)
from ..lib.openai_agents.config import get_flow_list_page_size_default
from ..models.api_schemas import OperationResult
from ..models.sql import get_db, CurationFlow
from ..schemas.flows import (
    CreateFlowRequest,
    DEFAULT_FLOW_EDGE_ROLE,
    FlowDefinition,
    FlowListResponse,
    FlowResponse,
    FlowSummaryResponse,
    FlowValidationWarning,
    UpdateFlowRequest,
    VALIDATION_ATTACHMENT_EDGE_ROLE,
)
from ..services.user_service import set_global_user_from_cognito


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/flows")
# Env-configurable via FLOW_LIST_PAGE_SIZE_DEFAULT (default 50); see config.py.
DEFAULT_FLOW_LIST_PAGE_SIZE = get_flow_list_page_size_default()


class _FlowDatabaseError(RuntimeError):
    """Sanitized flow database failure safe for logs and Sentry."""


def _sanitized_flow_db_error(exc: IntegrityError, *, operation: str) -> _FlowDatabaseError:
    try:
        raise _FlowDatabaseError(f"Flow {operation} failed ({type(exc.orig).__name__})") from None
    except _FlowDatabaseError as sanitized:
        sanitized.__context__ = None
        sanitized.__cause__ = None
        return sanitized


def _validated_flow_definition_payload(
    flow_definition: FlowDefinition,
    *,
    db_user_id: int | None = None,
    enforce_agent_step_policy: bool = False,
    enforce_agent_references: bool = False,
) -> dict[str, Any]:
    """Return flow definition JSON with metadata-backed validation defaults."""

    validated = _validated_flow_definition(
        flow_definition,
        db_user_id=db_user_id,
        enforce_agent_references=enforce_agent_references,
    )
    if enforce_agent_step_policy:
        _validate_flow_agent_step_policy(validated, db_user_id=db_user_id)
    return validated.model_dump()


def _validated_flow_definition(
    flow_definition: FlowDefinition,
    *,
    db_user_id: int | None = None,
    enforce_agent_references: bool = False,
) -> FlowDefinition:
    """Return a flow definition hydrated with metadata-backed validation defaults."""

    try:
        validated = apply_flow_validation_attachment_defaults(flow_definition)
    except FlowValidationAttachmentError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if enforce_agent_references:
        _validate_flow_agent_references(validated, db_user_id=db_user_id)
    return validated


def _flow_agent_policy_entry(
    agent_id: str,
    *,
    db_user_id: int | None,
) -> dict[str, Any] | None:
    """Return the metadata needed to enforce ordinary-flow-step policy."""

    registry_entry = AGENT_REGISTRY.get(agent_id)
    if isinstance(registry_entry, dict):
        return registry_entry

    metadata_kwargs: dict[str, Any] = {}
    if db_user_id is not None:
        metadata_kwargs["db_user_id"] = db_user_id

    try:
        metadata = get_agent_metadata(agent_id, **metadata_kwargs)
    except ValueError:
        return None

    return {
        "name": metadata.get("display_name", agent_id),
        "category": metadata.get("category") or "",
        "supervisor": metadata.get("supervisor") or {},
    }


def _validate_flow_agent_step_policy(
    flow_definition: FlowDefinition,
    *,
    db_user_id: int | None,
) -> None:
    """Reject attachment-only validators wired as ordinary flow steps."""

    validation_attachment_targets = {
        edge.target
        for edge in flow_definition.edges
        if edge.role == VALIDATION_ATTACHMENT_EDGE_ROLE
    }
    control_flow_edges_by_node: dict[str, list[str]] = {}
    for edge in flow_definition.edges:
        if edge.role != DEFAULT_FLOW_EDGE_ROLE:
            continue
        control_flow_edges_by_node.setdefault(edge.source, []).append(edge.id)
        control_flow_edges_by_node.setdefault(edge.target, []).append(edge.id)

    for node in flow_definition.nodes:
        agent_id = node.data.agent_id
        if agent_id == "task_input":
            continue

        entry = _flow_agent_policy_entry(agent_id, db_user_id=db_user_id)
        if entry is None or agent_allows_ordinary_flow_step(agent_id, entry):
            continue

        control_flow_edge_ids = control_flow_edges_by_node.get(node.id, [])
        if node.id in validation_attachment_targets and not control_flow_edge_ids:
            continue

        agent_name = str(entry.get("name") or node.data.agent_display_name or agent_id)
        reason = attachment_only_validator_reason(agent_name)
        if control_flow_edge_ids:
            reason = (
                f"{reason} Remove ordinary control-flow edge(s) connected to "
                f"node '{node.id}': {', '.join(control_flow_edge_ids)}."
            )
        else:
            reason = (
                f"{reason} Node '{node.id}' is not connected as a validation "
                "attachment target."
            )
        raise HTTPException(status_code=422, detail=reason)


def _validate_flow_agent_references(
    flow_definition: FlowDefinition,
    *,
    db_user_id: int | None,
) -> None:
    """Reject flows that reference agent_ids unavailable to the saving user."""

    missing_references = _missing_flow_agent_reference_messages(
        flow_definition,
        db_user_id=db_user_id,
    )
    if missing_references:
        raise HTTPException(
            status_code=422,
            detail=_missing_flow_agent_references_detail(missing_references),
        )


def _missing_flow_agent_reference_messages(
    flow_definition: FlowDefinition,
    *,
    db_user_id: int | None,
) -> list[str]:
    """Return messages for flow nodes that reference unavailable agents."""

    missing_references: list[str] = []
    for node in flow_definition.nodes:
        agent_id = str(node.data.agent_id or "").strip()
        if not agent_id or agent_id == "task_input":
            continue
        if _flow_agent_policy_entry(agent_id, db_user_id=db_user_id) is not None:
            continue
        agent_name = str(node.data.agent_display_name or agent_id)
        missing_references.append(
            f"node '{node.id}' ({agent_name}) references missing agent_id '{agent_id}'"
        )

    return missing_references


def _missing_flow_agent_references_detail(missing_references: list[str]) -> str:
    """Build the curator-facing unavailable-agent validation message."""

    return (
        "Flow references unavailable agent(s): "
        + "; ".join(missing_references)
        + ". Re-select an available agent before saving or running this flow."
    )


def _flow_to_response(flow: CurationFlow) -> FlowResponse:
    """Convert a stored flow to an API response with validation defaults hydrated."""

    flow_definition = _validated_flow_definition(
        FlowDefinition.model_validate(flow.flow_definition),
        db_user_id=flow.user_id,
    )
    missing_references = _missing_flow_agent_reference_messages(
        flow_definition,
        db_user_id=flow.user_id,
    )
    validation_warnings = (
        [
            FlowValidationWarning(
                type="CRITICAL",
                message=_missing_flow_agent_references_detail(missing_references),
            )
        ]
        if missing_references
        else []
    )
    return FlowResponse(
        id=flow.id,
        user_id=flow.user_id,
        name=flow.name,
        description=flow.description,
        flow_definition=flow_definition,
        execution_count=flow.execution_count,
        last_executed_at=flow.last_executed_at,
        created_at=flow.created_at,
        updated_at=flow.updated_at,
        validation_warnings=validation_warnings,
        has_critical_issues=bool(validation_warnings),
    )


def verify_flow_ownership(
    db: Session,
    flow_id: UUID,
    auth_user: Dict[str, Any]
) -> CurationFlow:
    """Verify flow ownership and return flow if authorized.

    Args:
        db: Database session
        flow_id: Flow UUID to check
        auth_user: Authenticated user from AWS Cognito JWT

    Returns:
        CurationFlow if user owns it

    Raises:
        HTTPException: 404 if flow not found (including soft-deleted), 403 if not owned by user
    """
    # Get database user (creates if first login)
    db_user = set_global_user_from_cognito(db, auth_user)

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


def _safe_attachment_filename(filename: str) -> str:
    """Sanitize attachment filenames to prevent header injection."""

    return (
        filename
        .replace('"', "'")
        .replace("\r", "")
        .replace("\n", "")
        .replace("\x00", "")
    )


@router.get("", response_model=FlowListResponse)
async def list_flows(
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(DEFAULT_FLOW_LIST_PAGE_SIZE, ge=1, le=100, description="Items per page (max 100)"),
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

    logger.info('Listed %s flows for user %s (page %s)', len(flow_summaries), db_user.id, page)

    return FlowListResponse(
        flows=flow_summaries,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/runs/{flow_run_id}/evidence/export")
async def export_flow_evidence(
    flow_run_id: str,
    export_format: FlowEvidenceExportFormat = Query(
        ...,
        alias="format",
        description="Evidence export format: csv, tsv, or json",
    ),
    user: Dict[str, Any] = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> Response:
    """Export persisted, deduplicated flow evidence for one flow run."""

    auth_user_id = str(user.get("sub") or user.get("uid") or "").strip()
    if not auth_user_id:
        raise HTTPException(status_code=401, detail="Missing authenticated user subject")

    try:
        extraction_results = resolve_authorized_flow_run_extraction_results(
            db=db,
            flow_run_id=flow_run_id,
            user_id=auth_user_id,
        )
        artifact = build_flow_evidence_export_artifact(
            flow_run_id=flow_run_id,
            extraction_results=extraction_results,
            export_format=export_format,
        )
    except FlowRunEvidenceExportNotFoundError as exc:
        raise_sanitized_http_exception(
            logger,
            status_code=404,
            detail="Flow run evidence not found",
            log_message=f"Flow evidence export requested for missing flow run {flow_run_id}",
            exc=exc,
            level=logging.WARNING,
        )
    except FlowRunEvidenceExportPermissionError as exc:
        raise_sanitized_http_exception(
            logger,
            status_code=403,
            detail="Not authorized to export flow run evidence",
            log_message=(
                f"Unauthorized flow evidence export attempt for flow run {flow_run_id} "
                f"by user {auth_user_id}"
            ),
            exc=exc,
            level=logging.WARNING,
        )
    except FlowRunEvidenceExportDataError as exc:
        raise_sanitized_http_exception(
            logger,
            status_code=500,
            detail="Failed to export flow run evidence",
            log_message=f"Failed to build flow evidence export for flow run {flow_run_id}",
            exc=exc,
        )

    safe_filename = _safe_attachment_filename(artifact.filename)

    logger.info(
        "Exported %s evidence records for flow run %s as %s",
        artifact.record_count,
        flow_run_id,
        export_format.value,
    )

    return Response(
        content=artifact.payload_text,
        media_type=artifact.content_type,
        headers={
            "Content-Disposition": f'attachment; filename="{safe_filename}"',
        },
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

    logger.info('Retrieved flow %s for user %s', flow_id, flow.user_id)

    return _flow_to_response(flow)


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
        flow_definition=_validated_flow_definition_payload(
            request.flow_definition,
            db_user_id=db_user.id,
            enforce_agent_references=True,
            enforce_agent_step_policy=True,
        ),
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
        raise_sanitized_http_exception(
            logger,
            status_code=500,
            detail="Database error while creating flow",
            log_message="Unexpected database integrity error creating flow",
            exc=_sanitized_flow_db_error(e, operation="create"),
        )

    logger.info("Created flow %s '%s' for user %s", flow.id, flow.name, db_user.id)

    return _flow_to_response(flow)


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
    logger.info('[Flow Update] Starting update for flow %s', flow_id)
    logger.debug(
        "[Flow Update] Request payload: name=%s, description=%s, flow_definition=%s",
        request.name is not None,
        request.description is not None,
        request.flow_definition is not None,
    )

    flow = verify_flow_ownership(db, flow_id, user)
    logger.debug(
        "[Flow Update] Current flow state: name='%s', updated_at=%s",
        flow.name,
        flow.updated_at,
    )

    # Track what was updated for logging
    updates = []

    # Update name if provided
    if request.name is not None:
        logger.debug("[Flow Update] Changing name: '%s' -> '%s'", flow.name, request.name)
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
        logger.debug('[Flow Update] Updating flow_definition: %s nodes, %s edges', node_count, edge_count)
        flow.flow_definition = _validated_flow_definition_payload(
            request.flow_definition,
            db_user_id=flow.user_id,
            enforce_agent_references=True,
            enforce_agent_step_policy=True,
        )
        # CRITICAL: SQLAlchemy doesn't detect changes to mutable JSONB fields
        # We must explicitly flag it as modified for the UPDATE to be emitted
        flag_modified(flow, "flow_definition")
        updates.append("flow_definition")

    # Only commit if something changed
    if updates:
        logger.info('[Flow Update] Committing changes to flow %s: %s', flow_id, ', '.join(updates))
        try:
            db.commit()
            logger.debug('[Flow Update] Commit completed, refreshing flow object')
            db.refresh(flow)
            logger.info('[Flow Update] Success - flow %s updated_at now: %s', flow_id, flow.updated_at)
        except IntegrityError as e:
            db.rollback()
            # Check if it's a unique constraint violation on name
            if "uq_user_flow_name_active" in str(e.orig).lower():
                raise HTTPException(
                    status_code=409,
                    detail="A flow with this name already exists"
                )
            # Wrap other integrity errors to avoid exposing database internals
            raise_sanitized_http_exception(
                logger,
                status_code=500,
                detail="Database error while updating flow",
                log_message=f"Unexpected database integrity error updating flow {flow_id}",
                exc=_sanitized_flow_db_error(e, operation="update"),
            )
    else:
        logger.info('[Flow Update] No changes detected for flow %s', flow_id)

    return _flow_to_response(flow)


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

    logger.info("Soft-deleted flow %s '%s'", flow_id, flow.name)

    return OperationResult(
        success=True,
        message=f"Flow '{flow.name}' has been deleted",
        operation="delete_flow",
    )
