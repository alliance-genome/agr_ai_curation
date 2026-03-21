"""OpenAI agent entry points.

Use lazy exports so importing one agent module does not eagerly import the full
agent graph and create avoidable package cycles.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = ["create_curation_prep_agent", "create_supervisor_agent"]


def __getattr__(name: str) -> Any:
    if name == "create_curation_prep_agent":
        return import_module(".curation_prep_agent", __name__).create_curation_prep_agent
    if name == "create_supervisor_agent":
        return import_module(".supervisor_agent", __name__).create_supervisor_agent
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
