"""Public runtime wrapper for the record_evidence document tool."""

from __future__ import annotations

from importlib import import_module
from typing import Any


def _load_record_evidence_module() -> Any:
    """Resolve the backend record_evidence tool implementation lazily."""
    return import_module("src.lib.openai_agents.tools.record_evidence")


def create_record_evidence_tool(*args: Any, **kwargs: Any) -> Any:
    return _load_record_evidence_module().create_record_evidence_tool(*args, **kwargs)


__all__ = ["create_record_evidence_tool"]
