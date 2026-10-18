"""ZIP SQL, worker and ASGI ownership contracts (no external services)."""
import asyncio
from contextlib import contextmanager
import io
import tempfile
import threading
from types import SimpleNamespace
from uuid import uuid4
import zipfile

import anyio
import pytest
from fastapi import HTTPException
from sqlalchemy.dialects import postgresql

from src.lib.batch import download as downloads
from src.models.sql.batch import BatchDocumentStatus


def doc(*ids, status=BatchDocumentStatus.COMPLETED):
    return SimpleNamespace(id=uuid4(), status=status,
                           result_files=[{"file_id": str(identity)} for identity in ids])


def install_db(monkeypatch, documents, rows=(), statements=None):
    """Capture actual query expressions, supplying deliberately unordered SQL rows."""
    from sqlalchemy import select
    state = SimpleNamespace(open=False, thread=None)

    class Query:
        def __init__(self, columns):
            self.statement = select(*columns)

        def filter(self, *predicates):
            self.statement = self.statement.where(*predicates)
            return self

        def all(self):
            assert state.open
            compiled = self.statement.compile(dialect=postgresql.dialect())
            if statements is not None:
                statements.append(compiled)
            params = compiled.params
            ids = params["id_1"]
            owner = params["curator_id_1"]
            return [r for r in reversed(rows) if r.id in ids and r.curator_id == owner]

    @contextmanager
    def session():
        assert not state.open
        state.open = True
        state.thread = threading.get_ident()
        try:
            yield SimpleNamespace(query=lambda *columns: Query(columns))
        finally:
            assert state.thread == threading.get_ident()
            state.open = False

    def get_batch(batch_id, user_id):
        assert state.open
        assert user_id == 8
        return SimpleNamespace(documents=documents)

    monkeypatch.setattr(downloads, "SessionLocal", session)
    monkeypatch.setattr(downloads, "BatchService", lambda db: SimpleNamespace(get_batch=get_batch))
    return state


def row(path, filename="results.csv", owner="subject"):
    return SimpleNamespace(id=uuid4(), file_path=str(path), filename=filename, curator_id=owner)


def track_archives(monkeypatch):
    handles = []
    factory = tempfile.TemporaryFile

    def create(*args, **kwargs):
        handle = factory(*args, **kwargs)
        handles.append(handle)
        return handle

    monkeypatch.setattr(downloads.tempfile, "TemporaryFile", create)
    return handles


@pytest.mark.parametrize("count", [1, 3, 4, 8])
def test_bulk_sql_chunks_owner_filter_and_manifest_order(monkeypatch, count):
    rows = [row(f"/{i}") for i in range(count)]
    alien = row("/secret", owner="other")
    missing = uuid4()
    documents = [doc(rows[0].id, "invalid", rows[0].id),
                 doc(*(r.id for r in rows[1:]), alien.id, missing),
                 doc(uuid4(), status=BatchDocumentStatus.PENDING)]
    statements = []
    state = install_db(monkeypatch, documents, [*rows, alien], statements)
    monkeypatch.setenv("BATCH_ZIP_METADATA_CHUNK_SIZE", "3")
    result = downloads._read_metadata(uuid4(), 8, "subject")
    assert result == [(r.file_path, r.filename) for r in [rows[0], *rows]]
    assert not state.open
    assert len(statements) == (count + 2 + 2) // 3
    queried = []
    for statement in statements:
        assert "file_outputs.curator_id =" in str(statement)
        assert "file_outputs.id IN" in str(statement)
        assert statement.params["curator_id_1"] == "subject"
        queried.extend(statement.params["id_1"])
        assert len(statement.params["id_1"]) <= 3
    assert len(queried) == len(set(queried)) == count + 2


@pytest.mark.parametrize("manifest,detail", [([], "No completed documents with results to download"),
                                           ([{"file_id": "bad"}], "No downloadable result files available for this batch")])
