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
from datetime import datetime
from typing import Annotated, Dict, Any, Optional, List
from fastapi import APIRouter, HTTPException, Request, Query, Path

from ..services.trace_extractor import TraceExtractor
from ..services.langfuse_run_reconstruction import (
    build_cost_summary,
    build_duplicate_report,
    build_ordered_reconstruction,
    build_payload_inventory,
    build_trace_tree,
    find_payload,
    paginate_payloads,
)
from ..analyzers.conversation import ConversationAnalyzer
from ..analyzers.tool_calls import ToolCallAnalyzer
from ..analyzers.trace_summary import TraceSummaryAnalyzer
from ..analyzers.extraction_timeline import (
    ANALYZER_SCHEMA_VERSION as EXTRACTION_TIMELINE_ANALYZER_SCHEMA_VERSION,
    ExtractionTimelineAnalyzer,
)
from .extraction_timeline_helpers import (
    build_extraction_timeline,
    load_extraction_timeline_context,
)
from ..utils.token_budget import (
    create_token_info_dict,
    create_lightweight_tool_call_summary,
    truncate_tool_call_results,
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
from ..utils.trace_output import is_trace_output_cacheable
from .auth import get_auth_dependency
from .domain_envelope_responses import domain_envelope_response_views


router = APIRouter()
TRANSIENT_CACHE_TTL_SECONDS = 15


# Default source for trace extraction (EC2 Langfuse)
DEFAULT_SOURCE = "local"


def _effective_source(source: str) -> str:
    return "local" if source == "auto" else source


def _trace_id_short(trace_id: Optional[str]) -> Optional[str]:
    if not trace_id:
        return None
    return trace_id[:8] if len(trace_id) >= 8 else trace_id


def _listed_trace_reference(trace: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "trace_id": trace.get("id"),
        "trace_id_short": _trace_id_short(trace.get("id")),
        "trace_name": trace.get("name"),
        "timestamp": trace.get("timestamp"),
        "session_id": trace.get("sessionId"),
        "user_id": trace.get("userId"),
        "environment": trace.get("environment"),
        "tags": trace.get("tags", []),
        "latency": trace.get("latency"),
        "total_cost": trace.get("totalCost"),
        "html_path": trace.get("htmlPath"),
    }


def _parse_optional_datetime(value: Optional[str], param_name: str) -> Optional[datetime]:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid {param_name}; expected ISO 8601 timestamp",
        ) from exc


def _ensure_search_scope(
    *,
    session_id: Optional[str],
    user_id: Optional[str],
    name: Optional[str],
    document_id: Optional[str],
    run_id: Optional[str],
    extraction_id: Optional[str],
    from_timestamp: Optional[str],
    to_timestamp: Optional[str],
) -> None:
    if any([session_id, user_id, name, document_id, run_id, extraction_id, from_timestamp, to_timestamp]):
        return
    raise HTTPException(
        status_code=400,
        detail=(
            "Provide at least one bounded search key: session_id, user_id, name, "
            "document_id, run_id, extraction_id, from_timestamp, or to_timestamp."
        ),
    )


def _cache_schema_is_current(cache_data: Dict[str, Any]) -> bool:
    return cache_data.get("analyzer_schema_version") == EXTRACTION_TIMELINE_ANALYZER_SCHEMA_VERSION


