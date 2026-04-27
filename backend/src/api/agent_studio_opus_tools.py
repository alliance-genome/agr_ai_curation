"""Opus tool definitions and tab-scoping helpers for Agent Studio."""

from typing import Any, Callable, Dict, List, Optional

from .logs import (
    ALLOWED_CONTAINERS as LOGS_API_ALLOWED_CONTAINERS,
    ALLOWED_LOG_LEVELS as LOGS_API_ALLOWED_LOG_LEVELS,
)
from src.lib.agent_studio import ChatContext, SUBMIT_SUGGESTION_TOOL
from src.lib.agent_studio.diagnostic_tools import get_diagnostic_tools_registry
from src.lib.agent_studio.flow_tools import register_flow_tools
from src.lib.chat_history_repository import (
    ALL_CHAT_KINDS_SENTINEL,
    ASSISTANT_CHAT_KIND,
    AGENT_STUDIO_CHAT_KIND,
)

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

CHAT_HISTORY_TOOL_CHAT_KINDS = [
    ASSISTANT_CHAT_KIND,
    AGENT_STUDIO_CHAT_KIND,
    ALL_CHAT_KINDS_SENTINEL,
]

LIST_RECENT_CHATS_TOOL = {
    "name": "list_recent_chats",
    "description": (
        "List the authenticated user's most recent durable chat sessions across "
        "assistant_chat, agent_studio, or both. Use this when the user asks for "
        "their last few chats or recent sessions."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "chat_kind": {
                "type": "string",
                "enum": CHAT_HISTORY_TOOL_CHAT_KINDS,
                "description": (
                    "Which durable chat kind to browse. Use 'all' to include both "
                    "assistant_chat and agent_studio sessions."
                ),
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of recent sessions to return (default: 10, max: 25).",
                "default": 10,
                "minimum": 1,
                "maximum": 25,
            },
        },
        "required": ["chat_kind"],
    },
}

SEARCH_CHAT_HISTORY_TOOL = {
    "name": "search_chat_history",
    "description": (
        "Search the authenticated user's durable chat history by keyword across "
        "session titles and transcript content. Use this when the user refers to "
        "a past conversation topic, phrase, gene, or session theme."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Full-text search query to run against durable chat history.",
            },
            "chat_kind": {
                "type": "string",
                "enum": CHAT_HISTORY_TOOL_CHAT_KINDS,
                "description": (
                    "Which durable chat kind to search. Use 'all' to include both "
                    "assistant_chat and agent_studio sessions."
                ),
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of matching sessions to return (default: 10, max: 25).",
                "default": 10,
                "minimum": 1,
                "maximum": 25,
            },
        },
        "required": ["query", "chat_kind"],
    },
}

GET_CHAT_CONVERSATION_TOOL = {
    "name": "get_chat_conversation",
    "description": (
        "Load the full durable transcript for one visible chat session by session_id. "
        "Use this when the user asks to open a specific prior conversation."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "session_id": {
                "type": "string",
                "description": "Durable chat session identifier returned by list_recent_chats or search_chat_history.",
            },
        },
        "required": ["session_id"],
    },
}

GET_TRACE_SUMMARY_TOOL = {
    "name": "get_trace_summary",
    "description": "Get lightweight trace summary (~500 tokens). ALWAYS CALL THIS FIRST when analyzing a trace. Returns: trace name, duration, cost, token counts, tool call count, unique tools, error status, context overflow detection.",
    "input_schema": {
        "type": "object",
        "properties": {
            "trace_id": {
                "type": "string",
                "description": "Langfuse trace ID (UUID with hyphens or 32-char hex string)",
            }
        },
        "required": ["trace_id"],
    },
}

