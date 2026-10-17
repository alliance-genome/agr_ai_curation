"""Real ASGI lifetimes with a deliberately scarce, instrumented SQL pool."""
import asyncio
import json
import threading
from types import SimpleNamespace
from uuid import uuid4

import fastapi
import pytest
import sqlalchemy
import starlette
from fastapi import FastAPI
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import QueuePool
from starlette.concurrency import run_in_threadpool

from src.api import batch as api
from src.lib.batch import streaming
from src.lib.batch.events import BatchEventBroadcaster
from src.models.sql.batch import BatchStatus, BatchDocumentStatus


async def until(predicate):
    async with asyncio.timeout(5):
        while not predicate():
            await asyncio.sleep(0.01)


@pytest.fixture
def harness(monkeypatch, tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'pool.db'}", poolclass=QueuePool,
        pool_size=1, max_overflow=0, pool_timeout=1,
        connect_args={"check_same_thread": False},
    )
    class OwnedSession(Session):
        def __init__(self, *args, **kwargs):
            self.owner_thread = threading.get_ident()
            super().__init__(*args, **kwargs)

        def execute(self, *args, **kwargs):
            assert threading.get_ident() == self.owner_thread
            return super().execute(*args, **kwargs)

        def close(self):
            assert threading.get_ident() == self.owner_thread
            return super().close()

    factory = sessionmaker(bind=engine, class_=OwnedSession)
    threads = []
    balances = []
    event.listen(engine, "checkout", lambda *_: balances.append(1))
    event.listen(engine, "checkin", lambda *_: balances.append(-1))
    batch = SimpleNamespace(
        id=uuid4(), status=BatchStatus.RUNNING, total_documents=1,
        completed_documents=0, failed_documents=0, documents=[],
        started_at=None, completed_at=None,
    )
    state = SimpleNamespace(
        batch=batch, queries=0, metadata=[], block=False, fail=None,
        entered=threading.Event(), release=threading.Event(), reports=[],
    )

    class Service:
        def __init__(self, db):
            self.db = db

        def get_batch(self, batch_id, owner_id):
            threads.append(threading.get_ident())
            self.db.execute(text("select 1")).scalar_one()
            state.queries += 1
            if state.block:
                state.entered.set()
                assert state.release.wait(5), "test did not release worker"
            if state.fail == "query":
                raise RuntimeError("fake-password curator-content")
            return state.batch if owner_id == 7 and batch_id == batch.id else None

        def get_documents_handoff_metadata(self, batch, docs):
            state.metadata.append([doc.id for doc in docs])
            if state.fail == "metadata":
                raise ValueError("fake-password malformed authoritative reference")
            return {doc.id: {"adapter_keys": ["test"], "extraction_result_ids": ["result"]} for doc in docs}

    async def provision(_principal):
        def work():
            with factory() as db:
                db.execute(text("select 1"))
                return SimpleNamespace(id=7)
        return await run_in_threadpool(work)

    monkeypatch.setattr(streaming, "SessionLocal", factory)
    monkeypatch.setattr(streaming, "BatchService", Service)
    monkeypatch.setattr(api, "provision_user_async", provision)
    monkeypatch.setattr(streaming, "get_batch_stream_poll_interval_seconds", lambda: 0.01)
    monkeypatch.setattr(streaming, "report_runtime_exception",
                        lambda exc, **kw: state.reports.append((exc, kw)))
    broadcaster = BatchEventBroadcaster(queue_maxsize=1)
    monkeypatch.setattr(streaming, "get_batch_broadcaster", lambda: broadcaster)
    app = FastAPI()
    app.include_router(api.router)
    route = next(r for r in api.router.routes if r.path.endswith("/stream"))
    app.dependency_overrides[route.dependant.dependencies[0].call] = lambda: {"sub": "test"}
    yield SimpleNamespace(
        engine=engine, factory=factory, balances=balances, threads=threads,
        state=state, app=app, broadcaster=broadcaster, batch=batch,
    )
    state.release.set()
    engine.dispose()