def test_empty_and_invalid_ids(monkeypatch, tmp_path, manifest, detail):
    document = doc()
    document.result_files = manifest
    install_db(monkeypatch, [document])
    monkeypatch.setattr(downloads, "FileOutputStorageService", lambda: SimpleNamespace(base_path=tmp_path))
    handles = track_archives(monkeypatch)
    with pytest.raises(HTTPException) as error:
        downloads.build_batch_zip(uuid4(), 8, "subject")
    assert error.value.detail == detail
    assert all(h.closed for h in handles)


@pytest.mark.parametrize("manifest", [{}, "bad", [None]])
def test_corrupt_manifest_raises_before_archive(monkeypatch, manifest):
    document = doc()
    document.result_files = manifest
    install_db(monkeypatch, [document])
    handles = track_archives(monkeypatch)
    with pytest.raises(ValueError, match="canonical result_files"):
        downloads.build_batch_zip(uuid4(), 8, "subject")
    assert not handles


def test_paths_order_bytes_duplicates_and_closed_sql(monkeypatch, tmp_path):
    root = tmp_path / "outputs"
    root.mkdir()
    valid = root / "ok.csv"
    valid.write_bytes(b"a,b\n1,2\n")
    secret = tmp_path / "secret"
    secret.write_bytes(b"secret")
    (root / "escape").symlink_to(secret)
    rows = [row(valid), row(valid, "RESULTS.CSV"), row(root / "../secret"),
            row(root / "escape"), row(root / "missing"), row(secret, owner="other")]
    state = install_db(monkeypatch, [doc(rows[0].id, rows[0].id), doc(*(r.id for r in rows[1:]))], rows)
    def storage():
        assert not state.open
        return SimpleNamespace(base_path=root)
    monkeypatch.setattr(downloads, "FileOutputStorageService", storage)
    with downloads.build_batch_zip(uuid4(), 8, "subject") as handle:
        with zipfile.ZipFile(handle) as archive:
            assert archive.namelist() == ["results.csv", "results_2.csv", "RESULTS_3.CSV"]
            assert all(archive.read(name) == valid.read_bytes() for name in archive.namelist())


def test_partial_entry_failure_rolls_back_and_reports_sanitized(monkeypatch, tmp_path):
    source = tmp_path / "data"
    source.write_bytes(b"unchanged")
    monkeypatch.setattr(downloads, "_read_metadata", lambda *args: [(str(source), "bad.csv"), (str(source), "good.csv")])
    monkeypatch.setattr(downloads, "FileOutputStorageService", lambda: SimpleNamespace(base_path=tmp_path))
    reports = []
    monkeypatch.setattr(downloads, "report_runtime_exception", lambda exc, **kw: reports.append((exc, kw)))
    original = zipfile.ZipFile.write
    def write(self, path, arcname):
        if arcname == "bad.csv":
            self.writestr(arcname, b"partial")
            raise OSError("fake-secret-token /sensitive/document")
        return original(self, path, arcname)
    monkeypatch.setattr(zipfile.ZipFile, "write", write)
    with downloads.build_batch_zip(uuid4(), 8, "subject") as handle:
        with zipfile.ZipFile(handle) as archive:
            assert archive.namelist() == ["good.csv"]
            assert archive.read("good.csv") == b"unchanged"
    assert len(reports) == 1
    exc, context = reports[0]
    assert context == {"component": "batch_zip", "operation": "entry_write_failed"}
    assert "fake-secret" not in str(exc)
    assert exc.__context__ is exc.__cause__ is None


