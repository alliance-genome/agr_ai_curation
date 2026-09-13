"""Content-free execution-stage boundaries shared by normal runtime adapters.

Observers own retention and durable worker leases. This module owns only the
async-local parent relationship and actual elapsed intervals, not aggregation.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from time import monotonic
from typing import TYPE_CHECKING, Iterator, Literal, Protocol
from collections.abc import Mapping
from uuid import UUID, uuid4

if TYPE_CHECKING:
    from .stage_definitions import StageDefinition


@dataclass(frozen=True)
class StageIdentity:
    """An authored stage, distinct from each attempt to execute that stage."""

    stage_id: str
    role: Literal["extraction", "validation", "output", "supervisor", "other"]
    node_id: str | None = None
    source_node_id: str | None = None
    binding_id: str | None = None
    agent_id: str | None = None


@dataclass(frozen=True)
class StageStart:
    execution_id: UUID
    identity: StageIdentity
    parent_execution_id: UUID | None
    parent_invocation_sequence: int | None
    started_at: datetime


@dataclass(frozen=True)
class StageFinish:
    start: StageStart
    completed_at: datetime
    elapsed_ms: int
    status: Literal["succeeded", "failed", "interrupted"]
    failure_type: str | None = None


class StageObserver(Protocol):
    def started(self, stage: StageStart) -> None: ...
    def completed(self, stage: StageFinish) -> None: ...


@dataclass
class _StageOutcome:
    failure_type: str | None = None


_observer: ContextVar[StageObserver | None] = ContextVar("benchmark_stage_observer", default=None)
_stage: ContextVar[StageStart | None] = ContextVar("benchmark_stage", default=None)
_outcome: ContextVar[_StageOutcome | None] = ContextVar("benchmark_stage_outcome", default=None)
_definitions: ContextVar[Mapping[str, "StageDefinition"] | None] = ContextVar("benchmark_stage_definitions", default=None)


def current_stage() -> StageStart | None:
    return _stage.get()


def record_handled_stage_failure(exc: Exception) -> None:
    """Retain failure type when normal runtime converts an error to a result.

    Do not change that runtime's return/raise behavior or retain exception text.
    """
    outcome = _outcome.get()
    if outcome is not None and outcome.failure_type is None:
        outcome.failure_type = type(exc).__name__


@contextmanager
def stage_definition_scope(definitions: Mapping[str, "StageDefinition"] | None) -> Iterator[None]:
    token = _definitions.set(definitions)
    try:
        yield
    finally:
        _definitions.reset(token)


@contextmanager
def measure_declared_stage(stage_id: str) -> Iterator[StageStart | None]:
    definitions = _definitions.get()
    if _observer.get() is None or definitions is None:
        yield None
        return
    definition = definitions.get(stage_id)
    if definition is None:
        raise ValueError("Executable stage has no frozen stage definition")
    with measure_stage(definition.identity()) as stage:
        yield stage


@contextmanager
def measure_bound_validator(binding_id: str, *, node_id: str | None = None) -> Iterator[StageStart | None]:
    """Resolve a scheduled binding or graph sidecar under its source node."""
    definitions = _definitions.get()
    if _observer.get() is None or definitions is None:
        yield None
        return
    parent = current_stage()
    if parent is None:
        raise ValueError("Validator stage requires its executing source stage")
    matches = [definition for definition in definitions.values()
               if definition.role == "validation"
               and definition.binding_id == binding_id
               and definition.source_node_id == parent.identity.node_id
               and definition.node_id == (node_id if node_id is not None else parent.identity.node_id)]
    if len(matches) != 1:
        raise ValueError("Validator binding has no unique frozen stage definition")
    with measure_stage(matches[0].identity()) as stage:
        yield stage


@contextmanager
def measure_flow_node(node_id: str, *, binding_id: str | None = None,
                      parent_invocation_sequence: int | None = None) -> Iterator[StageStart | None]:
    definitions = _definitions.get()
    if _observer.get() is None or definitions is None:
        # Historical runs without stage provenance remain unknown.
        yield None
        return
    matches = [definition for definition in definitions.values()
               if definition.node_id == node_id and definition.binding_id == binding_id]
    if len(matches) != 1:
        raise ValueError("Executable node has no unique frozen stage definition")
    with measure_stage(matches[0].identity(), parent_invocation_sequence=parent_invocation_sequence) as stage:
        yield stage


@contextmanager
def observe_stages(observer: StageObserver | None) -> Iterator[None]:
    """A worker-owned scope; None explicitly isolates non-benchmark work."""
    observer_token = _observer.set(observer)
    stage_token = _stage.set(None)
    outcome_token = _outcome.set(None)
    try:
        yield
    finally:
        _outcome.reset(outcome_token)
        _stage.reset(stage_token)
        _observer.reset(observer_token)


@contextmanager
def measure_stage(identity: StageIdentity, *, parent_invocation_sequence: int | None = None) -> Iterator[StageStart | None]:
    """Measure full stage work only when a benchmark observer is installed.

Start is acknowledged before executing work. Interrupted starts are retained
by the observer even when a process never reaches the completion callback.
"""
    observer = _observer.get()
    if observer is None:
        yield None
        return
    parent = current_stage()
    started = StageStart(
        execution_id=uuid4(), identity=identity,
        parent_execution_id=parent.execution_id if parent is not None else None,
        parent_invocation_sequence=parent_invocation_sequence,
        started_at=datetime.now(timezone.utc),
    )
    clock_start = monotonic()
    observer.started(started)
    token = _stage.set(started)
    outcome = _StageOutcome()
    outcome_token = _outcome.set(outcome)
    status: Literal["succeeded", "failed", "interrupted"] = "succeeded"
    failure_type = None
    try:
        yield started
    except BaseException as exc:
        status = "failed" if isinstance(exc, Exception) else "interrupted"
        failure_type = type(exc).__name__
        raise
    finally:
        if status == "succeeded" and outcome.failure_type is not None:
            status = "failed"
            failure_type = outcome.failure_type
        _outcome.reset(outcome_token)
        _stage.reset(token)
        observer.completed(StageFinish(
            start=started, completed_at=datetime.now(timezone.utc),
            elapsed_ms=max(0, round((monotonic() - clock_start) * 1000)),
            status=status, failure_type=failure_type,
        ))