async def _ensure_trace_analyzed(
    trace_id: str,
    request: Request,
    source: str = DEFAULT_SOURCE,
    refresh: bool = False,
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

    if refresh:
        cache_manager.delete(trace_id)

    cached_data = cache_manager.get(trace_id)
    if cached_data:
        if _cache_schema_is_current(cached_data):
            return cached_data
        cache_manager.delete(trace_id)

    # Cache miss - fetch and analyze
    try:
        extractor = TraceExtractor(source=_effective_source(source))
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
        domain_envelope, compact_domain_envelope = domain_envelope_response_views(trace_summary)
        document_hierarchy = DocumentHierarchyAnalyzer.analyze(trace_data, observations)
        agent_configs = AgentConfigAnalyzer.extract_agent_configs(observations)
        extraction_timeline = ExtractionTimelineAnalyzer.analyze(
            trace_id=trace_id,
            raw_trace=raw_trace,
            observations=observations,
        )

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
            "system_domain": system_domain,
            "domain_envelope": compact_domain_envelope,
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
            "analyzer_schema_version": EXTRACTION_TIMELINE_ANALYZER_SCHEMA_VERSION,
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
                "domain_envelope": domain_envelope,
                "document_hierarchy": document_hierarchy,
                "agent_configs": agent_configs,
                "extraction_timeline": extraction_timeline,
                "group_context": group_context
            }
        }

        if is_trace_output_cacheable(raw_trace.get("output")):
            cache_manager.set(trace_id, cache_data, cache_status="stable")
        else:
            cache_manager.set(
                trace_id,
                cache_data,
                cache_status="transient",
                ttl_seconds=TRANSIENT_CACHE_TTL_SECONDS,
            )
        return cache_data

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error analyzing trace: {str(e)}"
        )


def _sibling_trace_ids(
    *,
    trace_id: str,
    source: str,
    session_id: Optional[str],
    include_sibling_traces: bool,
) -> List[str]:
    if not include_sibling_traces or not session_id:
        return []
    extractor = TraceExtractor(source=_effective_source(source))
    session_listing = extractor.list_session_traces(session_id)
    return [
        listed_trace["id"]
        for listed_trace in session_listing.get("traces", [])
        if listed_trace.get("id") and listed_trace.get("id") != trace_id
    ]


def _extract_langfuse_trace(trace_id: str, source: str) -> Dict[str, Any]:
    try:
        extractor = TraceExtractor(source=_effective_source(source))
        return extractor.extract_complete_trace(trace_id)
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=404,
            detail=f"Trace {trace_id} not found in Langfuse ({source}): {str(exc)}",
        ) from exc


def _offset_pagination(*, limit: int, offset: int, total_items: int) -> Dict[str, Any]:
    next_offset = offset + limit if offset + limit < total_items else None
    return {
        "limit": limit,
        "offset": offset,
        "total_items": total_items,
        "has_next": next_offset is not None,
        "next_offset": next_offset,
    }


# =============================================================================
# Langfuse-first Inspection Endpoints
# =============================================================================

@router.get(
    "/search",
    response_model=ClaudeTraceResponse,
    summary="Search Langfuse traces",
    description="""
    Search Langfuse traces by session, user, trace name, indexed metadata IDs,
    or bounded timestamp window. Use this when a curator gives a session,
    document, run, or extraction ID instead of a trace ID.
    """
)
async def search_traces(
    source: str = Query(default=DEFAULT_SOURCE, description="Trace source: local, remote, or auto"),
    session_id: Optional[str] = Query(default=None, description="Langfuse session ID"),
    user_id: Optional[str] = Query(default=None, description="Langfuse user ID"),
    name: Optional[str] = Query(default=None, description="Trace name filter"),
    document_id: Optional[str] = Query(default=None, description="Trace metadata.document_id filter"),
    run_id: Optional[str] = Query(default=None, description="Trace metadata.run_id filter"),
    extraction_id: Optional[str] = Query(default=None, description="Trace metadata.extraction_id filter"),
    from_timestamp: Optional[str] = Query(default=None, description="ISO timestamp lower bound"),
    to_timestamp: Optional[str] = Query(default=None, description="ISO timestamp upper bound"),
    limit: int = Query(default=25, ge=1, le=100, description="Maximum traces to return"),
    user: Dict[str, Any] = get_auth_dependency(),
) -> ClaudeTraceResponse:
    _ensure_search_scope(
        session_id=session_id,
        user_id=user_id,
        name=name,
        document_id=document_id,
        run_id=run_id,
        extraction_id=extraction_id,
        from_timestamp=from_timestamp,
        to_timestamp=to_timestamp,
    )

    try:
        extractor = TraceExtractor(source=_effective_source(source))
        listing = extractor.list_traces(
            session_id=session_id,
            user_id=user_id,
            name=name,
            document_id=document_id,
            run_id=run_id,
            extraction_id=extraction_id,
            from_timestamp=_parse_optional_datetime(from_timestamp, "from_timestamp"),
            to_timestamp=_parse_optional_datetime(to_timestamp, "to_timestamp"),
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Unable to search Langfuse traces ({source}): {str(exc)}",
        ) from exc

    response_data = {
        "source": source,
        "trace_count": len(listing["traces"]),
        "query": listing["query"],
        "langfuse_meta": listing["meta"],
        "traces": [_listed_trace_reference(trace) for trace in listing["traces"]],
    }
    token_info = create_token_info_dict(response_data)
    return ClaudeTraceResponse(
        status="success",
        data=response_data,
        token_info=TokenInfo(**token_info),
    )


