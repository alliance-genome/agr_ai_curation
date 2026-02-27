"""Unit tests for batch SSE event broadcaster."""

import asyncio
from uuid import uuid4

import pytest

from src.lib.batch import events


@pytest.mark.asyncio
async def test_subscribe_publish_and_unsubscribe_round_trip():
    broadcaster = events.BatchEventBroadcaster(queue_maxsize=10)
    batch_id = uuid4()

    queue = await broadcaster.subscribe(batch_id)
    assert broadcaster.has_subscribers(batch_id) is True

    event = {"type": "DOCUMENT_STATUS", "status": "processing"}
    broadcaster.publish(batch_id, event)

    received = queue.get_nowait()
    assert received == event

    await broadcaster.unsubscribe(batch_id, queue)
    assert broadcaster.has_subscribers(batch_id) is False


@pytest.mark.asyncio
async def test_unsubscribe_ignores_unknown_queue():
    broadcaster = events.BatchEventBroadcaster(queue_maxsize=10)
    batch_id = uuid4()
    queue = await broadcaster.subscribe(batch_id)

    unknown_queue = asyncio.Queue()
    await broadcaster.unsubscribe(batch_id, unknown_queue)
    assert broadcaster.has_subscribers(batch_id) is True

    await broadcaster.unsubscribe(batch_id, queue)
    assert broadcaster.has_subscribers(batch_id) is False


@pytest.mark.asyncio
async def test_publish_no_subscribers_is_noop():
    broadcaster = events.BatchEventBroadcaster(queue_maxsize=10)
    batch_id = uuid4()

    # Should not raise
    broadcaster.publish(batch_id, {"type": "PING"})
    broadcaster.publish_sync(batch_id, {"type": "PING_SYNC"})

    assert broadcaster.has_subscribers(batch_id) is False


@pytest.mark.asyncio
async def test_publish_drops_when_queue_is_full():
    broadcaster = events.BatchEventBroadcaster(queue_maxsize=1)
    batch_id = uuid4()
    queue = await broadcaster.subscribe(batch_id)

    first_event = {"type": "FIRST"}
    second_event = {"type": "SECOND"}
    broadcaster.publish(batch_id, first_event)
    broadcaster.publish(batch_id, second_event)

    assert queue.qsize() == 1
    assert queue.get_nowait() == first_event


@pytest.mark.asyncio
async def test_publish_sync_to_multiple_subscribers():
    broadcaster = events.BatchEventBroadcaster(queue_maxsize=10)
    batch_id = uuid4()
    queue_one = await broadcaster.subscribe(batch_id)
    queue_two = await broadcaster.subscribe(batch_id)

    event = {"type": "BATCH_STATUS", "completed_documents": 1}
    broadcaster.publish_sync(batch_id, event)

    assert queue_one.get_nowait() == event
    assert queue_two.get_nowait() == event


@pytest.mark.asyncio
async def test_publish_handles_queue_put_exceptions():
    broadcaster = events.BatchEventBroadcaster(queue_maxsize=10)
    batch_id = uuid4()

    class _BrokenQueue:
        def put_nowait(self, _event):
            raise RuntimeError("queue broken")

    # Inject broken queue directly to exercise defensive exception handling.
    broadcaster._subscribers[str(batch_id)].append(_BrokenQueue())  # type: ignore[attr-defined]

    # Should not raise even if queue write fails.
    broadcaster.publish(batch_id, {"type": "ANY"})
    broadcaster.publish_sync(batch_id, {"type": "ANY_SYNC"})


@pytest.mark.asyncio
async def test_publish_completion_emits_stream_complete():
    broadcaster = events.BatchEventBroadcaster(queue_maxsize=10)
    batch_id = uuid4()
    queue = await broadcaster.subscribe(batch_id)

    await broadcaster.publish_completion(batch_id)
    event = queue.get_nowait()

    assert event["type"] == "BATCH_STREAM_COMPLETE"
    assert event["batch_id"] == str(batch_id)


def test_get_batch_broadcaster_returns_singleton(monkeypatch):
    monkeypatch.setattr(events, "_broadcaster", None)

    first = events.get_batch_broadcaster()
    second = events.get_batch_broadcaster()

    assert first is second
