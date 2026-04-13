"""API module exports with lazy imports.

Importing specific API modules in tests should not eagerly import the full API
package graph, since some modules pull in heavier optional dependencies.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any


__all__ = [
    "documents",
    "chunks",
    "processing",
    "strategies",
    "settings",
    "schema",
    "health",
    "pdf_viewer",
    "feedback",
    "maintenance",
    "agent_studio_custom",
    "pdf_jobs",
]


def __getattr__(name: str) -> Any:
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = import_module(f"{__name__}.{name}")
    globals()[name] = module
    return module
