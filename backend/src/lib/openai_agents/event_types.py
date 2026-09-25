"""Shared event type constants for OpenAI agent runtime streams."""

from typing import Any, Mapping

INTERNAL_EXTRACTION_RESULT_EVENT_TYPE = "INTERNAL_EXTRACTION_RESULT"

# RUN_ERROR error types whose message is written for the curator, not an
# internal error, so API layers show it instead of their generic failure text.
# ToolGroupCapError: a saved custom agent over the per-group tool cap
# (src.lib.openai_agents.tool_surface, ALL-1280).
CURATOR_FACING_RUN_ERROR_TYPES = frozenset({"ToolGroupCapError"})


def curator_facing_run_error_message(event_data: Mapping[str, Any]) -> str | None:
    """Return a RUN_ERROR's message when it is written for the curator."""

    if event_data.get("error_type") not in CURATOR_FACING_RUN_ERROR_TYPES:
        return None
    message = str(event_data.get("message") or "").strip()
    return message or None


__all__ = [
    "CURATOR_FACING_RUN_ERROR_TYPES",
    "INTERNAL_EXTRACTION_RESULT_EVENT_TYPE",
    "curator_facing_run_error_message",
]
