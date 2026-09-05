"""Agent Studio API endpoints.

Provides endpoints for the Agent Studio feature:
- GET /catalog - Get all agent prompts organized by category
- POST /chat - Stream an OpenAI Agents SDK authoring conversation
- GET /trace/{trace_id}/context - Get enriched trace context
"""

import json
import hashlib
import logging
import os
import re
import asyncio
import uuid
from datetime import datetime, timezone  # noqa: F401 - Agent Studio module API surface.
from typing import Any, Callable, Dict, List, NoReturn, Optional

import boto3
import openai
from agents import MaxTurnsExceeded, ModelBehaviorError, ModelRefusalError
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Path, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from .auth import get_auth_dependency
from . import agent_studio_opus_tools as opus_tools
from .agent_studio_schemas import (
    AgentMetadata,
    AgentTemplateItem,
    AgentTemplatesResponse,
    AgentTestRequest,
    CatalogResponse,
    ChatRequest,
    CloneAgentRequest,
    CombinedPromptRequest,
    CombinedPromptResponse,
    DirectSubmissionRequest,
    DirectSubmissionResponse,
    GroupOption,
    ManualSuggestionRequest,
    ModelOption,
    ModelsResponse,
    PromptPreviewResponse,
    RegistryMetadataResponse,
    ShareAgentRequest,
    SuggestionResponse,
    ToolIdeaConversationEntry,  # noqa: F401 - Agent Studio schema API surface.
    ToolIdeaCreateRequest,
    ToolIdeaListResponse,
    ToolIdeaResponseItem,
    ToolLibraryConfig,
    ToolLibraryItem,
    ToolLibraryResponse,
)
from src.lib.agent_studio import (
    PromptCatalog,
    GroupRuleInfo,
    PromptInfo,
    AgentPrompts,
    ChatMessage,
    ChatContext,
    TraceContextError,
    TraceNotFoundError,
    LangfuseUnavailableError,
    PromptSuggestion,
    SuggestionType,
    submit_suggestion_sns,
)
from src.lib.agent_studio import catalog_service
from src.lib.agent_studio.catalog_service import get_prompt_catalog
from src.lib.agent_studio.capability_catalog import (
    CapabilityCatalogContext,
    CapabilityCatalogRequestError,
    CapabilityCatalogUnavailable,
    get_capability_detail,
    search_capabilities,
)
from src.lib.agent_studio.tool_search_authorization import (
    AuthorizedToolUniverse,
    ToolSearchAuthorizationError,
    compile_authorized_tool_universe,
    is_tool_authorized_at_invocation,
)
import src.lib.agent_studio.chat_session as agent_studio_chat_session
import src.lib.agent_studio.domain_envelope_tools as agent_studio_domain_envelope_tools
import src.lib.agent_studio.prompt_builder as prompt_builder
from src.lib.agent_studio.flow_tools import (
    set_workflow_user_context,
    clear_workflow_user_context,
    set_current_flow_context,
    clear_current_flow_context,
)
from src.lib.observability.background_tasks import (
    add_observed_background_task,
    report_background_task_exception,
)
from src.lib.group_rules import get_groups_from_provider_groups
from src.lib.config import list_groups
from src.lib.agent_access import is_resource_access_allowed
from src.lib.agent_studio.agent_service import get_agent_by_key
from src.lib.agent_studio.authoring_context import workshop_authoring_metadata_json
from src.lib.agent_studio.flow_agent_policy import flow_palette_show_in_palette
from src.lib.flow_edge_roles import agent_can_source_output_attachment
from src.lib.config.schema_discovery import resolve_output_schema
from src.lib.agent_studio.diagnostic_tools import get_diagnostic_tools_registry
from src.lib.agent_studio.custom_agent_service import (
    CustomAgentAccessError,
    CustomAgentNotFoundError,
    clone_visible_agent_for_user,
    custom_main_prompt_for_parent,
    custom_agent_to_dict,
    get_custom_agent_group_prompt,
    get_custom_agent_for_user,
    get_custom_agent_visible_to_user,
    list_custom_agents_visible_to_user,
    make_custom_agent_id,
    normalize_custom_overlay_for_parent,
    normalize_editable_group_prompt_overrides,
    parse_custom_agent_id,
    set_custom_agent_visibility,
)
from src.lib.agent_studio.catalog_service import (
    get_agent_by_id,
    get_agent_metadata,
    tool_requires_document,
)
from src.lib.prompts.assembly import build_agent_prompt_layers
from src.lib.agent_studio.tool_policy_service import get_tool_policy_cache
from src.lib.agent_studio.tool_idea_service import (
    create_tool_idea_request,
    get_primary_project_id_for_user,
    list_tool_idea_requests_for_user,
    tool_idea_request_to_dict,
)
from src.lib.agent_studio.streaming import flatten_runner_event as _flatten_runner_event
from src.lib.agent_studio.openai_runtime import (
    AGENT_STUDIO_OPENAI_MODEL,
    AGENT_STUDIO_REASONING_EFFORT,
    AgentStudioRunState,
    ToolExecutionResult,
    build_agent_studio_model_settings,
    build_agent_studio_tools,
    expected_agent_studio_terminal_outcome,
    run_forced_agent_studio_tool,
    stream_agent_studio_run,
)
from src.lib.openai_agents.config import get_domain_reference_max_values
from src.lib.openai_agents.config import (
    get_api_key,
    get_agent_studio_chat_history_page_size,
    get_agent_studio_chat_recall_chunk_max_chars,
    get_agent_studio_chat_recall_page_size,
    get_agent_studio_openai_max_output_tokens,
    get_agent_studio_openai_max_turns,
    get_agent_studio_provider_tool_result_inline_max_chars,
    get_agent_studio_service_log_default_lines,
    get_agent_studio_suggestion_max_output_tokens,
    get_agent_studio_suggestion_max_turns,
    get_agent_studio_trace_review_aggregate_page_size,
    get_agent_studio_trace_review_chunk_max_chars,
    get_agent_studio_trace_review_page_size,
    get_agent_studio_workshop_prompt_chunk_max_chars,
)
from src.lib.executable_runs import (
    ExecutableRunAccessError,
    ExecutableRunConflictError,
    executable_run_manager,
)
from src.lib.alerts.tool_failure_notifier import notify_tool_failure
from src.lib.chat_history_repository import (
    ChatHistoryRepository,
    ChatHistorySessionNotFoundError,
    ChatMessageRecord,
    ChatSessionRecord,
)
from src.lib.config import list_model_definitions
from src.lib.packages import load_installed_agent_studio_prompt
from src.lib.context import set_current_session_id, set_current_user_id
from src.lib.http_errors import log_exception, raise_sanitized_http_exception
from src.lib.runtime_payload_budget import provider_context_preflight
from src.lib.observability.runtime import report_runtime_exception
from src.lib.openai_agents import run_agent_streamed
from src.lib.openai_agents.event_types import INTERNAL_EXTRACTION_RESULT_EVENT_TYPE
from src.lib.openai_agents.langfuse_client import clear_pending_configs
from src.models.sql.agent import Agent as UnifiedAgent
from src.models.sql import SessionLocal, get_db
from src.models.sql.chat_session import ChatSession as ChatSessionModel
from src.services.user_service import set_global_user_from_cognito

logger = logging.getLogger(__name__)

AGENT_STUDIO_SEEDED_SESSION_PREFIX = agent_studio_chat_session.AGENT_STUDIO_SEEDED_SESSION_PREFIX
def _raise_agent_studio_lookup_http_exception(
    *,
    exc: CustomAgentNotFoundError | CustomAgentAccessError,
    log_message: str,
    not_found_detail: str,
    access_denied_detail: str,
    not_found_error_types: tuple[type[Exception], ...] = (CustomAgentNotFoundError,),
) -> NoReturn:
    """Map lookup/access failures to client-safe HTTP errors with logging."""

    status_code = 404 if isinstance(exc, not_found_error_types) else 403
    detail = not_found_detail if status_code == 404 else access_denied_detail
    raise_sanitized_http_exception(
        logger,
        status_code=status_code,
        detail=detail,
        log_message=log_message,
        exc=exc,
        level=logging.WARNING,
    )


def _raise_agent_studio_validation_http_exception(
    *,
    exc: Exception,
    status_code: int,
    detail: str,
    log_message: str,
) -> NoReturn:
    """Log validation failures while returning a stable client response."""

    raise_sanitized_http_exception(
        logger,
        status_code=status_code,
        detail=detail,
        log_message=log_message,
        exc=exc,
        level=logging.WARNING,
    )


def _load_agent_studio_system_prompt_template() -> str:
    """Load the system prompt selected by the active runtime package profile."""
    return load_installed_agent_studio_prompt().content


# Create router with prefix
router = APIRouter(prefix="/api/agent-studio")


def _authenticated_group_ids(user: Any) -> list[str]:
    """Return canonical groups exclusively from authenticated provider claims."""

    if not isinstance(user, dict):
        return []
    return get_groups_from_provider_groups(user.get("cognito:groups", []))


def _agent_record_is_group_accessible(agent: Any, user: Any) -> bool:
    return is_resource_access_allowed(
        visibility_allowed=True,
        allowed_group_ids=list(agent.allowed_group_ids),
        active_group_ids=_authenticated_group_ids(user),
        resource_kind="agent",
    )


def _require_selected_agent_access(
    *,
    db: Session,
    db_user_id: int,
    user: Dict[str, Any],
    agent_id: str,
) -> None:
    """Fail non-enumeratingly when Agent Studio context names an unavailable agent."""

    if agent_id.startswith("ca_"):
        custom_uuid = parse_custom_agent_id(agent_id)
        if not custom_uuid:
            raise HTTPException(status_code=404, detail="Agent not found")
        try:
            custom_agent = get_custom_agent_visible_to_user(
                db,
                custom_uuid,
                db_user_id,
            )
        except (CustomAgentNotFoundError, CustomAgentAccessError) as exc:
            raise HTTPException(status_code=404, detail="Agent not found") from exc
        if not _agent_record_is_group_accessible(custom_agent, user):
            raise HTTPException(status_code=404, detail="Agent not found")
        return

    if get_agent_by_key(
        db,
        agent_id,
        user_id=db_user_id,
        active_group_ids=_authenticated_group_ids(user),
    ) is None:
        raise HTTPException(status_code=404, detail="Agent not found")


def _merge_custom_agents_into_catalog(
    catalog: PromptCatalog,
    auth_user: Any,
    db: Any,
) -> PromptCatalog:
    """Return catalog augmented with the current user's active custom agents."""
    if not isinstance(auth_user, dict) or not hasattr(db, "query"):
        return catalog

    augmented = catalog.model_copy(deep=True)
    active_group_ids = _authenticated_group_ids(auth_user)
    for category in augmented.categories:
        category.agents = [
            agent
            for agent in category.agents
            if is_resource_access_allowed(
                visibility_allowed=True,
                allowed_group_ids=list(
                    (catalog_service.AGENT_REGISTRY.get(agent.agent_id) or {}).get(
                        "allowed_group_ids"
                    )
                    or []
                ),
                active_group_ids=active_group_ids,
                resource_kind="agent_catalog",
            )
        ]
    augmented.categories = [category for category in augmented.categories if category.agents]
    augmented.total_agents = sum(len(category.agents) for category in augmented.categories)

    db_user = set_global_user_from_cognito(db, auth_user)
    custom_agents = [
        agent
        for agent in list_custom_agents_visible_to_user(db, db_user.id)
        if _agent_record_is_group_accessible(agent, auth_user)
    ]
    if not custom_agents:
        return augmented

    categories_by_name: Dict[str, AgentPrompts] = {c.category: c for c in augmented.categories}
    parent_agents_by_id: Dict[str, PromptInfo] = {
        agent.agent_id: agent
        for category in augmented.categories
        for agent in category.agents
    }

    for custom in custom_agents:
        template_source = str(getattr(custom, "template_source", "") or "").strip()
        template_prompt_info = parent_agents_by_id.get(template_source) if template_source else None
        template_name = template_prompt_info.agent_name if template_prompt_info else template_source
        category = getattr(custom, "category", None) or "Custom"
        custom_id = make_custom_agent_id(custom.id)
        custom_flow_policy_entry = {
            "category": category,
            "supervisor": {
                "enabled": bool(getattr(custom, "supervisor_enabled", False)),
            },
            "frontend": {
                "show_in_palette": bool(getattr(custom, "show_in_palette", True)),
            },
        }
        tools = list(getattr(custom, "tool_ids", None) or [])
        template_group_rules = template_prompt_info.group_rules if template_prompt_info else {}
        raw_overrides = getattr(custom, "group_prompt_overrides", None) or {}
        normalized_overrides = {
            str(group_id).strip().upper(): content
            for group_id, content in raw_overrides.items()
            if str(group_id).strip() and isinstance(content, str) and content.strip()
        }
        effective_group_rules: Dict[str, GroupRuleInfo] = {}
        overlay_normalization = normalize_custom_overlay_for_parent(
            template_source,
            getattr(custom, "instructions", ""),
        )
        main_prompt = ""
        main_prompt_error: Optional[str] = None
        if overlay_normalization.status != "needs_review":
            try:
                main_prompt = custom_main_prompt_for_parent(
                    template_source,
                    getattr(custom, "instructions", ""),
                )
            except ValueError as exc:
                main_prompt_error = str(exc)
        else:
            main_prompt_error = overlay_normalization.warning

        for group_id, parent_group_rule in template_group_rules.items():
            override_content = normalized_overrides.get(group_id.upper())
            effective_group_rules[group_id] = GroupRuleInfo(
                group_id=group_id,
                content=override_content if override_content else parent_group_rule.content,
                source_file=parent_group_rule.source_file,
                description=parent_group_rule.description,
                prompt_id=parent_group_rule.prompt_id,
                prompt_version=parent_group_rule.prompt_version,
                created_at=parent_group_rule.created_at,
                created_by=parent_group_rule.created_by,
            )

        prompt_bundle = None
        prompt_layer_error = None
        if template_source and not main_prompt_error:
            try:
                prompt_bundle = build_agent_prompt_layers(
                    template_source,
                    base_prompt_override=main_prompt,
                )
            except Exception as exc:
                logger.warning(
                    "Could not build prompt layer projection for custom agent %s.",
                    custom.id,
                    exc_info=(type(exc), exc, exc.__traceback__),
                )
                prompt_layer_error = "Prompt layer metadata could not be built."
        elif main_prompt_error:
            prompt_layer_error = "Custom agent prompt needs coordinator review."
        prompt_layers, effective_prompt_hash, layer_manifest = catalog_service.layer_projection(prompt_bundle)

        prompt_info = PromptInfo(
            agent_id=custom_id,
            agent_revision_id=(str(custom.execution_revision_id) if getattr(custom, "execution_revision_id", None) else None),
            agent_name=custom.name,
            description=custom.description or (
                f"Custom agent from {template_name}" if template_name else "Custom scratch agent"
            ),
            base_prompt=main_prompt,
            source_file=f"custom_agent:{custom.id}",
            has_group_rules=bool(effective_group_rules),
            group_rules=effective_group_rules,
            prompt_layers=prompt_layers,
            effective_prompt_hash=effective_prompt_hash,
            layer_manifest=layer_manifest,
            prompt_layer_error=prompt_layer_error,
            custom_prompt_overlay_status=overlay_normalization.status,
            custom_prompt_removed_layer_kinds=overlay_normalization.removed_layer_kinds,
            custom_prompt_warning=overlay_normalization.warning,
            tools=tools,
            subcategory=(
                "My Custom Agents" if custom.user_id == db_user.id else "Shared Agents"
            ),
            show_in_palette=flow_palette_show_in_palette(
                custom_id,
                custom_flow_policy_entry,
            ),
            documentation=None,
            prompt_id=str(custom.id),
            prompt_version=None,
            created_at=custom.created_at,
            created_by=None,
        )

        if category not in categories_by_name:
            categories_by_name[category] = AgentPrompts(category=category, agents=[])
        categories_by_name[category].agents.append(prompt_info)

    augmented.categories = [categories_by_name[name] for name in sorted(categories_by_name.keys())]
    augmented.total_agents = sum(len(category.agents) for category in augmented.categories)
    return augmented


# ============================================================================
# Registry Metadata Endpoints
# ============================================================================


@router.get(
    "/models",
    response_model=ModelsResponse,
    summary="Get model options",
    description="Returns curator-selectable model options from config/models.yaml.",
)
async def get_models_endpoint(
    user: Any = get_auth_dependency(),
) -> ModelsResponse:
    _ = user
    try:
        models = sorted(
            [model for model in list_model_definitions() if bool(getattr(model, "curator_visible", True))],
            key=lambda model: (not bool(model.default), model.name.lower()),
        )
        return ModelsResponse(
            models=[
                ModelOption(
                    model_id=model.model_id,
                    name=model.name,
                    provider=model.provider,
                    description=model.description,
                    guidance=model.guidance,
                    default=model.default,
                    supports_reasoning=model.supports_reasoning,
                    supports_temperature=model.supports_temperature,
                    reasoning_options=list(model.reasoning_options or []),
                    default_reasoning=model.default_reasoning,
                    reasoning_descriptions=dict(model.reasoning_descriptions or {}),
                    recommended_for=list(model.recommended_for or []),
                    avoid_for=list(model.avoid_for or []),
                )
                for model in models
            ]
        )
    except Exception as e:
        raise_sanitized_http_exception(
            logger,
            status_code=500,
            detail="Failed to load model options",
            log_message="Failed to load model options",
            exc=e,
        )


