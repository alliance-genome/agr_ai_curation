"""Unit tests for sanitized Sentry reporting on unfinished file-removal cleanup (Card 3 / W-D2)."""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
import sentry_sdk
from sentry_sdk.integrations.logging import LoggingIntegration

from src.lib.document_files import file_removal
from src.lib.document_files.file_removal import FileRemoved, FileRemovalCleanupFailed, remove_paper_file
from src.lib.observability.sentry import before_send, hash_sentry_identifier


def _make_removed(document_id, file_id, *, owner_subject: str, stored_path: str) -> FileRemoved:
    return FileRemoved(
        document_id=document_id,
        file_id=file_id,
        owner_subject=owner_subject,
        chunk_range=(3, 5),
        stored_paths=(stored_path,),
        ready_file_count=1,
        ready_piece_total=3,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("redaction_enabled", ["true", "false"])
@pytest.mark.parametrize("failing_step", ["pieces", "stored_files", "piece_count", "file_row", "cache"])
async def test_cleanup_failure_reports_sanitized_sentry_event_once_with_no_sensitive_leaks(
    monkeypatch, caplog, failing_step, redaction_enabled
):
    document_id = uuid4()
    file_id = uuid4()
    fake_email = "curator.secret@example.org"
    fake_subject = f"sub-secret-{fake_email}"
    fake_filename = "Private_Curator_Supplement_S1.pdf"
    fake_path = f"/runtime/state/pdf_storage/{fake_subject}/{document_id}/{fake_filename}"
    raw_secret_error = RuntimeError(
        f"Cleanup boom for {fake_email} at {fake_path} (doc={document_id}, file={file_id})"
    )

    removed = _make_removed(
        document_id,
        file_id,
        owner_subject=fake_subject,
        stored_path=f"{fake_subject}/{document_id}/{fake_filename}",
    )
    monkeypatch.setattr(file_removal, "_mark_removed_or_busy", lambda *_args: removed)

    def _delete_chunks(*_args, **_kwargs):
        if failing_step == "pieces":
            raise raw_secret_error

    def _remove_files(*_args, **_kwargs):
        if failing_step == "stored_files":
            raise raw_secret_error

    async def _update_piece_count(*_args, **_kwargs):
        if failing_step == "piece_count":
            raise raw_secret_error

    def _delete_row(*_args, **_kwargs):
        if failing_step == "file_row":
            raise raw_secret_error

    def _invalidate_cache(*_args, **_kwargs):
        if failing_step == "cache":
            raise raw_secret_error

    monkeypatch.setattr("src.lib.weaviate_client.chunks.delete_chunk_range", _delete_chunks)
    monkeypatch.setattr("src.lib.document_files.service.remove_stored_files", _remove_files)
    monkeypatch.setattr("src.lib.pipeline.store.update_document_piece_count", _update_piece_count)
    monkeypatch.setattr(file_removal, "_delete_removed_row", _delete_row)
    monkeypatch.setattr("src.lib.document_cache.invalidate_cache", _invalidate_cache)

    captured_events: list[dict] = []

    monkeypatch.setenv("SENTRY_CONTENT_REDACTION_ENABLED", redaction_enabled)
    sentry_sdk.init(
        dsn="https://public@example.invalid/1",
        transport=captured_events.append,
        before_send=before_send,
        send_default_pii=False,
        include_local_variables=False,
        default_integrations=False,
        integrations=[LoggingIntegration(level=logging.INFO, event_level=logging.ERROR)],
    )

    caplog.set_level(logging.WARNING, logger=file_removal.__name__)
    try:
        with sentry_sdk.isolation_scope() as scope:
            # Simulate an active HTTP DELETE request whose URL embeds raw document_id and file_id.
            def _attach_http_request(event, _hint):
                event.setdefault(
                    "request",
                    {
                        "method": "DELETE",
                        "url": f"https://app.example.org/api/weaviate/documents/{document_id}/files/{file_id}",
                    },
                )
                return event

            scope.add_event_processor(_attach_http_request)
            result = await remove_paper_file(document_id, 42, file_id, weaviate_client=SimpleNamespace())
        sentry_sdk.flush(timeout=2.0)
    finally:
        sentry_sdk.init()

    expected_unfinished = failing_step != "cache"
    assert result.cleanup_finished is (not expected_unfinished)
    assert result.ready_file_count == 1

    # Exactly ONE Sentry event (no duplicate promoted-log event even at ERROR log level).
    assert len(captured_events) == 1
    event = captured_events[0]
    assert event["level"] == ("error" if expected_unfinished else "warning")
    assert event["tags"]["runtime_component"] == "document_files"
    assert event["tags"]["operation"] == "file_removal_cleanup_failed"
    assert event["fingerprint"] == ["file_removal_cleanup_failed", failing_step]
    assert "request" not in event

    runtime_ctx = event["contexts"]["runtime_exception"]
    assert runtime_ctx["document_id"] == hash_sentry_identifier(str(document_id))
    assert runtime_ctx["step"] == failing_step

    exception_values = event["exception"]["values"]
    assert len(exception_values) == 1
    assert exception_values[0]["type"] == FileRemovalCleanupFailed.__name__
    assert exception_values[0]["stacktrace"]["frames"]

    # Companion log must skip Sentry log-event promotion and carry no sensitive values.
    records = [r for r in caplog.records if r.name == file_removal.__name__]
    assert len(records) == 1
    assert getattr(records[0], "sentry_skip_event", False) is True
    assert records[0].exc_info is None

    serialized_event = json.dumps(event)
    for sensitive in (
        fake_email,
        fake_subject,
        fake_filename,
        fake_path,
        str(document_id),
        str(file_id),
        "Cleanup boom",
    ):
        assert sensitive not in serialized_event
        assert sensitive not in caplog.text


@pytest.mark.asyncio
async def test_cleanup_report_preserves_sanitized_traceback_and_survives_capture_errors(monkeypatch, caplog):
    document_id = uuid4()
    file_id = uuid4()
    removed = _make_removed(
        document_id, file_id, owner_subject="curator-sub", stored_path="curator-sub/doc/supp.md",
    )
    monkeypatch.setattr(file_removal, "_mark_removed_or_busy", lambda *_args: removed)
    monkeypatch.setattr(
        "src.lib.weaviate_client.chunks.delete_chunk_range",
        Mock(side_effect=OSError("raw storage secret /private/path/file.pdf")),
    )
    monkeypatch.setattr("src.lib.document_files.service.remove_stored_files", lambda *_a, **_k: None)

    async def _noop_piece_count(*_a, **_k):
        return None

    monkeypatch.setattr("src.lib.pipeline.store.update_document_piece_count", _noop_piece_count)
    monkeypatch.setattr("src.lib.document_cache.invalidate_cache", lambda *_a, **_k: None)

    seen_errors: list[BaseException] = []

    def _failing_report(exc, **_kwargs):
        seen_errors.append(exc)
        raise RuntimeError("sentry transport offline")

    monkeypatch.setattr("src.lib.observability.runtime.report_runtime_exception", _failing_report)
    caplog.set_level(logging.WARNING, logger=file_removal.__name__)

    result = await remove_paper_file(document_id, 42, file_id, weaviate_client=SimpleNamespace())

    assert result.cleanup_finished is False
    assert result.ready_file_count == 1
    assert len(seen_errors) == 1
    err = seen_errors[0]
    assert isinstance(err, FileRemovalCleanupFailed)
    assert str(err) == "Clearing a removed file's pieces failed (OSError)"
    assert err.__traceback__ is not None
    assert err.__cause__ is None and err.__context__ is None
    assert "/private/path/file.pdf" not in caplog.text
    assert all(getattr(r, "sentry_skip_event", False) for r in caplog.records if r.name == file_removal.__name__)


@pytest.mark.asyncio
@pytest.mark.parametrize("redaction_enabled", ["true", "false"])
@pytest.mark.parametrize("failing_step", ["pieces", "piece_count"])
async def test_real_weaviate_reconnect_failure_emits_single_sanitized_event_without_url_leak(
    monkeypatch, caplog, failing_step, redaction_enabled
):
    """Real WeaviateConnection.session(caller_reports_failure=True) avoids promoted-log URL leaks."""
    from src.lib.weaviate_client.connection import WeaviateConnection

    document_id = uuid4()
    file_id = uuid4()
    fake_subject = "sub-real-weaviate-curator@example.org"
    raw_weaviate_secret = f"Weaviate refused connection for {fake_subject} doc={document_id} file={file_id}"
    removed = _make_removed(
        document_id,
        file_id,
        owner_subject=fake_subject,
        stored_path=f"{fake_subject}/{document_id}/Secret_Supplement.pdf",
    )
    monkeypatch.setattr(file_removal, "_mark_removed_or_busy", lambda *_args: removed)
    monkeypatch.setattr("src.lib.document_files.service.remove_stored_files", lambda *_a, **_k: None)
    monkeypatch.setattr("src.lib.document_cache.invalidate_cache", lambda *_a, **_k: None)

    # Isolate WeaviateConnection singleton so later tests never see a stale test instance.
    monkeypatch.setattr(WeaviateConnection, "_instance", None)
    broken_conn = WeaviateConnection(url="http://weaviate.invalid:8080")
    monkeypatch.setattr(
        "src.lib.weaviate_client.connection.weaviate.connect_to_custom",
        Mock(side_effect=ConnectionError(raw_weaviate_secret)),
    )

    seen_caller_reports: list[bool] = []
    if failing_step == "pieces":
        def _get_broken_conn(*, caller_reports_failure: bool = False):
            seen_caller_reports.append(caller_reports_failure)
            return broken_conn

        monkeypatch.setattr("src.lib.weaviate_client.chunks.get_connection", _get_broken_conn)

        async def _ok_piece_count(*_a, **_k):
            return None

        monkeypatch.setattr("src.lib.pipeline.store.update_document_piece_count", _ok_piece_count)
        weaviate_arg = SimpleNamespace()
    else:
        monkeypatch.setattr("src.lib.weaviate_client.chunks.delete_chunk_range", lambda *_a, **_k: 0)
        weaviate_arg = broken_conn

    captured_events: list[dict] = []
    monkeypatch.setenv("SENTRY_CONTENT_REDACTION_ENABLED", redaction_enabled)
    sentry_sdk.init(
        dsn="https://public@example.invalid/1",
        transport=captured_events.append,
        before_send=before_send,
        send_default_pii=False,
        include_local_variables=False,
        default_integrations=False,
        integrations=[LoggingIntegration(level=logging.INFO, event_level=logging.ERROR)],
    )
    caplog.set_level(logging.WARNING)
    try:
        with sentry_sdk.isolation_scope() as scope:
            def _attach_delete_request(event, _hint):
                event.setdefault(
                    "request",
                    {
                        "method": "DELETE",
                        "url": f"https://app.example.org/api/weaviate/documents/{document_id}/files/{file_id}",
                    },
                )
                return event

            scope.add_event_processor(_attach_delete_request)
            result = await remove_paper_file(document_id, 42, file_id, weaviate_client=weaviate_arg)
        sentry_sdk.flush(timeout=2.0)
    finally:
        sentry_sdk.init()

    assert result.cleanup_finished is False
    assert result.cleanup_failures == [failing_step]
    if failing_step == "pieces":
        assert seen_caller_reports == [True]
    # Exactly ONE Sentry event: WeaviateConnection.session(caller_reports_failure=True)
    # must NOT emit an unmarked ERROR log that LoggingIntegration promotes with the DELETE URL.
    assert len(captured_events) == 1
    event = captured_events[0]
    assert event["tags"]["operation"] == "file_removal_cleanup_failed"
    assert event["fingerprint"] == ["file_removal_cleanup_failed", failing_step]
    assert "request" not in event
    assert event["contexts"]["runtime_exception"]["document_id"] == hash_sentry_identifier(str(document_id))
    expected_value = (
        "[Filtered]"
        if redaction_enabled == "true"
        else f"Clearing a removed file's {failing_step} failed (ConnectionError)"
    )
    assert event["exception"]["values"][0]["value"] == expected_value

    serialized = json.dumps(event)
    for sensitive in (
        str(document_id),
        str(file_id),
        fake_subject,
        "Secret_Supplement.pdf",
        "Weaviate refused connection",
        "/api/weaviate/documents/",
    ):
        assert sensitive not in serialized
        assert sensitive not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("write_fails", [False, True])
async def test_cancelled_remove_paper_file_drains_in_flight_piece_count_write_before_releasing_lock(
    monkeypatch, write_fails,
):
    import asyncio
    import threading
    from contextlib import asynccontextmanager

    document_id = uuid4()
    file_id = uuid4()
    removed = _make_removed(
        document_id, file_id, owner_subject="curator-sub", stored_path="curator-sub/doc/supp.md",
    )
    monkeypatch.setattr(file_removal, "_mark_removed_or_busy", lambda *_args, **_kwargs: removed)
    monkeypatch.setattr("src.lib.weaviate_client.chunks.delete_chunk_range", lambda *_a, **_k: 0)
    monkeypatch.setattr("src.lib.document_files.service.remove_stored_files", lambda *_a, **_k: None)
    monkeypatch.setattr(file_removal, "_delete_removed_row", lambda *_a, **_k: None)
    monkeypatch.setattr("src.lib.document_cache.invalidate_cache", lambda *_a, **_k: None)
    report = Mock(return_value=True)
    monkeypatch.setattr("src.lib.observability.runtime.report_runtime_exception", report)

    entered = asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()
    order: list[str] = []

    class _SlowCountClient:
        from contextlib import contextmanager

        @contextmanager
        def session(self, *, caller_reports_failure=False):
            assert caller_reports_failure is True
            yield object()

    class _SlowCollection:
        class _Data:
            def update(self, **_kwargs):
                loop.call_soon_threadsafe(entered.set)
                assert release.wait(10)
                order.append("piece_count_finished")
                if write_fails:
                    raise ConnectionError("private-count-failure-secret")

        data = _Data()

    monkeypatch.setattr(
        "src.lib.weaviate_helpers.get_user_collections",
        lambda _client, _user: (None, _SlowCollection()),
    )

    @asynccontextmanager
    async def tracked_paper_lock(_doc_id):
        order.append("lock_acquired")
        try:
            yield object()
        finally:
            order.append("lock_released")

    async def locked_remove():
        async with tracked_paper_lock(document_id):
            return await remove_paper_file(document_id, 42, file_id, weaviate_client=_SlowCountClient())

    task = asyncio.create_task(locked_remove())
    try:
        await asyncio.wait_for(entered.wait(), timeout=10)
        task.cancel("first-cancel")
        await asyncio.sleep(0)
        assert not task.done()
        assert order == ["lock_acquired"]
        # A second cancellation while draining must still keep the lock held.
        task.cancel("second-cancel")
        await asyncio.sleep(0)
        assert not task.done()
        assert order == ["lock_acquired"]
    finally:
        release.set()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert order == ["lock_acquired", "piece_count_finished", "lock_released"]
    if write_fails:
        report.assert_called_once()
        assert report.call_args.kwargs["operation"] == "file_removal_cleanup_failed"
        assert report.call_args.kwargs["include_request"] is False
        assert "private-count-failure-secret" not in str(report.call_args)
    else:
        report.assert_not_called()


@pytest.mark.asyncio
async def test_remove_paper_file_cancel_drains_mark_removed_worker_before_lock_release(monkeypatch):
    """Cancelling remove_paper_file() under paper_operation_lock must drain _mark_removed_or_busy before _release."""
    import asyncio
    import threading
    import src.models.sql.database as sql_db
    from src.lib.document_files.paper_lock import paper_operation_lock

    document_id = uuid4()
    file_id = uuid4()
    events: list[str] = []
    worker_entered = threading.Event()
    resume_worker = threading.Event()

    class _LockConn:
        def execute(self, statement, _params=None):
            if "pg_advisory_unlock" in str(statement):
                events.append("lock_released")
            return SimpleNamespace(scalar_one=lambda: True)

        def commit(self):
            pass

        def close(self):
            pass

        def invalidate(self):
            pass

    monkeypatch.setattr(sql_db, "engine", SimpleNamespace(connect=lambda: _LockConn()))

    def _blocking_mark_removed(*_args):
        worker_entered.set()
        assert resume_worker.wait(timeout=5)
        events.append("worker_finished")
        return _make_removed(
            document_id,
            file_id,
            owner_subject="user-1",
            stored_path=f"user-1/{document_id}/supp.pdf",
        )

    monkeypatch.setattr(file_removal, "_mark_removed_or_busy", _blocking_mark_removed)

    async def _run():
        async with paper_operation_lock(document_id):
            await remove_paper_file(document_id, 42, file_id, weaviate_client=SimpleNamespace())

    task = asyncio.create_task(_run())
    try:
        assert await asyncio.to_thread(worker_entered.wait, 5)
        task.cancel("client disconnected")
        await asyncio.sleep(0.05)
        assert "lock_released" not in events
    finally:
        resume_worker.set()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=5)

    assert events == ["worker_finished", "lock_released"]


