"""Public runtime wrappers for the active resolver call ledger."""

from __future__ import annotations

from importlib import import_module
from typing import Any


_LEDGER_EXPORTS = {
    "ResolverCallLedgerEntry",
    "get_active_resolver_call_ledger",
}

ResolverCallLedgerEntry: Any


def _load_ledger_module() -> Any:
    """Resolve the backend resolver call ledger implementation lazily."""
    return import_module("src.lib.openai_agents.resolver_call_ledger")


def get_active_resolver_call_ledger() -> Any:
    return _load_ledger_module().get_active_resolver_call_ledger()


def __getattr__(name: str) -> Any:
    if name not in _LEDGER_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(_load_ledger_module(), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | _LEDGER_EXPORTS)


__all__ = [
    "ResolverCallLedgerEntry",
    "get_active_resolver_call_ledger",
]
