"""
Claude-Specific Trace Analysis API Endpoints

Token-aware endpoints designed for Claude/Opus workflow analysis.
All responses include token metadata to help Claude manage context budget.

Endpoints:
- GET /summary - Lightweight trace overview (~500 tokens)
- GET /tool_calls/summary - Paginated tool-call summaries
- GET /tool_calls - Paginated exact-field references
- GET /tool_calls/{call_id} - One exact input/result chunk
- GET /conversation - One exact user/assistant field chunk
"""

import json
import hashlib
import logging
import math
from datetime import datetime
from typing import Annotated, Callable, Dict, Any, Optional, List, Mapping, Literal
from fastapi import APIRouter, Depends, HTTPException, Request, Query, Path

from ..services.trace_extractor import TraceExtractor, TraceNotFoundError
from ..services.feedback_artifacts import (
    feedback_artifacts_contain_trace,
    fetch_feedback_trace_artifacts,
)
from ..services.langfuse_run_reconstruction import (
    build_cost_summary,
    build_duplicate_report,
    build_ordered_reconstruction,
    build_payload_inventory,
    build_trace_tree,
    find_payload,
    paginate_payloads,
    serialize_payload,
)
from ..config import (
    get_agent_studio_provider_tool_result_inline_max_chars,
    get_agent_studio_trace_review_aggregate_page_size,
    get_agent_studio_trace_review_chunk_max_chars,
    get_agent_studio_trace_review_page_size,
    get_agent_studio_trace_review_summary_max_chars,
    get_agent_studio_trace_search_default_limit,
    get_agent_studio_trace_search_filter_max_chars,
    get_agent_studio_trace_search_max_limit,
)
from ..analyzers.conversation import ConversationAnalyzer
from ..analyzers.tool_calls import ToolCallAnalyzer
from ..analyzers.trace_summary import TraceSummaryAnalyzer
from ..analyzers.extraction_timeline import (
    ANALYZER_SCHEMA_VERSION as EXTRACTION_TIMELINE_ANALYZER_SCHEMA_VERSION,
    ExtractionTimelineAnalyzer,
)
from .extraction_timeline_helpers import (
    build_evidence_revisions,
    build_extraction_timeline,
    load_extraction_timeline_context,
)
from ..utils.token_budget import (
    create_token_info_dict,
    create_lightweight_tool_call_summary,
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


def _trusted_caller_sub(user: Mapping[str, Any]) -> str:
    """Resolve the owner identity from authenticated, non-model-controlled claims."""
    claim = (
        user.get("trusted_caller_sub")
        if user.get("token_use") == "internal_service"
        else user.get("sub")
    )
    caller_sub = str(claim or "").strip()
    if not caller_sub:
        raise HTTPException(status_code=401, detail="Trusted caller identity is required.")
    return caller_sub


async def _authorize_claude_trace_request(
    request: Request,
    user: Dict[str, Any] = get_auth_dependency(),
) -> None:
    """Enforce owner-only access for every exact trace route on this router."""
    caller_sub = _trusted_caller_sub(user)
    request.state.trace_caller_sub = caller_sub
    trace_id = str(request.path_params.get("trace_id") or "").strip()
    if not trace_id:
        return

    source = request.query_params.get("source", DEFAULT_SOURCE)
    extraction_error: Exception | None = None
    cached_data = request.app.state.cache_manager.get(trace_id)
    if cached_data is not None:
        raw_trace = cached_data.get("raw_trace") or {}
        owner = str(
            raw_trace.get("userId") or raw_trace.get("user_id") or ""
        ).strip()
        if not owner or owner != caller_sub:
            raise HTTPException(status_code=404, detail="Trace not found.")
        return
    try:
        trace_data = TraceExtractor(
            source=_effective_source(source)
        ).extract_complete_trace(trace_id)
    except Exception as exc:
        extraction_error = exc
    else:
        raw_trace = trace_data.get("raw_trace") or {}
        owner = str(raw_trace.get("userId") or raw_trace.get("user_id") or "").strip()
        if not owner or owner != caller_sub:
            raise HTTPException(status_code=404, detail="Trace not found.")
        request.state.authorized_trace_extraction = {
            "trace_id": trace_id,
            "source": _effective_source(source),
            "trace_data": trace_data,
        }
        return

    route_name = str(getattr(request.scope.get("route"), "name", ""))
    feedback_id = request.query_params.get("feedback_id")
    feedback_routes = {
        "get_extraction_timeline",
        "get_extraction_diagnostic_report",
        "get_evidence_revisions",
    }
    if feedback_id and route_name in feedback_routes:
        artifacts = fetch_feedback_trace_artifacts(
            feedback_id,
            caller_sub=caller_sub,
            caller_email=str(
                user.get("trusted_caller_email") or user.get("email") or ""
            ).strip() or None,
        )
        if feedback_artifacts_contain_trace(artifacts, trace_id):
            request.state.authorized_feedback_artifacts = artifacts
            return
        artifact_status = str((artifacts or {}).get("status") or "")
        if artifact_status in {"not_configured", "unavailable"}:
            LOGGER.error(
                "Feedback artifact authorization lookup failed: status=%s",
                artifact_status,
            )
            raise HTTPException(
                status_code=503,
                detail="Trace provider is temporarily unavailable.",
            ) from None

    if isinstance(extraction_error, TraceNotFoundError):
        raise HTTPException(status_code=404, detail="Trace not found.") from extraction_error

    LOGGER.error(
        "Trace authorization provider lookup failed: error_type=%s",
        type(extraction_error).__name__,
    )
    raise HTTPException(
        status_code=503,
        detail="Trace provider is temporarily unavailable.",
    ) from None


router = APIRouter(dependencies=[Depends(_authorize_claude_trace_request)])
LOGGER = logging.getLogger(__name__)
TRANSIENT_CACHE_TTL_SECONDS = 15
TRACE_REVIEW_PAGE_SIZE = get_agent_studio_trace_review_page_size()
TRACE_REVIEW_AGGREGATE_PAGE_SIZE = get_agent_studio_trace_review_aggregate_page_size()
TRACE_REVIEW_SUMMARY_MAX_CHARS = get_agent_studio_trace_review_summary_max_chars()
TRACE_REVIEW_CHUNK_MAX_CHARS = get_agent_studio_trace_review_chunk_max_chars()
TRACE_REVIEW_PROVIDER_INLINE_MAX_CHARS = (
    get_agent_studio_provider_tool_result_inline_max_chars()
)
TRACE_SEARCH_DEFAULT_LIMIT = get_agent_studio_trace_search_default_limit()
TRACE_SEARCH_MAX_LIMIT = get_agent_studio_trace_search_max_limit()
TRACE_SEARCH_FILTER_MAX_CHARS = get_agent_studio_trace_search_filter_max_chars()


# Default source for trace extraction (EC2 Langfuse)
DEFAULT_SOURCE = "local"
TOOL_CALL_METADATA_KEYS = (
    "id",
    "call_id",
    "name",
    "time",
    "duration",
    "model",
    "url",
    "method",
    "status",
    "status_code",
    "tool_result_length",
)


def _effective_source(source: str) -> str:
    return "local" if source == "auto" else source


def _trace_id_short(trace_id: Optional[str]) -> Optional[str]:
    if not trace_id:
        return None
    return trace_id[:8] if len(trace_id) >= 8 else trace_id


def _estimate_tokens_from_chars(chars: int) -> int:
    if chars <= 0:
        return 0
    return max(1, math.ceil(chars / 4))


def _threshold_for_tokens(tokens: int) -> Optional[str]:
    reached = [threshold for threshold in (100_000, 250_000, 1_000_000) if tokens >= threshold]
    return str(max(reached)) if reached else None


def _payload_json_chars(value: Any) -> int:
    if isinstance(value, str):
        return len(value)
    try:
        return len(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str))
    except (TypeError, ValueError):
        LOGGER.warning("Falling back to string length for payload JSON sizing", exc_info=True)
        return len(str(value))


def _tool_call_selector(tool_call: Mapping[str, Any], index: int) -> str:
    """Return the stable selector accepted by the exact-detail endpoint."""
    for key in ("call_id", "id"):
        value = str(tool_call.get(key) or "").strip()
        if value and value.upper() != "N/A":
            if len(value) > TRACE_REVIEW_SUMMARY_MAX_CHARS:
                digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
                return f"sha256:{digest}"
            return value
    return f"index:{index}"


def _tool_call_exact_value(tool_call: Mapping[str, Any], field: str) -> Any:
    """Project analyzer formats onto canonical exact input/result fields."""
    if field == "input":
        return tool_call.get("input")
    if field == "thought":
        return tool_call.get("thought")
    if field == "metadata":
        return {key: tool_call.get(key) for key in TOOL_CALL_METADATA_KEYS}
    if field == "domain_envelope":
        return tool_call.get("domain_envelope")
    tool_result = tool_call.get("tool_result")
    if tool_result is not None or "call_id" in tool_call:
        return tool_result
    return tool_call.get("output")


def _json_bounded_prefix(value: str, max_json_chars: int) -> str:
    """Bound a string by its provider-compatible JSON-encoded content size."""
    if not value:
        return ""

    def encoded_content_chars(candidate: str) -> int:
        # The provider serializer uses json.dumps defaults, including ensure_ascii.
        return max(0, len(json.dumps(candidate, default=str)) - 2)

    if encoded_content_chars(value) <= max_json_chars:
        return value

    low, high = 0, len(value)
    while low < high:
        midpoint = (low + high + 1) // 2
        if encoded_content_chars(value[:midpoint]) <= max_json_chars:
            low = midpoint
        else:
            high = midpoint - 1
    # Always advance a valid cursor even under an unrealistically tiny setting.
    return value[: max(1, low)]


def _exact_text_metadata(*, field_id: str, field: str, value: Any) -> Dict[str, Any]:
    serialized = serialize_payload(value)
    return {
        "field_id": field_id,
        "field": field,
        "sha256": hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
        "total_char_count": len(serialized),
        "byte_count": len(serialized.encode("utf-8")),
    }


