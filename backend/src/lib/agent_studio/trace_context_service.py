"""
Trace Context Service.

Extracts and enriches trace data from Langfuse for display in the Prompt Explorer.
Provides a summary of what happened during a chat interaction, including which
prompts fired, tool calls, and routing decisions.
"""

import logging
import os
import re
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Optional, List, Any
from urllib.parse import quote

import httpx

from src.lib.agent_studio.trace_agent_metadata import (
    get_trace_agent_patterns,
    normalize_trace_agent_id,
    trace_agent_display_name,
)
from src.lib.openai_agents.config import get_trace_review_export_timeout_seconds
from src.lib.upstream_error_diagnostics import looks_like_header_or_html_response

from .models import (
    TraceContext,
    PromptExecution,
    RoutingDecision,
    ToolCallInfo,
)

logger = logging.getLogger(__name__)


class TraceContextError(Exception):
    """Base exception for trace context operations."""
    pass


class TraceNotFoundError(TraceContextError):
    """Raised when a trace is not found in Langfuse."""
    pass


class LangfuseUnavailableError(TraceContextError):
    """Raised when Langfuse client cannot be initialized."""
    pass


class TraceReviewExportError(TraceContextError):
    """Raised when TraceReview export cannot provide trace context."""
    pass


_TRACE_ID_PATH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
TRACE_CONTEXT_SOURCE_ENV = "TRACE_CONTEXT_SOURCE"
TRACE_CONTEXT_SOURCE_LANGFUSE_SDK = "langfuse_sdk"
TRACE_CONTEXT_SOURCE_TRACE_REVIEW_EXPORT = "trace_review_export"
_TRACE_REVIEW_SOURCE_ENV = "TRACE_REVIEW_SOURCE"
_TRACE_REVIEW_INTERNAL_API_TOKEN_ENV = "TRACE_REVIEW_INTERNAL_API_TOKEN"
# Env-configurable via TRACE_REVIEW_EXPORT_TIMEOUT_SECONDS (default 30); see config.py.
_TRACE_REVIEW_TIMEOUT_SECONDS = get_trace_review_export_timeout_seconds()


async def get_trace_context_for_explorer(trace_id: str) -> TraceContext:
    """
    Get enriched trace context for display in Prompt Explorer.

    Fetches trace data from Langfuse and transforms it into a format
    suitable for the Prompt Explorer UI.

    Args:
        trace_id: The Langfuse trace ID

    Returns:
        TraceContext with summarized execution details

    Raises:
        LangfuseUnavailableError: If Langfuse client cannot be initialized
        TraceNotFoundError: If the trace is not found
        TraceContextError: For other extraction failures
    """
    source = _configured_trace_context_source()
    if source == TRACE_CONTEXT_SOURCE_LANGFUSE_SDK:
        return await _get_trace_context_from_langfuse_sdk(trace_id)

    if source == TRACE_CONTEXT_SOURCE_TRACE_REVIEW_EXPORT:
        return await _get_trace_context_from_trace_review_export(trace_id)

    allowed_sources = ", ".join(
        (
            TRACE_CONTEXT_SOURCE_LANGFUSE_SDK,
            TRACE_CONTEXT_SOURCE_TRACE_REVIEW_EXPORT,
        )
    )
    raise TraceContextError(
        f"Unsupported {TRACE_CONTEXT_SOURCE_ENV}={source!r}; expected one of: "
        f"{allowed_sources}"
    )


