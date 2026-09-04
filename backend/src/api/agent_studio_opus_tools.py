"""AI Chat tool definitions and tab-scoping helpers for Agent Studio.

The module name remains a compatibility identifier for the shared hotfix.
"""

from functools import lru_cache
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
from src.lib.openai_agents.config import (
    get_agent_studio_chat_history_page_size,
    get_agent_studio_chat_recall_chunk_max_chars,
    get_agent_studio_chat_recall_page_size,
    get_agent_studio_service_log_default_lines,
    get_agent_studio_service_log_max_lines,
    get_agent_studio_service_log_max_lookback_minutes,
    get_agent_studio_trace_review_aggregate_page_size,
    get_agent_studio_trace_review_chunk_max_chars,
    get_agent_studio_trace_review_page_size,
    get_agent_studio_trace_review_summary_max_chars,
    get_agent_studio_trace_search_default_limit,
    get_agent_studio_trace_search_filter_max_chars,
    get_agent_studio_trace_search_max_limit,
    get_domain_pack_validation_plan_default_limit,
    get_domain_pack_validation_plan_max_limit,
    get_domain_runtime_inspection_default_limit,
    get_domain_runtime_inspection_max_limit,
)


_DOMAIN_PACK_VALIDATION_PLAN_MAX_LIMIT = get_domain_pack_validation_plan_max_limit()
_DOMAIN_PACK_VALIDATION_PLAN_DEFAULT_LIMIT = min(
    get_domain_pack_validation_plan_default_limit(),
    _DOMAIN_PACK_VALIDATION_PLAN_MAX_LIMIT,
)
_DOMAIN_RUNTIME_INSPECTION_MAX_LIMIT = get_domain_runtime_inspection_max_limit()
_DOMAIN_RUNTIME_INSPECTION_DEFAULT_LIMIT = min(
    get_domain_runtime_inspection_default_limit(),
    _DOMAIN_RUNTIME_INSPECTION_MAX_LIMIT,
)
_TRACE_REVIEW_PAGE_SIZE = get_agent_studio_trace_review_page_size()
_TRACE_REVIEW_AGGREGATE_PAGE_SIZE = get_agent_studio_trace_review_aggregate_page_size()
_TRACE_REVIEW_CHUNK_MAX_CHARS = get_agent_studio_trace_review_chunk_max_chars()
_TRACE_REVIEW_SUMMARY_MAX_CHARS = get_agent_studio_trace_review_summary_max_chars()
_TRACE_SEARCH_DEFAULT_LIMIT = get_agent_studio_trace_search_default_limit()
_TRACE_SEARCH_MAX_LIMIT = get_agent_studio_trace_search_max_limit()
_TRACE_SEARCH_FILTER_MAX_CHARS = get_agent_studio_trace_search_filter_max_chars()
_CHAT_RECALL_PAGE_SIZE = get_agent_studio_chat_recall_page_size()
_CHAT_RECALL_CHUNK_MAX_CHARS = get_agent_studio_chat_recall_chunk_max_chars()
_CHAT_HISTORY_PAGE_SIZE = get_agent_studio_chat_history_page_size()
_SERVICE_LOG_DEFAULT_LINES = get_agent_studio_service_log_default_lines()
_SERVICE_LOG_MAX_LINES = get_agent_studio_service_log_max_lines()
_SERVICE_LOG_MAX_LOOKBACK_MINUTES = get_agent_studio_service_log_max_lookback_minutes()

_AGGREGATE_PAGE_PROPERTIES = {
    "section": {
        "type": "string",
        "description": "Collection section named by the summary inventory. Omit for summary-only output.",
    },
    "offset": {
        "type": "integer",
        "description": "Section item offset.",
        "default": 0,
        "minimum": 0,
    },
    "limit": {
        "type": "integer",
        "description": "Maximum lossless section items (environment-bounded).",
        "default": _TRACE_REVIEW_AGGREGATE_PAGE_SIZE,
        "minimum": 1,
        "maximum": _TRACE_REVIEW_AGGREGATE_PAGE_SIZE,
    },
    "item_start": {
        "type": "integer",
        "description": "Exact JSON character cursor from page.next_call for one oversized item.",
        "default": 0,
        "minimum": 0,
    },
}

# Keep the public tool schema independent of the provider runtime.
SUGGESTION_TOOL = {
    "name": SUBMIT_SUGGESTION_TOOL["name"],
    "description": SUBMIT_SUGGESTION_TOOL["description"],
    "input_schema": SUBMIT_SUGGESTION_TOOL["input_schema"],
}