@pytest.mark.asyncio
async def test_remove_paper_file_cancel_drains_piece_count_update_before_lock_release(monkeypatch):
    """Cancelling remove_paper_file() during piece-count update drains the worker before paper_lock._release."""
    import asyncio
    from contextlib import contextmanager
    import threading
    from unittest.mock import MagicMock
    import src.models.sql.database as sql_db
    from src.lib.document_files.paper_lock import paper_operation_lock

    document_id = uuid4()
    file_id = uuid4()
    events: list[str] = []
    update_entered = threading.Event()
    resume_update = threading.Event()

    class _LockConn:
        def execute(self, statement, _params=None):
            if "pg_advisory_unlock" in str(statement):
                events.append("lock_released")
            return SimpleNamespace(scalar_one=lambda: True)

        def commit(self):
            pass

        def close(self):
            pass

        def invalidate(self):
            pass

    monkeypatch.setattr(sql_db, "engine", SimpleNamespace(connect=lambda: _LockConn()))

    removed = _make_removed(
        document_id,
        file_id,
        owner_subject="user-1",
        stored_path=f"user-1/{document_id}/supp.pdf",
    )
    monkeypatch.setattr(file_removal, "_mark_removed_or_busy", lambda *_a, **_kw: removed)
    monkeypatch.setattr("src.lib.weaviate_client.chunks.delete_chunk_range", lambda *_a, **_kw: None)
    monkeypatch.setattr("src.lib.document_files.service.remove_stored_files", lambda *_a, **_kw: None)

    pdf_collection = MagicMock()

    def _blocking_update(**_kwargs):
        update_entered.set()
        assert resume_update.wait(timeout=5)
        events.append("piece_count_finished")

    pdf_collection.data.update.side_effect = _blocking_update
    monkeypatch.setattr(
        "src.lib.weaviate_helpers.get_user_collections",
        lambda *_a, **_kw: (MagicMock(), pdf_collection),
    )

    @contextmanager
    def _weaviate_session(*_a, **_kw):
        yield MagicMock()

    weaviate_client = MagicMock()
    weaviate_client.session.side_effect = _weaviate_session

    async def _run():
        async with paper_operation_lock(document_id) as lock_conn:
            await remove_paper_file(
                document_id,
                42,
                file_id,
                weaviate_client=weaviate_client,
                lock_connection=lock_conn,
            )

    task = asyncio.create_task(_run())
    try:
        assert await asyncio.to_thread(update_entered.wait, 5)
        task.cancel("client disconnected during piece count update")
        await asyncio.sleep(0.05)
        assert "lock_released" not in events
    finally:
        resume_update.set()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=5)

    assert events == ["piece_count_finished", "lock_released"]