@pytest.mark.parametrize("failure", ["finalize", "seek", "response"])
@pytest.mark.asyncio
async def test_outer_failures_close_archive(monkeypatch, tmp_path, failure):
    source = tmp_path / "data"
    source.write_bytes(b"test")
    monkeypatch.setattr(downloads, "_read_metadata", lambda *args: [(str(source), "data")])
    monkeypatch.setattr(downloads, "FileOutputStorageService", lambda: SimpleNamespace(base_path=tmp_path))
    handles = track_archives(monkeypatch)
    if failure == "finalize":
        monkeypatch.setattr(zipfile.ZipFile, "_write_end_record", lambda self: (_ for _ in ()).throw(OSError("finalize")))
    elif failure == "seek":
        original = zipfile.ZipFile.close
        def close(self):
            original(self)
            handles[0].close()
        monkeypatch.setattr(zipfile.ZipFile, "close", close)
    else:
        monkeypatch.setattr(downloads, "BatchZipResponse", lambda *args: (_ for _ in ()).throw(RuntimeError("response")))
    with pytest.raises((OSError, ValueError, RuntimeError)):
        await downloads.prepare_batch_zip(uuid4(), 8, "subject")
    assert handles and all(h.closed for h in handles)
    assert not downloads._pending_builds


@pytest.mark.asyncio
async def test_worker_progress_limit_cancellation_and_late_close(monkeypatch, tmp_path):
    limiter = anyio.to_thread.current_default_thread_limiter()
    previous = limiter.total_tokens
    limiter.total_tokens = 1
    entered = asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()
    main_thread = threading.get_ident()
    calls = []
    handles = []
    def build(*args):
        assert threading.get_ident() != main_thread
        calls.append(args)
        handle = tempfile.TemporaryFile()
        handles.append(handle)
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(10)
        assert not handle.closed
        return handle
    monkeypatch.setattr(downloads, "build_batch_zip", build)
    first = asyncio.create_task(downloads.prepare_batch_zip(uuid4(), 8, "subject"))
    second = None
    try:
        await asyncio.wait_for(entered.wait(), 10)
        second = asyncio.create_task(downloads.prepare_batch_zip(uuid4(), 8, "subject"))
        await asyncio.sleep(0)
        # A separate coroutine has progressed while the worker is held.
        assert len(calls) == 1
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert not handles[0].closed
        assert limiter.borrowed_tokens == 1
        release.set()
        response = await asyncio.wait_for(second, 10)
        response.archive.close()
        await asyncio.sleep(0)
        assert all(h.closed for h in handles)
        assert len(calls) == 2
        assert not downloads._pending_builds
    finally:
        release.set()
        await asyncio.gather(first, *([second] if second else []), return_exceptions=True)
        limiter.total_tokens = previous


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", ["full", "partial", "before_first", "send_failure"])
async def test_asgi_archive_lifecycle(terminal):
    archive = tempfile.TemporaryFile()
    archive.write(b"x" * (io.DEFAULT_BUFFER_SIZE * 3))
    archive.seek(0)
    response = downloads.BatchZipResponse(archive, uuid4())
    disconnect = asyncio.Event()
    sent = []
    async def receive():
        await disconnect.wait()
        return {"type": "http.disconnect"}
    async def send(message):
        sent.append(message)
        if message["type"] == "http.response.start":
            if terminal == "send_failure":
                raise OSError("connection lost")
            if terminal == "before_first":
                disconnect.set()
                await asyncio.Event().wait()
        elif message.get("body") and terminal == "partial":
            disconnect.set()
            await asyncio.Event().wait()
    if terminal == "send_failure":
        with pytest.raises(OSError, match="connection lost"):
            await response({"type": "http", "asgi": {"spec_version": "2.0"}}, receive, send)
    else:
        await response({"type": "http", "asgi": {"spec_version": "2.0"}}, receive, send)
    assert archive.closed
    assert response.media_type == "application/zip"
    if terminal == "full":
        assert sum(len(m.get("body", b"")) for m in sent) == io.DEFAULT_BUFFER_SIZE * 3
    if terminal == "before_first":
        assert len(sent) == 1