@router.get(
    "/tools/library",
    response_model=ToolLibraryResponse,
    summary="Get tool library",
    description="Returns curator-visible tools from tool_policies.",
)
async def get_tool_library_endpoint(
    user: Any = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> ToolLibraryResponse:
    try:
        entries = get_tool_policy_cache().list_curator_visible(db)
        return ToolLibraryResponse(
            tools=[
                ToolLibraryItem(
                    tool_key=entry.tool_key,
                    display_name=entry.display_name,
                    description=entry.description,
                    category=entry.category,
                    curator_visible=entry.curator_visible,
                    allow_attach=entry.allow_attach,
                    allow_execute=entry.allow_execute,
                    config=ToolLibraryConfig.model_validate(
                        {
                            **entry.config,
                            "requires_document": tool_requires_document(entry.tool_key),
                        }
                    ),
                )
                for entry in entries
                if is_resource_access_allowed(
                    visibility_allowed=True,
                    allowed_group_ids=entry.config.get("allowed_group_ids", []),
                    active_group_ids=_authenticated_group_ids(user),
                    resource_kind="agent_studio_tool",
                )
            ]
        )
    except Exception as e:
        raise_sanitized_http_exception(
            logger,
            status_code=500,
            detail="Failed to load tool library",
            log_message="Failed to load tool library",
            exc=e,
        )


@router.get(
    "/agents/templates",
    response_model=AgentTemplatesResponse,
    summary="Get system agent templates",
    description="Returns system agents available as copy templates in Agent Workshop.",
)
async def get_agent_templates_endpoint(
    user: Any = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> AgentTemplatesResponse:
    from src.lib.agent_studio.domain_output_contract import initial_agent_output_contract

    try:
        rows = (
            db.query(UnifiedAgent)
            .filter(
                UnifiedAgent.visibility == "system",
                UnifiedAgent.is_active == True,  # noqa: E712
                UnifiedAgent.show_in_palette == True,  # noqa: E712
            )
            .order_by(UnifiedAgent.category.asc(), UnifiedAgent.name.asc())
            .all()
        )
        return AgentTemplatesResponse(
            templates=[
                AgentTemplateItem(
                    agent_id=agent.agent_key,
                    name=agent.name,
                    description=agent.description,
                    icon=agent.icon or "🤖",
                    category=agent.category,
                    model_id=agent.model_id,
                    tool_ids=list(agent.tool_ids or []),
                    allowed_group_ids=list(agent.allowed_group_ids),
                    output_schema_key=agent.output_schema_key,
                    output_contract=initial_agent_output_contract(agent).model_dump(mode="json"),
                )
                for agent in rows
                if _agent_record_is_group_accessible(agent, user)
            ],
            group_options=[
                GroupOption(group_id=group.group_id, name=group.name)
                for group in sorted(list_groups(), key=lambda item: item.name.casefold())
            ],
        )
    except Exception as e:
        raise_sanitized_http_exception(
            logger,
            status_code=500,
            detail="Failed to load agent templates",
            log_message="Failed to load agent templates",
            exc=e,
        )


@router.post(
    "/tool-ideas",
    response_model=ToolIdeaResponseItem,
    status_code=201,
    summary="Submit tool idea request",
    description="Submit a curated tool idea request for developer triage.",
)
async def create_tool_idea_endpoint(
    request: ToolIdeaCreateRequest,
    user: Dict[str, Any] = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> ToolIdeaResponseItem:
    """Create a tool idea request for the authenticated curator."""
    db_user = set_global_user_from_cognito(db, user)
    try:
        project_id = get_primary_project_id_for_user(db, db_user.id)
        record = create_tool_idea_request(
            db=db,
            user_id=db_user.id,
            project_id=project_id,
            title=request.title,
            description=request.description,
            opus_conversation=[
                entry.model_dump() for entry in request.opus_conversation or []
            ],
        )
        db.commit()
        db.refresh(record)
        return ToolIdeaResponseItem(**tool_idea_request_to_dict(record))
    except ValueError as exc:
        db.rollback()
        _raise_agent_studio_validation_http_exception(
            exc=exc,
            status_code=400,
            detail="Tool idea request is invalid",
            log_message="Failed to create tool idea request",
        )


@router.get(
    "/tool-ideas",
    response_model=ToolIdeaListResponse,
    summary="List my tool idea requests",
    description="Returns tool idea requests submitted by the current user.",
)
async def list_tool_ideas_endpoint(
    user: Dict[str, Any] = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> ToolIdeaListResponse:
    """List the authenticated curator's tool idea requests."""
    db_user = set_global_user_from_cognito(db, user)
    rows = list_tool_idea_requests_for_user(db, db_user.id)
    items = [ToolIdeaResponseItem(**tool_idea_request_to_dict(row)) for row in rows]
    return ToolIdeaListResponse(tool_ideas=items, total=len(items))


@router.post(
    "/agents/{agent_id}/clone",
    response_model=Dict[str, Any],
    status_code=201,
    summary="Clone visible agent",
    description="Clone a visible system/private/project agent into the caller's private workspace.",
)
async def clone_agent_endpoint(
    agent_id: str,
    request: CloneAgentRequest,
    user: Dict[str, Any] = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Clone any user-visible agent into caller-owned custom agent space."""
    db_user = set_global_user_from_cognito(db, user)
    try:
        custom_agent = clone_visible_agent_for_user(
            db=db,
            user_id=db_user.id,
            source_agent_key=agent_id,
            name=request.name,
            allowed_group_ids=request.allowed_group_ids,
            active_group_ids=_authenticated_group_ids(user),
        )
        db.commit()
        db.refresh(custom_agent)
        return custom_agent_to_dict(custom_agent)
    except (CustomAgentNotFoundError, CustomAgentAccessError) as exc:
        db.rollback()
        _raise_agent_studio_lookup_http_exception(
            exc=exc,
            log_message=f"Failed to clone visible agent '{agent_id}'",
            not_found_detail="Agent not found",
            access_denied_detail="Access denied to agent",
        )
    except ValueError as exc:
        db.rollback()
        if "already exists" in str(exc):
            _raise_agent_studio_validation_http_exception(
                exc=exc,
                status_code=409,
                detail="A custom agent with this name already exists",
                log_message=f"Failed to clone visible agent '{agent_id}' because the target name already exists",
            )
        _raise_agent_studio_validation_http_exception(
            exc=exc,
            status_code=400,
            detail="Agent clone request is invalid",
            log_message=f"Failed to clone visible agent '{agent_id}'",
        )


@router.post(
    "/agents/{agent_id}/share",
    response_model=Dict[str, Any],
    summary="Set custom agent visibility",
    description="Set a custom agent visibility to private or project-shared.",
)
async def share_agent_endpoint(
    agent_id: str,
    request: ShareAgentRequest,
    user: Dict[str, Any] = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Toggle caller-owned custom-agent visibility."""
    custom_uuid = parse_custom_agent_id(agent_id)
    if not custom_uuid:
        raise HTTPException(status_code=400, detail="Only custom agents can be shared")
    db_user = set_global_user_from_cognito(db, user)

    try:
        custom_agent = get_custom_agent_for_user(db, custom_uuid, db_user.id)
        if not _agent_record_is_group_accessible(custom_agent, user):
            db.rollback()
            raise HTTPException(status_code=404, detail="Custom agent not found")
        set_custom_agent_visibility(
            db=db,
            custom_agent=custom_agent,
            user_id=db_user.id,
            visibility=request.visibility,
        )
        db.commit()
        db.refresh(custom_agent)
        return custom_agent_to_dict(custom_agent)
    except (CustomAgentNotFoundError, CustomAgentAccessError) as exc:
        db.rollback()
        _raise_agent_studio_lookup_http_exception(
            exc=exc,
            log_message=f"Failed to update visibility for agent '{agent_id}'",
            not_found_detail="Custom agent not found",
            access_denied_detail="Access denied to custom agent",
        )
    except ValueError as exc:
        db.rollback()
        _raise_agent_studio_validation_http_exception(
            exc=exc,
            status_code=400,
            detail="Agent visibility update is invalid",
            log_message=f"Failed to update visibility for agent '{agent_id}'",
        )

@router.get(
    "/registry/metadata",
    response_model=RegistryMetadataResponse,
    summary="Get agent metadata for frontend",
    description="Returns icons, names, and categories for all agents from AGENT_REGISTRY.",
)
async def get_registry_metadata(
    user: Any = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> RegistryMetadataResponse:
    """
    Get agent metadata for frontend display.

    Returns icons, names, and categories for all agents.
    Frontend should fetch this on load and cache in context.
    """
    from src.lib.agent_studio.catalog_service import AGENT_REGISTRY
    from src.lib.agent_studio.domain_envelope_metadata import (
        custom_agent_revision_metadata,
        domain_envelope_metadata_catalog_by_agent,
    )
    from src.lib.flows.validation_attachments import validation_attachment_catalog_by_agent
    from src.lib.agent_studio.domain_output_contract import domain_extraction_ref_for_agent

    validation_attachments_by_agent = validation_attachment_catalog_by_agent(AGENT_REGISTRY)
    domain_envelope_metadata_by_agent = domain_envelope_metadata_catalog_by_agent(AGENT_REGISTRY)
    agents = {}

    def _produces_flow_artifacts(entry: Dict[str, Any]) -> bool:
        if not agent_can_source_output_attachment(entry):
            return False
        category = str(entry.get("category") or "").strip().lower()
        subcategory = str(entry.get("subcategory") or "").strip().lower()
        if "extract" in category or "extract" in subcategory:
            return True
        output_schema_key = str(entry.get("output_schema_key") or "").strip()
        return bool(output_schema_key and resolve_output_schema(output_schema_key))

    for agent_id, entry in AGENT_REGISTRY.items():
        if not is_resource_access_allowed(
            visibility_allowed=True,
            allowed_group_ids=list(entry.get("allowed_group_ids") or []),
            active_group_ids=_authenticated_group_ids(user),
            resource_kind="agent_registry",
        ):
            continue
        supervisor = entry.get("supervisor", {})
        # supervisor_tool is only set if supervisor is enabled (default True)
        supervisor_enabled = supervisor.get("enabled", True)
        supervisor_tool = supervisor.get("tool_name") if supervisor_enabled else None

        # Icon can be at top level or nested under frontend.icon
        icon = entry.get("icon")
        if icon is None:
            frontend = entry.get("frontend", {})
            icon = frontend.get("icon", "❓")

        domain_ref = domain_extraction_ref_for_agent(agent_id, active_group_ids=_authenticated_group_ids(user))
        agents[agent_id] = AgentMetadata(
            name=entry.get("name", agent_id),
            icon=icon,
            category=entry.get("category", "Unknown"),
            subcategory=entry.get("subcategory"),
            supervisor_tool=supervisor_tool,
            output_schema_key=entry.get("output_schema_key"),
            is_active=entry.get("is_active", True) is not False,
            visible=entry.get("visible", True) is not False,
            allowed_group_ids=list(entry.get("allowed_group_ids") or []),
            produces_flow_artifacts=_produces_flow_artifacts(entry),
            validation_attachments=validation_attachments_by_agent.get(agent_id, []),
            domain_envelope=domain_envelope_metadata_by_agent.get(agent_id),
            domain_extraction_ref=domain_ref.model_dump(mode="json") if domain_ref else None,
        )

    # Include current user's custom agents when authenticated.
    # Direct unit-test calls pass dependency placeholders, so guard by type.
    if isinstance(user, dict):
        db_user = set_global_user_from_cognito(db, user)
        custom_agents = [
            agent
            for agent in list_custom_agents_visible_to_user(db, db_user.id)
            if _agent_record_is_group_accessible(agent, user)
        ]
        for custom in custom_agents:
            category = custom.category or "Custom"
            custom_id = make_custom_agent_id(custom.id)
            receipt = None
            envelope_metadata = None
            execution_metadata_error = None
            try:
                receipt, envelope_metadata = custom_agent_revision_metadata(
                    db, custom_id, db_user.id, active_group_ids=_authenticated_group_ids(user),
                )
            except ValueError:
                execution_metadata_error = "Saved executable revision metadata is unavailable."

            agents[custom_id] = AgentMetadata(
                name=custom.name,
                icon=custom.icon or "❓",
                category=category,
                subcategory=(
                    "My Custom Agents" if custom.user_id == db_user.id else "Shared Agents"
                ),
                supervisor_tool=f"ask_{custom_id.replace('-', '_')}_specialist",
                output_schema_key=receipt.output_contract.output_schema_key if receipt else None,
                is_active=bool(getattr(custom, "is_active", True)) and receipt is not None,
                visible=True,
                allowed_group_ids=list(custom.allowed_group_ids),
                produces_flow_artifacts=bool(receipt and receipt.output_contract.output_state == "structured_extraction"),
                validation_attachments=envelope_metadata["validation_attachments"] if envelope_metadata else [],
                domain_envelope=envelope_metadata,
                execution_metadata_error=execution_metadata_error,
            )

    return RegistryMetadataResponse(agents=agents)


def _custom_agent_template_source(custom: Any) -> Optional[str]:
    raw_template_source = getattr(custom, "template_source", None)
    if not isinstance(raw_template_source, str):
        return None

    template_source = raw_template_source.strip()
    return template_source or None


def _build_custom_agent_effective_prompt_bundle(
    *,
    agent_id: str,
    group_id: Optional[str],
    user: Dict[str, Any],
    db: Session,
    lookup_custom_agent: Callable[[Session, uuid.UUID, Any], Any],
) -> tuple[Any, str, bool]:
    """Assemble a custom agent over its locked parent prompt layers."""

    custom_uuid = parse_custom_agent_id(agent_id)
    if not custom_uuid:
        raise HTTPException(status_code=400, detail="Invalid custom agent id")

    db_user = set_global_user_from_cognito(db, user)
    try:
        custom_agent = lookup_custom_agent(db, custom_uuid, db_user.id)
    except (CustomAgentNotFoundError, CustomAgentAccessError) as exc:
        _raise_agent_studio_lookup_http_exception(
            exc=exc,
            log_message=f"Failed to load custom agent '{agent_id}' for prompt assembly",
            not_found_detail="Custom agent not found",
            access_denied_detail="Access denied to custom agent",
            not_found_error_types=(CustomAgentNotFoundError,),
        )

    if not _agent_record_is_group_accessible(custom_agent, user):
        raise HTTPException(status_code=404, detail="Custom agent not found")

    custom_group_rules_enabled = bool(custom_agent.group_rules_enabled)
    try:
        custom_group_overrides = normalize_editable_group_prompt_overrides(
            custom_agent.group_prompt_overrides or {}
        )
    except ValueError as exc:
        logger.warning(
            "Custom agent group override contains copied locked/core prompt text.",
            exc_info=(type(exc), exc, exc.__traceback__),
            extra={"custom_agent_id": agent_id},
        )
        raise HTTPException(
            status_code=409,
            detail=(
                "Custom agent group rules contain copied locked/core prompt text "
                "that needs coordinator review before preview."
            ),
        ) from exc
    parent_agent_key = str(custom_agent.template_source or "").strip()
    if not parent_agent_key:
        raise HTTPException(
            status_code=400,
            detail="Custom agent is missing its system template source.",
        )

    active_group_id = group_id if custom_group_rules_enabled else None
    try:
        main_prompt = custom_main_prompt_for_parent(
            parent_agent_key,
            custom_agent.instructions,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail=(
                "Custom agent prompt contains copied locked/core prompt text "
                "that needs coordinator review before preview."
            ),
        ) from exc
    bundle = build_agent_prompt_layers(
        parent_agent_key,
        group_id=active_group_id,
        base_prompt_override=main_prompt,
        group_prompt_overrides=custom_group_overrides,
    )
    return bundle, parent_agent_key, custom_group_rules_enabled


# ============================================================================
# Catalog Endpoints
# ============================================================================

@router.get(
    "/catalog",
    response_model=CatalogResponse,
    summary="Get prompt catalog",
    description="Returns all agent prompts organized by category, including group-specific rules.",
)
async def get_catalog(
    user: Dict[str, Any] = get_auth_dependency(),
    db: Session = Depends(get_db),
):
    """Get the complete prompt catalog."""
    try:
        service = get_prompt_catalog()
        catalog = _merge_custom_agents_into_catalog(service.catalog, user, db)
        return CatalogResponse(catalog=catalog)
    except Exception as exc:
        raise_sanitized_http_exception(
            logger,
            status_code=500,
            detail="Failed to load prompt catalog",
            log_message="Failed to get prompt catalog",
            exc=exc,
        )


@router.post(
    "/catalog/refresh",
    response_model=CatalogResponse,
    summary="Refresh prompt catalog",
    description="Force rebuild of the prompt catalog from source files.",
)
async def refresh_catalog(
    user: Dict[str, Any] = get_auth_dependency(),
    db: Session = Depends(get_db),
):
    """Force refresh of the prompt catalog."""
    try:
        service = get_prompt_catalog()
        service.refresh()
        catalog = _merge_custom_agents_into_catalog(service.catalog, user, db)
        return CatalogResponse(catalog=catalog)
    except Exception as exc:
        raise_sanitized_http_exception(
            logger,
            status_code=500,
            detail="Failed to refresh prompt catalog",
            log_message="Failed to refresh prompt catalog",
            exc=exc,
        )


@router.post(
    "/catalog/combined",
    response_model=CombinedPromptResponse,
    summary="Get combined prompt",
    description="Returns the base prompt with group-specific rules injected.",
)
async def get_combined_prompt(
    request: CombinedPromptRequest,
    user: Dict[str, Any] = get_auth_dependency(),
    db: Session = Depends(get_db),
):
    """Get a combined prompt (base + group rules)."""
    try:
        if request.agent_id.startswith("ca_"):
            bundle, _, _ = _build_custom_agent_effective_prompt_bundle(
                agent_id=request.agent_id,
                group_id=request.group_id,
                user=user,
                db=db,
                lookup_custom_agent=get_custom_agent_visible_to_user,
            )
            return CombinedPromptResponse(
                agent_id=request.agent_id,
                group_id=request.group_id,
                combined_prompt=bundle.render(),
                effective_prompt_hash=bundle.hash,
                layer_manifest=bundle.to_manifest(),
            )

        db_user = set_global_user_from_cognito(db, user)
        if get_agent_by_key(
            db,
            request.agent_id,
            user_id=db_user.id,
            active_group_ids=_authenticated_group_ids(user),
        ) is None:
            raise HTTPException(status_code=404, detail="Agent not found")
        service = get_prompt_catalog()
        bundle = service.get_effective_prompt_bundle(request.agent_id, group_id=request.group_id)
        if bundle is None:
            raise HTTPException(
                status_code=404,
                detail=f"Agent '{request.agent_id}' or group '{request.group_id}' not found"
            )
        return CombinedPromptResponse(
            agent_id=request.agent_id,
            group_id=request.group_id,
            combined_prompt=bundle.render(),
            effective_prompt_hash=bundle.hash,
            layer_manifest=bundle.to_manifest(),
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise_sanitized_http_exception(
            logger,
            status_code=500,
            detail="Failed to get combined prompt",
            log_message="Failed to get combined prompt",
            exc=exc,
        )


def _reject_removed_prompt_preview_query(request: Request) -> None:
    """Fail closed when an old client sends the removed MOD selector."""
    if "mod_id" in request.query_params:
        raise HTTPException(
            status_code=400,
            detail="Unsupported query parameter mod_id. Use group_id.",
        )


@router.get(
    "/prompt-preview/{agent_id}",
    response_model=PromptPreviewResponse,
    summary="Get prompt preview",
    description="Returns the effective prompt text for a system or custom agent.",
)
async def get_prompt_preview(
    agent_id: str = Path(..., description="Agent ID (system ID or custom ca_<uuid>)"),
    group_id: Optional[str] = None,
    _removed_query_guard: None = Depends(_reject_removed_prompt_preview_query),
    user: Dict[str, Any] = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> PromptPreviewResponse:
    """Get prompt preview for system or custom agents."""
    try:
        # Custom agent preview with ownership check
        if agent_id.startswith("ca_"):
            bundle, parent_agent_key, custom_group_rules_enabled = (
                _build_custom_agent_effective_prompt_bundle(
                    agent_id=agent_id,
                    group_id=group_id,
                    user=user,
                    db=db,
                    lookup_custom_agent=get_custom_agent_for_user,
                )
            )

            return PromptPreviewResponse(
                agent_id=agent_id,
                prompt=bundle.render(),
                group_id=group_id,
                source="custom_agent",
                parent_agent_key=parent_agent_key,
                include_group_rules=custom_group_rules_enabled,
                effective_prompt_hash=bundle.hash,
                layer_manifest=bundle.to_manifest(),
            )

        # System agent preview
        db_user = set_global_user_from_cognito(db, user)
        if get_agent_by_key(
            db,
            agent_id,
            user_id=db_user.id,
            active_group_ids=_authenticated_group_ids(user),
        ) is None:
            raise HTTPException(status_code=404, detail="Agent not found")
        service = get_prompt_catalog()
        bundle = service.get_effective_prompt_bundle(agent_id, group_id=group_id)
        if bundle is None:
            raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")

        return PromptPreviewResponse(
            agent_id=agent_id,
            prompt=bundle.render(),
            group_id=group_id,
            source="system_agent",
            parent_agent_key=None,
            include_group_rules=None,
            effective_prompt_hash=bundle.hash,
            layer_manifest=bundle.to_manifest(),
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise_sanitized_http_exception(
            logger,
            status_code=500,
            detail="Failed to get prompt preview",
            log_message=f"Failed to get prompt preview for '{agent_id}'",
            exc=exc,
        )


@router.post(
    "/test-agent/{agent_id}",
    summary="Test an agent in isolation",
    description="Streams events for a single agent execution (system or custom agent).",
)
async def test_agent_endpoint(
    agent_id: str,
    request: AgentTestRequest,
    user: Dict[str, Any] = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """Run a one-off isolated agent test and stream execution events."""
    db_user = set_global_user_from_cognito(db, user)
    authenticated_groups = _authenticated_group_ids(user)

    resolved_agent_id = agent_id
    if agent_id.startswith("ca_"):
        custom_uuid = parse_custom_agent_id(agent_id)
        if not custom_uuid:
            raise HTTPException(status_code=400, detail="Invalid custom agent id")
        try:
            custom_agent = get_custom_agent_for_user(db, custom_uuid, db_user.id)
            if not _agent_record_is_group_accessible(custom_agent, user):
                raise CustomAgentNotFoundError("Custom agent not found")
            resolved_agent_id = make_custom_agent_id(custom_agent.id)
        except (CustomAgentNotFoundError, CustomAgentAccessError) as exc:
            _raise_agent_studio_lookup_http_exception(
                exc=exc,
                log_message=f"Failed to resolve custom agent '{agent_id}' for isolated test execution",
                not_found_detail="Custom agent not found",
                access_denied_detail="Access denied to custom agent",
            )

    try:
        metadata = get_agent_metadata(
            resolved_agent_id,
            db_user_id=db_user.id,
            authenticated_groups=authenticated_groups,
        )
    except ValueError as exc:
        _raise_agent_studio_validation_http_exception(
            exc=exc,
            status_code=404,
            detail="Agent not found",
            log_message=f"Failed to load agent metadata for '{resolved_agent_id}'",
        )

    if metadata.get("requires_document") and not request.document_id:
        raise HTTPException(
            status_code=400,
            detail="This agent requires a document_id for testing",
        )

    user_sub = user.get("sub") or db_user.auth_sub
    if not user_sub:
        raise HTTPException(status_code=401, detail="User identifier not found in token")

    session_id = request.session_id or f"agent-test-{uuid.uuid4()}"
    active_groups = [request.group_id] if request.group_id else []
    set_current_session_id(session_id)
    set_current_user_id(str(user_sub))

    clear_pending_configs()
    try:
        test_agent = get_agent_by_id(
            resolved_agent_id,
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
            detail="Failed to initialize agent",
            log_message=f"Failed to initialize agent '{agent_id}' for isolated test execution",
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
                agent=test_agent,
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
                            "Agent test runner emitted RUN_ERROR for %s: %s",
                            agent_id,
                            raw_message,
                            extra={"session_id": session_id, "trace_id": trace_id or flat.get("trace_id")},
                        )
                    else:
                        logger.error(
                            "Agent test runner emitted RUN_ERROR without message for %s",
                            agent_id,
                            extra={"session_id": session_id, "trace_id": trace_id or flat.get("trace_id")},
                        )
                    flat["message"] = "Agent test failed unexpectedly."
                    details = flat.get("details")
                    if isinstance(details, dict) and "error" in details:
                        flat["details"] = {**details, "error": "Agent test failed unexpectedly."}
                yield f"data: {json.dumps(flat, default=str)}\n\n"

            done_event = {
                "type": "DONE",
                "session_id": session_id,
                "trace_id": trace_id,
            }
            yield f"data: {json.dumps(done_event)}\n\n"
        except asyncio.CancelledError:
            logger.warning('Agent test stream cancelled: agent_id=%s', agent_id)
            error_event = {
                "type": "RUN_ERROR",
                "message": "Agent test cancelled unexpectedly.",
                "error_type": "StreamCancelled",
                "trace_id": trace_id,
                "session_id": session_id,
            }
            yield f"data: {json.dumps(error_event)}\n\n"
        except Exception as exc:
            log_exception(
                logger,
                message=f"Agent test stream error for {agent_id}",
                exc=exc,
            )
            error_event = {
                "type": "RUN_ERROR",
                "message": "Agent test failed unexpectedly.",
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


# ============================================================================
# Agent Studio AI Chat tool surface
# ============================================================================

# Public Agent Studio tool definitions exposed from the focused helper module.
SUGGESTION_TOOL = opus_tools.SUGGESTION_TOOL
REFRESH_WORKSHOP_PROMPT_TOOL = opus_tools.REFRESH_WORKSHOP_PROMPT_TOOL
PROPOSE_WORKSHOP_TOOL = opus_tools.PROPOSE_WORKSHOP_TOOL
REPORT_TOOL_FAILURE_TOOL = opus_tools.REPORT_TOOL_FAILURE_TOOL
CHAT_HISTORY_TOOL_CHAT_KINDS = opus_tools.CHAT_HISTORY_TOOL_CHAT_KINDS
LIST_RECENT_CHATS_TOOL = opus_tools.LIST_RECENT_CHATS_TOOL
SEARCH_CHAT_HISTORY_TOOL = opus_tools.SEARCH_CHAT_HISTORY_TOOL
GET_CHAT_CONVERSATION_TOOL = opus_tools.GET_CHAT_CONVERSATION_TOOL
GET_CHAT_TURN_TOOL = opus_tools.GET_CHAT_TURN_TOOL
SEARCH_TRACES_TOOL = opus_tools.SEARCH_TRACES_TOOL
GET_TRACE_SUMMARY_TOOL = opus_tools.GET_TRACE_SUMMARY_TOOL
GET_TOOL_CALLS_SUMMARY_TOOL = opus_tools.GET_TOOL_CALLS_SUMMARY_TOOL
GET_TOOL_CALLS_PAGE_TOOL = opus_tools.GET_TOOL_CALLS_PAGE_TOOL
GET_TOOL_CALL_DETAIL_TOOL = opus_tools.GET_TOOL_CALL_DETAIL_TOOL
GET_TRACE_CONVERSATION_TOOL = opus_tools.GET_TRACE_CONVERSATION_TOOL
GET_EXTRACTION_DIAGNOSTIC_REPORT_TOOL = opus_tools.GET_EXTRACTION_DIAGNOSTIC_REPORT_TOOL
GET_EXTRACTION_TIMELINE_TOOL = opus_tools.GET_EXTRACTION_TIMELINE_TOOL
GET_EVIDENCE_REVISIONS_TOOL = opus_tools.GET_EVIDENCE_REVISIONS_TOOL
GET_TRACE_TREE_TOOL = opus_tools.GET_TRACE_TREE_TOOL
GET_TRACE_RECONSTRUCTION_TOOL = opus_tools.GET_TRACE_RECONSTRUCTION_TOOL
GET_TRACE_PAYLOADS_TOOL = opus_tools.GET_TRACE_PAYLOADS_TOOL
GET_TRACE_PAYLOAD_TOOL = opus_tools.GET_TRACE_PAYLOAD_TOOL
GET_TRACE_COSTS_TOOL = opus_tools.GET_TRACE_COSTS_TOOL
GET_TRACE_DUPLICATES_TOOL = opus_tools.GET_TRACE_DUPLICATES_TOOL
GET_TRACE_VIEW_TOOL = opus_tools.GET_TRACE_VIEW_TOOL
GET_SERVICE_LOGS_TOOL = opus_tools.GET_SERVICE_LOGS_TOOL
LIST_DOMAIN_ENVELOPES_TOOL = opus_tools.LIST_DOMAIN_ENVELOPES_TOOL
GET_DOMAIN_ENVELOPE_STATE_TOOL = opus_tools.GET_DOMAIN_ENVELOPE_STATE_TOOL
GET_DOMAIN_PACK_VALIDATION_PLAN_TOOL = opus_tools.GET_DOMAIN_PACK_VALIDATION_PLAN_TOOL
GET_DOMAIN_ENVELOPE_REVIEW_ROWS_TOOL = opus_tools.GET_DOMAIN_ENVELOPE_REVIEW_ROWS_TOOL
GET_EXPORT_SUBMISSION_READINESS_TOOL = opus_tools.GET_EXPORT_SUBMISSION_READINESS_TOOL
_COMMON_TOOLS = opus_tools.COMMON_TOOLS
_DOMAIN_ENVELOPE_TOOLS = opus_tools.DOMAIN_ENVELOPE_TOOLS
_WORKSHOP_TOOLS = opus_tools.WORKSHOP_TOOLS
_TRACE_TOOLS = opus_tools.TRACE_TOOLS
_FLOW_TOOLS = opus_tools.FLOW_TOOLS
_AGENTS_ONLY_DIAGNOSTIC_TOOLS = opus_tools.AGENTS_ONLY_DIAGNOSTIC_TOOLS
_CAPABILITY_CATALOG_TOOLS = opus_tools.CAPABILITY_CATALOG_TOOLS


def _get_active_tab(context: Optional[ChatContext]) -> str:
    """Resolve active tab from chat context with a safe default."""
    return opus_tools.get_active_tab(context)


def _ensure_flow_tools_registered(registry: Any) -> None:
    """Ensure flow tools are present even if the diagnostic registry was reset."""
    return opus_tools.ensure_flow_tools_registered(registry, logger=logger)


def _is_tool_allowed_for_context(tool_name: str, context: Optional[ChatContext]) -> bool:
    """Check whether a tool is allowed for the current tab/context."""
    return opus_tools.is_tool_allowed_for_context(tool_name, context)


def _tool_scope_error(tool_name: str, context: Optional[ChatContext]) -> Dict[str, Any]:
    """Build a curator-friendly error for disallowed tool usage."""
    return opus_tools.tool_scope_error(tool_name, context)


def _get_all_opus_tools(context: Optional[ChatContext] = None) -> List[dict]:
    """Get all tools available to the Agent Studio assistant."""
    return opus_tools.get_all_opus_tools(
        context,
        diagnostic_registry_factory=get_diagnostic_tools_registry,
        ensure_registered=_ensure_flow_tools_registered,
        logger=logger,
        is_allowed=_is_tool_allowed_for_context,
    )


def _agent_studio_tool_namespace(tool_name: str) -> tuple[str, str]:
    """Place authorized tools in small, purpose-specific search namespaces."""

    if tool_name in _COMMON_TOOLS:
        return "studio_history", "Conversation recall, feedback, and failure reporting"
    if tool_name in _CAPABILITY_CATALOG_TOOLS:
        return "studio_capabilities", "Live authenticated Agent Studio resource discovery"
    if tool_name in _DOMAIN_ENVELOPE_TOOLS:
        return "domain_review", "Domain envelope, validation, and export-readiness inspection"
    if tool_name in _WORKSHOP_TOOLS:
        return "workshop_authoring", "Workshop inspection and curator-reviewed complete agent proposals"
    if tool_name in _FLOW_TOOLS:
        if tool_name in {
            "propose_flow_draft_update",
            "validate_flow",
            "get_flow_templates",
        }:
            return (
                "flow_authoring",
                "Curator-reviewed flow proposals, templates, and validation",
            )
        return "flow_inspection", "Focused inspection of the active flow draft"
    if tool_name in _TRACE_TOOLS:
        if tool_name in {
            "search_traces",
            "get_trace_summary",
            "get_trace_conversation",
            "get_trace_costs",
        }:
            return "trace_overview", "Trace discovery, summaries, conversation, and cost"
        if tool_name.startswith("get_tool_call") or tool_name == "get_service_logs":
            return "trace_tools", "Tool-call and service-log diagnostics"
        if tool_name in {
            "get_trace_payloads",
            "get_trace_payload",
            "get_trace_reconstruction",
            "get_trace_model_live_context",
        }:
            return "trace_payload", "Exact trace payload and reconstruction inspection"
        return "trace_evidence", "Detailed extraction trace, payload, and evidence reconstruction"
    if tool_name in _AGENTS_ONLY_DIAGNOSTIC_TOOLS:
        return "source_diagnostics", "Bounded source-code diagnostics for Agent Studio"
    if tool_name in opus_tools.TOOL_METADATA_TOOLS or tool_name == "get_prompt":
        return "agent_catalog", "Agent prompt and callable-tool catalog inspection"
    return "package_diagnostics", "Authenticated package-provided diagnostic capabilities"


def _get_openai_authorized_tool_definitions(
    context: Optional[ChatContext],
    *,
    user_id: int,
    active_group_ids: List[str],
) -> AuthorizedToolUniverse:
    """Return the request-local, context-authorized hosted-search universe."""

    definitions = _get_all_opus_tools(context)
    db = SessionLocal()
    try:
        try:
            return compile_authorized_tool_universe(
                db=db,
                definitions=definitions,
                user_id=user_id,
                active_group_ids=active_group_ids,
            )
        except ToolSearchAuthorizationError:
            raise
        except Exception as exc:
            raise ToolSearchAuthorizationError(
                "Agent Studio callable authorization source is unavailable",
                candidate_count=len(definitions),
                bound=len(definitions),
            ) from exc
    finally:
        db.close()


def _report_agent_studio_exception_once(
    exc: Exception,
    *,
    operation: str,
    phase: str,
    context: Optional[Dict[str, Any]] = None,
) -> bool:
    """Capture one sanitized Sentry event even when an SDK layer re-raises it."""

    marker = "_agent_studio_sentry_reported"
    if getattr(exc, marker, False):
        return False
    reported = report_runtime_exception(
        exc,
        component="agent_studio",
        operation=operation,
        tags={"phase": phase, "provider": "openai"},
        context=context or {"model": AGENT_STUDIO_OPENAI_MODEL},
    )
    try:
        setattr(exc, marker, True)
    except Exception:
        pass
    return reported


def _format_conversation_context(messages: Optional[List[dict]]) -> Optional[str]:
    """Format the entire conversation history as a readable string."""
    return prompt_builder.format_conversation_context(messages)


def _parse_markdown_heading(line: str) -> Optional[Dict[str, Any]]:
    """Parse a markdown heading line into level/text metadata."""
    return prompt_builder.parse_markdown_heading(line)


def _find_section_bounds(prompt: str, section_heading: str) -> Optional[Dict[str, Any]]:
    """Find byte-range bounds for a markdown section by heading text."""
    return prompt_builder.find_section_bounds(prompt, section_heading)


def _apply_targeted_workshop_edits(
    base_prompt: str,
    edits: List[Any],
) -> Dict[str, Any]:
    """Apply targeted edit operations against a workshop prompt draft."""
    return prompt_builder.apply_targeted_workshop_edits(base_prompt, edits)


def _parse_optional_datetime(value: Any) -> datetime | None:
    """Parse an optional ISO-ish timestamp."""

    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        logger.warning("Unparseable Agent Workshop datetime value: %r", value)
        raise


def _is_newer_datetime(left: datetime | None, right: datetime | None) -> bool:
    """Return whether left is newer than right, normalizing naive datetimes."""

    if left is None or right is None:
        return False
    left_cmp = left if left.tzinfo else left.replace(tzinfo=timezone.utc)
    right_cmp = right if right.tzinfo else right.replace(tzinfo=timezone.utc)
    return left_cmp > right_cmp


def _parse_workshop_custom_agent_uuid(raw_agent_id: Any) -> uuid.UUID | None:
    """Parse either `ca_<uuid>` or raw UUID custom-agent identifiers."""

    if not isinstance(raw_agent_id, str) or not raw_agent_id.strip():
        return None
    parsed = parse_custom_agent_id(raw_agent_id.strip())
    if parsed:
        return parsed
    try:
        return uuid.UUID(raw_agent_id.strip())
    except ValueError:
        return None


def _prompt_hash(prompt: str) -> str:
    """Return a stable content hash for refreshed prompt metadata."""

    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


# Short strings under these keys are persisted verbatim in tool-call audit
# summaries because they are operational identifiers, selectors, or statuses
# that support SQL debugging queries without exposing prompt text or
# credentials. When adding a new tool-call argument/result field that should be
# searchable in persisted audit metadata, add it here only if the value is
# non-sensitive by contract, then update the audit summarization tests.
_AUDIT_SAFE_VALUE_KEYS = {
    "agent_id",
    "apply_mode",
    "blocker_id",
    "call_id",
    "candidate_id",
    "chat_kind",
    "code",
    "custom_agent_id",
    "document_id",
    "domain_pack_id",
    "envelope_id",
    "envelope_revision",
    "event_id",
    "field_path",
    "finding_id",
    "flow_id",
    "flow_run_id",
    "node_id",
    "object_id",
    "pending_ref_id",
    "projection_key",
    "projection_status",
    "projection_type",
    "runtime_agent_id",
    "severity",
    "session_id",
    "source",
    "status",
    "success",
    "target_group_id",
    # Refresh-tool selector, not prompt content.
    "target_prompt",
    "tool_name",
    "trace_id",
    "validator_binding_id",
    "validator_id",
    "view_name",
}

_DOMAIN_REFERENCE_KEYS = {
    "blocker_id",
    "candidate_id",
    "code",
    "document_id",
    "domain_pack_id",
    "envelope_id",
    "envelope_revision",
    "event_id",
    "field_path",
    "finding_id",
    "flow_id",
    "flow_run_id",
    "node_id",
    "object_id",
    "pending_ref_id",
    "projection_key",
    "projection_status",
    "projection_type",
    "session_id",
    "status",
    "validator_binding_id",
    "validator_id",
}
_DOMAIN_REFERENCE_TOOL_NAMES = {
    "get_current_flow",
    "get_domain_envelope_review_rows",
    "get_domain_envelope_state",
    "get_domain_pack_validation_plan",
    "get_export_submission_readiness",
    "list_domain_envelopes",
}
# Env-configurable via DOMAIN_REFERENCE_MAX_VALUES (default 50); see config.py.
_DOMAIN_REFERENCE_MAX_VALUES = get_domain_reference_max_values()


def _audit_text_summary(value: str) -> Dict[str, Any]:
    """Return a compact non-reversible string summary for durable audit metadata."""

    return {
        "type": "string",
        "length": len(value),
        "sha256": _prompt_hash(value),
    }


def _summarize_audit_value(value: Any, *, key: str | None = None, depth: int = 0) -> Any:
    """Summarize JSON-ish data without storing raw prompts, credentials, or large values."""

    normalized_key = key.lower() if isinstance(key, str) else None
    if value is None:
        return {"type": "null"}
    if isinstance(value, bool):
        return {"type": "boolean", "value": value}
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return {"type": "number", "value": value}
    if isinstance(value, str):
        if normalized_key in _AUDIT_SAFE_VALUE_KEYS and len(value) <= 255:
            return {"type": "string", "value": value, "length": len(value)}
        return _audit_text_summary(value)
    if isinstance(value, list):
        summary: Dict[str, Any] = {"type": "array", "length": len(value)}
        if depth < 2:
            summary["items"] = [
                _summarize_audit_value(item, depth=depth + 1)
                for item in value[:5]
            ]
            if len(value) > 5:
                summary["truncated"] = True
        return summary
    if isinstance(value, dict):
        keys = sorted(str(item_key) for item_key in value.keys())
        summary = {
            "type": "object",
            "keys": keys,
            "key_count": len(keys),
        }
        if depth < 2:
            summary["fields"] = {
                str(item_key): _summarize_audit_value(
                    item_value,
                    key=str(item_key),
                    depth=depth + 1,
                )
                for item_key, item_value in value.items()
            }
        return summary

    return {
        "type": type(value).__name__,
        "repr_sha256": _prompt_hash(str(value)),
    }


def _collect_domain_reference_values(
    value: Any,
    references: Dict[str, set[str]],
    *,
    key: str | None = None,
    depth: int = 0,
) -> None:
    """Collect stable domain-envelope reference values from bounded tool output."""

    if depth > 6:
        return

    normalized_key = key.lower() if isinstance(key, str) else None
    if normalized_key in _DOMAIN_REFERENCE_KEYS:
        if isinstance(value, (str, int)) and not isinstance(value, bool):
            normalized_value = str(value).strip()
            if normalized_value and len(normalized_value) <= 255:
                references.setdefault(normalized_key, set()).add(normalized_value)
        return

    if isinstance(value, dict):
        for item_key, item_value in value.items():
            _collect_domain_reference_values(
                item_value,
                references,
                key=str(item_key),
                depth=depth + 1,
            )
        return

    if isinstance(value, list):
        for item in value[:100]:
            _collect_domain_reference_values(
                item,
                references,
                depth=depth + 1,
            )


def _domain_references_from_tool_result(
    tool_name: str,
    tool_result: Any,
) -> Dict[str, Any] | None:
    """Return durable reference metadata worth carrying into follow-up turns."""

    if tool_name not in _DOMAIN_REFERENCE_TOOL_NAMES:
        return None

    references: Dict[str, set[str]] = {}
    _collect_domain_reference_values(tool_result, references)
    if not references:
        return None

    return {
        "tool_name": tool_name,
        "references": {
            key: sorted(values)[:_DOMAIN_REFERENCE_MAX_VALUES]
            for key, values in sorted(references.items())
            if values
        },
    }


def _merge_domain_reference_events(
    events: List[Dict[str, Any]],
) -> Dict[str, Any] | None:
    """Merge per-tool reference events into compact durable chat context."""

    if not events:
        return None

    tool_names: List[str] = []
    merged: Dict[str, set[str]] = {}
    for event in events:
        tool_name = _normalize_optional_text(event.get("tool_name"))
        if tool_name and tool_name not in tool_names:
            tool_names.append(tool_name)
        raw_references = event.get("references")
        if not isinstance(raw_references, dict):
            continue
        for key, values in raw_references.items():
            if key not in _DOMAIN_REFERENCE_KEYS or not isinstance(values, list):
                continue
            target = merged.setdefault(key, set())
            for value in values:
                if len(target) >= _DOMAIN_REFERENCE_MAX_VALUES:
                    break
                normalized_value = _normalize_optional_text(value)
                if normalized_value and len(normalized_value) <= 255:
                    target.add(normalized_value)

    if not merged:
        return None

    return {
        "tool_names": tool_names,
        "references": {
            key: sorted(values)[:_DOMAIN_REFERENCE_MAX_VALUES]
            for key, values in sorted(merged.items())
            if values
        },
    }


def _prompt_digest_summary(prompt: Any) -> Dict[str, Any]:
    """Summarize prompt text without retaining the prompt itself."""

    if isinstance(prompt, str):
        return {
            "provided": True,
            "length": len(prompt),
            "sha256": _prompt_hash(prompt),
        }
    return {
        "provided": False,
        "length": None,
        "sha256": None,
    }


def _trace_capture_snapshot(trace_id: str | None) -> Dict[str, Any]:
    """Describe whether this Agent Studio turn has a durable trace link."""

    normalized_trace_id = _normalize_optional_text(trace_id)
    if normalized_trace_id:
        return {
            "status": "provided_context_trace_id",
            "trace_id": normalized_trace_id,
            "error": None,
        }
    return {
        "status": "capture_unavailable",
        "trace_id": None,
        "error": "Agent Studio AI Chat does not currently create a Langfuse trace.",
    }


def _tool_result_status(tool_result: Any) -> str:
    """Normalize heterogeneous tool results into a compact audit status."""

    if not isinstance(tool_result, dict):
        return "success"
    if tool_result.get("success") is False:
        return "error"
    raw_status = tool_result.get("status")
    if isinstance(raw_status, str) and raw_status.strip():
        normalized_status = raw_status.strip().lower()
        if normalized_status in {"error", "failed", "failure"}:
            return "error"
        return normalized_status
    if tool_result.get("error"):
        return "error"
    if tool_result.get("pending_user_approval") is True:
        return "pending_user_approval"
    return "success"


def _tool_result_error(tool_result: Any) -> str | None:
    if not isinstance(tool_result, dict):
        return None
    raw_error = tool_result.get("error")
    if isinstance(raw_error, str) and raw_error.strip():
        return raw_error.strip()[:500]
    return None


def _is_backend_scope_block(tool_name: str, context: Optional[ChatContext], tool_result: Any) -> bool:
    if not _is_tool_allowed_for_context(tool_name, context):
        return True
    error_text = _tool_result_error(tool_result) or ""
    return (
        "only available while the curator is on" in error_text
        or "is not available on the" in error_text
    )


def _tool_call_audit_entry(
    *,
    tool_name: str,
    tool_use_id: Any,
    tool_input: Any,
    tool_result: Any,
    context: Optional[ChatContext],
) -> Dict[str, Any]:
    """Build one durable tool-call audit record without raw arguments/results."""

    result_status = _tool_result_status(tool_result)
    result_error = _tool_result_error(tool_result)
    return {
        "tool_name": tool_name,
        "tool_use_id": str(tool_use_id) if tool_use_id is not None else None,
        "requested": True,
        # _AUDIT_SAFE_VALUE_KEYS is the only place that permits raw short
        # strings inside these summaries; add safe searchable fields there
        # when introducing new audited tool arguments or result metadata.
        "argument_summary": _summarize_audit_value(tool_input),
        "result_status": result_status,
        "result_error": result_error,
        "result_type": type(tool_result).__name__,
        "backend_blocked_tool_scope": _is_backend_scope_block(
            tool_name,
            context,
            tool_result,
        ),
        "result_summary": _summarize_audit_value(tool_result),
    }


def _summarize_provider_tool_result_value(
    value: Any,
    *,
    depth: int = 0,
) -> Any:
    """Return a compact structural summary safe for provider continuation."""

    return _summarize_audit_value(value, depth=depth)


def _collect_provider_payload_refs(
    value: Any,
    refs: set[str],
    *,
    key: str | None = None,
) -> None:
    if len(refs) >= _DOMAIN_REFERENCE_MAX_VALUES:
        return
    if key == "payload_id" and isinstance(value, str) and value.strip():
        refs.add(value.strip())
        return
    if isinstance(value, dict):
        for item_key, item_value in value.items():
            _collect_provider_payload_refs(
                item_value,
                refs,
                key=str(item_key),
            )
            if len(refs) >= _DOMAIN_REFERENCE_MAX_VALUES:
                return
        return
    if isinstance(value, list):
        for item in value:
            _collect_provider_payload_refs(item, refs)
            if len(refs) >= _DOMAIN_REFERENCE_MAX_VALUES:
                return




def _provider_content_identity(serialized: str) -> Dict[str, Any]:
    """Return stable identity for provider content omitted by compaction."""

    return {
        "json_chars": len(serialized),
        "sha256": hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
    }


def _provider_tool_input_selectors(tool_input: Any) -> Dict[str, Any]:
    """Keep shallow scalar selectors that can support a narrower repeat call."""

    safe_input = _json_safe(tool_input)
    if not isinstance(safe_input, dict):
        return {}
    return {
        str(key): value
        for key, value in sorted(safe_input.items(), key=lambda item: str(item[0]))
        if value is None or isinstance(value, (bool, int, float, str))
    }


def _provider_cap_error_content(inline_max_chars: int) -> str:
    """Return the smallest explicit JSON error that fits an unusable cap."""

    error_payloads: tuple[Any, ...] = (
        {
            "error": "provider_tool_result_cap_too_small",
            "configured_max_chars": inline_max_chars,
        },
        "provider_tool_result_cap_too_small",
        "provider_cap_too_small",
    )
    for payload in error_payloads:
        serialized = _serialize_provider_tool_result(payload)
        if len(serialized) <= inline_max_chars:
            return serialized
    raise ValueError(
        "AGENT_STUDIO_PROVIDER_TOOL_RESULT_INLINE_MAX_CHARS is too small to "
        "hold a provider-safe JSON error"
    )


def _fit_provider_compact_payload(
    *,
    tool_name: str,
    tool_input: Any,
    tool_result: Any,
    session_id: str,
    turn_id: str,
    inline_max_chars: int,
) -> str:
    """Fit one complete compact envelope using deterministic degradation."""

    safe_input = _json_safe(tool_input)
    input_content = _serialize_provider_tool_result(safe_input)
    result_content = _serialize_provider_tool_result(tool_result)
    payload: Dict[str, Any] = {
        "status": "compacted_tool_result",
        "tool": {
            "name": tool_name,
            "input": _provider_content_identity(input_content),
            "result": _provider_content_identity(result_content),
        },
        "recall": {
            "turn": {
                "tool": "get_chat_turn",
                "session_id": session_id,
                "turn_id": turn_id,
            },
        },
    }

    def serialize_if_fits(candidate: Dict[str, Any]) -> str | None:
        serialized = _serialize_provider_tool_result(candidate)
        return serialized if len(serialized) <= inline_max_chars else None

    if serialize_if_fits(payload) is None:
        return _provider_cap_error_content(inline_max_chars)

    if tool_name in {"propose_workshop_draft_update", "propose_flow_draft_update"}:
        payload["recall"]["retained_proposal_input"] = {
            "next_tool": "refresh_workshop_prompt" if tool_name == "propose_workshop_draft_update" else "get_current_flow",
        }
        if serialize_if_fits(payload) is None:
            payload["recall"].pop("retained_proposal_input")
    else:
        exact_call = {"tool": tool_name, "input": safe_input}
        payload["recall"]["next_call"] = exact_call
        if serialize_if_fits(payload) is None:
            payload["recall"].pop("next_call")
            selectors = _provider_tool_input_selectors(safe_input)
            required_inputs = opus_tools.get_builtin_tool_required_inputs(tool_name)
            projected_input: Dict[str, Any] = {}
            narrowing: Dict[str, Any] = {
                "tool": tool_name,
                "input": projected_input,
            }

            def omitted_input_fields() -> list[str]:
                if not isinstance(safe_input, dict):
                    return []
                omitted_required = (
                    [
                        field
                        for field in required_inputs
                        if field not in projected_input
                    ]
                    if required_inputs is not None
                    else []
                )
                omitted_other = sorted(
                    str(field)
                    for field in safe_input
                    if str(field) not in projected_input
                    and str(field) not in omitted_required
                )
                return omitted_required + omitted_other

            def update_narrowing_guidance() -> None:
                omitted = omitted_input_fields()
                if omitted:
                    narrowing["supply_bounded"] = omitted
                else:
                    narrowing.pop("supply_bounded", None)

            update_narrowing_guidance()
            payload["recall"]["narrow"] = narrowing
            if serialize_if_fits(payload) is None:
                payload["recall"].pop("narrow")
            else:

                def selector_priority(item: tuple[str, Any]) -> tuple[int, int, int, str]:
                    key, value = item
                    required_priority = (
                        0
                        if required_inputs is not None and key in required_inputs
                        else 1
                    )
                    if key == "payload_id":
                        identity_priority = 0
                    elif key == "trace_id":
                        identity_priority = 1
                    elif key.endswith("_id"):
                        identity_priority = 2
                    else:
                        identity_priority = 3
                    return (
                        required_priority,
                        identity_priority,
                        len(_serialize_provider_tool_result(value)),
                        key,
                    )

                for key, value in sorted(selectors.items(), key=selector_priority):
                    projected_input[key] = value
                    update_narrowing_guidance()
                    if serialize_if_fits(payload) is None:
                        projected_input.pop(key)
                        update_narrowing_guidance()

                if not omitted_input_fields():
                    payload["recall"].pop("narrow")
                    payload["recall"]["next_call"] = {
                        "tool": tool_name,
                        "input": projected_input,
                    }
                    if serialize_if_fits(payload) is None:
                        payload["recall"].pop("next_call")
                        payload["recall"]["narrow"] = narrowing

    payload_refs: set[str] = set()
    _collect_provider_payload_refs(tool_result, payload_refs)
    if payload_refs:
        payload_recall = {
            "tool": "get_trace_payload",
            "payload_ids": [],
        }
        payload["recall"]["trace_payloads"] = payload_recall
        for payload_id in sorted(payload_refs):
            payload_recall["payload_ids"].append(payload_id)
            if serialize_if_fits(payload) is None:
                payload_recall["payload_ids"].pop()
                break
        if not payload_recall["payload_ids"]:
            payload["recall"].pop("trace_payloads")

    optional_fields = (
        (
            "summary",
            _summarize_provider_tool_result_value(_json_safe(tool_result)),
        ),
        (
            "instruction",
            "Use the exact next call, bounded narrowing guidance, or durable turn recall before relying on omitted details.",
        ),
    )
    for key, value in optional_fields:
        payload[key] = value
        if serialize_if_fits(payload) is None:
            payload.pop(key)

    serialized = _serialize_provider_tool_result(payload)
    if len(serialized) > inline_max_chars:
        raise AssertionError("provider compact payload exceeded its configured cap")
    return serialized


def _provider_tool_result_content(
    *,
    tool_name: str,
    tool_input: Any,
    tool_result: Any,
    session_id: str,
    turn_id: str,
) -> str:
    """Serialize a bounded tool result for provider continuation only."""

    provider_tool_result = tool_result
    if (
        tool_name in {"propose_flow_draft_update", "propose_workshop_draft_update"}
        and isinstance(tool_result, dict)
        and tool_result.get("contract_version") in {"flow_authoring_proposal.v1", "workshop_authoring_proposal.v1"}
    ):
        # The full candidate and exact diff are transient UI state. Keep them out
        # of provider continuation and durable conversation records.
        provider_tool_result = {
            "contract_version": tool_result["contract_version"].replace("proposal.v1", "proposal_ack.v1"),
            "success": tool_result.get("success") is True,
            "valid": tool_result.get("valid") is True,
            "pending_user_approval": tool_result.get("pending_user_approval") is True,
            "approval_status": tool_result.get("approval_status"),
            "base_draft_fingerprint": tool_result.get("base_draft_fingerprint"),
            "candidate_draft_fingerprint": tool_result.get(
                "candidate_draft_fingerprint"
            ),
            "change_summary": tool_result.get("change_summary"),
            "finding_count": len(tool_result.get("findings") or []),
            "findings": tool_result.get("findings") or [],
            "diff_count": len(tool_result.get("diff") or []),
            "message": tool_result.get("message"),
            "instruction": (
                "Tell the curator the proposal is ready for review; do not claim it was applied or saved."
                if tool_result.get("valid") is True
                else "Repair the listed findings with another semantic proposal call; the request-local candidate is retained."
            ),
        }

    raw_content = _serialize_provider_tool_result(provider_tool_result)
    inline_max_chars = get_agent_studio_provider_tool_result_inline_max_chars()
    if len(raw_content) <= inline_max_chars:
        return raw_content

    return _fit_provider_compact_payload(
        tool_name=tool_name,
        tool_input=tool_input,
        tool_result=_json_safe(tool_result),
        session_id=session_id,
        turn_id=turn_id,
        inline_max_chars=inline_max_chars,
    )


def _serialize_provider_tool_result(tool_result: Any) -> str:
    """Serialize tool results exactly as the provider continuation will receive them."""

    return json.dumps(
        tool_result,
        default=str,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _resolve_saved_workshop_agent(
    *,
    db: Session,
    workshop: Any,
    user_db_id: int | None,
) -> tuple[uuid.UUID | None, UnifiedAgent | None, str | None]:
    custom_agent_uuid = _parse_workshop_custom_agent_uuid(workshop.custom_agent_id)
    if custom_agent_uuid is None or user_db_id is None:
        return custom_agent_uuid, None, None
    try:
        return (
            custom_agent_uuid,
            get_custom_agent_visible_to_user(db, custom_agent_uuid, user_db_id),
            None,
        )
    except (CustomAgentAccessError, CustomAgentNotFoundError) as exc:
        logger.warning(
            "Could not resolve Agent Workshop custom agent for debug snapshot",
            extra={"custom_agent_id": str(custom_agent_uuid)},
        )
        return custom_agent_uuid, None, type(exc).__name__


def _build_workshop_prompt_context_summary(
    *,
    db: Session,
    workshop: Any,
    user_db_id: int | None,
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    """Build redacted Agent Workshop prompt freshness/debug metadata."""

    custom_agent_uuid, saved_custom_agent, lookup_error = _resolve_saved_workshop_agent(
        db=db,
        workshop=workshop,
        user_db_id=user_db_id,
    )
    selected_group_id = (workshop.selected_group_id or "").strip().upper() or None
    frontend_main = _prompt_digest_summary(workshop.prompt_draft)
    frontend_group = _prompt_digest_summary(workshop.selected_group_prompt_draft)

    saved_main: Dict[str, Any] | None = None
    saved_group: Dict[str, Any] | None = None
    if saved_custom_agent is not None:
        saved_main = _prompt_digest_summary(saved_custom_agent.instructions or "")
        if selected_group_id:
            saved_group_prompt = get_custom_agent_group_prompt(
                parent_agent_key=saved_custom_agent.template_source or "",
                group_id=selected_group_id,
                group_prompt_overrides=saved_custom_agent.group_prompt_overrides,
            )
            saved_group = _prompt_digest_summary(saved_group_prompt)

    frontend_matches_saved = None
    if saved_main is not None and frontend_main["provided"]:
        main_matches = frontend_main["sha256"] == saved_main["sha256"]
        group_matches = True
        if selected_group_id and frontend_group["provided"] and saved_group is not None:
            group_matches = frontend_group["sha256"] == saved_group["sha256"]
        frontend_matches_saved = main_matches and group_matches

    if bool(workshop.draft_is_dirty) and (frontend_main["provided"] or frontend_group["provided"]):
        context_source = "frontend_draft"
    elif saved_custom_agent is not None:
        context_source = "saved_custom_agent"
    elif frontend_main["provided"] or frontend_group["provided"]:
        context_source = "frontend_draft"
    else:
        context_source = "unavailable"

    saved_updated_at = (
        saved_custom_agent.updated_at.isoformat()
        if saved_custom_agent is not None and isinstance(saved_custom_agent.updated_at, datetime)
        else None
    )
    saved_version = (
        int(saved_custom_agent.version)
        if saved_custom_agent is not None and saved_custom_agent.version is not None
        else None
    )
    saved_debug = {
        "custom_agent_id": str(custom_agent_uuid) if custom_agent_uuid else None,
        "runtime_agent_id": make_custom_agent_id(custom_agent_uuid) if custom_agent_uuid else None,
        "version": saved_version,
        "updated_at": saved_updated_at,
        "lookup_error": lookup_error,
    }
    prompt_summary = {
        "context_source": context_source,
        "frontend_draft_matches_saved_db": frontend_matches_saved,
        "selected_group_id": selected_group_id,
        "frontend_draft": {
            "main_prompt": frontend_main,
            "selected_group_prompt": frontend_group,
            "custom_agent_updated_at": workshop.custom_agent_updated_at,
            "draft_is_dirty": workshop.draft_is_dirty,
        },
        "saved_db_prompt": {
            **saved_debug,
            "main_prompt": saved_main,
            "selected_group_prompt": saved_group,
        },
    }
    return prompt_summary, saved_debug


def _build_agent_studio_user_debug_payload(
    *,
    db: Session,
    request: ChatRequest,
    prepared_turn: "PreparedAgentStudioTurn",
    user_db_id: int | None,
) -> Dict[str, Any]:
    """Build compact per-turn debug metadata for the durable user row."""

    context = request.context
    trace_id = context.trace_id if context else None
    payload: Dict[str, Any] = {
        "debug_context": {
            "session_id": prepared_turn.session_id,
            "turn_id": prepared_turn.turn_id,
            "requested_context_session_id": prepared_turn.requested_context_session_id,
            "active_tab": context.active_tab if context else None,
            "selected_agent_id": context.selected_agent_id if context else None,
            "selected_group_id": context.selected_group_id if context else None,
            "view_mode": context.view_mode if context else None,
        },
        "trace_capture": _trace_capture_snapshot(trace_id),
    }
    if context and context.flow_definition:
        payload["debug_context"]["flow_authoring"] = {
            "flow_id": context.flow_id,
            "baseline_updated_at": context.flow_updated_at,
            "draft_is_dirty": context.flow_is_dirty,
            "draft_fingerprint": context.flow_draft_fingerprint,
            "node_count": len(context.flow_definition.nodes),
            "edge_count": len(context.flow_definition.edges),
        }
    if context and context.agent_workshop:
        prompt_summary, saved_debug = _build_workshop_prompt_context_summary(
            db=db,
            workshop=context.agent_workshop,
            user_db_id=user_db_id,
        )
        workshop = context.agent_workshop
        payload["debug_context"]["agent_workshop"] = {
            "template_source": workshop.template_source,
            "template_name": workshop.template_name,
            "custom_agent_id": workshop.custom_agent_id,
            "custom_agent_name": workshop.custom_agent_name,
            "selected_group_id": workshop.selected_group_id,
            "include_group_rules": workshop.include_group_rules,
            "draft_is_dirty": workshop.draft_is_dirty,
            "draft_fingerprint": workshop.draft_fingerprint,
            "group_prompt_override_count": workshop.group_prompt_override_count,
            "has_group_prompt_overrides": workshop.has_group_prompt_overrides,
            "draft_tool_count": (
                len(workshop.draft_tool_ids)
                if isinstance(workshop.draft_tool_ids, list)
                else None
            ),
            "draft_model_id": workshop.draft_model_id,
            "draft_model_reasoning": workshop.draft_model_reasoning,
            "saved_custom_agent": saved_debug,
        }
        payload["agent_workshop_prompt_context"] = prompt_summary
    return _json_safe(payload)


def _persist_agent_studio_user_debug_payload(
    *,
    db: Session,
    user_id: str,
    prepared_turn: "PreparedAgentStudioTurn",
    trace_id: str | None,
    payload_json: Dict[str, Any],
) -> ChatMessageRecord:
    repository = ChatHistoryRepository(db)
    return repository.update_message_by_turn_id(
        session_id=prepared_turn.session_id,
        user_auth_sub=user_id,
        turn_id=prepared_turn.turn_id,
        role="user",
        payload_json=payload_json,
        trace_id=trace_id,
    )


def _resolve_refresh_target(
    target_prompt: str,
    tool_input: dict,
    context: Optional[ChatContext],
) -> tuple[str, str]:
    """Resolve which workshop prompt the refresh tool should return."""

    target_group_id = ""
    if target_prompt == "group" and context and context.agent_workshop:
        raw_group_id = tool_input.get("target_group_id")
        if isinstance(raw_group_id, str) and raw_group_id.strip():
            target_group_id = raw_group_id.strip().upper()
        else:
            target_group_id = (context.agent_workshop.selected_group_id or "").strip().upper()

    return target_prompt, target_group_id


def _build_refresh_workshop_prompt_result(
    *,
    tool_input: dict,
    context: Optional[ChatContext],
    user_db_id: int | None,
) -> Dict[str, Any]:
    """Return Workshop prompt identity first, then stable exact chunks on request."""

    if not context or context.active_tab != "agent_workshop" or not context.agent_workshop:
        return {
            "success": False,
            "error": "This tool is only available while the curator is on the Agent Workshop tab.",
        }

    workshop = context.agent_workshop
    target_prompt = str(tool_input.get("target_prompt", "main")).strip().lower()
    if target_prompt not in {"main", "group", "metadata"}:
        return {
            "success": False,
            "error": (
                f"Invalid target_prompt: {target_prompt!r}. "
                "Must be 'main', 'group', or 'metadata'."
            ),
        }
    target_prompt, target_group_id = _resolve_refresh_target(
        target_prompt,
        tool_input,
        context,
    )
    if target_prompt == "group":
        if not target_group_id:
            return {
                "success": False,
                "error": "No Agent Workshop group is selected for a group prompt refresh.",
            }
        selected_group_id = (workshop.selected_group_id or "").strip().upper()
        available_overrides = {
            str(group_id).strip().upper(): prompt
            for group_id, prompt in (workshop.group_prompt_overrides or {}).items()
            if str(group_id).strip()
        }
        if target_group_id != selected_group_id and target_group_id not in available_overrides:
            return {
                "success": False,
                "error": (
                    f"Agent Workshop has no editable group prompt for {target_group_id}."
                ),
            }
    if target_prompt == "metadata":
        context_prompt = workshop_authoring_metadata_json(workshop)
    elif target_prompt == "group":
        context_prompt = (
            workshop.selected_group_prompt_draft
            if target_group_id == (workshop.selected_group_id or "").strip().upper()
            else available_overrides.get(target_group_id)
        ) or ""
    else:
        context_prompt = workshop.prompt_draft or ""

    saved_prompt: str | None = None
    saved_custom_agent: UnifiedAgent | None = None
    saved_updated_at: datetime | None = None
    custom_agent_uuid = _parse_workshop_custom_agent_uuid(workshop.custom_agent_id)

    if target_prompt != "metadata" and custom_agent_uuid and user_db_id is not None:
        db = SessionLocal()
        try:
            saved_custom_agent = get_custom_agent_visible_to_user(
                db,
                custom_agent_uuid,
                user_db_id,
            )
            if target_prompt == "group":
                saved_parent_agent_key = str(saved_custom_agent.template_source or "").strip()
                saved_prompt = get_custom_agent_group_prompt(
                    parent_agent_key=saved_parent_agent_key,
                    group_id=target_group_id,
                    group_prompt_overrides=saved_custom_agent.group_prompt_overrides,
                )
            else:
                saved_parent_agent_key = str(saved_custom_agent.template_source or "").strip()
                try:
                    saved_prompt = custom_main_prompt_for_parent(
                        saved_parent_agent_key,
                        saved_custom_agent.instructions,
                    )
                except ValueError as exc:
                    return {
                        "success": False,
                        "error": str(exc),
                    }
            saved_updated_at = saved_custom_agent.updated_at
        except (CustomAgentAccessError, CustomAgentNotFoundError):
            logger.warning(
                "Agent Workshop prompt refresh could not access custom agent %s",
                custom_agent_uuid,
                exc_info=True,
            )
            return {
                "success": False,
                "error": f"Could not access custom agent {custom_agent_uuid}.",
            }
        finally:
            db.close()

    try:
        context_updated_at = _parse_optional_datetime(workshop.custom_agent_updated_at)
    except ValueError:
        return {
            "success": False,
            "error": (
                "Invalid custom_agent_updated_at value. "
                "Expected an ISO 8601 timestamp."
            ),
        }
    saved_is_newer = (
        False
        if target_prompt == "metadata"
        else _is_newer_datetime(saved_updated_at, context_updated_at)
    )
    has_unsaved_context = (
        target_prompt == "metadata"
        or (bool(workshop.draft_is_dirty) and not saved_is_newer)
    )

    if has_unsaved_context and context_prompt:
        source = (
            "current_workshop_metadata"
            if target_prompt == "metadata"
            else "current_workshop_draft"
        )
        refreshed_prompt = context_prompt
        version = saved_custom_agent.version if saved_custom_agent else None
        updated_at = context_updated_at
    elif saved_prompt is not None:
        source = "saved_custom_agent"
        refreshed_prompt = saved_prompt or ""
        version = saved_custom_agent.version if saved_custom_agent else None
        updated_at = saved_updated_at
    else:
        source = "current_workshop_draft"
        refreshed_prompt = context_prompt
        version = None
        updated_at = context_updated_at

    prompt_hash = _prompt_hash(refreshed_prompt)
    prompt_length = len(refreshed_prompt)
    chunk_cap = get_agent_studio_workshop_prompt_chunk_max_chars()
    common_result = {
        "success": True,
        "contract_version": "workshop_prompt_refresh.v1",
        "source": source,
        "target_prompt": target_prompt,
        "target_group_id": target_group_id or None,
        "custom_agent_id": str(custom_agent_uuid) if custom_agent_uuid else None,
        "runtime_agent_id": make_custom_agent_id(custom_agent_uuid) if custom_agent_uuid else None,
        "version": int(version) if version is not None else None,
        "updated_at": updated_at.isoformat() if isinstance(updated_at, datetime) else None,
        "length": prompt_length,
        "hash": prompt_hash,
        "freshness": {
            "draft_is_dirty": bool(workshop.draft_is_dirty),
            "has_unsaved_context": has_unsaved_context,
            "saved_is_newer": saved_is_newer,
            "context_updated_at": (
                context_updated_at.isoformat()
                if isinstance(context_updated_at, datetime)
                else None
            ),
            "saved_updated_at": (
                saved_updated_at.isoformat()
                if isinstance(saved_updated_at, datetime)
                else None
            ),
        },
        "chunk_max_chars": chunk_cap,
    }

    start = tool_input.get("start")
    requested_hash = tool_input.get("prompt_hash")
    requested_max_chars = tool_input.get("max_chars")
    if start is None:
        if requested_hash is not None or requested_max_chars is not None:
            return {
                "success": False,
                "error": "prompt_hash and max_chars require a start character offset.",
            }
        return {
            **common_result,
            "view": "summary",
            "next_call": {
                "tool": "refresh_workshop_prompt",
                "arguments": {
                    "target_prompt": target_prompt,
                    **(
                        {"target_group_id": target_group_id}
                        if target_group_id
                        else {}
                    ),
                    "prompt_hash": prompt_hash,
                    "start": 0,
                    "max_chars": chunk_cap,
                },
            },
            "instruction": (
                "This summary contains no exact content. Follow next_call until "
                "complete is true before judging the current Workshop context."
            ),
        }

    if not isinstance(start, int) or isinstance(start, bool) or start < 0:
        return {
            "success": False,
            "error": "start must be a non-negative integer character offset.",
        }
    if start > prompt_length:
        return {
            "success": False,
            "error": f"start {start} exceeds prompt length {prompt_length}.",
        }
    if not isinstance(requested_hash, str) or not requested_hash:
        return {
            "success": False,
            "error": "prompt_hash is required for exact chunk retrieval.",
        }
    if requested_hash != prompt_hash:
        return {
            "success": False,
            "error": (
                "The Workshop prompt changed after the summary was read. "
                "Refresh the summary and restart chunk retrieval."
            ),
            "current_hash": prompt_hash,
            "current_length": prompt_length,
        }
    if requested_max_chars is not None and (
        not isinstance(requested_max_chars, int)
        or isinstance(requested_max_chars, bool)
        or requested_max_chars < 1
    ):
        return {
            "success": False,
            "error": "max_chars must be a positive integer when provided.",
        }

    chunk_size = min(requested_max_chars or chunk_cap, chunk_cap)

    def build_chunk_result(end: int) -> Dict[str, Any]:
        complete = end == prompt_length
        next_call = None
        if not complete:
            next_call = {
                "tool": "refresh_workshop_prompt",
                "arguments": {
                    "target_prompt": target_prompt,
                    **(
                        {"target_group_id": target_group_id}
                        if target_group_id
                        else {}
                    ),
                    "prompt_hash": prompt_hash,
                    "start": end,
                    "max_chars": chunk_size,
                },
            }
        return {
            **common_result,
            "view": "chunk",
            "returned_range": {"start": start, "end": end},
            "content": refreshed_prompt[start:end],
            "complete": complete,
            "next_call": next_call,
            "instruction": (
                "Reconstruct prompt chunks in returned_range order. Treat conversation "
                "history and older prompt versions as historical."
            ),
        }

    requested_end = min(start + chunk_size, prompt_length)
    result = build_chunk_result(requested_end)
    provider_inline_cap = get_agent_studio_provider_tool_result_inline_max_chars()
    if len(_serialize_provider_tool_result(result)) <= provider_inline_cap:
        return result

    # Prompt characters can expand substantially during JSON escaping. Find the
    # largest exact range that fits the same serialization boundary enforced by
    # _provider_tool_result_content(), rather than allowing generic compaction to
    # discard the chunk content.
    fitting_result: Dict[str, Any] | None = None
    low = start + 1
    high = requested_end - 1
    while low <= high:
        candidate_end = (low + high) // 2
        candidate = build_chunk_result(candidate_end)
        if len(_serialize_provider_tool_result(candidate)) <= provider_inline_cap:
            fitting_result = candidate
            low = candidate_end + 1
        else:
            high = candidate_end - 1

    if fitting_result is not None:
        return fitting_result

    return {
        "success": False,
        "error": (
            "The provider inline tool-result limit is too small to return even one "
            "exact Workshop prompt character with its required identity metadata."
        ),
        "provider_inline_max_chars": provider_inline_cap,
    }


_PROMPT_SENSITIVE_WORKSHOP_RE = re.compile(
    r"\b("
    r"review|check|look\s+over|what\s+do\s+you\s+think|did\s+i\s+fix|fixed|"
    r"typo|misspell|spelling|schema|prompt|draft|main\s+prompt|group\s+prompt|"
    r"does\s+it\s+(?:still\s+)?(?:say|mention|contain)"
    r")\b",
    re.IGNORECASE,
)


def _should_force_workshop_prompt_refresh(
    *,
    context: Optional[ChatContext],
    latest_user_message: str,
) -> bool:
    """Selectively force prompt refresh for Agent Workshop review/check turns."""

    if not context or context.active_tab != "agent_workshop" or not context.agent_workshop:
        return False
    if not latest_user_message.strip():
        return False
    return bool(_PROMPT_SENSITIVE_WORKSHOP_RE.search(latest_user_message))


async def _handle_tool_call(
    tool_name: str,
    tool_input: dict,
    context: Optional[ChatContext],
    user_email: str,
    user_auth_sub: str,
    messages: Optional[List[dict]] = None,
    user_db_id: int | None = None,
    active_group_ids: Optional[List[str]] = None,
    workshop_proposal_state: Optional[dict] = None,
) -> dict:
    """
    Handle a tool call from Agent Studio AI Chat.

    Returns a dict with the tool result to send back to the assistant.
    """
    # Import tool functions (lazy import to avoid circular dependencies)
    from src.lib.agent_studio.tools import (
        get_service_logs,
        search_traces,
        get_trace_summary,
        get_tool_calls_summary,
        get_tool_calls_page,
        get_tool_call_detail,
        get_trace_conversation,
        get_extraction_diagnostic_report,
        get_extraction_timeline,
        get_evidence_revisions,
        get_trace_tree,
        get_trace_reconstruction,
        get_trace_model_live_context,
        get_trace_payloads,
        get_trace_payload,
        get_trace_costs,
        get_trace_duplicates,
        get_trace_view,
        set_trusted_trace_caller,
    )

    set_trusted_trace_caller(
        caller_sub=user_auth_sub,
        caller_email=user_email,
    )

    if tool_name in _CAPABILITY_CATALOG_TOOLS:
        if user_db_id is None:
            return {
                "success": False,
                "error": "Authenticated catalog access is unavailable.",
                "code": "catalog_identity_unavailable",
            }
        catalog_context = CapabilityCatalogContext(
            user_id=user_db_id,
            active_group_ids=tuple(active_group_ids or []),
            active_tab=_get_active_tab(context),
            artifact_kind="flow" if _get_active_tab(context) == "flows" else "agent",
        )
        db = SessionLocal()
        try:
            if tool_name == "search_studio_capabilities":
                return search_capabilities(
                    db=db,
                    context=catalog_context,
                    query=tool_input.get("query"),
                    kinds=tool_input.get("kinds"),
                    cursor=tool_input.get("cursor"),
                    limit=tool_input.get("limit"),
                    catalog_fingerprint=tool_input.get("catalog_fingerprint"),
                )
            return get_capability_detail(
                db=db,
                context=catalog_context,
                kind=tool_input.get("kind", ""),
                resource_id=tool_input.get("resource_id", ""),
                catalog_fingerprint=tool_input.get("catalog_fingerprint", ""),
                detail_hash=tool_input.get("detail_hash"),
                start=tool_input.get("start"),
                max_chars=tool_input.get("max_chars"),
            )
        except CapabilityCatalogRequestError as exc:
            return {
                "success": False,
                "error": str(exc),
                "code": "catalog_request_invalid",
            }
        except CapabilityCatalogUnavailable as exc:
            _report_agent_studio_exception_once(
                exc,
                operation="capability_catalog_unavailable",
                phase=exc.phase,
                context=exc.sanitized_context(),
            )
            logger.error(
                "Agent Studio capability catalog unavailable during %s",
                exc.phase,
                extra={"sentry_skip_event": True, **exc.sanitized_context()},
            )
            return {
                "success": False,
                "error": "The authenticated capability catalog is temporarily unavailable.",
                "code": "catalog_unavailable",
            }
        except Exception as exc:
            sanitized_context = {
                "authorization_phase": "catalog_build",
                "active_tab": catalog_context.active_tab,
                "artifact_kind": catalog_context.artifact_kind,
            }
            _report_agent_studio_exception_once(
                exc,
                operation="capability_catalog_unavailable",
                phase="catalog_build",
                context=sanitized_context,
            )
            logger.error(
                "Agent Studio capability catalog source failed",
                exc_info=True,
                extra={"sentry_skip_event": True, **sanitized_context},
            )
            return {
                "success": False,
                "error": "The authenticated capability catalog is temporarily unavailable.",
                "code": "catalog_unavailable",
            }
        finally:
            db.close()

    # ==========================================================================
    # Token-Aware Trace Analysis Tools (recommended)
    # ==========================================================================

    if tool_name == "search_traces":
        return await search_traces(
            session_id=tool_input.get("session_id"),
            name=tool_input.get("name"),
            document_id=tool_input.get("document_id"),
            run_id=tool_input.get("run_id"),
            extraction_id=tool_input.get("extraction_id"),
            from_timestamp=tool_input.get("from_timestamp"),
            to_timestamp=tool_input.get("to_timestamp"),
            offset=tool_input.get("offset", 0),
            limit=tool_input.get("limit"),
            item_start=tool_input.get("item_start", 0),
        )

    if tool_name == "get_trace_summary":
        trace_id = tool_input.get("trace_id")
        if not trace_id:
            return {
                "status": "error",
                "data": None,
                "token_info": None,
                "error": "Missing required parameter: trace_id",
                "help": "Provide trace_id from Langfuse"
            }
        return await get_trace_summary(trace_id=trace_id)

    elif tool_name == "get_tool_calls_summary":
        trace_id = tool_input.get("trace_id")
        if not trace_id:
            return {
                "status": "error",
                "data": None,
                "token_info": None,
                "error": "Missing required parameter: trace_id",
                "help": "Call get_trace_summary first to verify trace exists"
            }
        return await get_tool_calls_summary(
            trace_id=trace_id,
            page=tool_input.get("page", 1),
            page_size=tool_input.get(
                "page_size",
                get_agent_studio_trace_review_page_size(),
            ),
            item_offset=tool_input.get("item_offset", 0),
        )

    elif tool_name == "get_tool_calls_page":
        trace_id = tool_input.get("trace_id")
        if not trace_id:
            return {
                "status": "error",
                "tool_calls": None,
                "pagination": None,
                "token_info": None,
                "error": "Missing required parameter: trace_id",
                "help": "Call get_trace_summary first"
            }
        page = tool_input.get("page", 1)
        page_size = tool_input.get(
            "page_size",
            get_agent_studio_trace_review_page_size(),
        )
        tool_name_filter = tool_input.get("tool_name")
        return await get_tool_calls_page(
            trace_id=trace_id,
            page=page,
            page_size=page_size,
            item_offset=tool_input.get("item_offset", 0),
            tool_name=tool_name_filter,
        )

    elif tool_name == "get_tool_call_detail":
        trace_id = tool_input.get("trace_id")
        call_id = tool_input.get("call_id")
        field = tool_input.get("field")
        if not trace_id or not call_id or not field:
            missing = []
            if not trace_id:
                missing.append("trace_id")
            if not call_id:
                missing.append("call_id")
            if not field:
                missing.append("field")
            return {
                "status": "error",
                "tool_call": None,
                "token_info": None,
                "error": f"Missing required parameters: {', '.join(missing)}",
                "help": "Get call_id from get_tool_calls_summary response"
            }
        return await get_tool_call_detail(
            trace_id=trace_id,
            call_id=call_id,
            field=field,
            start=tool_input.get("start", 0),
            max_chars=tool_input.get(
                "max_chars",
                get_agent_studio_trace_review_chunk_max_chars(),
            ),
        )

    elif tool_name == "get_trace_conversation":
        trace_id = tool_input.get("trace_id")
        field = tool_input.get("field")
        if not trace_id or not field:
            return {
                "status": "error",
                "data": None,
                "token_info": None,
                "error": "Missing required parameters: trace_id and field",
                "help": "Call get_trace_summary first"
            }
        return await get_trace_conversation(
            trace_id=trace_id,
            field=field,
            start=tool_input.get("start", 0),
            max_chars=tool_input.get(
                "max_chars",
                get_agent_studio_trace_review_chunk_max_chars(),
            ),
        )

    elif tool_name == "get_extraction_diagnostic_report":
        trace_id = tool_input.get("trace_id")
        if not trace_id:
            return {
                "status": "error",
                "data": None,
                "token_info": None,
                "error": "Missing required parameter: trace_id",
                "help": "Call search_traces if you have a session/document/run ID instead"
            }
        return await get_extraction_diagnostic_report(
            trace_id=trace_id,
            session_id=tool_input.get("session_id"),
            feedback_id=tool_input.get("feedback_id"),
            include_sibling_traces=tool_input.get("include_sibling_traces", False),
            refresh=tool_input.get("refresh", False),
            include_raw_args=tool_input.get("include_raw_args", False),
            include_raw_outputs=tool_input.get("include_raw_outputs", False),
            tool_name=tool_input.get("tool_name"),
            event_type=tool_input.get("event_type"),
            candidate_id=tool_input.get("candidate_id"),
            section=tool_input.get("section"),
            offset=tool_input.get("offset", 0),
            limit=tool_input.get("limit"),
            item_start=tool_input.get("item_start", 0),
        )

    elif tool_name == "get_extraction_timeline":
        trace_id = tool_input.get("trace_id")
        if not trace_id:
            return {
                "status": "error",
                "data": None,
                "token_info": None,
                "error": "Missing required parameter: trace_id",
                "help": "Call search_traces if you have a session/document/run ID instead"
            }
        return await get_extraction_timeline(
            trace_id=trace_id,
            session_id=tool_input.get("session_id"),
            feedback_id=tool_input.get("feedback_id"),
            include_sibling_traces=tool_input.get("include_sibling_traces", False),
            refresh=tool_input.get("refresh", False),
            include_raw_args=tool_input.get("include_raw_args", False),
            include_raw_outputs=tool_input.get("include_raw_outputs", False),
            tool_name=tool_input.get("tool_name"),
            event_type=tool_input.get("event_type"),
            candidate_id=tool_input.get("candidate_id"),
            section=tool_input.get("section"),
            offset=tool_input.get("offset", 0),
            limit=tool_input.get("limit"),
            item_start=tool_input.get("item_start", 0),
        )

    elif tool_name == "get_evidence_revisions":
        trace_id = tool_input.get("trace_id")
        if not trace_id:
            return {
                "status": "error",
                "data": None,
                "token_info": None,
                "error": "Missing required parameter: trace_id",
                "help": "Call search_traces if you have a session/document/run ID instead"
            }
        return await get_evidence_revisions(
            trace_id=trace_id,
            session_id=tool_input.get("session_id"),
            feedback_id=tool_input.get("feedback_id"),
            include_sibling_traces=tool_input.get("include_sibling_traces", False),
            refresh=tool_input.get("refresh", False),
            tool_name=tool_input.get("tool_name"),
            event_type=tool_input.get("event_type"),
            candidate_id=tool_input.get("candidate_id"),
            section=tool_input.get("section"),
            offset=tool_input.get("offset", 0),
            limit=tool_input.get("limit"),
            item_start=tool_input.get("item_start", 0),
        )

    elif tool_name == "get_trace_tree":
        trace_id = tool_input.get("trace_id")
        if not trace_id:
            return {
                "status": "error",
                "data": None,
                "token_info": None,
                "error": "Missing required parameter: trace_id",
                "help": "Call get_trace_summary first or use search_traces to find a trace"
            }
        return await get_trace_tree(
            trace_id=trace_id,
            section=tool_input.get("section"),
            offset=tool_input.get("offset", 0),
            limit=tool_input.get("limit"),
            item_start=tool_input.get("item_start", 0),
        )

    elif tool_name == "get_trace_reconstruction":
        trace_id = tool_input.get("trace_id")
        if not trace_id:
            return {
                "status": "error",
                "data": None,
                "token_info": None,
                "error": "Missing required parameter: trace_id",
                "help": "Call get_trace_summary first or use search_traces to find a trace"
            }
        return await get_trace_reconstruction(
            trace_id=trace_id,
            limit=tool_input.get("limit"),
            offset=tool_input.get("offset", 0),
            section=tool_input.get("section"),
            item_start=tool_input.get("item_start", 0),
        )

    elif tool_name == "get_trace_model_live_context":
        trace_id = tool_input.get("trace_id")
        if not trace_id:
            return {
                "status": "error",
                "data": None,
                "token_info": None,
                "error": "Missing required parameter: trace_id",
                "help": "Call get_trace_summary first or use search_traces to find a trace"
            }
        return await get_trace_model_live_context(
            trace_id=trace_id,
            section=tool_input.get("section"),
            offset=tool_input.get("offset", 0),
            limit=tool_input.get("limit"),
            item_start=tool_input.get("item_start", 0),
        )

    elif tool_name == "get_trace_payloads":
        trace_id = tool_input.get("trace_id")
        if not trace_id:
            return {
                "status": "error",
                "data": None,
                "token_info": None,
                "error": "Missing required parameter: trace_id",
                "help": "Call get_trace_summary first or use search_traces to find a trace"
            }
        return await get_trace_payloads(
            trace_id=trace_id,
            sort=tool_input.get("sort", "largest"),
            limit=tool_input.get(
                "limit",
                get_agent_studio_trace_review_aggregate_page_size(),
            ),
            offset=tool_input.get("offset", 0),
            section=tool_input.get("section"),
            item_start=tool_input.get("item_start", 0),
        )

    elif tool_name == "get_trace_payload":
        trace_id = tool_input.get("trace_id")
        if not trace_id:
            return {
                "status": "error",
                "data": None,
                "token_info": None,
                "error": "Missing required parameter: trace_id",
                "help": "Call get_trace_payloads first to choose a payload"
            }
        return await get_trace_payload(
            trace_id=trace_id,
            payload_id=tool_input.get("payload_id"),
            scope=tool_input.get("scope"),
            observation_id=tool_input.get("observation_id"),
            field=tool_input.get("field"),
            start=tool_input.get("start", 0),
            max_chars=tool_input.get(
                "max_chars",
                get_agent_studio_trace_review_chunk_max_chars(),
            ),
        )

    elif tool_name == "get_trace_costs":
        trace_id = tool_input.get("trace_id")
        if not trace_id:
            return {
                "status": "error",
                "data": None,
                "token_info": None,
                "error": "Missing required parameter: trace_id",
                "help": "Call get_trace_summary first or use search_traces to find a trace"
            }
        return await get_trace_costs(
            trace_id=trace_id,
            section=tool_input.get("section"),
            offset=tool_input.get("offset", 0),
            limit=tool_input.get("limit"),
            item_start=tool_input.get("item_start", 0),
        )

    elif tool_name == "get_trace_duplicates":
        trace_id = tool_input.get("trace_id")
        if not trace_id:
            return {
                "status": "error",
                "data": None,
                "token_info": None,
                "error": "Missing required parameter: trace_id",
                "help": "Call get_trace_summary first or use search_traces to find a trace"
            }
        return await get_trace_duplicates(
            trace_id=trace_id,
            section=tool_input.get("section"),
            offset=tool_input.get("offset", 0),
            limit=tool_input.get("limit"),
            item_start=tool_input.get("item_start", 0),
        )

    elif tool_name == "get_trace_view":
        trace_id = tool_input.get("trace_id")
        view_name = tool_input.get("view_name")
        if not trace_id or not view_name:
            missing = []
            if not trace_id:
                missing.append("trace_id")
            if not view_name:
                missing.append("view_name")
            return {
                "status": "error",
                "data": None,
                "token_info": None,
                "error": f"Missing required parameters: {', '.join(missing)}",
                "help": "Valid view_name values: token_analysis, agent_context, pdf_citations, document_hierarchy, agent_configs, group_context, trace_summary, tool_calls, domain_envelope, extraction_timeline, evidence_revisions"
            }
        return await get_trace_view(
            trace_id=trace_id,
            view_name=view_name,
            section=tool_input.get("section"),
            offset=tool_input.get("offset", 0),
            limit=tool_input.get("limit"),
            item_start=tool_input.get("item_start", 0),
        )

    elif tool_name == "list_recent_chats":
        try:
            chat_kind = _require_tool_string(tool_input, "chat_kind")
            limit = _resolve_chat_history_limit(tool_input)
            cursor = tool_input.get("cursor")
            if cursor is not None and not isinstance(cursor, str):
                raise ValueError("cursor must be a string")
            return _with_chat_history_repository(
                lambda repository: _get_chat_session_page_payload(
                    repository=repository,
                    user_auth_sub=user_auth_sub,
                    chat_kind=chat_kind,
                    cursor=cursor,
                    limit=limit,
                )
            )
        except ValueError as exc:
            return {
                "success": False,
                "error": str(exc),
            }

    elif tool_name == "search_chat_history":
        try:
            query = _require_tool_string(tool_input, "query")
            chat_kind = _require_tool_string(tool_input, "chat_kind")
            limit = _resolve_chat_history_limit(tool_input)
            cursor = tool_input.get("cursor")
            if cursor is not None and not isinstance(cursor, str):
                raise ValueError("cursor must be a string")
            return _with_chat_history_repository(
                lambda repository: _get_chat_session_page_payload(
                    repository=repository,
                    user_auth_sub=user_auth_sub,
                    chat_kind=chat_kind,
                    cursor=cursor,
                    limit=limit,
                    query=query,
                )
            )
        except ValueError as exc:
            return {
                "success": False,
                "error": str(exc),
            }

    elif tool_name == "get_chat_conversation":
        try:
            session_id = _require_tool_string(tool_input, "session_id")
            page_size_cap = get_agent_studio_chat_recall_page_size()
            limit = tool_input.get("limit", page_size_cap)
            if (
                isinstance(limit, bool)
                or not isinstance(limit, int)
                or limit < 1
                or limit > page_size_cap
            ):
                raise ValueError(f"limit must be an integer from 1 to {page_size_cap}")
            cursor = tool_input.get("cursor")
            if cursor is not None and not isinstance(cursor, str):
                raise ValueError("cursor must be a string")
            return _with_chat_history_repository(
                lambda repository: _get_chat_conversation_payload(
                    repository=repository,
                    session_id=session_id,
                    user_auth_sub=user_auth_sub,
                    cursor=cursor,
                    limit=limit,
                )
            )
        except ValueError as exc:
            return {
                "success": False,
                "error": str(exc),
            }

    elif tool_name == "get_chat_turn":
        try:
            session_id = _require_tool_string(tool_input, "session_id")
            turn_id = _require_tool_string(tool_input, "turn_id")
            page_size_cap = get_agent_studio_chat_recall_page_size()
            limit = tool_input.get("limit", page_size_cap)
            if (
                isinstance(limit, bool)
                or not isinstance(limit, int)
                or limit < 1
                or limit > page_size_cap
            ):
                raise ValueError(f"limit must be an integer from 1 to {page_size_cap}")
            cursor = tool_input.get("cursor")
            if cursor is not None and not isinstance(cursor, str):
                raise ValueError("cursor must be a string")
            return _with_chat_history_repository(
                lambda repository: _get_chat_turn_payload(
                    repository=repository,
                    session_id=session_id,
                    turn_id=turn_id,
                    user_auth_sub=user_auth_sub,
                    cursor=cursor,
                    limit=limit,
                    message_id=tool_input.get("message_id"),
                    field=tool_input.get("field"),
                    start=tool_input.get("start"),
                    max_chars=tool_input.get("max_chars"),
                    field_hash=tool_input.get("field_hash"),
                )
            )
        except ValueError as exc:
            return {
                "success": False,
                "error": str(exc),
            }

    elif tool_name == "get_service_logs":
        container = tool_input.get("container", "backend")
        lines = tool_input.get("lines", get_agent_studio_service_log_default_lines())
        level = tool_input.get("level")
        since = tool_input.get("since")

        result = await get_service_logs(
            container=container,
            lines=lines,
            level=level,
            since=since,
            line_cursor=tool_input.get("line_cursor"),
            line_cursor_offset=tool_input.get("line_cursor_offset", 0),
            char_cursor=tool_input.get("char_cursor", 0),
        )
        return result

    elif tool_name == "list_domain_envelopes":
        return agent_studio_domain_envelope_tools.list_domain_envelopes(
            session_factory=SessionLocal,
            user_auth_sub=user_auth_sub,
            session_id=tool_input.get("session_id"),
            document_id=tool_input.get("document_id"),
            flow_run_id=tool_input.get("flow_run_id"),
            domain_pack_id=tool_input.get("domain_pack_id"),
            limit=tool_input.get("limit"),
            cursor=tool_input.get("cursor"),
        )

    elif tool_name == "get_domain_envelope_state":
        try:
            envelope_id = _require_tool_string(tool_input, "envelope_id")
            return agent_studio_domain_envelope_tools.get_domain_envelope_state(
                session_factory=SessionLocal,
                user_auth_sub=user_auth_sub,
                envelope_id=envelope_id,
                revision=tool_input.get("revision"),
                section=tool_input.get("section"),
                object_id=tool_input.get("object_id"),
                field_path=tool_input.get("field_path"),
                query=tool_input.get("query"),
                include_object_payload=tool_input.get("include_object_payload", False),
                limit=tool_input.get("limit"),
                cursor=tool_input.get("cursor"),
                reference_locator=tool_input.get("reference_locator"),
                reference_sha256=tool_input.get("reference_sha256"),
                char_cursor=tool_input.get("char_cursor"),
            )
        except ValueError as exc:
            return {"success": False, "error": str(exc)}

    elif tool_name == "get_domain_pack_validation_plan":
        return agent_studio_domain_envelope_tools.get_domain_pack_validation_plan(
            agent_id=tool_input.get("agent_id"),
            agent_revision_id=tool_input.get("agent_revision_id"),
            session_factory=SessionLocal,
            user_id=user_db_id,
            active_group_ids=active_group_ids or [],
            domain_pack_id=tool_input.get("domain_pack_id"),
            section=tool_input.get("section"),
            object_type=tool_input.get("object_type"),
            field_path=tool_input.get("field_path"),
            validator_id=tool_input.get("validator_id"),
            binding_id=tool_input.get("binding_id"),
            state=tool_input.get("state"),
            query=tool_input.get("query"),
            limit=tool_input.get("limit"),
            cursor=tool_input.get("cursor"),
        )

    elif tool_name == "get_domain_envelope_review_rows":
        try:
            envelope_id = _require_tool_string(tool_input, "envelope_id")
            return agent_studio_domain_envelope_tools.get_domain_envelope_review_rows(
                session_factory=SessionLocal,
                user_auth_sub=user_auth_sub,
                active_group_ids=active_group_ids or [],
                envelope_id=envelope_id,
                revision=tool_input.get("revision"),
                section=tool_input.get("section"),
                object_id=tool_input.get("object_id"),
                query=tool_input.get("query"),
                limit=tool_input.get("limit"),
                cursor=tool_input.get("cursor"),
            )
        except ValueError as exc:
            return {"success": False, "error": str(exc)}

    elif tool_name == "get_export_submission_readiness":
        try:
            candidate_ids = _optional_tool_string_list(
                tool_input.get("candidate_ids"),
                "candidate_ids",
            )
            expected_revisions = _optional_tool_int_mapping(
                tool_input.get("expected_envelope_revisions"),
                "expected_envelope_revisions",
            )
        except ValueError as exc:
            return {"success": False, "error": str(exc)}
        try:
            session_id = _require_tool_string(tool_input, "session_id")
        except ValueError as exc:
            return {"success": False, "error": str(exc)}
        return agent_studio_domain_envelope_tools.get_export_submission_readiness(
            session_factory=SessionLocal,
            user_auth_sub=user_auth_sub,
            session_id=session_id,
            candidate_ids=candidate_ids,
            expected_envelope_revisions=expected_revisions,
            mode=tool_input.get("mode", "readiness"),
            section=tool_input.get("section"),
            candidate_id=tool_input.get("candidate_id"),
            envelope_id=tool_input.get("envelope_id"),
            object_id=tool_input.get("object_id"),
            field_path=tool_input.get("field_path"),
            code=tool_input.get("code"),
            query=tool_input.get("query"),
            limit=tool_input.get("limit"),
            cursor=tool_input.get("cursor"),
            readiness_token=tool_input.get("readiness_token"),
        )

    elif tool_name == "refresh_workshop_prompt":
        return _build_refresh_workshop_prompt_result(
            tool_input=tool_input,
            context=context,
            user_db_id=user_db_id,
        )

    elif tool_name == "submit_prompt_suggestion":
        if "mod_id" in tool_input:
            return {
                "success": False,
                "error": "Unsupported field mod_id. Use group_id.",
            }

        # Validate required fields (agent_id is optional for general feedback)
        required_fields = ["suggestion_type", "summary", "detailed_reasoning"]
        missing_fields = [f for f in required_fields if not tool_input.get(f)]
        if missing_fields:
            return {
                "success": False,
                "error": f"Missing required fields: {', '.join(missing_fields)}",
            }

        # Validate suggestion_type
        try:
            suggestion_type = SuggestionType(tool_input["suggestion_type"])
        except ValueError:
            valid_types = [t.value for t in SuggestionType]
            return {
                "success": False,
                "error": f"Invalid suggestion_type. Must be one of: {valid_types}",
            }

        # Build the suggestion from tool input
        # Format the entire conversation history for context
        conversation_context = _format_conversation_context(messages)

        suggestion = PromptSuggestion(
            agent_id=tool_input.get("agent_id"),  # Optional for general feedback
            suggestion_type=suggestion_type,
            summary=tool_input["summary"],
            detailed_reasoning=tool_input["detailed_reasoning"],
            proposed_change=tool_input.get("proposed_change"),
            group_id=context.selected_group_id if context else None,
            trace_id=context.trace_id if context else None,
            conversation_context=conversation_context,
        )

        # Submit via SNS
        result = await submit_suggestion_sns(
            suggestion=suggestion,
            submitted_by=user_email,
            source="opus_tool",
        )

        if result.get("status") != "success":
            return {
                "success": False,
                "error": result["message"],
            }

        return {
            "success": True,
            "suggestion_id": result["suggestion_id"],
            "message": result["message"],
        }

    elif tool_name == "propose_workshop_draft_update":
        from src.lib.agent_studio.workshop_authoring import propose_workshop_update

        if not context or context.active_tab != "agent_workshop" or not context.agent_workshop or user_db_id is None:
            return {"success": False, "error": "Open an authenticated Workshop draft first."}
        with SessionLocal() as proposal_db:
            return propose_workshop_update(
                db=proposal_db, base=context.agent_workshop, tool_input=tool_input,
                user_id=user_db_id, active_group_ids=active_group_ids or [],
                state=workshop_proposal_state if workshop_proposal_state is not None else {},
            )

    elif tool_name == "report_tool_failure":
        _alert_task = asyncio.create_task(
            notify_tool_failure(
                error_type=tool_input.get("error_type", "unexpected_error"),
                error_message=tool_input.get("error_message", "No error message provided"),
                source="opus_report",
                specialist_name=tool_input.get("tool_name"),
                trace_id=context.trace_id if context else None,
                session_id=None,
                curator_id=user_email,
                context=tool_input.get("context"),
            )
        )
        return {
            "status": "success",
            "message": "Failure report sent to dev team",
        }

    # Check if this is a diagnostic tool from the registry
    registry = get_diagnostic_tools_registry()
    _ensure_flow_tools_registered(registry)
    tool_def = registry.get_tool(tool_name)

    if tool_def:
        if not _is_tool_allowed_for_context(tool_name, context):
            return _tool_scope_error(tool_name, context)

        # Execute the diagnostic tool handler
        logger.debug('Executing diagnostic tool: %s', tool_name)
        try:
            result = tool_def.handler(**tool_input)
            return result
        except Exception as e:
            _report_agent_studio_exception_once(
                e,
                operation="diagnostic_tool_execution_failed",
                phase="tool_execution",
                context={"model": AGENT_STUDIO_OPENAI_MODEL},
            )
            logger.error(
                'Diagnostic tool %s failed: %s',
                tool_name,
                e,
                exc_info=True,
                extra={"sentry_skip_event": True},
            )
            return {
                "success": False,
                "error": "Tool execution failed unexpectedly.",
            }

    return {
        "success": False,
        "error": f"Unknown tool: {tool_name}",
    }


PreparedAgentStudioTurn = agent_studio_chat_session.PreparedAgentStudioTurn


def _require_user_sub(user: Dict[str, Any]) -> str:
    """Return the authenticated user subject or raise 401."""

    user_id = str(user.get("sub") or "").strip()
    if not user_id:
        raise HTTPException(status_code=401, detail="User identifier not found in token")
    return user_id


def _normalize_optional_text(value: Any) -> str | None:
    return agent_studio_chat_session.normalize_optional_text(value)


def _json_safe(value: Any) -> Any:
    return agent_studio_chat_session.json_safe(value)


def _serialize_chat_history_session(record: ChatSessionRecord) -> Dict[str, Any]:
    return agent_studio_chat_session.serialize_chat_history_session(record)


def _require_tool_string(tool_input: dict[str, Any], field_name: str) -> str:
    return agent_studio_chat_session.require_tool_string(tool_input, field_name)


def _resolve_chat_history_limit(tool_input: dict[str, Any]) -> int:
    return agent_studio_chat_session.resolve_chat_history_limit(
        tool_input,
        max_limit=get_agent_studio_chat_history_page_size(),
    )


def _optional_tool_string_list(value: Any, field_name: str) -> List[str] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be an array of strings")
    normalized_values: List[str] = []
    for item in value:
        normalized_item = _normalize_optional_text(item)
        if normalized_item is not None:
            normalized_values.append(normalized_item)
    return normalized_values


def _optional_tool_int_mapping(value: Any, field_name: str) -> Dict[str, int] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be an object")
    normalized_values: Dict[str, int] = {}
    for raw_key, raw_value in value.items():
        key = _normalize_optional_text(raw_key)
        if key is None:
            continue
        if isinstance(raw_value, bool) or not isinstance(raw_value, int):
            raise ValueError(f"{field_name}.{key} must be an integer")
        normalized_values[key] = raw_value
    return normalized_values


def _with_chat_history_repository(
    callback: Callable[[ChatHistoryRepository], Dict[str, Any]],
) -> Dict[str, Any]:
    return agent_studio_chat_session.with_chat_history_repository(
        callback,
        session_factory=SessionLocal,
        repository_cls=ChatHistoryRepository,
    )


def _get_chat_conversation_payload(
    *,
    repository: ChatHistoryRepository,
    session_id: str,
    user_auth_sub: str,
    cursor: str | None,
    limit: int,
) -> Dict[str, Any]:
    return agent_studio_chat_session.get_chat_conversation_payload(
        repository=repository,
        session_id=session_id,
        user_auth_sub=user_auth_sub,
        cursor=cursor,
        limit=limit,
        provider_inline_max_chars=get_agent_studio_provider_tool_result_inline_max_chars(),
        serialize_session=_serialize_chat_history_session,
    )


def _get_chat_session_page_payload(
    *,
    repository: ChatHistoryRepository,
    user_auth_sub: str,
    chat_kind: str,
    cursor: str | None,
    limit: int,
    query: str | None = None,
) -> Dict[str, Any]:
    return agent_studio_chat_session.get_chat_session_page_payload(
        repository=repository,
        user_auth_sub=user_auth_sub,
        chat_kind=chat_kind,
        cursor=cursor,
        limit=limit,
        query=query,
        provider_inline_max_chars=get_agent_studio_provider_tool_result_inline_max_chars(),
    )


def _get_chat_turn_payload(
    *,
    repository: ChatHistoryRepository,
    session_id: str,
    turn_id: str,
    user_auth_sub: str,
    cursor: str | None,
    limit: int,
    message_id: str | None,
    field: str | None,
    start: int | None,
    max_chars: int | None,
    field_hash: str | None,
) -> Dict[str, Any]:
    return agent_studio_chat_session.get_chat_turn_payload(
        repository=repository,
        session_id=session_id,
        turn_id=turn_id,
        user_auth_sub=user_auth_sub,
        cursor=cursor,
        limit=limit,
        message_id=message_id,
        field=field,
        start=start,
        max_chars=max_chars,
        field_hash=field_hash,
        chunk_max_chars=get_agent_studio_chat_recall_chunk_max_chars(),
        provider_inline_max_chars=get_agent_studio_provider_tool_result_inline_max_chars(),
        serialize_session=_serialize_chat_history_session,
    )


def _extract_latest_user_message(messages: List[ChatMessage]) -> str:
    return agent_studio_chat_session.extract_latest_user_message(messages)


def _build_agent_studio_turn_id(messages: List[ChatMessage]) -> str:
    return agent_studio_chat_session.build_agent_studio_turn_id(messages)


def _derive_seeded_agent_studio_session_id(requested_session_id: str) -> str:
    return agent_studio_chat_session.derive_seeded_agent_studio_session_id(requested_session_id)


def _get_active_chat_session_row(db: Session, session_id: str) -> ChatSessionModel | None:
    return agent_studio_chat_session.get_active_chat_session_row(
        db,
        session_id,
        chat_session_model=ChatSessionModel,
    )


def _resolve_agent_studio_session_id(
    *,
    db: Session,
    user_id: str,
    requested_session_id: str | None,
) -> str:
    return agent_studio_chat_session.resolve_agent_studio_session_id(
        db=db,
        user_id=user_id,
        requested_session_id=requested_session_id,
        chat_session_model=ChatSessionModel,
    )


def _prepare_agent_studio_turn(
    *,
    db: Session,
    user_id: str,
    request: ChatRequest,
) -> PreparedAgentStudioTurn:
    return agent_studio_chat_session.prepare_agent_studio_turn(
        db=db,
        user_id=user_id,
        request=request,
        repository_cls=ChatHistoryRepository,
        chat_session_model=ChatSessionModel,
    )


def _assistant_tool_calls_from_payload(payload_json: Any) -> List[Dict[str, Any]]:
    return agent_studio_chat_session.assistant_tool_calls_from_payload(payload_json)


def _build_agent_studio_assistant_payload(
    *,
    tool_calls: List[Dict[str, Any]],
    requested_context_session_id: str | None,
    session_id: str,
    trace_capture: Dict[str, Any] | None = None,
    domain_references: Dict[str, Any] | None = None,
) -> Dict[str, Any] | None:
    payload = agent_studio_chat_session.build_agent_studio_assistant_payload(
        tool_calls=tool_calls,
        requested_context_session_id=requested_context_session_id,
        session_id=session_id,
        trace_capture=trace_capture,
    )
    if domain_references:
        payload = payload or {}
        payload["domain_references"] = domain_references
    return payload or None


def _persist_completed_agent_studio_turn(
    *,
    session_id: str,
    user_id: str,
    turn_id: str,
    assistant_message: str,
    trace_id: str | None,
    payload_json: Dict[str, Any] | None,
) -> ChatMessageRecord:
    return agent_studio_chat_session.persist_completed_agent_studio_turn(
        session_id=session_id,
        user_id=user_id,
        turn_id=turn_id,
        assistant_message=assistant_message,
        trace_id=trace_id,
        payload_json=payload_json,
        session_factory=SessionLocal,
        repository_cls=ChatHistoryRepository,
    )


def _opus_sse_event(
    *,
    session_id: str,
    turn_id: str,
    event_type: str,
    **payload: Any,
) -> str:
    return agent_studio_chat_session.opus_sse_event(
        session_id=session_id,
        turn_id=turn_id,
        event_type=event_type,
        **payload,
    )


def _build_agent_studio_replay_events(
    *,
    session_id: str,
    turn_id: str,
    assistant_turn: ChatMessageRecord,
) -> List[str]:
    return agent_studio_chat_session.build_agent_studio_replay_events(
        session_id=session_id,
        turn_id=turn_id,
        assistant_turn=assistant_turn,
    )


@router.post(
    "/chat",
    summary="Chat with the Agent Studio AI assistant",
    description="""Stream an OpenAI Agents SDK authoring conversation over SSE.""",
)
async def chat_with_opus(
    request: ChatRequest,
    user: Dict[str, Any] = get_auth_dependency(),
):
    """Run Agent Studio through the SDK-managed OpenAI Responses path."""

    user_id = _require_user_sub(user)
    user_email = user.get("email", user.get("sub", "unknown"))
    user_name = user.get("name", user.get("given_name", None))
    db_user_id: int | None = None

    try:
        db = next(get_db())
        try:
            try:
                db_user = set_global_user_from_cognito(db, user)
                db_user_id = db_user.id
            except Exception as exc:
                logger.warning("Could not resolve workflow user context: %s", exc)

            selected_agent_id = request.context.selected_agent_id if request.context else None
            if selected_agent_id:
                if db_user_id is None:
                    raise HTTPException(status_code=403, detail="Agent not available")
                _require_selected_agent_access(
                    db=db,
                    db_user_id=db_user_id,
                    user=user,
                    agent_id=selected_agent_id,
                )

            workshop_custom_agent_id = (
                request.context.agent_workshop.custom_agent_id
                if request.context and request.context.agent_workshop
                else None
            )
            if workshop_custom_agent_id:
                workshop_custom_uuid = _parse_workshop_custom_agent_uuid(
                    workshop_custom_agent_id
                )
                if workshop_custom_uuid is None:
                    raise HTTPException(status_code=404, detail="Agent not found")
                workshop_runtime_agent_id = make_custom_agent_id(workshop_custom_uuid)
                if workshop_runtime_agent_id != selected_agent_id:
                    if db_user_id is None:
                        raise HTTPException(status_code=403, detail="Agent not available")
                    _require_selected_agent_access(
                        db=db,
                        db_user_id=db_user_id,
                        user=user,
                        agent_id=workshop_runtime_agent_id,
                    )

            prepared_turn = _prepare_agent_studio_turn(
                db=db,
                user_id=user_id,
                request=request,
            )
            if prepared_turn.user_turn_created:
                source_trace_id = request.context.trace_id if request.context else None
                user_payload = _build_agent_studio_user_debug_payload(
                    db=db,
                    request=request,
                    prepared_turn=prepared_turn,
                    user_db_id=db_user_id,
                )
                _persist_agent_studio_user_debug_payload(
                    db=db,
                    user_id=user_id,
                    prepared_turn=prepared_turn,
                    trace_id=source_trace_id,
                    payload_json=user_payload,
                )
                db.commit()
        finally:
            db.close()
    except HTTPException:
        raise
    except ChatHistorySessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Chat session not found") from exc
    except ValueError as exc:
        _raise_agent_studio_validation_http_exception(
            exc=exc,
            status_code=400,
            detail="Agent Studio chat request is invalid",
            log_message="Failed to persist Agent Studio chat request because the request was invalid",
        )
    except Exception as exc:
        raise_sanitized_http_exception(
            logger,
            status_code=500,
            detail="Failed to persist Agent Studio chat request",
            log_message="Failed to persist Agent Studio chat request",
            exc=exc,
        )

    replay_assistant_turn = prepared_turn.replay_assistant_turn
    if replay_assistant_turn is not None:
        async def replay_stream():
            for event in _build_agent_studio_replay_events(
                session_id=prepared_turn.session_id,
                turn_id=prepared_turn.turn_id,
                assistant_turn=replay_assistant_turn,
            ):
                yield event

        return StreamingResponse(
            replay_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    if not str(get_api_key("openai") or "").strip():
        logger.error("OpenAI API key is not configured")
        raise HTTPException(status_code=500, detail="Chat service not properly configured")

    active_group_ids = _authenticated_group_ids(user)
    if db_user_id is not None:
        set_workflow_user_context(
            user_id=db_user_id,
            user_email=user_email,
            active_group_ids=active_group_ids,
        )

    # Keep the most recently visited Flow Builder draft callable while the curator
    # moves to Workshop and back. The active tab controls guidance, not whether
    # the captured authoring artifact exists.
    if request.context and request.context.flow_definition:
        task_input_node_id = next(
            (
                node.id
                for node in request.context.flow_definition.nodes
                if node.node_type == "task_input" or node.agent_id == "task_input"
            ),
            None,
        )
        set_current_flow_context(
            {
                "flow_name": request.context.flow_name or "Untitled Flow",
                "flow_id": request.context.flow_id,
                "flow_description": request.context.flow_description or "",
                "flow_updated_at": request.context.flow_updated_at,
                "flow_is_dirty": request.context.flow_is_dirty,
                "flow_draft_fingerprint": request.context.flow_draft_fingerprint,
                "version": request.context.flow_definition.version,
                "task_instructions_default_only": (
                    request.context.flow_definition.task_instructions_default_only
                ),
                "nodes": [
                    {**node.model_dump(exclude={"node_type"}), "type": node.node_type}
                    for node in request.context.flow_definition.nodes
                ],
                "edges": [edge.model_dump() for edge in request.context.flow_definition.edges],
                "entry_node_id": request.context.flow_definition.entry_node_id
                or task_input_node_id,
            }
        )
    else:
        clear_current_flow_context()

    system_prompt = _build_opus_system_prompt(
        context=request.context,
        user_name=user_name,
        user_email=user_email,
    )
    latest_user_index = max(
        (
            index
            for index, message in enumerate(request.messages)
            if str(message.role).strip() == "user"
        ),
        default=None,
    )
    input_items = [
        {
            "role": message.role,
            "content": (
                prepared_turn.user_message
                if latest_user_index is not None and index == latest_user_index
                else message.content
            ),
        }
        for index, message in enumerate(request.messages)
    ]
    try:
        if db_user_id is None:
            raise ToolSearchAuthorizationError(
                "Authenticated database identity is required for tool declaration",
                candidate_count=0,
                bound=0,
            )
        authorized_tools = _get_openai_authorized_tool_definitions(
            request.context,
            user_id=db_user_id,
            active_group_ids=active_group_ids,
        )
        tool_definitions = list(authorized_tools.definitions)
    except ToolSearchAuthorizationError as exc:
        _report_agent_studio_exception_once(
            exc,
            operation="tool_search_catalog_rejected",
            phase="tool_surface",
            context={"model": AGENT_STUDIO_OPENAI_MODEL, **exc.sanitized_context()},
        )
        logger.error(
            "Agent Studio tool-search catalog rejected: %s",
            exc,
            extra={"sentry_skip_event": True},
        )
        clear_workflow_user_context()
        clear_current_flow_context()
        raise HTTPException(status_code=503, detail="Agent Studio capability catalog is unavailable") from exc

    async def generate_stream():
        source_trace_id = request.context.trace_id if request.context else None
        run_state = AgentStudioRunState(trace_id=str(uuid.uuid4()))
        completed_tool_calls: List[Dict[str, Any]] = []
        domain_reference_events: List[Dict[str, Any]] = []
        workshop_proposal_state: dict = {}

        async def execute_tool(
            tool_name: str,
            tool_input: dict[str, Any],
            call_id: str | None,
        ) -> ToolExecutionResult:
            invocation_db = SessionLocal()
            try:
                invocation_authorized = is_tool_authorized_at_invocation(
                    db=invocation_db,
                    tool_name=tool_name,
                    declared_names=authorized_tools.authorized_names,
                    active_group_ids=active_group_ids,
                )
            finally:
                invocation_db.close()
            if not invocation_authorized:
                tool_result = {
                    "success": False,
                    "error": "This capability is no longer authorized for the current request.",
                    "code": "capability_not_authorized",
                }
            elif not _is_tool_allowed_for_context(tool_name, request.context):
                tool_result = _tool_scope_error(tool_name, request.context)
            else:
                try:
                    tool_result = await _handle_tool_call(
                        tool_name=tool_name,
                        tool_input=tool_input,
                        context=request.context,
                        user_email=user_email,
                        user_auth_sub=user_id,
                        messages=input_items,
                        user_db_id=db_user_id,
                        active_group_ids=active_group_ids,
                        workshop_proposal_state=workshop_proposal_state,
                    )
                except Exception as exc:
                    _report_agent_studio_exception_once(
                        exc,
                        operation="authorized_tool_execution_failed",
                        phase="tool_execution",
                        context={"model": AGENT_STUDIO_OPENAI_MODEL},
                    )
                    raise
            safe_result = _json_safe(tool_result)
            completed_tool_calls.append(
                _tool_call_audit_entry(
                    tool_name=tool_name,
                    tool_use_id=call_id,
                    tool_input=_json_safe(tool_input),
                    tool_result=safe_result,
                    context=request.context,
                )
            )
            domain_reference = _domain_references_from_tool_result(tool_name, safe_result)
            if domain_reference:
                domain_reference_events.append(domain_reference)
            return ToolExecutionResult(
                full_output=safe_result,
                provider_output=_provider_tool_result_content(
                    tool_name=tool_name,
                    tool_input=tool_input,
                    tool_result=safe_result,
                    session_id=prepared_turn.session_id,
                    turn_id=prepared_turn.turn_id,
                ),
            )

        forced_tool_name = (
            "refresh_workshop_prompt"
            if _should_force_workshop_prompt_refresh(
                context=request.context,
                latest_user_message=prepared_turn.user_message,
            )
            else None
        )
        try:
            tools, tool_counts = build_agent_studio_tools(
                tool_definitions,
                executor=execute_tool,
                state=run_state,
                namespace_for_tool=_agent_studio_tool_namespace,
                forced_tool_name=forced_tool_name,
                eager_tool_names=frozenset({"search_studio_capabilities"}),
            )
        except Exception as exc:
            _report_agent_studio_exception_once(
                exc,
                operation="tool_surface_build_failed",
                phase="tool_surface",
                context={"candidate_count": len(tool_definitions)},
            )
            logger.error(
                "Agent Studio OpenAI tool surface could not be built",
                exc_info=True,
                extra={"sentry_skip_event": True},
            )
            yield _opus_sse_event(
                session_id=prepared_turn.session_id,
                turn_id=prepared_turn.turn_id,
                event_type="ERROR",
                trace_id=run_state.trace_id,
                message="Agent Studio could not prepare its authorized capabilities. Please retry.",
                error_source="tool_surface",
            )
            clear_workflow_user_context()
            clear_current_flow_context()
            return
        logger.info(
            "Agent Studio OpenAI tool surface",
            extra={
                **tool_counts,
                "session_id": prepared_turn.session_id,
                "turn_id": prepared_turn.turn_id,
                "provider": "openai",
                "model": AGENT_STUDIO_OPENAI_MODEL,
                "authorization_fingerprint": authorized_tools.fingerprint,
                "authorization_filtered_count": authorized_tools.filtered_count,
            },
        )

        try:
            preflight = provider_context_preflight(
                surface="agent_studio",
                operation="agents_sdk_run",
                provider="openai",
                model=AGENT_STUDIO_OPENAI_MODEL,
                payload={
                    "instructions": system_prompt,
                    "input": input_items,
                    "tools": tool_definitions,
                    "tool_search": {
                        "forced_tool_name": forced_tool_name,
                        "authorization_fingerprint": authorized_tools.fingerprint,
                        "authorization_filtered_count": authorized_tools.filtered_count,
                        **tool_counts,
                    },
                },
                metadata={
                    "session_id": prepared_turn.session_id,
                    "turn_id": prepared_turn.turn_id,
                    "trace_id": run_state.trace_id,
                },
                emit_trace_event=True,
            )
            yield _opus_sse_event(
                session_id=prepared_turn.session_id,
                turn_id=prepared_turn.turn_id,
                event_type="PROVIDER_CONTEXT_PREFLIGHT",
                trace_id=run_state.trace_id,
                operation=preflight["operation"],
                provider="openai",
                model=AGENT_STUDIO_OPENAI_MODEL,
                model_live=True,
                payload_summary={
                    "json_chars": preflight["json_chars"],
                    "estimated_tokens": preflight["estimated_tokens"],
                    "threshold": preflight["threshold"],
                    "largest_paths": preflight["largest_paths"],
                },
            )

            model_settings = build_agent_studio_model_settings(
                max_output_tokens=get_agent_studio_openai_max_output_tokens(),
                tool_choice=forced_tool_name,
            )
            async for runtime_event in stream_agent_studio_run(
                instructions=system_prompt,
                input_items=input_items,
                tools=tools,
                state=run_state,
                session_id=prepared_turn.session_id,
                user_id=user_id,
                max_turns=get_agent_studio_openai_max_turns(),
                model_settings=model_settings,
            ):
                event_type = str(runtime_event.pop("type"))
                yield _opus_sse_event(
                    session_id=prepared_turn.session_id,
                    turn_id=prepared_turn.turn_id,
                    event_type=event_type,
                    trace_id=run_state.trace_id,
                    **runtime_event,
                )

            assistant_payload = _build_agent_studio_assistant_payload(
                tool_calls=completed_tool_calls,
                requested_context_session_id=prepared_turn.requested_context_session_id,
                session_id=prepared_turn.session_id,
                trace_capture={
                    "status": "captured",
                    "trace_id": run_state.trace_id,
                    "source_trace_id": source_trace_id,
                    "error": None,
                },
                domain_references=_merge_domain_reference_events(domain_reference_events),
            ) or {}
            assistant_payload["provider_run"] = {
                "provider": "openai",
                "model": AGENT_STUDIO_OPENAI_MODEL,
                "reasoning_effort": AGENT_STUDIO_REASONING_EFFORT,
                "response_id": run_state.response_id,
                "usage": {
                    "input_tokens": run_state.input_tokens,
                    "output_tokens": run_state.output_tokens,
                    "cached_input_tokens": run_state.cached_input_tokens,
                    "reasoning_tokens": run_state.reasoning_tokens,
                },
                "tool_search": {
                    **tool_counts,
                    "search_calls": run_state.tool_search_calls,
                    "search_outputs": run_state.tool_search_outputs,
                    "loaded_tool_count": run_state.tool_search_loaded_tools,
                },
            }
            assistant_turn = _persist_completed_agent_studio_turn(
                session_id=prepared_turn.session_id,
                user_id=user_id,
                turn_id=prepared_turn.turn_id,
                assistant_message=run_state.assistant_text,
                trace_id=run_state.trace_id,
                payload_json=assistant_payload,
            )
            yield _opus_sse_event(
                session_id=prepared_turn.session_id,
                turn_id=prepared_turn.turn_id,
                event_type="DONE",
                trace_id=assistant_turn.trace_id,
            )
        except ModelRefusalError:
            yield _opus_sse_event(
                session_id=prepared_turn.session_id,
                turn_id=prepared_turn.turn_id,
                event_type="REFUSAL",
                trace_id=run_state.trace_id,
                message="The model declined this request. No incomplete response was saved.",
                error_source="model_refusal",
            )
        except ModelBehaviorError as exc:
            error_text = str(exc).lower()
            is_incomplete = "response.incomplete" in error_text
            if not is_incomplete:
                _report_agent_studio_exception_once(
                    exc,
                    operation="openai_model_behavior_failure",
                    phase="agents_sdk_run",
                    context={"model": AGENT_STUDIO_OPENAI_MODEL},
                )
            yield _opus_sse_event(
                session_id=prepared_turn.session_id,
                turn_id=prepared_turn.turn_id,
                event_type="INCOMPLETE" if is_incomplete else "ERROR",
                trace_id=run_state.trace_id,
                message=(
                    "The model stopped before completing this turn. No incomplete response was saved."
                    if is_incomplete
                    else "Agent Studio could not complete the model request. Please review the last step and retry."
                ),
                error_source="openai",
            )
        except openai.BadRequestError as exc:
            error_text = str(exc).lower()
            is_context_overflow = any(
                phrase in error_text
                for phrase in ("too many tokens", "context length", "maximum context", "token limit")
            )
            if not is_context_overflow:
                _report_agent_studio_exception_once(
                    exc,
                    operation="openai_bad_request",
                    phase="agents_sdk_run",
                    context={"model": AGENT_STUDIO_OPENAI_MODEL},
                )
            yield _opus_sse_event(
                session_id=prepared_turn.session_id,
                turn_id=prepared_turn.turn_id,
                event_type="CONTEXT_OVERFLOW" if is_context_overflow else "ERROR",
                trace_id=run_state.trace_id,
                message=(
                    "The conversation exceeded the model context. Use a bounded recall tool or start a new chat."
                    if is_context_overflow
                    else "Agent Studio could not complete the model request. Please review the last step and retry."
                ),
                error_source="openai",
            )
        except MaxTurnsExceeded as exc:
            _report_agent_studio_exception_once(
                exc,
                operation="openai_turn_limit_exceeded",
                phase="agents_sdk_run",
                context={"model": AGENT_STUDIO_OPENAI_MODEL},
            )
            yield _opus_sse_event(
                session_id=prepared_turn.session_id,
                turn_id=prepared_turn.turn_id,
                event_type="ERROR",
                trace_id=run_state.trace_id,
                message="Agent Studio reached its configured tool-turn limit without completing.",
                error_source="turn_limit",
            )
        except openai.APIError as exc:
            _report_agent_studio_exception_once(
                exc,
                operation="openai_provider_failure",
                phase="agents_sdk_run",
                context={"model": AGENT_STUDIO_OPENAI_MODEL},
            )
            asyncio.create_task(
                notify_tool_failure(
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                    source="infrastructure",
                    specialist_name="agent_studio_openai",
                    trace_id=run_state.trace_id,
                    session_id=prepared_turn.session_id,
                    curator_id=user_email,
                    capture_sentry=False,
                )
            )
            logger.error(
                "OpenAI Agent Studio API error: %s",
                exc,
                exc_info=True,
                extra={"sentry_skip_event": True},
            )
            yield _opus_sse_event(
                session_id=prepared_turn.session_id,
                turn_id=prepared_turn.turn_id,
                event_type="ERROR",
                trace_id=run_state.trace_id,
                message="The model service had a temporary problem. Check any completed tool actions before retrying.",
                error_source="openai",
            )
        except ChatHistorySessionNotFoundError as exc:
            _report_agent_studio_exception_once(
                exc,
                operation="completed_turn_persistence_failed",
                phase="persistence",
                context={"model": AGENT_STUDIO_OPENAI_MODEL},
            )
            yield _opus_sse_event(
                session_id=prepared_turn.session_id,
                turn_id=prepared_turn.turn_id,
                event_type="ERROR",
                trace_id=run_state.trace_id,
                message="Agent Studio completed the response, but the durable session is no longer available.",
                error_source="history",
            )
        except Exception as exc:
            _report_agent_studio_exception_once(
                exc,
                operation="openai_stream_failure",
                phase="agents_sdk_run",
                context={"model": AGENT_STUDIO_OPENAI_MODEL},
            )
            logger.error(
                "Agent Studio OpenAI stream error: %s",
                exc,
                exc_info=True,
                extra={"sentry_skip_event": True},
            )
            yield _opus_sse_event(
                session_id=prepared_turn.session_id,
                turn_id=prepared_turn.turn_id,
                event_type="ERROR",
                trace_id=run_state.trace_id,
                message="Agent Studio ran into an unexpected problem. Check completed actions before retrying.",
                error_source=type(exc).__name__,
            )
        finally:
            clear_workflow_user_context()
            clear_current_flow_context()

    run_id = f"agent_studio_chat_turn:{prepared_turn.session_id}:{prepared_turn.turn_id}"

    def terminal_error_event(exc: Exception) -> str:
        return _opus_sse_event(
            session_id=prepared_turn.session_id,
            turn_id=prepared_turn.turn_id,
            event_type="ERROR",
            message="Agent Studio turn could not be started. Please retry.",
            error_source=type(exc).__name__,
        )

    try:
        executable_run, _ = await executable_run_manager.get_or_start_stream(
            run_id=run_id,
            kind="agent_studio_chat_turn",
            owner_user_id=user_id,
            session_id=prepared_turn.session_id,
            turn_id=prepared_turn.turn_id,
            stream_factory=generate_stream,
            can_cancel=False,
            terminal_error_event_factory=terminal_error_event,
        )
    except ExecutableRunAccessError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ExecutableRunConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return StreamingResponse(
        executable_run_manager.observe(executable_run),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _send_error_notification_sns(user_email: str, error_message: str, context: Optional[ChatContext] = None) -> None:
    """
    Send an error notification via SNS when background suggestion processing fails.

    Uses the same SNS topic as prompt suggestions (PROMPT_SUGGESTIONS_SNS_TOPIC_ARN).

    Args:
        user_email: The curator who submitted the suggestion
        error_message: Description of what went wrong
        context: Optional context (trace_id, agent_id) for debugging
    """
    try:
        # Use same topic and guard as suggestion service
        sns_topic_arn = os.getenv("PROMPT_SUGGESTIONS_SNS_TOPIC_ARN")
        use_sns = os.getenv("PROMPT_SUGGESTIONS_USE_SNS", "false").lower() == "true"

        if not use_sns or not sns_topic_arn:
            logger.info("SNS notifications disabled or not configured, skipping error notification")
            return

        sns_region = os.getenv("SNS_REGION", "us-east-1")
        aws_profile = os.getenv("AWS_PROFILE")
        if aws_profile:
            session = boto3.Session(profile_name=aws_profile)
            sns_client = session.client("sns", region_name=sns_region)
        else:
            sns_client = boto3.client("sns", region_name=sns_region)

        subject = f"[Submission Error] Failed for {user_email}"

        # Build error message with context
        message_parts = [
            "AI-Assisted Suggestion Submission Failed",
            "",
            f"User: {user_email}",
            f"Error: {error_message}",
        ]
        if context:
            if context.trace_id:
                message_parts.append(f"Trace ID: {context.trace_id}")
            if context.selected_agent_id:
                message_parts.append(f"Agent: {context.selected_agent_id}")

        message_parts.append("")
        message_parts.append("Please investigate the backend logs for more details.")

        response = sns_client.publish(
            TopicArn=sns_topic_arn,
            Subject=subject[:100],
            Message="\n".join(message_parts),
            MessageAttributes={
                "type": {"DataType": "String", "StringValue": "submission_error"},
            }
        )
        logger.info('Error notification sent to SNS: %s', response['MessageId'])

    except Exception as e:
        logger.error('Failed to send error notification via SNS: %s', e, exc_info=True)


async def _process_suggestion_background(
    messages: List[Dict[str, str]],
    system_prompt: str,
    context: Optional[ChatContext],
    user_email: str,
    user_auth_sub: str,
) -> None:
    """Submit AI-assisted feedback through a forced Agents SDK tool call."""

    state = AgentStudioRunState(trace_id=str(uuid.uuid4()))

    async def execute_tool(
        tool_name: str,
        tool_input: dict[str, Any],
        call_id: str | None,
    ) -> ToolExecutionResult:
        if tool_name != "submit_prompt_suggestion":
            result: dict[str, Any] = {
                "success": False,
                "error": "Only submit_prompt_suggestion is allowed in this run.",
            }
        else:
            result = await _handle_tool_call(
                tool_name=tool_name,
                tool_input=tool_input,
                context=context,
                user_email=user_email,
                user_auth_sub=user_auth_sub,
                messages=messages,
            )
        safe_result = _json_safe(result)
        return ToolExecutionResult(
            full_output=safe_result,
            provider_output=_serialize_provider_tool_result(safe_result),
        )

    try:
        execution = await run_forced_agent_studio_tool(
            instructions=system_prompt,
            input_items=messages,
            tool_definition=SUGGESTION_TOOL,
            executor=execute_tool,
            state=state,
            session_id=f"agent-studio-suggestion:{uuid.uuid4()}",
            user_id=user_auth_sub,
            max_turns=get_agent_studio_suggestion_max_turns(),
            max_output_tokens=get_agent_studio_suggestion_max_output_tokens(),
        )
        result = execution.output if execution is not None else None
        if not isinstance(result, dict) or result.get("success") is not True:
            error_message = (
                str(result.get("error"))
                if isinstance(result, dict) and result.get("error")
                else "OpenAI did not submit the requested suggestion."
            )
            raise RuntimeError(error_message)
        logger.info(
            "[Background] Suggestion submitted through OpenAI Responses for %s: %s",
            user_email,
            result.get("suggestion_id"),
            extra={"trace_id": state.trace_id, "response_id": state.response_id},
        )
    except Exception as exc:
        expected_outcome = expected_agent_studio_terminal_outcome(exc)
        if expected_outcome is not None:
            logger.info(
                "[Background] OpenAI suggestion ended with typed outcome: %s",
                expected_outcome,
                extra={"trace_id": state.trace_id},
            )
            _send_error_notification_sns(
                user_email,
                (
                    "The AI-assisted suggestion did not complete "
                    f"({expected_outcome.replace('_', ' ')}). Please retry."
                ),
                context,
            )
            return
        logger.error(
            "[Background] OpenAI suggestion submission failed: %s",
            exc,
            exc_info=True,
            extra={"sentry_skip_event": True},
        )
        report_background_task_exception(
            exc,
            task_name="agent_studio.process_suggestion",
            tags={
                "component": "agent_studio",
                "failure_stage": "openai_agents_sdk",
            },
        )
        _send_error_notification_sns(user_email, str(exc), context)


@router.post(
    "/submit-suggestion-direct",
    summary="Direct AI-assisted suggestion submission",
    description="""
    Directly trigger the Agent Studio assistant to analyze the current context and
    submit a suggestion to the development team. This bypasses the chat UI and
    forces the assistant to call
    the submit_prompt_suggestion tool based on available context (trace, selected agent, etc.).

    Used by the "AI-Assisted" feedback button to streamline the submission process.
    """,
    response_model=DirectSubmissionResponse,
)
async def submit_suggestion_direct(
    request: DirectSubmissionRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    user: dict = get_auth_dependency(),
):
    """
    Directly trigger the Agent Studio assistant to submit a suggestion.

    This endpoint validates the request and spawns a background task to process
    the suggestion. Returns immediately so the curator can continue working.
    On success or failure, notifications are sent via SNS.
    """
    try:
        user_email = user.get("email", "unknown@localhost")
        user_auth_sub = _require_user_sub(user)
        if not str(get_api_key("openai") or "").strip():
            raise HTTPException(status_code=500, detail="OpenAI API key not configured")

        db_user = set_global_user_from_cognito(db, user)

        # Validate selected_agent_id if provided
        if request.context and request.context.selected_agent_id:
            selected_agent_id = request.context.selected_agent_id
            if selected_agent_id.startswith("ca_"):
                custom_uuid = parse_custom_agent_id(selected_agent_id)
                if not custom_uuid:
                    raise HTTPException(status_code=400, detail=f"Invalid agent_id: {selected_agent_id}")
                try:
                    selected_custom_agent = get_custom_agent_for_user(
                        db,
                        custom_uuid,
                        db_user.id,
                    )
                    if not _agent_record_is_group_accessible(
                        selected_custom_agent,
                        user,
                    ):
                        raise CustomAgentNotFoundError("Custom agent not found")
                except CustomAgentNotFoundError:
                    raise HTTPException(status_code=400, detail=f"Invalid agent_id: {selected_agent_id}")
                except CustomAgentAccessError:
                    raise HTTPException(status_code=403, detail="Access denied to custom agent")
            else:
                if get_agent_by_key(
                    db,
                    selected_agent_id,
                    user_id=db_user.id,
                    active_group_ids=_authenticated_group_ids(user),
                ) is None:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Invalid agent_id: {selected_agent_id}",
                    )
                service = get_prompt_catalog()
                agent = service.get_agent(selected_agent_id)
                if not agent:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Invalid agent_id: {selected_agent_id}"
                    )

        # Build the system prompt
        system_prompt = _build_opus_system_prompt(request.context)

        # Create a forced message that instructs the authoring assistant to submit.
        context_description = []
        if request.context:
            if request.context.trace_id:
                context_description.append(f"trace ID {request.context.trace_id}")
            if request.context.selected_agent_id:
                context_description.append(f"the {request.context.selected_agent_id} agent prompt")
            if request.context.agent_workshop and request.context.agent_workshop.custom_agent_name:
                context_description.append(
                    f'the Agent Workshop draft for "{request.context.agent_workshop.custom_agent_name}"'
                )

        if context_description:
            context_str = " and ".join(context_description)
            forced_message = f"""The user has requested you submit feedback to the development team about {context_str}.

Please analyze the conversation history above and the available context, then submit a suggestion using the submit_prompt_suggestion tool. Provide a meaningful summary and detailed reasoning based on what we discussed.

If there's limited information available, that's okay - just explain what you know and suggest that the developers investigate further."""
        else:
            # No context - the assistant should still make a bounded submission attempt.
            forced_message = """The user has requested you submit feedback to the development team.

Please review our conversation history above and submit a general suggestion using the submit_prompt_suggestion tool. Summarize what we discussed and provide context for the developers."""

        # Prepend conversation history if provided by frontend
        messages = []
        if request.messages:
            # Convert ChatMessage objects to dicts
            messages = [
                {"role": msg.role, "content": msg.content}
                for msg in request.messages
            ]
            logger.info('[AI-Assisted Submit] Received %s messages from frontend', len(messages))
        else:
            logger.warning("[AI-Assisted Submit] No messages provided by frontend!")

        # Append the forced message
        messages.append({
            "role": "user",
            "content": forced_message,
        })

        # Spawn background task and return immediately
        add_observed_background_task(
            background_tasks,
            _process_suggestion_background,
            messages=messages,
            system_prompt=system_prompt,
            context=request.context,
            user_email=user_email,
            user_auth_sub=user_auth_sub,
            task_name="agent_studio.process_suggestion",
            tags={
                "component": "agent_studio",
            },
        )

        logger.info('[AI-Assisted Submit] Background task spawned for %s', user_email)
        return DirectSubmissionResponse(
            success=True,
            message="Submission sent",
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error('Direct submission error: %s', e, exc_info=True)
        return DirectSubmissionResponse(
            success=False,
            message="An error occurred",
            error="Failed to submit suggestion",
        )


def _build_opus_system_prompt(
    context: Optional[ChatContext],
    user_name: Optional[str] = None,
    user_email: Optional[str] = None,
) -> str:
    """Build the AI Chat system prompt from UI context and user identity."""
    from src.lib.agent_studio.context import prepare_trace_context

    return prompt_builder.build_opus_system_prompt(
        context=context,
        user_name=user_name,
        user_email=user_email,
        load_template=_load_agent_studio_system_prompt_template,
        list_model_definitions=list_model_definitions,
        get_prompt_catalog=get_prompt_catalog,
        prepare_trace_context=prepare_trace_context,
    )


# ============================================================================
# Trace Context Endpoints
# ============================================================================

# Regex pattern for valid Langfuse trace IDs (UUID format with hyphens)
# Langfuse generates trace IDs in standard UUID format: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
TRACE_ID_PATTERN = re.compile(r"^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$", re.IGNORECASE)


@router.get(
    "/trace/{trace_id}/context",
    summary="Get trace context",
    description="""
    Get enriched trace context for display in Prompt Explorer.

    Returns a summary of what happened during a chat interaction,
    including which prompts fired, tool calls, and routing decisions.
    """,
)
async def get_trace_context(
    trace_id: str = Path(..., description="Langfuse trace ID (UUID format with hyphens)"),
    user: Dict[str, Any] = get_auth_dependency()
):
    """Get enriched trace context."""
    # Validate trace_id format (UUID with hyphens - Langfuse native format)
    if not TRACE_ID_PATTERN.match(trace_id):
        raise HTTPException(
            status_code=400,
            detail="Invalid trace ID format. Expected UUID format with hyphens (e.g., 01784cd8-7512-4830-b5f5-a427502ab923)."
        )

    try:
        # Import the trace extraction service
        from src.lib.agent_studio.trace_context_service import get_trace_context_for_explorer

        context = await get_trace_context_for_explorer(trace_id)
        return {"context": context}
    except TraceNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=f"Trace '{trace_id}' not found"
        )
    except LangfuseUnavailableError as e:
        logger.error('Langfuse unavailable: %s', e)
        raise HTTPException(
            status_code=503,
            detail="Trace service temporarily unavailable"
        )
    except TraceContextError as e:
        raise_sanitized_http_exception(
            logger,
            status_code=500,
            detail="Failed to extract trace context",
            log_message="Trace context extraction failed",
            exc=e,
        )
    except Exception as e:
        raise_sanitized_http_exception(
            logger,
            status_code=500,
            detail="Internal server error",
            log_message="Unexpected error getting trace context",
            exc=e,
        )


# ============================================================================
# Suggestion Endpoints
# ============================================================================

@router.post(
    "/suggestions",
    response_model=SuggestionResponse,
    summary="Submit a prompt suggestion",
    description="""
    Manually submit a prompt improvement suggestion.

    This endpoint allows curators to submit suggestions directly,
    separate from the AI Chat conversation. Suggestions are sent
    via SNS to the development team.
    """,
)
async def submit_suggestion(
    request: ManualSuggestionRequest,
    user: Dict[str, Any] = get_auth_dependency()
):
    """Submit a prompt suggestion manually."""
    # Validate suggestion type
    try:
        suggestion_type = SuggestionType(request.suggestion_type)
    except ValueError:
        valid_types = [t.value for t in SuggestionType]
        raise HTTPException(
            status_code=400,
            detail=f"Invalid suggestion_type. Must be one of: {valid_types}"
        )

    # Validate trace_id format if provided (UUID with hyphens - Langfuse native format)
    if request.trace_id and not TRACE_ID_PATTERN.match(request.trace_id):
        raise HTTPException(
            status_code=400,
            detail="Invalid trace_id format. Expected UUID format with hyphens (e.g., 01784cd8-7512-4830-b5f5-a427502ab923)."
        )

    # Build suggestion
    suggestion = PromptSuggestion(
        agent_id=request.agent_id,
        suggestion_type=suggestion_type,
        summary=request.summary,
        detailed_reasoning=request.detailed_reasoning,
        proposed_change=request.proposed_change,
        group_id=request.group_id,
        trace_id=request.trace_id,
        conversation_context=None,
    )

    # Get user email
    user_email = user.get("email", user.get("sub", "unknown"))

    try:
        result = await submit_suggestion_sns(
            suggestion=suggestion,
            submitted_by=user_email,
            source="manual",
        )

        if result.get("status") != "success":
            status_code = 503 if result.get("sns_status") == "not_configured" else 502
            raise HTTPException(
                status_code=status_code,
                detail=result["message"],
            )

        return SuggestionResponse(
            status="success",
            suggestion_id=result["suggestion_id"],
            message=result["message"],
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error('Failed to submit suggestion: %s', e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Failed to submit suggestion"
        )


# ============================================================================
# Tool Details Endpoints
# ============================================================================

@router.get(
    "/tools",
    summary="Get all tools",
    description="Returns all available tools with their metadata.",
)
async def get_all_tools_endpoint(
    user: Dict[str, Any] = get_auth_dependency()
):
    """Get all tools from the registry."""
    try:
        tools = catalog_service.get_all_tools()
        return {"tools": tools}
    except Exception as exc:
        raise_sanitized_http_exception(
            logger,
            status_code=500,
            detail="Failed to retrieve tools",
            log_message="Failed to get tools",
            exc=exc,
        )


@router.get(
    "/tools/{tool_id}",
    summary="Get tool details",
    description="""
    Get detailed information about a specific tool.

    For multi-method tools like agr_curation_query, returns all available methods
    and their documentation.
    """,
)
async def get_tool_details_endpoint(
    tool_id: str = Path(..., description="Tool identifier (e.g., 'agr_curation_query', 'search_document')"),
    agent_id: Optional[str] = None,
    user: Dict[str, Any] = get_auth_dependency()
):
    """
    Get detailed information about a specific tool.

    Args:
        tool_id: Tool identifier
        agent_id: Optional agent ID to get agent-specific method context
    """
    try:
        if agent_id:
            # Get tool with agent-specific context
            tool = catalog_service.get_tool_for_agent(tool_id, agent_id)
        else:
            # Get generic tool details
            tool = catalog_service.get_tool_details(tool_id)

        if not tool:
            raise HTTPException(
                status_code=404,
                detail=f"Tool '{tool_id}' not found"
            )
        return {"tool": tool}
    except HTTPException:
        raise
    except Exception as exc:
        raise_sanitized_http_exception(
            logger,
            status_code=500,
            detail="Failed to retrieve tool details",
            log_message=f"Failed to get tool details for '{tool_id}'",
            exc=exc,
        )
