"""
Claude-Specific Trace Analysis API Endpoints

Token-aware endpoints designed for Claude/Opus workflow analysis.
All responses include token metadata to help Claude manage context budget.

Endpoints:
- GET /summary - Lightweight trace overview (~500 tokens)
- GET /tool_calls/summary - All tool calls with summaries (~100 tokens/call)
- GET /tool_calls - Paginated full tool calls with filtering
- GET /tool_calls/{call_id} - Single tool call detail
- GET /conversation - User query and assistant response
"""

import math
from typing import Dict, Any, Optional, List
from fastapi import APIRouter, HTTPException, Request, Query, Path

from ..services.trace_extractor import TraceExtractor
from ..analyzers.conversation import ConversationAnalyzer
from ..analyzers.tool_calls import ToolCallAnalyzer
from ..analyzers.trace_summary import TraceSummaryAnalyzer
from ..utils.token_budget import (
    create_token_info_dict,
    create_lightweight_tool_call_summary,
    truncate_tool_call_results,
    MAX_TOKENS_DEFAULT,
)
from ..models.responses import (
    TokenInfo,
    PaginationInfo,
    ClaudeTraceResponse,
    ToolCallsSummaryResponse,
    ToolCallsSummaryData,
    ToolCallSummaryItem,
    PaginatedToolCallsResponse,
    SingleToolCallResponse,
    ConversationResponse,
    ConversationData,
)
from .auth import get_auth_dependency


router = APIRouter()


# Default source for trace extraction (EC2 Langfuse)
DEFAULT_SOURCE = "local"


async def _ensure_trace_analyzed(
    trace_id: str,
    request: Request,
    source: str = DEFAULT_SOURCE
) -> Dict[str, Any]:
    """
    Ensure trace is analyzed and cached.

    If not in cache, fetches from Langfuse and runs all analyzers.

    Args:
        trace_id: Langfuse trace ID
        request: FastAPI request (for cache access)
        source: Trace source ("local" or "remote")

    Returns:
        Cached trace data with all analysis views

    Raises:
        HTTPException: If trace not found or analysis fails
    """
    cache_manager = request.app.state.cache_manager

    # Check cache first
    cached_data = cache_manager.get(trace_id)
    if cached_data:
        return cached_data

    # Cache miss - fetch and analyze
    try:
        extractor = TraceExtractor(source=source)
        trace_data = extractor.extract_complete_trace(trace_id)
    except Exception as e:
        raise HTTPException(
            status_code=404,
            detail=f"Trace {trace_id} not found: {str(e)}"
        )

    # Run analyzers
    try:
        raw_trace = trace_data["raw_trace"]
        observations = trace_data["observations"]

        # Import all analyzers
        from ..analyzers.pdf_citations import PDFCitationsAnalyzer
        from ..analyzers.token_analysis import TokenAnalysisAnalyzer
        from ..analyzers.agent_context import AgentContextAnalyzer
        from ..analyzers.document_hierarchy import DocumentHierarchyAnalyzer
        from ..analyzers.agent_config import AgentConfigAnalyzer

        # Generate all views
        conversation = ConversationAnalyzer.extract_conversation(raw_trace, observations)
        tool_calls = ToolCallAnalyzer.extract_tool_calls(observations)
        pdf_citations = PDFCitationsAnalyzer.analyze(observations)
        token_analysis = TokenAnalysisAnalyzer.analyze(trace_data, observations)
        agent_context = AgentContextAnalyzer.analyze(trace_data, observations)
        trace_summary = TraceSummaryAnalyzer.analyze(trace_data, observations)
        document_hierarchy = DocumentHierarchyAnalyzer.analyze(trace_data, observations)
        agent_configs = AgentConfigAnalyzer.extract_agent_configs(observations)

        # Build summary
        metadata = raw_trace.get("metadata") or {}
        system_domain = metadata.get("destination", "unknown")

        summary = {
            "trace_id": trace_id,
            "trace_id_short": trace_data["trace_id_short"],
            "trace_name": trace_data["metadata"]["trace_name"],
            "duration_seconds": trace_data["metadata"]["duration_seconds"],
            "total_cost": trace_data["metadata"]["total_cost"],
            "total_tokens": trace_data["metadata"]["total_tokens"],
            "observation_count": trace_data["metadata"]["observation_count"],
            "score_count": trace_data["metadata"]["score_count"],
            "timestamp": trace_data["metadata"]["timestamp"],
            "system_domain": system_domain
        }

        # Group context
        # Dual-read: support both active_groups (new) and active_mods (historical)
        active_groups = metadata.get("active_groups") or metadata.get("active_mods", [])
        group_context = {
            "active_groups": active_groups,
            "injection_active": len(active_groups) > 0,
            "group_count": len(active_groups),
        }

        # Cache the data
        cache_data = {
            "raw_trace": raw_trace,
            "observations": observations,
            "scores": trace_data["scores"],
            "analysis": {
                "summary": summary,
                "conversation": conversation,
                "tool_calls": tool_calls,
                "pdf_citations": pdf_citations,
                "token_analysis": token_analysis,
                "agent_context": agent_context,
                "trace_summary": trace_summary,
                "document_hierarchy": document_hierarchy,
                "agent_configs": agent_configs,
                "group_context": group_context
            }
        }

        cache_manager.set(trace_id, cache_data)
        return cache_data

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error analyzing trace: {str(e)}"
        )


