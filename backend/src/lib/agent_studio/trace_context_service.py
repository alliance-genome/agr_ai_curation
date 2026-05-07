"""
Trace Context Service.

Extracts and enriches trace data from Langfuse for display in the Prompt Explorer.
Provides a summary of what happened during a chat interaction, including which
prompts fired, tool calls, and routing decisions.
"""

import logging
import os
import re
from datetime import datetime
from types import SimpleNamespace
from typing import Optional, List, Any

import httpx

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


_HEADER_OR_HTML_RE = re.compile(
    r"(?is)(x-robots-tag|x-content-type-options|referrer-policy|<!doctype|<html)"
)
_TRACE_REVIEW_TIMEOUT_SECONDS = 30.0


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
    try:
        return await _get_trace_context_from_langfuse_sdk(trace_id)
    except TraceContextError as langfuse_error:
        if not _trace_review_fallback_configured():
            raise

        logger.warning(
            "Langfuse SDK trace context extraction failed for %s; "
            "trying TraceReview export fallback: %s",
            trace_id,
            _safe_exception_message(langfuse_error),
        )
        try:
            return await _get_trace_context_from_trace_review_export(trace_id)
        except TraceNotFoundError:
            raise
        except TraceContextError as trace_review_error:
            message = (
                "Failed to extract trace context via Langfuse SDK and "
                "TraceReview export fallback. "
                f"langfuse_error={_safe_exception_message(langfuse_error)}; "
                f"trace_review_error={_safe_exception_message(trace_review_error)}"
            )
            raise TraceContextError(message) from trace_review_error


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
            timestamp=trace.timestamp or datetime.utcnow(),
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
    base_url = os.getenv("TRACE_REVIEW_URL")
    if not base_url:
        raise TraceReviewExportError("TRACE_REVIEW_URL is not configured")

    source = os.getenv(
        "TRACE_CONTEXT_TRACE_REVIEW_SOURCE",
        os.getenv("TRACE_REVIEW_SOURCE", "remote"),
    )
    url = f"{base_url.rstrip('/')}/api/traces/{trace_id}/export"

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(_TRACE_REVIEW_TIMEOUT_SECONDS)
        ) as client:
            response = await client.get(url, params={"source": source})
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


