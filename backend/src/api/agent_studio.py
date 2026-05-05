"""Agent Studio API endpoints.

Provides endpoints for the Agent Studio feature:
- GET /catalog - Get all agent prompts organized by category
- POST /chat - Stream a conversation with the configured Anthropic chat model
- GET /trace/{trace_id}/context - Get enriched trace context
"""

import json
import logging
import os
import re
import asyncio
import uuid
from datetime import datetime  # noqa: F401 - Agent Studio module API surface.
from pathlib import Path as FilePath
from typing import Any, Callable, Dict, List, NoReturn, Optional

import anthropic
import boto3
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Path
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
import src.lib.agent_studio.chat_session as agent_studio_chat_session
import src.lib.agent_studio.prompt_builder as prompt_builder
from src.lib.agent_studio.flow_tools import (
    set_workflow_user_context,
    clear_workflow_user_context,
    set_current_flow_context,
    clear_current_flow_context,
)
from src.lib.agent_studio.diagnostic_tools import get_diagnostic_tools_registry
from src.lib.agent_studio.custom_agent_service import (
    CustomAgentAccessError,
    CustomAgentNotFoundError,
    clone_visible_agent_for_user,
    custom_agent_to_dict,
    get_custom_agent_group_prompt,
    get_custom_agent_for_user,
    list_custom_agents_visible_to_user,
    make_custom_agent_id,
    parse_custom_agent_id,
    set_custom_agent_visibility,
)
from src.lib.agent_studio.catalog_service import get_agent_by_id, get_agent_metadata
from src.lib.agent_studio.tool_policy_service import get_tool_policy_cache
from src.lib.agent_studio.tool_idea_service import (
    create_tool_idea_request,
    get_primary_project_id_for_user,
    list_tool_idea_requests_for_user,
    tool_idea_request_to_dict,
)
from src.lib.agent_studio.streaming import flatten_runner_event as _flatten_runner_event
from src.lib.alerts.tool_failure_notifier import notify_tool_failure
from src.lib.chat_history_repository import (
    ChatHistoryRepository,
    ChatHistorySessionNotFoundError,
    ChatMessageRecord,
    ChatSessionRecord,
)
from src.lib.config import list_model_definitions
from src.lib.context import set_current_session_id, set_current_user_id
from src.lib.http_errors import log_exception, raise_sanitized_http_exception
from src.lib.openai_agents import run_agent_streamed
from src.models.sql.agent import Agent as UnifiedAgent
from src.models.sql import SessionLocal, get_db
from src.models.sql.chat_session import ChatSession as ChatSessionModel
from src.services.user_service import set_global_user_from_cognito

logger = logging.getLogger(__name__)

PROMPT_EXPLORER_MODEL_ENV_VAR = "PROMPT_EXPLORER_MODEL_ID"
LEGACY_PROMPT_EXPLORER_MODEL_ENV_VAR = "ANTHROPIC_OPUS_MODEL"
AGENT_STUDIO_SEEDED_SESSION_PREFIX = agent_studio_chat_session.AGENT_STUDIO_SEEDED_SESSION_PREFIX
AGENT_STUDIO_SYSTEM_PROMPT_TEMPLATE_CANDIDATES = [
    # Prefer the canonical config copy when it exists; packaged files are
    # retained as fallbacks for test containers and backend-only packaging.
    FilePath(__file__).resolve().parents[3] / "alliance_config" / "agent_studio_system_prompt.md",
    FilePath(__file__).resolve().parents[2] / "alliance_config" / "agent_studio_system_prompt.md",
    FilePath(__file__).with_name("agent_studio_system_prompt.md"),
]


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


def _list_anthropic_catalog_models() -> List[Any]:
    """Return Anthropic models from catalog, sorted with defaults first."""
    return prompt_builder.list_anthropic_catalog_models(
        list_model_definitions=list_model_definitions,
        logger=logger,
    )


def _resolve_prompt_explorer_model() -> tuple[str, str]:
    """
    Resolve the model id/name for Agent Studio chat and suggestion submission.

    Resolution order:
    1. PROMPT_EXPLORER_MODEL_ID env override
    2. Legacy ANTHROPIC_OPUS_MODEL env override
    3. Anthropic model from config/models.yaml (default first)
    """
    configured_model_id = (
        os.getenv(PROMPT_EXPLORER_MODEL_ENV_VAR)
        or os.getenv(LEGACY_PROMPT_EXPLORER_MODEL_ENV_VAR)
        or ""
    ).strip()
    return prompt_builder.resolve_prompt_explorer_model(
        configured_model_id=configured_model_id,
        catalog_models=_list_anthropic_catalog_models(),
    )


def _load_agent_studio_system_prompt_template() -> str:
    """Load the shared Agent Studio system prompt template from alliance_config."""
    return prompt_builder.load_agent_studio_system_prompt_template(
        candidates=AGENT_STUDIO_SYSTEM_PROMPT_TEMPLATE_CANDIDATES,
        logger=logger,
    )


def _normalize_suggestion_type(value: Any) -> Any:
    """Normalize legacy suggestion type aliases during the MOD->Group migration."""
    if isinstance(value, str) and value.strip().lower() == "mod_specific":
        return "group_specific"
    return value

# Create router with prefix
router = APIRouter(prefix="/api/agent-studio")


