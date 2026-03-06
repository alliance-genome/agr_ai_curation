"""
Prompt Explorer module.

Provides services for exploring and analyzing agent prompts:
- PromptCatalogService: Extract and serve prompt metadata
- TraceContextService: Enrich traces with prompt context
- OpusChatService: Stream Opus conversations
- Flow Tools: Create and manage curation flows via Opus
"""
import logging
import os

from .models import (
    # Prompt catalog models
    MODRuleInfo,
    PromptInfo,
    AgentPrompts,
    PromptCatalog,
    # Chat models
    ChatMessage,
    ChatContext,
    ChatRequest,
    ChatResponse,
    # Trace context models
    ToolCallInfo,
    RoutingDecision,
    PromptExecution,
    TraceContext,
    # API response models
    PromptCatalogResponse,
    TraceContextResponse,
    ErrorResponse,
)

from .catalog_service import PromptCatalogService, get_prompt_catalog, get_prompt_key_for_agent
from .trace_context_service import (
    TraceContextError,
    TraceNotFoundError,
    LangfuseUnavailableError,
)
from .suggestion_service import (
    SuggestionType,
    PromptSuggestion,
    SuggestionSubmission,
    submit_suggestion_sns,
    SUBMIT_SUGGESTION_TOOL,
)
from .flow_tools import (
    register_flow_tools,
    set_workflow_user_context,
    clear_workflow_user_context,
    get_current_user_id,
    get_current_user_email,
    set_current_flow_context,
    clear_current_flow_context,
    FLOW_AGENT_IDS,
)

logger = logging.getLogger(__name__)

# Register flow tools on module import
# This makes create_flow, validate_flow, and get_flow_templates
# available to Opus via the DiagnosticToolRegistry
auto_register_flow_tools = (
    os.getenv("AGENT_STUDIO_AUTO_REGISTER_FLOW_TOOLS", "1").strip().lower()
    not in {"0", "false", "no", "off"}
)
if auto_register_flow_tools:
    try:
        register_flow_tools()
    except Exception:
        logger.exception("Flow tool registration failed during agent_studio import")


__all__ = [
    # Prompt catalog models
    "MODRuleInfo",
    "PromptInfo",
    "AgentPrompts",
    "PromptCatalog",
    # Chat models
    "ChatMessage",
    "ChatContext",
    "ChatRequest",
    "ChatResponse",
    # Trace context models
    "ToolCallInfo",
    "RoutingDecision",
    "PromptExecution",
    "TraceContext",
    # API response models
    "PromptCatalogResponse",
    "TraceContextResponse",
    "ErrorResponse",
    # Services
    "PromptCatalogService",
    "get_prompt_catalog",
    "get_prompt_key_for_agent",
    # Exceptions
    "TraceContextError",
    "TraceNotFoundError",
    "LangfuseUnavailableError",
    # Suggestion service
    "SuggestionType",
    "PromptSuggestion",
    "SuggestionSubmission",
    "submit_suggestion_sns",
    "SUBMIT_SUGGESTION_TOOL",
    # Flow tools
    "register_flow_tools",
    "set_workflow_user_context",
    "clear_workflow_user_context",
    "get_current_user_id",
    "get_current_user_email",
    "set_current_flow_context",
    "clear_current_flow_context",
    "FLOW_AGENT_IDS",
]
