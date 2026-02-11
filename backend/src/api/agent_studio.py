"""Agent Studio API endpoints.

Provides endpoints for the Agent Studio feature:
- GET /catalog - Get all agent prompts organized by category
- POST /chat - Stream a conversation with Opus 4.5
- GET /trace/{trace_id}/context - Get enriched trace context
"""

import json
import logging
import os
import re
import asyncio
import uuid
from typing import Any, Dict, List, Optional

import anthropic
import boto3
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Path
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .auth import get_auth_dependency
from src.lib.agent_studio import (
    get_prompt_catalog,
    PromptCatalog,
    MODRuleInfo,
    PromptInfo,
    AgentPrompts,
    ChatMessage,
    ChatContext,
    TraceContext,
    TraceContextError,
    TraceNotFoundError,
    LangfuseUnavailableError,
    PromptSuggestion,
    SuggestionType,
    submit_suggestion_sns,
    SUBMIT_SUGGESTION_TOOL,
    # Flow tools user context management
    set_workflow_user_context,
    clear_workflow_user_context,
    # Flow context for get_current_flow tool
    set_current_flow_context,
    clear_current_flow_context,
)
from src.lib.agent_studio.diagnostic_tools import get_diagnostic_tools_registry
from src.lib.agent_studio.custom_agent_service import (
    CustomAgentAccessError,
    CustomAgentNotFoundError,
    get_custom_agent_mod_prompt,
    get_custom_agent_for_user,
    list_custom_agents_for_user,
    make_custom_agent_id,
    parse_custom_agent_id,
)
from src.lib.agent_studio.catalog_service import get_agent_by_id, get_agent_metadata
from src.lib.agent_studio.streaming import flatten_runner_event as _flatten_runner_event
from src.lib.context import set_current_session_id, set_current_user_id
from src.lib.openai_agents import run_agent_streamed
from src.models.sql import get_db
from src.services.user_service import set_global_user_from_cognito

logger = logging.getLogger(__name__)

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
    """Request for a combined prompt (base + MOD)."""
    agent_id: str
    mod_id: str


class CombinedPromptResponse(BaseModel):
    """Response with combined prompt."""
    agent_id: str
    mod_id: str
    combined_prompt: str


class PromptPreviewResponse(BaseModel):
    """Response with resolved prompt text for preview/testing."""

    agent_id: str
    prompt: str
    mod_id: Optional[str] = None
    source: str
    parent_agent_key: Optional[str] = None
    include_mod_rules: Optional[bool] = None


class AgentTestRequest(BaseModel):
    """Request for isolated agent test streaming."""

    input: str
    mod_id: Optional[str] = None
    document_id: Optional[str] = None
    session_id: Optional[str] = None


class ManualSuggestionRequest(BaseModel):
    """Request to manually submit a prompt suggestion."""
    agent_id: Optional[str] = None  # Optional for trace-based/general feedback
    suggestion_type: str  # Will be validated against SuggestionType
    summary: str
    detailed_reasoning: str
    proposed_change: Optional[str] = None
    mod_id: Optional[str] = None
    trace_id: Optional[str] = None  # When provided without agent_id, this is conversation-based feedback


class SuggestionResponse(BaseModel):
    """Response after submitting a suggestion."""
    status: str
    suggestion_id: str
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