# =============================================================================
# Summary Endpoint
# =============================================================================

@router.get(
    "/{trace_id}/summary",
    response_model=ClaudeTraceResponse,
    summary="Get lightweight trace summary",
    description="""
    Returns a lightweight overview of the trace. ALWAYS call this first when
    analyzing a trace. Token cost: ~500 tokens.

    Includes: trace name, duration, cost, token counts, tool call count,
    error status, context overflow detection.
    """
)
async def get_trace_summary(
    trace_id: str = Path(..., description="Langfuse trace ID"),
    request: Request = None,
    source: str = Query(default=DEFAULT_SOURCE, description="Trace source"),
    user: Dict[str, Any] = get_auth_dependency()
) -> ClaudeTraceResponse:
    """Get lightweight trace summary with token metadata."""
    cached_data = await _ensure_trace_analyzed(trace_id, request, source)

    # Extract relevant data for Claude
    analysis = cached_data.get("analysis", {})
    summary = analysis.get("summary", {})
    trace_summary = analysis.get("trace_summary", {})
    tool_calls_data = analysis.get("tool_calls", {})

    # Build lightweight response
    response_data = {
        "trace_id": summary.get("trace_id", trace_id),
        "trace_id_short": summary.get("trace_id_short", trace_id[:8]),
        "trace_name": summary.get("trace_name"),
        "duration_seconds": summary.get("duration_seconds"),
        "total_cost": summary.get("total_cost"),
        "total_tokens": summary.get("total_tokens"),
        "tool_call_count": tool_calls_data.get("total_count", 0),
        "unique_tools": tool_calls_data.get("unique_tools", []),
        "has_errors": trace_summary.get("has_errors", False),
        "context_overflow_detected": trace_summary.get("context_overflow_detected", False),
        "timestamp": summary.get("timestamp"),
    }

    token_info = create_token_info_dict(response_data)

    return ClaudeTraceResponse(
        status="success",
        data=response_data,
        token_info=TokenInfo(**token_info)
    )


# =============================================================================
# Tool Calls Summary Endpoint
# =============================================================================

@router.get(
    "/{trace_id}/tool_calls/summary",
    response_model=ToolCallsSummaryResponse,
    summary="Get lightweight tool calls summary",
    description="""
    Returns a lightweight list of ALL tool calls with summaries (no full results).
    Use this to see what tools were called before drilling into details.
    Token cost: ~100 tokens per call.
    """
)
async def get_tool_calls_summary(
    trace_id: str = Path(..., description="Langfuse trace ID"),
    request: Request = None,
    source: str = Query(default=DEFAULT_SOURCE, description="Trace source"),
    user: Dict[str, Any] = get_auth_dependency()
) -> ToolCallsSummaryResponse:
    """Get lightweight summary of all tool calls."""
    cached_data = await _ensure_trace_analyzed(trace_id, request, source)

    analysis = cached_data.get("analysis", {})
    tool_calls_data = analysis.get("tool_calls", {})

    # Create lightweight summaries
    tool_calls = tool_calls_data.get("tool_calls", [])
    summaries = []
    for i, tc in enumerate(tool_calls):
        lightweight = create_lightweight_tool_call_summary(tc)
        summaries.append(ToolCallSummaryItem(
            index=i,
            call_id=lightweight["call_id"],
            name=lightweight["name"],
            time=lightweight["time"],
            duration=lightweight["duration"],
            status=lightweight["status"],
            input_summary=lightweight["input_summary"],
            result_summary=lightweight["result_summary"]
        ))

    duplicates = tool_calls_data.get("duplicates", {})

    response_data = ToolCallsSummaryData(
        total_count=tool_calls_data.get("total_count", 0),
        unique_tools=tool_calls_data.get("unique_tools", []),
        tool_calls=summaries,
        has_duplicates=duplicates.get("has_duplicates", False),
        duplicate_count=duplicates.get("total_duplicate_groups", 0)
    )

    token_info = create_token_info_dict(response_data.model_dump())

    return ToolCallsSummaryResponse(
        status="success",
        data=response_data,
        token_info=TokenInfo(**token_info)
    )


