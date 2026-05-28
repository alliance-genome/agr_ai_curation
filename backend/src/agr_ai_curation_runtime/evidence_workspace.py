"""Public runtime wrappers for active-run evidence workspace tools."""

from __future__ import annotations

from importlib import import_module
from typing import Any


def _load_evidence_workspace_module() -> Any:
    """Resolve the backend evidence workspace implementation lazily."""
    return import_module("src.lib.openai_agents.tools.evidence_workspace")


def create_list_recorded_evidence_tool(*args: Any, **kwargs: Any) -> Any:
    return _load_evidence_workspace_module().create_list_recorded_evidence_tool(*args, **kwargs)


def create_get_recorded_evidence_tool(*args: Any, **kwargs: Any) -> Any:
    return _load_evidence_workspace_module().create_get_recorded_evidence_tool(*args, **kwargs)


def create_attach_evidence_to_object_tool(*args: Any, **kwargs: Any) -> Any:
    return _load_evidence_workspace_module().create_attach_evidence_to_object_tool(*args, **kwargs)


def create_detach_evidence_from_object_tool(*args: Any, **kwargs: Any) -> Any:
    return _load_evidence_workspace_module().create_detach_evidence_from_object_tool(*args, **kwargs)


def create_discard_recorded_evidence_tool(*args: Any, **kwargs: Any) -> Any:
    return _load_evidence_workspace_module().create_discard_recorded_evidence_tool(*args, **kwargs)


def create_update_recorded_evidence_metadata_tool(*args: Any, **kwargs: Any) -> Any:
    return _load_evidence_workspace_module().create_update_recorded_evidence_metadata_tool(*args, **kwargs)


__all__ = [
    "create_attach_evidence_to_object_tool",
    "create_detach_evidence_from_object_tool",
    "create_discard_recorded_evidence_tool",
    "create_get_recorded_evidence_tool",
    "create_list_recorded_evidence_tool",
    "create_update_recorded_evidence_metadata_tool",
]