UPDATE_WORKSHOP_PROMPT_TOOL = {
    "name": "update_workshop_prompt_draft",
    "description": """Propose a prompt update for the current Agent Workshop draft.

Use this when the curator asks you to rewrite, replace, or significantly refactor
their editable workshop layers: the main/base prompt ("main") or selected group
override ("group"). Backend-owned core/generated layers and inherited base prompts
are read-only context and must not be copied into updated_prompt.
This tool does NOT auto-apply or auto-save changes.
The UI will show the proposal and require explicit curator approval before applying.
The continuation result is a compact approval acknowledgment and the full proposal is
delivered to the UI. For replace mode, updated_prompt remains in the retained tool
input. For targeted-edit mode, only the authored edits remain there; after approval,
use refresh_workshop_prompt chunks to read the exact resulting text when needed. Do
not replay the same proposal in another call while approval is pending.
Before proposing edits about PDF evidence extraction, inspect current prompt and
tool schemas so the update preserves span-id evidence recording instead of
legacy quote-generation guidance.
""",
    "input_schema": {
        "type": "object",
        "properties": {
            "target_prompt": {
                "type": "string",
                "enum": ["main", "group"],
                "description": "Which editable workshop layer to update. Use 'main' for the main/base prompt and 'group' for the selected group prompt override.",
                "default": "main",
            },
            "target_group_id": {
                "type": "string",
                "description": "Optional group ID when target_prompt='group' (for example 'WB'). Must match the currently selected group in Agent Workshop.",
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

REFRESH_WORKSHOP_PROMPT_TOOL = {
    "name": "refresh_workshop_prompt",
    "description": """Inspect the exact current Agent Workshop prompt before reviewing it.

Use this before commenting on current Agent Workshop prompt text or metadata, especially
after the curator saves manual edits or asks whether a typo, schema issue, or
prompt-quality concern is fixed. Treat older chat history and version snapshots
as historical after this tool returns. Omit start for a content-free identity,
hash, length, and freshness summary. Then follow next_call with its prompt_hash,
start, and max_chars until complete=true to reconstruct the exact prompt. Pair
Use target_prompt="metadata" to reconstruct exact authoring fields when the
system-context metadata preview is incomplete. Pair this with get_tool_inventory and get_tool_details before advising on
document/evidence tool instructions.
""",
    "input_schema": {
        "type": "object",
        "properties": {
            "target_prompt": {
                "type": "string",
                "enum": ["main", "group", "metadata"],
                "description": "Refresh the main prompt, a captured group override, or the exact non-prompt Workshop authoring metadata.",
                "default": "main",
            },
            "target_group_id": {
                "type": "string",
                "description": "Optional group ID when target_prompt='group'. Defaults to the selected group; any group listed in the Workshop context's Group override IDs may be inspected directly.",
            },
            "prompt_hash": {
                "type": "string",
                "description": "Stable prompt hash from the summary. Required with start so a changed prompt cannot be mixed into reconstruction.",
            },
            "start": {
                "type": "integer",
                "minimum": 0,
                "description": "Zero-based character offset for exact chunk retrieval. Omit for the content-free summary.",
            },
            "max_chars": {
                "type": "integer",
                "minimum": 1,
                "description": "Requested chunk size, bounded by backend configuration. Valid only with start.",
            },
        },
        "required": [],
    },
}

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
        "their last few chats or recent sessions. Follow next_call until complete=true."
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
                "description": "Maximum sessions requested before provider-envelope fitting.",
                "default": min(10, _CHAT_HISTORY_PAGE_SIZE),
                "minimum": 1,
                "maximum": _CHAT_HISTORY_PAGE_SIZE,
            },
            "cursor": {
                "type": "string",
                "description": "Opaque stable session cursor from the previous response's next_call.",
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
        "a past conversation topic, phrase, gene, session theme, or content from "
        "the current session that may have been compacted out of live context. "
        "Follow next_call until complete=true."
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
                "description": "Maximum matches requested before provider-envelope fitting.",
                "default": min(10, _CHAT_HISTORY_PAGE_SIZE),
                "minimum": 1,
                "maximum": _CHAT_HISTORY_PAGE_SIZE,
            },
            "cursor": {
                "type": "string",
                "description": "Opaque ranked-search cursor from the previous response's next_call.",
            },
        },
        "required": ["query", "chat_kind"],
    },
}

GET_CHAT_CONVERSATION_TOOL = {
    "name": "get_chat_conversation",
    "description": (
        "Browse one bounded metadata page of a visible durable chat transcript. "
        "Use this when the user asks to open a specific prior conversation, or when "
        "you need to rehydrate durable prior-turn context from the current session "
        "after provider context editing compacted it. Follow next_call until complete=true, "
        "then select a turn and use get_chat_turn for exact fields. Hidden "
        "context-compaction projection rows are not returned."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "session_id": {
                "type": "string",
                "description": "Durable chat session identifier returned by list_recent_chats or search_chat_history.",
            },
            "cursor": {
                "type": "string",
                "description": "Opaque stable message cursor from the previous response's next_call.",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum row summaries in this page (environment-bounded).",
                "default": _CHAT_RECALL_PAGE_SIZE,
                "minimum": 1,
                "maximum": _CHAT_RECALL_PAGE_SIZE,
            },
        },
        "required": ["session_id"],
    },
}

