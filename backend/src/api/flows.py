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
from ..lib.flows.persisted_flow_migrations import (
    PersistedFlowMigrationError,
    migrate_persisted_flow_definition,
)
from ..lib.agent_studio.catalog_service import (
    AGENT_REGISTRY,
    get_active_visible_agent_metadata,
)
from ..lib.group_rules import get_groups_from_provider_groups
from ..lib.agent_studio.authoring_validation import (
    AuthoringValidationContext,
    report_authoring_validation_engine_failure,
    validate_flow_authoring_draft,
)
from ..lib.config.schema_discovery import resolve_output_schema
from ..lib.openai_agents.config import get_flow_list_page_size_default
from ..models.api_schemas import OperationResult
from ..models.sql import get_db, CurationFlow
from ..schemas.flows import (
    CreateFlowRequest,
    FlowDefinition,
    FlowListResponse,
    FlowResponse,
    FlowSummaryResponse,
    FlowValidationAttachmentGroup,
    FlowValidationAttachmentSelection,
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


def _sanitized_flow_db_error(orig_type_name: str, *, operation: str) -> _FlowDatabaseError:
    try:
        raise _FlowDatabaseError(f"Flow {operation} failed ({orig_type_name})") from None
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
    active_group_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Return JSON accepted by the canonical exact-draft save validator."""

    context = AuthoringValidationContext.from_values(
        db_user_id=db_user_id,
        active_group_ids=active_group_ids,
    )

    def _apply_defaults(candidate: FlowDefinition) -> FlowDefinition:
        agent_registry, _ = _validation_attachment_agent_registry(
            candidate,
            db_user_id=db_user_id,
            active_group_ids=active_group_ids,
        )
        if agent_registry is None:
            return apply_flow_validation_attachment_defaults(candidate)
        return apply_flow_validation_attachment_defaults(
            candidate,
            agent_registry=agent_registry,
        )

    try:
        result = validate_flow_authoring_draft(
            flow_definition,
            context=context,
            resolve_agent=lambda agent_id, auth: _flow_agent_policy_entry(
                agent_id,
                db_user_id=auth.db_user_id,
                active_group_ids=list(auth.active_group_ids),
            ),
            apply_attachment_defaults=_apply_defaults,
            phase="save",
            enforce_agent_references=enforce_agent_references,
            enforce_agent_step_policy=enforce_agent_step_policy,
        )
    except Exception:
        report_authoring_validation_engine_failure(
            artifact_kind="flow",
            phase="save",
        )
        raise HTTPException(
            status_code=500,
            detail="Flow validation is temporarily unavailable",
        ) from None
    if not result.valid:
        raise HTTPException(status_code=422, detail=result.to_dict())
    validated_candidate = result.candidate
    if not isinstance(validated_candidate, FlowDefinition):
        report_authoring_validation_engine_failure(
            artifact_kind="flow",
            phase="save",
        )
        raise HTTPException(
            status_code=500,
            detail="Flow validation is temporarily unavailable",
        )
    return validated_candidate.model_dump()


def _validated_flow_definition(
    flow_definition: FlowDefinition,
    *,
    db_user_id: int | None = None,
    enforce_agent_references: bool = False,
    active_group_ids: list[str] | None = None,
    tolerate_unresolvable_custom_agent_attachments: bool = False,
) -> FlowDefinition:
    """Return a flow definition hydrated with metadata-backed validation defaults."""

    agent_registry, unresolvable_custom_agent_ids = _validation_attachment_agent_registry(
        flow_definition,
        db_user_id=db_user_id,
        active_group_ids=active_group_ids,
    )
    validation_input = flow_definition
    preserved_unresolvable_data: dict[
        str,
        tuple[
            list[FlowValidationAttachmentSelection],
            list[FlowValidationAttachmentGroup],
        ],
    ] = {}
    preserved_edges = None
    if tolerate_unresolvable_custom_agent_attachments and unresolvable_custom_agent_ids:
        validation_input = flow_definition.model_copy(deep=True)
        unresolvable_node_ids: set[str] = set()
        for node in validation_input.nodes:
            if node.data.agent_id not in unresolvable_custom_agent_ids:
                continue
            unresolvable_node_ids.add(node.id)
            preserved_unresolvable_data[node.id] = (
                node.data.validation_attachments,
                node.data.validation_groups,
            )
            node.data.validation_attachments = []
            node.data.validation_groups = []
        preserved_edges = validation_input.edges
        validation_input.edges = [
            edge
            for edge in validation_input.edges
            if not (
                edge.role == VALIDATION_ATTACHMENT_EDGE_ROLE
                and edge.source in unresolvable_node_ids
            )
        ]
    try:
        if agent_registry is None:
            validated = apply_flow_validation_attachment_defaults(validation_input)
        else:
            validated = apply_flow_validation_attachment_defaults(
                validation_input,
                agent_registry=agent_registry,
            )
    except FlowValidationAttachmentError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if enforce_agent_references:
        _validate_flow_agent_references(
            validated,
            db_user_id=db_user_id,
            active_group_ids=active_group_ids,
        )
    for node in validated.nodes:
        preserved = preserved_unresolvable_data.get(node.id)
        if preserved is None:
            continue
        node.data.validation_attachments, node.data.validation_groups = preserved
    if preserved_edges is not None:
        validated.edges = preserved_edges
    return validated


def _validation_attachment_agent_registry(
    flow_definition: FlowDefinition,
    *,
    db_user_id: int | None,
    active_group_ids: list[str] | None = None,
) -> tuple[dict[str, dict[str, Any]] | None, set[str]]:
    """Add visible custom extractors to the validation-attachment registry."""

    if db_user_id is None:
        return None, set()

    custom_agent_ids = sorted(
        {
            str(node.data.agent_id or "").strip()
            for node in flow_definition.nodes
            if str(node.data.agent_id or "").strip().startswith("ca_")
        }
    )
    custom_entries: dict[str, dict[str, Any]] = {}
    unresolvable_custom_agent_ids: set[str] = set()
    for agent_id in custom_agent_ids:
        try:
            metadata = get_active_visible_agent_metadata(
                agent_id,
                db_user_id=db_user_id,
                authenticated_groups=list(active_group_ids or []),
            )
        except ValueError:
            unresolvable_custom_agent_ids.add(agent_id)
            continue
        curation = metadata.get("curation") if isinstance(metadata, dict) else None
        if not isinstance(curation, dict) or not str(
            curation.get("domain_pack_id") or ""
        ).strip():
            unresolvable_custom_agent_ids.add(agent_id)
            continue
        custom_entries[agent_id] = metadata

    if not custom_entries:
        return None, unresolvable_custom_agent_ids
    return {**AGENT_REGISTRY, **custom_entries}, unresolvable_custom_agent_ids


def _flow_agent_policy_entry(
    agent_id: str,
    *,
    db_user_id: int | None,
    active_group_ids: list[str] | None = None,
) -> dict[str, Any] | None:
    """Return the metadata needed to enforce ordinary-flow-step policy."""

    metadata_kwargs: dict[str, Any] = {}
    if db_user_id is not None:
        metadata_kwargs["db_user_id"] = db_user_id
    metadata_kwargs["authenticated_groups"] = list(active_group_ids or [])

    try:
        metadata = get_active_visible_agent_metadata(agent_id, **metadata_kwargs)
    except ValueError:
        return None

    if not isinstance(metadata, dict):
        return None

    category = str(metadata.get("category") or "").strip().lower()
    subcategory = str(metadata.get("subcategory") or "").strip().lower()
    output_schema_key = str(
        metadata.get("output_schema_key") or metadata.get("output_schema") or ""
    ).strip()
    is_extraction = "extract" in category or "extract" in subcategory
    is_typed_validation = bool(
        "validation" in category
        and output_schema_key
        and resolve_output_schema(output_schema_key) is not None
    )

    return {
        "name": metadata.get("display_name", agent_id),
        "category": metadata.get("category") or "",
        "subcategory": metadata.get("subcategory") or "",
        "output_schema_key": output_schema_key or None,
        "is_active": metadata.get("is_active", True),
        "visible": metadata.get("visible", True),
        "visibility": metadata.get("visibility"),
        "produces_flow_artifacts": is_extraction or is_typed_validation,
        "supervisor": metadata.get("supervisor") or {},
        "curation": metadata.get("curation"),
    }


def _validate_flow_agent_references(
    flow_definition: FlowDefinition,
    *,
    db_user_id: int | None,
    active_group_ids: list[str] | None = None,
) -> None:
    """Reject flows that reference agent_ids unavailable to the saving user."""

    missing_references = _missing_flow_agent_reference_messages(
        flow_definition,
        db_user_id=db_user_id,
        active_group_ids=active_group_ids,
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
    active_group_ids: list[str] | None = None,
) -> list[str]:
    """Return messages for flow nodes that reference unavailable agents."""

    missing_references: list[str] = []
    for node in flow_definition.nodes:
        agent_id = str(node.data.agent_id or "").strip()
        if not agent_id or agent_id == "task_input":
            continue
        policy_entry = _flow_agent_policy_entry(
            agent_id,
            db_user_id=db_user_id,
            active_group_ids=active_group_ids,
        )
        if policy_entry is not None:
            curation = policy_entry.get("curation")
            has_domain_pack = isinstance(curation, dict) and bool(
                str(curation.get("domain_pack_id") or "").strip()
            )
            if (
                agent_id.startswith("ca_")
                and node.data.validation_attachments
                and not has_domain_pack
            ):
                agent_name = str(node.data.agent_display_name or agent_id)
                missing_references.append(
                    f"node '{node.id}' ({agent_name}) references agent_id "
                    f"'{agent_id}', which no longer declares validation attachments"
                )
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


def _flow_to_response(
    flow: CurationFlow,
    *,
    active_group_ids: list[str] | None = None,
) -> FlowResponse:
    """Convert a stored flow to an API response with validation defaults hydrated."""

    try:
        persisted_migration = migrate_persisted_flow_definition(
            flow.flow_definition
        )
    except PersistedFlowMigrationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    flow_definition = _validated_flow_definition(
        FlowDefinition.model_validate(persisted_migration.definition),
        db_user_id=flow.user_id,
        active_group_ids=active_group_ids,
        tolerate_unresolvable_custom_agent_attachments=True,
    )
    missing_references = _missing_flow_agent_reference_messages(
        flow_definition,
        db_user_id=flow.user_id,
        active_group_ids=active_group_ids,
    )
    validation_warnings = []
    if persisted_migration.changed:
        validation_warnings.append(
            FlowValidationWarning(
                type="WARNING",
                message=(
                    "This saved flow contained retired validation selections. "
                    "They were removed from the loaded definition; save the flow "
                    "to persist the repaired configuration."
                ),
            )
        )
    if missing_references:
        validation_warnings.append(
            FlowValidationWarning(
                type="CRITICAL",
                message=_missing_flow_agent_references_detail(missing_references),
            )
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
        has_critical_issues=any(
            warning.type == "CRITICAL" for warning in validation_warnings
        ),
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

    return _flow_to_response(
        flow,
        active_group_ids=get_groups_from_provider_groups(
            user.get("cognito:groups", [])
        ),
    )


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
    active_group_ids = get_groups_from_provider_groups(user.get("cognito:groups", []))

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
            active_group_ids=active_group_ids,
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
            exc=_sanitized_flow_db_error(type(e.orig).__name__, operation="create"),
        )

    logger.info("Created flow %s '%s' for user %s", flow.id, flow.name, db_user.id)

    return _flow_to_response(flow, active_group_ids=active_group_ids)


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
    active_group_ids = get_groups_from_provider_groups(user.get("cognito:groups", []))
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
            active_group_ids=active_group_ids,
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
                exc=_sanitized_flow_db_error(type(e.orig).__name__, operation="update"),
            )
    else:
        logger.info('[Flow Update] No changes detected for flow %s', flow_id)

    return _flow_to_response(flow, active_group_ids=active_group_ids)


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