async def _get_trace_context_from_langfuse_sdk(trace_id: str) -> TraceContext:
    try:
        from langfuse import Langfuse
    except ImportError as e:
        logger.error("Langfuse package not installed")
        raise LangfuseUnavailableError("Langfuse package not installed") from e

    host = os.getenv("LANGFUSE_HOST")
    public_key = os.getenv("LANGFUSE_PUBLIC_KEY")
    secret_key = os.getenv("LANGFUSE_SECRET_KEY")
    try:
        if host and public_key and secret_key:
            client = Langfuse(
                host=host,
                public_key=public_key,
                secret_key=secret_key,
            )
        else:
            client = Langfuse()
    except Exception as e:
        logger.error(
            "Failed to initialize Langfuse client: %s",
            _safe_exception_message(e),
            exc_info=True,
        )
        raise LangfuseUnavailableError(
            f"Failed to initialize Langfuse client: {_safe_exception_message(e)}"
        ) from e

    try:
        # Fetch the trace using the API client
        trace = client.api.trace.get(trace_id)
        if not trace:
            logger.warning('Trace not found: %s', trace_id)
            raise TraceNotFoundError(f"Trace not found: {trace_id}")

        # Fetch observations separately using the API
        obs_response = client.api.observations.get_many(trace_id=trace_id)
        observations = []
        if hasattr(obs_response, 'data'):
            observations = list(obs_response.data)

        # Parse the trace data
        prompts_executed = _extract_prompts_executed(observations)
        routing_decisions = _extract_routing_decisions(observations)
        tool_calls = _extract_tool_calls(observations)

        # Get user query and response
        user_query = _extract_user_query(trace, observations)
        final_response = _extract_final_response(trace, observations)

        # Calculate metrics (Langfuse SDK v3 uses 'total' not 'total_tokens')
        total_tokens = sum(
            (obs.usage.total if obs.usage else 0)
            for obs in observations
            if hasattr(obs, 'usage') and obs.usage
        )

        return TraceContext(
            trace_id=trace_id,
            session_id=trace.session_id,
            timestamp=trace.timestamp or datetime.now(timezone.utc),
            user_query=user_query,
            final_response_preview=final_response[:500] if final_response else "",
            prompts_executed=prompts_executed,
            routing_decisions=routing_decisions,
            tool_calls=tool_calls,
            total_duration_ms=_calculate_duration_ms(trace),
            total_tokens=total_tokens,
            agent_count=len(set(p.agent_id for p in prompts_executed)),
        )

    except (TraceNotFoundError, LangfuseUnavailableError):
        # Re-raise our custom exceptions
        raise
    except Exception as e:
        logger.error(
            "Failed to get trace context through Langfuse SDK: %s",
            _safe_exception_message(e),
            exc_info=True,
        )
        raise TraceContextError(
            "Failed to extract trace context via Langfuse SDK: "
            f"{_safe_exception_message(e)}"
        ) from e


async def _get_trace_context_from_trace_review_export(trace_id: str) -> TraceContext:
    _validate_trace_id_for_export_path(trace_id)

    base_url = os.getenv("TRACE_REVIEW_URL")
    if not base_url:
        raise TraceReviewExportError("TRACE_REVIEW_URL is not configured")

    source = os.getenv(_TRACE_REVIEW_SOURCE_ENV, "remote")
    encoded_trace_id = quote(trace_id, safe="")
    url = f"{base_url.rstrip('/')}/api/traces/{encoded_trace_id}/export"

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(_TRACE_REVIEW_TIMEOUT_SECONDS)
        ) as client:
            request_kwargs: dict[str, Any] = {"params": {"source": source}}
            headers = _trace_review_export_headers()
            if headers:
                request_kwargs["headers"] = headers
            response = await client.get(url, **request_kwargs)
    except httpx.TimeoutException as e:
        raise TraceReviewExportError(
            f"TraceReview export timed out after {_TRACE_REVIEW_TIMEOUT_SECONDS:g}s "
            f"(source={source})"
        ) from e
    except httpx.HTTPError as e:
        raise TraceReviewExportError(
            f"TraceReview export request failed: {_safe_exception_message(e)}"
        ) from e

    if response.status_code == 404:
        raise TraceNotFoundError(
            f"Trace {trace_id} not found via TraceReview export (source={source})"
        )

    if response.status_code != 200:
        detail = _response_error_detail(response)
        raise TraceReviewExportError(
            f"TraceReview export failed with HTTP {response.status_code} "
            f"(source={source}): {detail}"
        )

    try:
        export_payload = response.json()
    except ValueError as e:
        raise TraceReviewExportError(
            f"TraceReview export returned non-JSON response "
            f"(HTTP {response.status_code}, source={source})"
        ) from e

    return _trace_context_from_trace_review_export(trace_id, export_payload)


def _configured_trace_context_source() -> str:
    return os.getenv(
        TRACE_CONTEXT_SOURCE_ENV,
        TRACE_CONTEXT_SOURCE_LANGFUSE_SDK,
    ).strip()


def _validate_trace_id_for_export_path(trace_id: str) -> None:
    if _TRACE_ID_PATH_RE.fullmatch(trace_id):
        return
    raise TraceContextError(
        "Invalid trace ID for TraceReview export path; expected a non-empty "
        "Langfuse trace identifier containing only letters, digits, '_' or '-'"
    )


