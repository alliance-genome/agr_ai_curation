"""
Workflow Analysis Context Preparation

Provides lightweight trace context for Opus system prompt.
Replaces the heavy _fetch_trace_for_opus() which sent entire traces.
"""

from typing import Optional


def prepare_trace_context(trace_id: Optional[str]) -> str:
    """
    Prepare lightweight trace context for Opus system prompt.

    Instead of fetching and formatting the entire trace (2000+ chars),
    this function simply provides the trace_id and instructions for
    Opus to use the token-aware trace tools to fetch specific views.

    Args:
        trace_id: Langfuse trace ID, or None if no trace context

    Returns:
        String to inject into Opus system prompt with tool usage instructions
    """
    if not trace_id:
        return ""

    return f"""

## Trace Context

The user has provided a trace ID for analysis: `{trace_id}`

To analyze this trace, use these token-aware tools:
- **get_trace_summary**: Quick overview (duration, cost, tokens, observation counts)
- **get_trace_conversation**: User query and assistant response
- **get_tool_calls_summary**: Lightweight summaries of all tool calls
- **get_tool_calls_page**: Paginated full tool calls (use for large traces)
- **get_tool_call_detail**: Single tool call details
- **get_trace_view**: Specialized views (pdf_citations, token_analysis, agent_context, etc.)

Start by fetching the summary to understand what happened,
then drill down into specific views as needed.
"""
