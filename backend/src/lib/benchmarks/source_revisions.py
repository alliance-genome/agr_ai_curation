"""Cell-scoped immutable source identities for experimental model routes."""

from collections.abc import Mapping
from contextlib import contextmanager
from contextvars import ContextVar

from src.schemas.agent_execution_revision import AgentExecutionReceipt
from .system_snapshot import FrozenSystemAgent
from .supervisor_snapshot import FrozenFlowSupervisor

_sources: ContextVar[Mapping[str, AgentExecutionReceipt] | None] = ContextVar(
    "benchmark_source_execution_receipts", default=None,
)
_system_sources: ContextVar[Mapping[str, FrozenSystemAgent] | None] = ContextVar(
    "benchmark_system_sources", default=None,
)
_supervisor_source: ContextVar[FrozenFlowSupervisor | None] = ContextVar("benchmark_supervisor_source", default=None)


@contextmanager
def benchmark_source_revisions(
    sources: Mapping[str, AgentExecutionReceipt],
    system_sources: Mapping[str, FrozenSystemAgent] | None = None,
    supervisor: FrozenFlowSupervisor | None = None,
):
    token = _sources.set(dict(sources))
    system_token = _system_sources.set(dict(system_sources) if system_sources is not None else None)
    supervisor_token = _supervisor_source.set(supervisor)
    try:
        yield
    finally:
        _sources.reset(token)
        _system_sources.reset(system_token)
        _supervisor_source.reset(supervisor_token)


def active_supervisor_source() -> FrozenFlowSupervisor | None:
    return _supervisor_source.get()


def active_system_source(agent_key: str) -> FrozenSystemAgent | None:
    sources = _system_sources.get()
    if sources is None:
        return None
    source = sources.get(agent_key)
    if source is None or source.agent_key != agent_key:
        raise ValueError("System agent is absent from the frozen benchmark sources")
    return source


def require_benchmark_source(slot: str, agent_key: str) -> AgentExecutionReceipt:
    sources = _sources.get()
    receipt = sources.get(slot) if sources is not None else None
    if receipt is None or receipt.agent_key != agent_key:
        raise ValueError("Benchmark custom target requires its frozen source execution receipt")
    return receipt
