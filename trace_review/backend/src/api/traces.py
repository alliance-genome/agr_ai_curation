"""
Trace analysis API endpoints
"""
from datetime import datetime
from fastapi import APIRouter, HTTPException, Request
from typing import Dict, Any

from ..models.requests import AnalyzeTraceRequest
from ..services.trace_extractor import TraceExtractor
from ..analyzers.conversation import ConversationAnalyzer
from ..analyzers.tool_calls import ToolCallAnalyzer
from ..analyzers.pdf_citations import PDFCitationsAnalyzer
from ..analyzers.token_analysis import TokenAnalysisAnalyzer
from ..analyzers.agent_context import AgentContextAnalyzer
from ..analyzers.trace_summary import TraceSummaryAnalyzer
from ..analyzers.document_hierarchy import DocumentHierarchyAnalyzer
from ..analyzers.agent_config import AgentConfigAnalyzer
from .auth import get_auth_dependency


router = APIRouter()

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

    # Define all available views (supervisor_routing removed - no longer used)
    ALL_VIEWS = [
        "summary", "conversation", "tool_calls",
        "pdf_citations", "token_analysis", "agent_context", "trace_summary",
        "document_hierarchy", "agent_configs", "group_context"
    ]

    # Check cache first
    cached_data = cache_manager.get(trace_id)
    if cached_data:
        return {
            "status": "success",
            "trace_id": trace_id,
            "trace_id_short": trace_id[:8] if len(trace_id) >= 8 else trace_id,
            "message": "Trace loaded from cache",
            "cache_status": "hit",
            "cached_at": cached_data.get("cached_at"),
            "available_views": ALL_VIEWS
        }

    # Cache miss - fetch from Langfuse
    try:
        extractor = TraceExtractor(source=request_data.source)
        trace_data = extractor.extract_complete_trace(trace_id)
    except Exception as e:
        raise HTTPException(
            status_code=404,
            detail=f"Trace {trace_id} not found in Langfuse ({request_data.source}): {str(e)}"
        )

    # Run analyzers
    try:
        raw_trace = trace_data["raw_trace"]
        observations = trace_data["observations"]

        # Generate all views
        conversation = ConversationAnalyzer.extract_conversation(raw_trace, observations)
        tool_calls = ToolCallAnalyzer.extract_tool_calls(observations)
        pdf_citations = PDFCitationsAnalyzer.analyze(observations)
        token_analysis = TokenAnalysisAnalyzer.analyze(trace_data, observations)
        agent_context = AgentContextAnalyzer.analyze(trace_data, observations)
        trace_summary = TraceSummaryAnalyzer.analyze(trace_data, observations)
        document_hierarchy = DocumentHierarchyAnalyzer.analyze(trace_data, observations)
        agent_configs = AgentConfigAnalyzer.extract_agent_configs(observations)

        # Build summary from trace metadata
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

        # Extract group context from trace metadata
        # Dual-read: support both active_groups (new) and active_mods (historical)
        active_groups = metadata.get("active_groups") or metadata.get("active_mods", [])
        group_context = {
            "active_groups": active_groups,
            "injection_active": len(active_groups) > 0,
            "group_count": len(active_groups),
            "group_details": [
                {"group_id": grp, "description": GROUP_DESCRIPTIONS.get(grp, "Unknown group")}
                for grp in active_groups
            ]
        }

        # Store in cache
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

        return {
            "status": "success",
            "trace_id": trace_id,
            "trace_id_short": trace_data["trace_id_short"],
            "message": "Trace analyzed successfully",
            "cache_status": "miss",
            "available_views": ALL_VIEWS
        }

    except Exception as e:
        logger.exception(f"Error analyzing trace: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Error analyzing trace: {str(e)}"
        )


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
    source: str = "remote",
    user: Dict[str, Any] = get_auth_dependency()
) -> Dict[str, Any]:
    """
    Export full trace analysis as JSON
    - Checks cache first
    - If not cached, fetches from Langfuse, analyzes, and caches
    - Returns complete analysis data
    """
    import logging
    logger = logging.getLogger(__name__)
    logger.info(f"Exporting trace {trace_id} from source: {source}")

    cache_manager = request.app.state.cache_manager

    # Check cache first
    cached_data = cache_manager.get(trace_id)

    if cached_data:
        return cached_data

    # Cache miss - fetch from Langfuse and analyze
    try:
        logger.info(f"Trace {trace_id} not in cache, fetching from {source}")
        extractor = TraceExtractor(source=source)
        trace_data = extractor.extract_complete_trace(trace_id)
        logger.info(f"Successfully extracted trace {trace_id}")
    except Exception as e:
        logger.error(f"Error extracting trace {trace_id}: {str(e)}")
        raise HTTPException(
            status_code=404,
            detail=f"Trace {trace_id} not found in Langfuse ({source}): {str(e)}"
        )

    # Run analyzers
    try:
        raw_trace = trace_data["raw_trace"]
        observations = trace_data["observations"]

        # Generate all views
        conversation = ConversationAnalyzer.extract_conversation(raw_trace, observations)
        tool_calls = ToolCallAnalyzer.extract_tool_calls(observations)
        pdf_citations = PDFCitationsAnalyzer.analyze(observations)
        token_analysis = TokenAnalysisAnalyzer.analyze(trace_data, observations)
        agent_context = AgentContextAnalyzer.analyze(trace_data, observations)
        trace_summary = TraceSummaryAnalyzer.analyze(trace_data, observations)
        document_hierarchy = DocumentHierarchyAnalyzer.analyze(trace_data, observations)
        agent_configs = AgentConfigAnalyzer.extract_agent_configs(observations)

        # Build summary from trace metadata
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

        # Extract group context from trace metadata
        # Dual-read: support both active_groups (new) and active_mods (historical)
        active_groups = metadata.get("active_groups") or metadata.get("active_mods", [])
        group_context = {
            "active_groups": active_groups,
            "injection_active": len(active_groups) > 0,
            "group_count": len(active_groups),
            "group_details": [
                {"group_id": grp, "description": GROUP_DESCRIPTIONS.get(grp, "Unknown group")}
                for grp in active_groups
            ]
        }

        # Store in cache
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
        logger.info(f"Cached trace {trace_id}")

        return cache_data

    except Exception as e:
        logger.exception(f"Error analyzing trace {trace_id}: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Error analyzing trace: {str(e)}"
        )


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
