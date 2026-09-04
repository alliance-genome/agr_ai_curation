"""Custom agent CRUD API endpoints for Agent Workshop."""

import asyncio
import json
import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Literal, NoReturn, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .auth import get_auth_dependency
from src.models.sql import get_db
from src.services.user_service import set_global_user_from_cognito
from src.lib.context import set_current_session_id, set_current_user_id
from src.lib.openai_agents import run_agent_streamed
from src.lib.openai_agents.event_types import INTERNAL_EXTRACTION_RESULT_EVENT_TYPE
from src.lib.openai_agents.langfuse_client import clear_pending_configs
from src.lib.agent_studio.catalog_service import get_agent_by_id
from src.lib.agent_studio.streaming import flatten_runner_event as _flatten_runner_event
from src.lib.agent_studio.custom_agent_service import (
    CustomAgentAccessError,
    CustomAgentNotFoundError,
    create_custom_agent,
    clone_saved_custom_agent,
    custom_agent_to_dict,
    get_custom_agent_for_user,
    get_custom_agent_visible_to_user,
    parse_custom_agent_id,
    get_custom_agent_runtime_info,
    list_custom_agents_for_user,
    list_custom_agent_versions,
    make_custom_agent_id,
    soft_delete_custom_agent,
    update_custom_agent,
)
from src.lib.agent_studio.authoring_validation import AuthoringValidationError
from src.lib.agent_studio.models import AgentWorkshopContext
from src.lib.http_errors import log_exception, raise_sanitized_http_exception
from src.lib.group_rules import get_groups_from_provider_groups
from src.lib.agent_access import is_resource_access_allowed
from src.schemas.agent_execution_revision import AgentExecutionSnapshot, AgentOutputContract
from src.schemas.generic_extraction_profile import GenericProfileContract
from src.lib.agent_studio.execution_revision_service import ExecutionRevisionConflictError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agent-studio/custom-agents")


def _authenticated_group_ids(user: Dict[str, Any]) -> list[str]:
    return get_groups_from_provider_groups(user.get("cognito:groups", []))


def _require_custom_agent_group_access(agent: Any, user: Dict[str, Any]) -> None:
    if not is_resource_access_allowed(
        visibility_allowed=True,
        allowed_group_ids=list(getattr(agent, "allowed_group_ids", []) or []),
        active_group_ids=_authenticated_group_ids(user),
        resource_kind="custom_agent",
    ):
        raise CustomAgentNotFoundError("Custom agent not found")


class _CustomAgentDatabaseError(RuntimeError):
    """Sanitized custom-agent database failure safe for logs and Sentry."""


def _sanitized_custom_agent_db_error(exc: IntegrityError, *, operation: str) -> _CustomAgentDatabaseError:
    return _CustomAgentDatabaseError(
        f"Custom agent {operation} database error ({type(exc.orig).__name__})"
    )


def _raise_custom_agent_lookup_http_exception(
    *,
    exc: CustomAgentNotFoundError | CustomAgentAccessError,
    log_message: str,
) -> NoReturn:
    """Map custom-agent lookup failures to client-safe HTTP errors."""

    status_code = 404 if isinstance(exc, CustomAgentNotFoundError) else 403
    detail = "Custom agent not found" if status_code == 404 else "Access denied to custom agent"
    raise_sanitized_http_exception(
        logger,
        status_code=status_code,
        detail=detail,
        log_message=log_message,
        exc=exc,
        level=logging.WARNING,
    )


def _raise_custom_agent_validation_http_exception(
    *,
    exc: Exception,
    status_code: int,
    detail: Any,
    log_message: str,
    log_extra: Optional[Dict[str, Any]] = None,
) -> NoReturn:
    """Log validation failures while returning a stable client response."""

    logger.warning(
        log_message,
        exc_info=(type(exc), exc, exc.__traceback__),
        extra=log_extra,
    )
    raise HTTPException(status_code=status_code, detail=detail) from exc


