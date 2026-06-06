"""Context-local curation lookup registry for active supervisor turns."""

from __future__ import annotations

from contextvars import ContextVar
from copy import deepcopy
from typing import Any, Mapping

from .event_types import INTERNAL_EXTRACTION_RESULT_EVENT_TYPE


_current_turn_extraction_events: ContextVar[list[dict[str, Any]]] = ContextVar(
    "current_turn_extraction_events",
    default=[],
)


def clear_current_turn_curation_context() -> None:
    """Clear active-turn extraction refs before a new supervisor run."""

    _current_turn_extraction_events.set([])


def register_internal_extraction_event(event: Mapping[str, Any]) -> None:
    """Register one internal extraction-result event for same-turn lookup."""

    if event.get("type") != INTERNAL_EXTRACTION_RESULT_EVENT_TYPE:
        return

    details = event.get("details")
    internal = event.get("internal")
    if not isinstance(details, Mapping) or not isinstance(internal, Mapping):
        return

    canonical_payload = internal.get("canonical_payload")
    if canonical_payload is None:
        canonical_payload = internal.get("tool_output")
    if canonical_payload is None:
        return

    refs = {
        "tool_name": details.get("toolName"),
        "friendly_name": details.get("friendlyName"),
        "trace_id": event.get("trace_id"),
        "timestamp": event.get("timestamp"),
        "builder_finalization": deepcopy(internal.get("builder_finalization")),
        "output_length": internal.get("output_length"),
    }
    record = {
        "source": "current_turn",
        "refs": {key: value for key, value in refs.items() if value not in (None, "", [])},
        "payload_json": deepcopy(canonical_payload),
    }

    current = list(_current_turn_extraction_events.get())
    current.append(record)
    _current_turn_extraction_events.set(current)


def list_current_turn_curation_context() -> list[dict[str, Any]]:
    """Return active-turn extraction refs registered in this async context."""

    return deepcopy(_current_turn_extraction_events.get())


__all__ = [
    "clear_current_turn_curation_context",
    "list_current_turn_curation_context",
    "register_internal_extraction_event",
]
