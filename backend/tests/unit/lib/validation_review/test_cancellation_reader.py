"""Cancellation readers own their transport, including ephemeral batch loops."""
import asyncio
from unittest.mock import Mock

import pytest

from src.lib import redis_client
from src.lib.validation_review.chat import ChatReview
from src.lib.validation_review.stops import ReviewInterrupted


class LoopBoundClient:
    def __init__(self, value=None, read_error=None, close_error=None):
        self.loop = asyncio.get_running_loop()
        self.value = value
        self.read_error = read_error
        self.close_error = close_error
        self.reads = []
        self.closed = 0
        self.entered = asyncio.Event()

    async def get(self, key):
        assert asyncio.get_running_loop() is self.loop
        assert self.closed == 0
        self.reads.append(key)
        self.entered.set()
        if self.read_error:
            raise self.read_error
        return self.value

    async def aclose(self):
        assert asyncio.get_running_loop() is self.loop
        self.closed += 1
        if self.close_error:
            raise self.close_error


def test_reader_owns_each_sequential_worker_loop_without_touching_http(monkeypatch):
    clients = []

    def factory():
        client = LoopBoundClient(value="1")
        clients.append(client)
        return client

    monkeypatch.setattr(redis_client, "_create_redis_client", factory)

    async def document():
        async with redis_client.cancellation_signal_reader("synthetic") as read:
            assert await read()
            assert await read()

    def documents():
        asyncio.run(document())
        asyncio.run(document())

    async def http():
        shared = LoopBoundClient(value="http")
        monkeypatch.setattr(redis_client, "_redis_client", shared)
        worker = asyncio.create_task(asyncio.to_thread(documents))
        assert await (await redis_client.get_redis()).get("health") == "http"
        await worker
        assert await (await redis_client.get_redis()).get("health") == "http"
        assert redis_client._redis_client is shared
        assert shared.closed == 0
        await shared.aclose()

    asyncio.run(http())
    assert len(clients) == 2
    assert clients[0].loop is not clients[1].loop
    assert all(client.loop.is_closed() for client in clients)
    assert all(client.closed == 1 for client in clients)
    assert all(client.reads == ["chat:cancel:synthetic"] * 2 for client in clients)


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["success", "stop", "work_error", "parent_cancel", "read_error", "close_error"])
async def test_review_closes_reader_and_joins_work(monkeypatch, outcome):
    failure = RuntimeError("fake sensitive cancellation failure")
    client = LoopBoundClient(
        value="1" if outcome == "stop" else None,
        read_error=failure if outcome == "read_error" else None,
        close_error=failure if outcome == "close_error" else None,
    )
    monkeypatch.setattr(redis_client, "_create_redis_client", lambda: client)
    report = Mock()
    monkeypatch.setattr("src.lib.validation_review.audit.report_operational_failure", report)
    finished = asyncio.Event()

    async def work():
        try:
            await client.entered.wait()
            if outcome in {"stop", "parent_cancel", "read_error"}:
                await asyncio.Event().wait()
            if outcome == "work_error":
                raise ValueError("work failed")
            return "complete"
        finally:
            finished.set()

    context = ChatReview("owner", "doc", "session", "turn", None, (), "review")
    task = asyncio.create_task(context.until_stop(work()))
    await client.entered.wait()
    if outcome == "parent_cancel":
        task.cancel()
    expected = {
        "stop": ReviewInterrupted,
        "parent_cancel": asyncio.CancelledError,
        "work_error": ValueError,
        "read_error": RuntimeError,
        "close_error": RuntimeError,
    }
    if outcome in expected:
        with pytest.raises(expected[outcome]):
            await asyncio.wait_for(task, 2)
    else:
        assert await asyncio.wait_for(task, 2) == "complete"
    assert finished.is_set()
    assert client.closed == 1
    assert context.interrupted is (outcome == "stop")
    if outcome in {"read_error", "close_error"}:
        report.assert_called_once_with(failure)
    else:
        report.assert_not_called()