def _trace_review_export_headers() -> dict[str, str]:
    token = os.getenv(_TRACE_REVIEW_INTERNAL_API_TOKEN_ENV, "").strip()
    if not token:
        return {}
    return {"Authorization": f"Bearer {token}"}


def _trace_context_from_trace_review_export(
    trace_id: str,
    export_payload: dict[str, Any],
) -> TraceContext:
    raw_trace = _required_mapping(export_payload, "raw_trace")
    analysis = _required_mapping(export_payload, "analysis")
    summary = _required_mapping(analysis, "summary")
    conversation = _required_mapping(analysis, "conversation")
    tool_call_summary = _required_mapping(analysis, "tool_calls")
    raw_tool_calls = _required_list(tool_call_summary, "tool_calls")
    observations = []
    for item in _required_list(export_payload, "observations"):
        if not isinstance(item, dict):
            raise TraceReviewExportError("TraceReview observations contains a non-object item")
        observations.append(_observation_from_export(item))

    prompts_executed = _extract_prompts_executed(observations)
    routing_decisions = _extract_routing_decisions(observations)
    tool_calls = _tool_calls_from_trace_review_analysis(raw_tool_calls)

    user_query = _required_string(conversation, "user_input")
    final_response = _required_string(conversation, "assistant_response")
    timestamp = _required_datetime(summary, "timestamp")

    total_duration_ms = None
    total_duration_seconds = _number_to_float(summary.get("duration_seconds"))
    if total_duration_seconds is not None:
        total_duration_ms = int(total_duration_seconds * 1000)

    total_tokens = _number_to_int(summary.get("total_tokens"))

    agent_count = len({prompt.agent_id for prompt in prompts_executed})

    return TraceContext(
        trace_id=trace_id,
        session_id=_string_or_none(raw_trace.get("sessionId")),
        timestamp=timestamp,
        user_query=user_query,
        final_response_preview=final_response[:500] if final_response else "",
        prompts_executed=prompts_executed,
        routing_decisions=routing_decisions,
        tool_calls=tool_calls,
        total_duration_ms=total_duration_ms,
        total_tokens=total_tokens,
        agent_count=agent_count,
    )

def _observation_from_export(item: dict[str, Any]) -> SimpleNamespace:
    usage = _usage_from_export(item)
    return SimpleNamespace(
        type=item.get("type"),
        name=item.get("name") or "",
        input=item.get("input"),
        output=item.get("output"),
        model=item.get("model"),
        usage=usage,
        metadata=item.get("metadata") if isinstance(item.get("metadata"), dict) else None,
        start_time=_parse_datetime(item.get("startTime")),
        end_time=_parse_datetime(item.get("endTime")),
    )


def _usage_from_export(item: dict[str, Any]) -> SimpleNamespace | None:
    raw_usage = item.get("usage")
    if not isinstance(raw_usage, dict):
        return None

    return SimpleNamespace(total=_number_to_int(raw_usage.get("total")))


def _tool_calls_from_trace_review_analysis(
    raw_tool_calls: list[Any],
) -> List[ToolCallInfo]:
    tool_calls = []
    for item in raw_tool_calls:
        if not isinstance(item, dict):
            raise TraceReviewExportError("TraceReview analysis.tool_calls contains a non-object item")
        name = _required_string(item, "name")
        tool_calls.append(
            ToolCallInfo(
                name=name,
                input=item.get("input") if isinstance(item.get("input"), dict) else {},
                output_preview=None,
                duration_ms=_duration_to_ms(item.get("duration")),
                status=_required_string(item, "status"),
            )
        )
    return tool_calls


def _response_error_detail(response: httpx.Response) -> str:
    content_type = response.headers.get("content-type", "")
    try:
        payload = response.json()
    except ValueError:
        if (
            "html" in content_type.lower()
            or looks_like_header_or_html_response(response.text)
        ):
            return (
                "upstream returned an HTML/header response; "
                "check TraceReview source and Langfuse credentials"
            )
        text = response.text.strip()
        return _compact_message(text) if text else "empty response body"

    if isinstance(payload, dict):
        detail = payload.get("detail") or payload.get("error") or payload.get("message")
        if detail:
            return _compact_message(str(detail))
    return "unexpected error response"


