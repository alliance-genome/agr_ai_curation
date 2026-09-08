"""Resumable benchmark event transport; disconnects never mutate execution."""

import asyncio
import json
from dataclasses import asdict, dataclass
from threading import Lock
from typing import Any
from uuid import UUID

from anyio.to_thread import run_sync
from fastapi import HTTPException, Request
from fastapi.encoders import jsonable_encoder
from starlette.responses import StreamingResponse
from sqlalchemy import func, select, text

from src.api.benchmark_auth import require_benchmark_read
from src.lib.benchmarks.persistence import BenchmarkJobSummary, BenchmarkRepository
from src.lib.benchmarks.observability import sanitized_benchmark_error
from src.lib.observability.runtime import report_runtime_exception
from src.lib.openai_agents.config import (
    get_benchmark_event_heartbeat_seconds,
    get_benchmark_event_replay_batch_size,
    get_benchmark_max_event_connections_per_principal,
)
from src.models.sql.benchmark import BenchmarkEvent, BenchmarkJobStatus
from src.models.sql.database import SessionLocal

_connections: dict[str, int] = {}
_connection_lock = Lock()
_terminal = {
    BenchmarkJobStatus.COMPLETED, BenchmarkJobStatus.COMPLETED_WITH_FAILURES,
    BenchmarkJobStatus.CANCELLED, BenchmarkJobStatus.FAILED,
}


def _failure(status: int, code: str) -> HTTPException:
    return HTTPException(status, {"code": code, "message": "Benchmark event stream unavailable"})


def _reserve(subject: str) -> None:
    with _connection_lock:
        count = _connections.get(subject, 0)
        if count >= get_benchmark_max_event_connections_per_principal():
            raise _failure(429, "event_connection_limit")
        _connections[subject] = count + 1


def _release(subject: str) -> None:
    with _connection_lock:
        count = _connections[subject] - 1
        if count:
            _connections[subject] = count
        else:
            del _connections[subject]


@dataclass(frozen=True)
class EventBatch:
    summary: BenchmarkJobSummary
    events: tuple[dict[str, Any], ...]
    latest_sequence: int


def read_event_batch(job_id: UUID, subject: str, after: int) -> EventBatch:
    with SessionLocal() as session:
        session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        repository = BenchmarkRepository(session)
        job = repository.get_job(job_id=job_id, owner_subject=subject)
        if job is None:
            raise _failure(404, "not_found")
        latest = session.scalar(select(func.coalesce(func.max(BenchmarkEvent.sequence), 0)).where(
            BenchmarkEvent.job_id == job_id,
        ))
        if after > latest:
            raise _failure(409, "invalid_event_cursor")
        rows = repository.replay_events(
            job_id=job_id, owner_subject=subject, after_sequence=after,
            limit=get_benchmark_event_replay_batch_size(),
        )
        # Protected preparation receipts can precede holes in pruned ordinary
        # history. Check every sequence, not just the minimum retained sequence.
        if any(row.sequence != after + index + 1 for index, row in enumerate(rows)):
            raise HTTPException(410, {
                "code": "event_history_expired", "message": "Refresh job status before resuming",
                "resume_after": f"{job_id}:{latest}",
            })
        return EventBatch(job.summary, tuple({
            "sequence": row.sequence, "event_type": row.event_type,
            "payload": row.payload, "created_at": row.created_at,
        } for row in rows), latest)


def _frame(event: str, payload: Any, identifier: str | None = None) -> str:
    data = json.dumps(jsonable_encoder(payload), separators=(",", ":"))
    return (f"id: {identifier}\n" if identifier else "") + f"event: {event}\ndata: {data}\n\n"


class BenchmarkEventResponse(StreamingResponse):
    def __init__(self, content: Any, subject: str):
        super().__init__(content, media_type="text/event-stream", headers={
            "Cache-Control": "no-store", "X-Accel-Buffering": "no",
        })
        self.subject = subject

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            _release(self.subject)


async def create_event_response(request: Request, job_id: UUID, subject: str):
    cursor = request.headers.get("last-event-id")
    after = 0
    if cursor:
        try:
            cursor_job, sequence = cursor.rsplit(":", 1)
            if UUID(cursor_job) != job_id or not sequence.isascii() or not sequence.isdecimal():
                raise ValueError
            after = int(sequence)
            if after > 9223372036854775807:  # PostgreSQL bigint representation, not a runtime cap.
                raise ValueError
        except ValueError as exc:
            raise _failure(422, "invalid_event_cursor") from exc
    _reserve(subject)
    try:
        first = await run_sync(read_event_batch, job_id, subject, after)
    except BaseException:
        _release(subject)
        raise

    async def stream():
        nonlocal after
        batch = first
        while True:
            if await request.is_disconnected():
                return
            for event in batch.events:
                after = event["sequence"]
                yield _frame("benchmark.event", event, f"{job_id}:{after}")
            if after >= batch.latest_sequence:
                # Status is a fresh projection, not a fabricated durable event ID.
                yield _frame("job.status", asdict(batch.summary))
                if batch.summary.status in _terminal:
                    return
                await asyncio.sleep(get_benchmark_event_heartbeat_seconds())
                yield ": heartbeat\n\n"
            try:
                principal = await require_benchmark_read(request)
                if principal.get("sub") != subject:
                    raise _failure(403, "authorization_required")
                batch = await run_sync(read_event_batch, job_id, subject, after)
            except HTTPException as exc:
                yield _frame("stream.error", exc.detail)
                return
            except Exception as exc:
                report_runtime_exception(
                    sanitized_benchmark_error("event_stream", type(exc).__name__),
                    component="benchmark_events", operation="event_stream",
                )
                yield _frame("stream.error", {"code": "stream_unavailable"})
                return

    return BenchmarkEventResponse(stream(), subject)