GET_TOOL_CALLS_SUMMARY_TOOL = {
    "name": "get_tool_calls_summary",
    "description": "Get lightweight summary of ALL tool calls without full results (~100 tokens/call). Use this to see what tools were called before drilling into details. Returns: total count, unique tools, and list of summaries (call_id, name, time, duration, status, input_summary, result_summary).",
    "input_schema": {
        "type": "object",
        "properties": {
            "trace_id": {
                "type": "string",
                "description": "Langfuse trace ID",
            }
        },
        "required": ["trace_id"],
    },
}

GET_TOOL_CALLS_PAGE_TOOL = {
    "name": "get_tool_calls_page",
    "description": "Get paginated tool calls with full details. Use for detailed analysis of specific calls. Results are automatically truncated to fit within token budget. Supports filtering by tool name.",
    "input_schema": {
        "type": "object",
        "properties": {
            "trace_id": {
                "type": "string",
                "description": "Langfuse trace ID",
            },
            "page": {
                "type": "integer",
                "description": "Page number (1-indexed, default: 1)",
                "default": 1,
                "minimum": 1,
            },
            "page_size": {
                "type": "integer",
                "description": "Items per page (default: 10, max: 20)",
                "default": 10,
                "minimum": 1,
                "maximum": 20,
            },
            "tool_name": {
                "type": "string",
                "description": "Optional filter by tool name (e.g., 'search_document')",
            },
        },
        "required": ["trace_id"],
    },
}

GET_TOOL_CALL_DETAIL_TOOL = {
    "name": "get_tool_call_detail",
    "description": "Get full details for a single tool call. Use when you need complete input/output for a specific call identified from get_tool_calls_summary. Token cost: ~1-5K tokens depending on result size.",
    "input_schema": {
        "type": "object",
        "properties": {
            "trace_id": {
                "type": "string",
                "description": "Langfuse trace ID",
            },
            "call_id": {
                "type": "string",
                "description": "Tool call ID from get_tool_calls_summary response",
            },
        },
        "required": ["trace_id", "call_id"],
    },
}

GET_TRACE_CONVERSATION_TOOL = {
    "name": "get_trace_conversation",
    "description": "Get the user's query and assistant's final response. Use when you need to see what the curator asked and what the AI answered. Token cost varies by response length.",
    "input_schema": {
        "type": "object",
        "properties": {
            "trace_id": {
                "type": "string",
                "description": "Langfuse trace ID",
            }
        },
        "required": ["trace_id"],
    },
}

GET_TRACE_VIEW_TOOL = {
    "name": "get_trace_view",
    "description": "Get a specific analysis view with token metadata. Use for specialized views not covered by the primary tools. Available views: token_analysis, agent_context, pdf_citations, document_hierarchy, agent_configs, group_context, trace_summary.",
    "input_schema": {
        "type": "object",
        "properties": {
            "trace_id": {
                "type": "string",
                "description": "Langfuse trace ID",
            },
            "view_name": {
                "type": "string",
                "enum": ["token_analysis", "agent_context", "pdf_citations", "document_hierarchy", "agent_configs", "group_context", "mod_context", "trace_summary"],
                "description": "Which view to fetch",
            },
        },
        "required": ["trace_id", "view_name"],
    },
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
                "default": "backend",
            },
            "lines": {
                "type": "integer",
                "description": "Number of recent log lines (default: 2000, min: 100, max: 5000)",
                "default": 2000,
                "minimum": 100,
                "maximum": 5000,
            },
            "level": {
                "type": "string",
                "enum": sorted(LOGS_API_ALLOWED_LOG_LEVELS),
                "description": "Optional log level filter",
            },
            "since": {
                "type": "integer",
                "description": "Optional time filter in minutes ago (for example: 15 for the last 15 minutes)",
                "minimum": 1,
            },
        },
        "required": [],
    },
}


