"""
Trace Context Service.

Extracts and enriches trace data from Langfuse for display in the Prompt Explorer.
Provides a summary of what happened during a chat interaction, including which
prompts fired, tool calls, and routing decisions.
"""

import logging
from datetime import datetime
from typing import Optional, List, Any

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
        # Import Langfuse client
        from langfuse import Langfuse
    except ImportError as e:
        logger.error("Langfuse package not installed")
        raise LangfuseUnavailableError("Langfuse package not installed") from e

    try:
        # Get Langfuse instance
        client = Langfuse()
    except Exception as e:
        logger.error(f"Failed to initialize Langfuse client: {e}", exc_info=True)
        raise LangfuseUnavailableError(f"Failed to initialize Langfuse client: {e}") from e

    try:
        # Fetch the trace using the API client
        trace = client.api.trace.get(trace_id)
        if not trace:
            logger.warning(f"Trace not found: {trace_id}")
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
        logger.error(f"Failed to get trace context: {e}", exc_info=True)
        raise TraceContextError(f"Failed to extract trace context: {e}") from e


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
                    mod_applied=_extract_mod_from_observation(obs),
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
        'gene_expression': 'gene_expression',
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
        # Normalize pdf_specialist -> pdf to match AGENT_REGISTRY
        'pdf_specialist': 'pdf',
        'pdf': 'pdf',
    }

    for pattern, agent_id in agent_patterns.items():
        if pattern in name.lower():
            return agent_id

    # Check metadata
    if hasattr(obs, 'metadata') and obs.metadata:
        if 'agent' in obs.metadata:
            raw_id = obs.metadata['agent']
            # Normalize any pdf_specialist references to pdf
            return _normalize_agent_id(raw_id)

    return None


def _normalize_agent_id(agent_id: str) -> str:
    """
    Normalize agent ID to match AGENT_REGISTRY keys.

    Handles inconsistencies like 'pdf_specialist' vs 'pdf'.
    """
    # Mapping from legacy/trace names to canonical AGENT_REGISTRY IDs
    normalization_map = {
        'pdf_specialist': 'pdf',
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
        'gene_expression': 'Gene Expression Specialist',
        'gene': 'Gene Specialist',
        'allele': 'Allele Specialist',
        'disease': 'Disease Specialist',
        'chemical': 'Chemical Specialist',
        'gene_ontology': 'GO Term Specialist',
        'go_annotations': 'GO Annotations Specialist',
        'orthologs': 'Orthologs Specialist',
        'ontology_mapping': 'Ontology Mapping Specialist',
        'chat_output': 'Chat Output',
        'csv_formatter': 'CSV File Formatter',
        'tsv_formatter': 'TSV File Formatter',
        'json_formatter': 'JSON File Formatter',
        # Normalized: 'pdf' not 'pdf_specialist'
        'pdf': 'PDF Specialist',
    }
    return names.get(agent_id, agent_id.replace('_', ' ').title())


def _extract_mod_from_observation(obs: Any) -> Optional[str]:
    """Extract MOD info from observation metadata."""
    if hasattr(obs, 'metadata') and obs.metadata:
        return obs.metadata.get('active_mods', obs.metadata.get('mod'))
    return None


def _calculate_duration_ms(trace: Any) -> Optional[int]:
    """Calculate total trace duration in milliseconds."""
    if hasattr(trace, 'start_time') and hasattr(trace, 'end_time'):
        if trace.start_time and trace.end_time:
            return int((trace.end_time - trace.start_time).total_seconds() * 1000)
    return None
