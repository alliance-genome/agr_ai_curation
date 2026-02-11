"""Shared streaming helpers for Agent Studio APIs."""

from typing import Any, Dict


def flatten_runner_event(event: Dict[str, Any], session_id: str) -> Dict[str, Any]:
    """Flatten runner event payloads for frontend SSE compatibility."""
    event_type = event.get("type")
    event_data = event.get("data", {})
    flat_event = {
        "type": event_type,
        "session_id": session_id,
        "sessionId": session_id,
    }
    if isinstance(event_data, dict):
        flat_event.update(event_data)
    if "timestamp" in event:
        flat_event["timestamp"] = event["timestamp"]
    if "details" in event:
        flat_event["details"] = event["details"]
    return flat_event