GET_CHAT_TURN_TOOL = {
    "name": "get_chat_turn",
    "description": (
        "Browse bounded durable row metadata for one turn_id, then retrieve one exact "
        "content or payload_json field by message_id in deterministic chunks. Follow "
        "each returned next_call until complete=true. Completed prior turns are "
        "recallable from persistence. An in-flight same-turn raw tool result exists "
        "only in the current tool continuation until the assistant turn completes; "
        "this tool makes no earlier durability promise."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "session_id": {
                "type": "string",
                "description": "Durable chat session identifier. For the current Agent Studio session, use the session_id from compact tool-result recall hints.",
            },
            "turn_id": {
                "type": "string",
                "description": "Durable turn identifier, for example opus-turn-3-<digest>.",
            },
            "cursor": {
                "type": "string",
                "description": "Opaque row-page cursor from a previous get_chat_turn next_call.",
            },
            "limit": {
                "type": "integer",
                "default": _CHAT_RECALL_PAGE_SIZE,
                "minimum": 1,
                "maximum": _CHAT_RECALL_PAGE_SIZE,
            },
            "message_id": {
                "type": "string",
                "description": "Durable row UUID from turn metadata; required with field detail retrieval.",
            },
            "field": {
                "type": "string",
                "enum": ["content", "payload_json"],
                "description": "Exact row field to retrieve independently.",
            },
            "field_hash": {
                "type": "string",
                "description": "SHA-256 from row metadata, required to pin exact chunk identity.",
            },
            "start": {
                "type": "integer",
                "description": "Start character offset in the serialized field.",
                "default": 0,
                "minimum": 0,
            },
            "max_chars": {
                "type": "integer",
                "description": "Maximum requested exact characters; runtime may shorten to fit provider JSON.",
                "default": _CHAT_RECALL_CHUNK_MAX_CHARS,
                "minimum": 1,
                "maximum": _CHAT_RECALL_CHUNK_MAX_CHARS,
            },
        },
        "required": ["session_id", "turn_id"],
    },
}

GET_TRACE_SUMMARY_TOOL = {
    "name": "get_trace_summary",
    "description": "Get a bounded lightweight trace summary. ALWAYS CALL THIS FIRST when analyzing a trace. Omitted domain-envelope and unique-tool collections include exact aggregate continuations.",
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
    "description": "Get one bounded page of lightweight tool-call summaries without exact values. Follow pagination, then use get_tool_call_detail with a call_id and field for exact chunks.",
    "input_schema": {
        "type": "object",
        "properties": {
            "trace_id": {
                "type": "string",
                "description": "Langfuse trace ID",
            },
            "page": {
                "type": "integer",
                "description": "Page number (1-indexed).",
                "default": 1,
                "minimum": 1,
            },
            "page_size": {
                "type": "integer",
                "description": "Summaries per page (environment-bounded).",
                "default": _TRACE_REVIEW_PAGE_SIZE,
                "minimum": 1,
                "maximum": _TRACE_REVIEW_PAGE_SIZE,
            },
            "item_offset": {
                "type": "integer",
                "description": "Continuation offset within the requested page.",
                "default": 0,
                "minimum": 0,
                "maximum": _TRACE_REVIEW_PAGE_SIZE,
            },
        },
        "required": ["trace_id"],
    },
}

GET_TOOL_CALLS_PAGE_TOOL = {
    "name": "get_tool_calls_page",
    "description": "Get paginated tool-call metadata and deterministic exact-field references, without inline input/result values. Use get_tool_call_detail to retrieve a selected field chunk.",
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
                "description": "Items per page (environment-bounded).",
                "default": _TRACE_REVIEW_PAGE_SIZE,
                "minimum": 1,
                "maximum": _TRACE_REVIEW_PAGE_SIZE,
            },
            "item_offset": {
                "type": "integer",
                "description": "Continuation offset within the requested page.",
                "default": 0,
                "minimum": 0,
                "maximum": _TRACE_REVIEW_PAGE_SIZE,
            },
            "tool_name": {
                "type": "string",
                "maxLength": _TRACE_REVIEW_SUMMARY_MAX_CHARS,
                "description": "Optional filter by tool name (e.g., 'search_document')",
            },
        },
        "required": ["trace_id"],
    },
}

GET_TOOL_CALL_DETAIL_TOOL = {
    "name": "get_tool_call_detail",
    "description": "Get one exact input, tool_result, thought, metadata, or call-scoped domain_envelope chunk for a selected tool call. Follow next_call until complete=true; concatenating serialized chunks reconstructs the hashed field exactly.",
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
            "field": {
                "type": "string",
                "enum": ["input", "tool_result", "thought", "metadata", "domain_envelope"],
                "description": "Exact field to retrieve independently.",
            },
            "start": {
                "type": "integer",
                "description": "Start character for exact chunk retrieval.",
                "default": 0,
                "minimum": 0,
            },
            "max_chars": {
                "type": "integer",
                "description": "Maximum exact characters in this chunk.",
                "default": _TRACE_REVIEW_CHUNK_MAX_CHARS,
                "minimum": 1,
                "maximum": _TRACE_REVIEW_CHUNK_MAX_CHARS,
            },
        },
        "required": ["trace_id", "call_id", "field"],
    },
}

GET_TRACE_CONVERSATION_TOOL = {
    "name": "get_trace_conversation",
    "description": "Get one exact user_query or assistant_response chunk. Follow next_call until complete=true; concatenating serialized chunks reconstructs the hashed field exactly.",
    "input_schema": {
        "type": "object",
        "properties": {
            "trace_id": {
                "type": "string",
                "description": "Langfuse trace ID",
            },
            "field": {
                "type": "string",
                "enum": ["user_query", "assistant_response"],
                "description": "Exact conversation field to retrieve.",
            },
            "start": {
                "type": "integer",
                "default": 0,
                "minimum": 0,
            },
            "max_chars": {
                "type": "integer",
                "default": _TRACE_REVIEW_CHUNK_MAX_CHARS,
                "minimum": 1,
                "maximum": _TRACE_REVIEW_CHUNK_MAX_CHARS,
            },
        },
        "required": ["trace_id", "field"],
    },
}

