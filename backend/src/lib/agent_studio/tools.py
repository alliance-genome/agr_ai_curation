"""
Workflow Analysis Tools

Provides tool functions for Opus 4.5 to dynamically query trace data and Docker logs.
Used in the Workflow Analysis feature (formerly Prompt Explorer).

Token-Aware Tools (Claude-Specific Endpoints):
- get_trace_summary: Lightweight overview (~500 tokens)
- get_tool_calls_summary: All tool calls with summaries (~100 tokens/call)
- get_tool_calls_page: Paginated full tool calls with filtering
- get_tool_call_detail: Single tool call detail
- get_trace_conversation: User query and assistant response
- get_trace_view: Generic view access with token metadata

System Tools:
- get_docker_logs: Container log retrieval
"""

import httpx
import os
import re
from typing import Dict, Any, Optional


def get_trace_source() -> str:
    """Get the default trace source for TraceReview API.

    Returns "local" by default (EC2 Langfuse), can be overridden
    via TRACE_REVIEW_SOURCE environment variable.
    """
    return os.getenv("TRACE_REVIEW_SOURCE", "local")


def get_trace_review_url() -> str:
    """Get the TraceReview service base URL.

    Uses TRACE_REVIEW_URL env var. Defaults to http://172.17.0.1:8001 for
    Docker bridge network (backend container reaching host-networked trace_review).
    For local development outside Docker, set TRACE_REVIEW_URL=http://localhost:8001.
    """
    return os.getenv("TRACE_REVIEW_URL", "http://172.17.0.1:8001")


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
        "document_hierarchy", "agent_configs", "mod_context"
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
        timeout = httpx.Timeout(30.0)

        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url, params={"source": get_trace_source()})

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


async def get_tool_calls_summary(trace_id: str) -> Dict[str, Any]:
    """
    Get lightweight summary of ALL tool calls without full results.

    Use this to see what tools were called before drilling into details.
    Token cost: ~100 tokens per call (much smaller than full tool_calls view).

    Args:
        trace_id: Langfuse trace ID

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
            resp = await client.get(url, params={"source": get_trace_source()})

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
    page_size: int = 10,
    tool_name: Optional[str] = None
) -> Dict[str, Any]:
    """
    Get paginated tool calls with full details.

    Use for detailed analysis of specific calls. Results are automatically
    truncated to fit within token budget.

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
            "page_size": min(page_size, 20)  # Enforce max
        }
        if tool_name:
            params["tool_name"] = tool_name

        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url, params=params)

            if resp.status_code == 200:
                data = resp.json()
                return {
                    "status": "success",
                    "tool_calls": data.get("tool_calls"),
                    "pagination": data.get("pagination"),
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


async def get_tool_call_detail(trace_id: str, call_id: str) -> Dict[str, Any]:
    """
    Get full details for a single tool call.

    Use when you need complete input/output for a specific call identified
    from get_tool_calls_summary or get_tool_calls_page.

    Args:
        trace_id: Langfuse trace ID
        call_id: Either the OpenAI call_id (e.g., "call_oVv6...") or the
                 Langfuse observation id (e.g., "5d8254fb..."). Both work.
                 Prefer call_id when available (from tool_calls_summary).

    Returns:
        {
            "status": "success" | "error",
            "tool_call": {
                "call_id": str,
                "name": str,
                "time": str,
                "duration": str,
                "status": str,
                "input": {...},
                "tool_result": {...}
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
            resp = await client.get(url, params={"source": get_trace_source()})

            if resp.status_code == 200:
                data = resp.json()
                return {
                    "status": "success",
                    "tool_call": data.get("tool_call"),
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


async def get_trace_conversation(trace_id: str) -> Dict[str, Any]:
    """
    Get the user's query and assistant's final response.

    Use when you need to see what the curator asked and what the AI answered.
    Token cost varies by response length.

    Args:
        trace_id: Langfuse trace ID

    Returns:
        {
            "status": "success" | "error",
            "data": {
                "user_query": str,
                "assistant_response": str,
                "response_length": int
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
            resp = await client.get(url, params={"source": get_trace_source()})

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


async def get_trace_view(trace_id: str, view_name: str) -> Dict[str, Any]:
    """
    Get a specific analysis view with token metadata.

    Use for specialized views not covered by the primary tools.

    Args:
        trace_id: Langfuse trace ID
        view_name: One of: token_analysis, agent_context, pdf_citations,
                   document_hierarchy, agent_configs, mod_context, trace_summary

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
            "document_hierarchy", "agent_configs", "mod_context", "trace_summary"
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
        timeout = httpx.Timeout(30.0)

        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url, params={"source": get_trace_source()})

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


# ============================================================================
# System Tools
# ============================================================================

async def get_docker_logs(container: str = "backend", lines: int = 2000) -> Dict[str, Any]:
    """
    Retrieve Docker container logs for troubleshooting.

    Allows Opus to access container logs when helping curators debug issues.

    Args:
        container: Container name (default: "backend")
            Valid options: backend, frontend, weaviate, postgres
        lines: Number of recent log lines (default: 2000, min: 100, max: 5000)

    Returns:
        {
            "status": "success" | "error",
            "data": {
                "container": str,
                "lines_requested": int,
                "lines_returned": int,
                "logs": str
            } | None,
            "error": str | None,
            "help": str (if error)
        }
    """
    try:
        # Clamp lines to safe range
        lines = max(100, min(lines, 5000))

        # Call internal logs API endpoint
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
            response = await client.get(
                f"http://localhost:8000/api/logs/{container}",
                params={"lines": lines}
            )

            if response.status_code == 200:
                data = response.json()
                return {
                    "status": "success",
                    "data": {
                        "container": data["container"],
                        "lines_requested": lines,
                        "lines_returned": data["lines_returned"],
                        "logs": data["logs"]
                    },
                    "error": None
                }
            elif response.status_code == 400:
                # Invalid container name
                error_detail = response.json().get("detail", "Invalid container")
                return {
                    "status": "error",
                    "data": None,
                    "error": error_detail,
                    "help": "Valid containers: backend, frontend, weaviate, postgres, langfuse, redis"
                }
            else:
                # Other errors
                error_detail = response.json().get("detail", "Unknown error")
                return {
                    "status": "error",
                    "data": None,
                    "error": f"Logs API error: {error_detail}",
                    "help": "Check Docker service and container status"
                }

    except httpx.TimeoutException:
        return {
            "status": "error",
            "data": None,
            "error": "Timeout retrieving logs (15s exceeded)",
            "help": "Container may be producing logs too slowly or not responding"
        }
    except httpx.ConnectError:
        return {
            "status": "error",
            "data": None,
            "error": "Cannot connect to logs API endpoint",
            "help": "Ensure backend service is running"
        }
    except Exception as e:
        return {
            "status": "error",
            "data": None,
            "error": f"Failed to retrieve logs: {str(e)}",
            "help": "Verify Docker is accessible and container name is correct"
        }