def _exact_text_chunk(
    *,
    field_id: str,
    field: str,
    value: Any,
    start: int,
    max_chars: int,
    next_call: Mapping[str, Any],
    provider_result_builder: Callable[[Dict[str, Any]], Mapping[str, Any]],
) -> Dict[str, Any]:
    """Build the largest exact chunk whose complete provider result fits."""
    serialized = serialize_payload(value)
    safe_start = min(max(0, start), len(serialized))
    safe_max_chars = min(max(1, max_chars), TRACE_REVIEW_CHUNK_MAX_CHARS)
    metadata = _exact_text_metadata(field_id=field_id, field=field, value=value)

    def build_chunk(end: int) -> Dict[str, Any]:
        complete = end >= len(serialized)
        return {
            **metadata,
            "start": safe_start,
            "end": end,
            "returned_char_count": end - safe_start,
            "complete": complete,
            "next_start": None if complete else end,
            "next_call": (
                None
                if complete
                else {**next_call, "start": end, "max_chars": safe_max_chars}
            ),
            "serialized": serialized[safe_start:end],
        }

    def fits_provider_result(chunk: Dict[str, Any]) -> bool:
        return len(json.dumps(provider_result_builder(chunk), default=str)) <= (
            TRACE_REVIEW_PROVIDER_INLINE_MAX_CHARS
        )

    requested_end = min(safe_start + safe_max_chars, len(serialized))
    requested_chunk = build_chunk(requested_end)
    if fits_provider_result(requested_chunk):
        return requested_chunk

    fitting_chunk: Dict[str, Any] | None = None
    low = safe_start + 1
    high = requested_end - 1
    while low <= high:
        candidate_end = (low + high) // 2
        candidate = build_chunk(candidate_end)
        if fits_provider_result(candidate):
            fitting_chunk = candidate
            low = candidate_end + 1
        else:
            high = candidate_end - 1

    if fitting_chunk is not None:
        return fitting_chunk

    raise HTTPException(
        status_code=400,
        detail=(
            "The provider inline tool-result limit is too small to return even one "
            "exact TraceReview character with its required identity metadata."
        ),
    )


def _bounded_summary(value: Any) -> str:
    text = str(value or "")
    return _json_bounded_prefix(text, TRACE_REVIEW_SUMMARY_MAX_CHARS)


def _nonnegative_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _collection_projection(
    values: Any,
    *,
    next_call: Mapping[str, Any],
) -> Dict[str, Any]:
    """Describe an omitted collection and expose its lossless aggregate page."""
    items = _aggregate_items(values)
    return {
        "total_items": len(items),
        "complete": len(items) == 0,
        "next_call": None if not items else dict(next_call),
    }


