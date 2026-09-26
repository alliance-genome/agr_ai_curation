"""Trusted runtime attribution, separate from exported tracing metadata."""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True)
class RuntimeCostContext:
    owner_subject: str
    session_id: str
    run_id: str
    activity: str
    workflow_id: str | None = None
    flow_run_id: str | None = None


_context: ContextVar[RuntimeCostContext | None] = ContextVar("runtime_cost_owner", default=None)


def current_runtime_cost_context() -> RuntimeCostContext | None:
    return _context.get()


def set_runtime_cost_context(context: RuntimeCostContext | None):
    """Hydrate or clear trusted attribution in a reused package worker."""
    return _context.set(context)


@contextmanager
def runtime_cost_scope(context: RuntimeCostContext | None):
    token = _context.set(context)
    try:
        yield
    finally:
        _context.reset(token)