# =============================================================================
# Paginated Tool Calls Endpoint
# =============================================================================

@router.get(
    "/{trace_id}/tool_calls",
    response_model=PaginatedToolCallsResponse,
    summary="Get paginated tool calls with full details",
    description="""
    Returns paginated tool calls with full details. Use for detailed analysis
    of specific calls. Supports filtering by tool name.

    Query parameters:
    - page: Page number (1-indexed, default: 1)
    - page_size: Items per page (default: 10, max: 20)
    - tool_name: Optional filter by tool name

    Token cost: varies by page_size (~1-5K tokens per call with results).
    """
)
async def get_tool_calls_paginated(
    trace_id: str = Path(..., description="Langfuse trace ID"),
    request: Request = None,
    page: int = Query(default=1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(default=10, ge=1, le=20, description="Items per page"),
    tool_name: Optional[str] = Query(default=None, description="Filter by tool name"),
    source: str = Query(default=DEFAULT_SOURCE, description="Trace source"),
    user: Dict[str, Any] = get_auth_dependency()
) -> PaginatedToolCallsResponse:
    """Get paginated tool calls with full details."""
    cached_data = await _ensure_trace_analyzed(trace_id, request, source)

    analysis = cached_data.get("analysis", {})
    tool_calls_data = analysis.get("tool_calls", {})
    all_tool_calls = tool_calls_data.get("tool_calls", [])

    # Apply tool_name filter if provided
    if tool_name:
        all_tool_calls = [tc for tc in all_tool_calls if tc.get("name") == tool_name]

    # Calculate pagination
    total_items = len(all_tool_calls)
    total_pages = max(1, math.ceil(total_items / page_size))

    # Validate page number
    if page > total_pages and total_items > 0:
        raise HTTPException(
            status_code=400,
            detail=f"Page {page} exceeds total pages ({total_pages})"
        )

    # Get page slice
    start_idx = (page - 1) * page_size
    end_idx = start_idx + page_size
    page_tool_calls = all_tool_calls[start_idx:end_idx]

    # Truncate results if needed to fit token budget
    page_tool_calls = truncate_tool_call_results(page_tool_calls)

    pagination = PaginationInfo(
        page=page,
        page_size=page_size,
        total_items=total_items,
        total_pages=total_pages,
        has_next=page < total_pages,
        has_prev=page > 1
    )

    token_info = create_token_info_dict(page_tool_calls)

    return PaginatedToolCallsResponse(
        status="success",
        tool_calls=page_tool_calls,
        pagination=pagination,
        token_info=TokenInfo(**token_info),
        filter_applied=tool_name
    )


# =============================================================================
# Single Tool Call Detail Endpoint
# =============================================================================

@router.get(
    "/{trace_id}/tool_calls/{call_id}",
    response_model=SingleToolCallResponse,
    summary="Get single tool call detail",
    description="""
    Returns full details for a single tool call.
    Use when you need to see the complete input/output of a specific call.

    Accepts either:
    - `call_id`: OpenAI function call ID (e.g., "call_oVv6VsfK3iJEVN4eXh31evsf")
    - `id`: Langfuse observation ID (e.g., "5d8254fbec65a6f7")

    Both IDs are available in paginated tool_calls response. Prefer `call_id` when available.

    Token cost: ~1-5K tokens depending on result size.
    """
)
async def get_tool_call_detail(
    trace_id: str = Path(..., description="Langfuse trace ID"),
    call_id: str = Path(..., description="Tool call_id or observation id"),
    request: Request = None,
    source: str = Query(default=DEFAULT_SOURCE, description="Trace source"),
    user: Dict[str, Any] = get_auth_dependency()
) -> SingleToolCallResponse:
    """Get full details for a single tool call."""
    cached_data = await _ensure_trace_analyzed(trace_id, request, source)

    analysis = cached_data.get("analysis", {})
    tool_calls_data = analysis.get("tool_calls", {})
    all_tool_calls = tool_calls_data.get("tool_calls", [])

    # Find the tool call by call_id or observation id
    # Support both because paginated response includes both fields
    tool_call = None
    for tc in all_tool_calls:
        if tc.get("call_id") == call_id or tc.get("id") == call_id:
            tool_call = tc
            break

    if not tool_call:
        raise HTTPException(
            status_code=404,
            detail=f"Tool call with call_id or id '{call_id}' not found. "
                   f"Use call_id from tool_calls/summary or id from paginated tool_calls."
        )

    # Truncate if needed
    truncated = truncate_tool_call_results([tool_call])
    tool_call = truncated[0] if truncated else tool_call

    token_info = create_token_info_dict(tool_call)

    return SingleToolCallResponse(
        status="success",
        tool_call=tool_call,
        token_info=TokenInfo(**token_info)
    )


# =============================================================================
# Conversation Endpoint
# =============================================================================

@router.get(
    "/{trace_id}/conversation",
    response_model=ConversationResponse,
    summary="Get trace conversation",
    description="""
    Returns the user's query and the assistant's final response.

    Token cost: varies by response length (typically 1-10K tokens).
    """
)
async def get_trace_conversation(
    trace_id: str = Path(..., description="Langfuse trace ID"),
    request: Request = None,
    source: str = Query(default=DEFAULT_SOURCE, description="Trace source"),
    user: Dict[str, Any] = get_auth_dependency()
) -> ConversationResponse:
    """Get user query and assistant response."""
    cached_data = await _ensure_trace_analyzed(trace_id, request, source)

    analysis = cached_data.get("analysis", {})
    conversation = analysis.get("conversation", {})

    response_data = ConversationData(
        user_query=conversation.get("user_input"),  # ConversationAnalyzer returns "user_input" key
        assistant_response=conversation.get("assistant_response"),
        response_length=len(conversation.get("assistant_response", "") or "")
    )

    token_info = create_token_info_dict(response_data.model_dump())

    return ConversationResponse(
        status="success",
        data=response_data,
        token_info=TokenInfo(**token_info)
    )


# =============================================================================
# Generic View Endpoint (for other views)
# =============================================================================

@router.get(
    "/{trace_id}/views/{view_name}",
    response_model=ClaudeTraceResponse,
    summary="Get specific trace view",
    description="""
    Get a specific analysis view with token metadata.

    Available views: token_analysis, agent_context, pdf_citations,
    document_hierarchy, agent_configs, mod_context

    Token cost: varies by view (check token_info in response).
    """
)
async def get_trace_view(
    trace_id: str = Path(..., description="Langfuse trace ID"),
    view_name: str = Path(..., description="View name"),
    request: Request = None,
    source: str = Query(default=DEFAULT_SOURCE, description="Trace source"),
    user: Dict[str, Any] = get_auth_dependency()
) -> ClaudeTraceResponse:
    """Get specific trace view with token metadata."""
    valid_views = [
        "token_analysis", "agent_context", "pdf_citations",
        "document_hierarchy", "agent_configs", "mod_context", "trace_summary"
    ]

    if view_name not in valid_views:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid view '{view_name}'. Valid views: {', '.join(valid_views)}"
        )

    cached_data = await _ensure_trace_analyzed(trace_id, request, source)

    analysis = cached_data.get("analysis", {})
    view_data = analysis.get(view_name)

    if view_data is None:
        raise HTTPException(
            status_code=404,
            detail=f"View '{view_name}' not found for trace {trace_id}"
        )

    token_info = create_token_info_dict(view_data)

    return ClaudeTraceResponse(
        status="success",
        data=view_data,
        token_info=TokenInfo(**token_info)
    )