GET_TRACE_VIEW_TOOL = {
    "name": "get_trace_view",
    "description": "Get a specific analysis view with token metadata. Use its collection inventory and continuations for omitted summary evidence.",
    "input_schema": {
        "type": "object",
        "properties": {
            "trace_id": {
                "type": "string",
                "description": "Langfuse trace ID",
            },
            "view_name": {
                "type": "string",
                "enum": ["token_analysis", "agent_context", "pdf_citations", "document_hierarchy", "agent_configs", "group_context", "trace_summary", "tool_calls", "domain_envelope", "extraction_timeline", "evidence_revisions"],
                "description": "Which view to fetch",
            },
            **_AGGREGATE_PAGE_PROPERTIES,
        },
        "required": ["trace_id", "view_name"],
    },
}

SEARCH_TRACES_TOOL = {
    "name": "search_traces",
    "description": "Search the authenticated curator's Langfuse traces by session_id, trace name, document_id, run_id, extraction_id, or bounded timestamp window. Use this when the curator has a session/document/run ID but not a specific trace ID.",
    "input_schema": {
        "type": "object",
        "properties": {
            "session_id": {"type": "string", "maxLength": _TRACE_SEARCH_FILTER_MAX_CHARS, "description": "Optional Langfuse session ID."},
            "name": {"type": "string", "maxLength": _TRACE_SEARCH_FILTER_MAX_CHARS, "description": "Optional trace name filter."},
            "document_id": {"type": "string", "maxLength": _TRACE_SEARCH_FILTER_MAX_CHARS, "description": "Optional trace metadata.document_id filter."},
            "run_id": {"type": "string", "maxLength": _TRACE_SEARCH_FILTER_MAX_CHARS, "description": "Optional trace metadata.run_id filter."},
            "extraction_id": {"type": "string", "maxLength": _TRACE_SEARCH_FILTER_MAX_CHARS, "description": "Optional trace metadata.extraction_id filter."},
            "from_timestamp": {"type": "string", "maxLength": _TRACE_SEARCH_FILTER_MAX_CHARS, "description": "Optional ISO 8601 lower timestamp bound."},
            "to_timestamp": {"type": "string", "maxLength": _TRACE_SEARCH_FILTER_MAX_CHARS, "description": "Optional ISO 8601 upper timestamp bound."},
            "offset": {
                "type": "integer",
                "description": "Stable result offset from pagination.next_call.",
                "default": 0,
                "minimum": 0,
            },
            "limit": {
                "type": "integer",
                "description": "Maximum source matches requested before provider-envelope fitting.",
                "default": _TRACE_SEARCH_DEFAULT_LIMIT,
                "minimum": 1,
                "maximum": _TRACE_SEARCH_MAX_LIMIT,
            },
            "item_start": {
                "type": "integer",
                "description": "Exact JSON character cursor from pagination.next_call for one oversized trace reference.",
                "default": 0,
                "minimum": 0,
            },
        },
        "required": [],
    },
}

GET_EXTRACTION_DIAGNOSTIC_REPORT_TOOL = {
    "name": "get_extraction_diagnostic_report",
    "description": "Get a concise TraceReview report of what the extraction/builder/validator flow actually did. Use early for traces involving domain envelopes, extraction events, validation failures, lookup attempts, staged objects, patches, or finalize/envelope output. Includes ordered durable events, tool-call summaries, validation signals, and reasoning-summary status when available.",
    "input_schema": {
        "type": "object",
        "properties": {
            "trace_id": {"type": "string", "description": "Langfuse trace ID."},
            "session_id": {"type": "string", "description": "Optional Langfuse session ID for sibling trace expansion."},
            "feedback_id": {"type": "string", "description": "Optional feedback ID linked to stored trace artifacts."},
            "include_sibling_traces": {"type": "boolean", "description": "Include related traces from the same session when session_id is supplied.", "default": False},
            "refresh": {"type": "boolean", "description": "Refresh cached TraceReview analysis before rendering.", "default": False},
            "include_raw_args": {"type": "boolean", "description": "Include bounded raw tool argument summaries.", "default": False},
            "include_raw_outputs": {"type": "boolean", "description": "Include bounded raw tool output summaries.", "default": False},
            "tool_name": {"type": "string", "description": "Optional tool-name filter."},
            "event_type": {"type": "string", "description": "Optional extraction event type filter."},
            "candidate_id": {"type": "string", "description": "Optional candidate/object ID filter."},
            **_AGGREGATE_PAGE_PROPERTIES,
        },
        "required": ["trace_id"],
    },
}

GET_EXTRACTION_TIMELINE_TOOL = {
    "name": "get_extraction_timeline",
    "description": "Get the ordered extraction timeline and OpenAI/Agents SDK tool-call observations. Use when the diagnostic report points to a candidate, event type, or tool and you need more event-level detail.",
    "input_schema": GET_EXTRACTION_DIAGNOSTIC_REPORT_TOOL["input_schema"],
}

