"""
Token Analysis Analyzer
Extracts token usage, cost breakdown, and context growth patterns from traces.

This analyzer is critical for debugging context overflow issues and understanding
token accumulation across agent turns.
"""
from typing import Dict, List, Any, Optional
from datetime import datetime
from collections import defaultdict

from ..services.langfuse_run_reconstruction import usage_cost_summary


class TokenAnalysisAnalyzer:
    """Analyzes token usage and cost patterns in traces"""

    @staticmethod
    def _provider_usage(observations: List[Dict]) -> List[Dict[str, Any]]:
        """Decode the bounded provider-usage events emitted by the runtime."""

        records = []
        for observation in observations:
            metadata = observation.get("metadata")
            if not isinstance(metadata, dict):
                continue
            provider_usage = metadata.get("provider_usage")
            if not isinstance(provider_usage, dict):
                continue
            billed_cost = provider_usage.get("billed_cost")
            records.append(
                {
                    "requested_provider": provider_usage.get("requested_provider"),
                    "requested_model": provider_usage.get("requested_model"),
                    "actual_provider": provider_usage.get("actual_provider"),
                    "actual_model": provider_usage.get("actual_model"),
                    "routing_attempt": provider_usage.get("routing_attempt"),
                    "latency_ms": provider_usage.get("latency_ms"),
                    "input_tokens": provider_usage.get("input_tokens"),
                    "output_tokens": provider_usage.get("output_tokens"),
                    "total_tokens": provider_usage.get("total_tokens"),
                    "billed_cost": (
                        {
                            "amount": billed_cost.get("amount"),
                            "unit": billed_cost.get("unit"),
                            "source": billed_cost.get("source"),
                        }
                        if isinstance(billed_cost, dict)
                        else None
                    ),
                }
            )
        return records

    @staticmethod
    def _parse_time(time_val: Any) -> Optional[datetime]:
        """Parse timestamp string or return datetime object"""
        if not time_val:
            return None
        if isinstance(time_val, datetime):
            return time_val
        try:
            time_str = str(time_val)
            if time_str.endswith('Z'):
                time_str = time_str[:-1] + '+00:00'
            return datetime.fromisoformat(time_str)
        except Exception:
            return None

    @classmethod
    def analyze(cls, trace: Dict, observations: List[Dict]) -> Dict:
        """
        Analyze token usage and costs across all generations.

        Args:
            trace: Complete trace data from Langfuse (raw_trace)
            observations: List of all observations

        Returns:
            Dictionary with token analysis including:
            - total_cost: Total trace cost
            - total_latency: Total trace latency in seconds
            - generations: Per-generation breakdown
            - context_growth: Token accumulation pattern
            - model_breakdown: Cost/tokens by model
            - context_overflow_detected: Whether context limit was hit
        """
        # Extract raw_trace if wrapped
        raw_trace = trace.get("raw_trace", trace)

        # Filter and sort GENERATION observations
        generations = [o for o in observations if o.get("type") == "GENERATION"]
        generations.sort(key=lambda x: x.get("startTime", ""))
        provider_usage = cls._provider_usage(observations)

        if not generations:
            return {
                "found": bool(provider_usage),
                "total_cost": 0,
                "total_latency": 0,
                "total_generations": 0,
                "total_prompt_tokens": 0,
                "total_completion_tokens": 0,
                "generations": [],
                "context_growth": [],
                "model_breakdown": {},
                "context_overflow_detected": False,
                "context_overflow_details": None,
                "provider_usage": provider_usage,
            }

        # Analyze each generation
        generation_data = []
        context_growth = []
        model_breakdown = defaultdict(lambda: {
            "count": 0,
            "prompt_tokens": 0,
            "uncached_input_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "completion_tokens": 0,
            "reasoning_tokens": 0,
            "total_cost": 0
        })
        context_overflow_detected = False
        context_overflow_details = None
        prev_prompt_tokens = 0

        for gen in generations:
            model = (
                gen.get("providedModelName")
                or gen.get("provided_model_name")
                or gen.get("model")
                or gen.get("internal_model_id")
                or gen.get("model_id")
                or "unknown"
            )
            accounting = usage_cost_summary(gen)
            prompt_tokens = accounting["input_tokens"]
            completion_tokens = accounting["output_tokens"]
            total_tokens = accounting["total_tokens"]
            cost = accounting["total_cost"]

            # A zero-usage generation wrapper is not a provider call.
            if total_tokens == 0 and cost == 0:
                continue

            generation_number = len(generation_data) + 1

            # Determine output type
            output = gen.get("output", {})
            output_type = "unknown"
            tool_name = None
            if isinstance(output, dict):
                output_type = output.get("type", "unknown")
                if output_type == "function_call":
                    tool_name = output.get("name", "unknown")
                elif output_type == "message":
                    output_type = "response"

            # Calculate duration
            start = cls._parse_time(gen.get("startTime"))
            end = cls._parse_time(gen.get("endTime"))
            duration_ms = None
            if start and end:
                duration_ms = int((end - start).total_seconds() * 1000)

            # Track context growth
            token_delta = prompt_tokens - prev_prompt_tokens
            context_growth.append({
                "generation": generation_number,
                "prompt_tokens": prompt_tokens,
                "delta": token_delta
            })
            prev_prompt_tokens = prompt_tokens

            # Detect context overflow (0 completion tokens with high prompt tokens)
            if completion_tokens == 0 and prompt_tokens > 100000:
                context_overflow_detected = True
                context_overflow_details = {
                    "generation": generation_number,
                    "prompt_tokens": prompt_tokens,
                    "model": model,
                    "timestamp": gen.get("startTime")
                }

            # Update model breakdown
            model_breakdown[model]["count"] += 1
            model_breakdown[model]["prompt_tokens"] += prompt_tokens
            model_breakdown[model]["uncached_input_tokens"] += accounting["uncached_input_tokens"]
            model_breakdown[model]["cache_read_tokens"] += accounting["cache_read_tokens"]
            model_breakdown[model]["cache_write_tokens"] += accounting["cache_write_tokens"]
            model_breakdown[model]["completion_tokens"] += completion_tokens
            model_breakdown[model]["reasoning_tokens"] += accounting["reasoning_tokens"]
            model_breakdown[model]["total_cost"] += cost

            generation_data.append({
                "generation": generation_number,
                "model": model,
                "prompt_tokens": prompt_tokens,
                "uncached_input_tokens": accounting["uncached_input_tokens"],
                "cache_read_tokens": accounting["cache_read_tokens"],
                "cache_write_tokens": accounting["cache_write_tokens"],
                "completion_tokens": completion_tokens,
                "reasoning_tokens": accounting["reasoning_tokens"],
                "total_tokens": total_tokens,
                "cost": cost,
                "cost_source": accounting["cost_source"],
                "estimated_total_cost": accounting["estimated_total_cost"],
                "duration_ms": duration_ms,
                "output_type": output_type,
                "tool_name": tool_name,
                "time_to_first_token": gen.get("timeToFirstToken"),
                "latency": gen.get("latency"),
                "observation_id": gen.get("id"),
                "timestamp": gen.get("startTime")
            })

        # Calculate totals
        total_prompt = sum(g["prompt_tokens"] for g in generation_data)
        total_completion = sum(g["completion_tokens"] for g in generation_data)
        total_cost = sum(g["cost"] for g in generation_data)

        # Get trace-level data
        trace_total_cost = raw_trace.get("totalCost")
        if trace_total_cost is None:
            trace_total_cost = total_cost
        trace_latency = raw_trace.get("latency", 0)

        return {
            "found": True,
            "total_cost": trace_total_cost,
            "total_latency": trace_latency,
            "total_generations": len(generation_data),
            "total_prompt_tokens": total_prompt,
            "total_completion_tokens": total_completion,
            "generations": generation_data,
            "context_growth": context_growth,
            "model_breakdown": dict(model_breakdown),
            "provider_usage": provider_usage,
            "context_overflow_detected": context_overflow_detected,
            "context_overflow_details": context_overflow_details
        }

    @classmethod
    def get_context_growth_summary(cls, analysis_result: Dict) -> str:
        """
        Generate a human-readable summary of context growth.

        Args:
            analysis_result: Output from analyze()

        Returns:
            Formatted string showing context growth pattern
        """
        if not analysis_result.get("found"):
            return "No token data found"

        lines = ["Context Growth Analysis:", "-" * 40]

        for entry in analysis_result.get("context_growth", []):
            gen = entry["generation"]
            tokens = entry["prompt_tokens"]
            delta = entry["delta"]
            delta_str = f"+{delta:,}" if delta > 0 else f"{delta:,}"
            lines.append(f"Gen {gen:2}: {tokens:>10,} tokens ({delta_str})")

        if analysis_result.get("context_overflow_detected"):
            overflow = analysis_result["context_overflow_details"]
            lines.append("")
            lines.append("WARNING: Context overflow detected!")
            lines.append(f"  Generation: {overflow['generation']}")
            lines.append(f"  Tokens: {overflow['prompt_tokens']:,}")
            lines.append(f"  Model: {overflow['model']}")

        return "\n".join(lines)