@router.get(
    "/{trace_id}/langfuse_tree",
    response_model=ClaudeTraceResponse,
    summary="Get Langfuse observation tree",
    description="""
    Return the trace/observation parent-child tree with payload references,
    observation metadata, model names, agent hints, and usage/cost summaries.
    Full payload values are omitted; use langfuse_payloads and langfuse_payload
    to retrieve exact input/output values.
    """
)
async def get_langfuse_tree(
    trace_id: Annotated[str, Path(description="Langfuse trace ID")],
    source: str = Query(default=DEFAULT_SOURCE, description="Trace source"),
    user: Dict[str, Any] = get_auth_dependency(),
) -> ClaudeTraceResponse:
    trace_data = _extract_langfuse_trace(trace_id, source)
    response_data = {
        "source": source,
        "trace_id": trace_id,
        "tree": build_trace_tree(trace_data),
    }
    token_info = create_token_info_dict(response_data)
    return ClaudeTraceResponse(
        status="success",
        data=response_data,
        token_info=TokenInfo(**token_info),
    )


@router.get(
    "/{trace_id}/langfuse_reconstruction",
    response_model=ClaudeTraceResponse,
    summary="Get ordered Langfuse trace reconstruction",
    description="""
    Return chronological trace/model/tool/event reconstruction with payload
    references. The event list is offset/limit paginated for large traces.
    """
)
async def get_langfuse_reconstruction(
    trace_id: Annotated[str, Path(description="Langfuse trace ID")],
    source: str = Query(default=DEFAULT_SOURCE, description="Trace source"),
    include_payloads: bool = Query(default=False, description="Include full payload values in events"),
    limit: int = Query(default=100, ge=1, le=500, description="Maximum events to return"),
    offset: int = Query(default=0, ge=0, description="Event offset"),
    user: Dict[str, Any] = get_auth_dependency(),
) -> ClaudeTraceResponse:
    trace_data = _extract_langfuse_trace(trace_id, source)
    reconstruction = build_ordered_reconstruction(
        trace_data,
        include_payload_values=include_payloads,
    )
    events = reconstruction.get("events", [])
    page = events[offset:offset + limit]
    response_data = {
        "source": source,
        "trace_id": trace_id,
        "trace": reconstruction.get("trace"),
        "event_count": reconstruction.get("event_count", len(events)),
        "events": page,
        "pagination": _offset_pagination(
            limit=limit,
            offset=offset,
            total_items=len(events),
        ),
    }
    token_info = create_token_info_dict(response_data)
    return ClaudeTraceResponse(
        status="success",
        data=response_data,
        token_info=TokenInfo(**token_info),
    )