@pytest.mark.asyncio
@pytest.mark.parametrize("failing_step", ["pieces", "stored_files", "piece_count", "file_row"])
async def test_remove_paper_file_cancel_reports_discarded_worker_error_before_lock_release(
    monkeypatch, caplog, failing_step,
):
    """A worker error discarded during cancellation still emits one sanitized report before lock release."""
    import asyncio
    from contextlib import contextmanager
    import threading
    from unittest.mock import MagicMock
    import src.models.sql.database as sql_db
    from src.lib.document_files.paper_lock import paper_operation_lock

    document_id = uuid4()
    file_id = uuid4()
    fake_subject = "curator.cancel@example.org"
    fake_secret = f"private-worker-detail-{document_id}-{file_id}"
    events: list[str] = []
    worker_entered = threading.Event()
    resume_worker = threading.Event()

    class _LockConn:
        def execute(self, statement, _params=None):
            if "pg_advisory_unlock" in str(statement):
                events.append("lock_released")
            return SimpleNamespace(scalar_one=lambda: True)

        def commit(self):
            pass

        def close(self):
            pass

        def invalidate(self):
            pass

    monkeypatch.setattr(sql_db, "engine", SimpleNamespace(connect=lambda: _LockConn()))

    removed = _make_removed(
        document_id,
        file_id,
        owner_subject=fake_subject,
        stored_path=f"{fake_subject}/{document_id}/Secret_Supplement.pdf",
    )
    monkeypatch.setattr(file_removal, "_mark_removed_or_busy", lambda *_a, **_kw: removed)

    def _maybe_block_and_raise(step_name: str):
        if failing_step == step_name:
            worker_entered.set()
            assert resume_worker.wait(timeout=5)
            events.append(f"{step_name}_failed")
            raise OSError(f"boom {fake_secret} {fake_subject}")

    monkeypatch.setattr(
        "src.lib.weaviate_client.chunks.delete_chunk_range",
        lambda *_a, **_kw: _maybe_block_and_raise("pieces"),
    )
    monkeypatch.setattr(
        "src.lib.document_files.service.remove_stored_files",
        lambda *_a, **_kw: _maybe_block_and_raise("stored_files"),
    )
    monkeypatch.setattr(
        file_removal,
        "_delete_removed_row",
        lambda *_a, **_kw: _maybe_block_and_raise("file_row"),
    )
    monkeypatch.setattr("src.lib.document_cache.invalidate_cache", lambda *_a, **_kw: None)

    pdf_collection = MagicMock()
    pdf_collection.data.update.side_effect = lambda **_kw: _maybe_block_and_raise("piece_count")
    monkeypatch.setattr(
        "src.lib.weaviate_helpers.get_user_collections",
        lambda *_a, **_kw: (MagicMock(), pdf_collection),
    )

    @contextmanager
    def _weaviate_session(*_a, **_kw):
        yield MagicMock()

    weaviate_client = MagicMock()
    weaviate_client.session.side_effect = _weaviate_session

    captured_events: list[dict] = []

    def _record_transport(event: dict) -> None:
        events.append("reported")
        captured_events.append(event)

    monkeypatch.setenv("SENTRY_CONTENT_REDACTION_ENABLED", "false")
    sentry_sdk.init(
        dsn="https://public@example.invalid/1",
        transport=_record_transport,
        before_send=before_send,
        send_default_pii=False,
        include_local_variables=False,
        default_integrations=False,
        integrations=[LoggingIntegration(level=logging.INFO, event_level=logging.ERROR)],
    )

    caplog.set_level(logging.WARNING, logger=file_removal.__name__)
    try:
        with sentry_sdk.isolation_scope() as scope:
            def _attach_request(event, _hint):
                event.setdefault(
                    "request",
                    {
                        "method": "DELETE",
                        "url": f"https://app.example.org/api/weaviate/documents/{document_id}/files/{file_id}",
                    },
                )
                return event

            scope.add_event_processor(_attach_request)

            async def _run():
                async with paper_operation_lock(document_id) as lock_conn:
                    await remove_paper_file(
                        document_id,
                        42,
                        file_id,
                        weaviate_client=weaviate_client,
                        lock_connection=lock_conn,
                    )

            task = asyncio.create_task(_run())
            try:
                assert await asyncio.to_thread(worker_entered.wait, 5)
                task.cancel("client disconnected during cleanup step")
                await asyncio.sleep(0.05)
                assert "lock_released" not in events
            finally:
                resume_worker.set()

            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=5)
        sentry_sdk.flush(timeout=2.0)
    finally:
        sentry_sdk.init()

    assert events == [f"{failing_step}_failed", "reported", "lock_released"]
    assert len(captured_events) == 1
    event = captured_events[0]
    assert event["tags"]["runtime_component"] == "document_files"
    assert event["tags"]["operation"] == "file_removal_cleanup_failed"
    assert event["fingerprint"] == ["file_removal_cleanup_failed", failing_step]
    assert "request" not in event
    assert event["contexts"]["runtime_exception"]["document_id"] == hash_sentry_identifier(str(document_id))
    assert event["contexts"]["runtime_exception"]["step"] == failing_step

    serialized = json.dumps(event)
    for sensitive in (str(document_id), str(file_id), fake_subject, fake_secret, "Secret_Supplement.pdf"):
        assert sensitive not in serialized
        assert sensitive not in caplog.text