def _trace_context_from_trace_review_export(
    trace_id: str,
    export_payload: dict[str, Any],
) -> TraceContext:
    raw_trace = _mapping_or_empty(export_payload.get("raw_trace"))
    analysis = _mapping_or_empty(export_payload.get("analysis"))
    summary = _mapping_or_empty(analysis.get("summary"))
    conversation = _mapping_or_empty(analysis.get("conversation"))
    observations = [
        _observation_from_export(item)
        for item in export_payload.get("observations", [])
        if isinstance(item, dict)
    ]

    trace = _trace_from_export(raw_trace)
    prompts_executed = _extract_prompts_executed(observations)
    routing_decisions = _extract_routing_decisions(observations)
    tool_calls = _extract_tool_calls(observations)

    if not tool_calls:
        tool_calls = _tool_calls_from_trace_review_analysis(analysis)

    user_query = (
        _string_or_none(conversation.get("user_input"))
        or _string_or_none(conversation.get("user_query"))
        or _extract_user_query(trace, observations)
    )
    final_response = (
        _string_or_none(conversation.get("assistant_response"))
        or _string_or_none(conversation.get("response"))
        or _extract_final_response(trace, observations)
    )

    timestamp = (
        _parse_datetime(summary.get("timestamp"))
        or getattr(trace, "timestamp", None)
        or datetime.utcnow()
    )
    total_duration_ms = _number_to_int(summary.get("duration_ms"))
    if total_duration_ms is None:
        total_duration_seconds = _number_to_float(summary.get("duration_seconds"))
        if total_duration_seconds is not None:
            total_duration_ms = int(total_duration_seconds * 1000)
    if total_duration_ms is None:
        total_duration_ms = _calculate_duration_ms(trace)

    total_tokens = _number_to_int(summary.get("total_tokens"))
    if total_tokens is None:
        total_tokens = sum(
            (obs.usage.total if obs.usage else 0)
            for obs in observations
            if hasattr(obs, "usage") and obs.usage
        )

    agent_count = len({prompt.agent_id for prompt in prompts_executed})
    if agent_count == 0:
        agent_count = _number_to_int(summary.get("agent_count")) or 0

    return TraceContext(
        trace_id=trace_id,
        session_id=_string_or_none(
            raw_trace.get("session_id")
            or raw_trace.get("sessionId")
            or summary.get("session_id")
        ),
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


def _trace_review_fallback_configured() -> bool:
    return bool(os.getenv("TRACE_REVIEW_URL"))


def _trace_from_export(raw_trace: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(
        session_id=_string_or_none(raw_trace.get("session_id") or raw_trace.get("sessionId")),
        timestamp=_parse_datetime(raw_trace.get("timestamp") or raw_trace.get("startTime")),
        input=raw_trace.get("input"),
        output=raw_trace.get("output"),
        start_time=_parse_datetime(raw_trace.get("start_time") or raw_trace.get("startTime")),
        end_time=_parse_datetime(raw_trace.get("end_time") or raw_trace.get("endTime")),
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
        start_time=_parse_datetime(item.get("start_time") or item.get("startTime")),
        end_time=_parse_datetime(item.get("end_time") or item.get("endTime")),
    )


def _usage_from_export(item: dict[str, Any]) -> SimpleNamespace | None:
    raw_usage = item.get("usage")
    if not isinstance(raw_usage, dict):
        raw_usage = item.get("usageDetails")
    if not isinstance(raw_usage, dict):
        return None

    total = _number_to_int(
        raw_usage.get("total")
        or raw_usage.get("total_tokens")
        or raw_usage.get("totalTokens")
    )
    if total is None:
        prompt_tokens = _number_to_int(
            raw_usage.get("prompt_tokens")
            or raw_usage.get("input_tokens")
            or raw_usage.get("input")
        )
        completion_tokens = _number_to_int(
            raw_usage.get("completion_tokens")
            or raw_usage.get("output_tokens")
            or raw_usage.get("output")
        )
        if prompt_tokens is not None or completion_tokens is not None:
            total = (prompt_tokens or 0) + (completion_tokens or 0)

    return SimpleNamespace(total=total or 0)


def _tool_calls_from_trace_review_analysis(
    analysis: dict[str, Any],
) -> List[ToolCallInfo]:
    raw_tool_calls = analysis.get("tool_calls")
    if not isinstance(raw_tool_calls, list):
        return []

    tool_calls = []
    for item in raw_tool_calls:
        if not isinstance(item, dict):
            continue
        name = _string_or_none(item.get("name"))
        if not name:
            continue
        tool_calls.append(
            ToolCallInfo(
                name=name,
                input={},
                output_preview=None,
                duration_ms=_duration_to_ms(item.get("duration")),
                status=_string_or_none(item.get("status")) or "completed",
            )
        )
    return tool_calls


def _response_error_detail(response: httpx.Response) -> str:
    content_type = response.headers.get("content-type", "")
    try:
        payload = response.json()
    except ValueError:
        if "html" in content_type.lower() or _HEADER_OR_HTML_RE.search(response.text):
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
    if _HEADER_OR_HTML_RE.search(raw_message):
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


def _mapping_or_empty(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


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

    # Known agent patterns -> normalized AGENT_REGISTRY IDs
    # IMPORTANT: These must match catalog_service.py AGENT_REGISTRY keys
    agent_patterns = {
        'supervisor': 'supervisor',
        'gene_extraction': 'gene_extractor',
        'gene_extractor': 'gene_extractor',
        'ask_gene_extractor_': 'gene_extractor',
        'gene_expression': 'gene_expression',
        'allele_variant_extraction': 'allele_extractor',
        'allele_extractor': 'allele_extractor',
        'ask_allele_extractor_': 'allele_extractor',
        'disease_extraction': 'disease_extractor',
        'disease_extractor': 'disease_extractor',
        'ask_disease_extractor_': 'disease_extractor',
        'chemical_extraction': 'chemical_extractor',
        'chemical_extractor': 'chemical_extractor',
        'ask_chemical_extractor_': 'chemical_extractor',
        'phenotype_extraction': 'phenotype_extractor',
        'phenotype_extractor': 'phenotype_extractor',
        'phenotype_specialist': 'phenotype_extractor',
        'ask_phenotype_extractor_': 'phenotype_extractor',
        'ask_phenotype_': 'phenotype_extractor',
        'gene_agent': 'gene',
        'allele_agent': 'allele',
        'disease_agent': 'disease',
        'chemical_agent': 'chemical',
        'gene_ontology': 'gene_ontology',
        'go_annotations': 'go_annotations',
        'orthologs': 'orthologs',
        'ontology_mapping': 'ontology_mapping',
        'chat_output': 'chat_output',
        'csv_formatter': 'csv_formatter',
        'tsv_formatter': 'tsv_formatter',
        'json_formatter': 'json_formatter',
        # Normalize pdf_specialist -> pdf_extraction to match AGENT_REGISTRY
        'pdf_specialist': 'pdf_extraction',
        'pdf': 'pdf_extraction',
        'pdf_extraction': 'pdf_extraction',
    }

    for pattern, agent_id in agent_patterns.items():
        if pattern in name.lower():
            return agent_id

    # Check metadata
    if hasattr(obs, 'metadata') and obs.metadata:
        if 'agent' in obs.metadata:
            raw_id = obs.metadata['agent']
            # Normalize any pdf_specialist references to pdf_extraction
            return _normalize_agent_id(raw_id)

    return None


def _normalize_agent_id(agent_id: str) -> str:
    """
    Normalize agent ID to match AGENT_REGISTRY keys.

    Handles inconsistencies like 'pdf_specialist' vs 'pdf_extraction'.
    """
    # Mapping from legacy/trace names to canonical AGENT_REGISTRY IDs
    normalization_map = {
        'pdf_specialist': 'pdf_extraction',
        'pdf': 'pdf_extraction',
        'gene_extraction': 'gene_extractor',
        'ask_gene_extractor_specialist': 'gene_extractor',
        'allele_variant_extraction': 'allele_extractor',
        'ask_allele_extractor_specialist': 'allele_extractor',
        'disease_extraction': 'disease_extractor',
        'ask_disease_extractor_specialist': 'disease_extractor',
        'chemical_extraction': 'chemical_extractor',
        'ask_chemical_extractor_specialist': 'chemical_extractor',
        'phenotype_extraction': 'phenotype_extractor',
        'phenotype_extractor': 'phenotype_extractor',
        'phenotype_specialist': 'phenotype_extractor',
        'ask_phenotype_extractor_specialist': 'phenotype_extractor',
        'ask_phenotype_specialist': 'phenotype_extractor',
    }
    return normalization_map.get(agent_id, agent_id)


def _agent_id_to_name(agent_id: str) -> str:
    """
    Convert agent ID to human-readable name.

    Uses normalized IDs that match AGENT_REGISTRY keys.
    """
    # Map normalized agent IDs to display names
    # These should match AGENT_REGISTRY 'name' values
    names = {
        'supervisor': 'Supervisor',
        'gene_extractor': 'Gene Extraction Agent',
        'gene_expression': 'Gene Expression Extractor',
        'allele_extractor': 'Allele/Variant Extraction Agent',
        'disease_extractor': 'Disease Extraction Agent',
        'chemical_extractor': 'Chemical Extraction Agent',
        'phenotype_extractor': 'Phenotype Extraction Agent',
        'gene': 'Gene Validation Agent',
        'allele': 'Allele Validation Agent',
        'disease': 'Disease Ontology Agent',
        'chemical': 'Chemical Ontology Agent',
        'gene_ontology': 'Gene Ontology Agent',
        'go_annotations': 'GO Annotations Agent',
        'orthologs': 'Orthologs Agent',
        'ontology_mapping': 'Ontology Mapping Agent',
        'chat_output': 'Chat Output',
        'csv_formatter': 'CSV File Formatter',
        'tsv_formatter': 'TSV File Formatter',
        'json_formatter': 'JSON File Formatter',
        # Normalized: 'pdf_extraction' not 'pdf_specialist'
        'pdf_extraction': 'General PDF Extraction Agent',
    }
    return names.get(agent_id, agent_id.replace('_', ' ').title())


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