@router.get(
    "/{trace_id}/langfuse_payloads",
    response_model=ClaudeTraceResponse,
    summary="List Langfuse trace payloads",
    description="""
    Return trace/model/tool input/output payload summaries, largest first by
    default. Full values are omitted unless include_values is true; prefer
    langfuse_payload for exact chunked retrieval.
    """
)
async def get_langfuse_payloads(
    trace_id: Annotated[str, Path(description="Langfuse trace ID")],
    source: str = Query(default=DEFAULT_SOURCE, description="Trace source"),
    include_values: bool = Query(default=False, description="Include full payload values in page"),
    sort: str = Query(default="largest", description="Sort order: largest or chronological"),
    limit: int = Query(default=50, ge=1, le=200, description="Maximum payload summaries to return"),
    offset: int = Query(default=0, ge=0, description="Pagination offset"),
    user: Dict[str, Any] = get_auth_dependency(),
) -> ClaudeTraceResponse:
    if sort not in {"largest", "chronological"}:
        raise HTTPException(status_code=400, detail="sort must be 'largest' or 'chronological'")
    trace_data = _extract_langfuse_trace(trace_id, source)
    payloads = build_payload_inventory(trace_data, include_values=include_values)
    page, pagination = paginate_payloads(payloads, limit=limit, offset=offset, sort=sort)
    response_data = {
        "source": source,
        "trace_id": trace_id,
        "sort": sort,
        "pagination": pagination,
        "payloads": page,
    }
    token_info = create_token_info_dict(response_data)
    return ClaudeTraceResponse(
        status="success",
        data=response_data,
        token_info=TokenInfo(**token_info),
    )


@router.get(
    "/{trace_id}/langfuse_payload",
    response_model=ClaudeTraceResponse,
    summary="Get one exact Langfuse payload",
    description="""
    Return one exact trace or observation payload by payload_id, or by
    scope/observation_id/field. Defaults to a 12K-character chunk to keep
    Claude responses bounded.
    """
)
async def get_langfuse_payload(
    trace_id: Annotated[str, Path(description="Langfuse trace ID")],
    source: str = Query(default=DEFAULT_SOURCE, description="Trace source"),
    payload_id: Optional[str] = Query(default=None, description="Payload ID returned by langfuse_payloads"),
    scope: Optional[str] = Query(default=None, description="Payload scope: trace or observation"),
    observation_id: Optional[str] = Query(default=None, description="Observation/span ID"),
    field: Optional[str] = Query(default=None, description="Payload field: input, output, metadata.agent_config, or metadata.event_payload"),
    start: int = Query(default=0, ge=0, description="Start character for chunked retrieval"),
    max_chars: int = Query(default=12000, ge=0, le=50000, description="Maximum characters; 0 returns the full payload"),
    user: Dict[str, Any] = get_auth_dependency(),
) -> ClaudeTraceResponse:
    if not payload_id:
        if field not in {"input", "output", "metadata.agent_config", "metadata.event_payload"}:
            raise HTTPException(
                status_code=400,
                detail="field must be 'input', 'output', 'metadata.agent_config', or 'metadata.event_payload'",
            )
        if scope and scope not in {"trace", "observation"}:
            raise HTTPException(status_code=400, detail="scope must be 'trace' or 'observation'")

    trace_data = _extract_langfuse_trace(trace_id, source)
    payload = find_payload(
        trace_data,
        payload_id=payload_id,
        scope=scope,
        observation_id=observation_id,
        field=field,
        start=start,
        max_chars=max_chars,
    )
    if payload is None:
        raise HTTPException(status_code=404, detail="Payload not found in Langfuse trace data")

    response_data = {
        "source": source,
        "trace_id": trace_id,
        "payload": payload,
    }
    token_info = create_token_info_dict(response_data)
    return ClaudeTraceResponse(
        status="success",
        data=response_data,
        token_info=TokenInfo(**token_info),
    )


@router.get(
    "/{trace_id}/langfuse_costs",
    response_model=ClaudeTraceResponse,
    summary="Get Langfuse cost summary",
    description="Return token and cost accounting by trace, agent, model, kind, and observation."
)
async def get_langfuse_costs(
    trace_id: Annotated[str, Path(description="Langfuse trace ID")],
    source: str = Query(default=DEFAULT_SOURCE, description="Trace source"),
    user: Dict[str, Any] = get_auth_dependency(),
) -> ClaudeTraceResponse:
    trace_data = _extract_langfuse_trace(trace_id, source)
    response_data = {
        "source": source,
        "trace_id": trace_id,
        "costs": build_cost_summary(trace_data),
    }
    token_info = create_token_info_dict(response_data)
    return ClaudeTraceResponse(
        status="success",
        data=response_data,
        token_info=TokenInfo(**token_info),
    )


