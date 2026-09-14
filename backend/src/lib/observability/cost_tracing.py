"""Enrich the existing OpenInference spans; never emit a second cost generation.

The adapter is tested against the pinned OpenInference processor contract. Its
span map is used only while a span is alive; application identity is immutable
request context or registry metadata attached to the nearest SDK agent span.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from openinference.instrumentation.openai_agents._processor import OpenInferenceTracingProcessor
from opentelemetry.metrics import get_meter

from .cost_context import current_cost_context

logger = logging.getLogger(__name__)
_missing_usage = get_meter(__name__).create_counter("ai_curation.model_usage_missing")


def _metadata(span) -> dict[str, Any]:
    raw = (getattr(span, "attributes", None) or {}).get("metadata")
    if not raw:
        return {}
    try:
        value = json.loads(raw)
        return value.get("cost_context", {})
    except (ValueError, TypeError):
        return {}


class CostTracingProcessor(OpenInferenceTracingProcessor):
    def on_span_start(self, span):
        parent = self._otel_spans.get(span.parent_id)
        context = {**current_cost_context(), **_metadata(parent)}
        super().on_span_start(span)
        current = self._otel_spans.get(span.span_id)
        if current is not None:
            current.set_attribute("metadata", json.dumps({"cost_context": context}))

    def on_span_end(self, span):
        current = self._otel_spans.get(span.span_id)
        if current is not None and span.span_data.type in {"response", "generation"}:
            try:
                self._enrich_generation(span, current)
            except Exception as exc:
                # Avoid exception contents, which may contain provider payloads.
                logger.warning("Cost telemetry enrichment failed (%s)", type(exc).__name__)
        super().on_span_end(span)

    def _enrich_generation(self, span, current):
        metadata = _metadata(current)
        response = getattr(span.span_data, "response", None)
        metadata["attempt_id"] = span.span_id
        metadata["attempt_outcome"] = "error" if span.error else "success" if response is not None else "unknown"
        metadata["usage_status"] = "missing"
        if response is not None:
            metadata.setdefault("provider", "openai")
            metadata["provider_response_id"] = getattr(response, "id", None)
            metadata["service_tier"] = getattr(response, "service_tier", None)
            reasoning = getattr(response, "reasoning", None)
            metadata["effort"] = getattr(reasoning, "effort", None)
            usage = getattr(response, "usage", None)
            if usage is not None:
                details = usage.model_dump()
                inputs = details.get("input_tokens_details") or {}
                outputs = details.get("output_tokens_details") or {}
                reads = inputs.get("cached_tokens", 0)
                writes = inputs.get("cache_write_tokens")
                metadata["cache_write_status"] = "recorded" if writes is not None else "not_recorded"
                total_input = details.get("input_tokens", 0)
                total_output = details.get("output_tokens", 0)
                reasoning_tokens = outputs.get("reasoning_tokens", 0)
                exclusive = {
                    "input": total_input - reads - (writes or 0),
                    "input_cached_tokens": reads,
                    "output": total_output - reasoning_tokens,
                    "output_reasoning_tokens": reasoning_tokens,
                }
                if writes is not None:
                    exclusive["input_cache_creation"] = writes
                if any(value < 0 for value in exclusive.values()):
                    metadata["usage_status"] = "inconsistent"
                else:
                    current.set_attribute("langfuse.observation.usage_details", json.dumps(exclusive))
                    metadata["usage_status"] = "recorded"
                    # Sentry and OTel use inclusive provider totals; Langfuse
                    # uses disjoint buckets. They describe the same usage.
                    from sentry_sdk import get_current_span
                    sentry_span = get_current_span()
                    for key, value in {
                        "input_tokens": total_input, "output_tokens": total_output,
                        "input_tokens.cached": reads,
                        "output_tokens.reasoning": reasoning_tokens,
                        **({"input_tokens.cache_write": writes} if writes is not None else {}),
                    }.items():
                        current.set_attribute("gen_ai.usage." + key, value)
                        if sentry_span is not None:
                            sentry_span.set_data("gen_ai.usage." + key, value)
            else:
                metadata["usage_status"] = "missing"
        elif span.span_data.type == "generation":
            usage = getattr(span.span_data, "usage", None)
            if usage:
                metadata["usage_status"] = "recorded"
        if metadata["usage_status"] == "missing":
            _missing_usage.add(1)
        config = getattr(span.span_data, "model_config", None) or {}
        metadata.setdefault("effort", config.get("reasoning_effort"))
        current.set_attribute("metadata", json.dumps({"cost_context": metadata}))


def install_cost_tracing():
    from agents import set_trace_processors
    from opentelemetry import trace
    from openinference.instrumentation import OITracer, TraceConfig
    from openinference.instrumentation.openai_agents import __version__
    tracer = OITracer(trace.get_tracer("openinference.instrumentation.openai_agents", __version__), config=TraceConfig())
    set_trace_processors([CostTracingProcessor(tracer)])
