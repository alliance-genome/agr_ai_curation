"""
Trace analysis API endpoints
"""
import logging
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Query, Request
from typing import Dict, Any, List, Optional, Tuple

from ..models.requests import AnalyzeTraceRequest, TraceSource
from ..models.responses import SessionTraceExportResponse
from ..services.trace_extractor import TraceExtractor
from ..analyzers.conversation import ConversationAnalyzer
from ..analyzers.tool_calls import ToolCallAnalyzer
from ..analyzers.pdf_citations import PDFCitationsAnalyzer
from ..analyzers.token_analysis import TokenAnalysisAnalyzer
from ..analyzers.agent_context import AgentContextAnalyzer
from ..analyzers.trace_summary import TraceSummaryAnalyzer
from ..analyzers.document_hierarchy import DocumentHierarchyAnalyzer
from ..analyzers.agent_config import AgentConfigAnalyzer
from ..utils.token_budget import create_lightweight_tool_call_summary
from ..utils.trace_output import is_trace_output_cacheable
from .auth import get_auth_dependency


logger = logging.getLogger(__name__)
router = APIRouter()
TRANSIENT_CACHE_TTL_SECONDS = 15
ALL_VIEWS = [
    "summary", "conversation", "tool_calls",
    "pdf_citations", "token_analysis", "agent_context", "trace_summary",
    "document_hierarchy", "agent_configs", "group_context"
]

# Group descriptions for display (Alliance MODs as default groups)
GROUP_DESCRIPTIONS = {
    "MGI": "Mouse Genome Informatics (Mus musculus)",
    "FB": "FlyBase (Drosophila melanogaster)",
    "WB": "WormBase (Caenorhabditis elegans)",
    "ZFIN": "Zebrafish Information Network (Danio rerio)",
    "RGD": "Rat Genome Database (Rattus norvegicus)",
    "SGD": "Saccharomyces Genome Database (yeast)",
    "HGNC": "HUGO Gene Nomenclature Committee (human)",
}


class TraceExtractionError(Exception):
    """Raised when a trace cannot be fetched from Langfuse."""


class TraceAnalysisError(Exception):
    """Raised when TraceReview analyzers cannot process fetched trace data."""


def _trace_id_short(trace_id: Optional[str]) -> Optional[str]:
    if not trace_id:
        return None
    return trace_id[:8] if len(trace_id) >= 8 else trace_id


def _group_context_from_metadata(metadata: Dict[str, Any]) -> Dict[str, Any]:
    # Dual-read kept for existing TraceReview metadata shape; this ticket does not
    # change trace-writing contracts.
    active_groups = metadata.get("active_groups") or metadata.get("active_mods", [])
    return {
        "active_groups": active_groups,
        "injection_active": len(active_groups) > 0,
        "group_count": len(active_groups),
        "group_details": [
            {"group_id": grp, "description": GROUP_DESCRIPTIONS.get(grp, "Unknown group")}
            for grp in active_groups
        ]
    }


def _build_trace_cache_data(trace_id: str, trace_data: Dict[str, Any]) -> Dict[str, Any]:
    raw_trace = trace_data["raw_trace"]
    observations = trace_data["observations"]

    conversation = ConversationAnalyzer.extract_conversation(raw_trace, observations)
    tool_calls = ToolCallAnalyzer.extract_tool_calls(observations)
    pdf_citations = PDFCitationsAnalyzer.analyze(observations)
    token_analysis = TokenAnalysisAnalyzer.analyze(trace_data, observations)
    agent_context = AgentContextAnalyzer.analyze(trace_data, observations)
    trace_summary = TraceSummaryAnalyzer.analyze(trace_data, observations)
    document_hierarchy = DocumentHierarchyAnalyzer.analyze(trace_data, observations)
    agent_configs = AgentConfigAnalyzer.extract_agent_configs(observations)

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

    return {
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
            "group_context": _group_context_from_metadata(metadata)
        }
    }


def _store_trace_cache(cache_manager: Any, trace_id: str, cache_data: Dict[str, Any]) -> str:
    raw_trace = cache_data["raw_trace"]
    if is_trace_output_cacheable(raw_trace.get("output")):
        cache_manager.set(trace_id, cache_data, cache_status="stable")
        logger.info("Cached trace %s", trace_id)
        return "miss"

    cache_manager.set(
        trace_id,
        cache_data,
        cache_status="transient",
        ttl_seconds=TRANSIENT_CACHE_TTL_SECONDS,
    )
    logger.info("Trace %s looks in-flight; cached transiently", trace_id)
    return "transient"