class Connection:
    """ASGI transport that leaves the response open and can pause each body send."""
    def __init__(self, h, batch_id=None, pause=True):
        self.messages = []
        self.disconnect = asyncio.Event()
        self.resume = asyncio.Event()
        if not pause:
            self.resume.set()
        scope = {
            "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1", "method": "GET", "scheme": "http",
            "path": f"/api/batches/{batch_id or h.batch.id}/stream",
            "raw_path": b"/", "query_string": b"", "headers": [],
            "client": ("127.0.0.1", 123), "server": ("test", 80),
        }

        async def receive():
            await self.disconnect.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            self.messages.append(message)
            if message["type"] == "http.response.body" and message.get("body"):
                await self.resume.wait()

        self.task = asyncio.create_task(h.app(scope, receive, send))

    @property
    def bodies(self):
        return [m["body"] for m in self.messages if m["type"] == "http.response.body" and m.get("body")]

    async def close(self):
        self.disconnect.set()
        await asyncio.wait_for(self.task, 5)


@pytest.mark.asyncio
async def test_open_asgi_viewers_release_one_connection_pool(harness):
    h = harness
    print(f"runtime fastapi={fastapi.__version__} starlette={starlette.__version__} sqlalchemy={sqlalchemy.__version__}")
    assert fastapi.__version__ == "0.136.3"
    clients = []
    try:
        for _ in range(3):
            c = Connection(h)
            clients.append(c)
            await until(lambda: c.bodies)
            assert not c.task.done()
            assert h.engine.pool.checkedout() == 0
            assert sum(h.balances) == 0
        def unrelated():
            with h.factory() as db:
                return db.execute(text("select 42")).scalar_one()
        assert await run_in_threadpool(unrelated) == 42
        assert all(t != threading.get_ident() for t in h.threads)
    finally:
        for c in clients:
            await c.close()
    assert not h.broadcaster._subscribers
    assert h.engine.pool.checkedout() == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("late_failure", [False, True])
async def test_disconnect_during_worker_releases_eventually(harness, late_failure):
    h = harness
    c = Connection(h)
    await until(lambda: c.bodies)
    h.state.block = True
    c.resume.set()
    await until(h.state.entered.is_set)
    # The event loop remains responsive while the worker owns the only connection.
    assert h.engine.pool.checkedout() == 1
    await c.close()
    assert not h.broadcaster._subscribers
    queries = h.state.queries
    if late_failure:
        h.state.fail = "query"
    h.state.release.set()
    await until(lambda: h.engine.pool.checkedout() == 0)
    await asyncio.sleep(0.15)
    assert h.state.queries == queries
    assert len(h.state.reports) == int(late_failure)
    assert sum(h.balances) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("other_owner", [False, True])
async def test_asgi_404_before_stream(harness, monkeypatch, other_owner):
    h = harness
    if other_owner:
        async def provision(_):
            return SimpleNamespace(id=99)
        monkeypatch.setattr(api, "provision_user_async", provision)
    c = Connection(h, batch_id=h.batch.id if other_owner else uuid4(), pause=False)
    await asyncio.wait_for(c.task, 5)
    assert c.messages[0]["status"] == 404
    assert not h.broadcaster._subscribers
    assert h.engine.pool.checkedout() == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", [BatchStatus.COMPLETED, BatchStatus.CANCELLED, None])
async def test_durable_reconciliation_despite_dropped_events(harness, terminal):
    h = harness
    c = Connection(h)
    await until(lambda: c.bodies)
    h.broadcaster.publish(h.batch.id, {"type": "TOOL_COMPLETE", "internal": {"secret": "fake-password"}})
    h.broadcaster.publish(h.batch.id, {"type": "BATCH_STREAM_COMPLETE"})  # dropped
    if terminal:
        h.batch.status = terminal
        h.batch.completed_documents = 1
        h.batch.documents = [document()]
    else:
        h.state.batch = None
    c.resume.set()
    await asyncio.wait_for(c.task, 5)
    body = b"".join(c.bodies)
    assert b"internal" not in body and b"fake-password" not in body
    assert b"BATCH_STREAM_COMPLETE" not in body
    if terminal:
        assert b"BATCH_COMPLETE" in body and b"DOCUMENT_STATUS" in body
    else:
        assert b"Batch not found" in body
    assert h.engine.pool.checkedout() == 0
    assert not h.broadcaster._subscribers