@pytest.mark.asyncio
async def test_watcher_failure_uses_existing_sanitized_reporter(monkeypatch):
    sensitive = "Bearer sk-FAKE-secret document text should never enter the report"
    client = LoopBoundClient(read_error=RuntimeError(sensitive))
    monkeypatch.setattr(redis_client, "_create_redis_client", lambda: client)
    captured = []

    def capture(error, **kwargs):
        captured.append((str(error), kwargs, error.__cause__, error.__context__))
        return True

    monkeypatch.setattr("src.lib.observability.runtime.report_runtime_exception", capture)
    context = ChatReview("owner", "doc", "session", "turn", None, (), "review")
    with pytest.raises(RuntimeError, match="FAKE-secret"):
        await context.until_stop(asyncio.Event().wait())
    from src.lib.validation_review.audit import report_operational_failure
    # The outer flow owner can see the same failure without a duplicate event.
    report_operational_failure(client.read_error)
    assert captured == [("Validation review operational failure (RuntimeError)",
                         {"component": "validation_review", "operation": "execute_review_work"}, None, None)]
    assert sensitive not in repr(captured)
    assert client.closed == 1


@pytest.mark.asyncio
async def test_cancellation_during_close_still_joins_connection_cleanup(monkeypatch):
    closing = asyncio.Event()
    release_close = asyncio.Event()
    closed = asyncio.Event()

    class SlowCloseClient(LoopBoundClient):
        async def aclose(self):
            closing.set()
            await release_close.wait()
            await super().aclose()
            closed.set()

    client = SlowCloseClient()
    monkeypatch.setattr(redis_client, "_create_redis_client", lambda: client)

    async def use_reader():
        async with redis_client.cancellation_signal_reader("session") as read:
            assert not await read()

    task = asyncio.create_task(use_reader())
    await closing.wait()
    task.cancel()
    await asyncio.sleep(0)
    release_close.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed.is_set()
    assert client.closed == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["repeat_cancel", "read_failure"])
async def test_owner_drains_close_and_preserves_read_failure(monkeypatch, mode):
    closing = asyncio.Event()
    release_close = asyncio.Event()
    closed = asyncio.Event()
    watcher = None
    failure = RuntimeError("read failed before close")

    class SlowCloseClient(LoopBoundClient):
        async def get(self, key):
            nonlocal watcher
            watcher = asyncio.current_task()
            return await super().get(key)

        async def aclose(self):
            closing.set()
            await release_close.wait()
            await super().aclose()
            closed.set()

    client = SlowCloseClient(
        value="1", read_error=failure if mode == "read_failure" else None
    )
    monkeypatch.setattr(redis_client, "_create_redis_client", lambda: client)
    report = Mock()
    monkeypatch.setattr("src.lib.validation_review.audit.report_operational_failure", report)

    async def work():
        await closing.wait()
        return "work finished during close"

    context = ChatReview("owner", "doc", "session", "turn", None, (), "review")
    owner = asyncio.create_task(context.until_stop(work()))

    async def internal_cancel_started():
        await closing.wait()
        while not watcher.cancelling():
            await asyncio.sleep(0)

    try:
        await asyncio.wait_for(internal_cancel_started(), 2)
        if mode == "repeat_cancel":
            for _ in range(3):
                owner.cancel()
                await asyncio.sleep(0)
        assert not owner.done(), "Owner left before Redis closure completed"
        release_close.set()
        expected = asyncio.CancelledError if mode == "repeat_cancel" else RuntimeError
        with pytest.raises(expected):
            await asyncio.wait_for(owner, 2)
        assert closed.is_set()
        assert client.closed == 1
        assert not context.interrupted
        if mode == "read_failure":
            report.assert_called_once_with(failure)
        else:
            report.assert_not_called()
    finally:
        release_close.set()
        if not owner.done():
            owner.cancel()
        await asyncio.gather(owner, return_exceptions=True)