@router.get(
    "/{trace_id}/langfuse_duplicates",
    response_model=ClaudeTraceResponse,
    summary="Get duplicated Langfuse payload report",
    description="Return repeated payload fingerprints across trace and observation input/output payloads."
)
async def get_langfuse_duplicates(
    trace_id: Annotated[str, Path(description="Langfuse trace ID")],
    source: str = Query(default=DEFAULT_SOURCE, description="Trace source"),
    user: Dict[str, Any] = get_auth_dependency(),
) -> ClaudeTraceResponse:
    trace_data = _extract_langfuse_trace(trace_id, source)
    response_data = {
        "source": source,
        "trace_id": trace_id,
        "duplicates": build_duplicate_report(trace_data),
    }
    token_info = create_token_info_dict(response_data)
    return ClaudeTraceResponse(
        status="success",
        data=response_data,
        token_info=TokenInfo(**token_info),
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
    trace_id: Annotated[str, Path(description="Langfuse trace ID")],
    request: Request,
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
        "domain_envelope": summary["domain_envelope"],
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
    trace_id: Annotated[str, Path(description="Langfuse trace ID")],
    request: Request,
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
            result_summary=lightweight["result_summary"],
            domain_envelope=lightweight.get("domain_envelope"),
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
    trace_id: Annotated[str, Path(description="Langfuse trace ID")],
    request: Request,
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
    trace_id: Annotated[str, Path(description="Langfuse trace ID")],
    call_id: Annotated[str, Path(description="Tool call_id or observation id")],
    request: Request,
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
    trace_id: Annotated[str, Path(description="Langfuse trace ID")],
    request: Request,
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
        response_length=len(conversation.get("assistant_response", "") or ""),
        domain_envelope=conversation.get("domain_envelope"),
    )

    token_info = create_token_info_dict(response_data.model_dump())

    return ConversationResponse(
        status="success",
        data=response_data,
        token_info=TokenInfo(**token_info)
    )


# =============================================================================
# Extraction Diagnostics Endpoints
# =============================================================================

@router.get(
    "/{trace_id}/extraction_timeline",
    response_model=ClaudeTraceResponse,
    summary="Get extraction diagnostic timeline",
    description="""
    Returns ordered extraction-adjacent durable events plus OpenAI/Agents SDK
    tool-call observations. Supports filters and bounded raw args/output views.
    """
)
async def get_extraction_timeline(
    trace_id: Annotated[str, Path(description="Langfuse trace ID")],
    request: Request,
    source: str = Query(default=DEFAULT_SOURCE, description="Trace source: local, remote, or auto"),
    session_id: Optional[str] = Query(default=None, description="Langfuse session ID for sibling-trace expansion"),
    feedback_id: Optional[str] = Query(default=None, description="Feedback ID linked to stored trace artifacts"),
    include_sibling_traces: bool = Query(default=False, description="Include durable events from traces in the same session"),
    refresh: bool = Query(default=False, description="Refresh cached trace analysis before rendering"),
    include_raw_args: bool = Query(default=False, description="Include bounded raw argument summaries"),
    include_raw_outputs: bool = Query(default=False, description="Include bounded raw output summaries"),
    tool_name: Optional[str] = Query(default=None, description="Filter by tool name"),
    event_type: Optional[str] = Query(default=None, description="Filter by event type"),
    candidate_id: Optional[str] = Query(default=None, description="Filter by candidate ID"),
    user: Dict[str, Any] = get_auth_dependency()
) -> ClaudeTraceResponse:
    context = await load_extraction_timeline_context(
        trace_id=trace_id,
        feedback_id=feedback_id,
        include_sibling_traces=include_sibling_traces,
        load_cached_data=lambda: _ensure_trace_analyzed(trace_id, request, source, refresh=refresh),
        load_sibling_trace_ids=lambda: _sibling_trace_ids(
            trace_id=trace_id,
            source=source,
            session_id=session_id,
            include_sibling_traces=include_sibling_traces,
        ),
        load_sibling_cached_data=lambda sibling_trace_id: _ensure_trace_analyzed(
            sibling_trace_id,
            request,
            source,
            refresh=refresh,
        ),
        fallback_exceptions=(HTTPException,),
    )
    timeline = build_extraction_timeline(
        trace_id=trace_id,
        context=context,
        include_raw_args=include_raw_args,
        include_raw_outputs=include_raw_outputs,
        tool_name=tool_name,
        event_type=event_type,
        candidate_id=candidate_id,
        session_id=session_id,
        feedback_id=feedback_id,
    )
    token_info = create_token_info_dict(timeline)
    return ClaudeTraceResponse(
        status="success",
        data=timeline,
        token_info=TokenInfo(**token_info),
    )