def _safe_exception_message(error: Exception) -> str:
    response = getattr(error, "response", None)
    status_code = getattr(error, "status_code", None)
    if response is not None and getattr(response, "status_code", None) is not None:
        status_code = response.status_code

    raw_message = str(error)
    if looks_like_header_or_html_response(raw_message):
        if status_code:
            return (
                f"{error.__class__.__name__}: upstream returned an HTML/header "
                f"response (HTTP {status_code}); check Langfuse URL and credentials"
            )
        return (
            f"{error.__class__.__name__}: upstream returned an HTML/header "
            "response; check Langfuse URL and credentials"
        )

    if status_code:
        return f"{error.__class__.__name__}: HTTP {status_code}"
    return f"{error.__class__.__name__}: {_compact_message(raw_message)}"


def _compact_message(message: str, *, max_chars: int = 240) -> str:
    normalized = " ".join(message.split())
    if len(normalized) <= max_chars:
        return normalized
    return f"{normalized[:max_chars - 3]}..."


def _required_mapping(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key)
    if isinstance(value, dict):
        return value
    raise TraceReviewExportError(f"TraceReview export missing object field: {key}")


def _required_list(payload: dict[str, Any], key: str) -> list[Any]:
    value = payload.get(key)
    if isinstance(value, list):
        return value
    raise TraceReviewExportError(f"TraceReview export missing array field: {key}")


def _required_string(payload: dict[str, Any], key: str) -> str:
    value = _string_or_none(payload.get(key))
    if value is not None:
        return value
    raise TraceReviewExportError(f"TraceReview export missing string field: {key}")


def _required_datetime(payload: dict[str, Any], key: str) -> datetime:
    value = _parse_datetime(payload.get(key))
    if value is not None:
        return value
    raise TraceReviewExportError(f"TraceReview export missing datetime field: {key}")


def _string_or_none(value: Any) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None


def _number_to_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _number_to_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def _duration_to_ms(value: Any) -> int | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(value)
    if not isinstance(value, str):
        return None
    match = re.match(r"^\s*(\d+(?:\.\d+)?)\s*(ms|s)?\s*$", value)
    if not match:
        return None
    amount = float(match.group(1))
    unit = match.group(2) or "ms"
    if unit == "s":
        return int(amount * 1000)
    return int(amount)


def _extract_prompts_executed(observations: List[Any]) -> List[PromptExecution]:
    """Extract prompt executions from observations."""
    prompts = []

    for obs in observations:
        # Look for generation observations (LLM calls)
        if hasattr(obs, 'type') and obs.type == 'GENERATION':
            # Try to identify the agent
            agent_id = _identify_agent_from_observation(obs)
            if agent_id:
                prompt_preview = ""
                if hasattr(obs, 'input') and obs.input:
                    if isinstance(obs.input, str):
                        prompt_preview = obs.input[:500]
                    elif isinstance(obs.input, dict) and 'messages' in obs.input:
                        # Extract system message
                        for msg in obs.input['messages']:
                            if msg.get('role') == 'system':
                                prompt_preview = msg.get('content', '')[:500]
                                break

                prompts.append(PromptExecution(
                    agent_id=agent_id,
                    agent_name=_agent_id_to_name(agent_id),
                    prompt_preview=prompt_preview,
                    group_applied=_extract_group_from_observation(obs),
                    model=getattr(obs, 'model', None),
                    tokens_used=obs.usage.total if hasattr(obs, 'usage') and obs.usage else None,
                ))

    return prompts


def _extract_routing_decisions(observations: List[Any]) -> List[RoutingDecision]:
    """Extract routing decisions from supervisor observations."""
    decisions = []

    for obs in observations:
        # Look for transfer tool calls
        if hasattr(obs, 'type') and obs.type == 'SPAN':
            name = getattr(obs, 'name', '')
            if name.startswith('transfer_to_'):
                target_agent = name.replace('transfer_to_', '')
                decisions.append(RoutingDecision(
                    from_agent='supervisor',
                    to_agent=target_agent,
                    reason=None,  # Could extract from metadata if available
                    timestamp=obs.start_time if hasattr(obs, 'start_time') else None,
                ))

    return decisions


