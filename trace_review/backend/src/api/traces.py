"""
Trace analysis API endpoints
"""
import json
import logging
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response
from typing import Dict, Any, List, Optional, Tuple

from ..models.requests import AnalyzeTraceRequest, TraceSource
from ..models.responses import SessionTraceExportResponse
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
from ..analyzers.pdf_citations import PDFCitationsAnalyzer
from ..analyzers.token_analysis import TokenAnalysisAnalyzer
from ..analyzers.agent_context import AgentContextAnalyzer
from ..analyzers.trace_summary import TraceSummaryAnalyzer
from ..analyzers.document_hierarchy import DocumentHierarchyAnalyzer
from ..analyzers.agent_config import AgentConfigAnalyzer
from ..analyzers.extraction_timeline import (
    ANALYZER_SCHEMA_VERSION as EXTRACTION_TIMELINE_ANALYZER_SCHEMA_VERSION,
    ExtractionTimelineAnalyzer,
)
from .extraction_timeline_helpers import (
    build_extraction_timeline,
    load_extraction_timeline_context,
)
from ..utils.token_budget import create_lightweight_tool_call_summary
from ..utils.trace_output import is_trace_output_cacheable
from .auth import get_auth_dependency
from .domain_envelope_responses import domain_envelope_response_views


logger = logging.getLogger(__name__)
router = APIRouter()
TRANSIENT_CACHE_TTL_SECONDS = 15
ALL_VIEWS = [
    "summary", "conversation", "tool_calls",
    "pdf_citations", "token_analysis", "agent_context", "trace_summary",
    "document_hierarchy", "agent_configs", "group_context", "domain_envelope",
    "extraction_timeline",
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


def _effective_source(source: TraceSource) -> str:
    return "local" if source == "auto" else source


def _cache_schema_is_current(cache_data: Dict[str, Any]) -> bool:
    return cache_data.get("analyzer_schema_version") == EXTRACTION_TIMELINE_ANALYZER_SCHEMA_VERSION


def _build_trace_cache_data(trace_id: str, trace_data: Dict[str, Any]) -> Dict[str, Any]:
    raw_trace = trace_data["raw_trace"]
    observations = trace_data["observations"]

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

    return {
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
    refresh: bool = False,
) -> Tuple[Dict[str, Any], Optional[str], bool]:
    if refresh:
        cache_manager.delete(trace_id)

    cached_data = cache_manager.get(trace_id)
    if cached_data:
        if _cache_schema_is_current(cached_data):
            return cached_data, cache_manager.get_status(trace_id), True
        cache_manager.delete(trace_id)

    active_extractor = extractor or TraceExtractor(source=_effective_source(source))

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


def _sibling_trace_ids(
    *,
    trace_id: str,
    source: TraceSource,
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
            "domain_envelope": analysis["domain_envelope"],
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
    timestamps = sorted(str(trace["timestamp"]) for trace in traces if trace.get("timestamp"))
    if not timestamps:
        return None, None
    return timestamps[0], timestamps[-1]


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


def _extract_langfuse_trace(trace_id: str, source: TraceSource) -> Dict[str, Any]:
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


@router.get("/search")
async def search_traces(
    source: TraceSource = Query(default="local", description="Trace source: 'remote' (EC2), 'local' (Docker), or 'auto'"),
    session_id: Optional[str] = Query(default=None, description="Langfuse session ID"),
    user_id: Optional[str] = Query(default=None, description="Langfuse user ID"),
    name: Optional[str] = Query(default=None, description="Trace name filter"),
    document_id: Optional[str] = Query(default=None, description="Trace metadata.document_id filter"),
    run_id: Optional[str] = Query(default=None, description="Trace metadata.run_id filter"),
    extraction_id: Optional[str] = Query(default=None, description="Trace metadata.extraction_id filter"),
    from_timestamp: Optional[str] = Query(default=None, description="ISO timestamp lower bound"),
    to_timestamp: Optional[str] = Query(default=None, description="ISO timestamp upper bound"),
    limit: int = Query(default=100, ge=1, le=1000, description="Maximum traces to return"),
    user: Dict[str, Any] = get_auth_dependency(),
) -> Dict[str, Any]:
    """Search Langfuse traces by indexed IDs or trace metadata."""
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
        logger.exception("Error searching Langfuse traces from %s", source)
        raise HTTPException(
            status_code=502,
            detail=f"Unable to search Langfuse traces ({source}): {str(exc)}",
        ) from exc

    return {
        "status": "success",
        "source": source,
        "trace_count": len(listing["traces"]),
        "query": listing["query"],
        "langfuse_meta": listing["meta"],
        "traces": [_listed_trace_reference(trace) for trace in listing["traces"]],
    }


@router.get("/{trace_id}/tree")
async def get_trace_tree(
    trace_id: str,
    source: TraceSource = Query(default="local", description="Trace source: 'remote' (EC2), 'local' (Docker), or 'auto'"),
    user: Dict[str, Any] = get_auth_dependency(),
) -> Dict[str, Any]:
    """Fetch a complete Langfuse trace tree with raw observations and scores."""
    trace_data = _extract_langfuse_trace(trace_id, source)
    return {
        "status": "success",
        "source": source,
        "trace_id": trace_id,
        "raw_trace": trace_data["raw_trace"],
        "observations": trace_data["observations"],
        "scores": trace_data["scores"],
        "metadata": trace_data["metadata"],
        "tree": build_trace_tree(trace_data),
    }


@router.get("/{trace_id}/reconstruction")
async def get_trace_reconstruction(
    trace_id: str,
    source: TraceSource = Query(default="local", description="Trace source: 'remote' (EC2), 'local' (Docker), or 'auto'"),
    include_payloads: bool = Query(default=False, description="Include full payload values in ordered events"),
    user: Dict[str, Any] = get_auth_dependency(),
) -> Dict[str, Any]:
    """Return chronological agent/model/tool/event reconstruction for a trace."""
    trace_data = _extract_langfuse_trace(trace_id, source)
    return {
        "status": "success",
        "source": source,
        "data": build_ordered_reconstruction(
            trace_data,
            include_payload_values=include_payloads,
        ),
    }


@router.get("/{trace_id}/reconstruction.ndjson")
async def get_trace_reconstruction_ndjson(
    trace_id: str,
    source: TraceSource = Query(default="local", description="Trace source: 'remote' (EC2), 'local' (Docker), or 'auto'"),
    include_payloads: bool = Query(default=False, description="Include full payload values in NDJSON events"),
    user: Dict[str, Any] = get_auth_dependency(),
) -> Response:
    """Return ordered reconstruction as machine-friendly NDJSON."""
    trace_data = _extract_langfuse_trace(trace_id, source)
    reconstruction = build_ordered_reconstruction(
        trace_data,
        include_payload_values=include_payloads,
    )
    lines = [
        json.dumps({"record_type": "trace", "trace": reconstruction["trace"]}, default=str),
        *[
            json.dumps({"record_type": "event", **event}, default=str)
            for event in reconstruction["events"]
        ],
    ]
    return Response(
        content="\n".join(lines) + "\n",
        media_type="application/x-ndjson",
    )


@router.get("/{trace_id}/payloads")
async def get_trace_payloads(
    trace_id: str,
    source: TraceSource = Query(default="local", description="Trace source: 'remote' (EC2), 'local' (Docker), or 'auto'"),
    include_values: bool = Query(default=False, description="Include full payload values in the paginated response"),
    sort: str = Query(default="largest", description="Sort order: 'largest' or 'chronological'"),
    limit: int = Query(default=50, ge=1, le=1000, description="Maximum payload summaries to return"),
    offset: int = Query(default=0, ge=0, description="Pagination offset"),
    user: Dict[str, Any] = get_auth_dependency(),
) -> Dict[str, Any]:
    """Return trace/model/tool input/output payload summaries, largest first by default."""
    if sort not in {"largest", "chronological"}:
        raise HTTPException(status_code=400, detail="sort must be 'largest' or 'chronological'")
    trace_data = _extract_langfuse_trace(trace_id, source)
    payloads = build_payload_inventory(trace_data, include_values=include_values)
    page, pagination = paginate_payloads(payloads, limit=limit, offset=offset, sort=sort)
    return {
        "status": "success",
        "source": source,
        "trace_id": trace_id,
        "sort": sort,
        "pagination": pagination,
        "payloads": page,
    }


@router.get("/{trace_id}/payload")
async def get_trace_payload(
    trace_id: str,
    source: TraceSource = Query(default="local", description="Trace source: 'remote' (EC2), 'local' (Docker), or 'auto'"),
    payload_id: Optional[str] = Query(default=None, description="Payload ID returned by /payloads, e.g. observation:obs-id:output"),
    scope: Optional[str] = Query(default=None, description="Payload scope when payload_id is omitted: trace or observation"),
    observation_id: Optional[str] = Query(default=None, description="Observation/span ID when retrieving observation input/output"),
    field: Optional[str] = Query(default=None, description="Payload field: input or output"),
    start: int = Query(default=0, ge=0, description="Start character for chunked retrieval"),
    max_chars: int = Query(default=0, ge=0, description="Maximum characters to return; 0 returns the full payload"),
    user: Dict[str, Any] = get_auth_dependency(),
) -> Dict[str, Any]:
    """Return one exact trace or observation payload from Langfuse."""
    if not payload_id:
        if field not in {"input", "output"}:
            raise HTTPException(status_code=400, detail="field must be 'input' or 'output'")
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
    return {
        "status": "success",
        "source": source,
        "trace_id": trace_id,
        "payload": payload,
    }


@router.get("/{trace_id}/costs")
async def get_trace_costs(
    trace_id: str,
    source: TraceSource = Query(default="local", description="Trace source: 'remote' (EC2), 'local' (Docker), or 'auto'"),
    user: Dict[str, Any] = get_auth_dependency(),
) -> Dict[str, Any]:
    """Return token and cost accounting by trace, agent, model, and observation."""
    trace_data = _extract_langfuse_trace(trace_id, source)
    return {
        "status": "success",
        "source": source,
        "data": build_cost_summary(trace_data),
    }


@router.get("/{trace_id}/duplicates")
async def get_trace_duplicate_payloads(
    trace_id: str,
    source: TraceSource = Query(default="local", description="Trace source: 'remote' (EC2), 'local' (Docker), or 'auto'"),
    user: Dict[str, Any] = get_auth_dependency(),
) -> Dict[str, Any]:
    """Return repeated payload fingerprints across trace and observation IO."""
    trace_data = _extract_langfuse_trace(trace_id, source)
    return {
        "status": "success",
        "source": source,
        "data": build_duplicate_report(trace_data),
    }


@router.get("/{trace_id}/export")
async def export_trace(
    trace_id: str,
    request: Request,
    source: TraceSource = "remote",
    refresh: bool = Query(default=False, description="Refresh cached analysis before export"),
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
            refresh=refresh,
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
    source: TraceSource = Query(default="remote", description="Trace source: 'remote' (EC2), 'local' (Docker), or 'auto'"),
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
        extractor = TraceExtractor(source=_effective_source(source))
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
    source: TraceSource = Query(default="remote", description="Trace source: 'remote' (EC2), 'local' (Docker), or 'auto'"),
    session_id: Optional[str] = Query(default=None, description="Langfuse session ID for sibling-trace expansion"),
    feedback_id: Optional[str] = Query(default=None, description="Feedback ID linked to stored trace artifacts"),
    include_sibling_traces: bool = Query(default=False, description="Include durable events from traces in the same session"),
    refresh: bool = Query(default=False, description="Refresh cached trace analysis before rendering the view"),
    include_raw_args: bool = Query(default=False, description="Include bounded raw tool/event argument summaries"),
    include_raw_outputs: bool = Query(default=False, description="Include bounded raw tool/event output summaries"),
    tool_name: Optional[str] = Query(default=None, description="Filter extraction events by tool name"),
    event_type: Optional[str] = Query(default=None, description="Filter extraction events by event type"),
    candidate_id: Optional[str] = Query(default=None, description="Filter extraction events by candidate ID"),
    user: Dict[str, Any] = get_auth_dependency()
) -> Dict[str, Any]:
    """
    Get specific view data for a trace
    Must call /analyze first to populate cache
    """
    cache_manager = request.app.state.cache_manager

    if view_name == "extraction_timeline":
        async def load_cached_data() -> Dict[str, Any]:
            cached_data, _cache_status, _from_cache = _get_or_analyze_trace_export(
                trace_id,
                cache_manager,
                source,
                refresh=refresh,
            )
            return cached_data

        async def load_sibling_cached_data(sibling_trace_id: str) -> Dict[str, Any]:
            cached_data, _cache_status, _from_cache = _get_or_analyze_trace_export(
                sibling_trace_id,
                cache_manager,
                source,
                refresh=refresh,
            )
            return cached_data

        try:
            context = await load_extraction_timeline_context(
                trace_id=trace_id,
                feedback_id=feedback_id,
                include_sibling_traces=include_sibling_traces,
                load_cached_data=load_cached_data,
                load_sibling_trace_ids=lambda: _sibling_trace_ids(
                    trace_id=trace_id,
                    source=source,
                    session_id=session_id,
                    include_sibling_traces=include_sibling_traces,
                ),
                load_sibling_cached_data=load_sibling_cached_data,
                fallback_exceptions=(TraceExtractionError,),
                unavailable_exception_factory=lambda exc: HTTPException(
                    status_code=404,
                    detail=f"Trace {trace_id} not found in Langfuse ({source}): {str(exc)}",
                ),
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
        except TraceAnalysisError as e:
            logger.exception("Error analyzing trace: %s", e)
            raise HTTPException(
                status_code=500,
                detail=f"Error analyzing trace: {str(e)}"
            )

        return {
            "view": view_name,
            "trace_id": trace_id,
            "cached_at": context.cached_data.get("cached_at"),
            "data": timeline,
        }

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