GET_EVIDENCE_REVISIONS_TOOL = {
    "name": "get_evidence_revisions",
    "description": "Inspect diagnostics-only hidden evidence revision history for same-ID evidence updates and backend-refused scope mutations. Use when troubleshooting evidence quote/provenance changes, validator evidence updates, or validator attempts to modify evidence outside its supplied field/object scope. Extractors create field-level evidence; validators receive scoped quote bundles, may search/read paper, and may update the same evidence record through scoped record_evidence. Live evidence fields are authoritative.",
    "input_schema": {
        "type": "object",
        "properties": {
            "trace_id": {"type": "string", "description": "Langfuse trace ID."},
            "session_id": {"type": "string", "description": "Optional Langfuse session ID for sibling trace expansion."},
            "feedback_id": {"type": "string", "description": "Optional feedback ID linked to stored trace artifacts."},
            "include_sibling_traces": {"type": "boolean", "description": "Include related traces from the same session when session_id is supplied.", "default": False},
            "refresh": {"type": "boolean", "description": "Refresh cached TraceReview analysis before rendering.", "default": False},
            "tool_name": {"type": "string", "description": "Optional tool-name filter."},
            "event_type": {"type": "string", "description": "Optional extraction event type filter."},
            "candidate_id": {"type": "string", "description": "Optional candidate/object ID filter."},
            **_AGGREGATE_PAGE_PROPERTIES,
        },
        "required": ["trace_id"],
    },
}

GET_TRACE_TREE_TOOL = {
    "name": "get_trace_tree",
    "description": "Get the Langfuse parent-child observation tree with payload references, model/agent hints, metadata, status, and usage/cost summaries. Full payload values are omitted.",
    "input_schema": {
        "type": "object",
        "properties": {
            "trace_id": {"type": "string", "description": "Langfuse trace ID."},
            **_AGGREGATE_PAGE_PROPERTIES,
        },
        "required": ["trace_id"],
    },
}

GET_TRACE_RECONSTRUCTION_TOOL = {
    "name": "get_trace_reconstruction",
    "description": "Get chronological Langfuse trace/model/tool/event reconstruction with payload references, never full payload values. Use get_trace_payload for selected exact content.",
    "input_schema": {
        "type": "object",
        "properties": {
            "trace_id": {"type": "string", "description": "Langfuse trace ID."},
            "section": {
                "type": "string",
                "enum": ["events"],
                "description": "Request `events`; omit for summary-only output.",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum events to return (environment-bounded).",
                "default": _TRACE_REVIEW_AGGREGATE_PAGE_SIZE,
                "minimum": 1,
                "maximum": _TRACE_REVIEW_AGGREGATE_PAGE_SIZE,
            },
            "offset": {
                "type": "integer",
                "description": "Event offset for pagination.",
                "default": 0,
                "minimum": 0,
            },
            "item_start": _AGGREGATE_PAGE_PROPERTIES["item_start"],
        },
        "required": ["trace_id"],
    },
}

GET_TRACE_PAYLOADS_TOOL = {
    "name": "get_trace_payloads",
    "description": "List exact Langfuse payloads available in a trace with payload_id, source observation, field, size, token estimate, hash, and preview. Use before get_trace_payload to find the exact prompt, model output, tool input/output, agent_config, or event_payload to inspect.",
    "input_schema": {
        "type": "object",
        "properties": {
            "trace_id": {"type": "string", "description": "Langfuse trace ID."},
            "section": {
                "type": "string",
                "enum": ["payloads"],
                "description": "Request `payloads`; omit for summary-only output.",
            },
            "sort": {
                "type": "string",
                "enum": ["largest", "chronological"],
                "description": "Sort largest first for prompt/context bloat, or chronological to follow the run.",
                "default": "largest",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum payload summaries to return (environment-bounded).",
                "default": _TRACE_REVIEW_AGGREGATE_PAGE_SIZE,
                "minimum": 1,
                "maximum": _TRACE_REVIEW_AGGREGATE_PAGE_SIZE,
            },
            "offset": {
                "type": "integer",
                "description": "Pagination offset.",
                "default": 0,
                "minimum": 0,
            },
            "item_start": _AGGREGATE_PAGE_PROPERTIES["item_start"],
        },
        "required": ["trace_id"],
    },
}

GET_TRACE_MODEL_LIVE_CONTEXT_TOOL = {
    "name": "get_trace_model_live_context",
    "description": (
        "Summarize model-live provider input sizes for a trace without raw "
        "prompt values. Use this before get_trace_payloads/get_trace_payload "
        "when investigating token budget, context replay, or prompt bloat."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "trace_id": {"type": "string", "description": "Langfuse trace ID."},
            **_AGGREGATE_PAGE_PROPERTIES,
        },
        "required": ["trace_id"],
    },
}

GET_TRACE_PAYLOAD_TOOL = {
    "name": "get_trace_payload",
    "description": "Retrieve one exact Langfuse payload by payload_id, or by scope/observation_id/field. Returns a chunk with start/end/next_start so large prompts/results can be inspected safely.",
    "input_schema": {
        "type": "object",
        "properties": {
            "trace_id": {"type": "string", "description": "Langfuse trace ID."},
            "payload_id": {"type": "string", "description": "Payload ID returned by get_trace_payloads, for example observation:<id>:output."},
            "scope": {"type": "string", "enum": ["trace", "observation"], "description": "Payload scope when payload_id is omitted."},
            "observation_id": {"type": "string", "description": "Observation/span ID when retrieving an observation payload."},
            "field": {
                "type": "string",
                "enum": ["input", "output", "metadata.agent_config", "metadata.event_payload"],
                "description": "Payload field when payload_id is omitted.",
            },
            "start": {
                "type": "integer",
                "description": "Start character for chunked retrieval.",
                "default": 0,
                "minimum": 0,
            },
            "max_chars": {
                "type": "integer",
                "description": "Maximum exact characters in this chunk.",
                "default": _TRACE_REVIEW_CHUNK_MAX_CHARS,
                "minimum": 1,
                "maximum": _TRACE_REVIEW_CHUNK_MAX_CHARS,
            },
        },
        "required": ["trace_id"],
    },
}