COMMON_TOOLS = {
    "get_chat_conversation",
    "list_recent_chats",
    "search_chat_history",
    "submit_prompt_suggestion",
    "report_tool_failure",
}
TRACE_TOOLS = {
    "get_trace_summary",
    "get_tool_calls_summary",
    "get_tool_calls_page",
    "get_tool_call_detail",
    "get_trace_conversation",
    "get_trace_view",
    "get_service_logs",
}
FLOW_TOOLS = {
    "create_flow",
    "validate_flow",
    "get_flow_templates",
    "get_current_flow",
    "get_available_agents",
}
AGENTS_ONLY_DIAGNOSTIC_TOOLS = {
    "agr_curation_query",
    "curation_db_sql",
    "chebi_api_call",
    "quickgo_api_call",
    "go_api_call",
    "search_codebase",
    "read_source_file",
}


def get_active_tab(context: Optional[ChatContext]) -> str:
    """Resolve active tab from chat context with a safe default."""

    if context and context.active_tab in {"agents", "flows", "agent_workshop"}:
        return context.active_tab
    return "agents"


def ensure_flow_tools_registered(registry: Any, *, logger: Any) -> None:
    """Ensure flow tools are present even if the diagnostic registry was reset."""

    if all(registry.has_tool(name) for name in FLOW_TOOLS):
        return
    try:
        register_flow_tools()
    except Exception:
        logger.exception("Failed to ensure flow tool registration for Agent Studio tools")


def is_tool_allowed_for_context(tool_name: str, context: Optional[ChatContext]) -> bool:
    """Check whether a tool is allowed for the current tab/context."""

    active_tab = get_active_tab(context)
    has_trace = bool(context and context.trace_id)

    if tool_name in COMMON_TOOLS:
        return True

    if tool_name == "update_workshop_prompt_draft":
        return active_tab == "agent_workshop" and bool(context and context.agent_workshop)

    if tool_name in FLOW_TOOLS:
        return active_tab == "flows"

    if tool_name in AGENTS_ONLY_DIAGNOSTIC_TOOLS:
        return active_tab == "agents"

    if tool_name == "get_prompt":
        return active_tab in {"agents", "flows", "agent_workshop"}

    if tool_name in TRACE_TOOLS:
        return active_tab == "agents" or has_trace

    # Unknown/legacy tools are left to existing handlers and validation paths.
    return True


def tool_scope_error(tool_name: str, context: Optional[ChatContext]) -> Dict[str, Any]:
    """Build a curator-friendly error for disallowed tool usage."""

    active_tab = get_active_tab(context)
    return {
        "success": False,
        "error": (
            f"Tool '{tool_name}' is not available on the {active_tab} tab. "
            "Use the matching screen for that tool type."
        ),
    }


def get_all_opus_tools(
    context: Optional[ChatContext] = None,
    *,
    diagnostic_registry_factory: Callable[[], Any] = get_diagnostic_tools_registry,
    ensure_registered: Callable[[Any], None],
    logger: Any,
    is_allowed: Callable[[str, Optional[ChatContext]], bool] = is_tool_allowed_for_context,
) -> List[dict]:
    """
    Get all tools available to Opus in Anthropic format.

    Combines the suggestion tool, workflow analysis tools, and diagnostic tools.
    """

    candidate_tools = [
        ANTHROPIC_SUGGESTION_TOOL,
        ANTHROPIC_UPDATE_WORKSHOP_PROMPT_TOOL,
        ANTHROPIC_REPORT_TOOL_FAILURE_TOOL,
        LIST_RECENT_CHATS_TOOL,
        SEARCH_CHAT_HISTORY_TOOL,
        GET_CHAT_CONVERSATION_TOOL,
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
        if is_allowed(str(tool.get("name", "")), context)
    ]

    registry = diagnostic_registry_factory()
    ensure_registered(registry)
    diagnostic_tools = []
    for tool in registry.get_all_tools():
        if not is_allowed(tool.name, context):
            continue
        diagnostic_tools.append(
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema,
            }
        )
    tools.extend(diagnostic_tools)
    logger.debug("Loaded %s diagnostic tools for Opus", len(diagnostic_tools))

    return tools
