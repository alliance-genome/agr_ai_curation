"""Custom agent CRUD API endpoints for Agent Workshop."""

import asyncio
import json
import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import AliasChoices, BaseModel, ConfigDict, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .auth import get_auth_dependency
from src.models.sql import get_db
from src.services.user_service import set_global_user_from_cognito
from src.lib.context import set_current_session_id, set_current_user_id
from src.lib.openai_agents import run_agent_streamed
from src.lib.agent_studio.catalog_service import get_agent_by_id
from src.lib.agent_studio.streaming import flatten_runner_event as _flatten_runner_event
from src.lib.agent_studio.custom_agent_service import (
    CustomAgentAccessError,
    CustomAgentNotFoundError,
    create_custom_agent,
    custom_agent_to_dict,
    get_custom_agent_for_user,
    get_custom_agent_runtime_info,
    list_custom_agents_for_user,
    list_custom_agent_versions,
    make_custom_agent_id,
    revert_custom_agent_to_version,
    soft_delete_custom_agent,
    update_custom_agent,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agent-studio/custom-agents")


class CreateCustomAgentRequest(BaseModel):
    """Create request for custom agent."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    template_source: Optional[str] = Field(None, min_length=1, max_length=100)
    name: str = Field(..., min_length=1, max_length=100)
    custom_prompt: Optional[str] = None
    group_prompt_overrides: Dict[str, str] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("group_prompt_overrides", "mod_prompt_overrides"),
    )
    description: Optional[str] = None
    icon: Optional[str] = Field(None, max_length=10)
    include_group_rules: bool = Field(
        True,
        validation_alias=AliasChoices("include_group_rules", "include_mod_rules"),
    )
    model_id: Optional[str] = Field(None, min_length=1, max_length=100)
    model_temperature: Optional[float] = None
    model_reasoning: Optional[str] = Field(None, max_length=20)
    tool_ids: Optional[List[str]] = None
    output_schema_key: Optional[str] = Field(None, max_length=100)
    category: Optional[str] = Field(None, max_length=100)


class UpdateCustomAgentRequest(BaseModel):
    """Update request for custom agent."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    name: Optional[str] = Field(None, min_length=1, max_length=100)
    custom_prompt: Optional[str] = None
    group_prompt_overrides: Optional[Dict[str, str]] = Field(
        None,
        validation_alias=AliasChoices("group_prompt_overrides", "mod_prompt_overrides"),
    )
    description: Optional[str] = None
    icon: Optional[str] = Field(None, max_length=10)
    include_group_rules: Optional[bool] = Field(
        None,
        validation_alias=AliasChoices("include_group_rules", "include_mod_rules"),
    )
    model_id: Optional[str] = Field(None, min_length=1, max_length=100)
    model_temperature: Optional[float] = None
    model_reasoning: Optional[str] = Field(None, max_length=20)
    tool_ids: Optional[List[str]] = None
    output_schema_key: Optional[str] = Field(None, max_length=100)
    allow_empty_tool_ids: bool = False
    notes: Optional[str] = None


class TestCustomAgentRequest(BaseModel):
    """Request for running a quick custom-agent test."""

    input: str = Field(..., min_length=1)
    group_id: Optional[str] = Field(
        None,
        max_length=20,
        validation_alias=AliasChoices("group_id", "mod_id"),
    )
    document_id: Optional[str] = None
    session_id: Optional[str] = None


class CustomAgentResponse(BaseModel):
    """API response for custom agent."""

    id: str
    agent_id: str
    user_id: int
    template_source: Optional[str] = None
    name: str
    description: Optional[str] = None
    custom_prompt: str
    group_prompt_overrides: Dict[str, str] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("group_prompt_overrides", "mod_prompt_overrides"),
    )
    icon: str
    include_group_rules: bool = Field(
        ...,
        validation_alias=AliasChoices("include_group_rules", "include_mod_rules"),
    )
    model_id: str
    model_temperature: float
    model_reasoning: Optional[str] = None
    tool_ids: List[str] = Field(default_factory=list)
    output_schema_key: Optional[str] = None
    visibility: str
    project_id: Optional[str] = None
    parent_prompt_hash: Optional[str] = None
    current_parent_prompt_hash: Optional[str] = None
    parent_prompt_stale: bool = False
    parent_exists: bool
    is_active: bool
    created_at: datetime
    updated_at: datetime