def _merge_custom_agents_into_catalog(
    catalog: PromptCatalog,
    auth_user: Any,
    db: Any,
) -> PromptCatalog:
    """Return catalog augmented with the current user's active custom agents."""
    if not isinstance(auth_user, dict) or not hasattr(db, "query"):
        return catalog

    db_user = set_global_user_from_cognito(db, auth_user)
    custom_agents = list_custom_agents_visible_to_user(db, db_user.id)
    if not custom_agents:
        return catalog

    augmented = catalog.model_copy(deep=True)
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
        tools = list(getattr(custom, "tool_ids", None) or [])
        template_group_rules = template_prompt_info.group_rules if template_prompt_info else {}
        raw_overrides = (
            getattr(custom, "group_prompt_overrides", None)
            or getattr(custom, "mod_prompt_overrides", None)
            or {}
        )
        normalized_overrides = {
            str(group_id).strip().upper(): content
            for group_id, content in raw_overrides.items()
            if str(group_id).strip() and isinstance(content, str) and content.strip()
        }
        effective_group_rules: Dict[str, GroupRuleInfo] = {}

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

        prompt_info = PromptInfo(
            agent_id=make_custom_agent_id(custom.id),
            agent_name=custom.name,
            description=custom.description or (
                f"Custom agent from {template_name}" if template_name else "Custom scratch agent"
            ),
            base_prompt=custom.custom_prompt,
            source_file=f"custom_agent:{custom.id}",
            has_group_rules=bool(effective_group_rules),
            group_rules=effective_group_rules,
            tools=tools,
            subcategory=(
                "My Custom Agents" if custom.user_id == db_user.id else "Shared Agents"
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
        logger.error("Failed to load model options: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to load model options")


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
    _ = user
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
                    config=entry.config,
                )
                for entry in entries
            ]
        )
    except Exception as e:
        logger.error("Failed to load tool library: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to load tool library")


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
    _ = user
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
                    output_schema_key=agent.output_schema_key,
                )
                for agent in rows
            ]
        )
    except Exception as e:
        logger.error("Failed to load agent templates: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to load agent templates")


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

    agents = {}
    for agent_id, entry in AGENT_REGISTRY.items():
        supervisor = entry.get("supervisor", {})
        # supervisor_tool is only set if supervisor is enabled (default True)
        supervisor_enabled = supervisor.get("enabled", True)
        supervisor_tool = supervisor.get("tool_name") if supervisor_enabled else None

        # Icon can be at top level or nested under frontend.icon
        icon = entry.get("icon")
        if icon is None:
            frontend = entry.get("frontend", {})
            icon = frontend.get("icon", "❓")

        agents[agent_id] = AgentMetadata(
            name=entry.get("name", agent_id),
            icon=icon,
            category=entry.get("category", "Unknown"),
            subcategory=entry.get("subcategory"),
            supervisor_tool=supervisor_tool,
        )

    # Include current user's custom agents when authenticated.
    # Direct unit-test calls pass dependency placeholders, so guard by type.
    if isinstance(user, dict):
        db_user = set_global_user_from_cognito(db, user)
        custom_agents = list_custom_agents_visible_to_user(db, db_user.id)
        for custom in custom_agents:
            category = custom.category or "Custom"
            custom_id = make_custom_agent_id(custom.id)

            agents[custom_id] = AgentMetadata(
                name=custom.name,
                icon=custom.icon or "❓",
                category=category,
                subcategory=(
                    "My Custom Agents" if custom.user_id == db_user.id else "Shared Agents"
                ),
                supervisor_tool=f"ask_{custom_id.replace('-', '_')}_specialist",
            )

    return RegistryMetadataResponse(agents=agents)


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
    user: Dict[str, Any] = get_auth_dependency()
):
    """Get a combined prompt (base + group rules)."""
    try:
        service = get_prompt_catalog()
        combined = service.get_combined_prompt(request.agent_id, request.group_id)
        if combined is None:
            raise HTTPException(
                status_code=404,
                detail=f"Agent '{request.agent_id}' or group '{request.group_id}' not found"
            )
        return CombinedPromptResponse(
            agent_id=request.agent_id,
            group_id=request.group_id,
            combined_prompt=combined,
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


@router.get(
    "/prompt-preview/{agent_id}",
    response_model=PromptPreviewResponse,
    summary="Get prompt preview",
    description="Returns the effective prompt text for a system or custom agent.",
)
async def get_prompt_preview(
    agent_id: str = Path(..., description="Agent ID (system ID or custom ca_<uuid>)"),
    group_id: Optional[str] = None,
    mod_id: Optional[str] = None,
    user: Dict[str, Any] = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> PromptPreviewResponse:
    """Get prompt preview for system or custom agents."""
    try:
        resolved_group_id = group_id or mod_id
        # Custom agent preview with ownership check
        if agent_id.startswith("ca_"):
            from src.lib.agent_studio.custom_agent_service import (
                parse_custom_agent_id,
                get_custom_agent_for_user,
                CustomAgentNotFoundError,
                CustomAgentAccessError,
            )

            custom_uuid = parse_custom_agent_id(agent_id)
            if not custom_uuid:
                raise HTTPException(status_code=400, detail="Invalid custom agent id")

            db_user = set_global_user_from_cognito(db, user)
            try:
                custom_agent = get_custom_agent_for_user(db, custom_uuid, db_user.id)
            except (CustomAgentNotFoundError, CustomAgentAccessError) as exc:
                _raise_agent_studio_lookup_http_exception(
                    exc=exc,
                    log_message=f"Failed to load prompt preview for custom agent '{agent_id}'",
                    not_found_detail="Custom agent not found",
                    access_denied_detail="Access denied to custom agent",
                    not_found_error_types=(CustomAgentNotFoundError,),
                )
            preview = custom_agent.custom_prompt

            custom_group_rules_enabled = bool(
                getattr(
                    custom_agent,
                    "group_rules_enabled",
                    getattr(custom_agent, "include_mod_rules", False),
                )
            )
            custom_group_overrides = (
                getattr(custom_agent, "group_prompt_overrides", None)
                or getattr(custom_agent, "mod_prompt_overrides", None)
                or {}
            )

            if resolved_group_id and custom_group_rules_enabled:
                group_prompt = get_custom_agent_group_prompt(
                    parent_agent_key=custom_agent.parent_agent_key,
                    group_id=resolved_group_id,
                    group_prompt_overrides=custom_group_overrides,
                )
                if group_prompt:
                    preview = (
                        f"{preview}\n\n## GROUP-SPECIFIC RULES\n\n"
                        f"The following rules are specific to {resolved_group_id}:\n\n"
                        f"{group_prompt}\n\n## END GROUP-SPECIFIC RULES\n"
                    )

            return PromptPreviewResponse(
                agent_id=agent_id,
                prompt=preview,
                group_id=resolved_group_id,
                source="custom_agent",
                parent_agent_key=custom_agent.parent_agent_key,
                include_group_rules=custom_group_rules_enabled,
            )

        # System agent preview
        service = get_prompt_catalog()
        if resolved_group_id:
            prompt = service.get_combined_prompt(agent_id, resolved_group_id)
            if prompt is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"Agent '{agent_id}' or group '{resolved_group_id}' not found",
                )
        else:
            agent = service.get_agent(agent_id)
            if not agent:
                raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")
            prompt = agent.base_prompt

        return PromptPreviewResponse(
            agent_id=agent_id,
            prompt=prompt,
            group_id=resolved_group_id,
            source="system_agent",
            parent_agent_key=None,
            include_group_rules=None,
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

    resolved_agent_id = agent_id
    if agent_id.startswith("ca_"):
        custom_uuid = parse_custom_agent_id(agent_id)
        if not custom_uuid:
            raise HTTPException(status_code=400, detail="Invalid custom agent id")
        try:
            custom_agent = get_custom_agent_for_user(db, custom_uuid, db_user.id)
            resolved_agent_id = make_custom_agent_id(custom_agent.id)
        except (CustomAgentNotFoundError, CustomAgentAccessError) as exc:
            _raise_agent_studio_lookup_http_exception(
                exc=exc,
                log_message=f"Failed to resolve custom agent '{agent_id}' for isolated test execution",
                not_found_detail="Custom agent not found",
                access_denied_detail="Access denied to custom agent",
            )

    try:
        metadata = get_agent_metadata(resolved_agent_id, db_user_id=db_user.id)
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

    try:
        test_agent = get_agent_by_id(
            resolved_agent_id,
            db_user_id=db_user.id,
            document_id=request.document_id,
            user_id=str(user_sub),
            active_groups=active_groups,
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
# Chat Endpoints (Configured Anthropic Model)
# ============================================================================

# Public Agent Studio tool definitions exposed from the focused helper module.
ANTHROPIC_SUGGESTION_TOOL = opus_tools.ANTHROPIC_SUGGESTION_TOOL
UPDATE_WORKSHOP_PROMPT_TOOL = opus_tools.UPDATE_WORKSHOP_PROMPT_TOOL
ANTHROPIC_UPDATE_WORKSHOP_PROMPT_TOOL = opus_tools.ANTHROPIC_UPDATE_WORKSHOP_PROMPT_TOOL
REPORT_TOOL_FAILURE_TOOL = opus_tools.REPORT_TOOL_FAILURE_TOOL
ANTHROPIC_REPORT_TOOL_FAILURE_TOOL = opus_tools.ANTHROPIC_REPORT_TOOL_FAILURE_TOOL
CHAT_HISTORY_TOOL_CHAT_KINDS = opus_tools.CHAT_HISTORY_TOOL_CHAT_KINDS
LIST_RECENT_CHATS_TOOL = opus_tools.LIST_RECENT_CHATS_TOOL
SEARCH_CHAT_HISTORY_TOOL = opus_tools.SEARCH_CHAT_HISTORY_TOOL
GET_CHAT_CONVERSATION_TOOL = opus_tools.GET_CHAT_CONVERSATION_TOOL
GET_TRACE_SUMMARY_TOOL = opus_tools.GET_TRACE_SUMMARY_TOOL
GET_TOOL_CALLS_SUMMARY_TOOL = opus_tools.GET_TOOL_CALLS_SUMMARY_TOOL
GET_TOOL_CALLS_PAGE_TOOL = opus_tools.GET_TOOL_CALLS_PAGE_TOOL
GET_TOOL_CALL_DETAIL_TOOL = opus_tools.GET_TOOL_CALL_DETAIL_TOOL
GET_TRACE_CONVERSATION_TOOL = opus_tools.GET_TRACE_CONVERSATION_TOOL
GET_TRACE_VIEW_TOOL = opus_tools.GET_TRACE_VIEW_TOOL
GET_SERVICE_LOGS_TOOL = opus_tools.GET_SERVICE_LOGS_TOOL
_COMMON_TOOLS = opus_tools.COMMON_TOOLS
_TRACE_TOOLS = opus_tools.TRACE_TOOLS
_FLOW_TOOLS = opus_tools.FLOW_TOOLS
_AGENTS_ONLY_DIAGNOSTIC_TOOLS = opus_tools.AGENTS_ONLY_DIAGNOSTIC_TOOLS


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
    """Get all tools available to Opus in Anthropic format."""
    return opus_tools.get_all_opus_tools(
        context,
        diagnostic_registry_factory=get_diagnostic_tools_registry,
        ensure_registered=_ensure_flow_tools_registered,
        logger=logger,
        is_allowed=_is_tool_allowed_for_context,
    )


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


async def _handle_tool_call(
    tool_name: str,
    tool_input: dict,
    context: Optional[ChatContext],
    user_email: str,
    user_auth_sub: str,
    messages: Optional[List[dict]] = None,
) -> dict:
    """
    Handle a tool call from Opus.

    Returns a dict with the tool result to send back to Opus.
    """
    # Import tool functions (lazy import to avoid circular dependencies)
    from src.lib.agent_studio.tools import (
        get_service_logs,
        get_trace_summary,
        get_tool_calls_summary,
        get_tool_calls_page,
        get_tool_call_detail,
        get_trace_conversation,
        get_trace_view,
    )

    # ==========================================================================
    # Token-Aware Trace Analysis Tools (recommended)
    # ==========================================================================

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
        return await get_tool_calls_summary(trace_id=trace_id)

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
        page_size = tool_input.get("page_size", 10)
        tool_name_filter = tool_input.get("tool_name")
        return await get_tool_calls_page(
            trace_id=trace_id,
            page=page,
            page_size=page_size,
            tool_name=tool_name_filter
        )

    elif tool_name == "get_tool_call_detail":
        trace_id = tool_input.get("trace_id")
        call_id = tool_input.get("call_id")
        if not trace_id or not call_id:
            missing = []
            if not trace_id:
                missing.append("trace_id")
            if not call_id:
                missing.append("call_id")
            return {
                "status": "error",
                "tool_call": None,
                "token_info": None,
                "error": f"Missing required parameters: {', '.join(missing)}",
                "help": "Get call_id from get_tool_calls_summary response"
            }
        return await get_tool_call_detail(trace_id=trace_id, call_id=call_id)

    elif tool_name == "get_trace_conversation":
        trace_id = tool_input.get("trace_id")
        if not trace_id:
            return {
                "status": "error",
                "data": None,
                "token_info": None,
                "error": "Missing required parameter: trace_id",
                "help": "Call get_trace_summary first"
            }
        return await get_trace_conversation(trace_id=trace_id)

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
                "help": "Valid view_name values: token_analysis, agent_context, pdf_citations, document_hierarchy, agent_configs, group_context, mod_context, trace_summary"
            }
        return await get_trace_view(trace_id=trace_id, view_name=view_name)

    elif tool_name == "list_recent_chats":
        try:
            chat_kind = _require_tool_string(tool_input, "chat_kind")
            limit = _resolve_chat_history_limit(tool_input)
            return _with_chat_history_repository(
                lambda repository: {
                    "success": True,
                    "chat_kind": chat_kind,
                    "limit": limit,
                    "total_sessions": repository.count_sessions(
                        user_auth_sub=user_auth_sub,
                        chat_kind=chat_kind,
                    ),
                    "sessions": [
                        _serialize_chat_history_session(session)
                        for session in repository.list_sessions(
                            user_auth_sub=user_auth_sub,
                            chat_kind=chat_kind,
                            limit=limit,
                        ).items
                    ],
                }
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
            return _with_chat_history_repository(
                lambda repository: {
                    "success": True,
                    "query": query,
                    "chat_kind": chat_kind,
                    "limit": limit,
                    "total_sessions": repository.count_sessions(
                        user_auth_sub=user_auth_sub,
                        chat_kind=chat_kind,
                        query=query,
                    ),
                    "sessions": [
                        _serialize_chat_history_session(session)
                        for session in repository.search_sessions_ranked(
                            user_auth_sub=user_auth_sub,
                            chat_kind=chat_kind,
                            query=query,
                            limit=limit,
                        ).items
                    ],
                }
            )
        except ValueError as exc:
            return {
                "success": False,
                "error": str(exc),
            }

    elif tool_name == "get_chat_conversation":
        try:
            session_id = _require_tool_string(tool_input, "session_id")
            return _with_chat_history_repository(
                lambda repository: _get_chat_conversation_payload(
                    repository=repository,
                    session_id=session_id,
                    user_auth_sub=user_auth_sub,
                )
            )
        except ValueError as exc:
            return {
                "success": False,
                "error": str(exc),
            }

    elif tool_name == "get_service_logs":
        container = tool_input.get("container", "backend")
        lines = tool_input.get("lines", 2000)
        level = tool_input.get("level")
        since = tool_input.get("since")

        result = await get_service_logs(
            container=container,
            lines=lines,
            level=level,
            since=since,
        )
        return result

    elif tool_name == "submit_prompt_suggestion":
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
            suggestion_type = SuggestionType(
                _normalize_suggestion_type(tool_input["suggestion_type"])
            )
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

    elif tool_name == "update_workshop_prompt_draft":
        if not context or context.active_tab != "agent_workshop" or not context.agent_workshop:
            return {
                "success": False,
                "error": "This tool is only available while the curator is on the Agent Workshop tab.",
            }

        target_prompt = str(tool_input.get("target_prompt", "main")).strip().lower()
        if target_prompt == "mod":
            target_prompt = "group"
        if target_prompt not in {"main", "group"}:
            return {
                "success": False,
                "error": "Unsupported target_prompt. Must be 'main' or 'group'. Legacy 'mod' is also accepted.",
            }

        target_group_id = ""
        if target_prompt == "group":
            selected_group_id = (context.agent_workshop.selected_group_id or "").strip().upper()
            raw_target_group = tool_input.get("target_group_id", tool_input.get("target_mod_id"))
            if raw_target_group is not None and not isinstance(raw_target_group, str):
                return {
                    "success": False,
                    "error": "target_group_id must be a string when provided.",
                }
            requested_group_id = raw_target_group.strip().upper() if isinstance(raw_target_group, str) else ""
            target_group_id = requested_group_id or selected_group_id

            if not target_group_id or selected_group_id != target_group_id:
                return {
                    "success": False,
                    "error": (
                        "To edit a group prompt, select that group in Agent Workshop first "
                        "and then retry this update."
                    ),
                }

        apply_mode = tool_input.get("apply_mode", "replace")
        if apply_mode not in {"replace", "targeted_edit"}:
            return {
                "success": False,
                "error": "Unsupported apply_mode. Must be 'replace' or 'targeted_edit'.",
            }

        change_summary = tool_input.get("change_summary")
        if change_summary is not None and not isinstance(change_summary, str):
            return {
                "success": False,
                "error": "change_summary must be a string when provided.",
            }

        updated_prompt = ""
        applied_edits: List[str] = []

        if apply_mode == "replace":
            candidate_prompt = tool_input.get("updated_prompt")
            if not isinstance(candidate_prompt, str) or not candidate_prompt.strip():
                return {
                    "success": False,
                    "error": "updated_prompt must be a non-empty string when apply_mode='replace'.",
                }
            updated_prompt = candidate_prompt
        else:
            base_prompt = (
                context.agent_workshop.selected_group_prompt_draft
                if target_prompt == "group"
                else context.agent_workshop.prompt_draft
            ) or ""
            if not base_prompt.strip():
                missing_target = (
                    "selected group prompt"
                    if target_prompt == "group"
                    else "workshop draft prompt"
                )
                return {
                    "success": False,
                    "error": (
                        f"No {missing_target} is available to edit. "
                        "Provide updated_prompt with apply_mode='replace' instead."
                    ),
                }
            edits = tool_input.get("edits")
            if not isinstance(edits, list) or len(edits) == 0:
                return {
                    "success": False,
                    "error": "edits must be a non-empty array when apply_mode='targeted_edit'.",
                }

            edit_result = _apply_targeted_workshop_edits(base_prompt=base_prompt, edits=edits)
            if not edit_result.get("success"):
                return {
                    "success": False,
                    "error": str(edit_result.get("error", "Failed to apply targeted edits.")),
                }
            updated_prompt = str(edit_result.get("prompt", ""))
            applied_edits = [str(item) for item in edit_result.get("applied_edits", [])]
            if not change_summary and isinstance(edit_result.get("summary"), str):
                change_summary = edit_result["summary"]

        if len(updated_prompt) > 40000:
            return {
                "success": False,
                "error": "proposed prompt exceeds maximum size (40,000 characters).",
            }

        return {
            "success": True,
            "pending_user_approval": True,
            "apply_mode": apply_mode,
            "proposed_prompt": updated_prompt,
            "target_prompt": target_prompt,
            "target_group_id": target_group_id if target_prompt == "group" else None,
            "target_mod_id": target_group_id if target_prompt == "group" else None,
            "change_summary": change_summary.strip() if isinstance(change_summary, str) else "",
            "applied_edits": applied_edits,
            "message": "Prompt update proposal prepared. Awaiting curator approval in the UI.",
        }

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
            logger.error('Diagnostic tool %s failed: %s', tool_name, e, exc_info=True)
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


def _serialize_chat_history_message(record: ChatMessageRecord) -> Dict[str, Any]:
    return agent_studio_chat_session.serialize_chat_history_message(record)


def _require_tool_string(tool_input: dict[str, Any], field_name: str) -> str:
    return agent_studio_chat_session.require_tool_string(tool_input, field_name)


def _resolve_chat_history_limit(tool_input: dict[str, Any]) -> int:
    return agent_studio_chat_session.resolve_chat_history_limit(tool_input)


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
) -> Dict[str, Any]:
    return agent_studio_chat_session.get_chat_conversation_payload(
        repository=repository,
        session_id=session_id,
        user_auth_sub=user_auth_sub,
        serialize_session=_serialize_chat_history_session,
        serialize_message=_serialize_chat_history_message,
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


def _extract_opus_text_content(content_blocks: List[Any]) -> str:
    return agent_studio_chat_session.extract_opus_text_content(content_blocks)


def _build_agent_studio_assistant_payload(
    *,
    tool_calls: List[Dict[str, Any]],
    requested_context_session_id: str | None,
    session_id: str,
) -> Dict[str, Any] | None:
    return agent_studio_chat_session.build_agent_studio_assistant_payload(
        tool_calls=tool_calls,
        requested_context_session_id=requested_context_session_id,
        session_id=session_id,
    )


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
    summary="Chat with configured model",
    description="""
    Stream a conversation with the configured Anthropic model about prompts.

    The assistant can discuss prompts, suggest improvements, and submit suggestions
    to the development team using the submit_prompt_suggestion tool.

    Uses the effort parameter (beta) set to "medium" for optimal quality/cost balance.

    The response is a Server-Sent Events stream with the following event types:
    - TEXT_DELTA: Text content from Opus
    - TOOL_USE: Opus is calling a tool (includes tool name and input)
    - TOOL_RESULT: Result of a tool call
    - DONE: Stream complete
    - ERROR: An error occurred
    """,
)
async def chat_with_opus(
    request: ChatRequest,
    user: Dict[str, Any] = get_auth_dependency()
):
    """Stream a conversation with the configured Anthropic model with tool support."""
    import anthropic

    # Get user info for attribution and prompt personalization
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
                logger.warning('Could not resolve workflow user context: %s', exc)

            prepared_turn = _prepare_agent_studio_turn(
                db=db,
                user_id=user_id,
                request=request,
            )
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
        logger.error('Failed to persist Agent Studio chat request: %s', exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to persist Agent Studio chat request") from exc

    if prepared_turn.replay_assistant_turn is not None:
        async def replay_stream():
            for event in _build_agent_studio_replay_events(
                session_id=prepared_turn.session_id,
                turn_id=prepared_turn.turn_id,
                assistant_turn=prepared_turn.replay_assistant_turn,
            ):
                yield event

        return StreamingResponse(
            replay_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            }
        )

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        logger.error("ANTHROPIC_API_KEY environment variable not set")
        raise HTTPException(
            status_code=500,
            detail="Chat service not properly configured"
        )
    try:
        anthropic_model_id, anthropic_model_name = _resolve_prompt_explorer_model()
    except ValueError as exc:
        logger.error("%s", exc)
        raise HTTPException(status_code=500, detail="Agent Studio chat model is not configured")

    if db_user_id is not None:
        set_workflow_user_context(user_id=db_user_id, user_email=user_email)
        logger.debug('Set workflow context for user %s', db_user_id)

    # Set flow context if user is on Flows tab (for get_current_flow tool)
    if request.context and request.context.active_tab == 'flows' and request.context.flow_definition:
        # Convert Pydantic models to dicts for the context variable
        flow_context = {
            "flow_name": request.context.flow_name or "Untitled Flow",
            "nodes": [node.model_dump() for node in request.context.flow_definition.nodes],
            "edges": [edge.model_dump() for edge in request.context.flow_definition.edges],
            "entry_node_id": None,  # Will be determined by the tool
        }
        set_current_flow_context(flow_context)
        logger.debug('Set flow context: %s', flow_context.get('flow_name'))
    else:
        # Clear any previous flow context
        clear_current_flow_context()

    # Build system prompt based on context and user identity
    system_prompt = _build_opus_system_prompt(
        context=request.context,
        user_name=user_name,
        user_email=user_email,
    )

    # Convert messages to Anthropic format
    latest_user_index = max(
        (
            index
            for index, message in enumerate(request.messages)
            if str(message.role).strip() == "user"
        ),
        default=None,
    )
    messages = []
    for index, message in enumerate(request.messages):
        message_content = (
            prepared_turn.user_message
            if latest_user_index is not None and index == latest_user_index
            else message.content
        )
        messages.append({"role": message.role, "content": message_content})

    async def generate_stream():
        """Generate SSE events from Opus with true streaming and tool support."""
        trace_id = request.context.trace_id if request.context else None
        try:
            # Use AsyncAnthropic for non-blocking streaming
            client = anthropic.AsyncAnthropic(api_key=api_key)
            current_messages = messages.copy()
            collected_content: List[Any] = []
            assistant_text_parts: List[str] = []
            completed_tool_calls: List[Dict[str, Any]] = []

            # Note: User context was set before entering generate_stream().
            # We'll clean it up in the finally block at the end of this generator.

            # Build API call parameters for beta API with effort parameter
            # Using effort="medium" for optimal quality/cost balance (76% fewer tokens)
            api_params = {
                "model": anthropic_model_id,
                "betas": ["effort-2025-11-24"],
                "max_tokens": 16384,
                "system": system_prompt,
                "messages": current_messages,
                "tools": _get_all_opus_tools(request.context),
                "output_config": {"effort": "medium"},
            }
            logger.info(
                "Agent Studio chat using model='%s' (%s) and effort='medium' for balanced quality/cost",
                anthropic_model_id,
                anthropic_model_name,
            )

            while True:
                collected_content = []

                # Stream the response using beta API for effort parameter support
                async with client.beta.messages.stream(**api_params) as stream:
                    async for event in stream:
                        if event.type == "content_block_delta":
                            if hasattr(event.delta, "text"):
                                delta_text = event.delta.text
                                if delta_text:
                                    assistant_text_parts.append(delta_text)
                                    yield _opus_sse_event(
                                        session_id=prepared_turn.session_id,
                                        turn_id=prepared_turn.turn_id,
                                        event_type="TEXT_DELTA",
                                        delta=delta_text,
                                        trace_id=trace_id,
                                    )
                            elif hasattr(event.delta, "partial_json"):
                                # Tool input is being built - we'll handle complete tool use later
                                pass

                    # Get the final message to access complete tool inputs and stop reason
                    final_message = await stream.get_final_message()
                    collected_content = final_message.content
                    stop_reason = final_message.stop_reason

                # Process any tool uses after streaming completes
                if stop_reason == "tool_use":
                    tool_results_for_api = []

                    for block in collected_content:
                        if block.type == "tool_use":
                            safe_tool_input = _json_safe(block.input)

                            # Notify frontend about tool use
                            yield _opus_sse_event(
                                session_id=prepared_turn.session_id,
                                turn_id=prepared_turn.turn_id,
                                event_type="TOOL_USE",
                                tool_name=block.name,
                                tool_input=safe_tool_input,
                                trace_id=trace_id,
                            )

                            # Execute the tool
                            tool_result = await _handle_tool_call(
                                tool_name=block.name,
                                tool_input=block.input,
                                context=request.context,
                                user_email=user_email,
                                user_auth_sub=user_id,
                                messages=current_messages,
                            )
                            safe_tool_result = _json_safe(tool_result)

                            # Send tool result event to frontend
                            yield _opus_sse_event(
                                session_id=prepared_turn.session_id,
                                turn_id=prepared_turn.turn_id,
                                event_type="TOOL_RESULT",
                                tool_name=block.name,
                                result=safe_tool_result,
                                trace_id=trace_id,
                            )

                            completed_tool_calls.append(
                                {
                                    "tool_name": block.name,
                                    "tool_input": safe_tool_input,
                                    "result": safe_tool_result,
                                }
                            )

                            # Collect for API continuation
                            tool_results_for_api.append({
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": json.dumps(tool_result, default=str),
                            })

                    # Add assistant message and tool results for next turn
                    current_messages.append({
                        "role": "assistant",
                        "content": collected_content,
                    })
                    current_messages.append({
                        "role": "user",
                        "content": tool_results_for_api,
                    })
                    # Update api_params with new messages for next iteration
                    api_params["messages"] = current_messages
                    # Continue the loop for next turn
                else:
                    # Done - either end_turn or max_tokens
                    break

            assistant_message = "".join(assistant_text_parts)
            if not assistant_message:
                assistant_message = _extract_opus_text_content(collected_content)

            assistant_payload = _build_agent_studio_assistant_payload(
                tool_calls=completed_tool_calls,
                requested_context_session_id=prepared_turn.requested_context_session_id,
                session_id=prepared_turn.session_id,
            )
            assistant_turn = _persist_completed_agent_studio_turn(
                session_id=prepared_turn.session_id,
                user_id=user_id,
                turn_id=prepared_turn.turn_id,
                assistant_message=assistant_message,
                trace_id=trace_id,
                payload_json=assistant_payload,
            )

            yield _opus_sse_event(
                session_id=prepared_turn.session_id,
                turn_id=prepared_turn.turn_id,
                event_type="DONE",
                trace_id=assistant_turn.trace_id,
            )

        except anthropic.BadRequestError as e:
            # Check for context overflow specifically
            error_str = str(e).lower()
            is_context_overflow = any(phrase in error_str for phrase in [
                "too many tokens",
                "context length",
                "maximum context",
                "token limit",
                "prompt is too long",
            ])

            if is_context_overflow:
                logger.warning('Context overflow detected: %s', e)
                error_event_type = "CONTEXT_OVERFLOW"
                error_payload = {
                    "message": "I've hit my token limit for this conversation. The last tool call returned too much data.",
                    "recovery_hint": "Try a lighter-weight tool call: use get_trace_summary instead of full views, get_tool_calls_summary instead of get_tool_calls_page, or use smaller page_size (e.g., 5) with get_tool_calls_page. You can also filter by tool_name to get only specific tool calls.",
                    "suggested_tools": [
                        "get_trace_summary - lightweight overview (~500 tokens)",
                        "get_tool_calls_summary - summaries only, no full results",
                        "get_tool_calls_page with page_size=5 - smaller batches",
                        "get_tool_call_detail - single call at a time"
                    ],
                }
            else:
                asyncio.create_task(
                    notify_tool_failure(
                        error_type=type(e).__name__,
                        error_message=str(e),
                        source="infrastructure",
                        specialist_name="agent_studio_opus",
                        trace_id=trace_id,
                        session_id=prepared_turn.session_id,
                        curator_id=user_email,
                    )
                )
                logger.error('Anthropic bad request error: %s', e, exc_info=True)
                error_event_type = "ERROR"
                error_payload = {
                    "message": (
                        "Agent Studio couldn't complete that request because it ran into a "
                        "problem sending it to the model. Please review your last step and "
                        "try again. If the problem continues, refresh Agent Studio and retry."
                    ),
                    "error_source": "anthropic",
                }
            yield _opus_sse_event(
                session_id=prepared_turn.session_id,
                turn_id=prepared_turn.turn_id,
                event_type=error_event_type,
                trace_id=trace_id,
                **error_payload,
            )

        except anthropic.APIError as e:
            asyncio.create_task(
                notify_tool_failure(
                    error_type=type(e).__name__,
                    error_message=str(e),
                    source="infrastructure",
                    specialist_name="agent_studio_opus",
                    trace_id=trace_id,
                    session_id=prepared_turn.session_id,
                    curator_id=user_email,
                )
            )
            logger.error('Anthropic API error: %s', e, exc_info=True)
            yield _opus_sse_event(
                session_id=prepared_turn.session_id,
                turn_id=prepared_turn.turn_id,
                event_type="ERROR",
                trace_id=trace_id,
                message=(
                    "The model service had a temporary problem while working on your request. "
                    "Any tool actions started during this turn may already have completed, so "
                    "please check the results before retrying. If needed, try again in a moment."
                ),
                error_source="anthropic",
            )

        except ChatHistorySessionNotFoundError:
            logger.warning(
                "Agent Studio durable session disappeared before assistant completion save",
                extra={"session_id": prepared_turn.session_id, "user_id": user_id},
            )
            yield _opus_sse_event(
                session_id=prepared_turn.session_id,
                turn_id=prepared_turn.turn_id,
                event_type="ERROR",
                trace_id=trace_id,
                message="Agent Studio completed the response, but the durable session is no longer available.",
                error_source="history",
            )

        except Exception as e:
            # Also check for context overflow in general exceptions
            error_str = str(e).lower()
            is_context_overflow = any(phrase in error_str for phrase in [
                "too many tokens",
                "context length",
                "maximum context",
                "token limit",
            ])

            if is_context_overflow:
                logger.warning('Context overflow (general exception): %s', e)
                error_event_type = "CONTEXT_OVERFLOW"
                error_payload = {
                    "message": "I've hit my token limit for this conversation. The last tool call returned too much data.",
                    "recovery_hint": "Try a lighter-weight tool call: use get_trace_summary, get_tool_calls_summary, or get_tool_calls_page with a smaller page_size (e.g., 5). You can also use get_tool_call_detail to fetch one specific call at a time.",
                    "suggested_tools": [
                        "get_trace_summary - lightweight overview (~500 tokens)",
                        "get_tool_calls_summary - summaries only, no full results",
                        "get_tool_calls_page with page_size=5 - smaller batches",
                        "get_tool_call_detail - single call at a time"
                    ],
                }
            else:
                asyncio.create_task(
                    notify_tool_failure(
                        error_type=type(e).__name__,
                        error_message=str(e),
                        source="infrastructure",
                        specialist_name="agent_studio_opus",
                        trace_id=trace_id,
                        session_id=prepared_turn.session_id,
                        curator_id=user_email,
                    )
                )
                logger.error('Chat stream error: %s', e, exc_info=True)
                error_event_type = "ERROR"
                error_payload = {
                    "message": (
                        "Agent Studio ran into an unexpected problem while completing your request. "
                        "Any tool actions started during this turn may already have completed, so "
                        "please check the results before retrying. If needed, refresh Agent Studio "
                        "and try again."
                    ),
                }
            yield _opus_sse_event(
                session_id=prepared_turn.session_id,
                turn_id=prepared_turn.turn_id,
                event_type=error_event_type,
                trace_id=trace_id,
                **error_payload,
            )

        finally:
            # Clear user and flow context after streaming completes (success or error)
            clear_workflow_user_context()
            clear_current_flow_context()
            logger.debug("Cleared workflow and flow context after streaming")

    return StreamingResponse(
        generate_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
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
    api_key: str,
) -> None:
    """
    Background task that processes suggestion submission with configured chat model.

    This runs after the HTTP response has been sent to the user.
    On success, sends the suggestion via SNS.
    On failure, sends an error notification via SNS.
    """
    try:
        logger.info('[Background] Starting suggestion processing for %s', user_email)

        try:
            anthropic_model_id, anthropic_model_name = _resolve_prompt_explorer_model()
        except ValueError as exc:
            error_msg = str(exc)
            logger.error('[Background] %s', error_msg)
            _send_error_notification_sns(user_email, error_msg, context)
            return

        # Call Anthropic synchronously to get the tool call
        client = anthropic.Anthropic(api_key=api_key)

        response = client.messages.create(
            model=anthropic_model_id,
            max_tokens=4096,
            system=system_prompt,
            messages=messages,
            tools=[ANTHROPIC_SUGGESTION_TOOL],
            tool_choice={"type": "tool", "name": "submit_prompt_suggestion"},
        )
        logger.info(
            "[Background] Suggestion submission model='%s' (%s)",
            anthropic_model_id,
            anthropic_model_name,
        )

        # Extract tool use from response
        tool_use_block = None
        for block in response.content:
            if block.type == "tool_use" and block.name == "submit_prompt_suggestion":
                tool_use_block = block
                break

        if not tool_use_block:
            error_msg = "Configured model did not call submit_prompt_suggestion despite forced tool choice"
            logger.error('[Background] %s', error_msg)
            _send_error_notification_sns(user_email, error_msg, context)
            return

        # Execute the tool
        tool_result = await _handle_tool_call(
            tool_name="submit_prompt_suggestion",
            tool_input=tool_use_block.input,
            context=context,
            user_email=user_email,
            user_auth_sub=user_auth_sub,
            messages=messages,
        )

        if tool_result.get("success"):
            logger.info('[Background] Suggestion submitted successfully for %s: %s', user_email, tool_result.get('suggestion_id'))
        else:
            error_msg = tool_result.get("error", "Unknown error during tool execution")
            logger.error('[Background] Tool execution failed: %s', error_msg)
            _send_error_notification_sns(user_email, error_msg, context)

    except anthropic.APIError as e:
        error_msg = f"Anthropic API error: {str(e)}"
        logger.error('[Background] %s', error_msg, exc_info=True)
        _send_error_notification_sns(user_email, error_msg, context)

    except Exception as e:
        error_msg = f"Unexpected error: {str(e)}"
        logger.error('[Background] %s', error_msg, exc_info=True)
        _send_error_notification_sns(user_email, error_msg, context)


@router.post(
    "/submit-suggestion-direct",
    summary="Direct AI-assisted suggestion submission",
    description="""
    Directly trigger Opus to analyze the current context and submit a suggestion
    to the development team. This bypasses the chat UI and forces Opus to call
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
    Directly trigger Opus to submit a suggestion based on available context.

    This endpoint validates the request and spawns a background task to process
    the suggestion via Opus. Returns immediately so the curator can continue working.
    On success or failure, notifications are sent via SNS.
    """
    try:
        user_email = user.get("email", "unknown@localhost")
        user_auth_sub = _require_user_sub(user)
        api_key = os.getenv("ANTHROPIC_API_KEY")

        if not api_key:
            raise HTTPException(status_code=500, detail="Anthropic API key not configured")

        db_user = set_global_user_from_cognito(db, user)

        # Validate selected_agent_id if provided
        if request.context and request.context.selected_agent_id:
            selected_agent_id = request.context.selected_agent_id
            if selected_agent_id.startswith("ca_"):
                custom_uuid = parse_custom_agent_id(selected_agent_id)
                if not custom_uuid:
                    raise HTTPException(status_code=400, detail=f"Invalid agent_id: {selected_agent_id}")
                try:
                    get_custom_agent_for_user(db, custom_uuid, db_user.id)
                except CustomAgentNotFoundError:
                    raise HTTPException(status_code=400, detail=f"Invalid agent_id: {selected_agent_id}")
                except CustomAgentAccessError:
                    raise HTTPException(status_code=403, detail="Access denied to custom agent")
            else:
                service = get_prompt_catalog()
                agent = service.get_agent(selected_agent_id)
                if not agent:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Invalid agent_id: {selected_agent_id}"
                    )

        # Build the system prompt
        system_prompt = _build_opus_system_prompt(request.context)

        # Create a forced message that instructs Opus to submit
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
            # No context - Opus should still try
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
        background_tasks.add_task(
            _process_suggestion_background,
            messages=messages,
            system_prompt=system_prompt,
            context=request.context,
            user_email=user_email,
            user_auth_sub=user_auth_sub,
            api_key=api_key,
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


def _fetch_trace_for_opus(trace_id: str) -> Optional[str]:
    """Fetch trace data from Langfuse and format it for Opus's context."""
    return prompt_builder.fetch_trace_for_opus(trace_id, logger=logger)


def _build_opus_system_prompt(
    context: Optional[ChatContext],
    user_name: Optional[str] = None,
    user_email: Optional[str] = None,
) -> str:
    """Build the system prompt for Opus based on UI context and user identity."""
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
        logger.error('Trace context extraction failed: %s', e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Failed to extract trace context"
        )
    except Exception as e:
        logger.error('Unexpected error getting trace context: %s', e, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


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
    separate from the Opus chat conversation. Suggestions are sent
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
        suggestion_type = SuggestionType(_normalize_suggestion_type(request.suggestion_type))
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