def _custom_agent_log_context(
    *,
    action: str,
    db_user: Any,
    request: Any,
    custom_agent_id: Optional[UUID] = None,
) -> Dict[str, Any]:
    """Build safe custom-agent log metadata without prompt content."""

    return {
        "action": action,
        "user_id": getattr(db_user, "id", None),
        "custom_agent_id": str(custom_agent_id) if custom_agent_id else None,
        "has_template_source": bool(getattr(request, "template_source", None)),
        "has_model_selection": bool(getattr(request, "model_id", None)),
        "tool_count": len(getattr(request, "tool_ids", None) or []),
        "has_output_selection": bool(getattr(request, "output_schema_key", None)),
        "include_group_rules": getattr(request, "include_group_rules", None),
        "has_custom_prompt": getattr(request, "custom_prompt", None) is not None,
        "group_override_count": len(getattr(request, "group_prompt_overrides", None) or {}),
        "allowed_group_count": len(getattr(request, "allowed_group_ids", None) or []),
    }


def _validate_output_transition_request(request):
    explicit = request.model_fields_set & {"output_contract", "new_generic_profile"}
    if len(explicit) > 1 or (explicit and "output_schema_key" in request.model_fields_set):
        raise ValueError("Choose exactly one output transition")
    if any(getattr(request, name) is None for name in explicit):
        raise ValueError("Choose an explicit output state; null is not an output transition")
    return request


