"""
Prompt Explorer module.

Provides services for exploring and analyzing agent prompts:
- PromptCatalogService: Extract and serve prompt metadata
- TraceContextService: Enrich traces with prompt context
- OpusChatService: Stream Opus conversations
- Flow Tools: Create and manage curation flows via Opus

Heavy runtime dependencies (``agents`` SDK, flow-tool registration) are
**not** imported at package level.  Use explicit submodule imports for
``catalog_service``, ``flow_tools``, and ``suggestion_service``.
"""

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
    # Exceptions
    "TraceContextError",
    "TraceNotFoundError",
    "LangfuseUnavailableError",
    # Lazy-loaded (available via __getattr__)
    "PromptCatalogService",
    "get_prompt_catalog",
    "get_prompt_key_for_agent",
    "SuggestionType",
    "PromptSuggestion",
    "SuggestionSubmission",
    "submit_suggestion_sns",
    "SUBMIT_SUGGESTION_TOOL",
    "register_flow_tools",
    "set_workflow_user_context",
    "clear_workflow_user_context",
    "get_current_user_id",
    "get_current_user_email",
    "set_current_flow_context",
    "clear_current_flow_context",
    "FLOW_AGENT_IDS",
]

# ---------------------------------------------------------------------------
# Lazy accessor – heavy submodules are only imported on first attribute access
# ---------------------------------------------------------------------------
_LAZY_IMPORTS: dict[str, tuple[str, str]] = {
    # catalog_service (depends on agents.Agent)
    "PromptCatalogService": (".catalog_service", "PromptCatalogService"),
    "get_prompt_catalog": (".catalog_service", "get_prompt_catalog"),
    "get_prompt_key_for_agent": (".catalog_service", "get_prompt_key_for_agent"),
    # suggestion_service (depends on boto3 / pydantic – moderate weight)
    "SuggestionType": (".suggestion_service", "SuggestionType"),
    "PromptSuggestion": (".suggestion_service", "PromptSuggestion"),
    "SuggestionSubmission": (".suggestion_service", "SuggestionSubmission"),
    "submit_suggestion_sns": (".suggestion_service", "submit_suggestion_sns"),
    "SUBMIT_SUGGESTION_TOOL": (".suggestion_service", "SUBMIT_SUGGESTION_TOOL"),
    # flow_tools (depends on catalog_service → agents.Agent)
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
        import importlib

        mod = importlib.import_module(module_path, __package__)
        value = getattr(mod, attr)
        # Cache on the module so __getattr__ is not called again
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
