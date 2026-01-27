"""
Prompt Explorer module.

Provides services for exploring and analyzing agent prompts:
- PromptCatalogService: Extract and serve prompt metadata
- TraceContextService: Enrich traces with prompt context
- OpusChatService: Stream Opus 4.5 conversations
- Flow Tools: Create and manage curation flows via Opus
"""

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

from .catalog_service import PromptCatalogService, get_prompt_catalog
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


# Register flow tools on module import
# This makes create_flow, validate_flow, and get_flow_templates
# available to Opus via the DiagnosticToolRegistry
register_flow_tools()


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