def _domain_envelope_projection(
    value: Any,
    *,
    trace_id: str,
    call_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Return fixed-shape domain signals without copying analyzer collections."""
    if not isinstance(value, Mapping):
        return None
    raw_summary = value.get("summary")
    summary: Mapping[str, Any] = raw_summary if isinstance(raw_summary, Mapping) else {}
    collection_names = (
        "envelope_ids",
        "object_ids",
        "pending_ref_ids",
        "finding_ids",
        "field_paths",
        "readiness_statuses",
        "validator_group_scope_audits",
    )
    return {
        "found": bool(value.get("found")),
        "summary": {
            key: _nonnegative_int(summary.get(key))
            for key in (
                "envelope_count",
                "object_count",
                "finding_count",
                "blocker_count",
                "pending_ref_count",
            )
            if key in summary
        },
        "has_blockers": bool(value.get("has_blockers") or summary.get("blocker_count")),
        "has_definition_state_flags": bool(value.get("has_definition_state_flags")),
        "collections": {
            name: _collection_projection(
                value.get(name, []),
                next_call=(
                    {
                        "trace_id": trace_id,
                        "call_id": call_id,
                        "field": "domain_envelope",
                        "start": 0,
                        "max_chars": TRACE_REVIEW_CHUNK_MAX_CHARS,
                    }
                    if call_id is not None
                    else {
                        "trace_id": trace_id,
                        "view_name": "domain_envelope",
                        "section": name,
                        "offset": 0,
                        "limit": TRACE_REVIEW_AGGREGATE_PAGE_SIZE,
                    }
                ),
            )
            for name in collection_names
        },
    }


def _unique_tools_projection(values: Any, *, trace_id: str) -> Dict[str, Any]:
    tools = sorted(str(value) for value in values or [])
    return {
        "total_items": len(tools),
        "preview": [_bounded_summary(value) for value in tools[:TRACE_REVIEW_AGGREGATE_PAGE_SIZE]],
        "complete": len(tools) <= TRACE_REVIEW_AGGREGATE_PAGE_SIZE,
        "next_call": (
            None
            if not tools
            else {
                "trace_id": trace_id,
                "view_name": "tool_calls",
                "section": "unique_tools",
                "offset": 0,
                "limit": TRACE_REVIEW_AGGREGATE_PAGE_SIZE,
            }
        ),
    }


def _tool_call_metadata_projection(
    tool_call: Mapping[str, Any],
    *,
    index: int,
    trace_id: str,
) -> Dict[str, Any]:
    """Allowlist bounded metadata shared by page and exact-detail envelopes."""
    selector = _tool_call_selector(tool_call, index)
    metadata = {
        "index": index,
        "call_id": selector,
        "id": _bounded_summary(tool_call.get("id")),
        "name": _bounded_summary(tool_call.get("name")),
        "time": _bounded_summary(tool_call.get("time")),
        "duration": _bounded_summary(tool_call.get("duration")),
        "model": _bounded_summary(tool_call.get("model")),
        "url": _bounded_summary(tool_call.get("url")),
        "method": _bounded_summary(tool_call.get("method")),
        "status": _bounded_summary(tool_call.get("status")),
        "status_code": _bounded_summary(tool_call.get("status_code")),
        "tool_result_length": _nonnegative_int(tool_call.get("tool_result_length")),
        "domain_envelope": _domain_envelope_projection(
            tool_call.get("domain_envelope"),
            trace_id=trace_id,
            call_id=selector,
        ),
    }
    return metadata


def _tool_call_metadata_requires_exact(tool_call: Mapping[str, Any]) -> bool:
    return any(
        _bounded_summary(tool_call.get(key)) != str(tool_call.get(key) or "")
        for key in TOOL_CALL_METADATA_KEYS
        if key != "tool_result_length"
    )


def _aggregate_items(value: Any) -> List[Any]:
    """Project a sequence or mapping into lossless, deterministically ordered items."""
    if isinstance(value, Mapping):
        return [
            {"key": str(key), "value": value[key]}
            for key in sorted(value, key=str)
        ]
    if isinstance(value, list):
        return value
    return [value]


def _aggregate_token_info(data: Mapping[str, Any]) -> Dict[str, Any]:
    """Measure the complete provider-visible aggregate result to a fixed point."""
    token_info = create_token_info_dict(data)
    for _ in range(3):
        provider_result = {
            "status": "success",
            "data": data,
            "token_info": token_info,
            "error": None,
        }
        serialized_chars = len(json.dumps(provider_result, default=str))
        within_budget = serialized_chars <= TRACE_REVIEW_PROVIDER_INLINE_MAX_CHARS
        token_info = {
            **token_info,
            "serialized_chars": serialized_chars,
            "max_serialized_chars": TRACE_REVIEW_PROVIDER_INLINE_MAX_CHARS,
            "within_budget": within_budget,
            "warning": (
                None
                if within_budget
                else (
                    "Response exceeds the Agent Studio serialized-character boundary "
                    f"({serialized_chars:,} chars > "
                    f"{TRACE_REVIEW_PROVIDER_INLINE_MAX_CHARS:,})."
                )
            ),
        }
    return token_info


def _aggregate_provider_result(data: Mapping[str, Any]) -> Dict[str, Any]:
    """Build the complete provider-visible wrapper emitted by Agent Studio."""
    return {
        "status": "success",
        "data": data,
        "token_info": _aggregate_token_info(data),
        "error": None,
    }


def _complete_provider_token_info(
    payload: Any,
    provider_result_builder: Callable[[Mapping[str, Any]], Mapping[str, Any]],
) -> Dict[str, Any]:
    """Measure a non-aggregate provider wrapper, including its token metadata."""
    token_info = create_token_info_dict(payload)
    for _ in range(4):
        serialized_chars = len(
            json.dumps(provider_result_builder(token_info), default=str)
        )
        within_budget = serialized_chars <= TRACE_REVIEW_PROVIDER_INLINE_MAX_CHARS
        token_info = {
            **token_info,
            "estimated_tokens": _estimate_tokens_from_chars(serialized_chars),
            "serialized_chars": serialized_chars,
            "max_serialized_chars": TRACE_REVIEW_PROVIDER_INLINE_MAX_CHARS,
            "within_budget": within_budget,
            "warning": (
                None
                if within_budget
                else (
                    "Response exceeds the Agent Studio serialized-character boundary "
                    f"({serialized_chars:,} chars > "
                    f"{TRACE_REVIEW_PROVIDER_INLINE_MAX_CHARS:,})."
                )
            ),
        }
    return token_info


def _aggregate_response_data(
    *,
    source: str,
    trace_id: str,
    view: str,
    summary: Mapping[str, Any],
    collections: Mapping[str, Any],
    filters: Mapping[str, Any],
    section: Optional[str],
    offset: int,
    limit: int,
    next_call_base: Mapping[str, Any],
    item_start: int = 0,
) -> Dict[str, Any]:
    """Return a summary-first aggregate page that fits the provider envelope."""
    inventory_limit = min(max(1, limit), TRACE_REVIEW_AGGREGATE_PAGE_SIZE)
    inventory = []
    for name, value in collections.items():
        total_items = len(_aggregate_items(value))
        complete = total_items == 0
        inventory.append({
            "section": name,
            "total_items": total_items,
            "complete": complete,
            "truncated": not complete,
            "next_call": (
                None
                if complete
                else {
                    **next_call_base,
                    "section": name,
                    "offset": 0,
                    "limit": inventory_limit,
                }
            ),
        })
    base: Dict[str, Any] = {
        "source": source,
        "trace_id": trace_id,
        "view": view,
        "summary": dict(summary),
        "filters": {key: value for key, value in filters.items() if value is not None},
        "collections": inventory,
    }
    if section is None:
        base["page"] = None
        if len(json.dumps(_aggregate_provider_result(base), default=str)) > (
            TRACE_REVIEW_PROVIDER_INLINE_MAX_CHARS
        ):
            raise HTTPException(
                status_code=400,
                detail="Aggregate summary exceeds the provider inline tool-result limit",
            )
        return base
    if section not in collections:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Invalid section '{section}'. Valid sections: "
                f"{', '.join(collections)}"
            ),
        )

    items = _aggregate_items(collections[section])
    safe_offset = max(0, offset)
    safe_limit = inventory_limit
    safe_item_start = max(0, item_start)
    selected: List[Any] = []

    def build_page(page_items: List[Any]) -> Dict[str, Any]:
        next_offset = safe_offset + len(page_items)
        complete = next_offset >= len(items)
        return {
            **base,
            "page": {
                "section": section,
                "offset": safe_offset,
                "limit": safe_limit,
                "total_items": len(items),
                "returned_items": len(page_items),
                "complete": complete,
                "truncated": not complete,
                "next_offset": None if complete else next_offset,
                "next_call": (
                    None
                    if complete
                    else {
                        **next_call_base,
                        "section": section,
                        "offset": next_offset,
                        "limit": safe_limit,
                    }
                ),
                "items": page_items,
            },
        }

    for item in items[safe_offset:safe_offset + safe_limit]:
        if safe_item_start:
            break
        candidate = build_page([*selected, item])
        if len(json.dumps(_aggregate_provider_result(candidate), default=str)) > (
            TRACE_REVIEW_PROVIDER_INLINE_MAX_CHARS
        ):
            break
        selected.append(item)

    page_data = build_page(selected)
    if safe_offset < len(items) and not selected:
        serialized_item = json.dumps(
            items[safe_offset],
            default=str,
            sort_keys=True,
            separators=(",", ":"),
        )
        item_total_chars = len(serialized_item)
        safe_item_start = min(safe_item_start, item_total_chars)
        item_sha256 = hashlib.sha256(serialized_item.encode("utf-8")).hexdigest()

        def build_item_chunk(end: int) -> Dict[str, Any]:
            item_complete = end >= item_total_chars
            aggregate_complete = item_complete and safe_offset + 1 >= len(items)
            next_offset = safe_offset + (1 if item_complete else 0)
            next_call = None
            if not aggregate_complete:
                next_call = {
                    **next_call_base,
                    "section": section,
                    "offset": next_offset,
                    "limit": safe_limit,
                    "item_start": 0 if item_complete else end,
                }
            return {
                **base,
                "page": {
                    "section": section,
                    "offset": safe_offset,
                    "limit": safe_limit,
                    "total_items": len(items),
                    "returned_items": 0,
                    "complete": aggregate_complete,
                    "truncated": not aggregate_complete,
                    "next_offset": None if aggregate_complete else next_offset,
                    "next_call": next_call,
                    "items": [],
                    "item_chunk": {
                        "item_offset": safe_offset,
                        "encoding": "json",
                        "sha256": item_sha256,
                        "start": safe_item_start,
                        "end": end,
                        "total_chars": item_total_chars,
                        "complete": item_complete,
                        "content": serialized_item[safe_item_start:end],
                    },
                },
            }

        low, high = safe_item_start, item_total_chars
        fitting_chunk = None
        while low <= high:
            candidate_end = (low + high) // 2
            candidate = build_item_chunk(candidate_end)
            if len(json.dumps(_aggregate_provider_result(candidate), default=str)) <= (
                TRACE_REVIEW_PROVIDER_INLINE_MAX_CHARS
            ):
                fitting_chunk = candidate
                low = candidate_end + 1
            else:
                high = candidate_end - 1
        if (
            fitting_chunk is None
            or fitting_chunk["page"]["item_chunk"]["end"] <= safe_item_start
        ):
            raise HTTPException(
                status_code=400,
                detail=(
                    "The provider inline tool-result limit is too small to return "
                    "one aggregate item character with its identity metadata"
                ),
            )
        return fitting_chunk
    return page_data


def _flatten_trace_tree(root: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """Flatten a tree losslessly except for replacing children with stable IDs."""
    flattened: List[Dict[str, Any]] = []

    def visit(node: Mapping[str, Any]) -> None:
        children = [child for child in node.get("children", []) if isinstance(child, Mapping)]
        flattened.append({
            **{key: value for key, value in node.items() if key != "children"},
            "child_ids": [child.get("id") for child in children],
        })
        for child in children:
            visit(child)

    visit(root)
    return flattened


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _event_payload_from_observation(observation: Mapping[str, Any]) -> Mapping[str, Any] | None:
    metadata = observation.get("metadata")
    if not isinstance(metadata, Mapping):
        return None
    payload = metadata.get("event_payload")
    return payload if isinstance(payload, Mapping) else None


def _preflight_call_from_event(
    *,
    ordinal: int,
    observation: Mapping[str, Any],
    event_payload: Mapping[str, Any],
) -> Dict[str, Any] | None:
    event_type = str(event_payload.get("event_type") or "").lower()
    if "provider_context_preflight" not in event_type:
        return None
    input_summary = event_payload.get("input_summary")
    preview = input_summary.get("preview") if isinstance(input_summary, Mapping) else None
    details = (
        preview
        if isinstance(preview, Mapping)
        else input_summary
        if isinstance(input_summary, Mapping)
        else {}
    )
    payload_summary = details.get("payload_summary") if isinstance(details, Mapping) else None
    payload_summary = payload_summary if isinstance(payload_summary, Mapping) else {}
    input_json_chars = _int_or_none(payload_summary.get("json_chars")) or 0
    estimated_tokens = _int_or_none(payload_summary.get("estimated_tokens"))
    if estimated_tokens is None:
        estimated_tokens = _estimate_tokens_from_chars(input_json_chars)
    return {
        "ordinal": ordinal,
        "surface": details.get("surface"),
        "operation": details.get("operation"),
        "provider": details.get("provider"),
        "model": details.get("model"),
        "input_json_chars": input_json_chars,
        "estimated_input_tokens": estimated_tokens,
        "threshold": payload_summary.get("threshold") or _threshold_for_tokens(estimated_tokens),
        "payload_refs": [
            f"observation:{observation.get('id')}:metadata.event_payload",
        ],
        "observability_only_refs": [],
        "largest_paths": payload_summary.get("largest_paths") or [],
        "classification_source": "provider_context_preflight",
    }


def _generation_call_from_observation(
    *,
    ordinal: int,
    observation: Mapping[str, Any],
    payloads_by_id: Mapping[str, Mapping[str, Any]],
) -> Dict[str, Any] | None:
    obs_type = str(observation.get("type") or observation.get("observationType") or "").lower()
    model = (
        observation.get("providedModelName")
        or observation.get("model")
        or observation.get("model_name")
    )
    if obs_type != "generation":
        return None
    obs_id = observation.get("id")
    payload_id = f"observation:{obs_id}:input"
    payload_ref = payloads_by_id.get(payload_id)
    if isinstance(payload_ref, Mapping):
        input_json_chars = _int_or_none(
            payload_ref.get("json_chars") or payload_ref.get("char_count")
        ) or 0
        estimated_tokens = _int_or_none(
            payload_ref.get("estimated_tokens") or payload_ref.get("rough_token_estimate")
        )
        if estimated_tokens is None:
            estimated_tokens = _estimate_tokens_from_chars(input_json_chars)
    else:
        input_json_chars = _payload_json_chars(observation.get("input"))
        estimated_tokens = _estimate_tokens_from_chars(input_json_chars)
    return {
        "ordinal": ordinal,
        "surface": "langfuse_generation",
        "operation": observation.get("name") or "generation",
        "provider": None,
        "model": model,
        "input_json_chars": input_json_chars,
        "estimated_input_tokens": estimated_tokens,
        "threshold": _threshold_for_tokens(estimated_tokens),
        "payload_refs": [payload_id] if payload_ref else [],
        "observability_only_refs": [],
        "largest_paths": [],
        "classification_source": "inferred_generation_input",
    }


def _build_model_live_context(trace_data: Mapping[str, Any]) -> Dict[str, Any]:
    payloads = build_payload_inventory(trace_data, include_values=False)
    for payload in payloads:
        preview = _bounded_summary(payload.get("preview"))
        payload["preview"] = preview
        payload["truncated_preview"] = payload.get("char_count", 0) > len(preview)
    payloads_by_id = {
        str(payload.get("payload_id")): payload
        for payload in payloads
        if payload.get("payload_id")
    }
    calls: List[Dict[str, Any]] = []
    ordinal = 1
    for observation in trace_data.get("observations") or []:
        if not isinstance(observation, Mapping):
            continue
        event_payload = _event_payload_from_observation(observation)
        preflight_call = (
            _preflight_call_from_event(
                ordinal=ordinal,
                observation=observation,
                event_payload=event_payload,
            )
            if event_payload is not None
            else None
        )
        generation_call = _generation_call_from_observation(
            ordinal=ordinal,
            observation=observation,
            payloads_by_id=payloads_by_id,
        )
        call = preflight_call or generation_call
        if call is None:
            continue
        calls.append(call)
        ordinal += 1

    total_chars = sum(call["input_json_chars"] for call in calls)
    total_tokens = sum(call["estimated_input_tokens"] for call in calls)
    threshold_counts = {
        "100000": sum(1 for call in calls if call["estimated_input_tokens"] >= 100_000),
        "250000": sum(1 for call in calls if call["estimated_input_tokens"] >= 250_000),
        "1000000": sum(1 for call in calls if call["estimated_input_tokens"] >= 1_000_000),
    }
    explicit_preflight_calls = [
        call for call in calls
        if call["classification_source"] == "provider_context_preflight"
    ]
    inferred_generation_calls = [
        call for call in calls
        if call["classification_source"] == "inferred_generation_input"
    ]
    possible_double_count = bool(explicit_preflight_calls and inferred_generation_calls)
    precision = (
        "mixed_explicit_and_inferred"
        if possible_double_count
        else "explicit"
        if explicit_preflight_calls
        else "inferred_from_langfuse_generation_inputs"
    )
    totals_by_classification = {}
    for source, source_calls in (
        ("provider_context_preflight", explicit_preflight_calls),
        ("inferred_generation_input", inferred_generation_calls),
    ):
        totals_by_classification[source] = {
            "call_count": len(source_calls),
            "total_input_json_chars": sum(call["input_json_chars"] for call in source_calls),
            "total_estimated_input_tokens": sum(
                call["estimated_input_tokens"] for call in source_calls
            ),
        }
    return {
        "observed_call_record_count": len(calls),
        "total_observed_input_json_chars": total_chars,
        "total_observed_estimated_input_tokens": total_tokens,
        "totals_by_classification": totals_by_classification,
        "threshold_counts": threshold_counts,
        "classification": {
            "preflight_event_count": len(explicit_preflight_calls),
            "inferred_generation_count": len(inferred_generation_calls),
            "historical_precision": precision,
            "possible_double_count": possible_double_count,
        },
        "calls": calls,
    }


def _listed_trace_reference(trace: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "trace_id": trace.get("id"),
        "trace_id_short": _trace_id_short(trace.get("id")),
        "trace_name": trace.get("name"),
        "timestamp": trace.get("timestamp"),
        "session_id": trace.get("sessionId"),
        "environment": trace.get("environment"),
        "tags": trace.get("tags", []),
        "latency": trace.get("latency"),
        "total_cost": trace.get("totalCost"),
        "html_path": trace.get("htmlPath"),
    }


def _search_response_data(
    *,
    source: str,
    traces: List[Dict[str, Any]],
    query: Mapping[str, Any],
    offset: int,
    limit: int,
    total_items: Optional[int],
    source_exhausted: bool,
    local_result_count: Optional[int],
    item_start: int,
    provider_scan: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Fit a stable search page, including exact chunks for one oversized record."""
    references = [_listed_trace_reference(trace) for trace in traces]
    filters = {
        key: value
        for key, value in query.items()
        if key not in {"offset", "limit"} and value is not None
    }
    safe_offset = max(0, offset)
    safe_item_start = max(0, item_start)
    authoritative_total = max(0, total_items) if total_items is not None else None
    locally_available = (
        max(0, local_result_count) if local_result_count is not None else None
    )

    def next_arguments(next_offset: int, next_item_start: int = 0) -> Dict[str, Any]:
        return {
            **filters,
            "offset": next_offset,
            "limit": limit,
            **({"item_start": next_item_start} if next_item_start else {}),
        }

    def is_complete(next_offset: int) -> bool:
        if authoritative_total is not None:
            return next_offset >= authoritative_total
        if locally_available is not None and next_offset >= locally_available:
            return True
        return source_exhausted and next_offset >= safe_offset + len(references)

    base = {
        "source": source,
        "query": filters,
        "total_items": authoritative_total,
        "total_is_authoritative": authoritative_total is not None,
        **({"provider_scan": dict(provider_scan)} if provider_scan is not None else {}),
    }

    def build_page(items: List[Dict[str, Any]]) -> Dict[str, Any]:
        next_offset = safe_offset + len(items)
        complete = is_complete(next_offset)
        return {
            **base,
            "trace_count": len(items),
            "traces": items,
            "pagination": {
                "offset": safe_offset,
                "limit": limit,
                "returned_items": len(items),
                "complete": complete,
                "truncated": not complete,
                "next_offset": None if complete else next_offset,
                "next_call": None if complete else next_arguments(next_offset),
            },
        }

    selected: List[Dict[str, Any]] = []
    for reference in references:
        if safe_item_start:
            break
        candidate = build_page([*selected, reference])
        if len(json.dumps(_aggregate_provider_result(candidate), default=str)) > (
            TRACE_REVIEW_PROVIDER_INLINE_MAX_CHARS
        ):
            break
        selected.append(reference)

    if selected or not references:
        return build_page(selected)

    serialized = json.dumps(
        references[0],
        default=str,
        sort_keys=True,
        separators=(",", ":"),
    )
    safe_item_start = min(safe_item_start, len(serialized))
    item_sha256 = hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def build_chunk(end: int) -> Dict[str, Any]:
        item_complete = end >= len(serialized)
        next_offset = safe_offset + (1 if item_complete else 0)
        complete = item_complete and is_complete(next_offset)
        return {
            **base,
            "trace_count": 0,
            "traces": [],
            "pagination": {
                "offset": safe_offset,
                "limit": limit,
                "returned_items": 0,
                "complete": complete,
                "truncated": not complete,
                "next_offset": None if complete else next_offset,
                "next_call": (
                    None
                    if complete
                    else next_arguments(next_offset, 0 if item_complete else end)
                ),
            },
            "trace_chunk": {
                "item_offset": safe_offset,
                "encoding": "json",
                "sha256": item_sha256,
                "start": safe_item_start,
                "end": end,
                "total_chars": len(serialized),
                "complete": item_complete,
                "content": serialized[safe_item_start:end],
            },
        }

    low, high = safe_item_start + 1, len(serialized)
    fitting_chunk = None
    while low <= high:
        candidate_end = (low + high) // 2
        candidate = build_chunk(candidate_end)
        if len(json.dumps(_aggregate_provider_result(candidate), default=str)) <= (
            TRACE_REVIEW_PROVIDER_INLINE_MAX_CHARS
        ):
            fitting_chunk = candidate
            low = candidate_end + 1
        else:
            high = candidate_end - 1
    if fitting_chunk is None:
        raise HTTPException(
            status_code=400,
            detail="Provider boundary is too small for one TraceReview search character",
        )
    return fitting_chunk


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
    name: Optional[str],
    document_id: Optional[str],
    run_id: Optional[str],
    extraction_id: Optional[str],
    from_timestamp: Optional[str],
    to_timestamp: Optional[str],
) -> None:
    filters = {
        "session_id": session_id,
        "name": name,
        "document_id": document_id,
        "run_id": run_id,
        "extraction_id": extraction_id,
        "from_timestamp": from_timestamp,
        "to_timestamp": to_timestamp,
    }
    if not any(filters.values()):
        raise HTTPException(
            status_code=400,
            detail=(
                "Provide at least one bounded search key: session_id, name, "
                "document_id, run_id, extraction_id, from_timestamp, or to_timestamp."
            ),
        )
    oversized = [
        key
        for key, value in filters.items()
        if value is not None and len(value) > TRACE_SEARCH_FILTER_MAX_CHARS
    ]
    if oversized:
        raise HTTPException(
            status_code=400,
            detail=(
                f"TraceReview search filters exceed the {TRACE_SEARCH_FILTER_MAX_CHARS}-"
                f"character limit: {', '.join(oversized)}"
            ),
        )


