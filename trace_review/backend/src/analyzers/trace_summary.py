"""
Trace Summary Analyzer
Provides a comprehensive overview of a trace including all key metrics.

This analyzer combines data from multiple sources to give a complete
picture of what happened during an agent run.
"""
from typing import Dict, List, Any
from collections import defaultdict

from .conversation import ConversationAnalyzer


class TraceSummaryAnalyzer:
    """Provides comprehensive trace summaries"""

    @classmethod
    def analyze(cls, trace: Dict, observations: List[Dict]) -> Dict:
        """
        Generate a comprehensive trace summary.

        Args:
            trace: Complete trace data from Langfuse (raw_trace)
            observations: List of all observations

        Returns:
            Dictionary with complete trace summary including:
            - trace_info: Basic trace metadata
            - timing: Latency and duration info
            - cost: Cost breakdown
            - generations: Generation stats
            - tool_calls: Tool call summary
            - errors: Any errors detected
            - links: Useful links (Langfuse UI)
        """
        # Extract raw_trace if wrapped
        raw_trace = trace.get("raw_trace", trace)
        trace_metadata = raw_trace.get("metadata", {})

        # Basic trace info
        trace_info = {
            "trace_id": raw_trace.get("id"),
            "name": raw_trace.get("name"),
            "session_id": raw_trace.get("sessionId"),
            "user_id": raw_trace.get("userId"),
            "timestamp": raw_trace.get("timestamp"),
            "tags": raw_trace.get("tags", []),
            "environment": raw_trace.get("environment"),
            "bookmarked": raw_trace.get("bookmarked", False)
        }

        # Input/Output
        trace_input = raw_trace.get("input", {})
        trace_output = raw_trace.get("output")

        # Extract query from various possible locations
        if isinstance(trace_input, dict):
            query = trace_input.get("query") or trace_input.get("message") or "N/A"
            document_id = trace_input.get("document_id")
            document_name = trace_input.get("document_name")
        else:
            query = str(trace_input) if trace_input else "N/A"
            document_id = None
            document_name = None

        # Timing info
        timing = {
            "total_latency_seconds": raw_trace.get("latency", 0),
            "created_at": raw_trace.get("createdAt"),
            "updated_at": raw_trace.get("updatedAt")
        }

        # Cost info
        cost = {
            "total_cost": raw_trace.get("totalCost", 0),
            "currency": "USD"
        }

        # Generation stats
        generations = [o for o in observations if o.get("type") == "GENERATION"]
        generations.sort(key=lambda x: x.get("startTime", ""))

        total_prompt_tokens = 0
        total_completion_tokens = 0
        total_gen_cost = 0
        models_used = defaultdict(int)

        for gen in generations:
            total_prompt_tokens += gen.get("promptTokens", 0) or 0
            total_completion_tokens += gen.get("completionTokens", 0) or 0
            total_gen_cost += gen.get("calculatedTotalCost", 0) or 0
            models_used[gen.get("model", "unknown")] += 1

        generation_stats = {
            "total_generations": len(generations),
            "total_prompt_tokens": total_prompt_tokens,
            "total_completion_tokens": total_completion_tokens,
            "total_tokens": total_prompt_tokens + total_completion_tokens,
            "models_used": dict(models_used)
        }

        # Tool call summary
        tool_calls = []
        tool_counts = defaultdict(int)

        for gen in generations:
            output = gen.get("output", {})

            # Handle both formats:
            # 1. output is a dict with type="function_call"
            # 2. output is an array containing items with type="function_call"
            function_calls = []
            if isinstance(output, dict) and output.get("type") == "function_call":
                function_calls = [output]
            elif isinstance(output, list):
                function_calls = [item for item in output if isinstance(item, dict) and item.get("type") == "function_call"]

            for fc in function_calls:
                tool_name = fc.get("name", "unknown")
                tool_counts[tool_name] += 1
                tool_calls.append({
                    "name": tool_name,
                    "timestamp": gen.get("startTime"),
                    "call_id": fc.get("call_id")
                })

        tool_summary = {
            "total_tool_calls": len(tool_calls),
            "tool_counts": dict(tool_counts),
            "unique_tools": list(tool_counts.keys())
        }

        # Error detection
        errors = []
        context_overflow = False

        for gen in generations:
            # Check for context overflow (0 completion tokens with high prompt)
            if (gen.get("completionTokens", 0) or 0) == 0:
                prompt = gen.get("promptTokens", 0) or 0
                if prompt > 100000:
                    context_overflow = True
                    errors.append({
                        "type": "context_overflow",
                        "message": f"Context overflow detected: {prompt:,} tokens with 0 completion",
                        "generation_id": gen.get("id"),
                        "model": gen.get("model")
                    })

            # Check for error status
            status = gen.get("metadata", {}).get("status")
            if status and status != "completed":
                errors.append({
                    "type": "generation_error",
                    "message": f"Generation status: {status}",
                    "generation_id": gen.get("id"),
                    "status_message": gen.get("statusMessage")
                })

        # Useful links
        html_path = raw_trace.get("htmlPath", "")
        langfuse_host = "http://localhost:3000"  # Default, could be configurable
        links = {
            "langfuse_trace": f"{langfuse_host}{html_path}" if html_path else None
        }

        # Agent metadata from trace
        agent_info = {
            "supervisor_agent": trace_metadata.get("supervisor_agent"),
            "supervisor_model": trace_metadata.get("supervisor_model"),
            "has_document": trace_metadata.get("has_document"),
            "sdk_info": trace_metadata.get("resourceAttributes", {})
        }

        # MOD-specific context (which Model Organism Database rules were active)
        active_mods = trace_metadata.get("active_mods", [])
        mod_context = {
            "active_mods": active_mods,
            "injection_active": len(active_mods) > 0,
            "mod_count": len(active_mods)
        }

        # Extract clean response using ConversationAnalyzer
        conversation = ConversationAnalyzer.extract_conversation(raw_trace, observations)
        response_text = conversation.get("assistant_response", "")

        # Create response preview (first 500 chars)
        response_preview = response_text[:500] if response_text else None
        if response_preview and len(response_text) > 500:
            response_preview += "..."

        return {
            "trace_info": trace_info,
            "query": query,
            "document": {
                "id": document_id,
                "name": document_name
            } if document_id else None,
            "response_preview": response_preview,
            "response_length": len(response_text) if response_text else 0,
            "timing": timing,
            "cost": cost,
            "generation_stats": generation_stats,
            "tool_summary": tool_summary,
            "errors": errors,
            "has_errors": len(errors) > 0,
            "context_overflow_detected": context_overflow,
            "agent_info": agent_info,
            "mod_context": mod_context,
            "links": links
        }

    @classmethod
    def get_quick_summary(cls, trace: Dict, observations: List[Dict]) -> str:
        """
        Generate a one-line summary of the trace.

        Args:
            trace: Complete trace data
            observations: List of all observations

        Returns:
            Human-readable one-line summary
        """
        analysis = cls.analyze(trace, observations)

        parts = [
            f"Trace {analysis['trace_info']['trace_id'][:8]}...",
            f"{analysis['generation_stats']['total_generations']} gens",
            f"{analysis['tool_summary']['total_tool_calls']} tools",
            f"${analysis['cost']['total_cost']:.4f}",
            f"{analysis['timing']['total_latency_seconds']:.1f}s"
        ]

        if analysis["has_errors"]:
            parts.append("ERRORS")

        if analysis["context_overflow_detected"]:
            parts.append("OVERFLOW")

        return " | ".join(parts)