GET_TRACE_COSTS_TOOL = {
    "name": "get_trace_costs",
    "description": "Get Langfuse token and cost accounting by trace, agent, model, observation kind, and observation. Use for cost spikes, large-context investigation, and model/tool spend attribution.",
    "input_schema": {
        "type": "object",
        "properties": {
            "trace_id": {"type": "string", "description": "Langfuse trace ID."},
            **_AGGREGATE_PAGE_PROPERTIES,
        },
        "required": ["trace_id"],
    },
}

GET_TRACE_DUPLICATES_TOOL = {
    "name": "get_trace_duplicates",
    "description": "Get repeated payload fingerprints across trace and observation input/output payloads. Use to detect duplicate prompt stuffing, repeated context injection, or identical tool/model payloads.",
    "input_schema": {
        "type": "object",
        "properties": {
            "trace_id": {"type": "string", "description": "Langfuse trace ID."},
            **_AGGREGATE_PAGE_PROPERTIES,
        },
        "required": ["trace_id"],
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
                "description": "Number of recent logical log lines (environment-bounded).",
                "default": _SERVICE_LOG_DEFAULT_LINES,
                "minimum": 1,
                "maximum": _SERVICE_LOG_MAX_LINES,
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
                "maximum": _SERVICE_LOG_MAX_LOOKBACK_MINUTES,
            },
            "line_cursor": {
                "type": "string",
                "description": "Unix-nanosecond line cursor from page.next_call.",
            },
            "line_cursor_offset": {
                "type": "integer",
                "description": "Within-timestamp line offset from page.next_call.",
                "default": 0,
                "minimum": 0,
            },
            "char_cursor": {
                "type": "integer",
                "description": "Exact character cursor from page.next_call for an oversized line page.",
                "default": 0,
                "minimum": 0,
            },
        },
        "required": [],
    },
}

LIST_DOMAIN_ENVELOPES_TOOL = {
    "name": "list_domain_envelopes",
    "description": (
        "List visible persisted domain envelopes for a session, document, flow run, "
        "or domain pack. Use this before discussing live envelope state when the "
        "curator has not already supplied an envelope_id."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "session_id": {
                "type": "string",
                "description": "Optional curation review session UUID.",
            },
            "document_id": {
                "type": "string",
                "description": "Optional document UUID.",
            },
            "flow_run_id": {
                "type": "string",
                "description": "Optional flow run identifier.",
            },
            "domain_pack_id": {
                "type": "string",
                "description": "Optional domain pack ID to filter results.",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum envelopes to return (default: 10, max: 50).",
                "default": 10,
                "minimum": 1,
                "maximum": 50,
            },
            "cursor": {
                "type": "string",
                "description": "Deterministic decimal offset from next_request.",
            },
        },
        "required": [],
    },
}

GET_DOMAIN_ENVELOPE_STATE_TOOL = {
    "name": "get_domain_envelope_state",
    "description": (
        "Summarize current persisted domain envelope state, then retrieve revision-pinned "
        "bounded detail pages for objects, findings, lookup and validator summaries, "
        "history, projections, and reference indexes. Follow next_request until complete."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "envelope_id": {
                "type": "string",
                "description": "Persisted domain envelope ID.",
            },
            "revision": {
                "type": "integer",
                "minimum": 1,
                "description": "Revision from the summary or next_request; omit only for a fresh summary.",
            },
            "section": {
                "type": "string",
                "enum": [
                    "objects",
                    "validation_findings",
                    "projections",
                    "history",
                    "lookup_attempts",
                    "validator_summaries",
                    "object_ref_index",
                    "reference",
                ],
                "description": "Optional detail section. Omit for authoritative summary counts/status.",
            },
            "object_id": {
                "type": "string",
                "description": "Optional object_id or pending_ref_id filter.",
            },
            "field_path": {
                "type": "string",
                "description": "Optional field path filter for validation findings.",
            },
            "query": {
                "type": "string",
                "description": "Case-insensitive text query for supported detail sections.",
            },
            "include_object_payload": {
                "type": "boolean",
                "description": "Include bounded object payload JSON when true.",
                "default": False,
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": _DOMAIN_RUNTIME_INSPECTION_MAX_LIMIT,
                "default": _DOMAIN_RUNTIME_INSPECTION_DEFAULT_LIMIT,
                "description": "Bounded records to return from a detail section.",
            },
            "cursor": {
                "type": "string",
                "description": "Deterministic decimal offset from next_cursor.",
            },
            "reference_locator": {
                "type": "string",
                "description": "Opaque exact-reference selector from a reference manifest.",
            },
            "reference_sha256": {
                "type": "string",
                "description": "Expected canonical JSON hash from a reference manifest.",
            },
            "char_cursor": {
                "type": "integer",
                "minimum": 0,
                "description": "Exact character offset from a reference next_request.",
            },
        },
        "required": ["envelope_id"],
    },
}