def document():
    return SimpleNamespace(
        id=uuid4(), document_id=uuid4(), position=0,
        status=BatchDocumentStatus.COMPLETED, result_files=[],
        output_status="partial", output_branches=[{"status": "partial"}],
        review_session_ids=["authoritative"], error_message=None, processing_time_ms=1,
    )


@pytest.mark.asyncio
async def test_snapshot_delta_metadata_and_serialized_payload(harness):
    h = harness
    doc = document()
    unchanged = document()
    h.batch.documents = [doc, unchanged]
    previous = streaming.Snapshot()
    for expected_reads in (1, 1, 2):
        if expected_reads == 2:
            doc.status = BatchDocumentStatus.FAILED
        previous = await run_in_threadpool(streaming.read_snapshot, h.batch.id, 7, previous)
        assert len(h.state.metadata) == expected_reads
        assert h.engine.pool.checkedout() == 0
    assert h.state.queries == 3
    assert h.state.metadata == [[doc.id, unchanged.id], [doc.id]]
    initial = await run_in_threadpool(streaming.read_snapshot, h.batch.id, 7, streaming.Snapshot())
    events = [json.loads(e.removeprefix("data: ")) for e in initial.events]
    payload = next(e for e in events if e["type"] == "DOCUMENT_STATUS")
    assert payload["review_session_ids"] == ["authoritative"]
    assert payload["output_branches"] == [{"status": "partial"}]
    assert payload["adapter_keys"] == ["test"]
    assert events[0]["partial_documents"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["query", "metadata", "serialization"])
async def test_worker_failures_close_and_report_sanitized(harness, failure):
    h = harness
    c = Connection(h)
    await until(lambda: c.bodies)
    h.batch.documents = [document()]
    h.state.fail = failure
    if failure == "serialization":
        h.batch.documents[0].error_message = object()
    c.resume.set()
    await asyncio.wait_for(c.task, 5)
    assert b"internal error" in b"".join(c.bodies)
    assert b"fake-password" not in b"".join(c.bodies)
    assert len(h.state.reports) == 1
    exc, context = h.state.reports[0]
    assert "fake-password" not in str(exc)
    assert exc.__context__ is None and exc.__cause__ is None
    assert exc.__traceback__ is not None
    assert context == {"component": "batch_stream", "operation": "stream_failed"}
    assert h.engine.pool.checkedout() == 0
    assert not h.broadcaster._subscribers


@pytest.mark.asyncio
async def test_task_cancellation_while_sending_closes_subscription(harness):
    h = harness
    c = Connection(h)
    await until(lambda: c.bodies)
    c.task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await c.task
    assert not h.broadcaster._subscribers
    assert h.engine.pool.checkedout() == 0


@pytest.mark.asyncio
async def test_initial_terminal_snapshot_preserves_queued_audit_order(harness, monkeypatch):
    h = harness
    h.batch.status = BatchStatus.CANCELLED
    broadcaster = BatchEventBroadcaster(queue_maxsize=3)
    original_subscribe = broadcaster.subscribe

    async def subscribe(batch_id):
        queue = await original_subscribe(batch_id)
        for index in range(3):
            queue.put_nowait({"type": "TOOL_COMPLETE", "index": index})
        return queue

    monkeypatch.setattr(broadcaster, "subscribe", subscribe)
    monkeypatch.setattr(streaming, "get_batch_broadcaster", lambda: broadcaster)
    c = Connection(h, pause=False)
    await asyncio.wait_for(c.task, 5)
    events = [json.loads(body.removeprefix(b"data: ")) for body in c.bodies]
    assert [e["index"] for e in events if e["type"] == "TOOL_COMPLETE"] == [0, 1, 2]
    assert events[-1]["type"] == "BATCH_COMPLETE"
    assert not broadcaster._subscribers


@pytest.mark.asyncio
async def test_send_failure_closes_suspended_generator(harness):
    from starlette.requests import ClientDisconnect
    h = harness
    response = await api.stream_batch_progress(h.batch.id, {"sub": "test"})

    async def send(_message):
        if _message["type"] == "http.response.body":
            raise OSError("peer disconnected")

    async def receive():
        raise AssertionError("ASGI 2.4 disconnect is signalled by send")

    with pytest.raises(ClientDisconnect):
        await response({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send)
    assert not h.broadcaster._subscribers
    assert h.engine.pool.checkedout() == 0
