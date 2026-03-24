"""
Prompt Explorer module.

Provides services for exploring and analyzing agent prompts:
- PromptCatalogService: Extract and serve prompt metadata
- TraceContextService: Enrich traces with prompt context
- OpusChatService: Stream Opus conversations
- Flow Tools: Create and manage curation flows via Opus
"""
import importlib
import logging

from .models import (
    # Prompt catalog models
    GroupRuleInfo,
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

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy accessors for heavy submodules (catalog_service, flow_tools)
# ---------------------------------------------------------------------------
# These names were previously imported eagerly, pulling in the OpenAI Agents
# SDK (agents.Agent) on every ``import agent_studio``.  They are now resolved
# on first access so that lightweight consumers (models, validation, etc.)
# are not penalised.
#
# Auto-registration of flow tools has been removed from module import.
# Call ``register_flow_tools()`` explicitly where needed (e.g. API startup).
# ---------------------------------------------------------------------------

_LAZY_IMPORTS: dict[str, tuple[str, str]] = {
    # catalog_service
    "PromptCatalogService": (".catalog_service", "PromptCatalogService"),
    "get_prompt_catalog": (".catalog_service", "get_prompt_catalog"),
    "get_prompt_key_for_agent": (".catalog_service", "get_prompt_key_for_agent"),
    # flow_tools
    "register_flow_tools": (".flow_tools", "register_flow_tools"),
    "set_workflow_user_context": (".flow_tools", "set_workflow_user_context"),
    "clear_workflow_user_context": (".flow_tools", "clear_workflow_user_context"),
    "get_current_user_id": (".flow_tools", "get_current_user_id"),
    "get_current_user_email": (".flow_tools", "get_current_user_email"),
    "set_current_flow_context": (".flow_tools", "set_current_flow_context"),
    "clear_current_flow_context": (".flow_tools", "clear_current_flow_context"),
    "FLOW_AGENT_IDS": (".flow_tools", "FLOW_AGENT_IDS"),
}


def __getattr__(name: str):
    if name in _LAZY_IMPORTS:
        module_path, attr = _LAZY_IMPORTS[name]
        mod = importlib.import_module(module_path, __package__)
        val = getattr(mod, attr)
        globals()[name] = val  # cache for subsequent access
        return val
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    # Prompt catalog models
    "GroupRuleInfo",
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
    # Services (lazy)
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
    # Flow tools (lazy)
    "register_flow_tools",
    "set_workflow_user_context",
    "clear_workflow_user_context",
    "get_current_user_id",
    "get_current_user_email",
    "set_current_flow_context",
    "clear_current_flow_context",
    "FLOW_AGENT_IDS",
]