def _get_or_analyze_trace_export(
    trace_id: str,
    cache_manager: Any,
    source: TraceSource,
    extractor: Optional[TraceExtractor] = None,
) -> Tuple[Dict[str, Any], Optional[str], bool]:
    cached_data = cache_manager.get(trace_id)
    if cached_data:
        return cached_data, cache_manager.get_status(trace_id), True

    active_extractor = extractor or TraceExtractor(source=source)

    try:
        trace_data = active_extractor.extract_complete_trace(trace_id)
    except Exception as exc:
        raise TraceExtractionError(str(exc)) from exc

    try:
        cache_data = _build_trace_cache_data(trace_id, trace_data)
    except Exception as exc:
        raise TraceAnalysisError(str(exc)) from exc

    cache_status = _store_trace_cache(cache_manager, trace_id, cache_data)
    return cache_data, cache_status, False


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


def _tool_call_summary(tool_calls: Dict[str, Any]) -> Dict[str, Any]:
    summaries = []
    for index, tool_call in enumerate(tool_calls["tool_calls"]):
        summaries.append({
            "index": index,
            **create_lightweight_tool_call_summary(tool_call),
        })

    return {
        "total_count": tool_calls["total_count"],
        "unique_tools": tool_calls["unique_tools"],
        "duplicates": tool_calls["duplicates"],
        "tool_calls": summaries,
    }


def _compact_trace_bundle(trace_id: str, listed_trace: Dict[str, Any], cache_data: Dict[str, Any]) -> Dict[str, Any]:
    analysis = cache_data["analysis"]
    return {
        "status": "success",
        "trace_id": trace_id,
        "trace_id_short": _trace_id_short(trace_id),
        "listed_trace": _listed_trace_reference(listed_trace),
        "summary": analysis["summary"],
        "conversation": analysis["conversation"],
        "tool_summary": _tool_call_summary(analysis["tool_calls"]),
        "analyzer_outputs": {
            "pdf_citations": analysis["pdf_citations"],
            "token_analysis": analysis["token_analysis"],
            "agent_context": analysis["agent_context"],
            "trace_summary": analysis["trace_summary"],
            "document_hierarchy": analysis["document_hierarchy"],
            "agent_configs": analysis["agent_configs"],
            "group_context": analysis["group_context"],
        },
    }


def _trace_error(source: TraceSource, listed_trace: Dict[str, Any], message: str) -> Dict[str, Any]:
    trace_id = listed_trace.get("id")
    return {
        "trace_id": trace_id,
        "trace_id_short": _trace_id_short(trace_id),
        "trace_name": listed_trace.get("name"),
        "timestamp": listed_trace.get("timestamp"),
        "source": source,
        "message": message,
    }


def _session_timestamp_bounds(traces: List[Dict[str, Any]]) -> Tuple[Optional[str], Optional[str]]:
    timestamps = sorted(
        trace.get("timestamp")
        for trace in traces
        if trace.get("timestamp")
    )
    if not timestamps:
        return None, None
    return timestamps[0], timestamps[-1]


@router.get("/test")
async def test_route():
    """Test route to verify router is working"""
    return {"status": "ok", "message": "Router is working!"}


@router.post("/analyze")
async def analyze_trace(
    request_data: AnalyzeTraceRequest,
    request: Request,
    user: Dict[str, Any] = get_auth_dependency()
) -> Dict[str, Any]:
    """
    Analyze a trace by ID
    - Checks cache first
    - If not cached, fetches from Langfuse, analyzes, and caches
    - Returns metadata about available views
    """
    trace_id = request_data.trace_id
    cache_manager = request.app.state.cache_manager

    try:
        cache_data, cache_status, from_cache = _get_or_analyze_trace_export(
            trace_id,
            cache_manager,
            request_data.source,
        )
    except TraceExtractionError as e:
        raise HTTPException(
            status_code=404,
            detail=f"Trace {trace_id} not found in Langfuse ({request_data.source}): {str(e)}"
        )
    except TraceAnalysisError as e:
        logger.exception("Error analyzing trace: %s", e)
        raise HTTPException(
            status_code=500,
            detail=f"Error analyzing trace: {str(e)}"
        )

    response_cache_status = "transient" if cache_status == "transient" else "hit"
    if not from_cache:
        response_cache_status = cache_status or "miss"

    response = {
        "status": "success",
        "trace_id": trace_id,
        "trace_id_short": _trace_id_short(trace_id),
        "message": "Trace loaded from cache" if from_cache else "Trace analyzed successfully",
        "cache_status": response_cache_status,
        "available_views": ALL_VIEWS
    }
    if from_cache:
        response["cached_at"] = cache_data.get("cached_at")
    return response


@router.post("/cache/clear")
async def clear_cache(
    request: Request,
    user: Dict[str, Any] = get_auth_dependency()
) -> Dict[str, Any]:
    """
    Clear all cached trace analyses
    """
    cache_manager = request.app.state.cache_manager
    cleared_count = cache_manager.clear_all()

    return {
        "status": "success",
        "message": f"Cache cleared: {cleared_count} traces removed",
        "cleared_count": cleared_count
    }


