"""Persistence helpers for LangGraph supervisor telemetry."""

from __future__ import annotations

from datetime import datetime, timezone
from time import perf_counter
from typing import Any, Dict, Iterable, Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.models import LangGraphNodeRun, LangGraphRun


class LangGraphRunRepository:
    """Stores LangGraph workflow telemetry in Postgres."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def start_run(
        self,
        *,
        session_id: UUID,
        pdf_id: Optional[UUID],
        workflow_name: str,
        question: str,
        run_metadata: Optional[Dict[str, Any]] = None,
    ) -> LangGraphRun:
        run = LangGraphRun(
            session_id=session_id,
            pdf_id=pdf_id,
            workflow_name=workflow_name,
            input_query=question,
            run_metadata=run_metadata or {},
        )
        self._db.add(run)
        self._db.flush()
        return run

    def complete_run(
        self,
        run: LangGraphRun,
        *,
        state_snapshot: Dict[str, Any],
        specialists_invoked: Iterable[str],
        latency_ms: int,
        status: str = "COMPLETED",
    ) -> None:
        run.state_snapshot = state_snapshot
        run.specialists_invoked = list(specialists_invoked)
        run.latency_ms = latency_ms
        run.status = status
        run.completed_at = datetime.now(timezone.utc)

    def log_node_finish(
        self,
        run: LangGraphRun,
        *,
        node_key: str,
        node_type: str,
        input_state: Dict[str, Any],
        output_state: Dict[str, Any],
        latency_ms: int,
        status: str = "COMPLETED",
        error: Optional[str] = None,
        deps_snapshot: Optional[Dict[str, Any]] = None,
    ) -> LangGraphNodeRun:
        node = LangGraphNodeRun(
            graph_run_id=run.id,
            node_key=node_key,
            node_type=node_type,
            input_state=input_state,
            output_state=output_state,
            latency_ms=latency_ms,
            status=status,
            error=error,
            deps_snapshot=deps_snapshot,
        )
        self._db.add(node)
        self._db.flush()
        return node

    def commit(self) -> None:
        self._db.commit()


class TimedNodeLogger:
    """Context helper to measure node execution time and persist results."""

    def __init__(
        self,
        repo: LangGraphRunRepository,
        run: LangGraphRun,
        *,
        node_key: str,
        node_type: str,
        input_state: Dict[str, Any],
    ) -> None:
        self._repo = repo
        self._run = run
        self._node_key = node_key
        self._node_type = node_type
        self._input_state = input_state
        self._start = perf_counter()

    def finish(
        self,
        *,
        output_state: Dict[str, Any],
        status: str = "COMPLETED",
        error: Optional[str] = None,
        deps_snapshot: Optional[Dict[str, Any]] = None,
    ) -> LangGraphNodeRun:
        latency_ms = int((perf_counter() - self._start) * 1000)
        return self._repo.log_node_finish(
            self._run,
            node_key=self._node_key,
            node_type=self._node_type,
            input_state=self._input_state,
            output_state=output_state,
            latency_ms=latency_ms,
            status=status,
            error=error,
            deps_snapshot=deps_snapshot,
        )


__all__ = ["LangGraphRunRepository", "TimedNodeLogger"]
