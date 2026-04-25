"""Helpers for interpreting trace-level outputs across Langfuse SDK versions."""

import json
from typing import Any, Optional

_MAX_RESPONSE_EXTRACTION_DEPTH = 8

_PLACEHOLDER_RESPONSE_TEXTS = {
    "n/a",
    "na",
    "none",
    "null",
    "undefined",
    "not available",
    "not applicable",
    "no response",
    "no response available",
    "no final response",
    "no final response available",
    "no final user-visible output was emitted",
}

_RESPONSE_VALUE_KEYS = (
    "response",
    "assistant_response",
    "answer",
    "final_output",
    "text",
    "content",
    "message",
)

_OUTPUT_CONTAINER_KEYS = (
    "output",
    "result",
    "data",
)

_TOOL_OUTPUT_ITEM_TYPES = {
    "function_call",
    "function_call_output",
    "tool_call",
    "tool_result",
}

_FINAL_OBSERVATION_MARKERS = (
    "chat output",
    "final output",
    "assistant response",
    "final response",
    "chat response",
    "chat output ready",
)


def _extract_text_from_message_content(content: Any) -> Optional[str]:
    """Extract text from OpenAI-style message content arrays."""
    if not isinstance(content, list):
        return None

    texts: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue

        item_type = item.get("type")
        if item_type in {"output_text", "text"}:
            text = item.get("text")
            if isinstance(text, str):
                cleaned = _clean_response_text(text)
                if cleaned:
                    texts.append(cleaned)

    if texts:
        return "\n\n".join(texts)
    return None


def _clean_response_text(value: str) -> Optional[str]:
    stripped = value.strip()
    if not stripped:
        return None

    normalized = " ".join(stripped.casefold().split()).rstrip(".")
    if normalized in _PLACEHOLDER_RESPONSE_TEXTS:
        return None

    return stripped


def _extract_trace_response_text(trace_output: Any, *, depth: int) -> Optional[str]:
    if depth > _MAX_RESPONSE_EXTRACTION_DEPTH:
        return None

    if isinstance(trace_output, str):
        stripped = trace_output.strip()
        if not stripped:
            return None

        if stripped[0] in "[{\"":
            try:
                return _extract_trace_response_text(json.loads(stripped), depth=depth + 1)
            except (json.JSONDecodeError, TypeError):
                pass

        return _clean_response_text(stripped)

    if isinstance(trace_output, list):
        for item in trace_output:
            if isinstance(item, str):
                extracted = _extract_trace_response_text(item, depth=depth + 1)
                if extracted:
                    return extracted
                continue

            if not isinstance(item, dict):
                continue

            item_type = item.get("type")
            if item.get("type") == "message":
                extracted = _extract_text_from_message_content(item.get("content"))
                if extracted:
                    return extracted

            if item_type in _TOOL_OUTPUT_ITEM_TYPES:
                continue

            for key in _RESPONSE_VALUE_KEYS:
                if key not in item:
                    continue
                extracted = _extract_trace_response_text(item.get(key), depth=depth + 1)
                if extracted:
                    return extracted
        return None

    if isinstance(trace_output, dict):
        for key in _RESPONSE_VALUE_KEYS:
            if key not in trace_output:
                continue
            extracted = _extract_trace_response_text(trace_output.get(key), depth=depth + 1)
            if extracted:
                return extracted

        for key in ("message", "content"):
            extracted = _extract_text_from_message_content(trace_output.get(key))
            if extracted:
                return extracted

        if trace_output.get("type") not in _TOOL_OUTPUT_ITEM_TYPES:
            for key in _OUTPUT_CONTAINER_KEYS:
                if key not in trace_output:
                    continue
                extracted = _extract_trace_response_text(trace_output.get(key), depth=depth + 1)
                if extracted:
                    return extracted

    return None


def extract_trace_response_text(trace_output: Any) -> Optional[str]:
    """Return assistant-facing response text from trace output when present."""
    return _extract_trace_response_text(trace_output, depth=0)


def _normalize_observation_label(value: Any) -> str:
    return " ".join(str(value).replace("_", " ").replace("-", " ").casefold().split())


def _observation_labels(observation: dict[str, Any]) -> list[str]:
    labels = [
        _normalize_observation_label(observation.get("name", "")),
        _normalize_observation_label(observation.get("type", "")),
    ]

    for container_key in ("metadata", "input", "output"):
        container = observation.get(container_key)
        if not isinstance(container, dict):
            continue
        for key in (
            "name",
            "type",
            "event",
            "event_type",
            "eventName",
            "agent",
            "agent_name",
            "tool_name",
        ):
            value = container.get(key)
            if value:
                labels.append(_normalize_observation_label(value))

    return labels


def _is_final_response_observation(observation: dict[str, Any]) -> bool:
    labels = _observation_labels(observation)
    return any(
        marker in label
        for label in labels
        for marker in _FINAL_OBSERVATION_MARKERS
    )


def extract_observation_response_text(observation: Any) -> Optional[str]:
    """Return final assistant text from a response-like observation when safe."""
    if not isinstance(observation, dict):
        return None

    if not _is_final_response_observation(observation):
        return None

    for key in ("output", "result", "data"):
        extracted = extract_trace_response_text(observation.get(key))
        if extracted:
            return extracted

    return None


def is_trace_output_cacheable(trace_output: Any) -> bool:
    """Only cache traces once they appear to have a final response or terminal error."""
    if extract_trace_response_text(trace_output):
        return True

    if isinstance(trace_output, dict):
        for key in ("error", "error_type", "status_message", "statusMessage"):
            value = trace_output.get(key)
            if isinstance(value, str) and value.strip():
                return True

    return False