@pytest.mark.asyncio
async def test_remove_paper_file_cancel_preserves_cancellation_and_unlock_when_reporter_raises(monkeypatch):
    """If reporting a discarded worker error itself raises, CancelledError and lock unlock still complete cleanly."""
    import asyncio
    from contextlib import contextmanager
    import threading
    from unittest.mock import MagicMock
    import src.models.sql.database as sql_db
    from src.lib.document_files.paper_lock import paper_operation_lock

    document_id = uuid4()
    file_id = uuid4()
    events: list[str] = []
    update_entered = threading.Event()
    resume_update = threading.Event()

    class _LockConn:
        def execute(self, statement, _params=None):
            if "pg_advisory_unlock" in str(statement):
                events.append("lock_released")
            return SimpleNamespace(scalar_one=lambda: True)

        def commit(self):
            pass

        def close(self):
            pass

        def invalidate(self):
            pass

    monkeypatch.setattr(sql_db, "engine", SimpleNamespace(connect=lambda: _LockConn()))

    removed = _make_removed(
        document_id,
        file_id,
        owner_subject="user-1",
        stored_path=f"user-1/{document_id}/supp.pdf",
    )
    monkeypatch.setattr(file_removal, "_mark_removed_or_busy", lambda *_a, **_kw: removed)
    monkeypatch.setattr("src.lib.weaviate_client.chunks.delete_chunk_range", lambda *_a, **_kw: None)
    monkeypatch.setattr("src.lib.document_files.service.remove_stored_files", lambda *_a, **_kw: None)

    def _failing_reporter(*_a, **_kw):
        events.append("reporter_raised")
        raise RuntimeError("reporter offline")

    monkeypatch.setattr(file_removal, "_report_cleanup_failure", _failing_reporter)

    pdf_collection = MagicMock()

    def _blocking_failing_update(**_kwargs):
        update_entered.set()
        assert resume_update.wait(timeout=5)
        events.append("piece_count_failed")
        raise OSError("weaviate timeout")

    pdf_collection.data.update.side_effect = _blocking_failing_update
    monkeypatch.setattr(
        "src.lib.weaviate_helpers.get_user_collections",
        lambda *_a, **_kw: (MagicMock(), pdf_collection),
    )

    @contextmanager
    def _weaviate_session(*_a, **_kw):
        yield MagicMock()

    weaviate_client = MagicMock()
    weaviate_client.session.side_effect = _weaviate_session

    async def _run():
        async with paper_operation_lock(document_id) as lock_conn:
            await remove_paper_file(
                document_id,
                42,
                file_id,
                weaviate_client=weaviate_client,
                lock_connection=lock_conn,
            )

    task = asyncio.create_task(_run())
    try:
        assert await asyncio.to_thread(update_entered.wait, 5)
        task.cancel("client disconnected")
        await asyncio.sleep(0.05)
        assert "lock_released" not in events
    finally:
        resume_update.set()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=5)

    assert events == ["piece_count_failed", "reporter_raised", "lock_released"]
