"""Public runtime wrapper for durable extraction trace events."""

from __future__ import annotations

from importlib import import_module
from typing import Any


def _load_trace_events_module() -> Any:
    """Resolve the backend extraction trace event writer lazily."""
    return import_module("src.lib.openai_agents.extraction_trace_events")


def write_extraction_trace_event(*args: Any, **kwargs: Any) -> Any:
    return _load_trace_events_module().write_extraction_trace_event(*args, **kwargs)


__all__ = ["write_extraction_trace_event"]
