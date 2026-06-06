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
- **search_traces**: Find traces by session, document, run, extraction, name, or time window
- **get_trace_summary**: Quick overview (duration, cost, tokens, observation counts)
- **get_trace_conversation**: User query and assistant response
- **get_extraction_diagnostic_report**: Concise extraction/builder/validation timeline and findings
- **get_extraction_timeline**: Detailed ordered extraction events and tool observations
- **get_trace_reconstruction**: Chronological Langfuse model/tool/event reconstruction with payload refs
- **get_trace_payloads**: Payload inventory with sizes, hashes, and previews
- **get_trace_payload**: Exact chunked payload retrieval by payload_id
- **get_trace_costs**: Token and cost accounting by agent/model/kind
- **get_trace_duplicates**: Duplicate prompt/context/payload report
- **get_tool_calls_summary**: Lightweight summaries of all tool calls
- **get_tool_calls_page**: Paginated full tool calls (use for large traces)
- **get_tool_call_detail**: Single tool call details
- **get_trace_view**: Specialized views (pdf_citations, token_analysis, agent_context, domain_envelope, etc.)

Start with the summary, then use the diagnostic report or reconstruction to
understand the run. Use payload tools only when exact prompt, model output, or
tool input/output evidence matters.
"""
