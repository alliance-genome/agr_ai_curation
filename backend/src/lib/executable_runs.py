"""In-process executable run observation registry.

The registry separates long-running producer tasks from HTTP response
observers.  A dropped SSE/fetch connection removes only that observer; the
producer continues until it reaches its own terminal state or an explicit
cancel signal is observed by the underlying surface.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

from src.lib.openai_agents.config import (
    get_executable_run_event_replay_limit,
    get_executable_run_retention_seconds,
)

logger = logging.getLogger(__name__)

ExecutableRunKind = Literal[
    "assistant_chat_turn",
    "curation_flow_run",
    "agent_studio_chat_turn",
    "agent_test_stream",
    "batch",
    "pdf_processing_job",
]
ExecutableRunStatus = Literal[
    "pending",
    "running",
    "completed",
    "failed",
    "cancel_requested",
    "cancelled",
]


class ExecutableRunConflictError(RuntimeError):
    """Raised when an incompatible executable run is already active."""


class ExecutableRunAccessError(RuntimeError):
    """Raised when a caller tries to observe another user's run."""


@dataclass
class ExecutableRunSnapshot:
    run_id: str
    kind: ExecutableRunKind
    status: ExecutableRunStatus
    owner_user_id: str
    session_id: str | None
    turn_id: str | None
    flow_run_id: str | None
    batch_id: str | None
    job_id: str | None
    started_at: str
    updated_at: str
    completed_at: str | None
    can_cancel: bool
    explicit_cancel_only: bool
    replay_supported: bool


@dataclass
class ExecutableRun:
    run_id: str
    kind: ExecutableRunKind
    owner_user_id: str
    session_id: str | None = None
    turn_id: str | None = None
    flow_run_id: str | None = None
    batch_id: str | None = None
    job_id: str | None = None
    can_cancel: bool = True
    explicit_cancel_only: bool = True
    replay_supported: bool = True
    status: ExecutableRunStatus = "pending"
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime | None = None
    events: list[str] = field(default_factory=list)
    subscribers: set[asyncio.Queue[str | None]] = field(default_factory=set)
    task: asyncio.Task[None] | None = None
    terminal_monotonic: float | None = None
    terminal_error_event_factory: Callable[[Exception], str] | None = None

    def snapshot(self) -> ExecutableRunSnapshot:
        return ExecutableRunSnapshot(
            run_id=self.run_id,
            kind=self.kind,
            status=self.status,
            owner_user_id=self.owner_user_id,
            session_id=self.session_id,
            turn_id=self.turn_id,
            flow_run_id=self.flow_run_id,
            batch_id=self.batch_id,
            job_id=self.job_id,
            started_at=self.started_at.isoformat(),
            updated_at=self.updated_at.isoformat(),
            completed_at=self.completed_at.isoformat() if self.completed_at else None,
            can_cancel=self.can_cancel,
            explicit_cancel_only=self.explicit_cancel_only,
            replay_supported=self.replay_supported,
        )

    @property
    def terminal(self) -> bool:
        return self.status in {"completed", "failed", "cancelled"}