@router.get("/{trace_id}/export")
async def export_trace(
    trace_id: str,
    request: Request,
    source: TraceSource = "remote",
    user: Dict[str, Any] = get_auth_dependency()
) -> Dict[str, Any]:
    """
    Export full trace analysis as JSON
    - Checks cache first
    - If not cached, fetches from Langfuse, analyzes, and caches
    - Returns complete analysis data
    """
    logger.info("Exporting trace %s from source: %s", trace_id, source)

    cache_manager = request.app.state.cache_manager

    try:
        cache_data, _cache_status, _from_cache = _get_or_analyze_trace_export(
            trace_id,
            cache_manager,
            source,
        )
    except TraceExtractionError as e:
        logger.error("Error extracting trace %s: %s", trace_id, e)
        raise HTTPException(
            status_code=404,
            detail=f"Trace {trace_id} not found in Langfuse ({source}): {str(e)}"
        )
    except TraceAnalysisError as e:
        logger.exception("Error analyzing trace %s: %s", trace_id, e)
        raise HTTPException(
            status_code=500,
            detail=f"Error analyzing trace: {str(e)}"
        )

    return cache_data


@router.get("/sessions/{session_id}/export", response_model=SessionTraceExportResponse)
async def export_session(
    session_id: str,
    request: Request,
    source: TraceSource = Query(default="remote", description="Trace source: 'remote' (EC2) or 'local' (Docker)"),
    user: Dict[str, Any] = get_auth_dependency()
) -> Dict[str, Any]:
    """
    Export a compact analysis bundle for every trace in a Langfuse session.

    Individual trace fetch/analyzer failures are represented in the returned
    bundle so one broken trace does not prevent session reconstruction.
    """
    logger.info("Exporting session %s from source: %s", session_id, source)
    cache_manager = request.app.state.cache_manager

    try:
        extractor = TraceExtractor(source=source)
        session_listing = extractor.list_session_traces(session_id)
    except ValueError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except RuntimeError as e:
        logger.error("Error listing session %s from %s: %s", session_id, source, e)
        raise HTTPException(
            status_code=502,
            detail=f"Unable to list traces for session {session_id} from Langfuse ({source}): {str(e)}"
        )

    listed_traces = session_listing["traces"]
    bundle_traces: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []

    for listed_trace in listed_traces:
        trace_id = listed_trace.get("id")
        if not trace_id:
            error = _trace_error(source, listed_trace, "Session trace listing did not include a trace id")
            errors.append(error)
            bundle_traces.append({
                "status": "error",
                "trace_id": "unknown",
                "trace_id_short": None,
                "listed_trace": _listed_trace_reference(listed_trace),
                "error": error,
            })
            continue

        try:
            cache_data, _cache_status, _from_cache = _get_or_analyze_trace_export(
                trace_id,
                cache_manager,
                source,
                extractor=extractor,
            )
        except (TraceExtractionError, TraceAnalysisError) as e:
            error = _trace_error(source, listed_trace, str(e))
            errors.append(error)
            bundle_traces.append({
                "status": "error",
                "trace_id": trace_id,
                "trace_id_short": _trace_id_short(trace_id),
                "listed_trace": _listed_trace_reference(listed_trace),
                "error": error,
            })
            continue

        bundle_traces.append(_compact_trace_bundle(trace_id, listed_trace, cache_data))

    first_timestamp, last_timestamp = _session_timestamp_bounds(listed_traces)
    successful_count = sum(1 for trace in bundle_traces if trace.get("status") == "success")

    return {
        "status": "success",
        "session": {
            "session_id": session_id,
            "source": source,
            "trace_count": len(bundle_traces),
            "listed_trace_count": len(listed_traces),
            "successful_trace_count": successful_count,
            "failed_trace_count": len(errors),
            "trace_ids": [
                trace.get("id")
                for trace in listed_traces
                if trace.get("id")
            ],
            "first_timestamp": first_timestamp,
            "last_timestamp": last_timestamp,
            "langfuse_meta": session_listing["meta"],
            "exported_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        },
        "traces": bundle_traces,
        "errors": errors,
    }


@router.get("/{trace_id}/views/{view_name}")
async def get_trace_view(
    trace_id: str,
    view_name: str,
    request: Request,
    user: Dict[str, Any] = get_auth_dependency()
) -> Dict[str, Any]:
    """
    Get specific view data for a trace
    Must call /analyze first to populate cache
    """
    cache_manager = request.app.state.cache_manager

    # Get from cache
    cached_data = cache_manager.get(trace_id)
    if not cached_data:
        raise HTTPException(
            status_code=404,
            detail="Trace not found in cache. Call /api/traces/analyze first."
        )

    # Extract requested view
    analysis = cached_data.get("analysis", {})
    view_data = analysis.get(view_name)

    if view_data is None:
        raise HTTPException(
            status_code=404,
            detail=f"View '{view_name}' not found. Available views: {', '.join(ALL_VIEWS)}"
        )

    return {
        "view": view_name,
        "trace_id": trace_id,
        "cached_at": cached_data.get("cached_at"),
        "data": view_data
    }
