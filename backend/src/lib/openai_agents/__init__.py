"""OpenAI Agents SDK package exports.

Keep package-level exports lazy so importing submodules does not eagerly pull in
the full supervisor/runner graph during unrelated test collection.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = [
    "run_agent_streamed",
    "create_supervisor_agent",
]


def __getattr__(name: str) -> Any:
    if name == "run_agent_streamed":
        return import_module(".runner", __name__).run_agent_streamed
    if name == "create_supervisor_agent":
        return import_module(".agents", __name__).create_supervisor_agent
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
