"""Short-lived worker-owned reads for batch progress SSE.

Only serialized events and scalar comparison state cross the worker boundary.
The broadcaster remains best-effort; periodic reads reconcile durable state.
"""
import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict
from uuid import UUID

import anyio
from anyio.to_thread import run_sync
from starlette.responses import StreamingResponse

from .events import get_batch_broadcaster
from .result_files import canonical_result_files as _batch_document_result_files
from .service import BatchService
from ...models.sql.batch import BatchStatus
from ...models.sql.database import SessionLocal
from ..observability.runtime import report_runtime_exception, sanitized_runtime_error
from ..openai_agents.config import get_batch_stream_poll_interval_seconds

_BACKEND_ONLY_BATCH_EVENT_FIELDS = {"internal"}
logger = logging.getLogger(__name__)


def _sanitize_batch_stream_event(event: Dict[str, Any]) -> Dict[str, Any]:
    """Remove backend-only fields before streaming SSE payloads to clients."""
    return {
        key: value
        for key, value in event.items()
        if key not in _BACKEND_ONLY_BATCH_EVENT_FIELDS
    }


def _batch_document_status_event(
    batch: Any,
    doc: Any,
    handoff_metadata: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    stored_output_branches = getattr(doc, "output_branches", None)
    return {
        "type": "DOCUMENT_STATUS",
        "batch_id": str(batch.id),
        "document_id": str(doc.document_id),
        "batch_document_id": str(doc.id),
        "position": doc.position,
        "status": doc.status.value,
        "result_files": _batch_document_result_files(doc),
        "output_status": getattr(doc, "output_status", None),
        "output_branches": (
            stored_output_branches if isinstance(stored_output_branches, list) else []
        ),
        "review_session_ids": doc.review_session_ids,
        **(handoff_metadata or {}),
        "error_message": doc.error_message,
        "processing_time_ms": doc.processing_time_ms,
    }


def _batch_partial_document_count(batch: Any) -> int:
    """Count completed documents whose configured output branches were partial."""

    return sum(
        1
        for doc in (getattr(batch, "documents", None) or [])
        if getattr(doc, "output_status", None) == "partial"
    )



@dataclass(frozen=True)
class Snapshot:
    events: tuple[str, ...] = ()
    statuses: dict[str, str] = field(default_factory=dict)
    counts: tuple[int, int] | None = None
    terminal: bool = False


def _encode(event: dict) -> str:
    return f"data: {json.dumps(event)}\n\n"


def _report_failure() -> None:
    # Do not capture SQL, parameters, identifiers, or the original exception chain.
    report_runtime_exception(
        sanitized_runtime_error("Batch progress stream failed"),
        component="batch_stream", operation="stream_failed",
    )


def verify_batch_owner(batch_id: UUID, user_id: int) -> bool:
    """Verification completes before headers; no request-scoped DB dependency."""
    with SessionLocal() as db:
        return BatchService(db).get_batch(batch_id, user_id) is not None


def read_snapshot(batch_id: UUID, user_id: int, previous: Snapshot) -> Snapshot:
    """Own all SQL and serialization, including failures after a disconnect."""
    try:
        with SessionLocal() as db:
            service = BatchService(db)
            batch = service.get_batch(batch_id, user_id)
            if batch is None:
                return Snapshot(
                    events=(_encode({"type": "ERROR", "message": "Batch not found"}),),
                    terminal=True,
                )
            statuses = {str(doc.id): doc.status.value for doc in batch.documents}
            changed = [
                doc for doc in batch.documents
                if previous.statuses.get(str(doc.id)) != doc.status.value
            ]
            metadata = service.get_documents_handoff_metadata(batch, changed) if changed else {}
            status = {
                "batch_id": str(batch.id),
                "status": batch.status.value,
                "total_documents": batch.total_documents,
                "completed_documents": batch.completed_documents,
                "failed_documents": batch.failed_documents,
                "partial_documents": _batch_partial_document_count(batch),
            }
            counts = (batch.completed_documents, batch.failed_documents)
            events = []
            if previous.counts is None:
                events.append(_encode({"type": "BATCH_STATUS", **status}))
            events.extend(
                _encode(_batch_document_status_event(batch, doc, metadata[doc.id]))
                for doc in changed
            )
            if previous.counts is not None and previous.counts != counts:
                events.append(_encode({"type": "BATCH_STATUS", **status}))
            terminal = batch.status in (BatchStatus.COMPLETED, BatchStatus.CANCELLED)
            if terminal:
                events.append(_encode({
                    "type": "BATCH_COMPLETE", **status,
                    "started_at": batch.started_at.isoformat() if batch.started_at else None,
                    "completed_at": batch.completed_at.isoformat() if batch.completed_at else None,
                }))
            return Snapshot(tuple(events), statuses, counts, terminal)
    except Exception as exc:
        logger.error(
            "Batch progress snapshot failed (%s)", type(exc).__name__,
            extra={"sentry_skip_event": True},
        )
        _report_failure()
        raise


async def generate_batch_stream(batch_id: UUID, user_id: int):
    broadcaster = get_batch_broadcaster()
    queue = None
    snapshot_failed = False
    try:
        queue = await broadcaster.subscribe(batch_id)
        previous = Snapshot()
        next_poll = 0.0
        interval = get_batch_stream_poll_interval_seconds()
        while True:
            if asyncio.get_running_loop().time() >= next_poll:
                snapshot_failed = True
                # Abandoning the await never closes a Session from another thread.
                # AnyIO observes worker completion; its with block closes the
                # Session and its exception handler reports even after disconnect.
                previous = await run_sync(
                    read_snapshot, batch_id, user_id, previous,
                    abandon_on_cancel=True,
                )
                snapshot_failed = False
                next_poll = asyncio.get_running_loop().time() + interval
                for event in previous.events[:-1] if previous.terminal else previous.events:
                    yield event
            while True:
                try:
                    event = _sanitize_batch_stream_event(queue.get_nowait())
                except asyncio.QueueEmpty:
                    break
                yield f"data: {json.dumps(event, default=str)}\n\n"
                if event.get("type") == "BATCH_STREAM_COMPLETE":
                    return
            if previous.terminal:
                yield previous.events[-1]
                return
            # Keep nonblocking queue polling: producer threads do not provide
            # asyncio cross-thread wakeup guarantees.
            await asyncio.sleep(0.1)
    except Exception as exc:
        if not snapshot_failed:
            logger.error(
                "Batch progress stream failed (%s)", type(exc).__name__,
                extra={"sentry_skip_event": True},
            )
            _report_failure()
        yield _encode({"type": "ERROR", "message": "Batch stream encountered an internal error"})
    finally:
        if queue is not None:
            with anyio.CancelScope(shield=True):
                await broadcaster.unsubscribe(batch_id, queue)


class BatchProgressResponse(StreamingResponse):
    """Close the subscription even when transport cancellation occurs at send."""

    def __init__(self, batch_id: UUID, user_id: int):
        self.stream = generate_batch_stream(batch_id, user_id)
        super().__init__(
            self.stream,
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            with anyio.CancelScope(shield=True):
                await self.stream.aclose()