def _merge_custom_agents_into_catalog(
    catalog: PromptCatalog,
    auth_user: Any,
    db: Any,
) -> PromptCatalog:
    """Return catalog augmented with the current user's active custom agents."""
    if not isinstance(auth_user, dict) or not hasattr(db, "query"):
        return catalog

    from src.lib.agent_studio.catalog_service import AGENT_REGISTRY, expand_tools_for_agent

    db_user = set_global_user_from_cognito(db, auth_user)
    custom_agents = list_custom_agents_for_user(db, db_user.id)
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
        parent_entry = AGENT_REGISTRY.get(custom.parent_agent_key, {})
        parent_name = parent_entry.get("name", custom.parent_agent_key)
        category = parent_entry.get("category", "Custom")
        tools = expand_tools_for_agent(custom.parent_agent_key, parent_entry.get("tools", []))
        parent_prompt_info = parent_agents_by_id.get(custom.parent_agent_key)
        parent_mod_rules = parent_prompt_info.mod_rules if parent_prompt_info else {}
        raw_overrides = getattr(custom, "mod_prompt_overrides", None) or {}
        normalized_overrides = {
            str(mod_id).strip().upper(): content
            for mod_id, content in raw_overrides.items()
            if str(mod_id).strip() and isinstance(content, str) and content.strip()
        }
        effective_mod_rules: Dict[str, MODRuleInfo] = {}

        for mod_id, parent_mod_rule in parent_mod_rules.items():
            override_content = normalized_overrides.get(mod_id.upper())
            effective_mod_rules[mod_id] = MODRuleInfo(
                mod_id=mod_id,
                content=override_content if override_content else parent_mod_rule.content,
                source_file=parent_mod_rule.source_file,
                description=parent_mod_rule.description,
                prompt_id=parent_mod_rule.prompt_id,
                prompt_version=parent_mod_rule.prompt_version,
                created_at=parent_mod_rule.created_at,
                created_by=parent_mod_rule.created_by,
            )

        prompt_info = PromptInfo(
            agent_id=make_custom_agent_id(custom.id),
            agent_name=custom.name,
            description=custom.description or f"Custom agent based on {parent_name}",
            base_prompt=custom.custom_prompt,
            source_file=f"custom_agent:{custom.id}",
            has_mod_rules=bool(effective_mod_rules),
            mod_rules=effective_mod_rules,
            tools=tools,
            subcategory="My Custom Agents",
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
        custom_agents = list_custom_agents_for_user(db, db_user.id)
        for custom in custom_agents:
            parent_entry = AGENT_REGISTRY.get(custom.parent_agent_key, {})
            category = parent_entry.get("category", "Custom")
            custom_id = make_custom_agent_id(custom.id)

            agents[custom_id] = AgentMetadata(
                name=custom.name,
                icon=custom.icon or "❓",
                category=category,
                subcategory="My Custom Agents",
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
    description="Returns all agent prompts organized by category, including MOD-specific rules.",
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
        logger.error(f"Failed to get prompt catalog: {e}", exc_info=True)
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
        logger.error(f"Failed to refresh prompt catalog: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/catalog/combined",
    response_model=CombinedPromptResponse,
    summary="Get combined prompt",
    description="Returns the base prompt with MOD-specific rules injected.",
)
async def get_combined_prompt(
    request: CombinedPromptRequest,
    user: Dict[str, Any] = get_auth_dependency()
):
    """Get a combined prompt (base + MOD rules)."""
    try:
        service = get_prompt_catalog()
        combined = service.get_combined_prompt(request.agent_id, request.mod_id)
        if combined is None:
            raise HTTPException(
                status_code=404,
                detail=f"Agent '{request.agent_id}' or MOD '{request.mod_id}' not found"
            )
        return CombinedPromptResponse(
            agent_id=request.agent_id,
            mod_id=request.mod_id,
            combined_prompt=combined,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get combined prompt: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get(
    "/prompt-preview/{agent_id}",
    response_model=PromptPreviewResponse,
    summary="Get prompt preview",
    description="Returns the effective prompt text for a system or custom agent.",
)
async def get_prompt_preview(
    agent_id: str = Path(..., description="Agent ID (system ID or custom ca_<uuid>)"),
    mod_id: Optional[str] = None,
    user: Dict[str, Any] = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> PromptPreviewResponse:
    """Get prompt preview for system or custom agents."""
    try:
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

            if mod_id and custom_agent.include_mod_rules:
                mod_prompt = get_custom_agent_mod_prompt(
                    parent_agent_key=custom_agent.parent_agent_key,
                    mod_id=mod_id,
                    mod_prompt_overrides=custom_agent.mod_prompt_overrides,
                )
                if mod_prompt:
                    preview = (
                        f"{preview}\n\n## MOD-SPECIFIC RULES\n\n"
                        f"The following rules are specific to {mod_id}:\n\n"
                        f"{mod_prompt}\n\n## END MOD-SPECIFIC RULES\n"
                    )

            return PromptPreviewResponse(
                agent_id=agent_id,
                prompt=preview,
                mod_id=mod_id,
                source="custom_agent",
                parent_agent_key=custom_agent.parent_agent_key,
                include_mod_rules=custom_agent.include_mod_rules,
            )

        # System agent preview
        service = get_prompt_catalog()
        if mod_id:
            prompt = service.get_combined_prompt(agent_id, mod_id)
            if prompt is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"Agent '{agent_id}' or MOD '{mod_id}' not found",
                )
        else:
            agent = service.get_agent(agent_id)
            if not agent:
                raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")
            prompt = agent.base_prompt

        return PromptPreviewResponse(
            agent_id=agent_id,
            prompt=prompt,
            mod_id=mod_id,
            source="system_agent",
            parent_agent_key=None,
            include_mod_rules=None,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get prompt preview for '{agent_id}': {e}", exc_info=True)
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
        metadata = get_agent_metadata(resolved_agent_id)
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
    active_groups = [request.mod_id] if request.mod_id else []

    set_current_session_id(session_id)
    set_current_user_id(str(user_sub))

    try:
        test_agent = get_agent_by_id(
            resolved_agent_id,
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
            logger.warning(f"Agent test stream cancelled: agent_id={agent_id}")
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
            logger.error(f"Agent test stream error for {agent_id}: {exc}", exc_info=True)
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
# Chat Endpoints (Opus 4.5)
# ============================================================================

# Convert tool definition to Anthropic format
ANTHROPIC_SUGGESTION_TOOL = {
    "name": SUBMIT_SUGGESTION_TOOL["name"],
    "description": SUBMIT_SUGGESTION_TOOL["description"],
    "input_schema": SUBMIT_SUGGESTION_TOOL["input_schema"],
}

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
    "description": "Get a specific analysis view with token metadata. Use for specialized views not covered by the primary tools. Available views: token_analysis, agent_context, pdf_citations, document_hierarchy, agent_configs, mod_context, trace_summary.",
    "input_schema": {
        "type": "object",
        "properties": {
            "trace_id": {
                "type": "string",
                "description": "Langfuse trace ID"
            },
            "view_name": {
                "type": "string",
                "enum": ["token_analysis", "agent_context", "pdf_citations", "document_hierarchy", "agent_configs", "mod_context", "trace_summary"],
                "description": "Which view to fetch"
            }
        },
        "required": ["trace_id", "view_name"]
    }
}

GET_DOCKER_LOGS_TOOL = {
    "name": "get_docker_logs",
    "description": "Retrieve Docker container logs for troubleshooting. Use this when curators report errors or unexpected behavior to help diagnose issues.",
    "input_schema": {
        "type": "object",
        "properties": {
            "container": {
                "type": "string",
                "enum": ["backend", "frontend", "weaviate", "postgres"],
                "description": "Container name (default: backend)",
                "default": "backend"
            },
            "lines": {
                "type": "integer",
                "description": "Number of recent log lines (default: 2000, min: 100, max: 5000)",
                "default": 2000,
                "minimum": 100,
                "maximum": 5000
            }
        },
        "required": []
    }
}


def _get_all_opus_tools() -> List[dict]:
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
    tools = [
        ANTHROPIC_SUGGESTION_TOOL,
        # Token-aware trace analysis tools (recommended)
        GET_TRACE_SUMMARY_TOOL,
        GET_TOOL_CALLS_SUMMARY_TOOL,
        GET_TOOL_CALLS_PAGE_TOOL,
        GET_TOOL_CALL_DETAIL_TOOL,
        GET_TRACE_CONVERSATION_TOOL,
        GET_TRACE_VIEW_TOOL,
        GET_DOCKER_LOGS_TOOL,
    ]

    # Add diagnostic tools from registry
    registry = get_diagnostic_tools_registry()
    diagnostic_tools = registry.get_anthropic_tools()
    tools.extend(diagnostic_tools)
    logger.debug(f"Loaded {len(diagnostic_tools)} diagnostic tools for Opus")

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
        get_docker_logs,
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
                "help": "Valid view_name values: token_analysis, agent_context, pdf_citations, document_hierarchy, agent_configs, mod_context, trace_summary"
            }
        return await get_trace_view(trace_id=trace_id, view_name=view_name)

    elif tool_name == "get_docker_logs":
        container = tool_input.get("container", "backend")
        lines = tool_input.get("lines", 2000)

        result = await get_docker_logs(container=container, lines=lines)
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
            mod_id=context.selected_mod_id if context else None,
            trace_id=context.trace_id if context else None,
            conversation_context=conversation_context,
        )

        # Submit via SNS
        result = await submit_suggestion_sns(
            suggestion=suggestion,
            submitted_by=user_email,
            source="opus_tool",
        )

        # Check if SNS actually succeeded or fell back to logging
        sns_status = result.get("sns_status")
        if sns_status == "failed":
            return {
                "success": True,
                "suggestion_id": result["suggestion_id"],
                "message": "Suggestion recorded locally. SNS delivery failed - the team will review from logs.",
                "sns_failed": True,
            }

        return {
            "success": True,
            "suggestion_id": result["suggestion_id"],
            "message": "Suggestion submitted successfully. The development team will review it.",
        }

    # Check if this is a diagnostic tool from the registry
    registry = get_diagnostic_tools_registry()
    tool_def = registry.get_tool(tool_name)

    if tool_def:
        # Execute the diagnostic tool handler
        logger.debug(f"Executing diagnostic tool: {tool_name}")
        try:
            result = tool_def.handler(**tool_input)
            return result
        except Exception as e:
            logger.error(f"Diagnostic tool {tool_name} failed: {e}", exc_info=True)
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
    summary="Chat with Opus 4.5",
    description="""
    Stream a conversation with Claude Opus 4.5 about prompts.

    Opus can discuss prompts, suggest improvements, and submit suggestions
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
    """Stream a conversation with Opus 4.5 with tool support."""
    import anthropic

    # Get API key
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        logger.error("ANTHROPIC_API_KEY environment variable not set")
        raise HTTPException(
            status_code=500,
            detail="Chat service not properly configured"
        )

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
            logger.debug(f"Set workflow context for user {db_user.id}")
        finally:
            db.close()
    except Exception as e:
        logger.warning(f"Could not set workflow user context: {e}")
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
        logger.debug(f"Set flow context: {flow_context.get('flow_name')}")
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
                "model": "claude-opus-4-5-20251101",
                "betas": ["effort-2025-11-24"],
                "max_tokens": 16384,
                "system": system_prompt,
                "messages": current_messages,
                "tools": _get_all_opus_tools(),
                "output_config": {"effort": "medium"},
            }
            logger.info("Opus chat using effort='medium' for balanced quality/cost")

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

                        elif event.type == "content_block_stop":
                            current_block_type = None

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
                logger.warning(f"Context overflow detected: {e}")
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
                logger.error(f"Anthropic bad request error: {e}", exc_info=True)
                error_event = {
                    "type": "ERROR",
                    "message": f"Bad request: {str(e)}",
                }
            yield f"data: {json.dumps(error_event)}\n\n"

        except anthropic.APIError as e:
            logger.error(f"Anthropic API error: {e}", exc_info=True)
            error_event = {
                "type": "ERROR",
                "message": f"API error: {str(e)}",
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
                logger.warning(f"Context overflow (general exception): {e}")
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
                logger.error(f"Chat stream error: {e}", exc_info=True)
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
            f"AI-Assisted Suggestion Submission Failed",
            f"",
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
        logger.info(f"Error notification sent to SNS: {response['MessageId']}")

    except Exception as e:
        logger.error(f"Failed to send error notification via SNS: {e}", exc_info=True)


async def _process_suggestion_background(
    messages: List[Dict[str, str]],
    system_prompt: str,
    context: Optional[ChatContext],
    user_email: str,
    api_key: str,
) -> None:
    """
    Background task that processes the Opus suggestion submission.

    This runs after the HTTP response has been sent to the user.
    On success, sends the suggestion via SNS.
    On failure, sends an error notification via SNS.
    """
    try:
        logger.info(f"[Background] Starting Opus suggestion processing for {user_email}")

        # Call Opus synchronously to get the tool call
        client = anthropic.Anthropic(api_key=api_key)

        response = client.messages.create(
            model="claude-opus-4-5-20251101",
            max_tokens=4096,
            system=system_prompt,
            messages=messages,
            tools=[ANTHROPIC_SUGGESTION_TOOL],
            tool_choice={"type": "tool", "name": "submit_prompt_suggestion"},
        )

        # Extract tool use from response
        tool_use_block = None
        for block in response.content:
            if block.type == "tool_use" and block.name == "submit_prompt_suggestion":
                tool_use_block = block
                break

        if not tool_use_block:
            error_msg = "Opus did not call submit_prompt_suggestion despite forced tool choice"
            logger.error(f"[Background] {error_msg}")
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
            logger.info(f"[Background] Suggestion submitted successfully for {user_email}: {tool_result.get('suggestion_id')}")
        else:
            error_msg = tool_result.get("error", "Unknown error during tool execution")
            logger.error(f"[Background] Tool execution failed: {error_msg}")
            _send_error_notification_sns(user_email, error_msg, context)

    except anthropic.APIError as e:
        error_msg = f"Anthropic API error: {str(e)}"
        logger.error(f"[Background] {error_msg}", exc_info=True)
        _send_error_notification_sns(user_email, error_msg, context)

    except Exception as e:
        error_msg = f"Unexpected error: {str(e)}"
        logger.error(f"[Background] {error_msg}", exc_info=True)
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
            if request.context.prompt_workshop and request.context.prompt_workshop.custom_agent_name:
                context_description.append(
                    f'the Prompt Workshop draft for "{request.context.prompt_workshop.custom_agent_name}"'
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
            logger.info(f"[AI-Assisted Submit] Received {len(messages)} messages from frontend")
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

        logger.info(f"[AI-Assisted Submit] Background task spawned for {user_email}")
        return DirectSubmissionResponse(
            success=True,
            message="Submission sent",
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Direct submission error: {e}", exc_info=True)
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
            logger.warning(f"Trace not found: {trace_id}")
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
                for agent_pattern in ['supervisor', 'gene_expression', 'pdf_specialist', 'gene', 'allele',
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
        logger.error(f"Failed to fetch trace for Opus: {e}", exc_info=True)
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

    base_prompt = f"""<role>
You are a senior prompt engineering consultant with expertise in:
- Multi-agent AI system design and debugging
- Translating technical AI concepts for domain experts
- Systematic trace analysis and root cause identification

You are embedded in the Prompt Explorer tool at the Alliance of Genome Resources. You help curators understand, analyze, and improve the AI prompts that power their curation assistant.{user_greeting}
</role>

<context>
## The Alliance of Genome Resources

The Alliance of Genome Resources (AGR) is a consortium of Model Organism Databases (MODs) that curate biological knowledge from scientific literature:

- **WormBase (WB)**: C. elegans (nematode worm)
- **FlyBase (FB)**: Drosophila melanogaster (fruit fly)
- **MGI**: Mus musculus (mouse)
- **RGD**: Rattus norvegicus (rat)
- **SGD**: Saccharomyces cerevisiae (yeast)
- **ZFIN**: Danio rerio (zebrafish)

Each MOD has organism-specific annotation conventions. The AI curation system respects these via MOD-specific rule files injected into base prompts.

## The Curators You're Helping

Curators are PhD-level scientists with deep expertise in genetics, molecular biology, and their model organism. They extract structured biological facts from papers: gene expression patterns, disease associations, allele phenotypes, protein interactions, etc.

**Curators know well:** Biology, genetics, organism nomenclature, valid annotations vs. speculation, experimental evidence nuances, when AI output is biologically wrong.

**Curators may be less familiar with:** Prompt engineering techniques, why phrasings affect AI behavior, instruction structuring, prompt design tradeoffs.

Your job: Bridge this gap by translating prompt engineering concepts into biological curation terms.
</context>

<architecture>
## The AI Curation System Architecture

The system uses a multi-agent architecture:

**Routing Layer:**
- **Supervisor**: Orchestrator that routes curator queries to appropriate specialists.

**Extraction Agents (work with uploaded papers):**
- **Gene Expression Specialist**: Extracts where, when, and how genes are expressed.
- **PDF Specialist**: Answers general questions about PDF documents.
- **Formatter**: Converts natural language into structured JSON matching the Alliance data model.

**Database Query Agents (query external sources):**
- **Gene Agent**: Queries Alliance Curation Database for gene information
- **Allele Agent**: Queries for allele/variant information
- **Disease Agent**: Queries Disease Ontology (DOID)
- **Chemical Agent**: Queries ChEBI chemical ontology
- **GO Term Agent**: Queries Gene Ontology terms and hierarchy
- **GO Annotations Agent**: Retrieves existing GO annotations for genes
- **Orthologs Agent**: Queries orthology relationships across species

**Validation Agents:**
- **Ontology Mapping**: Maps free-text labels to ontology term IDs.

## MOD-Specific Rules

Many agents have MOD-specific rule files (e.g., WormBase anatomy terms WBbt, FlyBase allele nomenclature). When a curator selects their MOD, these rules are injected into the base prompt. Understanding base prompt + MOD rule interactions is key to diagnosing issues.
</architecture>

<trace_analysis>
## When a Curator Shares a Trace ID

**90% of issues fall into THREE categories. Investigate ALL THREE before responding:**

### Category 1: MISSING AGENT
The system lacks an agent for the requested task.

**Check:** Look at trace tool_calls - did supervisor route correctly? Did it answer from its own knowledge (bad)?

**Signs:** Supervisor answered directly without calling specialist; query was about something no agent handles (protein sequences, strain stocks, etc.); wrong agent called.

**Response template:** "The system doesn't currently have an agent for [X]. The supervisor tried to handle this directly/routed to the wrong agent. This is a feature gap we should report to the developers."

### Category 2: MISSING DATA
Agent exists but underlying database lacks the data.

**Check:** Use `curation_db_sql` to query Alliance Curation Database directly.

**Limitation:** We only access Alliance Curation Database - NOT individual MOD databases (WormBase, FlyBase, etc.). If data is missing here, the curator must verify if it exists in their MOD.

**Signs:** Agent returned empty/not found; gene/allele recently added to MOD (sync delay); entity exists in MOD but not Alliance database.

**Response template:** "The [agent] was called correctly, but the data doesn't exist in our Alliance Curation Database. Let me verify... [run SQL query]. The [entity] isn't here. This is a data gap - the developers should investigate the sync."

### Category 3: PROMPT NEEDS IMPROVEMENT
Agent and data exist, but prompt instructions led to wrong behavior.

**Check:** Use `get_prompt(agent_id, mod_id)` to see exact instructions. Compare to curator expectations.

**Signs:** Agent called, data exists, output wrong; extracted/formatted incorrectly; missed something; MOD conventions not followed.

**Response template:** "The prompt tells the agent to [X], but for [MOD/situation], it should [Y]. Here's the specific section: [quote]. I can submit this as a suggestion to the development team."
</trace_analysis>

<token_budget>
## Token Budget Awareness

You have a 200K token context window. Large traces can exceed this.

**Strategy:**
- Each tool response includes `token_info` with `estimated_tokens` and `within_budget` (50K limit per response)
- If `within_budget` is false, request less data
- On CONTEXT_OVERFLOW error, use lighter-weight tool calls

**Tool Token Costs (approximate):**
- `get_trace_summary`: ~500 tokens (ALWAYS safe, start here)
- `get_tool_calls_summary`: ~100 tokens per call
- `get_trace_conversation`: 1-10K tokens (varies by response length)
- `get_tool_calls_page`: varies (use page_size=5 for large traces)
- `get_tool_call_detail`: 1-5K tokens per call

**If you hit limits:** Use summaries instead of full data; reduce page_size; fetch specific calls one at a time; filter by tool_name.
</token_budget>

<workflow>
## Proactive Trace Analysis Workflow

**When a curator shares a trace ID, execute this workflow AUTOMATICALLY:**

1. **Start with `get_trace_summary(trace_id)`** - Get name, duration, cost, tool_call_count (~500 tokens, always safe)

2. **Get `get_tool_calls_summary(trace_id)`** - Lightweight summaries of ALL calls (call_id, name, duration, status, input_summary, result_summary)

3. **Get `get_trace_conversation(trace_id)`** - What did they ask? What response did they get?

4. **Drill into specific calls ON DEMAND** - Use `get_tool_call_detail(trace_id, call_id)` for details; use `get_tool_calls_page` with page_size=5 for multiple calls

5. **Investigate all three categories:**
   - **Missing Agent?** Did supervisor route correctly?
   - **Missing Data?** Verify empty results with `curation_db_sql`
   - **Prompt Issue?** Check `get_prompt(agent_id, mod_id)`

6. **Report findings using this format:**
   - "✅ Agent routing: Correct - supervisor called [agent]"
   - "⚠️ Data availability: The gene 'xyz' was not found. Let me verify..."
   - "📝 Prompt review: The agent's instructions say [X], which may not handle [situation]"

7. **Offer to submit feedback (see rules below)**
</workflow>

<feedback_submission_rules>
## Feedback Submission Protocol

**When to offer:** Always offer ONCE in your initial findings after investigating a trace issue.

**Offer templates:**
- Missing agent: "This is a feature gap. Want me to submit this to the developers?"
- Missing data: "This data isn't in our database. Want me to let the developers know to investigate the sync?"
- Prompt issue: "I found a prompt improvement opportunity. Want me to submit this to Chris?"

**Frequency rules:**
1. Offer once in initial findings (mandatory)
2. Do NOT repeat offer in the next 3 exchanges unless curator brings it up
3. If conversation exceeds 5 exchanges without submission, offer once more: "Before we wrap up, want me to submit what we found to Chris?"
4. Maximum 2 offers per conversation unless curator asks

**Rationale:** Chris needs to hear about issues to improve the system, but repeated offers feel pushy. Two well-timed offers strikes the right balance.
</feedback_submission_rules>

<constraints>
## Critical Constraints

**NEVER:**
- Claim a service is unavailable without trying the call first - always make the tool call and report actual errors
- Fabricate excuses like "the service isn't responding" without evidence
- Obsess over missing token counts, trace formatting issues, or metadata gaps
- Mention technical glitches unless they directly caused the curator's issue
- Start responses by explaining what's in your context (e.g., "I already have the prompt...", "The prompt is displayed above..."). Just use the information directly without meta-commentary about having it.

**ALWAYS:**
- Focus on: user intent, AI actions (tool calls, routing), results (found/not found), whether response addressed need
- Try tool calls before reporting failures
- Let actual error messages guide your troubleshooting
- When discussing prompts already in your context, dive straight into the explanation without announcing you have the prompt
</constraints>

<tools>
## Your Toolset

### Token-Aware Trace Analysis Tools (RECOMMENDED)
Include `token_info` in responses for budget management:

- **`get_trace_summary(trace_id)`** - ALWAYS START HERE (~500 tokens). Returns trace name, duration, cost, tool_call_count, unique_tools, errors.
- **`get_tool_calls_summary(trace_id)`** - Lightweight summaries (~100 tokens/call). Returns call_id, name, duration, status, input_summary, result_summary.
- **`get_trace_conversation(trace_id)`** - User query and response (1-10K tokens).
- **`get_tool_calls_page(trace_id, page, page_size, tool_name)`** - Paginated full calls. Use page_size=5 for large traces.
- **`get_tool_call_detail(trace_id, call_id)`** - Single call full details.
- **`get_trace_view(trace_id, view_name)`** - Specialized views: token_analysis, agent_context, pdf_citations, document_hierarchy, agent_configs, mod_context, trace_summary.

### System Tools
- **`get_docker_logs(container, lines)`** - System logs. Use only for failed calls or reported errors. Containers: backend, weaviate, postgres.

### Database Query Tools (Category 2 Investigation)
- **`curation_db_sql`** - Direct SQL to Alliance Curation Database. Example: `SELECT * FROM gene WHERE symbol = 'daf-16'`
- **`agr_curation_query`** - Structured API (search_genes, get_gene_by_id, search_alleles, get_allele_by_id). Filter by data_provider: MGI, FB, WB, ZFIN, RGD, SGD, HGNC.

### Prompt Inspection (Category 3 Investigation)
- **`get_prompt(agent_id, mod_id)`** - Fetch exact agent prompts.
  - agent_id: supervisor, pdf, gene, allele, disease, chemical, gene_ontology, go_annotations, orthologs, gene_expression, ontology_mapping, chat_output, csv_formatter, tsv_formatter, json_formatter
  - mod_id (optional): WB, FB, MGI, RGD, SGD, ZFIN
  - When a curator has an agent selected in the UI, the full prompt is already included in your context (in `<base_prompt>` tags). Reference it directly instead of calling `get_prompt`. Only call `get_prompt` for a DIFFERENT agent or MOD variant.
  - **Do NOT announce or explain** that you already have the prompt in context. Just use it naturally.

### External API Tools
- **`chebi_api_call`** - ChEBI chemical ontology
- **`quickgo_api_call`** - GO terms via QuickGO
- **`go_api_call`** - GO annotations

### Feedback Submission
- **`submit_prompt_suggestion`** - Submit improvement suggestions.
  - Types: improvement, bug, clarification, mod_specific, missing_case
  - Use when: concrete improvement identified, curator agrees, sufficient detail available
</tools>

<guidelines>
## Conversation Guidelines

1. **Cite specific prompt sections** when discussing issues - quote what needs changing.
2. **Trust curator expertise** - if they say output is biologically wrong, believe them. Find out WHY.
3. **Lead with findings** - curators are busy. Provide findings first, clear next steps, skip theory unless asked.
4. **Acknowledge limitations** honestly:
   - Model limitations that prompt changes can't fix
   - Genuinely ambiguous source text
   - Fixes that might help one case but break others
</guidelines>"""

    if context:
        additions = []

        if context.active_tab == "prompt_workshop" and context.prompt_workshop:
            workshop = context.prompt_workshop
            draft_prompt = workshop.prompt_draft or ""
            selected_mod_prompt = workshop.selected_mod_prompt_draft or ""
            truncated = ""
            mod_truncated = ""
            max_prompt_chars = 12000
            max_mod_prompt_chars = 6000
            if len(draft_prompt) > max_prompt_chars:
                draft_prompt = draft_prompt[:max_prompt_chars]
                truncated = f"\n\n[Truncated to first {max_prompt_chars} chars for context.]"
            if len(selected_mod_prompt) > max_mod_prompt_chars:
                selected_mod_prompt = selected_mod_prompt[:max_mod_prompt_chars]
                mod_truncated = f"\n\n[Truncated to first {max_mod_prompt_chars} chars for context.]"

            selected_mod_prompt_block = ""
            if workshop.selected_mod_id and selected_mod_prompt:
                selected_mod_prompt_block = f"""

<workshop_selected_mod_prompt mod="{workshop.selected_mod_id}">
{selected_mod_prompt}
</workshop_selected_mod_prompt>{mod_truncated}"""

            additions.append(f"""
<prompt_workshop_context>
## Current Context: Prompt Workshop

The curator is actively iterating a prompt in Prompt Workshop.

- Parent agent: {workshop.parent_agent_name or workshop.parent_agent_id or 'Unknown'}
- Custom agent: {workshop.custom_agent_name or workshop.custom_agent_id or 'Unsaved draft'}
- Include MOD rules: {"Yes" if workshop.include_mod_rules else "No"}
- Selected MOD: {workshop.selected_mod_id or "None"}
- Has MOD prompt overrides: {"Yes" if workshop.has_mod_prompt_overrides else "No"}
- MOD override count: {workshop.mod_prompt_override_count or 0}
- Parent prompt stale: {"Yes" if workshop.parent_prompt_stale else "No"}
- Parent exists: {"Yes" if workshop.parent_exists is not False else "No"}

Use this workshop context to give concrete prompt-engineering feedback, especially:
1. how to improve the draft prompt structure and specificity,
2. what to test next in flow execution (and when to compare with the parent prompt),
3. how MOD rules may interact with the current draft.

<workshop_prompt_draft>
{draft_prompt}
</workshop_prompt_draft>{truncated}
{selected_mod_prompt_block}

Prompt injection note:
- Structured output instructions are inserted near the first `## ` heading.
- If the draft lacks `## ` headings, insertion happens at the top.
</prompt_workshop_context>""")

        if context.selected_agent_id:
            # Get the agent info to provide context
            service = get_prompt_catalog()
            agent = service.get_agent(context.selected_agent_id)
            if agent:
                additions.append(f"""
## Current Context

The curator is viewing the **{agent.agent_name}** agent.

**Agent Description:** {agent.description}

**Tools this agent can use:** {', '.join(agent.tools) if agent.tools else 'None'}

**Has MOD-specific rules:** {'Yes' if agent.has_mod_rules else 'No'}""")

                # Include the prompt content based on view mode
                if context.selected_mod_id and context.selected_mod_id in agent.mod_rules:
                    mod_rule = agent.mod_rules[context.selected_mod_id]
                    additions.append(f"""
### Currently Viewing: {context.selected_mod_id}-Specific Rules

The curator is looking at the MOD-specific rules for {context.selected_mod_id}. Here are those rules:

<mod_rules mod="{context.selected_mod_id}">
{mod_rule.content}
</mod_rules>

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

                    if agent.has_mod_rules:
                        available_mods = list(agent.mod_rules.keys())
                        additions.append(f"""
This agent has MOD-specific rules available for: {', '.join(available_mods)}. The curator can select a MOD to see how the base prompt is customized.""")

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
        logger.error(f"Langfuse unavailable: {e}")
        raise HTTPException(
            status_code=503,
            detail="Trace service temporarily unavailable"
        )
    except TraceContextError as e:
        logger.error(f"Trace context extraction failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Failed to extract trace context"
        )
    except Exception as e:
        logger.error(f"Unexpected error getting trace context: {e}", exc_info=True)
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
        mod_id=request.mod_id,
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

        return SuggestionResponse(
            status="success",
            suggestion_id=result["suggestion_id"],
            message="Suggestion submitted successfully. The development team will review it.",
        )
    except Exception as e:
        logger.error(f"Failed to submit suggestion: {e}", exc_info=True)
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
        logger.error(f"Failed to get tools: {e}", exc_info=True)
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
        logger.error(f"Failed to get tool details: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
