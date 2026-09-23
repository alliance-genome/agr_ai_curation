"""Enrich the existing OpenInference spans; never emit a second cost generation.

The adapter is tested against the pinned OpenInference processor contract. Its
span map is used only while a span is alive; application identity is immutable
request context or registry metadata attached to the nearest SDK agent span.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from typing import Any

from openinference.instrumentation import TraceConfig
from openinference.instrumentation.openai_agents._processor import OpenInferenceTracingProcessor
from opentelemetry.metrics import get_meter

from .cost_context import current_cost_context, current_model_request

logger = logging.getLogger(__name__)
_missing_usage = get_meter(__name__).create_counter("ai_curation.model_usage_missing")
_MODEL_SPAN_TYPES = frozenset({"response", "generation"})
_OUTCOMES = {
    "completed": "success", "failed": "error", "cancelled": "cancelled",
    "incomplete": "incomplete", "in_progress": "ongoing", "queued": "ongoing",
}


def _metadata(span) -> dict[str, Any]:
    raw = (getattr(span, "attributes", None) or {}).get("metadata")
    if not raw:
        return {}
    try:
        value = json.loads(raw)
        return value.get("cost_context", {})
    except (ValueError, TypeError):
        return {}


def openinference_trace_config() -> TraceConfig:
    """OpenInference export settings for Agents SDK spans.

    ``input.value`` already carries the full request input as JSON (Langfuse
    stores it as the observation input). The flattened per-message copies add
    several span attributes per history item, so long tool-loop turns overflowed
    the OpenTelemetry span attribute limit (128, oldest evicted first) and lost
    model, usage and cost context.
    """
    return TraceConfig(hide_input_messages=True)


def _usage_mapping(usage) -> dict[str, Any] | None:
    """Provider-reported usage as a mapping; None when the provider reported none."""
    if usage is None:
        return None
    details = dict(usage) if isinstance(usage, Mapping) else usage.model_dump()
    # The SDK substitutes a zero Usage() (requests=0) when a provider omits usage.
    if details.get("requests") == 0:
        return None
    return details


class _PinnedAttributesSpan:
    """Write cost attributes last, after OpenInference exports the payload.

    The span attribute limit evicts the oldest attributes first, so the
    attributes written last are the ones that survive a large payload.
    """

    def __init__(self, span, attributes: dict[str, Any]):
        self._span = span
        self._pinned = attributes

    def __getattr__(self, name):
        return getattr(self._span, name)

    def end(self, end_time=None):
        for key, value in self._pinned.items():
            self._span.set_attribute(key, value)
        self._span.end(end_time)


class CostTracingProcessor(OpenInferenceTracingProcessor):
    def on_span_start(self, span):
        parent = self._otel_spans.get(span.parent_id)
        context = {**current_cost_context(), **_metadata(parent)}
        if span.span_data.type in _MODEL_SPAN_TYPES:
            context.update(current_model_request())
        super().on_span_start(span)
        current = self._otel_spans.get(span.span_id)
        if current is not None:
            current.set_attribute("metadata", json.dumps({"cost_context": context}))

    def on_span_end(self, span):
        current = self._otel_spans.get(span.span_id)
        if current is not None and span.span_data.type in _MODEL_SPAN_TYPES:
            try:
                enriched = self._enrich_generation(span, current)
            except Exception as exc:
                # Avoid exception contents, which may contain provider payloads.
                logger.warning("Cost telemetry enrichment failed (%s)", type(exc).__name__)
            else:
                # Span-start identity (trace/session/user) is pinned too; cost
                # attributes go last so they are the newest of all.
                started = getattr(current, "attributes", None) or {}
                pinned = {key: value for key, value in started.items() if key not in enriched}
                self._otel_spans[span.span_id] = _PinnedAttributesSpan(current, {**pinned, **enriched})
        super().on_span_end(span)

    def _enrich_generation(self, span, current) -> dict[str, Any]:
        """Cost attributes for one model attempt; usage is never reported as zero."""
        data = span.span_data
        metadata = _metadata(current)
        attributes: dict[str, Any] = {}
        response = getattr(data, "response", None)
        span_usage = getattr(data, "usage", None)
        # The SDK fills response/usage/output only after the provider's terminal
        # event; a span with none of them ended early (cancelled or closed).
        terminal = response is not None or span_usage is not None or bool(getattr(data, "output", None))
        metadata["attempt_id"] = span.span_id
        if response is not None:
            metadata.setdefault("provider", "openai")
            metadata["provider_response_id"] = getattr(response, "id", None)
            metadata["service_tier"] = getattr(response, "service_tier", None)
            reasoning = getattr(response, "reasoning", None)
            metadata["effort"] = getattr(reasoning, "effort", None)
            usage = _usage_mapping(getattr(response, "usage", None))
        else:
            # Without the response payload the SDK still records the usage it read.
            usage = _usage_mapping(span_usage)
            if usage is None and span_usage is not None:
                # Keep OpenInference from exporting the SDK's zero Usage() as tokens.
                data.usage = None
        if span.error:
            metadata["attempt_outcome"] = "error"
        elif response is not None:
            metadata["attempt_outcome"] = _OUTCOMES.get(getattr(response, "status", None), "unknown")
        else:
            metadata["attempt_outcome"] = "unknown" if terminal else "cancelled"
        model = getattr(response, "model", None) or getattr(data, "model", None)
        if usage is None:
            metadata["usage_status"] = (
                "failed" if span.error else "provider_omitted" if terminal else "cancelled"
            )
        else:
            inputs = usage.get("input_tokens_details") or {}
            outputs = usage.get("output_tokens_details") or {}
            reads = inputs.get("cached_tokens") or 0
            writes = inputs.get("cache_write_tokens")
            metadata["cache_write_status"] = "recorded" if writes is not None else "not_recorded"
            total_input = usage.get("input_tokens", 0)
            total_output = usage.get("output_tokens", 0)
            reasoning_tokens = outputs.get("reasoning_tokens") or 0
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
                attributes["langfuse.observation.usage_details"] = json.dumps(exclusive)
                metadata["usage_status"] = "recorded"
                model = model or metadata.get("requested_model")
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
                    attributes["gen_ai.usage." + key] = value
                    if sentry_span is not None:
                        sentry_span.set_data("gen_ai.usage." + key, value)
        if metadata["usage_status"] != "recorded":
            _missing_usage.add(1, {"usage_status": metadata["usage_status"]})
        if model:
            attributes["llm.model_name"] = model
        config = getattr(data, "model_config", None) or {}
        metadata.setdefault("effort", config.get("reasoning_effort"))
        attributes["metadata"] = json.dumps({"cost_context": metadata})
        return attributes


def install_cost_tracing():
    from agents import set_trace_processors
    from opentelemetry import trace
    from openinference.instrumentation import OITracer
    from openinference.instrumentation.openai_agents import __version__
    tracer = OITracer(trace.get_tracer("openinference.instrumentation.openai_agents", __version__),
                      config=openinference_trace_config())
    set_trace_processors([CostTracingProcessor(tracer)])
