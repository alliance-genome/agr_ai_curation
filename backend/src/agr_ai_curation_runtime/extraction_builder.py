"""Public runtime wrappers for active-run extraction builder state."""

from __future__ import annotations

from importlib import import_module
from typing import Any


_BUILDER_EXPORTS = {
    "CANDIDATE_STATUS_VALID",
    "ExtractionBuilderError",
    "ExtractionBuilderValidationError",
    "get_active_extraction_builder_workspace",
}

CANDIDATE_STATUS_VALID: Any
ExtractionBuilderError: Any
ExtractionBuilderValidationError: Any


def _load_builder_module() -> Any:
    """Resolve the backend extraction builder implementation lazily."""
    return import_module("src.lib.openai_agents.extraction_builder_workspace")


def get_active_extraction_builder_workspace() -> Any:
    return _load_builder_module().get_active_extraction_builder_workspace()


def __getattr__(name: str) -> Any:
    if name not in _BUILDER_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(_load_builder_module(), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | _BUILDER_EXPORTS)


__all__ = [
    "CANDIDATE_STATUS_VALID",
    "ExtractionBuilderError",
    "ExtractionBuilderValidationError",
    "get_active_extraction_builder_workspace",
]