class CreateCustomAgentRequest(BaseModel):
    """Create request for custom agent."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    template_source: Optional[str] = Field(None, min_length=1, max_length=100)
    clone_source_agent_id: Optional[str] = Field(None, min_length=1, max_length=100)
    clone_source_updated_at: Optional[datetime] = None
    visibility: Literal["private", "project"] = "private"
    name: str = Field(..., min_length=1, max_length=100)
    custom_prompt: Optional[str] = None
    # Removed legacy MOD aliases — group-based prompt override fields are now
    # the sole Agent Studio contract after ALL-714.
    group_prompt_overrides: Dict[str, str] = Field(default_factory=dict)
    description: Optional[str] = None
    icon: Optional[str] = Field(None, max_length=10)
    include_group_rules: bool = True
    model_id: Optional[str] = Field(None, min_length=1, max_length=100)
    model_temperature: Optional[float] = None
    model_reasoning: Optional[str] = Field(None, max_length=20)
    tool_ids: Optional[List[str]] = None
    output_schema_key: Optional[str] = Field(None, max_length=100)
    category: Optional[str] = Field(None, max_length=100)
    allowed_group_ids: Optional[List[str]] = None
    output_contract: AgentOutputContract | None = None
    new_generic_profile: GenericProfileContract | None = None

    @model_validator(mode="after")
    def explicit_output_transition(self):
        return _validate_output_transition_request(self)


class UpdateCustomAgentRequest(BaseModel):
    """Update request for custom agent."""
    expected_updated_at: Optional[datetime] = None
    expected_revision_id: UUID | None = None
    visibility: Optional[Literal["private", "project"]] = None

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    name: Optional[str] = Field(None, min_length=1, max_length=100)
    custom_prompt: Optional[str] = None
    group_prompt_overrides: Optional[Dict[str, str]] = None
    description: Optional[str] = None
    icon: Optional[str] = Field(None, max_length=10)
    include_group_rules: Optional[bool] = None
    model_id: Optional[str] = Field(None, min_length=1, max_length=100)
    model_temperature: Optional[float] = None
    model_reasoning: Optional[str] = Field(None, max_length=20)
    tool_ids: Optional[List[str]] = None
    output_schema_key: Optional[str] = Field(None, max_length=100)
    allow_empty_tool_ids: bool = False
    notes: Optional[str] = None
    allowed_group_ids: Optional[List[str]] = None
    output_contract: AgentOutputContract | None = None
    new_generic_profile: GenericProfileContract | None = None

    @model_validator(mode="after")
    def explicit_output_transition(self):
        return _validate_output_transition_request(self)


class TestCustomAgentRequest(BaseModel):
    """Request for running a quick custom-agent test."""

    model_config = ConfigDict(extra="forbid")

    input: str = Field(..., min_length=1)
    group_id: Optional[str] = Field(None, max_length=20)
    document_id: Optional[str] = None
    session_id: Optional[str] = None


class CustomAgentResponse(BaseModel):
    """API response for custom agent."""

    id: str
    agent_id: str
    execution_revision_id: UUID | None = None
    user_id: int
    template_source: Optional[str] = None
    name: str
    description: Optional[str] = None
    custom_prompt: str
    custom_prompt_overlay_status: Literal["clean", "deduplicated", "needs_review"] = "clean"
    custom_prompt_removed_layer_kinds: List[str] = Field(default_factory=list)
    custom_prompt_warning: Optional[str] = None
    group_prompt_overrides: Dict[str, str] = Field(default_factory=dict)
    allowed_group_ids: List[str] = Field(default_factory=list)
    inherited_allowed_group_ids: List[str] = Field(default_factory=list)
    icon: str
    include_group_rules: bool
    model_id: str
    model_temperature: float
    model_reasoning: Optional[str] = None
    tool_ids: List[str] = Field(default_factory=list)
    output_schema_key: Optional[str] = None
    visibility: str
    project_id: Optional[str] = None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class WorkshopSavedReference(BaseModel):
    agent_id: str


class ListCustomAgentsResponse(BaseModel):
    """List response for custom agents."""

    custom_agents: List[CustomAgentResponse]
    total: int


class CustomAgentVersionResponse(BaseModel):
    """Read-only historical prompt record, not an executable configuration."""

    executable: Literal[False] = False

    id: str
    custom_agent_id: str
    version: int
    custom_prompt: str
    group_prompt_overrides: Dict[str, str] = Field(default_factory=dict)
    allowed_group_ids: List[str] = Field(default_factory=list)
    notes: Optional[str] = None
    created_at: datetime


class ExecutionRevisionResponse(BaseModel):
    id: UUID
    agent_id: UUID
    revision: int
    fingerprint: str
    snapshot: AgentExecutionSnapshot
    notes: str | None = None
    created_at: datetime


class ExecutionRevisionListResponse(BaseModel):
    revisions: list[ExecutionRevisionResponse]
    next_before_revision: int | None


class RestoreExecutionRevisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision_id: UUID


def _execution_revision_payload(row, saved):
    return ExecutionRevisionResponse(
        id=row.id, agent_id=row.agent_id, revision=row.revision,
        fingerprint=row.fingerprint, snapshot=saved, notes=row.notes, created_at=row.created_at,
    )


@router.get("/{custom_agent_id}/execution-revisions", response_model=ExecutionRevisionListResponse)
async def list_execution_revisions_endpoint(
    custom_agent_id: UUID,
    before_revision: int | None = Query(None, ge=1),
    user: Dict[str, Any] = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> ExecutionRevisionListResponse:
    from src.lib.agent_studio.execution_revision_service import (
        list_execution_revisions, ExecutionRevisionNotFoundError,
    )
    db_user = set_global_user_from_cognito(db, user)
    try:
        rows, cursor = list_execution_revisions(
            db, custom_agent_id, db_user.id,
            active_group_ids=_authenticated_group_ids(user), before_revision=before_revision,
        )
        return ExecutionRevisionListResponse(
            revisions=[_execution_revision_payload(row, saved) for row, saved in rows],
            next_before_revision=cursor,
        )
    except ExecutionRevisionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Executable revision not found") from exc


@router.get("/{custom_agent_id}/execution-revisions/{revision_id}", response_model=ExecutionRevisionResponse)
async def get_execution_revision_endpoint(
    custom_agent_id: UUID, revision_id: UUID,
    user: Dict[str, Any] = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> ExecutionRevisionResponse:
    from src.lib.agent_studio.execution_revision_service import (
        get_execution_revision, ExecutionRevisionNotFoundError,
    )
    db_user = set_global_user_from_cognito(db, user)
    try:
        row, saved = get_execution_revision(
            db, custom_agent_id, revision_id, db_user.id,
            active_group_ids=_authenticated_group_ids(user),
        )
        return _execution_revision_payload(row, saved)
    except ExecutionRevisionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Executable revision not found") from exc


@router.post("/{custom_agent_id}/execution-revisions/{revision_id}/restore", response_model=CustomAgentResponse)
async def restore_execution_revision_endpoint(
    custom_agent_id: UUID, revision_id: UUID, request: RestoreExecutionRevisionRequest,
    user: Dict[str, Any] = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> CustomAgentResponse:
    from src.lib.agent_studio.execution_revision_service import (
        restore_execution_revision, ExecutionRevisionNotFoundError,
    )
    db_user = set_global_user_from_cognito(db, user)
    try:
        restore_execution_revision(
            db, custom_agent_id, revision_id, user_id=db_user.id,
            expected_revision_id=request.expected_revision_id,
            active_group_ids=_authenticated_group_ids(user),
        )
        agent = get_custom_agent_for_user(db, custom_agent_id, db_user.id)
        db.commit()
        db.refresh(agent)
        return _as_response_payload(agent)
    except ExecutionRevisionNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail="Executable revision not found") from exc
    except ExecutionRevisionConflictError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except IntegrityError as exc:
        db.rollback()
        raise_sanitized_http_exception(
            logger, status_code=500, detail="Database error while restoring agent revision",
            log_message="Database error while restoring agent revision",
            exc=_sanitized_custom_agent_db_error(exc, operation="restore_revision"),
        )


def _as_response_payload(agent_obj) -> CustomAgentResponse:
    return CustomAgentResponse(**custom_agent_to_dict(agent_obj))


def _as_version_payload(version_obj) -> CustomAgentVersionResponse:
    return CustomAgentVersionResponse(
        id=str(version_obj.id),
        custom_agent_id=str(version_obj.custom_agent_id),
        version=version_obj.version,
        custom_prompt=version_obj.custom_prompt,
        group_prompt_overrides=version_obj.group_prompt_overrides or {},
        allowed_group_ids=version_obj.allowed_group_ids,
        notes=version_obj.notes,
        created_at=version_obj.created_at,
    )


class WorkshopDraftValidationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workshop: AgentWorkshopContext
    phase: Literal["pre_apply", "post_apply"]


@router.post("/validate-draft")
async def validate_workshop_draft_endpoint(
    request: WorkshopDraftValidationRequest,
    user: Dict[str, Any] = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> dict:
    from src.models.sql.user import User
    from src.lib.agent_studio.authoring_context import workshop_draft_fingerprint
    from src.lib.agent_studio.workshop_authoring import validate_workshop_context

    db_user = db.query(User).filter(User.auth_sub == str(user.get("sub") or "")).one_or_none()
    if db_user is None:
        raise HTTPException(status_code=403, detail="Authenticated curator not found")
    if request.workshop.draft_fingerprint != workshop_draft_fingerprint(request.workshop):
        raise HTTPException(status_code=422, detail="Workshop candidate fingerprint mismatch")
    return validate_workshop_context(
        db, workshop=request.workshop, user_id=db_user.id,
        active_group_ids=_authenticated_group_ids(user), phase=request.phase,
    ).to_dict()


@router.get("/{custom_agent_id}/authoring-reference", response_model=WorkshopSavedReference)
async def get_workshop_saved_reference(
    custom_agent_id: UUID,
    user: Dict[str, Any] = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> WorkshopSavedReference:
    """Refresh the authorized capability catalog before exposing a flow reference."""
    from src.lib.agent_studio.capability_catalog import CapabilityCatalogContext, build_authorized_capability_catalog
    from src.models.sql.user import User
    db_user = db.query(User).filter(User.auth_sub == user.get("sub")).one_or_none()
    if db_user is None:
        raise HTTPException(status_code=403, detail="Authoring access is unavailable")
    agent_id = make_custom_agent_id(custom_agent_id)
    records = build_authorized_capability_catalog(
        db=db, context=CapabilityCatalogContext(
            user_id=db_user.id, active_group_ids=tuple(_authenticated_group_ids(user)),
            active_tab="flows", artifact_kind="flow",
        ),
    )
    if not any(record.kind == "agent" and record.resource_id == agent_id and record.selectable
               and record.availability == "available" and record.compatibility.get("flow_selectable")
               for record in records):
        raise HTTPException(status_code=409, detail="The saved agent is not available in the current flow catalog")
    return WorkshopSavedReference(agent_id=agent_id)


@router.post("", response_model=CustomAgentResponse, status_code=201)
async def create_custom_agent_endpoint(
    request: CreateCustomAgentRequest,
    user: Dict[str, Any] = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> CustomAgentResponse:
    """Create custom agent from template or explicit model/tool settings."""
    db_user = set_global_user_from_cognito(db, user)
    log_context = _custom_agent_log_context(
        action="create",
        db_user=db_user,
        request=request,
    )
    try:
        source = None
        if request.clone_source_agent_id:
            source_id = parse_custom_agent_id(request.clone_source_agent_id)
            if source_id is None:
                raise ValueError("Invalid clone source")
            source = get_custom_agent_visible_to_user(db, source_id, db_user.id)
            db.refresh(source, with_for_update=True)
            from src.lib.agent_studio.workshop_authoring import workshop_source_is_current
            if not workshop_source_is_current(source, request.clone_source_updated_at):
                raise ValueError("The clone source changed; reopen the source")
            _require_custom_agent_group_access(source, user)
        if source is not None:
            edits = request.model_dump(exclude_unset=True, exclude={
                "clone_source_agent_id", "clone_source_updated_at", "name",
                "visibility", "allowed_group_ids",
            })
            custom_agent = clone_saved_custom_agent(
                db, db_user.id, source, name=request.name,
                allowed_group_ids=request.allowed_group_ids,
                active_group_ids=_authenticated_group_ids(user),
                visibility=request.visibility, edits=edits,
            )
        else:
            custom_agent = create_custom_agent(
                db=db,
                user_id=db_user.id,
                template_source=request.template_source,
                name=request.name,
                custom_prompt=request.custom_prompt,
                group_prompt_overrides=request.group_prompt_overrides,
                description=request.description,
                icon=request.icon,
                include_group_rules=request.include_group_rules,
                model_id=request.model_id,
                model_temperature=request.model_temperature,
                model_reasoning=request.model_reasoning,
                model_reasoning_provided="model_reasoning" in request.model_fields_set,
                tool_ids=request.tool_ids,
                output_schema_key=request.output_schema_key,
                output_schema_key_provided="output_schema_key" in request.model_fields_set,
                output_contract=request.output_contract,
                new_generic_profile=request.new_generic_profile,
                category=request.category,
                allowed_group_ids=request.allowed_group_ids,
                active_group_ids=_authenticated_group_ids(user),
                visibility=request.visibility,
            )
        db.commit()
        db.refresh(custom_agent)
        logger.info(
            "Created custom agent",
            extra={
                **log_context,
                "custom_agent_id": str(
                    getattr(custom_agent, "id", getattr(custom_agent, "agent_id", ""))
                ),
            },
        )
        return _as_response_payload(custom_agent)
    except ValueError as exc:
        db.rollback()
        if "already exists" in str(exc):
            _raise_custom_agent_validation_http_exception(
                exc=exc,
                status_code=409,
                detail="A custom agent with this name already exists",
                log_message="Failed to create custom agent because the target name already exists",
                log_extra=log_context,
            )
        _raise_custom_agent_validation_http_exception(
            exc=exc,
            status_code=400,
            detail=(
                exc.result.to_dict()
                if isinstance(exc, AuthoringValidationError)
                else str(exc) or "Custom agent request is invalid"
            ),
            log_message="Failed to create custom agent",
            log_extra=log_context,
        )
    except IntegrityError as exc:
        db.rollback()
        error_text = str(exc.orig)
        if (
            "uq_custom_agents_active" in error_text
            or "uq_agents_active_custom_name_per_user" in error_text
            or "duplicate key value violates unique constraint" in error_text
        ):
            raise HTTPException(status_code=409, detail="A custom agent with this name already exists")
        raise_sanitized_http_exception(
            logger,
            status_code=500,
            detail="Database error while creating custom agent",
            log_message="Database error while creating custom agent",
            exc=_sanitized_custom_agent_db_error(exc, operation="create"),
        )


@router.get("", response_model=ListCustomAgentsResponse)
async def list_custom_agents_endpoint(
    template_source: Optional[str] = Query(None, description="Optional template source filter"),
    user: Dict[str, Any] = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> ListCustomAgentsResponse:
    """List active custom agents for current user."""
    db_user = set_global_user_from_cognito(db, user)
    try:
        agents = [
            agent
            for agent in list_custom_agents_for_user(
                db,
                db_user.id,
                template_source=template_source,
            )
            if is_resource_access_allowed(
                visibility_allowed=True,
                allowed_group_ids=list(agent.allowed_group_ids),
                active_group_ids=_authenticated_group_ids(user),
                resource_kind="custom_agent_catalog",
            )
        ]
        return ListCustomAgentsResponse(
            custom_agents=[_as_response_payload(agent) for agent in agents],
            total=len(agents),
        )
    except ValueError as exc:
        _raise_custom_agent_validation_http_exception(
            exc=exc,
            status_code=400,
            detail="Custom agent query is invalid",
            log_message="Failed to list custom agents",
        )


@router.get("/{custom_agent_id}", response_model=CustomAgentResponse)
async def get_custom_agent_endpoint(
    custom_agent_id: UUID,
    user: Dict[str, Any] = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> CustomAgentResponse:
    """Get custom agent details with staleness metadata."""
    db_user = set_global_user_from_cognito(db, user)
    try:
        custom_agent = get_custom_agent_for_user(db, custom_agent_id, db_user.id)
        _require_custom_agent_group_access(custom_agent, user)
        return _as_response_payload(custom_agent)
    except (CustomAgentNotFoundError, CustomAgentAccessError) as exc:
        _raise_custom_agent_lookup_http_exception(
            exc=exc,
            log_message=f"Failed to load custom agent '{custom_agent_id}'",
        )


@router.put("/{custom_agent_id}", response_model=CustomAgentResponse)
async def update_custom_agent_endpoint(
    custom_agent_id: UUID,
    request: UpdateCustomAgentRequest,
    user: Dict[str, Any] = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> CustomAgentResponse:
    """Update custom-agent settings and/or prompt text."""
    db_user = set_global_user_from_cognito(db, user)
    log_context = _custom_agent_log_context(
        action="update",
        db_user=db_user,
        request=request,
        custom_agent_id=custom_agent_id,
    )
    try:
        custom_agent = get_custom_agent_for_user(db, custom_agent_id, db_user.id)
        _require_custom_agent_group_access(custom_agent, user)
        update_custom_agent(
            db=db,
            custom_agent=custom_agent,
            expected_updated_at=request.expected_updated_at,
            expected_revision_id=request.expected_revision_id,
            visibility=request.visibility,
            name=request.name,
            custom_prompt=request.custom_prompt,
            group_prompt_overrides=request.group_prompt_overrides,
            description=request.description,
            icon=request.icon,
            include_group_rules=request.include_group_rules,
            model_id=request.model_id,
            model_temperature=request.model_temperature,
            model_reasoning=request.model_reasoning,
            model_reasoning_provided="model_reasoning" in request.model_fields_set,
            tool_ids=request.tool_ids,
            output_schema_key=request.output_schema_key,
            output_schema_key_provided="output_schema_key" in request.model_fields_set,
            output_contract=request.output_contract,
            new_generic_profile=request.new_generic_profile,
            allow_empty_tool_ids=request.allow_empty_tool_ids,
            notes=request.notes,
            allowed_group_ids=request.allowed_group_ids,
            active_group_ids=_authenticated_group_ids(user),
        )
        db.commit()
        db.refresh(custom_agent)
        logger.info("Updated custom agent", extra=log_context)
        return _as_response_payload(custom_agent)
    except (CustomAgentNotFoundError, CustomAgentAccessError) as exc:
        db.rollback()
        _raise_custom_agent_lookup_http_exception(
            exc=exc,
            log_message=f"Failed to update custom agent '{custom_agent_id}'",
        )
    except ExecutionRevisionConflictError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        db.rollback()
        if "already exists" in str(exc):
            _raise_custom_agent_validation_http_exception(
                exc=exc,
                status_code=409,
                detail="A custom agent with this name already exists",
                log_message=f"Failed to update custom agent '{custom_agent_id}' because the target name already exists",
                log_extra=log_context,
            )
        _raise_custom_agent_validation_http_exception(
            exc=exc,
            status_code=400,
            detail=(
                exc.result.to_dict()
                if isinstance(exc, AuthoringValidationError)
                else str(exc) or "Custom agent update is invalid"
            ),
            log_message=f"Failed to update custom agent '{custom_agent_id}'",
            log_extra=log_context,
        )
    except IntegrityError as exc:
        db.rollback()
        error_text = str(exc.orig)
        if (
            "uq_custom_agents_active" in error_text
            or "uq_agents_active_custom_name_per_user" in error_text
            or "duplicate key value violates unique constraint" in error_text
        ):
            raise HTTPException(status_code=409, detail="A custom agent with this name already exists")
        raise_sanitized_http_exception(
            logger,
            status_code=500,
            detail="Database error while updating custom agent",
            log_message=f"Database error while updating custom agent '{custom_agent_id}'",
            exc=_sanitized_custom_agent_db_error(exc, operation="update"),
        )


@router.delete("/{custom_agent_id}")
async def delete_custom_agent_endpoint(
    custom_agent_id: UUID,
    user: Dict[str, Any] = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Soft delete custom agent."""
    db_user = set_global_user_from_cognito(db, user)
    try:
        custom_agent = get_custom_agent_for_user(db, custom_agent_id, db_user.id)
        _require_custom_agent_group_access(custom_agent, user)
        soft_delete_custom_agent(custom_agent)
        db.commit()
        return {"status": "deleted", "id": str(custom_agent_id)}
    except (CustomAgentNotFoundError, CustomAgentAccessError) as exc:
        db.rollback()
        _raise_custom_agent_lookup_http_exception(
            exc=exc,
            log_message=f"Failed to delete custom agent '{custom_agent_id}'",
        )


