"""Cell-scoped immutable source identities for experimental model routes."""

from collections.abc import Mapping
from contextlib import contextmanager
from contextvars import ContextVar

from src.schemas.agent_execution_revision import AgentExecutionReceipt

_sources: ContextVar[Mapping[str, AgentExecutionReceipt] | None] = ContextVar(
    "benchmark_source_execution_receipts", default=None,
)


@contextmanager
def benchmark_source_revisions(sources: Mapping[str, AgentExecutionReceipt]):
    token = _sources.set(dict(sources))
    try:
        yield
    finally:
        _sources.reset(token)


def require_benchmark_source(slot: str, agent_key: str) -> AgentExecutionReceipt:
    sources = _sources.get()
    receipt = sources.get(slot) if sources is not None else None
    if receipt is None or receipt.agent_key != agent_key:
        raise ValueError("Benchmark custom target requires its frozen source execution receipt")
    return receipt