class ExecutableRunManager:
    """Tracks producer tasks and replayable observer streams in one API worker."""

    def __init__(self) -> None:
        self._runs: dict[str, ExecutableRun] = {}
        self._active_session_run_ids: dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def get_or_start_stream(
        self,
        *,
        run_id: str,
        kind: ExecutableRunKind,
        owner_user_id: str,
        stream_factory: Callable[[], AsyncIterator[str]],
        session_id: str | None = None,
        turn_id: str | None = None,
        flow_run_id: str | None = None,
        batch_id: str | None = None,
        job_id: str | None = None,
        can_cancel: bool = True,
        terminal_error_event_factory: Callable[[Exception], str] | None = None,
    ) -> tuple[ExecutableRun, bool]:
        await self._prune_expired_terminal_runs()

        async with self._lock:
            existing = self._runs.get(run_id)
            if existing is not None:
                if existing.owner_user_id != owner_user_id:
                    raise ExecutableRunAccessError("Executable run is owned by another user")
                return existing, False

            if session_id:
                active_run_id = self._active_session_run_ids.get(session_id)
                active_run = self._runs.get(active_run_id) if active_run_id else None
                if active_run is not None and not active_run.terminal and active_run_id != run_id:
                    raise ExecutableRunConflictError("Session already has an active executable run")

            run = ExecutableRun(
                run_id=run_id,
                kind=kind,
                owner_user_id=owner_user_id,
                session_id=session_id,
                turn_id=turn_id,
                flow_run_id=flow_run_id,
                batch_id=batch_id,
                job_id=job_id,
                can_cancel=can_cancel,
                status="running",
                terminal_error_event_factory=terminal_error_event_factory,
            )
            self._runs[run_id] = run
            if session_id:
                self._active_session_run_ids[session_id] = run_id

            run.task = asyncio.create_task(self._drive_stream(run, stream_factory))
            return run, True

    async def get_active_session_run(self, session_id: str) -> ExecutableRun | None:
        await self._prune_expired_terminal_runs()

        async with self._lock:
            active_run_id = self._active_session_run_ids.get(session_id)
            active_run = self._runs.get(active_run_id) if active_run_id else None
            if active_run is None or active_run.terminal:
                return None
            return active_run

    async def request_cancel_for_session(
        self,
        *,
        session_id: str,
        owner_user_id: str,
    ) -> ExecutableRun | None:
        await self._prune_expired_terminal_runs()

        async with self._lock:
            active_run_id = self._active_session_run_ids.get(session_id)
            active_run = self._runs.get(active_run_id) if active_run_id else None
            if active_run is None or active_run.terminal:
                return None
            if active_run.owner_user_id != owner_user_id:
                raise ExecutableRunAccessError("Executable run is owned by another user")
            if not active_run.can_cancel:
                return None
            active_run.status = "cancel_requested"
            active_run.updated_at = datetime.now(timezone.utc)
            return active_run

    async def observe(self, run: ExecutableRun) -> AsyncIterator[str]:
        queue: asyncio.Queue[str | None] = asyncio.Queue()
        async with self._lock:
            replay = list(run.events)
            terminal = run.terminal
            if not terminal:
                run.subscribers.add(queue)

        try:
            for event in replay:
                yield event

            if terminal:
                return

            while True:
                event = await queue.get()
                if event is None:
                    return
                yield event
        finally:
            async with self._lock:
                run.subscribers.discard(queue)

    async def _drive_stream(
        self,
        run: ExecutableRun,
        stream_factory: Callable[[], AsyncIterator[str]],
    ) -> None:
        try:
            async for event in stream_factory():
                await self._publish(run, event)
            await self._finish(run, "completed")
        except asyncio.CancelledError:
            await self._finish(run, "cancelled")
            raise
        except Exception as exc:
            logger.exception(
                "Executable run producer failed: run_id=%s kind=%s",
                run.run_id,
                run.kind,
            )
            if run.terminal_error_event_factory is not None:
                try:
                    await self._publish(run, run.terminal_error_event_factory(exc))
                except Exception:
                    logger.exception(
                        "Executable run terminal error event failed: run_id=%s kind=%s",
                        run.run_id,
                        run.kind,
                    )
            await self._finish(run, "failed")

    async def _publish(self, run: ExecutableRun, event: str) -> None:
        async with self._lock:
            run.events.append(event)
            replay_limit = get_executable_run_event_replay_limit()
            if len(run.events) > replay_limit:
                del run.events[: len(run.events) - replay_limit]
            run.updated_at = datetime.now(timezone.utc)
            subscribers = list(run.subscribers)

        for queue in subscribers:
            queue.put_nowait(event)

    async def _finish(self, run: ExecutableRun, status: ExecutableRunStatus) -> None:
        async with self._lock:
            run.status = (
                "cancelled"
                if run.status == "cancel_requested" and status == "completed"
                else status
            )
            run.updated_at = datetime.now(timezone.utc)
            run.completed_at = run.updated_at
            run.terminal_monotonic = time.monotonic()
            if run.session_id and self._active_session_run_ids.get(run.session_id) == run.run_id:
                self._active_session_run_ids.pop(run.session_id, None)
            subscribers = list(run.subscribers)
            run.subscribers.clear()

        for queue in subscribers:
            queue.put_nowait(None)

    async def _prune_expired_terminal_runs(self) -> None:
        retention_seconds = get_executable_run_retention_seconds()
        now = time.monotonic()
        async with self._lock:
            expired_run_ids = [
                run_id
                for run_id, run in self._runs.items()
                if run.terminal_monotonic is not None
                and now - run.terminal_monotonic > retention_seconds
            ]
            for run_id in expired_run_ids:
                self._runs.pop(run_id, None)


executable_run_manager = ExecutableRunManager()