@router.get(
    "/{trace_id}/diagnostic_report",
    response_model=ClaudeTraceResponse,
    summary="Get concise extraction diagnostic report",
    description="""
    Returns a concise extraction diagnostics report rendered from the same
    extraction timeline analysis.
    """
)
async def get_extraction_diagnostic_report(
    trace_id: Annotated[str, Path(description="Langfuse trace ID")],
    request: Request,
    source: str = Query(default=DEFAULT_SOURCE, description="Trace source: local, remote, or auto"),
    session_id: Optional[str] = Query(default=None, description="Langfuse session ID for sibling-trace expansion"),
    feedback_id: Optional[str] = Query(default=None, description="Feedback ID linked to stored trace artifacts"),
    include_sibling_traces: bool = Query(default=False, description="Include durable events from traces in the same session"),
    refresh: bool = Query(default=False, description="Refresh cached trace analysis before rendering"),
    include_raw_args: bool = Query(default=False, description="Include bounded raw argument summaries"),
    include_raw_outputs: bool = Query(default=False, description="Include bounded raw output summaries"),
    tool_name: Optional[str] = Query(default=None, description="Filter by tool name"),
    event_type: Optional[str] = Query(default=None, description="Filter by event type"),
    candidate_id: Optional[str] = Query(default=None, description="Filter by candidate ID"),
    user: Dict[str, Any] = get_auth_dependency()
) -> ClaudeTraceResponse:
    context = await load_extraction_timeline_context(
        trace_id=trace_id,
        feedback_id=feedback_id,
        include_sibling_traces=include_sibling_traces,
        load_cached_data=lambda: _ensure_trace_analyzed(trace_id, request, source, refresh=refresh),
        load_sibling_trace_ids=lambda: _sibling_trace_ids(
            trace_id=trace_id,
            source=source,
            session_id=session_id,
            include_sibling_traces=include_sibling_traces,
        ),
        load_sibling_cached_data=lambda sibling_trace_id: _ensure_trace_analyzed(
            sibling_trace_id,
            request,
            source,
            refresh=refresh,
        ),
        fallback_exceptions=(HTTPException,),
    )
    timeline = build_extraction_timeline(
        trace_id=trace_id,
        context=context,
        include_raw_args=include_raw_args,
        include_raw_outputs=include_raw_outputs,
        tool_name=tool_name,
        event_type=event_type,
        candidate_id=candidate_id,
        session_id=session_id,
        feedback_id=feedback_id,
    )
    report = ExtractionTimelineAnalyzer.diagnostic_report(timeline)
    token_info = create_token_info_dict(report)
    return ClaudeTraceResponse(
        status="success",
        data=report,
        token_info=TokenInfo(**token_info),
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
    trace_id: Annotated[str, Path(description="Langfuse trace ID")],
    view_name: Annotated[str, Path(description="View name")],
    request: Request,
    source: str = Query(default=DEFAULT_SOURCE, description="Trace source"),
    user: Dict[str, Any] = get_auth_dependency()
) -> ClaudeTraceResponse:
    """Get specific trace view with token metadata."""
    valid_views = [
        "token_analysis", "agent_context", "pdf_citations",
        "document_hierarchy", "agent_configs", "mod_context", "trace_summary",
        "domain_envelope", "extraction_timeline",
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