class ListCustomAgentsResponse(BaseModel):
    """List response for custom agents."""

    custom_agents: List[CustomAgentResponse]
    total: int


class CustomAgentVersionResponse(BaseModel):
    """Version entry response."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    custom_agent_id: str
    version: int
    custom_prompt: str
    group_prompt_overrides: Dict[str, str] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("group_prompt_overrides", "mod_prompt_overrides"),
    )
    notes: Optional[str] = None
    created_at: datetime


class RevertCustomAgentRequest(BaseModel):
    """Optional notes for revert action."""

    notes: Optional[str] = None


def _as_response_payload(agent_obj) -> CustomAgentResponse:
    return CustomAgentResponse(**custom_agent_to_dict(agent_obj))


def _as_version_payload(version_obj) -> CustomAgentVersionResponse:
    return CustomAgentVersionResponse(
        id=str(version_obj.id),
        custom_agent_id=str(version_obj.custom_agent_id),
        version=version_obj.version,
        custom_prompt=version_obj.custom_prompt,
        group_prompt_overrides=version_obj.group_prompt_overrides or {},
        notes=version_obj.notes,
        created_at=version_obj.created_at,
    )


@router.post("", response_model=CustomAgentResponse, status_code=201)
async def create_custom_agent_endpoint(
    request: CreateCustomAgentRequest,
    user: Dict[str, Any] = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> CustomAgentResponse:
    """Create custom agent from template or explicit model/tool settings."""
    db_user = set_global_user_from_cognito(db, user)
    try:
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
            tool_ids=request.tool_ids,
            output_schema_key=request.output_schema_key,
            category=request.category,
        )
        db.commit()
        db.refresh(custom_agent)
        return _as_response_payload(custom_agent)
    except ValueError as exc:
        db.rollback()
        if "already exists" in str(exc):
            raise HTTPException(status_code=409, detail=str(exc))
        raise HTTPException(status_code=400, detail=str(exc))
    except IntegrityError as exc:
        db.rollback()
        error_text = str(exc.orig)
        if (
            "uq_custom_agents_active" in error_text
            or "uq_agents_active_custom_name_per_user" in error_text
            or "duplicate key value violates unique constraint" in error_text
        ):
            raise HTTPException(status_code=409, detail="A custom agent with this name already exists")
        raise HTTPException(status_code=500, detail="Database error while creating custom agent")


@router.get("", response_model=ListCustomAgentsResponse)
async def list_custom_agents_endpoint(
    template_source: Optional[str] = Query(None, description="Optional template source filter"),
    user: Dict[str, Any] = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> ListCustomAgentsResponse:
    """List active custom agents for current user."""
    db_user = set_global_user_from_cognito(db, user)
    try:
        agents = list_custom_agents_for_user(db, db_user.id, template_source=template_source)
        return ListCustomAgentsResponse(
            custom_agents=[_as_response_payload(agent) for agent in agents],
            total=len(agents),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


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
        return _as_response_payload(custom_agent)
    except CustomAgentNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except CustomAgentAccessError as exc:
        raise HTTPException(status_code=403, detail=str(exc))


@router.put("/{custom_agent_id}", response_model=CustomAgentResponse)
async def update_custom_agent_endpoint(
    custom_agent_id: UUID,
    request: UpdateCustomAgentRequest,
    user: Dict[str, Any] = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> CustomAgentResponse:
    """Update custom-agent settings and/or prompt text."""
    db_user = set_global_user_from_cognito(db, user)
    try:
        custom_agent = get_custom_agent_for_user(db, custom_agent_id, db_user.id)
        update_custom_agent(
            db=db,
            custom_agent=custom_agent,
            name=request.name,
            custom_prompt=request.custom_prompt,
            group_prompt_overrides=request.group_prompt_overrides,
            description=request.description,
            icon=request.icon,
            include_group_rules=request.include_group_rules,
            model_id=request.model_id,
            model_temperature=request.model_temperature,
            model_reasoning=request.model_reasoning,
            tool_ids=request.tool_ids,
            output_schema_key=request.output_schema_key,
            allow_empty_tool_ids=request.allow_empty_tool_ids,
            notes=request.notes,
        )
        db.commit()
        db.refresh(custom_agent)
        return _as_response_payload(custom_agent)
    except CustomAgentNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc))
    except CustomAgentAccessError as exc:
        db.rollback()
        raise HTTPException(status_code=403, detail=str(exc))
    except ValueError as exc:
        db.rollback()
        if "already exists" in str(exc):
            raise HTTPException(status_code=409, detail=str(exc))
        raise HTTPException(status_code=400, detail=str(exc))
    except IntegrityError as exc:
        db.rollback()
        error_text = str(exc.orig)
        if (
            "uq_custom_agents_active" in error_text
            or "uq_agents_active_custom_name_per_user" in error_text
            or "duplicate key value violates unique constraint" in error_text
        ):
            raise HTTPException(status_code=409, detail="A custom agent with this name already exists")
        raise HTTPException(status_code=500, detail="Database error while updating custom agent")


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
        soft_delete_custom_agent(custom_agent)
        db.commit()
        return {"status": "deleted", "id": str(custom_agent_id)}
    except CustomAgentNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc))
    except CustomAgentAccessError as exc:
        db.rollback()
        raise HTTPException(status_code=403, detail=str(exc))


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
        versions = list_custom_agent_versions(db, custom_agent.id)
        return [_as_version_payload(v) for v in versions]
    except CustomAgentNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except CustomAgentAccessError as exc:
        raise HTTPException(status_code=403, detail=str(exc))


@router.post("/{custom_agent_id}/revert/{version}", response_model=CustomAgentResponse)
async def revert_custom_agent_endpoint(
    custom_agent_id: UUID,
    version: int,
    request: RevertCustomAgentRequest,
    user: Dict[str, Any] = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> CustomAgentResponse:
    """Revert custom-agent prompt to a specific saved version."""
    db_user = set_global_user_from_cognito(db, user)
    try:
        custom_agent = get_custom_agent_for_user(db, custom_agent_id, db_user.id)
        revert_custom_agent_to_version(
            db=db,
            custom_agent=custom_agent,
            version=version,
            notes=request.notes,
        )
        db.commit()
        db.refresh(custom_agent)
        return _as_response_payload(custom_agent)
    except CustomAgentNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc))
    except CustomAgentAccessError as exc:
        db.rollback()
        raise HTTPException(status_code=403, detail=str(exc))


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
    except CustomAgentNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except CustomAgentAccessError as exc:
        raise HTTPException(status_code=403, detail=str(exc))

    runtime_info = get_custom_agent_runtime_info(make_custom_agent_id(custom_agent.id), db=db)
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

    set_current_session_id(session_id)
    set_current_user_id(str(user_sub))

    try:
        agent = get_agent_by_id(
            make_custom_agent_id(custom_agent.id),
            db_user_id=db_user.id,
            document_id=request.document_id,
            user_id=str(user_sub),
            active_groups=active_groups,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Failed to initialize custom agent: {exc}")

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
                flat = _flatten_runner_event(event, session_id)
                if flat.get("type") == "RUN_STARTED":
                    trace_id = flat.get("trace_id")
                yield f"data: {json.dumps(flat, default=str)}\n\n"

            done_event = {
                "type": "DONE",
                "session_id": session_id,
                "sessionId": session_id,
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
                "sessionId": session_id,
            }
            yield f"data: {json.dumps(error_event)}\n\n"
        except Exception as exc:
            logger.error(
                'Custom-agent test stream error for %s: %s', custom_agent_id, exc, exc_info=True,
            )
            error_event = {
                "type": "RUN_ERROR",
                "message": f"Custom-agent test failed: {exc}",
                "error_type": type(exc).__name__,
                "trace_id": trace_id,
                "session_id": session_id,
                "sessionId": session_id,
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
