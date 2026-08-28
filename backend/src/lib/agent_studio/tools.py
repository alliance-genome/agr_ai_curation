"""
Workflow Analysis Tools

Provides tool functions for Opus to dynamically query trace data and Loki-backed service logs.
Used in the Workflow Analysis feature (formerly Prompt Explorer).

Token-Aware Tools (Claude-Specific Endpoints):
- get_trace_summary: Lightweight overview (~500 tokens)
- get_tool_calls_summary: Paginated call summaries
- get_tool_calls_page: Paginated call metadata and exact-field references
- get_tool_call_detail: Independently selected exact call-field chunks
- get_trace_conversation: Independently selected exact conversation-field chunks
- get_trace_view: Generic view access with token metadata
- search_traces: Find traces by session/document/run/extraction metadata
- get_extraction_diagnostic_report: Concise extraction/builder/validation report
- get_extraction_timeline: Ordered extraction and tool timeline
- get_evidence_revisions: Evidence source update and scope refusal diagnostics
- get_trace_tree: Langfuse parent/child observation tree
- get_trace_reconstruction: Ordered Langfuse reconstruction with payload refs
- get_trace_model_live_context: Provider-call input size classification
- get_trace_payloads: Payload inventory with sizes and previews
- get_trace_payload: Exact chunked payload retrieval
- get_trace_costs: Token and cost accounting
- get_trace_duplicates: Duplicate payload report

System Tools:
- get_service_logs: Service log retrieval
"""

import httpx
import os
import re
from typing import Dict, Any, Optional

from src.lib.loki_client import LOG_LEVEL_LABEL_PATTERNS
from src.lib.openai_agents.config import (
    get_agent_studio_endpoint_timeout_seconds,
    get_agent_studio_service_log_default_lines,
    get_agent_studio_service_log_max_lines,
    get_agent_studio_service_log_max_lookback_minutes,
    get_agent_studio_service_log_page_max_chars,
    get_agent_studio_service_log_timeout_seconds,
    get_agent_studio_trace_review_aggregate_page_size,
    get_agent_studio_trace_review_chunk_max_chars,
    get_agent_studio_trace_review_page_size,
    get_agent_studio_trace_tool_timeout_seconds,
)


VALID_SERVICE_LOG_LEVELS = frozenset(LOG_LEVEL_LABEL_PATTERNS)


def get_trace_source() -> str:
    """Get the default trace source for TraceReview API.

    Returns "local" by default (EC2 Langfuse), can be overridden
    via TRACE_REVIEW_SOURCE environment variable.
    """
    return os.getenv("TRACE_REVIEW_SOURCE", "local")


def get_trace_review_url() -> str:
    """Get the TraceReview service base URL.

    Uses TRACE_REVIEW_URL env var. Defaults to the trace_review_backend service
    on the shared Compose network. For local development outside Docker, set
    TRACE_REVIEW_URL=http://localhost:8001.
    """
    return os.getenv("TRACE_REVIEW_URL", "http://trace_review_backend:8001")


def _trace_review_request_headers() -> Dict[str, str]:
    """Authenticate backend-to-TraceReview requests when a service token exists."""
    token = os.getenv("TRACE_REVIEW_INTERNAL_API_TOKEN", "").strip()
    if not token:
        return {}
    return {"Authorization": f"Bearer {token}"}


# ============================================================================
# Validation Helpers
# ============================================================================

def validate_trace_id(trace_id: str) -> None:
    """Validate trace_id format.

    Langfuse trace IDs can be in two formats:
    1. UUID with hyphens: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx (36 chars)
    2. Hex string without hyphens: xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx (32 chars)

    The OpenAI Agents SDK generates trace IDs without hyphens.

    Args:
        trace_id: Langfuse trace ID to validate

    Raises:
        ValueError: If trace_id format is invalid
    """
    trace_id_lower = trace_id.lower()

    # Format 1: UUID with hyphens (e.g., 01784cd8-7512-4830-b5f5-a427502ab923)
    uuid_pattern = r'^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$'

    # Format 2: Hex string without hyphens (e.g., 856df16f1752cb53ee43dcb2f5ecfd16)
    hex_pattern = r'^[a-f0-9]{32}$'

    if not (re.match(uuid_pattern, trace_id_lower) or re.match(hex_pattern, trace_id_lower)):
        raise ValueError(
            f"Invalid trace_id format: {trace_id}. "
            f"Expected either UUID format (xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx) "
            f"or 32-character hex string."
        )


def validate_view(view: str) -> None:
    """Validate view name against available TraceReview views.

    Args:
        view: View name to validate

    Raises:
        ValueError: If view name is invalid
    """
    valid_views = [
        "summary", "tool_calls", "conversation", "pdf_citations",
        "token_analysis", "agent_context", "trace_summary",
        "document_hierarchy", "agent_configs", "group_context",
        "domain_envelope", "extraction_timeline", "evidence_revisions"
    ]
    if view not in valid_views:
        raise ValueError(f"Invalid view '{view}'. Must be one of: {', '.join(valid_views)}")


# ============================================================================
# Token-Aware Tool Functions (Claude-Specific Endpoints)
# ============================================================================

