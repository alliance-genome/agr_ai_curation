"""Trusted runtime attribution, separate from exported tracing metadata."""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from uuid import uuid4


@dataclass(frozen=True)
class RuntimeCostContext:
    owner_subject: str
    session_id: str | None
    run_id: str
    activity: str
    workflow_id: str | None = None
    flow_run_id: str | None = None
    document_id: str | None = None
    job_id: str | None = None
    invocation_id: str | None = None
    parent_invocation_id: str | None = None


_context: ContextVar[RuntimeCostContext | None] = ContextVar("runtime_cost_owner", default=None)
_agent_boundary: ContextVar[object | None] = ContextVar("runtime_cost_agent_boundary", default=None)


def child_invocation(context):
    """An invocation is an actual runner call, not an inferred request parent."""
    if context is None:
        return None
    return replace(context, invocation_id=str(uuid4()), parent_invocation_id=context.invocation_id)


@contextmanager
def runtime_agent_scope(context, agent):
    # The synchronous owned runner delegates to its async wrapper with the
    # same agent. That is one invocation, not an extra parent/child pair.
    if _agent_boundary.get() is agent and agent is not None:
        with runtime_cost_scope(context):
            yield
        return
    token = _agent_boundary.set(agent)
    try:
        with runtime_cost_scope(child_invocation(context)):
            yield
    finally:
        _agent_boundary.reset(token)


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