def _extract_tool_calls(observations: List[Any]) -> List[ToolCallInfo]:
    """Extract tool calls from observations."""
    tool_calls = []

    for obs in observations:
        if hasattr(obs, 'type') and obs.type == 'SPAN':
            name = getattr(obs, 'name', '')
            # Skip transfer tools (those are routing decisions)
            if name.startswith('transfer_to_'):
                continue
            # Skip internal spans
            if name in ('supervisor', 'agent_run'):
                continue

            # This looks like a tool call
            tool_input = {}
            if hasattr(obs, 'input') and obs.input:
                if isinstance(obs.input, dict):
                    tool_input = obs.input
                elif isinstance(obs.input, str):
                    tool_input = {'query': obs.input}

            output_preview = None
            if hasattr(obs, 'output') and obs.output:
                if isinstance(obs.output, str):
                    output_preview = obs.output[:200]
                elif isinstance(obs.output, dict):
                    output_preview = str(obs.output)[:200]

            duration_ms = None
            if hasattr(obs, 'start_time') and hasattr(obs, 'end_time'):
                if obs.start_time and obs.end_time:
                    duration_ms = int((obs.end_time - obs.start_time).total_seconds() * 1000)

            tool_calls.append(ToolCallInfo(
                name=name,
                input=tool_input,
                output_preview=output_preview,
                duration_ms=duration_ms,
                status='completed',  # Could check for errors
            ))

    return tool_calls


def _extract_user_query(trace: Any, observations: List[Any]) -> str:
    """Extract the user's original query."""
    # Try trace input first
    if hasattr(trace, 'input') and trace.input:
        if isinstance(trace.input, str):
            return trace.input
        if isinstance(trace.input, dict):
            return trace.input.get('message', trace.input.get('query', str(trace.input)))

    # Look in observations
    for obs in observations:
        if hasattr(obs, 'input') and obs.input:
            if isinstance(obs.input, dict) and 'messages' in obs.input:
                for msg in obs.input['messages']:
                    if msg.get('role') == 'user':
                        return msg.get('content', '')

    return "Unknown query"


def _extract_final_response(trace: Any, observations: List[Any]) -> str:
    """Extract the final response."""
    # Try trace output first
    if hasattr(trace, 'output') and trace.output:
        if isinstance(trace.output, str):
            return trace.output
        if isinstance(trace.output, dict):
            return trace.output.get('response', trace.output.get('content', str(trace.output)))

    # Look in observations (last generation output)
    for obs in reversed(observations):
        if hasattr(obs, 'type') and obs.type == 'GENERATION':
            if hasattr(obs, 'output') and obs.output:
                if isinstance(obs.output, str):
                    return obs.output
                if isinstance(obs.output, dict):
                    return obs.output.get('content', str(obs.output))

    return ""


def _identify_agent_from_observation(obs: Any) -> Optional[str]:
    """
    Identify which agent made this observation.

    Returns normalized agent IDs that match catalog_service.py AGENT_REGISTRY keys.
    This ensures trace context agent_ids can be used to look up prompts.
    """
    # Check observation name
    name = getattr(obs, 'name', '')

    for pattern, agent_id in get_trace_agent_patterns().items():
        if pattern in name.lower():
            return agent_id

    # Check metadata
    if hasattr(obs, 'metadata') and obs.metadata:
        if 'agent' in obs.metadata:
            raw_id = obs.metadata['agent']
            return _normalize_agent_id(raw_id)

    return None


def _normalize_agent_id(agent_id: str) -> str:
    """
    Normalize agent ID to match AGENT_REGISTRY keys.

    Handles inconsistencies like 'pdf_specialist' vs 'pdf_extraction'.
    """
    return normalize_trace_agent_id(agent_id)


def _agent_id_to_name(agent_id: str) -> str:
    """
    Convert agent ID to human-readable name.

    Uses normalized IDs that match AGENT_REGISTRY keys.
    """
    return trace_agent_display_name(agent_id)


def _extract_group_from_observation(obs: Any) -> Optional[str]:
    """Extract group info from observation metadata.

    Dual-read: supports both active_groups (new) and active_mods (historical).
    """
    if hasattr(obs, 'metadata') and obs.metadata:
        return obs.metadata.get('active_groups', obs.metadata.get('active_mods', obs.metadata.get('mod')))
    return None


def _calculate_duration_ms(trace: Any) -> Optional[int]:
    """Calculate total trace duration in milliseconds."""
    if hasattr(trace, 'start_time') and hasattr(trace, 'end_time'):
        if trace.start_time and trace.end_time:
            return int((trace.end_time - trace.start_time).total_seconds() * 1000)
    return None