def _get_claude_api_url() -> str:
    """Get the Claude-specific TraceReview API base URL."""
    base = get_trace_review_url()
    return f"{base}/api/claude/traces"


def _response_detail(resp: httpx.Response) -> str:
    try:
        payload = resp.json()
    except Exception:
        return f"HTTP {resp.status_code}"
    detail = payload.get("detail") if isinstance(payload, dict) else None
    if isinstance(detail, str):
        return detail
    return f"HTTP {resp.status_code}"


async def _get_claude_endpoint(
    path: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    # Env-configurable via AGENT_STUDIO_ENDPOINT_TIMEOUT_SECONDS (default 30).
    timeout_seconds: float = get_agent_studio_endpoint_timeout_seconds(),
) -> Dict[str, Any]:
    request_params: Dict[str, Any] = {"source": get_trace_source()}
    if params:
        request_params.update({key: value for key, value in params.items() if value is not None})

    try:
        timeout = httpx.Timeout(timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(
                f"{_get_claude_api_url()}{path}",
                params=request_params,
                headers=_trace_review_request_headers(),
            )

        if resp.status_code == 200:
            payload = resp.json()
            return {
                "status": "success",
                "data": payload.get("data"),
                "token_info": payload.get("token_info"),
                "error": None,
            }
        if resp.status_code == 404:
            return {
                "status": "error",
                "data": None,
                "token_info": None,
                "error": _response_detail(resp),
                "help": "Verify the trace ID, payload ID, and TraceReview source",
            }
        if resp.status_code == 400:
            return {
                "status": "error",
                "data": None,
                "token_info": None,
                "error": f"Invalid request: {_response_detail(resp)}",
                "help": "Check the tool parameters and retry with a narrower request",
            }
        return {
            "status": "error",
            "data": None,
            "token_info": None,
            "error": f"TraceReview API error: {resp.status_code}",
            "help": "Check TraceReview service status",
        }
    except httpx.TimeoutException:
        return {
            "status": "error",
            "data": None,
            "token_info": None,
            "error": f"TraceReview service timeout ({timeout_seconds:g}s exceeded)",
            "help": "Retry with a narrower request or check service load",
        }
    except Exception as e:
        return {
            "status": "error",
            "data": None,
            "token_info": None,
            "error": f"Unexpected error: {str(e)}",
            "help": "Contact development team if issue persists",
        }


async def get_trace_summary(trace_id: str) -> Dict[str, Any]:
    """
    Get lightweight trace summary with token metadata.

    ALWAYS CALL THIS FIRST when analyzing a trace. Provides essential overview
    information with minimal token cost (~500 tokens).

    Args:
        trace_id: Langfuse trace ID (UUID with hyphens or 32-char hex string)

    Returns:
        {
            "status": "success" | "error",
            "data": {
                "trace_id": str,
                "trace_name": str,
                "duration_seconds": float,
                "total_cost": float,
                "total_tokens": int,
                "tool_call_count": int,
                "unique_tools": [str],
                "has_errors": bool,
                "context_overflow_detected": bool,
                "timestamp": str
            },
            "token_info": {
                "estimated_tokens": int,
                "within_budget": bool,
                "warning": str | None
            },
            "error": str | None
        }
    """
    try:
        validate_trace_id(trace_id)
        url = f"{_get_claude_api_url()}/{trace_id}/summary"
        timeout = httpx.Timeout(get_agent_studio_endpoint_timeout_seconds())

        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(
                url,
                params={"source": get_trace_source()},
                headers=_trace_review_request_headers(),
            )

            if resp.status_code == 200:
                data = resp.json()
                return {
                    "status": "success",
                    "data": data.get("data"),
                    "token_info": data.get("token_info"),
                    "error": None
                }
            elif resp.status_code == 404:
                return {
                    "status": "error",
                    "data": None,
                    "token_info": None,
                    "error": f"Trace {trace_id} not found",
                    "help": "Verify trace_id is correct and trace exists in Langfuse"
                }
            else:
                return {
                    "status": "error",
                    "data": None,
                    "token_info": None,
                    "error": f"API error: {resp.status_code}",
                    "help": "Check TraceReview service status"
                }

    except httpx.TimeoutException:
        return {
            "status": "error",
            "data": None,
            "token_info": None,
            "error": "TraceReview service timeout (30s exceeded)",
            "help": "Service may be under load or unavailable"
        }
    except ValueError as e:
        return {
            "status": "error",
            "data": None,
            "token_info": None,
            "error": str(e),
            "help": "Check trace_id format"
        }
    except Exception as e:
        return {
            "status": "error",
            "data": None,
            "token_info": None,
            "error": f"Unexpected error: {str(e)}",
            "help": "Contact development team if issue persists"
        }


async def get_tool_calls_summary(
    trace_id: str,
    page: int = 1,
    page_size: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Get one bounded page of tool-call summaries without exact results.

    Use this to see what tools were called before drilling into details.
    Token cost: ~100 tokens per call (much smaller than full tool_calls view).

    Args:
        trace_id: Langfuse trace ID
        page: One-indexed summary page.
        page_size: Requested summaries per page, capped by configuration.

    Returns:
        {
            "status": "success" | "error",
            "data": {
                "total_count": int,
                "unique_tools": [str],
                "tool_calls": [
                    {
                        "index": int,
                        "call_id": str,
                        "name": str,
                        "time": str,
                        "duration": str,
                        "status": str,
                        "input_summary": str,
                        "result_summary": str
                    }
                ],
                "pagination": {...},
                "next_call": {...} | None,
                "has_duplicates": bool,
                "duplicate_count": int
            },
            "token_info": {...},
            "error": str | None
        }
    """
    try:
        validate_trace_id(trace_id)
        url = f"{_get_claude_api_url()}/{trace_id}/tool_calls/summary"
        timeout = httpx.Timeout(30.0)

        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(
                url,
                params={
                    "source": get_trace_source(),
                    "page": max(1, page),
                    "page_size": max(1, min(
                        page_size or get_agent_studio_trace_review_page_size(),
                        get_agent_studio_trace_review_page_size(),
                    )),
                },
                headers=_trace_review_request_headers(),
            )

            if resp.status_code == 200:
                data = resp.json()
                return {
                    "status": "success",
                    "data": data.get("data"),
                    "token_info": data.get("token_info"),
                    "error": None
                }
            elif resp.status_code == 404:
                return {
                    "status": "error",
                    "data": None,
                    "token_info": None,
                    "error": f"Trace {trace_id} not found",
                    "help": "Call get_trace_summary first to verify trace exists"
                }
            else:
                return {
                    "status": "error",
                    "data": None,
                    "token_info": None,
                    "error": f"API error: {resp.status_code}",
                    "help": "Check TraceReview service status"
                }

    except httpx.TimeoutException:
        return {
            "status": "error",
            "data": None,
            "token_info": None,
            "error": "TraceReview service timeout",
            "help": "Service may be under load"
        }
    except ValueError as e:
        return {
            "status": "error",
            "data": None,
            "token_info": None,
            "error": str(e),
            "help": "Check trace_id format"
        }
    except Exception as e:
        return {
            "status": "error",
            "data": None,
            "token_info": None,
            "error": f"Unexpected error: {str(e)}",
            "help": "Contact development team if issue persists"
        }


async def get_tool_calls_page(
    trace_id: str,
    page: int = 1,
    page_size: Optional[int] = None,
    tool_name: Optional[str] = None
) -> Dict[str, Any]:
    """
    Get paginated tool-call metadata and exact-field references.

    Use for selecting one call and exact field before chunked retrieval.

    Args:
        trace_id: Langfuse trace ID
        page: Page number (1-indexed, default: 1)
        page_size: Items per page (default: 10, max: 20)
        tool_name: Optional filter by tool name (e.g., "search_document")

    Returns:
        {
            "status": "success" | "error",
            "tool_calls": [...],  # Full tool call details
            "pagination": {
                "page": int,
                "page_size": int,
                "total_items": int,
                "total_pages": int,
                "has_next": bool,
                "has_prev": bool
            },
            "token_info": {...},
            "filter_applied": str | None,
            "error": str | None
        }
    """
    try:
        validate_trace_id(trace_id)
        url = f"{_get_claude_api_url()}/{trace_id}/tool_calls"
        timeout = httpx.Timeout(30.0)

        params = {
            "source": get_trace_source(),
            "page": page,
            "page_size": max(1, min(
                page_size or get_agent_studio_trace_review_page_size(),
                get_agent_studio_trace_review_page_size(),
            )),
        }
        if tool_name:
            params["tool_name"] = tool_name

        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(
                url,
                params=params,
                headers=_trace_review_request_headers(),
            )

            if resp.status_code == 200:
                data = resp.json()
                return {
                    "status": "success",
                    "tool_calls": data.get("tool_calls"),
                    "pagination": data.get("pagination"),
                    "next_call": data.get("next_call"),
                    "token_info": data.get("token_info"),
                    "filter_applied": data.get("filter_applied"),
                    "error": None
                }
            elif resp.status_code == 400:
                return {
                    "status": "error",
                    "tool_calls": None,
                    "pagination": None,
                    "token_info": None,
                    "error": f"Invalid request: {resp.json().get('detail', 'Unknown')}",
                    "help": "Check page number is valid"
                }
            elif resp.status_code == 404:
                return {
                    "status": "error",
                    "tool_calls": None,
                    "pagination": None,
                    "token_info": None,
                    "error": f"Trace {trace_id} not found",
                    "help": "Call get_trace_summary first"
                }
            else:
                return {
                    "status": "error",
                    "tool_calls": None,
                    "pagination": None,
                    "token_info": None,
                    "error": f"API error: {resp.status_code}",
                    "help": "Check TraceReview service status"
                }

    except httpx.TimeoutException:
        return {
            "status": "error",
            "tool_calls": None,
            "pagination": None,
            "token_info": None,
            "error": "TraceReview service timeout",
            "help": "Service may be under load"
        }
    except ValueError as e:
        return {
            "status": "error",
            "tool_calls": None,
            "pagination": None,
            "token_info": None,
            "error": str(e),
            "help": "Check trace_id format"
        }
    except Exception as e:
        return {
            "status": "error",
            "tool_calls": None,
            "pagination": None,
            "token_info": None,
            "error": f"Unexpected error: {str(e)}",
            "help": "Contact development team"
        }


async def get_tool_call_detail(
    trace_id: str,
    call_id: str,
    field: str,
    start: int = 0,
    max_chars: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Get one exact field chunk for a single tool call.

    Select input or tool_result independently, then follow next_call until the
    response reports complete=true.

    Args:
        trace_id: Langfuse trace ID
        call_id: Either the OpenAI call_id (e.g., "call_oVv6...") or the
                 Langfuse observation id (e.g., "5d8254fb..."). Both work.
                 Prefer call_id when available (from tool_calls_summary).
        field: Exact field to retrieve: input or tool_result.
        start: Start character in the serialized exact field.
        max_chars: Requested exact characters, capped by configuration.

    Returns:
        {
            "status": "success" | "error",
            "tool_call": {
                "call_id": str,
                "name": str,
                "time": str,
                "duration": str,
                "status": str
            },
            "chunk": {
                "field_id": str,
                "sha256": str,
                "start": int,
                "end": int,
                "complete": bool,
                "next_call": {...} | None,
                "serialized": str
            },
            "token_info": {...},
            "error": str | None
        }
    """
    try:
        validate_trace_id(trace_id)
        url = f"{_get_claude_api_url()}/{trace_id}/tool_calls/{call_id}"
        timeout = httpx.Timeout(30.0)

        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(
                url,
                params={
                    "source": get_trace_source(),
                    "field": field,
                    "start": max(0, start),
                    "max_chars": max(1, min(
                        max_chars or get_agent_studio_trace_review_chunk_max_chars(),
                        get_agent_studio_trace_review_chunk_max_chars(),
                    )),
                },
                headers=_trace_review_request_headers(),
            )

            if resp.status_code == 200:
                data = resp.json()
                return {
                    "status": "success",
                    "tool_call": data.get("tool_call"),
                    "chunk": data.get("chunk"),
                    "token_info": data.get("token_info"),
                    "error": None
                }
            elif resp.status_code == 404:
                return {
                    "status": "error",
                    "tool_call": None,
                    "token_info": None,
                    "error": f"Tool call '{call_id}' not found in trace {trace_id}",
                    "help": "Verify call_id from get_tool_calls_summary response"
                }
            else:
                return {
                    "status": "error",
                    "tool_call": None,
                    "token_info": None,
                    "error": f"API error: {resp.status_code}",
                    "help": "Check TraceReview service status"
                }

    except httpx.TimeoutException:
        return {
            "status": "error",
            "tool_call": None,
            "token_info": None,
            "error": "TraceReview service timeout",
            "help": "Service may be under load"
        }
    except ValueError as e:
        return {
            "status": "error",
            "tool_call": None,
            "token_info": None,
            "error": str(e),
            "help": "Check trace_id format"
        }
    except Exception as e:
        return {
            "status": "error",
            "tool_call": None,
            "token_info": None,
            "error": f"Unexpected error: {str(e)}",
            "help": "Contact development team"
        }


async def get_trace_conversation(
    trace_id: str,
    field: str,
    start: int = 0,
    max_chars: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Get one exact selected conversation-field chunk.

    Select user_query or assistant_response independently, then follow
    next_call until the response reports complete=true.

    Args:
        trace_id: Langfuse trace ID
        field: Exact field to retrieve: user_query or assistant_response.
        start: Start character in the selected field.
        max_chars: Requested exact characters, capped by configuration.

    Returns:
        {
            "status": "success" | "error",
            "data": {
                "field": str,
                "chunk": {
                    "field_id": str,
                    "sha256": str,
                    "start": int,
                    "end": int,
                    "complete": bool,
                    "next_call": {...} | None,
                    "serialized": str
                },
                "domain_envelope": {...} | None
            },
            "token_info": {...},
            "error": str | None
        }
    """
    try:
        validate_trace_id(trace_id)
        url = f"{_get_claude_api_url()}/{trace_id}/conversation"
        timeout = httpx.Timeout(30.0)

        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(
                url,
                params={
                    "source": get_trace_source(),
                    "field": field,
                    "start": max(0, start),
                    "max_chars": max(1, min(
                        max_chars or get_agent_studio_trace_review_chunk_max_chars(),
                        get_agent_studio_trace_review_chunk_max_chars(),
                    )),
                },
                headers=_trace_review_request_headers(),
            )

            if resp.status_code == 200:
                data = resp.json()
                return {
                    "status": "success",
                    "data": data.get("data"),
                    "token_info": data.get("token_info"),
                    "error": None
                }
            elif resp.status_code == 404:
                return {
                    "status": "error",
                    "data": None,
                    "token_info": None,
                    "error": f"Trace {trace_id} not found",
                    "help": "Call get_trace_summary first"
                }
            else:
                return {
                    "status": "error",
                    "data": None,
                    "token_info": None,
                    "error": f"API error: {resp.status_code}",
                    "help": "Check TraceReview service status"
                }

    except httpx.TimeoutException:
        return {
            "status": "error",
            "data": None,
            "token_info": None,
            "error": "TraceReview service timeout",
            "help": "Service may be under load"
        }
    except ValueError as e:
        return {
            "status": "error",
            "data": None,
            "token_info": None,
            "error": str(e),
            "help": "Check trace_id format"
        }
    except Exception as e:
        return {
            "status": "error",
            "data": None,
            "token_info": None,
            "error": f"Unexpected error: {str(e)}",
            "help": "Contact development team"
        }


async def get_trace_view(
    trace_id: str,
    view_name: str,
    section: Optional[str] = None,
    offset: int = 0,
    limit: Optional[int] = None,
    item_start: int = 0,
) -> Dict[str, Any]:
    """
    Get a specific analysis view with token metadata.

    Use for specialized views not covered by the primary tools.

    Args:
        trace_id: Langfuse trace ID
        view_name: One of: token_analysis, agent_context, pdf_citations,
                   document_hierarchy, agent_configs, group_context, trace_summary
        item_start: Exact JSON character cursor for one oversized aggregate item

    Returns:
        {
            "status": "success" | "error",
            "data": {...},
            "token_info": {...},
            "error": str | None
        }
    """
    try:
        validate_trace_id(trace_id)

        valid_views = [
            "token_analysis", "agent_context", "pdf_citations",
            "document_hierarchy", "agent_configs", "group_context",
            "trace_summary", "domain_envelope", "extraction_timeline", "evidence_revisions"
        ]
        if view_name not in valid_views:
            return {
                "status": "error",
                "data": None,
                "token_info": None,
                "error": f"Invalid view '{view_name}'. Valid views: {', '.join(valid_views)}",
                "help": "Use get_trace_summary for basic info, get_tool_calls_summary for tool calls"
            }

        url = f"{_get_claude_api_url()}/{trace_id}/views/{view_name}"
        timeout = httpx.Timeout(get_agent_studio_endpoint_timeout_seconds())

        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(
                url,
                params={
                    "source": get_trace_source(),
                    "section": section,
                    "offset": max(0, offset),
                    "limit": min(
                        max(1, limit or get_agent_studio_trace_review_aggregate_page_size()),
                        get_agent_studio_trace_review_aggregate_page_size(),
                    ),
                    "item_start": max(0, item_start),
                },
                headers=_trace_review_request_headers(),
            )

            if resp.status_code == 200:
                data = resp.json()
                return {
                    "status": "success",
                    "data": data.get("data"),
                    "token_info": data.get("token_info"),
                    "error": None
                }
            elif resp.status_code == 400:
                return {
                    "status": "error",
                    "data": None,
                    "token_info": None,
                    "error": resp.json().get("detail", "Invalid view"),
                    "help": f"Valid views: {', '.join(valid_views)}"
                }
            elif resp.status_code == 404:
                return {
                    "status": "error",
                    "data": None,
                    "token_info": None,
                    "error": f"Trace {trace_id} or view '{view_name}' not found",
                    "help": "Call get_trace_summary first"
                }
            else:
                return {
                    "status": "error",
                    "data": None,
                    "token_info": None,
                    "error": f"API error: {resp.status_code}",
                    "help": "Check TraceReview service status"
                }

    except httpx.TimeoutException:
        return {
            "status": "error",
            "data": None,
            "token_info": None,
            "error": "TraceReview service timeout",
            "help": "Service may be under load"
        }
    except ValueError as e:
        return {
            "status": "error",
            "data": None,
            "token_info": None,
            "error": str(e),
            "help": "Check trace_id format"
        }
    except Exception as e:
        return {
            "status": "error",
            "data": None,
            "token_info": None,
            "error": f"Unexpected error: {str(e)}",
            "help": "Contact development team"
        }


async def search_traces(
    session_id: Optional[str] = None,
    user_id: Optional[str] = None,
    name: Optional[str] = None,
    document_id: Optional[str] = None,
    run_id: Optional[str] = None,
    extraction_id: Optional[str] = None,
    from_timestamp: Optional[str] = None,
    to_timestamp: Optional[str] = None,
    limit: int = 25,
) -> Dict[str, Any]:
    """Search Langfuse traces by bounded session, user, metadata, name, or time filters."""
    if not any([session_id, user_id, name, document_id, run_id, extraction_id, from_timestamp, to_timestamp]):
        return {
            "status": "error",
            "data": None,
            "token_info": None,
            "error": "At least one search filter is required",
            "help": "Provide session_id, user_id, name, document_id, run_id, extraction_id, from_timestamp, or to_timestamp",
        }

    return await _get_claude_endpoint(
        "/search",
        params={
            "session_id": session_id,
            "user_id": user_id,
            "name": name,
            "document_id": document_id,
            "run_id": run_id,
            "extraction_id": extraction_id,
            "from_timestamp": from_timestamp,
            "to_timestamp": to_timestamp,
            "limit": max(1, min(limit, 100)),
        },
    )


async def get_extraction_diagnostic_report(
    trace_id: str,
    session_id: Optional[str] = None,
    feedback_id: Optional[str] = None,
    include_sibling_traces: bool = False,
    refresh: bool = False,
    include_raw_args: bool = False,
    include_raw_outputs: bool = False,
    tool_name: Optional[str] = None,
    event_type: Optional[str] = None,
    candidate_id: Optional[str] = None,
    section: Optional[str] = None,
    offset: int = 0,
    limit: Optional[int] = None,
) -> Dict[str, Any]:
    """Get concise extraction, builder, tool, and validation diagnostics for a trace."""
    try:
        validate_trace_id(trace_id)
    except ValueError as e:
        return {
            "status": "error",
            "data": None,
            "token_info": None,
            "error": str(e),
            "help": "Check trace_id format",
        }

    return await _get_claude_endpoint(
        f"/{trace_id}/diagnostic_report",
        params={
            "session_id": session_id,
            "feedback_id": feedback_id,
            "include_sibling_traces": include_sibling_traces,
            "refresh": refresh,
            "include_raw_args": include_raw_args,
            "include_raw_outputs": include_raw_outputs,
            "tool_name": tool_name,
            "event_type": event_type,
            "candidate_id": candidate_id,
            "section": section,
            "offset": max(0, offset),
            "limit": min(max(1, limit or get_agent_studio_trace_review_aggregate_page_size()), get_agent_studio_trace_review_aggregate_page_size()),
        },
    )


async def get_extraction_timeline(
    trace_id: str,
    session_id: Optional[str] = None,
    feedback_id: Optional[str] = None,
    include_sibling_traces: bool = False,
    refresh: bool = False,
    include_raw_args: bool = False,
    include_raw_outputs: bool = False,
    tool_name: Optional[str] = None,
    event_type: Optional[str] = None,
    candidate_id: Optional[str] = None,
    section: Optional[str] = None,
    offset: int = 0,
    limit: Optional[int] = None,
) -> Dict[str, Any]:
    """Get ordered extraction events and OpenAI/Agents SDK tool-call observations."""
    try:
        validate_trace_id(trace_id)
    except ValueError as e:
        return {
            "status": "error",
            "data": None,
            "token_info": None,
            "error": str(e),
            "help": "Check trace_id format",
        }

    return await _get_claude_endpoint(
        f"/{trace_id}/extraction_timeline",
        params={
            "session_id": session_id,
            "feedback_id": feedback_id,
            "include_sibling_traces": include_sibling_traces,
            "refresh": refresh,
            "include_raw_args": include_raw_args,
            "include_raw_outputs": include_raw_outputs,
            "tool_name": tool_name,
            "event_type": event_type,
            "candidate_id": candidate_id,
            "section": section,
            "offset": max(0, offset),
            "limit": min(max(1, limit or get_agent_studio_trace_review_aggregate_page_size()), get_agent_studio_trace_review_aggregate_page_size()),
        },
    )


async def get_evidence_revisions(
    trace_id: str,
    session_id: Optional[str] = None,
    feedback_id: Optional[str] = None,
    include_sibling_traces: bool = False,
    refresh: bool = False,
    tool_name: Optional[str] = None,
    event_type: Optional[str] = None,
    candidate_id: Optional[str] = None,
    section: Optional[str] = None,
    offset: int = 0,
    limit: Optional[int] = None,
) -> Dict[str, Any]:
    """Get diagnostics-only evidence source updates and scope refusals."""
    try:
        validate_trace_id(trace_id)
    except ValueError as e:
        return {
            "status": "error",
            "data": None,
            "token_info": None,
            "error": str(e),
            "help": "Check trace_id format",
        }

    return await _get_claude_endpoint(
        f"/{trace_id}/evidence_revisions",
        params={
            "session_id": session_id,
            "feedback_id": feedback_id,
            "include_sibling_traces": include_sibling_traces,
            "refresh": refresh,
            "tool_name": tool_name,
            "event_type": event_type,
            "candidate_id": candidate_id,
            "section": section,
            "offset": max(0, offset),
            "limit": min(max(1, limit or get_agent_studio_trace_review_aggregate_page_size()), get_agent_studio_trace_review_aggregate_page_size()),
        },
    )


async def get_trace_tree(
    trace_id: str,
    section: Optional[str] = None,
    offset: int = 0,
    limit: Optional[int] = None,
) -> Dict[str, Any]:
    """Get the Langfuse parent/child observation tree with payload references."""
    try:
        validate_trace_id(trace_id)
    except ValueError as e:
        return {
            "status": "error",
            "data": None,
            "token_info": None,
            "error": str(e),
            "help": "Check trace_id format",
        }
    return await _get_claude_endpoint(
        f"/{trace_id}/langfuse_tree",
        params={
            "section": section,
            "offset": max(0, offset),
            "limit": min(max(1, limit or get_agent_studio_trace_review_aggregate_page_size()), get_agent_studio_trace_review_aggregate_page_size()),
        },
    )


async def get_trace_reconstruction(
    trace_id: str,
    limit: Optional[int] = None,
    offset: int = 0,
    section: Optional[str] = None,
) -> Dict[str, Any]:
    """Get chronological Langfuse reconstruction events with payload references."""
    try:
        validate_trace_id(trace_id)
    except ValueError as e:
        return {
            "status": "error",
            "data": None,
            "token_info": None,
            "error": str(e),
            "help": "Check trace_id format",
        }
    return await _get_claude_endpoint(
        f"/{trace_id}/langfuse_reconstruction",
        params={
            "limit": min(max(1, limit or get_agent_studio_trace_review_aggregate_page_size()), get_agent_studio_trace_review_aggregate_page_size()),
            "offset": max(0, offset),
            "section": section,
        },
        # Env-configurable via AGENT_STUDIO_TRACE_TOOL_TIMEOUT_SECONDS (default 45).
        timeout_seconds=get_agent_studio_trace_tool_timeout_seconds(),
    )


async def get_trace_payloads(
    trace_id: str,
    sort: str = "largest",
    limit: Optional[int] = None,
    offset: int = 0,
    section: Optional[str] = None,
) -> Dict[str, Any]:
    """List Langfuse trace payload summaries with sizes, hashes, and previews."""
    try:
        validate_trace_id(trace_id)
    except ValueError as e:
        return {
            "status": "error",
            "data": None,
            "token_info": None,
            "error": str(e),
            "help": "Check trace_id format",
        }
    if sort not in {"largest", "chronological"}:
        return {
            "status": "error",
            "data": None,
            "token_info": None,
            "error": "sort must be 'largest' or 'chronological'",
            "help": "Use sort='largest' to find big prompts/results or sort='chronological' to follow the run",
        }
    return await _get_claude_endpoint(
        f"/{trace_id}/langfuse_payloads",
        params={
            "sort": sort,
            "limit": max(1, min(
                limit or get_agent_studio_trace_review_aggregate_page_size(),
                get_agent_studio_trace_review_aggregate_page_size(),
            )),
            "offset": max(0, offset),
            "section": section,
        },
        # Env-configurable via AGENT_STUDIO_TRACE_TOOL_TIMEOUT_SECONDS (default 45).
        timeout_seconds=get_agent_studio_trace_tool_timeout_seconds(),
    )


async def get_trace_model_live_context(
    trace_id: str,
    section: Optional[str] = None,
    offset: int = 0,
    limit: Optional[int] = None,
) -> Dict[str, Any]:
    """Get bounded provider-call input size classification for a trace."""
    try:
        validate_trace_id(trace_id)
    except ValueError as e:
        return {
            "status": "error",
            "data": None,
            "token_info": None,
            "error": str(e),
            "help": "Check trace_id format",
        }
    return await _get_claude_endpoint(
        f"/{trace_id}/model_live_context",
        params={"section": section, "offset": max(0, offset), "limit": min(max(1, limit or get_agent_studio_trace_review_aggregate_page_size()), get_agent_studio_trace_review_aggregate_page_size())},
    )


async def get_trace_payload(
    trace_id: str,
    payload_id: Optional[str] = None,
    scope: Optional[str] = None,
    observation_id: Optional[str] = None,
    field: Optional[str] = None,
    start: int = 0,
    max_chars: Optional[int] = None,
) -> Dict[str, Any]:
    """Retrieve one exact Langfuse payload by ID or scope/observation/field."""
    try:
        validate_trace_id(trace_id)
    except ValueError as e:
        return {
            "status": "error",
            "data": None,
            "token_info": None,
            "error": str(e),
            "help": "Check trace_id format",
        }
    if not payload_id and not field:
        return {
            "status": "error",
            "data": None,
            "token_info": None,
            "error": "Missing payload selector: provide payload_id or field",
            "help": "Call get_trace_payloads first, then pass payload_id; or provide scope/observation_id/field",
        }
    return await _get_claude_endpoint(
        f"/{trace_id}/langfuse_payload",
        params={
            "payload_id": payload_id,
            "scope": scope,
            "observation_id": observation_id,
            "field": field,
            "start": max(0, start),
            "max_chars": max(1, min(
                max_chars or get_agent_studio_trace_review_chunk_max_chars(),
                get_agent_studio_trace_review_chunk_max_chars(),
            )),
        },
        # Env-configurable via AGENT_STUDIO_TRACE_TOOL_TIMEOUT_SECONDS (default 45).
        timeout_seconds=get_agent_studio_trace_tool_timeout_seconds(),
    )


async def get_trace_costs(trace_id: str, section: Optional[str] = None, offset: int = 0, limit: Optional[int] = None) -> Dict[str, Any]:
    """Get Langfuse token and cost accounting for the trace."""
    try:
        validate_trace_id(trace_id)
    except ValueError as e:
        return {
            "status": "error",
            "data": None,
            "token_info": None,
            "error": str(e),
            "help": "Check trace_id format",
        }
    return await _get_claude_endpoint(f"/{trace_id}/langfuse_costs", params={"section": section, "offset": max(0, offset), "limit": min(max(1, limit or get_agent_studio_trace_review_aggregate_page_size()), get_agent_studio_trace_review_aggregate_page_size())})


async def get_trace_duplicates(trace_id: str, section: Optional[str] = None, offset: int = 0, limit: Optional[int] = None) -> Dict[str, Any]:
    """Get duplicate payload fingerprints across trace and observation payloads."""
    try:
        validate_trace_id(trace_id)
    except ValueError as e:
        return {
            "status": "error",
            "data": None,
            "token_info": None,
            "error": str(e),
            "help": "Check trace_id format",
        }
    return await _get_claude_endpoint(f"/{trace_id}/langfuse_duplicates", params={"section": section, "offset": max(0, offset), "limit": min(max(1, limit or get_agent_studio_trace_review_aggregate_page_size()), get_agent_studio_trace_review_aggregate_page_size())})


# ============================================================================
# System Tools
# ============================================================================

async def get_service_logs(
    container: str = "backend",
    lines: int = get_agent_studio_service_log_default_lines(),
    level: Optional[str] = None,
    since: Optional[int] = None,
    line_cursor: Optional[str] = None,
    line_cursor_offset: int = 0,
    char_cursor: int = 0,
) -> Dict[str, Any]:
    """
    Retrieve Loki-backed service logs for troubleshooting.

    Allows Opus to access internal service logs through `/api/logs/{container}`
    when helping curators debug issues.

    Args:
        container: Service/container name (default: "backend")
            Valid options mirror `/api/logs/{container}` (backend, frontend,
            weaviate, postgres, langfuse, redis, clickhouse, minio,
            trace_review_backend)
        lines: Number of recent logical log lines (environment-bounded)
        level: Optional log level filter (DEBUG, INFO, WARN, ERROR, FATAL)
        since: Optional time filter in minutes ago
        line_cursor: Exact Unix-nanosecond line cursor returned by the prior page
        line_cursor_offset: Within-timestamp line offset returned by the prior page
        char_cursor: Exact character cursor within the selected line page

    Returns:
        {
            "status": "success" | "error",
            "data": {
                "container": str,
                "lines_requested": int,
                "lines": int,
                "logs": str
            } | None,
            "error": str | None,
            "help": str (if error)
        }
    """
    try:
        allowed_levels = ", ".join(sorted(VALID_SERVICE_LOG_LEVELS))
        lines = max(1, min(lines, get_agent_studio_service_log_max_lines()))
        params: Dict[str, Any] = {
            "lines": lines,
            "max_chars": get_agent_studio_service_log_page_max_chars(),
            "char_cursor": max(0, char_cursor),
            "line_cursor_offset": max(0, line_cursor_offset),
        }
        if line_cursor:
            params["line_cursor"] = line_cursor

        if level is not None:
            if not isinstance(level, str):
                return {
                    "status": "error",
                    "data": None,
                    "error": "Log level filter must be a string",
                    "help": f"Use one of: {allowed_levels}"
                }
            normalized_level = level.strip().upper()
            if not normalized_level:
                return {
                    "status": "error",
                    "data": None,
                    "error": "Log level filter cannot be blank",
                    "help": f"Use one of: {allowed_levels}"
                }
            if normalized_level not in VALID_SERVICE_LOG_LEVELS:
                return {
                    "status": "error",
                    "data": None,
                    "error": f"Unsupported log level filter: {normalized_level}",
                    "help": f"Use one of: {allowed_levels}"
                }
            params["level"] = normalized_level

        if since is not None:
            if isinstance(since, bool) or not isinstance(since, int):
                return {
                    "status": "error",
                    "data": None,
                    "error": "Time filter must be an integer number of minutes",
                    "help": "Use a positive integer such as 15 for the last 15 minutes"
                }
            if since < 1:
                return {
                    "status": "error",
                    "data": None,
                    "error": "Time filter must be at least 1 minute",
                    "help": "Use a positive integer such as 15 for the last 15 minutes"
                }
            params["since"] = min(since, get_agent_studio_service_log_max_lookback_minutes())

        # Call internal logs API endpoint
        timeout_seconds = get_agent_studio_service_log_timeout_seconds()
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_seconds)) as client:
            response = await client.get(
                f"http://localhost:8000/api/logs/{container}",
                params=params
            )

            if response.status_code == 200:
                data = response.json()
                return {
                    "status": "success",
                    "data": {
                        "container": data["container"],
                        "lines_requested": lines,
                        **data,
                    },
                    "error": None
                }
            elif response.status_code == 400:
                error_detail = response.json().get("detail", "Invalid log request")
                return {
                    "status": "error",
                    "data": None,
                    "error": error_detail,
                    "help": "Check the service name and optional log filters"
                }
            else:
                error_detail = response.json().get("detail", "Unknown error")
                return {
                    "status": "error",
                    "data": None,
                    "error": f"Logs API error: {error_detail}",
                    "help": "Check the backend logs API and Loki availability"
                }

    except httpx.TimeoutException:
        return {
            "status": "error",
            "data": None,
            "error": f"Timeout retrieving logs ({get_agent_studio_service_log_timeout_seconds():g}s exceeded)",
            "help": "The logs API may be under load or the query window may be too large"
        }
    except httpx.ConnectError:
        return {
            "status": "error",
            "data": None,
            "error": "Cannot connect to logs API endpoint",
            "help": "Ensure the backend service is running and /api/logs is reachable"
        }
    except Exception as e:
        return {
            "status": "error",
            "data": None,
            "error": f"Failed to retrieve logs: {str(e)}",
            "help": "Verify the logs API is reachable and the service name is correct"
        }