@router.get("/{custom_agent_id}/versions", response_model=List[CustomAgentVersionResponse])
async def list_custom_agent_versions_endpoint(
    custom_agent_id: UUID,
    user: Dict[str, Any] = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> List[CustomAgentVersionResponse]:
    """List version snapshots for a custom agent."""
    db_user = set_global_user_from_cognito(db, user)
    try:
        custom_agent = get_custom_agent_for_user(db, custom_agent_id, db_user.id)
        _require_custom_agent_group_access(custom_agent, user)
        versions = list_custom_agent_versions(db, custom_agent.id)
        return [_as_version_payload(v) for v in versions]
    except (CustomAgentNotFoundError, CustomAgentAccessError) as exc:
        _raise_custom_agent_lookup_http_exception(
            exc=exc,
            log_message=f"Failed to list versions for custom agent '{custom_agent_id}'",
        )


@router.post("/{custom_agent_id}/test")
async def test_custom_agent_endpoint(
    custom_agent_id: UUID,
    request: TestCustomAgentRequest,
    user: Dict[str, Any] = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """Run a quick isolated test for a custom agent and stream events via SSE."""
    db_user = set_global_user_from_cognito(db, user)
    try:
        custom_agent = get_custom_agent_for_user(db, custom_agent_id, db_user.id)
        _require_custom_agent_group_access(custom_agent, user)
    except (CustomAgentNotFoundError, CustomAgentAccessError) as exc:
        _raise_custom_agent_lookup_http_exception(
            exc=exc,
            log_message=f"Failed to initialize custom agent test for '{custom_agent_id}'",
        )

    runtime_info = get_custom_agent_runtime_info(
        make_custom_agent_id(custom_agent.id), db=db, user_id=db_user.id,
        active_group_ids=_authenticated_group_ids(user),
    )
    if not runtime_info:
        raise HTTPException(status_code=404, detail="Custom agent is not available")
    if runtime_info.requires_document and not request.document_id:
        raise HTTPException(
            status_code=400,
            detail="This custom agent requires a document_id for testing",
        )

    user_sub = user.get("sub") or db_user.auth_sub
    if not user_sub:
        raise HTTPException(status_code=401, detail="User identifier not found in token")

    session_id = request.session_id or f"custom-test-{uuid.uuid4()}"
    active_groups = [request.group_id] if request.group_id else []
    authenticated_groups = _authenticated_group_ids(user)

    set_current_session_id(session_id)
    set_current_user_id(str(user_sub))

    clear_pending_configs()
    try:
        agent = get_agent_by_id(
            make_custom_agent_id(custom_agent.id),
            db_user_id=db_user.id,
            document_id=request.document_id,
            user_id=str(user_sub),
            active_groups=active_groups,
            authenticated_groups=authenticated_groups,
        )
    except Exception as exc:
        raise_sanitized_http_exception(
            logger,
            status_code=400,
            detail="Failed to initialize custom agent",
            log_message=f"Failed to initialize custom agent '{custom_agent_id}' for isolated test execution",
            exc=exc,
        )

    async def _stream_events():
        trace_id = None
        try:
            async for event in run_agent_streamed(
                context_messages=[{"role": "user", "content": request.input}],
                user_id=str(user_sub),
                session_id=session_id,
                document_id=request.document_id,
                active_groups=active_groups,
                agent=agent,
            ):
                if event.get("type") == INTERNAL_EXTRACTION_RESULT_EVENT_TYPE:
                    continue
                flat = _flatten_runner_event(event, session_id)
                if flat.get("type") == "RUN_STARTED":
                    trace_id = flat.get("trace_id")
                elif flat.get("type") == "RUN_ERROR":
                    raw_message = str(flat.get("message") or "").strip()
                    if raw_message:
                        logger.error(
                            "Custom-agent test runner emitted RUN_ERROR for %s: %s",
                            custom_agent_id,
                            raw_message,
                            extra={"session_id": session_id, "trace_id": trace_id or flat.get("trace_id")},
                        )
                    else:
                        logger.error(
                            "Custom-agent test runner emitted RUN_ERROR without message for %s",
                            custom_agent_id,
                            extra={"session_id": session_id, "trace_id": trace_id or flat.get("trace_id")},
                        )
                    flat["message"] = "Custom-agent test failed unexpectedly."
                    details = flat.get("details")
                    if isinstance(details, dict) and "error" in details:
                        flat["details"] = {**details, "error": "Custom-agent test failed unexpectedly."}
                yield f"data: {json.dumps(flat, default=str)}\n\n"

            done_event = {
                "type": "DONE",
                "session_id": session_id,
                "trace_id": trace_id,
            }
            yield f"data: {json.dumps(done_event)}\n\n"
        except asyncio.CancelledError:
            logger.warning('Custom-agent test stream cancelled: custom_agent_id=%s', custom_agent_id)
            error_event = {
                "type": "RUN_ERROR",
                "message": "Custom-agent test cancelled unexpectedly.",
                "error_type": "StreamCancelled",
                "trace_id": trace_id,
                "session_id": session_id,
            }
            yield f"data: {json.dumps(error_event)}\n\n"
        except Exception as exc:
            log_exception(
                logger,
                message=f"Custom-agent test stream error for {custom_agent_id}",
                exc=exc,
            )
            error_event = {
                "type": "RUN_ERROR",
                "message": "Custom-agent test failed unexpectedly.",
                "error_type": type(exc).__name__,
                "trace_id": trace_id,
                "session_id": session_id,
            }
            yield f"data: {json.dumps(error_event)}\n\n"

    return StreamingResponse(
        _stream_events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
