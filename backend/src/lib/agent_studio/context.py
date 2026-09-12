"""
Workflow Analysis Context Preparation

Provides lightweight trace context for the Agent Studio AI Chat system prompt.
"""

from typing import Optional


def prepare_trace_context(trace_id: Optional[str]) -> str:
    """
    Prepare lightweight trace context for the AI Chat system prompt.

    Instead of fetching and formatting the entire trace (2000+ chars),
    this function simply provides the trace_id and instructions for
    the assistant to use token-aware trace tools to fetch specific views.

    Args:
        trace_id: Langfuse trace ID, or None if no trace context

    Returns:
        String to inject into the AI Chat system prompt with tool usage instructions
    """
    if not trace_id:
        return ""

    return f"""

## Trace Context

The user has provided a trace ID for analysis: `{trace_id}`

To analyze this trace, use these token-aware tools:
- **search_traces**: Find traces by session, document, run, extraction, name, or time window
- **get_trace_summary**: Quick overview (duration, cost, tokens, observation counts)
- **get_trace_conversation**: Exact field chunks for user query or assistant response
- **get_extraction_diagnostic_report**: Summary/inventory first; request a named section for bounded findings or timeline pages
- **get_extraction_timeline**: Summary/inventory first; request the timeline section and follow next_call
- **get_trace_reconstruction**: Summary first; request section=events for bounded payload-reference pages
- **get_trace_payloads**: Summary first; request section=payloads for bounded IDs, sizes, hashes, and previews
- **get_trace_payload**: Exact chunked payload retrieval by payload_id
- **get_trace_costs**: Totals first; request a named cost collection for bounded detail
- **get_trace_duplicates**: Counts first; request duplicate_groups or duplicate_payloads for bounded detail
- **get_tool_calls_summary**: Paginated lightweight tool-call summaries
- **get_tool_calls_page**: Paginated call metadata and exact-field references
- **get_tool_call_detail**: Exact input or result chunks for one call
- **get_trace_view**: Specialized summary/inventory views; request one named section at a time

Start with each aggregate tool's summary and collection inventory, then request
one named section and follow its next_call until complete=true. Use exact payload
tools only when prompt, model output, or tool input/output evidence matters.
"""