GET_DOMAIN_PACK_VALIDATION_PLAN_TOOL = {
    "name": "get_domain_pack_validation_plan",
    "description": (
        "Summarize a domain pack's validation plan, then retrieve bounded detail "
        "pages by section. The summary reports active automatic validation defaults, "
        "under-development validator metadata, section counts, and valid detail "
        "requests. Request validator_bindings or validation_attachments details to "
        "find validator agent IDs for get_prompt(agent_id=...)."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "agent_id": {
                "type": "string",
                "description": "Optional agent ID whose domain pack should be inspected.",
            },
            "domain_pack_id": {
                "type": "string",
                "description": "Optional domain pack ID to inspect directly.",
            },
            "section": {
                "type": "string",
                "enum": [
                    "object_definitions",
                    "fields",
                    "validators",
                    "validator_bindings",
                    "field_policies",
                    "validation_attachments",
                ],
                "description": (
                    "Optional detail section. Omit for the compact summary and its "
                    "inventory of supported section requests."
                ),
            },
            "object_type": {
                "type": "string",
                "description": "Exact object type filter for supported sections.",
            },
            "field_path": {
                "type": "string",
                "description": "Exact field path filter for supported sections.",
            },
            "validator_id": {
                "type": "string",
                "description": "Exact validator ID filter for supported sections.",
            },
            "binding_id": {
                "type": "string",
                "description": "Exact validator binding ID filter for supported sections.",
            },
            "state": {
                "type": "string",
                "enum": ["active", "under_development"],
                "description": "Exact validator or binding state filter.",
            },
            "query": {
                "type": "string",
                "description": "Case-insensitive text query over section records.",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": _DOMAIN_PACK_VALIDATION_PLAN_MAX_LIMIT,
                "default": _DOMAIN_PACK_VALIDATION_PLAN_DEFAULT_LIMIT,
                "description": "Bounded records to return.",
            },
            "cursor": {
                "type": "string",
                "description": "Deterministic decimal offset from next_cursor.",
            },
        },
        "required": [],
    },
}

GET_DOMAIN_ENVELOPE_REVIEW_ROWS_TOOL = {
    "name": "get_domain_envelope_review_rows",
    "description": (
        "Summarize review rows from a persisted domain envelope revision, then retrieve "
        "bounded revision-pinned row pages. Follow next_request until complete."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "envelope_id": {
                "type": "string",
                "description": "Persisted domain envelope ID.",
            },
            "revision": {
                "type": "integer",
                "description": "Optional envelope revision. Defaults to the latest revision.",
            },
            "object_id": {
                "type": "string",
                "description": "Optional object_id filter.",
            },
            "section": {
                "type": "string",
                "enum": ["rows"],
                "description": "Omit for authoritative row totals; use rows for details.",
            },
            "query": {
                "type": "string",
                "description": "Case-insensitive text query over materialized rows.",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": _DOMAIN_RUNTIME_INSPECTION_MAX_LIMIT,
                "default": _DOMAIN_RUNTIME_INSPECTION_DEFAULT_LIMIT,
            },
            "cursor": {
                "type": "string",
                "description": "Deterministic decimal offset from next_cursor.",
            },
        },
        "required": ["envelope_id"],
    },
}

GET_EXPORT_SUBMISSION_READINESS_TOOL = {
    "name": "get_export_submission_readiness",
    "description": (
        "Summarize read-only projection/export/submission readiness with authoritative "
        "candidate, ready, and blocker totals, then page candidate or blocker details. "
        "Follow next_request until complete; this never executes export or submission."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "session_id": {
                "type": "string",
                "description": "Curation review session UUID.",
            },
            "candidate_ids": {
                "type": "array",
                "description": "Optional candidate UUIDs to inspect.",
                "items": {"type": "string"},
            },
            "expected_envelope_revisions": {
                "type": "object",
                "description": "Optional map of envelope_id to expected revision.",
                "additionalProperties": {"type": "integer"},
            },
            "readiness_token": {
                "type": "string",
                "description": (
                    "Bounded candidate-scope and revision identity from the summary or "
                    "next_request; replaces repeated candidate sets. Replay "
                    "expected_envelope_revisions when next_request includes it."
                ),
            },
            "mode": {
                "type": "string",
                "description": "Optional label for the readiness check, such as export or submission.",
                "default": "readiness",
            },
            "section": {
                "type": "string",
                "enum": ["candidates", "blockers"],
                "description": "Omit for authoritative readiness totals; select a detail page otherwise.",
            },
            "candidate_id": {"type": "string", "description": "Exact candidate filter."},
            "envelope_id": {"type": "string", "description": "Exact blocker envelope filter."},
            "object_id": {"type": "string", "description": "Exact blocker object filter."},
            "field_path": {"type": "string", "description": "Exact blocker field filter."},
            "code": {"type": "string", "description": "Exact blocker code filter."},
            "query": {"type": "string", "description": "Case-insensitive text detail filter."},
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": _DOMAIN_RUNTIME_INSPECTION_MAX_LIMIT,
                "default": _DOMAIN_RUNTIME_INSPECTION_DEFAULT_LIMIT,
            },
            "cursor": {
                "type": "string",
                "description": "Deterministic decimal offset from next_cursor.",
            },
        },
        "required": ["session_id"],
    },
}