@pytest.mark.asyncio
async def test_abandoned_worker_failure_observed_and_redacted(monkeypatch):
    entered = asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()
    reported = asyncio.Event()
    reports = []
    def build(*args):
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(10)
        raise OSError("fake-secret /private/path")
    def report(exc, **kwargs):
        reports.append((exc, kwargs))
        reported.set()
    monkeypatch.setattr(downloads, "build_batch_zip", build)
    monkeypatch.setattr(downloads, "report_runtime_exception", report)
    task = asyncio.create_task(downloads.prepare_batch_zip(uuid4(), 8, "subject"))
    try:
        await asyncio.wait_for(entered.wait(), 10)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        release.set()
    await asyncio.wait_for(reported.wait(), 10)
    assert len(reports) == 1
    assert "fake-secret" not in str(reports[0][0])
    assert reports[0][0].__context__ is reports[0][0].__cause__ is None
    assert not downloads._pending_builds


@pytest.mark.parametrize("invalid_id", [None, 123, {}, [], "bad", ""])
def test_malformed_file_ids_are_skipped(monkeypatch, invalid_id):
    document = doc()
    document.result_files = [{"file_id": invalid_id}]
    statements = []
    install_db(monkeypatch, [document], statements=statements)
    assert downloads._read_metadata(uuid4(), 8, "subject") == []
    assert statements == []


@pytest.mark.asyncio
async def test_real_compression_session_lifetime_and_cancellation(monkeypatch, tmp_path):
    source = tmp_path / "data.tsv"
    source.write_bytes(b"a\tb\n1\t2\n")
    output = row(source, "data.tsv")
    state = install_db(monkeypatch, [doc(output.id)], [output])
    handles = track_archives(monkeypatch)
    monkeypatch.setattr(downloads, "FileOutputStorageService", lambda: SimpleNamespace(base_path=tmp_path))
    loop = asyncio.get_running_loop()
    main_thread = threading.get_ident()
    entered = asyncio.Event()
    completed = asyncio.Event()
    release = threading.Event()
    original = zipfile.ZipFile.write
    def write(self, *args, **kwargs):
        assert state.thread == threading.get_ident() != main_thread
        assert not state.open
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(10)
        assert not handles[0].closed
        return original(self, *args, **kwargs)
    original_cleanup = downloads._close_abandoned
    def cleanup(task):
        original_cleanup(task)
        completed.set()
    monkeypatch.setattr(zipfile.ZipFile, "write", write)
    monkeypatch.setattr(downloads, "_close_abandoned", cleanup)
    task = asyncio.create_task(downloads.prepare_batch_zip(uuid4(), 8, "subject"))
    try:
        await asyncio.wait_for(entered.wait(), 10)
        # Reaching here proves the loop progresses during the actual held ZIP write.
        assert not state.open
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not handles[0].closed
    finally:
        release.set()
    await asyncio.wait_for(completed.wait(), 10)
    assert handles[0].closed


def test_source_read_failure_omits_partial_member(monkeypatch, tmp_path):
    import builtins
    bad = tmp_path / "bad.csv"
    good = tmp_path / "good.json"
    bad.write_bytes(b"partial content")
    good.write_bytes(b'{"ok": true}')
    monkeypatch.setattr(downloads, "_read_metadata", lambda *args: [(str(bad), bad.name), (str(good), good.name)])
    monkeypatch.setattr(downloads, "FileOutputStorageService", lambda: SimpleNamespace(base_path=tmp_path))
    reports = []
    monkeypatch.setattr(downloads, "report_runtime_exception", lambda *args, **kwargs: reports.append(kwargs))
    original_open = builtins.open

    class BrokenReader:
        def __init__(self):
            self.file = original_open(bad, "rb")
            self.read_once = False

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.file.close()

        def read(self, size):
            if self.read_once:
                raise OSError("fake-sensitive-read-error")
            self.read_once = True
            return self.file.read(size)

    monkeypatch.setattr(builtins, "open", lambda path, *args, **kwargs:
                        BrokenReader() if path == bad else original_open(path, *args, **kwargs))
    with downloads.build_batch_zip(uuid4(), 8, "subject") as handle:
        with zipfile.ZipFile(handle) as archive:
            assert archive.namelist() == ["good.json"]
            assert archive.read("good.json") == good.read_bytes()
    assert len(reports) == 1