def _ensure_search_provider_headroom(
    *,
    source: str,
    query: Mapping[str, Any],
    limit: int,
) -> None:
    """Reject filters whose exact continuation leaves no trace-chunk progress."""
    try:
        _search_response_data(
            source=source,
            traces=[{"id": "provider-headroom-probe", "name": "x" * TRACE_REVIEW_PROVIDER_INLINE_MAX_CHARS}],
            query=query,
            offset=0,
            limit=limit,
            total_items=1,
            source_exhausted=True,
            local_result_count=1,
            item_start=0,
        )
    except HTTPException as exc:
        raise HTTPException(
            status_code=400,
            detail=(
                "TraceReview search filters leave no provider-envelope room for "
                "deterministic result progress"
            ),
        ) from exc


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
    caller_sub: str,
) -> List[str]:
    if not include_sibling_traces or not session_id:
        return []
    extractor = TraceExtractor(source=_effective_source(source))
    session_listing = extractor.list_session_traces(session_id)
    return [
        listed_trace["id"]
        for listed_trace in session_listing.get("traces", [])
        if listed_trace.get("id")
        and listed_trace.get("id") != trace_id
        and str(listed_trace.get("userId") or "").strip() == caller_sub
    ]


def _extract_langfuse_trace(
    request: Request,
    trace_id: str,
    source: str,
) -> Dict[str, Any]:
    authorized_extraction = getattr(
        request.state,
        "authorized_trace_extraction",
        None,
    )
    effective_source = _effective_source(source)
    if (
        authorized_extraction
        and authorized_extraction.get("trace_id") == trace_id
        and authorized_extraction.get("source") == effective_source
    ):
        return authorized_extraction["trace_data"]

    try:
        extractor = TraceExtractor(source=effective_source)
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
    Search the authenticated curator's Langfuse traces by session, trace name, indexed metadata IDs,
    or bounded timestamp window. Use this when a curator gives a session,
    document, run, or extraction ID instead of a trace ID.
    """
)
async def search_traces(
    source: str = Query(default=DEFAULT_SOURCE, description="Trace source: local, remote, or auto"),
    session_id: Optional[str] = Query(default=None, max_length=TRACE_SEARCH_FILTER_MAX_CHARS, description="Langfuse session ID"),
    name: Optional[str] = Query(default=None, max_length=TRACE_SEARCH_FILTER_MAX_CHARS, description="Trace name filter"),
    document_id: Optional[str] = Query(default=None, max_length=TRACE_SEARCH_FILTER_MAX_CHARS, description="Trace metadata.document_id filter"),
    run_id: Optional[str] = Query(default=None, max_length=TRACE_SEARCH_FILTER_MAX_CHARS, description="Trace metadata.run_id filter"),
    extraction_id: Optional[str] = Query(default=None, max_length=TRACE_SEARCH_FILTER_MAX_CHARS, description="Trace metadata.extraction_id filter"),
    from_timestamp: Optional[str] = Query(default=None, max_length=TRACE_SEARCH_FILTER_MAX_CHARS, description="ISO timestamp lower bound"),
    to_timestamp: Optional[str] = Query(default=None, max_length=TRACE_SEARCH_FILTER_MAX_CHARS, description="ISO timestamp upper bound"),
    offset: Annotated[int, Query(ge=0, description="Stable result offset from next_call")] = 0,
    limit: int = Query(
        default=TRACE_SEARCH_DEFAULT_LIMIT,
        ge=1,
        le=TRACE_SEARCH_MAX_LIMIT,
        description="Maximum source matches requested before provider-envelope fitting",
    ),
    item_start: Annotated[
        int,
        Query(ge=0, description="Exact JSON cursor for an oversized record"),
    ] = 0,
    user: Dict[str, Any] = get_auth_dependency(),
) -> ClaudeTraceResponse:
    user_id = _trusted_caller_sub(user)
    _ensure_search_scope(
        session_id=session_id,
        name=name,
        document_id=document_id,
        run_id=run_id,
        extraction_id=extraction_id,
        from_timestamp=from_timestamp,
        to_timestamp=to_timestamp,
    )
    _ensure_search_provider_headroom(
        source=source,
        query={
            "session_id": session_id,
            "name": name,
            "document_id": document_id,
            "run_id": run_id,
            "extraction_id": extraction_id,
            "from_timestamp": from_timestamp,
            "to_timestamp": to_timestamp,
        },
        limit=limit,
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
            offset=offset,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Unable to search Langfuse traces ({source}): {str(exc)}",
        ) from exc

    response_data = _search_response_data(
        source=source,
        traces=listing["traces"],
        query={
            key: value
            for key, value in listing["query"].items()
            if key != "user_id"
        },
        offset=offset,
        limit=limit,
        total_items=listing.get("total_items"),
        source_exhausted=bool(listing.get("source_exhausted")),
        local_result_count=listing.get("local_result_count"),
        item_start=item_start,
        provider_scan={
            "status": (
                "partial"
                if listing.get("scan_truncated")
                or int(listing["meta"].get("observationsRejected") or 0)
                or int(listing["meta"].get("tracesRejected") or 0)
                else "complete"
            ),
            "scan_truncated": bool(listing.get("scan_truncated")),
            "hydration_truncated": bool(
                listing["meta"].get("hydrationTruncated")
            ),
            "requests_made": int(listing["meta"].get("requestsMade") or 0),
            "request_limit": int(listing["meta"].get("requestLimit") or 0),
            "mismatched_observation_count": int(
                listing["meta"].get("observationsRejected") or 0
            ),
            "mismatched_trace_count": int(
                listing["meta"].get("tracesRejected") or 0
            ),
        },
    )
    rejected_count = (
        int(listing["meta"].get("observationsRejected") or 0)
        + int(listing["meta"].get("tracesRejected") or 0)
    )
    token_info = _aggregate_token_info(response_data)
    return ClaudeTraceResponse(
        status=(
            "partial"
            if listing.get("scan_truncated") or rejected_count
            else "success"
        ),
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
    request: Request,
    source: str = Query(default=DEFAULT_SOURCE, description="Trace source"),
    section: Annotated[Optional[str], Query(description="Collection section to page")] = None,
    offset: Annotated[int, Query(ge=0, description="Section item offset")] = 0,
    limit: Annotated[int, Query(ge=1, le=TRACE_REVIEW_AGGREGATE_PAGE_SIZE)] = TRACE_REVIEW_AGGREGATE_PAGE_SIZE,
    item_start: Annotated[int, Query(ge=0)] = 0,
    user: Dict[str, Any] = get_auth_dependency(),
) -> ClaudeTraceResponse:
    trace_data = _extract_langfuse_trace(request, trace_id, source)
    tree = build_trace_tree(trace_data, include_metadata_values=False)
    nodes = _flatten_trace_tree(tree)
    response_data = _aggregate_response_data(
        source=source,
        trace_id=trace_id,
        view="langfuse_tree",
        summary={
            "root_id": tree.get("id"),
            "node_count": len(nodes),
            "root_child_count": len(tree.get("children", [])),
        },
        collections={"nodes": nodes},
        filters={},
        section=section,
        offset=offset,
        limit=limit,
        next_call_base={"trace_id": trace_id},
        item_start=item_start,
    )
    token_info = _aggregate_token_info(response_data)
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
    request: Request,
    source: str = Query(default=DEFAULT_SOURCE, description="Trace source"),
    limit: int = Query(default=TRACE_REVIEW_AGGREGATE_PAGE_SIZE, ge=1, le=TRACE_REVIEW_AGGREGATE_PAGE_SIZE, description="Maximum events to return"),
    offset: Annotated[int, Query(ge=0, description="Event offset")] = 0,
    section: Annotated[Optional[str], Query(description="Collection section to page")] = None,
    item_start: Annotated[int, Query(ge=0)] = 0,
    user: Dict[str, Any] = get_auth_dependency(),
) -> ClaudeTraceResponse:
    trace_data = _extract_langfuse_trace(request, trace_id, source)
    reconstruction = build_ordered_reconstruction(
        trace_data,
        include_payload_values=False,
    )
    events = reconstruction.get("events", [])
    response_data = _aggregate_response_data(
        source=source,
        trace_id=trace_id,
        view="langfuse_reconstruction",
        summary={
            "trace": reconstruction.get("trace"),
            "event_count": reconstruction.get("event_count", len(events)),
        },
        collections={"events": events},
        filters={},
        section=section,
        offset=offset,
        limit=limit,
        next_call_base={"trace_id": trace_id},
        item_start=item_start,
    )
    token_info = _aggregate_token_info(response_data)
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
    default. Full values are never included; use langfuse_payload for exact
    chunked retrieval.
    """
)
async def get_langfuse_payloads(
    trace_id: Annotated[str, Path(description="Langfuse trace ID")],
    request: Request,
    source: str = Query(default=DEFAULT_SOURCE, description="Trace source"),
    sort: str = Query(default="largest", description="Sort order: largest or chronological"),
    limit: Annotated[
        int,
        Query(ge=1, le=TRACE_REVIEW_AGGREGATE_PAGE_SIZE, description="Maximum payload summaries"),
    ] = TRACE_REVIEW_AGGREGATE_PAGE_SIZE,
    offset: Annotated[int, Query(ge=0, description="Pagination offset")] = 0,
    section: Annotated[Optional[str], Query(description="Collection section to page")] = None,
    item_start: Annotated[int, Query(ge=0)] = 0,
    user: Dict[str, Any] = get_auth_dependency(),
) -> ClaudeTraceResponse:
    if sort not in {"largest", "chronological"}:
        raise HTTPException(status_code=400, detail="sort must be 'largest' or 'chronological'")
    trace_data = _extract_langfuse_trace(request, trace_id, source)
    payloads = build_payload_inventory(trace_data, include_values=False)
    for payload in payloads:
        preview = _bounded_summary(payload.get("preview"))
        payload["preview"] = preview
        payload["truncated_preview"] = payload.get("char_count", 0) > len(preview)
    page, _pagination = paginate_payloads(payloads, limit=len(payloads) or 1, offset=0, sort=sort)
    response_data = _aggregate_response_data(
        source=source,
        trace_id=trace_id,
        view="langfuse_payloads",
        summary={
            "payload_count": len(page),
            "total_json_chars": sum(int(item.get("json_chars") or item.get("char_count") or 0) for item in page),
            "total_bytes": sum(int(item.get("byte_count") or 0) for item in page),
        },
        collections={"payloads": page},
        filters={"sort": sort},
        section=section,
        offset=offset,
        limit=limit,
        next_call_base={"trace_id": trace_id, "sort": sort},
        item_start=item_start,
    )
    token_info = _aggregate_token_info(response_data)
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
    scope/observation_id/field. Exact chunks are environment-bounded below the
    provider inline-result envelope.
    """
)
async def get_langfuse_payload(
    trace_id: Annotated[str, Path(description="Langfuse trace ID")],
    request: Request,
    source: str = Query(default=DEFAULT_SOURCE, description="Trace source"),
    payload_id: Optional[str] = Query(default=None, description="Payload ID returned by langfuse_payloads"),
    scope: Optional[str] = Query(default=None, description="Payload scope: trace or observation"),
    observation_id: Optional[str] = Query(default=None, description="Observation/span ID"),
    field: Optional[str] = Query(default=None, description="Payload field: input, output, metadata.agent_config, or metadata.event_payload"),
    start: int = Query(default=0, ge=0, description="Start character for chunked retrieval"),
    max_chars: Annotated[
        int,
        Query(ge=1, le=TRACE_REVIEW_CHUNK_MAX_CHARS, description="Maximum exact characters"),
    ] = TRACE_REVIEW_CHUNK_MAX_CHARS,
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

    trace_data = _extract_langfuse_trace(request, trace_id, source)
    payload = find_payload(
        trace_data,
        payload_id=payload_id,
        scope=scope,
        observation_id=observation_id,
        field=field,
        start=0,
        max_chars=0,
    )
    if payload is None:
        raise HTTPException(status_code=404, detail="Payload not found in Langfuse trace data")

    serialized = payload.get("serialized", "")
    payload_metadata = {
        key: value
        for key, value in payload.items()
        if key not in {
            "value", "serialized", "preview", "truncated_preview", "sha256",
            "byte_count", "char_count", "rough_token_estimate", "start", "end",
            "returned_char_count", "total_char_count", "truncated", "next_start",
        }
    }

    def build_payload_provider_result(chunk: Dict[str, Any]) -> Mapping[str, Any]:
        provider_payload = {**payload_metadata, **chunk, "truncated": not chunk["complete"]}
        provider_data = {
            "source": source,
            "trace_id": trace_id,
            "payload": provider_payload,
        }
        return {
            "status": "success",
            "data": provider_data,
            "token_info": create_token_info_dict(provider_data),
            "error": None,
        }

    chunk = _exact_text_chunk(
        field_id=f"payload:{payload['payload_id']}",
        field=str(payload["field"]),
        value=serialized,
        start=start,
        max_chars=max_chars,
        next_call={"trace_id": trace_id, "payload_id": payload["payload_id"]},
        provider_result_builder=build_payload_provider_result,
    )
    payload = {**payload_metadata, **chunk, "truncated": not chunk["complete"]}

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
    "/{trace_id}/model_live_context",
    response_model=ClaudeTraceResponse,
    summary="Summarize model-live provider context",
    description="""
    Return bounded provider-call input size summaries. Raw prompt and payload
    values are not returned; use langfuse_payloads/langfuse_payload for explicit
    payload retrieval when needed.
    """
)
async def get_model_live_context(
    trace_id: Annotated[str, Path(description="Langfuse trace ID")],
    request: Request,
    source: str = Query(default=DEFAULT_SOURCE, description="Trace source"),
    section: Annotated[Optional[str], Query(description="Collection section to page")] = None,
    offset: Annotated[int, Query(ge=0, description="Section item offset")] = 0,
    limit: Annotated[int, Query(ge=1, le=TRACE_REVIEW_AGGREGATE_PAGE_SIZE)] = TRACE_REVIEW_AGGREGATE_PAGE_SIZE,
    item_start: Annotated[int, Query(ge=0)] = 0,
    user: Dict[str, Any] = get_auth_dependency(),
) -> ClaudeTraceResponse:
    trace_data = _extract_langfuse_trace(request, trace_id, source)
    model_live_context = _build_model_live_context(trace_data)
    calls = model_live_context.pop("calls", [])
    response_data = _aggregate_response_data(
        source=source,
        trace_id=trace_id,
        view="model_live_context",
        summary={
            **model_live_context,
            "observability_payloads": {
            "payload_inventory_available": True,
            "exact_payload_requires_explicit_lookup": True,
            "inventory_endpoint": "langfuse_payloads",
            "exact_payload_endpoint": "langfuse_payload",
        },
        },
        collections={"calls": calls},
        filters={},
        section=section,
        offset=offset,
        limit=limit,
        next_call_base={"trace_id": trace_id},
        item_start=item_start,
    )
    token_info = _aggregate_token_info(response_data)
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
    request: Request,
    source: str = Query(default=DEFAULT_SOURCE, description="Trace source"),
    section: Annotated[Optional[str], Query(description="Cost collection to page")] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=TRACE_REVIEW_AGGREGATE_PAGE_SIZE)] = TRACE_REVIEW_AGGREGATE_PAGE_SIZE,
    item_start: Annotated[int, Query(ge=0)] = 0,
    user: Dict[str, Any] = get_auth_dependency(),
) -> ClaudeTraceResponse:
    trace_data = _extract_langfuse_trace(request, trace_id, source)
    costs = build_cost_summary(trace_data)
    response_data = _aggregate_response_data(
        source=source,
        trace_id=trace_id,
        view="langfuse_costs",
        summary={"totals": costs["totals"]},
        collections={key: costs[key] for key in ("by_agent", "by_model", "by_kind", "observations")},
        filters={},
        section=section,
        offset=offset,
        limit=limit,
        next_call_base={"trace_id": trace_id},
        item_start=item_start,
    )
    token_info = _aggregate_token_info(response_data)
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
    request: Request,
    source: str = Query(default=DEFAULT_SOURCE, description="Trace source"),
    section: Annotated[Optional[str], Query(description="Duplicate collection to page")] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=TRACE_REVIEW_AGGREGATE_PAGE_SIZE)] = TRACE_REVIEW_AGGREGATE_PAGE_SIZE,
    item_start: Annotated[int, Query(ge=0)] = 0,
    user: Dict[str, Any] = get_auth_dependency(),
) -> ClaudeTraceResponse:
    trace_data = _extract_langfuse_trace(request, trace_id, source)
    duplicates = build_duplicate_report(trace_data)
    duplicate_groups = []
    duplicate_payloads = []
    for group in duplicates["duplicates"]:
        payload_refs = group.get("payloads", [])
        duplicate_groups.append({
            **{key: value for key, value in group.items() if key != "payloads"},
            "payload_count": len(payload_refs),
        })
        duplicate_payloads.extend(
            {"sha256": group["sha256"], "payload": payload}
            for payload in payload_refs
        )
    response_data = _aggregate_response_data(
        source=source,
        trace_id=trace_id,
        view="langfuse_duplicates",
        summary={
            "duplicate_group_count": duplicates["duplicate_group_count"],
            "duplicated_payload_count": duplicates["duplicated_payload_count"],
        },
        collections={
            "duplicate_groups": duplicate_groups,
            "duplicate_payloads": duplicate_payloads,
        },
        filters={},
        section=section,
        offset=offset,
        limit=limit,
        next_call_base={"trace_id": trace_id},
        item_start=item_start,
    )
    token_info = _aggregate_token_info(response_data)
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
        "trace_name": _bounded_summary(summary.get("trace_name")),
        "duration_seconds": summary.get("duration_seconds"),
        "total_cost": summary.get("total_cost"),
        "total_tokens": summary.get("total_tokens"),
        "tool_call_count": tool_calls_data.get("total_count", 0),
        "unique_tools": _unique_tools_projection(
            tool_calls_data.get("unique_tools", []),
            trace_id=trace_id,
        ),
        "has_errors": trace_summary.get("has_errors", False),
        "context_overflow_detected": trace_summary.get("context_overflow_detected", False),
        "timestamp": _bounded_summary(summary.get("timestamp")),
        "domain_envelope": _domain_envelope_projection(
            summary.get("domain_envelope"),
            trace_id=trace_id,
        ),
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
    Returns one bounded page of tool calls with summaries (no full results).
    Use this to see what tools were called before drilling into details.
    Token cost: ~100 tokens per call.
    """
)
async def get_tool_calls_summary(
    trace_id: Annotated[str, Path(description="Langfuse trace ID")],
    request: Request,
    page: Annotated[int, Query(ge=1, description="Page number (1-indexed)")] = 1,
    page_size: Annotated[
        int,
        Query(ge=1, le=TRACE_REVIEW_PAGE_SIZE, description="Summaries per page"),
    ] = TRACE_REVIEW_PAGE_SIZE,
    item_offset: Annotated[
        int,
        Query(ge=0, le=TRACE_REVIEW_PAGE_SIZE, description="Continuation offset within the requested page"),
    ] = 0,
    source: str = Query(default=DEFAULT_SOURCE, description="Trace source"),
    user: Dict[str, Any] = get_auth_dependency()
) -> ToolCallsSummaryResponse:
    """Get one bounded page of lightweight tool-call summaries."""
    cached_data = await _ensure_trace_analyzed(trace_id, request, source)

    analysis = cached_data.get("analysis", {})
    tool_calls_data = analysis.get("tool_calls", {})

    # Create lightweight summaries
    tool_calls = tool_calls_data.get("tool_calls", [])
    total_items = len(tool_calls)
    total_pages = max(1, math.ceil(total_items / page_size))
    if page > total_pages and total_items > 0:
        raise HTTPException(
            status_code=400,
            detail=f"Page {page} exceeds total pages ({total_pages})",
        )
    if item_offset >= page_size:
        raise HTTPException(status_code=400, detail="item_offset must be smaller than page_size")
    page_start_idx = (page - 1) * page_size
    start_idx = page_start_idx + item_offset
    page_end_idx = min(page_start_idx + page_size, total_items)
    candidate_summaries = []
    for i, tc in enumerate(tool_calls[start_idx:page_end_idx], start=start_idx):
        lightweight = create_lightweight_tool_call_summary(tc)
        exact_result = _tool_call_exact_value(tc, "tool_result")
        if (
            lightweight["result_summary"] == "N/A"
            and tc.get("tool_result") is None
            and "call_id" not in tc
            and exact_result is not None
        ):
            lightweight["result_summary"] = _bounded_summary(exact_result)
        selector = _tool_call_selector(tc, i)
        candidate_summaries.append(ToolCallSummaryItem(
            index=i,
            call_id=selector,
            name=_bounded_summary(lightweight["name"]),
            time=_bounded_summary(lightweight["time"]),
            duration=_bounded_summary(lightweight["duration"]),
            status=_bounded_summary(lightweight["status"]),
            input_summary=_bounded_summary(lightweight["input_summary"]),
            result_summary=_bounded_summary(lightweight["result_summary"]),
            domain_envelope=_domain_envelope_projection(
                lightweight.get("domain_envelope"),
                trace_id=trace_id,
                call_id=selector,
            ),
        ))

    duplicates = tool_calls_data.get("duplicates", {})

    def build_response_data(items: List[ToolCallSummaryItem]) -> ToolCallsSummaryData:
        next_index = start_idx + len(items)
        if next_index >= total_items:
            next_call = None
        elif next_index < page_end_idx:
            next_call = {
                "trace_id": trace_id,
                "page": page,
                "page_size": page_size,
                "item_offset": item_offset + len(items),
            }
        else:
            next_call = {
                "trace_id": trace_id,
                "page": page + 1,
                "page_size": page_size,
            }
        return ToolCallsSummaryData(
            total_count=tool_calls_data.get("total_count", 0),
            unique_tools=_unique_tools_projection(
                tool_calls_data.get("unique_tools", []),
                trace_id=trace_id,
            ),
            tool_calls=items,
            pagination=PaginationInfo(
                page=page,
                page_size=page_size,
                total_items=total_items,
                total_pages=total_pages,
                has_next=next_call is not None,
                has_prev=start_idx > 0,
            ),
            next_call=next_call,
            has_duplicates=duplicates.get("has_duplicates", False),
            duplicate_count=duplicates.get("total_duplicate_groups", 0),
        )

    summaries: List[ToolCallSummaryItem] = []
    for summary in candidate_summaries:
        candidate_data = build_response_data([*summaries, summary])
        candidate_payload = candidate_data.model_dump()
        candidate_token_info = _complete_provider_token_info(
            candidate_payload,
            lambda info: {
                "status": "success",
                "data": candidate_payload,
                "token_info": dict(info),
                "error": None,
            },
        )
        if not candidate_token_info["within_budget"]:
            break
        summaries.append(summary)
    if candidate_summaries and not summaries:
        raise HTTPException(
            status_code=400,
            detail="Provider boundary is too small for one bounded tool-call summary",
        )

    response_data = build_response_data(summaries)
    response_payload = response_data.model_dump()
    token_info = _complete_provider_token_info(
        response_payload,
        lambda info: {
            "status": "success",
            "data": response_payload,
            "token_info": dict(info),
            "error": None,
        },
    )

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
    summary="Get paginated tool-call exact-field references",
    description="""
    Returns paginated tool-call metadata and exact-field references. Use the
    single-call detail endpoint to retrieve independently chunked input/result
    fields. Supports filtering by tool name.

    Query parameters:
    - page: Page number (1-indexed, default: 1)
    - page_size: Items per page (environment-bounded)
    - tool_name: Optional filter by tool name

    Exact input/result values are never embedded in this page.
    """
)
async def get_tool_calls_paginated(
    trace_id: Annotated[str, Path(description="Langfuse trace ID")],
    request: Request,
    page: int = Query(default=1, ge=1, description="Page number (1-indexed)"),
    page_size: Annotated[
        int,
        Query(ge=1, le=TRACE_REVIEW_PAGE_SIZE, description="Items per page"),
    ] = TRACE_REVIEW_PAGE_SIZE,
    item_offset: Annotated[
        int,
        Query(ge=0, le=TRACE_REVIEW_PAGE_SIZE, description="Continuation offset within the requested page"),
    ] = 0,
    tool_name: Optional[str] = Query(default=None, max_length=TRACE_REVIEW_SUMMARY_MAX_CHARS, description="Filter by tool name"),
    source: str = Query(default=DEFAULT_SOURCE, description="Trace source"),
    user: Dict[str, Any] = get_auth_dependency()
) -> PaginatedToolCallsResponse:
    """Get paginated tool-call metadata and exact-field references."""
    cached_data = await _ensure_trace_analyzed(trace_id, request, source)

    analysis = cached_data.get("analysis", {})
    tool_calls_data = analysis.get("tool_calls", {})
    all_tool_calls = tool_calls_data.get("tool_calls", [])
    indexed_tool_calls = list(enumerate(all_tool_calls))

    # Apply tool_name filter if provided
    if tool_name:
        if len(tool_name) > TRACE_REVIEW_SUMMARY_MAX_CHARS:
            raise HTTPException(
                status_code=400,
                detail=(
                    "tool_name exceeds the configured TraceReview summary "
                    f"limit of {TRACE_REVIEW_SUMMARY_MAX_CHARS} characters"
                ),
            )
        indexed_tool_calls = [
            (index, tool_call)
            for index, tool_call in indexed_tool_calls
            if tool_call.get("name") == tool_name
        ]

    # Calculate pagination
    total_items = len(indexed_tool_calls)
    total_pages = max(1, math.ceil(total_items / page_size))

    # Validate page number
    if page > total_pages and total_items > 0:
        raise HTTPException(
            status_code=400,
            detail=f"Page {page} exceeds total pages ({total_pages})"
        )

    if item_offset >= page_size:
        raise HTTPException(status_code=400, detail="item_offset must be smaller than page_size")

    # Get the logical page slice and fit as many bounded records as the full
    # provider wrapper can carry. item_offset resumes within this same slice.
    page_start_idx = (page - 1) * page_size
    start_idx = page_start_idx + item_offset
    end_idx = min(page_start_idx + page_size, total_items)
    candidate_tool_calls = []
    for index, tool_call in indexed_tool_calls[start_idx:end_idx]:
        selector = _tool_call_selector(tool_call, index)
        exact_fields = []
        fields = ["input", "tool_result"]
        if tool_call.get("thought") not in {None, "", "N/A"}:
            fields.append("thought")
        if _tool_call_metadata_requires_exact(tool_call):
            fields.append("metadata")
        if isinstance(tool_call.get("domain_envelope"), Mapping):
            fields.append("domain_envelope")
        for field in fields:
            field_id = f"tool_call:{selector}:{field}"
            exact_fields.append({
                **_exact_text_metadata(
                    field_id=field_id,
                    field=field,
                    value=_tool_call_exact_value(tool_call, field),
                ),
                "next_call": {
                    "trace_id": trace_id,
                    "call_id": selector,
                    "field": field,
                    "start": 0,
                    "max_chars": TRACE_REVIEW_CHUNK_MAX_CHARS,
                },
            })
        candidate_tool_calls.append({
            **_tool_call_metadata_projection(
                tool_call,
                index=index,
                trace_id=trace_id,
            ),
            "exact_fields": exact_fields,
        })

    def build_page_result(items: List[Dict[str, Any]]) -> Dict[str, Any]:
        next_index = start_idx + len(items)
        if next_index >= total_items:
            next_call = None
        elif next_index < end_idx:
            next_call = {
                "trace_id": trace_id,
                "page": page,
                "page_size": page_size,
                "item_offset": item_offset + len(items),
                **({"tool_name": tool_name} if tool_name else {}),
            }
        else:
            next_call = {
                "trace_id": trace_id,
                "page": page + 1,
                "page_size": page_size,
                **({"tool_name": tool_name} if tool_name else {}),
            }
        return {
            "status": "success",
            "tool_calls": items,
            "pagination": PaginationInfo(
                page=page,
                page_size=page_size,
                total_items=total_items,
                total_pages=total_pages,
                has_next=next_call is not None,
                has_prev=start_idx > 0,
            ).model_dump(),
            "next_call": next_call,
            "filter_applied": tool_name,
        }

    page_tool_calls: List[Dict[str, Any]] = []
    for tool_call in candidate_tool_calls:
        candidate_result = build_page_result([*page_tool_calls, tool_call])
        candidate_token_info = _complete_provider_token_info(
            candidate_result,
            lambda info: {**candidate_result, "token_info": dict(info), "error": None},
        )
        if not candidate_token_info["within_budget"]:
            break
        page_tool_calls.append(tool_call)
    if candidate_tool_calls and not page_tool_calls:
        raise HTTPException(
            status_code=400,
            detail="Provider boundary is too small for one bounded tool-call record",
        )

    page_result = build_page_result(page_tool_calls)
    token_info = _complete_provider_token_info(
        page_result,
        lambda info: {**page_result, "token_info": dict(info), "error": None},
    )
    pagination = PaginationInfo(**page_result["pagination"])

    return PaginatedToolCallsResponse(
        status="success",
        tool_calls=page_tool_calls,
        pagination=pagination,
        next_call=page_result["next_call"],
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
    Returns one exact, independently selected input or result field chunk for a
    single tool call. Follow next_call until complete=true.

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
    field: Annotated[
        Literal["input", "tool_result", "thought", "metadata", "domain_envelope"],
        Query(description="Exact field to retrieve"),
    ],
    start: Annotated[int, Query(ge=0, description="Start character")] = 0,
    max_chars: Annotated[
        int,
        Query(ge=1, le=TRACE_REVIEW_CHUNK_MAX_CHARS, description="Maximum exact characters"),
    ] = TRACE_REVIEW_CHUNK_MAX_CHARS,
    source: str = Query(default=DEFAULT_SOURCE, description="Trace source"),
    user: Dict[str, Any] = get_auth_dependency()
) -> SingleToolCallResponse:
    """Get one exact field chunk for a single tool call."""
    cached_data = await _ensure_trace_analyzed(trace_id, request, source)

    analysis = cached_data.get("analysis", {})
    tool_calls_data = analysis.get("tool_calls", {})
    all_tool_calls = tool_calls_data.get("tool_calls", [])

    # Find the tool call by call_id or observation id
    # Support both because paginated response includes both fields
    tool_call = None
    tool_call_index = -1
    for index, tc in enumerate(all_tool_calls):
        if _tool_call_selector(tc, index) == call_id:
            tool_call = tc
            tool_call_index = index
            break

    if not tool_call:
        raise HTTPException(
            status_code=404,
            detail=f"Tool call with call_id or id '{call_id}' not found. "
                   f"Use call_id from tool_calls/summary or id from paginated tool_calls."
        )

    selector = _tool_call_selector(tool_call, tool_call_index)
    tool_call_metadata = _tool_call_metadata_projection(
        tool_call,
        index=tool_call_index,
        trace_id=trace_id,
    )

    def build_tool_call_provider_result(chunk: Dict[str, Any]) -> Mapping[str, Any]:
        token_info = create_token_info_dict({"tool_call": tool_call_metadata, "chunk": chunk})
        return {
            "status": "success",
            "tool_call": tool_call_metadata,
            "chunk": chunk,
            "token_info": token_info,
            "error": None,
        }

    chunk = _exact_text_chunk(
        field_id=f"tool_call:{selector}:{field}",
        field=field,
        value=_tool_call_exact_value(tool_call, field),
        start=start,
        max_chars=max_chars,
        next_call={"trace_id": trace_id, "call_id": selector, "field": field},
        provider_result_builder=build_tool_call_provider_result,
    )

    token_info = create_token_info_dict({"tool_call": tool_call_metadata, "chunk": chunk})

    return SingleToolCallResponse(
        status="success",
        tool_call=tool_call_metadata,
        chunk=chunk,
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
    Returns one exact user-query or assistant-response chunk. Follow next_call
    until complete=true.
    """
)
async def get_trace_conversation(
    trace_id: Annotated[str, Path(description="Langfuse trace ID")],
    request: Request,
    field: Annotated[
        Literal["user_query", "assistant_response"],
        Query(description="Exact conversation field to retrieve"),
    ],
    start: Annotated[int, Query(ge=0, description="Start character")] = 0,
    max_chars: Annotated[
        int,
        Query(ge=1, le=TRACE_REVIEW_CHUNK_MAX_CHARS, description="Maximum exact characters"),
    ] = TRACE_REVIEW_CHUNK_MAX_CHARS,
    source: str = Query(default=DEFAULT_SOURCE, description="Trace source"),
    user: Dict[str, Any] = get_auth_dependency()
) -> ConversationResponse:
    """Get user query and assistant response."""
    cached_data = await _ensure_trace_analyzed(trace_id, request, source)

    analysis = cached_data.get("analysis", {})
    conversation = analysis.get("conversation", {})

    analyzer_field = "user_input" if field == "user_query" else "assistant_response"
    domain_envelope = conversation.get("domain_envelope")

    def build_conversation_provider_result(chunk: Dict[str, Any]) -> Mapping[str, Any]:
        provider_data = {
            "field": field,
            "chunk": chunk,
            "domain_envelope": domain_envelope,
        }
        return {
            "status": "success",
            "data": provider_data,
            "token_info": create_token_info_dict(provider_data),
            "error": None,
        }

    chunk = _exact_text_chunk(
        field_id=f"conversation:{field}",
        field=field,
        value=conversation.get(analyzer_field),
        start=start,
        max_chars=max_chars,
        next_call={"trace_id": trace_id, "field": field},
        provider_result_builder=build_conversation_provider_result,
    )
    response_data = ConversationData(
        field=field,
        chunk=chunk,
        domain_envelope=domain_envelope,
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
    section: Annotated[Optional[str], Query(description="Timeline collection to page")] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=TRACE_REVIEW_AGGREGATE_PAGE_SIZE)] = TRACE_REVIEW_AGGREGATE_PAGE_SIZE,
    item_start: Annotated[int, Query(ge=0)] = 0,
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
            caller_sub=_trusted_caller_sub(user),
        ),
        load_sibling_cached_data=lambda sibling_trace_id: _ensure_trace_analyzed(
            sibling_trace_id,
            request,
            source,
            refresh=refresh,
        ),
        fallback_exceptions=(HTTPException,),
        caller_sub=_trusted_caller_sub(user),
        caller_email=str(user.get("trusted_caller_email") or user.get("email") or "").strip() or None,
        authorized_feedback_artifacts=getattr(
            getattr(request, "state", None), "authorized_feedback_artifacts", None
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
    reasoning = timeline.get("reasoning_summary", {})
    summary = {
        key: value
        for key, value in timeline.items()
        if key not in {"timeline", "sibling_trace_ids", "reasoning_summary", "query"}
    }
    summary["reasoning_summary_status"] = reasoning.get("status")
    response_data = _aggregate_response_data(
        source=source,
        trace_id=trace_id,
        view="extraction_timeline",
        summary=summary,
        collections={
            "timeline": timeline.get("timeline", []),
            "sibling_trace_ids": timeline.get("sibling_trace_ids", []),
            "reasoning_request_settings": reasoning.get("request_settings", []),
            "reasoning_summaries": reasoning.get("summaries", []),
        },
        filters=timeline.get("query", {}),
        section=section,
        offset=offset,
        limit=limit,
        next_call_base={
            "trace_id": trace_id,
            "session_id": session_id,
            "feedback_id": feedback_id,
            "include_sibling_traces": include_sibling_traces,
            "refresh": refresh,
            "include_raw_args": include_raw_args,
            "include_raw_outputs": include_raw_outputs,
            "tool_name": tool_name,
            "event_type": event_type,
            "candidate_id": candidate_id,
        },
        item_start=item_start,
    )
    token_info = _aggregate_token_info(response_data)
    return ClaudeTraceResponse(
        status="success",
        data=response_data,
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
    section: Annotated[Optional[str], Query(description="Report collection to page")] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=TRACE_REVIEW_AGGREGATE_PAGE_SIZE)] = TRACE_REVIEW_AGGREGATE_PAGE_SIZE,
    item_start: Annotated[int, Query(ge=0)] = 0,
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
            caller_sub=_trusted_caller_sub(user),
        ),
        load_sibling_cached_data=lambda sibling_trace_id: _ensure_trace_analyzed(
            sibling_trace_id,
            request,
            source,
            refresh=refresh,
        ),
        fallback_exceptions=(HTTPException,),
        caller_sub=_trusted_caller_sub(user),
        caller_email=str(user.get("trusted_caller_email") or user.get("email") or "").strip() or None,
        authorized_feedback_artifacts=getattr(
            getattr(request, "state", None), "authorized_feedback_artifacts", None
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
    report = ExtractionTimelineAnalyzer.diagnostic_report(timeline)
    reasoning = report.get("reasoning_summary", {})
    response_data = _aggregate_response_data(
        source=source,
        trace_id=trace_id,
        view="diagnostic_report",
        summary={
            **report.get("summary", {}),
            "schema_version": report.get("schema_version"),
            "size_summary": report.get("size_summary", {}),
            "reasoning_summary_status": reasoning.get("status"),
        },
        collections={
            "validation_failures": report.get("validation_failures", []),
            "timeline": report.get("timeline", []),
            "reasoning_request_settings": reasoning.get("request_settings", []),
            "reasoning_summaries": reasoning.get("summaries", []),
        },
        filters=timeline.get("query", {}),
        section=section,
        offset=offset,
        limit=limit,
        next_call_base={
            "trace_id": trace_id,
            "session_id": session_id,
            "feedback_id": feedback_id,
            "include_sibling_traces": include_sibling_traces,
            "refresh": refresh,
            "include_raw_args": include_raw_args,
            "include_raw_outputs": include_raw_outputs,
            "tool_name": tool_name,
            "event_type": event_type,
            "candidate_id": candidate_id,
        },
        item_start=item_start,
    )
    token_info = _aggregate_token_info(response_data)
    return ClaudeTraceResponse(
        status="success",
        data=response_data,
        token_info=TokenInfo(**token_info),
    )


@router.get(
    "/{trace_id}/evidence_revisions",
    response_model=ClaudeTraceResponse,
    summary="Get evidence revision diagnostics",
    description="""
    Returns an opt-in diagnostics-only summary of hidden evidence revision
    history and backend-refused evidence scope mutations. Live evidence fields
    remain authoritative for product behavior.
    """
)
async def get_evidence_revisions(
    trace_id: Annotated[str, Path(description="Langfuse trace ID")],
    request: Request,
    source: str = Query(default=DEFAULT_SOURCE, description="Trace source: local, remote, or auto"),
    session_id: Optional[str] = Query(default=None, description="Langfuse session ID for sibling-trace expansion"),
    feedback_id: Optional[str] = Query(default=None, description="Feedback ID linked to stored trace artifacts"),
    include_sibling_traces: bool = Query(default=False, description="Include durable events from traces in the same session"),
    refresh: bool = Query(default=False, description="Refresh cached trace analysis before rendering"),
    tool_name: Optional[str] = Query(default=None, description="Filter by tool name"),
    event_type: Optional[str] = Query(default=None, description="Filter by event type"),
    candidate_id: Optional[str] = Query(default=None, description="Filter by candidate ID"),
    section: Annotated[Optional[str], Query(description="Evidence collection to page")] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=TRACE_REVIEW_AGGREGATE_PAGE_SIZE)] = TRACE_REVIEW_AGGREGATE_PAGE_SIZE,
    item_start: Annotated[int, Query(ge=0)] = 0,
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
            caller_sub=_trusted_caller_sub(user),
        ),
        load_sibling_cached_data=lambda sibling_trace_id: _ensure_trace_analyzed(
            sibling_trace_id,
            request,
            source,
            refresh=refresh,
        ),
        fallback_exceptions=(HTTPException,),
        caller_sub=_trusted_caller_sub(user),
        caller_email=str(user.get("trusted_caller_email") or user.get("email") or "").strip() or None,
        authorized_feedback_artifacts=getattr(
            getattr(request, "state", None), "authorized_feedback_artifacts", None
        ),
    )
    evidence_revisions = build_evidence_revisions(
        trace_id=trace_id,
        context=context,
        tool_name=tool_name,
        event_type=event_type,
        candidate_id=candidate_id,
        session_id=session_id,
        feedback_id=feedback_id,
    )
    response_data = _aggregate_response_data(
        source=source,
        trace_id=trace_id,
        view="evidence_revisions",
        summary={
            **evidence_revisions.get("summary", {}),
            "schema_version": evidence_revisions.get("schema_version"),
        },
        collections={
            "evidence_records": evidence_revisions.get("evidence_records", []),
            "scope_refusals": evidence_revisions.get("scope_refusals", []),
        },
        filters=evidence_revisions.get("query", {}),
        section=section,
        offset=offset,
        limit=limit,
        next_call_base={
            "trace_id": trace_id,
            "session_id": session_id,
            "feedback_id": feedback_id,
            "include_sibling_traces": include_sibling_traces,
            "refresh": refresh,
            "tool_name": tool_name,
            "event_type": event_type,
            "candidate_id": candidate_id,
        },
        item_start=item_start,
    )
    token_info = _aggregate_token_info(response_data)
    return ClaudeTraceResponse(
        status="success",
        data=response_data,
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
    document_hierarchy, agent_configs, group_context, trace_summary,
    tool_calls, domain_envelope, extraction_timeline, evidence_revisions

    Token cost: varies by view (check token_info in response).
    """
)
async def get_trace_view(
    trace_id: Annotated[str, Path(description="Langfuse trace ID")],
    view_name: Annotated[str, Path(description="View name")],
    request: Request,
    source: str = Query(default=DEFAULT_SOURCE, description="Trace source"),
    section: Annotated[Optional[str], Query(description="View section to page")] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=TRACE_REVIEW_AGGREGATE_PAGE_SIZE)] = TRACE_REVIEW_AGGREGATE_PAGE_SIZE,
    item_start: Annotated[int, Query(ge=0)] = 0,
    user: Dict[str, Any] = get_auth_dependency()
) -> ClaudeTraceResponse:
    """Get specific trace view with token metadata."""
    valid_views = [
        "token_analysis", "agent_context", "pdf_citations", "tool_calls",
        "document_hierarchy", "agent_configs", "group_context", "trace_summary",
        "domain_envelope", "extraction_timeline", "evidence_revisions",
    ]

    if view_name not in valid_views:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid view '{view_name}'. Valid views: {', '.join(valid_views)}"
        )

    if view_name == "evidence_revisions":
        context = await load_extraction_timeline_context(
            trace_id=trace_id,
            feedback_id=None,
            include_sibling_traces=False,
            load_cached_data=lambda: _ensure_trace_analyzed(trace_id, request, source),
            load_sibling_trace_ids=lambda: [],
            load_sibling_cached_data=lambda sibling_trace_id: _ensure_trace_analyzed(
                sibling_trace_id,
                request,
                source,
            ),
            fallback_exceptions=(HTTPException,),
        )
        view_data = build_evidence_revisions(
            trace_id=trace_id,
            context=context,
            tool_name=None,
            event_type=None,
            candidate_id=None,
        )
        response_data = _aggregate_response_data(
            source=source,
            trace_id=trace_id,
            view=view_name,
            summary={
                **view_data.get("summary", {}),
                "schema_version": view_data.get("schema_version"),
            },
            collections={
                "evidence_records": view_data.get("evidence_records", []),
                "scope_refusals": view_data.get("scope_refusals", []),
            },
            filters=view_data.get("query", {}),
            section=section,
            offset=offset,
            limit=limit,
            next_call_base={"trace_id": trace_id, "view_name": view_name},
            item_start=item_start,
        )
        token_info = _aggregate_token_info(response_data)
        return ClaudeTraceResponse(
            status="success",
            data=response_data,
            token_info=TokenInfo(**token_info),
        )

    cached_data = await _ensure_trace_analyzed(trace_id, request, source)

    analysis = cached_data.get("analysis", {})
    view_data = analysis.get(view_name)

    if view_data is None:
        raise HTTPException(
            status_code=404,
            detail=f"View '{view_name}' not found for trace {trace_id}"
        )

    view_mapping = view_data if isinstance(view_data, Mapping) else {"value": view_data}
    response_data = _aggregate_response_data(
        source=source,
        trace_id=trace_id,
        view=view_name,
        summary={
            "section_count": len(view_mapping),
            "serialized_json_chars": len(json.dumps(view_data, default=str)),
        },
        collections=view_mapping,
        filters={},
        section=section,
        offset=offset,
        limit=limit,
        next_call_base={"trace_id": trace_id, "view_name": view_name},
        item_start=item_start,
    )
    token_info = _aggregate_token_info(response_data)

    return ClaudeTraceResponse(
        status="success",
        data=response_data,
        token_info=TokenInfo(**token_info)
    )
