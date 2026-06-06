"""Runtime payload size summaries for provider-bound context."""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from typing import Any, Mapping


LOGGER = logging.getLogger(__name__)
DEFAULT_TOKEN_THRESHOLDS = (100_000, 250_000, 1_000_000)


@dataclass(frozen=True)
class RuntimePayloadSize:
    json_chars: int
    estimated_tokens: int
    threshold: str | None


def estimate_tokens_from_chars(chars: int) -> int:
    if chars <= 0:
        return 0
    return max(1, math.ceil(chars / 4))


def classify_threshold(
    estimated_tokens: int,
    *,
    thresholds: tuple[int, ...] = DEFAULT_TOKEN_THRESHOLDS,
) -> str | None:
    reached = [threshold for threshold in thresholds if estimated_tokens >= threshold]
    return str(max(reached)) if reached else None


def stable_json_dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)


def json_size(value: Any) -> RuntimePayloadSize:
    text = value if isinstance(value, str) else stable_json_dumps(value)
    chars = len(text)
    tokens = estimate_tokens_from_chars(chars)
    return RuntimePayloadSize(
        json_chars=chars,
        estimated_tokens=tokens,
        threshold=classify_threshold(tokens),
    )


def summarize_text(value: str, *, max_chars: int) -> dict[str, Any]:
    return {
        "preview": value[:max_chars],
        "original_chars": len(value),
        "omitted_chars": max(0, len(value) - max_chars),
        "truncated": len(value) > max_chars,
    }


def largest_json_paths(
    value: Any,
    *,
    max_entries: int = 20,
    min_json_chars: int = 256,
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []

    def visit(node: Any, path: str) -> int:
        size = json_size(node).json_chars
        if size >= min_json_chars:
            entries.append(
                {
                    "path": path or "$",
                    "json_chars": size,
                    "estimated_tokens": estimate_tokens_from_chars(size),
                    "value_type": type(node).__name__,
                    "item_count": len(node) if isinstance(node, (list, dict)) else None,
                }
            )
        if isinstance(node, Mapping):
            for key, child in node.items():
                child_path = f"{path}.{key}" if path else str(key)
                visit(child, child_path)
        elif isinstance(node, list):
            for index, child in enumerate(node):
                visit(child, f"{path}[{index}]" if path else f"[{index}]")
        return size

    visit(value, "")
    entries.sort(key=lambda item: item["json_chars"], reverse=True)
    return entries[:max_entries]


def large_scalar_paths(
    value: Any,
    *,
    root_path: str = "",
    min_chars: int = 1000,
    max_entries: int = 20,
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []

    def visit(node: Any, path: str) -> None:
        if isinstance(node, str):
            if len(node) >= min_chars:
                entries.append(
                    {
                        "path": path or "$",
                        "chars": len(node),
                        "estimated_tokens": estimate_tokens_from_chars(len(node)),
                    }
                )
            return
        if isinstance(node, Mapping):
            for key, child in node.items():
                child_path = f"{path}.{key}" if path else str(key)
                visit(child, child_path)
            return
        if isinstance(node, list):
            for index, child in enumerate(node):
                visit(child, f"{path}[{index}]" if path else f"[{index}]")

    visit(value, root_path)
    entries.sort(key=lambda item: item["chars"], reverse=True)
    return entries[:max_entries]


def provider_context_preflight(
    *,
    surface: str,
    operation: str,
    provider: str | None,
    model: str | None,
    payload: Any,
    metadata: Mapping[str, Any] | None = None,
    emit_runtime_event: bool = False,
    emit_trace_event: bool = False,
) -> dict[str, Any]:
    size = json_size(payload)
    summary = {
        "event": "provider_context_preflight",
        "surface": surface,
        "operation": operation,
        "provider": provider,
        "model": model,
        "json_chars": size.json_chars,
        "estimated_tokens": size.estimated_tokens,
        "threshold": size.threshold,
        "largest_paths": largest_json_paths(payload, max_entries=10),
        "metadata": dict(metadata or {}),
    }
    log_method = LOGGER.warning if size.threshold else LOGGER.info
    log_method(
        "provider_context_preflight surface=%s operation=%s model=%s "
        "json_chars=%s estimated_tokens=%s threshold=%s",
        surface,
        operation,
        model,
        size.json_chars,
        size.estimated_tokens,
        size.threshold,
        extra={"provider_context_preflight": summary},
    )
    if emit_trace_event:
        _write_extraction_trace_preflight_event(summary)
    if emit_runtime_event:
        _emit_specialist_runtime_event(summary)
    return summary


def _preflight_event_details(summary: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "surface": summary.get("surface"),
        "operation": summary.get("operation"),
        "provider": summary.get("provider"),
        "model": summary.get("model"),
        "model_live": True,
        "payload_summary": {
            "json_chars": summary.get("json_chars"),
            "estimated_tokens": summary.get("estimated_tokens"),
            "threshold": summary.get("threshold"),
            "largest_paths": summary.get("largest_paths", []),
        },
        "metadata": summary.get("metadata", {}),
    }


def _write_extraction_trace_preflight_event(summary: Mapping[str, Any]) -> None:
    try:
        from src.lib.openai_agents.extraction_trace_events import (
            write_extraction_trace_event,
        )

        metadata = summary.get("metadata")
        trace_id = metadata.get("trace_id") if isinstance(metadata, Mapping) else None
        write_extraction_trace_event(
            event_type="runtime.provider_context_preflight",
            trace_id=str(trace_id) if trace_id else None,
            input_summary=_preflight_event_details(summary),
            metadata={
                "surface": summary.get("surface"),
                "operation": summary.get("operation"),
                "provider": summary.get("provider"),
                "model": summary.get("model"),
            },
        )
    except Exception:
        LOGGER.debug("Failed to write provider context preflight trace event", exc_info=True)


def _emit_specialist_runtime_event(summary: Mapping[str, Any]) -> None:
    try:
        from src.lib.openai_agents.streaming_tools import add_specialist_event

        add_specialist_event(
            {
                "type": "PROVIDER_CONTEXT_PREFLIGHT",
                "details": _preflight_event_details(summary),
            }
        )
    except Exception:
        LOGGER.debug("Failed to emit provider context preflight event", exc_info=True)
