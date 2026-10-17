"""Native Responses context management, separate from the exact event archive.

The SDK owns tool execution. Compaction changes only the next model input; it
never starts another Runner, repeats tools, or changes the builder lifecycle.
"""
from __future__ import annotations

from dataclasses import replace
import hashlib
from typing import Any

from . import config


def compaction_key(item: dict[str, Any]) -> str | None:
    if item.get("type") == "compaction" and item.get("encrypted_content"):
        return hashlib.sha256(str(item["encrypted_content"]).encode()).hexdigest()
    return None


def native_compaction_settings(model: Any, settings: Any, *, provider_hint=None) -> Any:
    """Apply the native policy at the actual provider boundary, not Agent metadata."""
    from .model_request_measurement import describe_model

    if (
        describe_model(model, provider_hint=provider_hint)[:2] != ("openai", "responses")
        or not config.get_agent_context_compaction_enabled()
    ):
        return settings
    return replace(settings, context_management=[{
        "type": "compaction",
        "compact_threshold": config.get_agent_context_compaction_token_threshold(),
    }])


def compacted_window(items: list[dict[str, Any]], native_keys: set[str]) -> list[dict[str, Any]]:
    """Use only a server-generated compaction boundary from this invocation.

    Standalone /compact output is a whole canonical window and must not be
    pruned; its opaque items are deliberately absent from native_keys.
    """
    boundary = next((i for i in range(len(items) - 1, -1, -1)
                     if compaction_key(items[i]) in native_keys), None)
    if boundary is None:
        return items
    # Never orphan a tool response if the provider emitted compaction between
    # a call and its result. Retaining the full window is valid Responses input.
    previous_calls = {item.get("call_id") for item in items[:boundary]
                      if item.get("type") == "function_call"}
    previous_outputs = {item.get("call_id") for item in items[:boundary]
                        if item.get("type") == "function_call_output"}
    if previous_calls - previous_outputs:
        return items
    if any(item.get("type") == "function_call_output" and item.get("call_id") in previous_calls
           for item in items[boundary:]):
        return items
    return items[boundary:]


def compaction_progress_event(data: Any, agent_name: str) -> dict[str, Any] | None:
    """Expose opaque compaction as progress, never as curator answer text."""
    item = getattr(data, "item", None)
    if (getattr(data, "type", None) not in {"response.output_item.added", "response.output_item.done"}
            or getattr(item, "type", None) != "compaction"):
        return None
    return {"type": "AGENT_GENERATING", "details": {
        "agentRole": agent_name, "agentDisplayName": agent_name,
        "message": "Organizing working context; continuing your request. Saved evidence is retained.",
    }}
