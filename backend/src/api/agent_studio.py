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
from datetime import datetime
from pathlib import Path as FilePath
from typing import Any, Dict, List, Literal, Optional

import anthropic
import boto3
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Path
from fastapi.responses import StreamingResponse
from pydantic import AliasChoices, BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from .auth import get_auth_dependency
from .logs import (
    ALLOWED_CONTAINERS as LOGS_API_ALLOWED_CONTAINERS,
    ALLOWED_LOG_LEVELS as LOGS_API_ALLOWED_LOG_LEVELS,
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
    SUBMIT_SUGGESTION_TOOL,
)
from src.lib.agent_studio.catalog_service import get_prompt_catalog
from src.lib.agent_studio.flow_tools import (
    register_flow_tools,
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
from src.lib.config import list_model_definitions
from src.lib.context import set_current_session_id, set_current_user_id
from src.lib.openai_agents import run_agent_streamed
from src.models.sql.agent import Agent as UnifiedAgent
from src.models.sql import get_db
from src.services.user_service import set_global_user_from_cognito

logger = logging.getLogger(__name__)

PROMPT_EXPLORER_MODEL_ENV_VAR = "PROMPT_EXPLORER_MODEL_ID"
LEGACY_PROMPT_EXPLORER_MODEL_ENV_VAR = "ANTHROPIC_OPUS_MODEL"
AGENT_STUDIO_SYSTEM_PROMPT_TEMPLATE_CANDIDATES = [
    # Prefer the canonical config copy when it exists; packaged files are
    # retained as fallbacks for test containers and backend-only packaging.
    FilePath(__file__).resolve().parents[3] / "alliance_config" / "agent_studio_system_prompt.md",
    FilePath(__file__).resolve().parents[2] / "alliance_config" / "agent_studio_system_prompt.md",
    FilePath(__file__).with_name("agent_studio_system_prompt.md"),
]


def _list_anthropic_catalog_models() -> List[Any]:
    """Return Anthropic models from catalog, sorted with defaults first."""
    try:
        models = list_model_definitions()
    except Exception as exc:
        logger.warning("Failed to load model catalog while resolving prompt explorer model: %s", exc)
        return []

    anthropic_models = [
        model
        for model in models
        if str(getattr(model, "provider", "") or "").strip().lower() == "anthropic"
    ]
    anthropic_models.sort(
        key=lambda model: (
            not bool(getattr(model, "default", False)),
            str(getattr(model, "name", "") or "").lower(),
        )
    )
    return anthropic_models


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

    catalog_models = _list_anthropic_catalog_models()
    catalog_name_by_id = {
        str(getattr(model, "model_id", "")).strip(): str(getattr(model, "name", "")).strip()
        for model in catalog_models
        if str(getattr(model, "model_id", "")).strip()
    }

    if configured_model_id:
        configured_name = catalog_name_by_id.get(configured_model_id) or configured_model_id
        return configured_model_id, configured_name

    if catalog_models:
        selected = catalog_models[0]
        selected_id = str(getattr(selected, "model_id", "")).strip()
        selected_name = str(getattr(selected, "name", "")).strip() or selected_id
        if selected_id:
            return selected_id, selected_name

    raise ValueError(
        "No Agent Studio Anthropic model configured. Set PROMPT_EXPLORER_MODEL_ID "
        "(or legacy ANTHROPIC_OPUS_MODEL), or add an anthropic model to config/models.yaml."
    )


def _load_agent_studio_system_prompt_template() -> str:
    """Load the shared Agent Studio system prompt template from alliance_config."""
    for candidate in AGENT_STUDIO_SYSTEM_PROMPT_TEMPLATE_CANDIDATES:
        try:
            if candidate.exists():
                return candidate.read_text(encoding="utf-8")
        except OSError:
            logger.debug("Failed to read Agent Studio system prompt template candidate: %s", candidate)

    candidate_list = ", ".join(str(path) for path in AGENT_STUDIO_SYSTEM_PROMPT_TEMPLATE_CANDIDATES)
    raise RuntimeError(
        "Failed to load Agent Studio system prompt template from any candidate path: "
        f"{candidate_list}"
    )


def _normalize_suggestion_type(value: Any) -> Any:
    """Normalize legacy suggestion type aliases during the MOD->Group migration."""
    if isinstance(value, str) and value.strip().lower() == "mod_specific":
        return "group_specific"
    return value

# Create router with prefix
router = APIRouter(prefix="/api/agent-studio")


# ============================================================================
# Request/Response Models
# ============================================================================

class ChatRequest(BaseModel):
    """Request to send a message to Opus."""
    messages: List[ChatMessage]
    context: Optional[ChatContext] = None


class CatalogResponse(BaseModel):
    """Response for prompt catalog."""
    catalog: PromptCatalog


class CombinedPromptRequest(BaseModel):
    """Request for a combined prompt (base + group rules)."""

    model_config = ConfigDict(populate_by_name=True)

    agent_id: str
    group_id: str = Field(
        ...,
        validation_alias=AliasChoices("group_id", "mod_id"),
    )


class CombinedPromptResponse(BaseModel):
    """Response with combined prompt."""
    agent_id: str
    group_id: str
    combined_prompt: str


class PromptPreviewResponse(BaseModel):
    """Response with resolved prompt text for preview/testing."""

    model_config = ConfigDict(populate_by_name=True)

    agent_id: str
    prompt: str
    group_id: Optional[str] = None
    source: str
    parent_agent_key: Optional[str] = None
    include_group_rules: Optional[bool] = None


class AgentTestRequest(BaseModel):
    """Request for isolated agent test streaming."""

    model_config = ConfigDict(populate_by_name=True)

    input: str
    group_id: Optional[str] = Field(
        None,
        validation_alias=AliasChoices("group_id", "mod_id"),
    )
    document_id: Optional[str] = None
    session_id: Optional[str] = None


class ManualSuggestionRequest(BaseModel):
    """Request to manually submit a prompt suggestion."""

    model_config = ConfigDict(populate_by_name=True)
    agent_id: Optional[str] = None  # Optional for trace-based/general feedback
    suggestion_type: str  # Will be validated against SuggestionType
    summary: str
    detailed_reasoning: str
    proposed_change: Optional[str] = None
    group_id: Optional[str] = Field(
        None,
        validation_alias=AliasChoices("group_id", "mod_id"),
    )
    trace_id: Optional[str] = None  # When provided without agent_id, this is conversation-based feedback


class SuggestionResponse(BaseModel):
    """Response after submitting a suggestion."""
    status: str
    suggestion_id: Optional[str] = None
    message: str


class AgentMetadata(BaseModel):
    """Metadata for a single agent."""
    name: str
    icon: str
    category: str
    subcategory: Optional[str] = None
    supervisor_tool: Optional[str] = None


class RegistryMetadataResponse(BaseModel):
    """Response for registry metadata endpoint."""
    agents: Dict[str, AgentMetadata]


class ModelOption(BaseModel):
    """Curator-selectable model option."""

    model_id: str
    name: str
    provider: str
    description: str = ""
    guidance: str = ""
    default: bool = False
    supports_reasoning: bool = True
    supports_temperature: bool = True
    reasoning_options: List[str] = Field(default_factory=list)
    default_reasoning: Optional[str] = None
    reasoning_descriptions: Dict[str, str] = Field(default_factory=dict)
    recommended_for: List[str] = Field(default_factory=list)
    avoid_for: List[str] = Field(default_factory=list)


class ModelsResponse(BaseModel):
    """Response for available model options."""

    models: List[ModelOption]


class ToolLibraryItem(BaseModel):
    """Single tool entry from tool library policy table."""

    tool_key: str
    display_name: str
    description: str
    category: str
    curator_visible: bool
    allow_attach: bool
    allow_execute: bool
    config: Dict[str, Any] = Field(default_factory=dict)


class ToolLibraryResponse(BaseModel):
    """Response for tool library."""

    tools: List[ToolLibraryItem]


class AgentTemplateItem(BaseModel):
    """System agent template option for Agent Workshop."""

    agent_id: str
    name: str
    description: Optional[str] = None
    icon: str
    category: Optional[str] = None
    model_id: str
    tool_ids: List[str]
    output_schema_key: Optional[str] = None


class AgentTemplatesResponse(BaseModel):
    """Response for available system templates."""

    templates: List[AgentTemplateItem]


class CloneAgentRequest(BaseModel):
    """Optional clone parameters."""

    name: Optional[str] = Field(None, min_length=1, max_length=100)


class ShareAgentRequest(BaseModel):
    """Visibility update payload for sharing toggle."""

    visibility: Literal["private", "project"]


class ToolIdeaConversationEntry(BaseModel):
    """Single Opus ideation conversation turn."""

    role: Literal["user", "assistant", "system"]
    content: str = Field(..., min_length=1)
    timestamp: Optional[str] = None


class ToolIdeaCreateRequest(BaseModel):
    """Payload for submitting a new tool idea request."""

    title: str = Field(..., min_length=1, max_length=255)
    description: str = Field(..., min_length=1)
    opus_conversation: Optional[List[ToolIdeaConversationEntry]] = None

    model_config = ConfigDict(extra="forbid")


class ToolIdeaResponseItem(BaseModel):
    """Tool idea request row returned to curators."""

    id: str
    user_id: int
    project_id: Optional[str] = None
    title: str
    description: str
    opus_conversation: List[Dict[str, Any]]
    status: Literal["submitted", "reviewed", "in_progress", "completed", "declined"]
    developer_notes: Optional[str] = None
    resulting_tool_key: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class ToolIdeaListResponse(BaseModel):
    """Response payload for current user's tool idea requests."""

    tool_ideas: List[ToolIdeaResponseItem]
    total: int


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
        raise HTTPException(status_code=400, detail=str(exc))


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
    except CustomAgentNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc))
    except CustomAgentAccessError as exc:
        db.rollback()
        raise HTTPException(status_code=403, detail=str(exc))
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc))

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
    except Exception as e:
        logger.error('Failed to get prompt catalog: %s', e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


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
    except Exception as e:
        logger.error('Failed to refresh prompt catalog: %s', e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


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
    except Exception as e:
        logger.error('Failed to get combined prompt: %s', e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


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
                raise HTTPException(status_code=400, detail=f"Invalid custom agent id: {agent_id}")

            db_user = set_global_user_from_cognito(db, user)
            try:
                custom_agent = get_custom_agent_for_user(db, custom_uuid, db_user.id)
            except CustomAgentNotFoundError as exc:
                raise HTTPException(status_code=404, detail=str(exc))
            except CustomAgentAccessError as exc:
                raise HTTPException(status_code=403, detail=str(exc))
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
    except Exception as e:
        logger.error("Failed to get prompt preview for '%s': %s", agent_id, e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


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
            raise HTTPException(status_code=400, detail=f"Invalid custom agent id: {agent_id}")
        try:
            custom_agent = get_custom_agent_for_user(db, custom_uuid, db_user.id)
            resolved_agent_id = make_custom_agent_id(custom_agent.id)
        except CustomAgentNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except CustomAgentAccessError as exc:
            raise HTTPException(status_code=403, detail=str(exc))

    try:
        metadata = get_agent_metadata(resolved_agent_id, db_user_id=db_user.id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

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
        raise HTTPException(status_code=400, detail=f"Failed to initialize agent '{agent_id}': {exc}")

    async def _stream_events():
        trace_id = None
        try:
            async for event in run_agent_streamed(
                user_message=request.input,
                user_id=str(user_sub),
                session_id=session_id,
                document_id=request.document_id,
                conversation_history=None,
                active_groups=active_groups,
                agent=test_agent,
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
            logger.warning('Agent test stream cancelled: agent_id=%s', agent_id)
            error_event = {
                "type": "RUN_ERROR",
                "message": "Agent test cancelled unexpectedly.",
                "error_type": "StreamCancelled",
                "trace_id": trace_id,
                "session_id": session_id,
                "sessionId": session_id,
            }
            yield f"data: {json.dumps(error_event)}\n\n"
        except Exception as exc:
            logger.error('Agent test stream error for %s: %s', agent_id, exc, exc_info=True)
            error_event = {
                "type": "RUN_ERROR",
                "message": f"Agent test failed: {exc}",
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


# ============================================================================
# Chat Endpoints (Configured Anthropic Model)
# ============================================================================

# Convert tool definition to Anthropic format
ANTHROPIC_SUGGESTION_TOOL = {
    "name": SUBMIT_SUGGESTION_TOOL["name"],
    "description": SUBMIT_SUGGESTION_TOOL["description"],
    "input_schema": SUBMIT_SUGGESTION_TOOL["input_schema"],
}

UPDATE_WORKSHOP_PROMPT_TOOL = {
    "name": "update_workshop_prompt_draft",
    "description": """Propose a prompt update for the current Agent Workshop draft.

Use this when the curator asks you to rewrite, replace, or significantly refactor
their current workshop prompt (main prompt or selected group prompt). This tool does
NOT auto-apply or auto-save changes.
The UI will show the proposal and require explicit curator approval before applying.
""",
    "input_schema": {
        "type": "object",
        "properties": {
            "target_prompt": {
                "type": "string",
                "enum": ["main", "group", "mod"],
                "description": "Which workshop prompt to update. Use 'main' for the base system prompt and 'group' for the selected group prompt override. Legacy 'mod' is accepted during migration.",
                "default": "main",
            },
            "target_group_id": {
                "type": "string",
                "description": "Optional group ID when target_prompt='group' (for example 'WB'). Must match the currently selected group in Agent Workshop. Legacy 'target_mod_id' is accepted during migration.",
            },
            "updated_prompt": {
                "type": "string",
                "description": "Complete replacement prompt text (required when apply_mode='replace').",
            },
            "edits": {
                "type": "array",
                "description": "Targeted edit operations (required when apply_mode='targeted_edit').",
                "items": {
                    "type": "object",
                    "properties": {
                        "operation": {
                            "type": "string",
                            "enum": ["replace_text", "replace_section"],
                            "description": "Edit operation type.",
                        },
                        "find_text": {
                            "type": "string",
                            "description": "Text to find when operation='replace_text'.",
                        },
                        "replacement_text": {
                            "type": "string",
                            "description": "Replacement text for the operation.",
                        },
                        "occurrence": {
                            "type": "string",
                            "enum": ["first", "last", "all"],
                            "description": "Which occurrence to replace for replace_text (default: first).",
                        },
                        "section_heading": {
                            "type": "string",
                            "description": "Markdown section heading text to replace when operation='replace_section'.",
                        },
                    },
                    "required": ["operation"],
                },
            },
            "change_summary": {
                "type": "string",
                "description": "Optional short summary of what changed and why.",
            },
            "apply_mode": {
                "type": "string",
                "enum": ["replace", "targeted_edit"],
                "description": "How to build the proposed update.",
                "default": "replace",
            },
        },
        "required": [],
    },
}

ANTHROPIC_UPDATE_WORKSHOP_PROMPT_TOOL = UPDATE_WORKSHOP_PROMPT_TOOL

REPORT_TOOL_FAILURE_TOOL = {
    "name": "report_tool_failure",
    "description": """Report a tool failure to the development team.

Use this tool immediately when any tool call returns an infrastructure or service
failure (error status, timeout, connection failure, service unavailable, or
unexpected empty response that indicates a system issue).

Do NOT use this for user input errors (e.g., invalid gene names, malformed IDs).""",
    "input_schema": {
        "type": "object",
        "properties": {
            "tool_name": {
                "type": "string",
                "description": "Name of the tool that failed",
            },
            "error_message": {
                "type": "string",
                "description": "Error message or concise description of the failure",
            },
            "error_type": {
                "type": "string",
                "enum": [
                    "timeout",
                    "connection_error",
                    "service_unavailable",
                    "unexpected_error",
                    "empty_response",
                    "api_error",
                ],
                "description": "Category of the tool failure",
            },
            "context": {
                "type": "string",
                "description": "Optional brief context describing what you were trying to do",
            },
        },
        "required": ["tool_name", "error_message", "error_type"],
    },
}

ANTHROPIC_REPORT_TOOL_FAILURE_TOOL = REPORT_TOOL_FAILURE_TOOL

# =============================================================================
# Token-Aware Trace Analysis Tools (Claude-Specific Endpoints)
# =============================================================================
# These tools use the new /api/claude/traces/ endpoints that include token
# metadata and automatic truncation to stay within budget.

GET_TRACE_SUMMARY_TOOL = {
    "name": "get_trace_summary",
    "description": "Get lightweight trace summary (~500 tokens). ALWAYS CALL THIS FIRST when analyzing a trace. Returns: trace name, duration, cost, token counts, tool call count, unique tools, error status, context overflow detection.",
    "input_schema": {
        "type": "object",
        "properties": {
            "trace_id": {
                "type": "string",
                "description": "Langfuse trace ID (UUID with hyphens or 32-char hex string)"
            }
        },
        "required": ["trace_id"]
    }
}

GET_TOOL_CALLS_SUMMARY_TOOL = {
    "name": "get_tool_calls_summary",
    "description": "Get lightweight summary of ALL tool calls without full results (~100 tokens/call). Use this to see what tools were called before drilling into details. Returns: total count, unique tools, and list of summaries (call_id, name, time, duration, status, input_summary, result_summary).",
    "input_schema": {
        "type": "object",
        "properties": {
            "trace_id": {
                "type": "string",
                "description": "Langfuse trace ID"
            }
        },
        "required": ["trace_id"]
    }
}

GET_TOOL_CALLS_PAGE_TOOL = {
    "name": "get_tool_calls_page",
    "description": "Get paginated tool calls with full details. Use for detailed analysis of specific calls. Results are automatically truncated to fit within token budget. Supports filtering by tool name.",
    "input_schema": {
        "type": "object",
        "properties": {
            "trace_id": {
                "type": "string",
                "description": "Langfuse trace ID"
            },
            "page": {
                "type": "integer",
                "description": "Page number (1-indexed, default: 1)",
                "default": 1,
                "minimum": 1
            },
            "page_size": {
                "type": "integer",
                "description": "Items per page (default: 10, max: 20)",
                "default": 10,
                "minimum": 1,
                "maximum": 20
            },
            "tool_name": {
                "type": "string",
                "description": "Optional filter by tool name (e.g., 'search_document')"
            }
        },
        "required": ["trace_id"]
    }
}

GET_TOOL_CALL_DETAIL_TOOL = {
    "name": "get_tool_call_detail",
    "description": "Get full details for a single tool call. Use when you need complete input/output for a specific call identified from get_tool_calls_summary. Token cost: ~1-5K tokens depending on result size.",
    "input_schema": {
        "type": "object",
        "properties": {
            "trace_id": {
                "type": "string",
                "description": "Langfuse trace ID"
            },
            "call_id": {
                "type": "string",
                "description": "Tool call ID from get_tool_calls_summary response"
            }
        },
        "required": ["trace_id", "call_id"]
    }
}

GET_TRACE_CONVERSATION_TOOL = {
    "name": "get_trace_conversation",
    "description": "Get the user's query and assistant's final response. Use when you need to see what the curator asked and what the AI answered. Token cost varies by response length.",
    "input_schema": {
        "type": "object",
        "properties": {
            "trace_id": {
                "type": "string",
                "description": "Langfuse trace ID"
            }
        },
        "required": ["trace_id"]
    }
}

GET_TRACE_VIEW_TOOL = {
    "name": "get_trace_view",
    "description": "Get a specific analysis view with token metadata. Use for specialized views not covered by the primary tools. Available views: token_analysis, agent_context, pdf_citations, document_hierarchy, agent_configs, group_context, trace_summary.",
    "input_schema": {
        "type": "object",
        "properties": {
            "trace_id": {
                "type": "string",
                "description": "Langfuse trace ID"
            },
            "view_name": {
                "type": "string",
                "enum": ["token_analysis", "agent_context", "pdf_citations", "document_hierarchy", "agent_configs", "group_context", "mod_context", "trace_summary"],
                "description": "Which view to fetch"
            }
        },
        "required": ["trace_id", "view_name"]
    }
}

GET_SERVICE_LOGS_TOOL = {
    "name": "get_service_logs",
    "description": "Retrieve Loki-backed service logs for troubleshooting. Use this when curators report errors or unexpected behavior; optional level and time filters can narrow the results.",
    "input_schema": {
        "type": "object",
        "properties": {
            "container": {
                "type": "string",
                "enum": sorted(LOGS_API_ALLOWED_CONTAINERS),
                "description": "Service/container name (default: backend)",
                "default": "backend"
            },
            "lines": {
                "type": "integer",
                "description": "Number of recent log lines (default: 2000, min: 100, max: 5000)",
                "default": 2000,
                "minimum": 100,
                "maximum": 5000
            },
            "level": {
                "type": "string",
                "enum": sorted(LOGS_API_ALLOWED_LOG_LEVELS),
                "description": "Optional log level filter"
            },
            "since": {
                "type": "integer",
                "description": "Optional time filter in minutes ago (for example: 15 for the last 15 minutes)",
                "minimum": 1
            }
        },
        "required": []
    }
}


_COMMON_TOOLS = {
    "submit_prompt_suggestion",
    "report_tool_failure",
}
_TRACE_TOOLS = {
    "get_trace_summary",
    "get_tool_calls_summary",
    "get_tool_calls_page",
    "get_tool_call_detail",
    "get_trace_conversation",
    "get_trace_view",
    "get_service_logs",
}
_FLOW_TOOLS = {
    "create_flow",
    "validate_flow",
    "get_flow_templates",
    "get_current_flow",
    "get_available_agents",
}
_AGENTS_ONLY_DIAGNOSTIC_TOOLS = {
    "agr_curation_query",
    "curation_db_sql",
    "chebi_api_call",
    "quickgo_api_call",
    "go_api_call",
}


def _get_active_tab(context: Optional[ChatContext]) -> str:
    """Resolve active tab from chat context with a safe default."""
    if context and context.active_tab in {"agents", "flows", "agent_workshop"}:
        return context.active_tab
    return "agents"


def _ensure_flow_tools_registered(registry: Any) -> None:
    """Ensure flow tools are present even if the diagnostic registry was reset."""
    if all(registry.has_tool(name) for name in _FLOW_TOOLS):
        return
    try:
        register_flow_tools()
    except Exception:
        logger.exception("Failed to ensure flow tool registration for Agent Studio tools")


def _is_tool_allowed_for_context(tool_name: str, context: Optional[ChatContext]) -> bool:
    """Check whether a tool is allowed for the current tab/context."""
    active_tab = _get_active_tab(context)
    has_trace = bool(context and context.trace_id)

    if tool_name in _COMMON_TOOLS:
        return True

    if tool_name == "update_workshop_prompt_draft":
        return active_tab == "agent_workshop" and bool(context and context.agent_workshop)

    if tool_name in _FLOW_TOOLS:
        return active_tab == "flows"

    if tool_name in _AGENTS_ONLY_DIAGNOSTIC_TOOLS:
        return active_tab == "agents"

    if tool_name == "get_prompt":
        return active_tab in {"agents", "flows", "agent_workshop"}

    if tool_name in _TRACE_TOOLS:
        return active_tab == "agents" or has_trace

    # Unknown/legacy tools are left to existing handlers and validation paths.
    return True


def _tool_scope_error(tool_name: str, context: Optional[ChatContext]) -> Dict[str, Any]:
    """Build a curator-friendly error for disallowed tool usage."""
    active_tab = _get_active_tab(context)
    return {
        "success": False,
        "error": (
            f"Tool '{tool_name}' is not available on the {active_tab} tab. "
            "Use the matching screen for that tool type."
        ),
    }


def _get_all_opus_tools(context: Optional[ChatContext] = None) -> List[dict]:
    """
    Get all tools available to Opus in Anthropic format.

    Combines the suggestion tool, workflow analysis tools, and diagnostic tools.

    Token-Aware Tools (recommended for trace analysis):
    - get_trace_summary: Lightweight overview (~500 tokens)
    - get_tool_calls_summary: All tool calls with summaries (~100 tokens/call)
    - get_tool_calls_page: Paginated full tool calls with filtering
    - get_tool_call_detail: Single tool call detail
    - get_trace_conversation: User query and assistant response
    - get_trace_view: Generic view access with token metadata
    """
    candidate_tools = [
        ANTHROPIC_SUGGESTION_TOOL,
        ANTHROPIC_UPDATE_WORKSHOP_PROMPT_TOOL,
        ANTHROPIC_REPORT_TOOL_FAILURE_TOOL,
        # Token-aware trace analysis tools
        GET_TRACE_SUMMARY_TOOL,
        GET_TOOL_CALLS_SUMMARY_TOOL,
        GET_TOOL_CALLS_PAGE_TOOL,
        GET_TOOL_CALL_DETAIL_TOOL,
        GET_TRACE_CONVERSATION_TOOL,
        GET_TRACE_VIEW_TOOL,
        GET_SERVICE_LOGS_TOOL,
    ]

    tools = [
        tool
        for tool in candidate_tools
        if _is_tool_allowed_for_context(str(tool.get("name", "")), context)
    ]

    # Add diagnostic tools from registry using the same context-aware gate.
    registry = get_diagnostic_tools_registry()
    _ensure_flow_tools_registered(registry)
    diagnostic_tools = []
    for tool in registry.get_all_tools():
        if not _is_tool_allowed_for_context(tool.name, context):
            continue
        diagnostic_tools.append(
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema,
            }
        )
    tools.extend(diagnostic_tools)
    logger.debug('Loaded %s diagnostic tools for Opus', len(diagnostic_tools))

    return tools


def _format_conversation_context(messages: Optional[List[dict]]) -> Optional[str]:
    """
    Format the entire conversation history as a readable string.

    Args:
        messages: List of message dicts with 'role' and 'content' keys

    Returns:
        Formatted conversation string, or None if no messages
    """
    if not messages:
        return None

    lines = []
    for msg in messages:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")

        # Handle content that's a list (tool results)
        if isinstance(content, list):
            # Skip tool result messages - they're not part of the user conversation
            continue

        # Format role label
        role_label = {
            "user": "Curator",
            "assistant": "Opus"
        }.get(role, role.title())

        lines.append(f"{role_label}: {content}")

    return "\n\n".join(lines) if lines else None


def _parse_markdown_heading(line: str) -> Optional[Dict[str, Any]]:
    """Parse a markdown heading line into level/text metadata."""
    match = re.match(r"^\s*(#{1,6})\s+(.+?)\s*$", line)
    if not match:
        return None
    return {
        "level": len(match.group(1)),
        "text": match.group(2).strip(),
    }


def _find_section_bounds(prompt: str, section_heading: str) -> Optional[Dict[str, Any]]:
    """Find byte-range bounds for a markdown section by heading text."""
    target = section_heading.strip().lower()
    if not target:
        return None

    lines = prompt.splitlines(keepends=True)
    if not lines:
        return None

    start_line_idx = None
    start_level = None
    heading_line = ""

    for idx, line in enumerate(lines):
        heading = _parse_markdown_heading(line)
        if not heading:
            continue
        if heading["text"].strip().lower() == target:
            start_line_idx = idx
            start_level = heading["level"]
            heading_line = line if line.endswith("\n") else f"{line}\n"
            break

    if start_line_idx is None or start_level is None:
        return None

    end_line_idx = len(lines)
    for idx in range(start_line_idx + 1, len(lines)):
        heading = _parse_markdown_heading(lines[idx])
        if heading and heading["level"] <= start_level:
            end_line_idx = idx
            break

    start_char = sum(len(line) for line in lines[:start_line_idx])
    end_char = sum(len(line) for line in lines[:end_line_idx])

    return {
        "start_char": start_char,
        "end_char": end_char,
        "heading_line": heading_line,
    }


def _apply_targeted_workshop_edits(
    base_prompt: str,
    edits: List[Any],
) -> Dict[str, Any]:
    """Apply targeted edit operations against a workshop prompt draft."""
    working_prompt = base_prompt
    applied_edits: List[str] = []

    for idx, raw_edit in enumerate(edits, start=1):
        if not isinstance(raw_edit, dict):
            return {
                "success": False,
                "error": f"Edit #{idx} must be an object.",
            }

        operation = str(raw_edit.get("operation", "")).strip()
        if operation not in {"replace_text", "replace_section"}:
            return {
                "success": False,
                "error": f"Edit #{idx} has unsupported operation: {operation or 'missing operation'}",
            }

        replacement_text = raw_edit.get("replacement_text")
        if replacement_text is None:
            replacement_text = ""
        if not isinstance(replacement_text, str):
            return {
                "success": False,
                "error": f"Edit #{idx} replacement_text must be a string.",
            }

        if operation == "replace_text":
            find_text = raw_edit.get("find_text")
            if not isinstance(find_text, str) or not find_text:
                return {
                    "success": False,
                    "error": f"Edit #{idx} requires non-empty find_text for replace_text.",
                }

            occurrence = str(raw_edit.get("occurrence", "first")).strip().lower()
            if occurrence not in {"first", "last", "all"}:
                return {
                    "success": False,
                    "error": f"Edit #{idx} occurrence must be one of: first, last, all.",
                }

            if occurrence == "all":
                count = working_prompt.count(find_text)
                if count == 0:
                    return {
                        "success": False,
                        "error": f"Edit #{idx} could not find text to replace.",
                    }
                working_prompt = working_prompt.replace(find_text, replacement_text)
                applied_edits.append(
                    f"replace_text all occurrences ({count} replacements)"
                )
            else:
                pos = working_prompt.find(find_text) if occurrence == "first" else working_prompt.rfind(find_text)
                if pos < 0:
                    return {
                        "success": False,
                        "error": f"Edit #{idx} could not find text to replace.",
                    }
                working_prompt = (
                    working_prompt[:pos]
                    + replacement_text
                    + working_prompt[pos + len(find_text):]
                )
                applied_edits.append(f"replace_text {occurrence} occurrence")

        elif operation == "replace_section":
            section_heading = raw_edit.get("section_heading")
            if not isinstance(section_heading, str) or not section_heading.strip():
                return {
                    "success": False,
                    "error": f"Edit #{idx} requires section_heading for replace_section.",
                }

            bounds = _find_section_bounds(working_prompt, section_heading)
            if not bounds:
                return {
                    "success": False,
                    "error": f"Edit #{idx} could not find section heading '{section_heading}'.",
                }

            replacement_block = replacement_text
            if not replacement_block.strip():
                return {
                    "success": False,
                    "error": f"Edit #{idx} replacement_text cannot be empty for replace_section.",
                }

            if not _parse_markdown_heading(replacement_block.splitlines()[0] if replacement_block.splitlines() else ""):
                replacement_block = f"{bounds['heading_line']}{replacement_block.lstrip()}"

            if not replacement_block.endswith("\n"):
                replacement_block += "\n"

            start_char = bounds["start_char"]
            end_char = bounds["end_char"]
            working_prompt = (
                working_prompt[:start_char]
                + replacement_block
                + working_prompt[end_char:]
            )
            applied_edits.append(f"replace_section '{section_heading.strip()}'")

    summary = "; ".join(applied_edits) if applied_edits else "No edits applied."
    return {
        "success": True,
        "prompt": working_prompt,
        "applied_edits": applied_edits,
        "summary": summary,
    }


async def _handle_tool_call(
    tool_name: str,
    tool_input: dict,
    context: Optional[ChatContext],
    user_email: str,
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
                "error": f"Tool execution failed: {str(e)}",
            }

    return {
        "success": False,
        "error": f"Unknown tool: {tool_name}",
    }


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

    # Get API key
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

    # Get user info for attribution and prompt personalization
    user_email = user.get("email", user.get("sub", "unknown"))
    user_name = user.get("name", user.get("given_name", None))

    # Set user context for flow tools (create_flow needs user_id)
    # Get database user ID from Cognito token
    try:
        db = next(get_db())
        try:
            db_user = set_global_user_from_cognito(db, user)
            set_workflow_user_context(user_id=db_user.id, user_email=user_email)
            logger.debug('Set workflow context for user %s', db_user.id)
        finally:
            db.close()
    except Exception as e:
        logger.warning('Could not set workflow user context: %s', e)
        # Continue without user context - create_flow will fail gracefully

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
    messages = [
        {"role": msg.role, "content": msg.content}
        for msg in request.messages
    ]

    async def generate_stream():
        """Generate SSE events from Opus with true streaming and tool support."""
        try:
            # Use AsyncAnthropic for non-blocking streaming
            client = anthropic.AsyncAnthropic(api_key=api_key)
            current_messages = messages.copy()

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
                # Track tool uses that need processing after stream completes
                pending_tool_uses = []
                collected_content = []

                # Stream the response using beta API for effort parameter support
                async with client.beta.messages.stream(**api_params) as stream:
                    async for event in stream:
                        if event.type == "content_block_start":
                            if event.content_block.type == "tool_use":
                                # Tool use starting - collect it
                                pending_tool_uses.append({
                                    "id": event.content_block.id,
                                    "name": event.content_block.name,
                                    "input": {},
                                })

                        elif event.type == "content_block_delta":
                            if hasattr(event.delta, "text"):
                                # Stream text deltas immediately
                                sse_event = {
                                    "type": "TEXT_DELTA",
                                    "delta": event.delta.text,
                                }
                                yield f"data: {json.dumps(sse_event)}\n\n"
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
                            # Notify frontend about tool use
                            tool_event = {
                                "type": "TOOL_USE",
                                "tool_name": block.name,
                                "tool_input": block.input,
                            }
                            yield f"data: {json.dumps(tool_event)}\n\n"

                            # Execute the tool
                            tool_result = await _handle_tool_call(
                                tool_name=block.name,
                                tool_input=block.input,
                                context=request.context,
                                user_email=user_email,
                                messages=current_messages,
                            )

                            # Send tool result event to frontend
                            result_event = {
                                "type": "TOOL_RESULT",
                                "tool_name": block.name,
                                "result": tool_result,
                            }
                            yield f"data: {json.dumps(result_event)}\n\n"

                            # Collect for API continuation
                            tool_results_for_api.append({
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": json.dumps(tool_result),
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

            # Send completion event
            yield f"data: {json.dumps({'type': 'DONE'})}\n\n"

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
                error_event = {
                    "type": "CONTEXT_OVERFLOW",
                    "message": "I've hit my token limit for this conversation. The last tool call returned too much data.",
                    "recovery_hint": "Try a lighter-weight tool call: use get_trace_summary instead of full views, get_tool_calls_summary instead of get_tool_calls_page, or use smaller page_size (e.g., 5) with get_tool_calls_page. You can also filter by tool_name to get only specific tool calls.",
                    "suggested_tools": [
                        "get_trace_summary - lightweight overview (~500 tokens)",
                        "get_tool_calls_summary - summaries only, no full results",
                        "get_tool_calls_page with page_size=5 - smaller batches",
                        "get_tool_call_detail - single call at a time"
                    ]
                }
            else:
                _alert_task = asyncio.create_task(
                    notify_tool_failure(
                        error_type=type(e).__name__,
                        error_message=str(e),
                        source="infrastructure",
                        specialist_name="agent_studio_opus",
                        trace_id=request.context.trace_id if request.context else None,
                        session_id=None,
                        curator_id=user_email,
                    )
                )
                logger.error('Anthropic bad request error: %s', e, exc_info=True)
                error_event = {
                    "type": "ERROR",
                    "message": (
                        "Agent Studio couldn't complete that request because it ran into a "
                        "problem sending it to the model. Please review your last step and "
                        "try again. If the problem continues, refresh Agent Studio and retry."
                    ),
                    "error_source": "anthropic",
                }
            yield f"data: {json.dumps(error_event)}\n\n"

        except anthropic.APIError as e:
            _alert_task = asyncio.create_task(
                notify_tool_failure(
                    error_type=type(e).__name__,
                    error_message=str(e),
                    source="infrastructure",
                    specialist_name="agent_studio_opus",
                    trace_id=request.context.trace_id if request.context else None,
                    session_id=None,
                    curator_id=user_email,
                )
            )
            logger.error('Anthropic API error: %s', e, exc_info=True)
            error_event = {
                "type": "ERROR",
                "message": (
                    "The model service had a temporary problem while working on your request. "
                    "Any tool actions started during this turn may already have completed, so "
                    "please check the results before retrying. If needed, try again in a moment."
                ),
                "error_source": "anthropic",
            }
            yield f"data: {json.dumps(error_event)}\n\n"

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
                error_event = {
                    "type": "CONTEXT_OVERFLOW",
                    "message": "I've hit my token limit for this conversation. The last tool call returned too much data.",
                    "recovery_hint": "Try a lighter-weight tool call: use get_trace_summary, get_tool_calls_summary, or get_tool_calls_page with a smaller page_size (e.g., 5). You can also use get_tool_call_detail to fetch one specific call at a time.",
                    "suggested_tools": [
                        "get_trace_summary - lightweight overview (~500 tokens)",
                        "get_tool_calls_summary - summaries only, no full results",
                        "get_tool_calls_page with page_size=5 - smaller batches",
                        "get_tool_call_detail - single call at a time"
                    ]
                }
            else:
                _alert_task = asyncio.create_task(
                    notify_tool_failure(
                        error_type=type(e).__name__,
                        error_message=str(e),
                        source="infrastructure",
                        specialist_name="agent_studio_opus",
                        trace_id=request.context.trace_id if request.context else None,
                        session_id=None,
                        curator_id=user_email,
                    )
                )
                logger.error('Chat stream error: %s', e, exc_info=True)
                error_event = {
                    "type": "ERROR",
                    "message": str(e),
                }
            yield f"data: {json.dumps(error_event)}\n\n"

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


class DirectSubmissionRequest(BaseModel):
    """Request to directly trigger suggestion submission via Opus (bypassing chat UI)."""
    context: Optional[ChatContext] = None
    messages: Optional[List[ChatMessage]] = None


class DirectSubmissionResponse(BaseModel):
    """Response from direct suggestion submission."""
    success: bool
    suggestion_id: Optional[str] = None
    message: str
    error: Optional[str] = None


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
            error=str(e)
        )


def _fetch_trace_for_opus(trace_id: str) -> Optional[str]:
    """
    Fetch trace data from Langfuse and format it for Opus's context.

    Returns a formatted string with the trace summary, or None if fetch fails.
    """
    try:
        from langfuse import Langfuse

        client = Langfuse()

        # Fetch trace details
        trace = client.api.trace.get(trace_id)
        if not trace:
            logger.warning('Trace not found: %s', trace_id)
            return None

        # Fetch observations
        obs_response = client.api.observations.get_many(trace_id=trace_id)
        observations = list(obs_response.data) if hasattr(obs_response, 'data') else []

        # Build the trace summary
        lines = []

        # Basic info
        lines.append(f"**Trace ID:** {trace_id}")
        if hasattr(trace, 'input') and trace.input:
            user_input = trace.input
            if isinstance(user_input, dict):
                user_input = user_input.get('message', user_input.get('query', str(user_input)))
            lines.append(f"**User Query:** {user_input}")

        if hasattr(trace, 'output') and trace.output:
            output = trace.output
            if isinstance(output, dict):
                output = output.get('response', output.get('content', str(output)))
            # Truncate very long outputs
            if len(str(output)) > 2000:
                output = str(output)[:2000] + "... [truncated]"
            lines.append(f"**Final Response:** {output}")

        # Extract agents used and tool calls
        agents_used = set()
        tool_calls = []

        for obs in observations:
            obs_type = getattr(obs, 'type', None)
            obs_name = getattr(obs, 'name', '')

            # Identify agents from generation observations
            if obs_type == 'GENERATION':
                # Try to identify the agent
                for agent_pattern in ['supervisor', 'gene_extraction', 'gene_extractor', 'ask_gene_extractor_', 'gene_expression', 'allele_variant_extraction', 'allele_extractor', 'ask_allele_extractor_', 'disease_extraction', 'disease_extractor', 'ask_disease_extractor_', 'chemical_extraction', 'chemical_extractor', 'ask_chemical_extractor_', 'phenotype_extraction', 'phenotype_extractor', 'phenotype_specialist', 'ask_phenotype_extractor_', 'ask_phenotype_', 'pdf_specialist', 'gene', 'allele',
                                     'disease', 'chemical', 'gene_ontology', 'go_annotations',
                                     'orthologs', 'ontology_mapping', 'chat_output',
                                     'csv_formatter', 'tsv_formatter', 'json_formatter']:
                    if agent_pattern in obs_name.lower():
                        agents_used.add(agent_pattern)
                        break

            # Capture tool calls from spans
            if obs_type == 'SPAN' and not obs_name.startswith('transfer_to_'):
                if obs_name not in ['supervisor', 'agent_run', '']:
                    tool_input = getattr(obs, 'input', None)
                    tool_output = getattr(obs, 'output', None)

                    # Format input
                    input_str = ""
                    if tool_input:
                        if isinstance(tool_input, dict):
                            input_str = json.dumps(tool_input, indent=2)[:500]
                        else:
                            input_str = str(tool_input)[:500]

                    # Format output (truncate)
                    output_str = ""
                    if tool_output:
                        if isinstance(tool_output, str):
                            output_str = tool_output[:300]
                        else:
                            output_str = str(tool_output)[:300]

                    tool_calls.append({
                        'name': obs_name,
                        'input': input_str,
                        'output': output_str + ("..." if len(str(tool_output or "")) > 300 else "")
                    })

        if agents_used:
            lines.append(f"**Agents Involved:** {', '.join(sorted(agents_used))}")

        if tool_calls:
            lines.append("\n**Tool Calls:**")
            for i, tc in enumerate(tool_calls[:15], 1):  # Limit to 15 tool calls
                lines.append(f"\n{i}. **{tc['name']}**")
                if tc['input']:
                    lines.append(f"   Input: {tc['input']}")
                if tc['output']:
                    lines.append(f"   Output: {tc['output']}")

            if len(tool_calls) > 15:
                lines.append(f"\n... and {len(tool_calls) - 15} more tool calls")

        return "\n".join(lines)

    except Exception as e:
        logger.error('Failed to fetch trace for Opus: %s', e, exc_info=True)
        return None


def _build_opus_system_prompt(
    context: Optional[ChatContext],
    user_name: Optional[str] = None,
    user_email: Optional[str] = None,
) -> str:
    """Build the system prompt for Opus based on UI context and user identity."""

    # Check if this user is a developer (configured in .env for security)
    developer_emails = os.getenv("PROMPT_EXPLORER_DEVELOPER_EMAILS", "").lower().split(",")
    developer_emails = [e.strip() for e in developer_emails if e.strip()]
    is_developer = user_email and user_email.lower() in developer_emails

    # User greeting - inject for everyone
    user_greeting = ""
    if user_name:
        user_greeting = f"\n\n**You are speaking with: {user_name}**\n"
        if is_developer:
            # Developer-specific prompt (content from .env for security)
            dev_prompt = os.getenv(
                "PROMPT_EXPLORER_DEVELOPER_PROMPT",
                "This user is a developer on the AI curation project. They may ask you to help with testing, debugging, or technical tasks beyond standard curator support. You can assist with these requests while maintaining your helpful assistant demeanor."
            )
            user_greeting += f"\n{dev_prompt}\n"

    base_prompt = _load_agent_studio_system_prompt_template().replace(
        "{{USER_GREETING}}",
        user_greeting,
    )

    if context:
        additions = []
        workshop_draft_tools: Optional[List[str]] = None

        if context.active_tab == "agent_workshop" and context.agent_workshop:
            workshop = context.agent_workshop
            workshop_draft_tools = workshop.draft_tool_ids or []
            draft_prompt = workshop.prompt_draft or ""
            selected_group_prompt = workshop.selected_group_prompt_draft or ""
            truncated = ""
            group_truncated = ""
            max_prompt_chars = 12000
            max_group_prompt_chars = 6000
            if len(draft_prompt) > max_prompt_chars:
                draft_prompt = draft_prompt[:max_prompt_chars]
                truncated = f"\n\n[Truncated to first {max_prompt_chars} chars for context.]"
            if len(selected_group_prompt) > max_group_prompt_chars:
                selected_group_prompt = selected_group_prompt[:max_group_prompt_chars]
                group_truncated = f"\n\n[Truncated to first {max_group_prompt_chars} chars for context.]"

            selected_group_prompt_block = ""
            if workshop.selected_group_id and selected_group_prompt:
                selected_group_prompt_block = f"""

<workshop_selected_group_prompt group="{workshop.selected_group_id}">
{selected_group_prompt}
</workshop_selected_group_prompt>{group_truncated}"""

            model_catalog_lines: List[str] = []
            try:
                for model in sorted(
                    [
                        model
                        for model in list_model_definitions()
                        if bool(getattr(model, "curator_visible", True))
                    ],
                    key=lambda model: (not bool(model.default), model.name.lower()),
                ):
                    reasoning_label = (
                        f"{', '.join(model.reasoning_options)} (default: {model.default_reasoning or 'none'})"
                        if model.reasoning_options
                        else "n/a"
                    )
                    model_catalog_lines.append(
                        f"- {model.name} [{model.model_id}]: "
                        f"{(model.guidance or model.description or '').strip() or 'No guidance configured.'} "
                        f"(reasoning: {reasoning_label})"
                    )
            except Exception:
                model_catalog_lines = []

            model_catalog_text = "\n".join(model_catalog_lines) if model_catalog_lines else "- Model catalog unavailable."

            additions.append(f"""
<agent_workshop_context>
## Current Context: Agent Workshop

The curator is actively iterating an agent draft in Agent Workshop.

- Template source: {workshop.template_name or workshop.template_source or 'Unknown'}
- Custom agent: {workshop.custom_agent_name or workshop.custom_agent_id or 'Unsaved draft'}
- Include group rules: {"Yes" if workshop.include_group_rules else "No"}
- Selected group: {workshop.selected_group_id or "None"}
- Has group prompt overrides: {"Yes" if workshop.has_group_prompt_overrides else "No"}
- Group override count: {workshop.group_prompt_override_count or 0}
- Template prompt stale: {"Yes" if workshop.template_prompt_stale else "No"}
- Template exists: {"Yes" if workshop.template_exists is not False else "No"}
- Draft attached tools: {", ".join(workshop_draft_tools) if workshop_draft_tools else "None"}
- Draft model: {workshop.draft_model_id or "Not set"}
- Draft reasoning: {workshop.draft_model_reasoning or "Not set"}

Agent Workshop model recommendation defaults:
- Use `openai/gpt-oss-120b` for fast database lookup and validation workflows.
- Use `gpt-5.4` with `medium` reasoning for difficult PDF extraction and deep reasoning.
- Use `gpt-5.4-nano` for fast iterative drafting and balanced quality/speed.

Configured model options:
{model_catalog_text}

Use this workshop context to give concrete prompt-engineering feedback, especially:
1. how to improve the draft prompt structure and specificity,
2. what to test next in flow execution (and when to compare with the template-source prompt),
3. how group rules may interact with the current draft.
4. proactively identify concrete prompt improvements during normal conversation and suggest them.
5. before making any draft update call, ask for permission in plain language (e.g., "Want me to apply this as a targeted edit?").
6. after clear approval, call `update_workshop_prompt_draft`:
   - set `target_prompt="main"` for general/global draft behavior changes,
   - set `target_prompt="group"` for group-specific wording/rules and include `target_group_id`,
   - full rewrite: `apply_mode="replace"` and provide `updated_prompt`,
   - small scoped tweaks: `apply_mode="targeted_edit"` and provide `edits`.
7. when the curator is in Agent Workshop, do NOT call flow-only tools (`get_current_flow`, `get_available_agents`, `get_flow_templates`, `create_flow`, `validate_flow`) unless they explicitly switch to Flows.
8. after a curator applies a prompt update, verify the current `<workshop_prompt_draft>` contains the intended change and provide a quick quality review.
9. when proposing or applying prompt edits, use this distilled OpenAI-style prompt playbook:
   - put core instructions first, then separate context/examples with clear delimiters (`###` sections or triple quotes),
   - make directions specific and measurable (length, format, required fields, decision rules),
   - prefer explicit output schemas and short examples over vague prose,
   - replace vague wording ("brief", "not too much") with concrete bounds,
   - avoid "don't do X" alone; add the preferred behavior ("do Y instead"),
   - start with minimal/targeted edits first; escalate to larger rewrites only when needed,
   - for extraction/factual behavior, prioritize deterministic wording over creative language.
10. in reviews, explicitly check whether the updated prompt follows the playbook above and call out any misses.
11. choose the right target for edits:
   - use main prompt updates for behavior that should apply across all groups,
   - use group prompt updates only for organism/group-specific exceptions or conventions.

<workshop_prompt_draft>
{draft_prompt}
</workshop_prompt_draft>{truncated}
{selected_group_prompt_block}

Prompt injection note:
- Structured output instructions are inserted near the first `## ` heading.
- If the draft lacks `## ` headings, insertion happens at the top.
</agent_workshop_context>""")

        if context.selected_agent_id:
            # Get the agent info to provide context
            service = get_prompt_catalog()
            agent = service.get_agent(context.selected_agent_id)
            if agent:
                tools_label = "Tools this agent can use"
                tools_for_context = agent.tools
                # In Agent Workshop, prefer the live draft tool attachments from UI context.
                if context.active_tab == "agent_workshop" and workshop_draft_tools is not None:
                    tools_label = "Tools attached to current workshop draft"
                    tools_for_context = workshop_draft_tools

                additions.append(f"""
## Current Context

The curator is viewing the **{agent.agent_name}** agent.

**Agent Description:** {agent.description}

**{tools_label}:** {', '.join(tools_for_context) if tools_for_context else 'None'}

**Has group-specific rules:** {'Yes' if agent.has_group_rules else 'No'}""")

                # Include the prompt content based on view mode
                if context.selected_group_id and context.selected_group_id in agent.group_rules:
                    group_rule = agent.group_rules[context.selected_group_id]
                    additions.append(f"""
### Currently Viewing: {context.selected_group_id}-Specific Rules

The curator is looking at the group-specific rules for {context.selected_group_id}. Here are those rules:

<group_rules group="{context.selected_group_id}">
{group_rule.content}
</group_rules>

And here is the base prompt that these rules extend:

<base_prompt agent="{agent.agent_id}">
{agent.base_prompt}
</base_prompt>""")
                else:
                    # Just viewing the base prompt
                    additions.append(f"""
### Currently Viewing: Base Prompt

<base_prompt agent="{agent.agent_id}">
{agent.base_prompt}
</base_prompt>""")

                    if agent.has_group_rules:
                        available_groups = list(agent.group_rules.keys())
                        additions.append(f"""
This agent has group-specific rules available for: {', '.join(available_groups)}. The curator can select a group to see how the base prompt is customized.""")

        if context.trace_id:
            # Provide lightweight trace context with tool usage instructions
            from src.lib.agent_studio.context import prepare_trace_context
            trace_context = prepare_trace_context(context.trace_id)
            if trace_context:
                additions.append(trace_context)

        # Add flow context when user is on the Flows tab
        if context.active_tab == 'flows':
            flow_context = """
<flow_context>
## Current Context: Flow Builder

The curator is designing a curation flow - a visual pipeline that chains agents together to process documents.

<critical_instruction>
**MANDATORY: ALWAYS call `get_current_flow` tool FIRST before any flow discussion.**

This tool returns:
- Flow in **execution order** (following edges from entry node, not canvas placement order)
- Accurate step numbering based on actual execution sequence
- Disconnected nodes flagged as warnings
- Clean markdown representation

**NEVER** reference flow structure without calling this tool first.
</critical_instruction>

<responsibilities>
**Your role:**
1. **Verify** - Check flow structure against validation checklist
2. **Suggest** - Recommend better ordering, missing steps, optimizations
3. **Explain** - Help curators understand what each agent does
4. **Debug** - Identify problems in flow structure or configuration
</responsibilities>

<validation_checklist>
**When asked to verify, check for:**
1. **Initial Instructions MUST Be First** - Every flow MUST start with the Initial Instructions node (task_input). This is the entry point that defines what the curator wants to accomplish.
2. **All Nodes Connected** - Disconnected nodes = steps that won't execute
3. **Logical Data Flow** - Each agent's output feeds appropriately to the next
4. **Custom Instructions Redundancy** - For EACH node with custom instructions:
   - Call `get_prompt(agent_id)` to fetch the base prompt
   - Compare custom instructions to base prompt content
   - Flag any duplication (phrases, instructions, or concepts already in base)
5. **Missing Agents** - Any important processing steps absent?
6. **Redundant Steps** - Any agents called unnecessarily?

**CRITICAL for item 4:** You MUST actually call `get_prompt` for each agent with custom instructions to perform the comparison. Do NOT skip this step or guess based on agent name alone.
</validation_checklist>

<flow_design_guidance>
## Flow Design Best Practices

**Every flow follows this pattern:**
1. **Initial Instructions** (REQUIRED FIRST STEP) - Define the curation task
2. **Extraction/Verification agents** - Process the document
3. **Output agent** (if exporting data) - Format results as CSV, TSV, or JSON

**Initial Instructions should specify:**
- What to extract (e.g., "Extract all alleles mentioned in this paper")
- What data categories to capture (e.g., "For each allele, capture: parent gene symbol, allele identifier, phenotype description")
- Any validation requirements (e.g., "Verify allele IDs against the Alliance database")

**When exporting to file (CSV/TSV/JSON):**
- The Initial Instructions should define WHAT data to collect
- The formatter agent (csv_formatter, tsv_formatter, json_formatter) should define HOW to format it
- Formatter custom instructions should specify column headers matching the data defined in Initial Instructions

**Example flow for allele extraction:**
1. **Initial Instructions**: "Extract alleles from this paper. For each allele, capture: parent gene symbol, allele identifier, and phenotype. Verify identifiers against the database."
2. **PDF Extraction**: Extract relevant sections
3. **Allele Verification**: Validate allele data against Alliance database
4. **CSV Formatter**: "Export with columns: parent_gene, allele_id, phenotype"
</flow_design_guidance>

<output_format>
**Structure your verification feedback as:**
- ✅ [What's correct] - Brief explanation
- ⚠️ [Warning] - Issue that may cause problems
- ❌ [Problem] - Must be fixed before flow will work correctly
- 💡 [Suggestion] - Optional improvement
</output_format>
</flow_context>"""

            additions.append(flow_context)

        if additions:
            base_prompt += "\n" + "\n".join(additions)

    return base_prompt


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
    from src.lib.agent_studio.catalog_service import get_all_tools
    try:
        tools = get_all_tools()
        return {"tools": tools}
    except Exception as e:
        logger.error('Failed to get tools: %s', e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


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
    from src.lib.agent_studio.catalog_service import get_tool_details, get_tool_for_agent
    try:
        if agent_id:
            # Get tool with agent-specific context
            tool = get_tool_for_agent(tool_id, agent_id)
        else:
            # Get generic tool details
            tool = get_tool_details(tool_id)

        if not tool:
            raise HTTPException(
                status_code=404,
                detail=f"Tool '{tool_id}' not found"
            )
        return {"tool": tool}
    except HTTPException:
        raise
    except Exception as e:
        logger.error('Failed to get tool details: %s', e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