COMMON_TOOLS = {
    "get_chat_turn",
    "get_chat_conversation",
    "list_recent_chats",
    "search_chat_history",
    "submit_prompt_suggestion",
    "report_tool_failure",
}
DOMAIN_ENVELOPE_TOOLS = {
    "list_domain_envelopes",
    "get_domain_envelope_state",
    "get_domain_pack_validation_plan",
    "get_domain_envelope_review_rows",
    "get_export_submission_readiness",
}
TOOL_METADATA_TOOLS = {
    "get_tool_inventory",
    "get_tool_details",
}
WORKSHOP_TOOLS = {
    "refresh_workshop_prompt",
    "update_workshop_prompt_draft",
}
TRACE_TOOLS = {
    "search_traces",
    "get_trace_summary",
    "get_tool_calls_summary",
    "get_tool_calls_page",
    "get_tool_call_detail",
    "get_trace_conversation",
    "get_extraction_diagnostic_report",
    "get_extraction_timeline",
    "get_evidence_revisions",
    "get_trace_tree",
    "get_trace_reconstruction",
    "get_trace_model_live_context",
    "get_trace_payloads",
    "get_trace_payload",
    "get_trace_costs",
    "get_trace_duplicates",
    "get_trace_view",
    "get_service_logs",
}
FLOW_TOOLS = {
    "create_flow",
    "validate_flow",
    "get_flow_templates",
    "get_current_flow",
    "get_current_flow_topology",
    "get_current_flow_node",
    "get_current_flow_instructions",
    "get_current_flow_projection_plan",
    "get_current_flow_validation_warnings",
    "get_current_flow_validation_schedule",
    "get_available_agents",
}
AGENTS_ONLY_DIAGNOSTIC_TOOLS = {
    "search_codebase",
    "read_source_file",
}

_BUILTIN_OPUS_TOOLS = (
    SUGGESTION_TOOL,
    REFRESH_WORKSHOP_PROMPT_TOOL,
    UPDATE_WORKSHOP_PROMPT_TOOL,
    REPORT_TOOL_FAILURE_TOOL,
    LIST_RECENT_CHATS_TOOL,
    SEARCH_CHAT_HISTORY_TOOL,
    GET_CHAT_CONVERSATION_TOOL,
    GET_CHAT_TURN_TOOL,
    SEARCH_TRACES_TOOL,
    GET_TRACE_SUMMARY_TOOL,
    GET_TOOL_CALLS_SUMMARY_TOOL,
    GET_TOOL_CALLS_PAGE_TOOL,
    GET_TOOL_CALL_DETAIL_TOOL,
    GET_TRACE_CONVERSATION_TOOL,
    GET_EXTRACTION_DIAGNOSTIC_REPORT_TOOL,
    GET_EXTRACTION_TIMELINE_TOOL,
    GET_EVIDENCE_REVISIONS_TOOL,
    GET_TRACE_TREE_TOOL,
    GET_TRACE_RECONSTRUCTION_TOOL,
    GET_TRACE_MODEL_LIVE_CONTEXT_TOOL,
    GET_TRACE_PAYLOADS_TOOL,
    GET_TRACE_PAYLOAD_TOOL,
    GET_TRACE_COSTS_TOOL,
    GET_TRACE_DUPLICATES_TOOL,
    GET_TRACE_VIEW_TOOL,
    GET_SERVICE_LOGS_TOOL,
    LIST_DOMAIN_ENVELOPES_TOOL,
    GET_DOMAIN_ENVELOPE_STATE_TOOL,
    GET_DOMAIN_PACK_VALIDATION_PLAN_TOOL,
    GET_DOMAIN_ENVELOPE_REVIEW_ROWS_TOOL,
    GET_EXPORT_SUBMISSION_READINESS_TOOL,
)


def get_builtin_tool_required_inputs(tool_name: str) -> tuple[str, ...] | None:
    """Return canonical required inputs, or None for a registry-provided tool."""

    for tool in _BUILTIN_OPUS_TOOLS:
        if tool.get("name") != tool_name:
            continue
        input_schema = tool.get("input_schema")
        if not isinstance(input_schema, dict):
            return ()
        required = input_schema.get("required", [])
        return tuple(str(field) for field in required)
    return None


@lru_cache(maxsize=1)
def _package_agent_only_diagnostic_tools() -> set[str]:
    from src.lib.agent_studio.catalog_service import get_tool_registry

    registry = get_tool_registry()
    tool_names: set[str] = set()
    for tool_id, tool_info in registry.items():
        agent_studio_metadata = tool_info.get("agent_studio")
        if not isinstance(agent_studio_metadata, dict):
            continue
        diagnostic = agent_studio_metadata.get("diagnostic")
        if isinstance(diagnostic, dict) and bool(diagnostic.get("enabled")):
            tool_names.add(str(tool_id))
    return tool_names


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

    if tool_name in DOMAIN_ENVELOPE_TOOLS:
        return active_tab in {"agents", "flows", "agent_workshop"}

    if tool_name in WORKSHOP_TOOLS:
        return active_tab == "agent_workshop" and bool(context and context.agent_workshop)

    if tool_name in FLOW_TOOLS:
        return active_tab == "flows"

    if tool_name in AGENTS_ONLY_DIAGNOSTIC_TOOLS or tool_name in _package_agent_only_diagnostic_tools():
        return active_tab == "agents"

    if tool_name == "get_prompt" or tool_name in TOOL_METADATA_TOOLS:
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
    Get all tools available to Agent Studio AI Chat.

    Combines the suggestion tool, workflow analysis tools, and diagnostic tools.
    """

    tools = [
        tool
        for tool in _BUILTIN_OPUS_TOOLS
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
    logger.debug("Loaded %s diagnostic tools for Agent Studio AI Chat", len(diagnostic_tools))

    return tools
