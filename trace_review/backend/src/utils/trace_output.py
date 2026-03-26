"""Helpers for interpreting trace-level outputs across Langfuse SDK versions."""

import json
from typing import Any, Optional


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
            if isinstance(text, str) and text.strip():
                texts.append(text.strip())

    if texts:
        return "\n\n".join(texts)
    return None


def extract_trace_response_text(trace_output: Any) -> Optional[str]:
    """Return assistant-facing response text from trace output when present."""
    if not trace_output:
        return None

    if isinstance(trace_output, str):
        stripped = trace_output.strip()
        if not stripped:
            return None

        if stripped[0] in "[{":
            try:
                return extract_trace_response_text(json.loads(stripped))
            except (json.JSONDecodeError, TypeError):
                pass

        return stripped

    if isinstance(trace_output, list):
        for item in trace_output:
            if not isinstance(item, dict):
                continue

            if item.get("type") == "message":
                extracted = _extract_text_from_message_content(item.get("content"))
                if extracted:
                    return extracted

            for key in ("text", "content", "message"):
                value = item.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        return None

    if isinstance(trace_output, dict):
        for key in ("response", "assistant_response", "text", "content", "message"):
            value = trace_output.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            extracted = extract_trace_response_text(value)
            if extracted:
                return extracted

        for key in ("message", "content", "output"):
            extracted = _extract_text_from_message_content(trace_output.get(key))
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
