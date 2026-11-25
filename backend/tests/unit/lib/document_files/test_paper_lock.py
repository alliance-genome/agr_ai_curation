"""Paper lock cleanup after the request is cancelled during acquisition."""

import asyncio
from unittest.mock import Mock
from uuid import uuid4

import pytest

from src.lib.document_files import paper_lock


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "capture_fails"),
    [("acquire", False), ("acquire", True), ("release", False), ("release", True), (None, False)],
)
async def test_cancel_during_acquire(monkeypatch, caplog, failure, capture_fails):
    document_id = uuid4()
    db_error = RuntimeError("private SQL parameter")
    capture_error = ValueError("private capture details")
    connection = Mock()
    taken = Mock()
    taken.scalar_one.return_value = True
    if failure == "acquire":
        connection.execute.side_effect = db_error
    else:
        connection.execute.side_effect = [taken, db_error if failure == "release" else None]
    monkeypatch.setattr("src.models.sql.database.engine.connect", lambda: connection)
    report = Mock(side_effect=capture_error if capture_fails else None)
    monkeypatch.setattr("src.lib.observability.runtime.report_runtime_exception", report)

    # Control worker completion on the test loop while exercising the real lock SQL cleanup.
    resume_acquire = asyncio.Event()
    workers = asyncio.Queue()

    async def run_worker(func, *args):
        workers.put_nowait(asyncio.current_task())
        if func is paper_lock._acquire:
            await resume_acquire.wait()
        return func(*args)

    monkeypatch.setattr(paper_lock.asyncio, "to_thread", run_worker)

    async def request():
        async with paper_lock.paper_operation_lock(document_id):
            pytest.fail("Cancelled request entered the lock")

    request_task = asyncio.create_task(request())
    acquiring = await asyncio.wait_for(workers.get(), timeout=2)
    request_task.cancel("request cancelled")
    with pytest.raises(asyncio.CancelledError, match="request cancelled"):
        await request_task

    resume_acquire.set()
    # Wait for each task and its previously registered callbacks without retrieving its exception.
    await asyncio.wait({acquiring})
    if failure != "acquire":
        releasing = await asyncio.wait_for(workers.get(), timeout=2)
        await asyncio.wait({releasing})

    connection.close.assert_called_once_with()
    if failure is None:
        report.assert_not_called()
        connection.invalidate.assert_not_called()
    else:
        connection.invalidate.assert_called_once_with()
        report.assert_called_once()
        (error,) = report.call_args.args
        operation = "paper_lock_acquire_failed_after_cancel" if failure == "acquire" else "paper_lock_release_failed"
        assert report.call_args.kwargs == {
            "component": "document_files",
            "operation": operation,
            "level": "warning",
            "fingerprint": [operation],
            "include_request": False,
        }
        assert type(db_error).__name__ in str(error)
        assert error.__traceback__ is not None
        assert error.__cause__ is error.__context__ is None
        warnings = [record for record in caplog.records if record.name == paper_lock.__name__]
        assert any(type(db_error).__name__ in record.getMessage() for record in warnings)
        assert all(getattr(record, "sentry_skip_event", False) and record.exc_info is None for record in warnings)

    for sensitive in (str(db_error), str(capture_error), str(document_id), str(paper_lock.paper_lock_key(document_id))):
        assert sensitive not in repr(report.call_args)
        assert sensitive not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("redaction_enabled", ["false", "true"])
async def test_paper_lock_release_failure_omits_request_url_with_real_sentry_sdk(
    monkeypatch, caplog, redaction_enabled,
):
    import json
    import logging

    import sentry_sdk
    from sentry_sdk import Client
    from sentry_sdk.integrations.logging import LoggingIntegration
    from sentry_sdk.transport import Transport

    from src.lib.observability.sentry import before_send

    document_id = uuid4()
    raw_db_secret = f"pg_advisory_unlock failed for doc={document_id} user=curator.secret@example.org"
    connection = Mock()
    taken = Mock()
    taken.scalar_one.return_value = True
    connection.execute.side_effect = [taken, RuntimeError(raw_db_secret)]
    monkeypatch.setattr("src.models.sql.database.engine.connect", lambda: connection)
    monkeypatch.setenv("SENTRY_CONTENT_REDACTION_ENABLED", redaction_enabled)

    events: list[dict] = []

    class MemoryTransport(Transport):
        def capture_envelope(self, envelope):
            for item in envelope.items:
                if item.headers.get("type") == "event":
                    events.append(item.payload.json)

    client = Client(
        dsn="https://public@example.invalid/1",
        transport=MemoryTransport,
        before_send=before_send,
        send_default_pii=False,
        include_local_variables=False,
        default_integrations=False,
        integrations=[LoggingIntegration(level=logging.INFO, event_level=logging.ERROR)],
    )
    caplog.set_level(logging.WARNING, logger=paper_lock.__name__)
    try:
        with sentry_sdk.isolation_scope() as scope:
            scope.set_client(client)

            def _attach_delete_request(event, _hint):
                event.setdefault(
                    "request",
                    {
                        "method": "DELETE",
                        "url": f"https://app.example.org/api/weaviate/documents/{document_id}",
                    },
                )
                return event

            scope.add_event_processor(_attach_delete_request)
            async with paper_lock.paper_operation_lock(document_id) as held_connection:
                assert held_connection is connection
            client.flush()
    finally:
        client.close()

    assert len(events) == 1
    event = events[0]
    assert event["tags"]["runtime_component"] == "document_files"
    assert event["tags"]["operation"] == "paper_lock_release_failed"
    assert event["level"] == "warning"
    assert event["fingerprint"] == ["paper_lock_release_failed"]
    assert "request" not in event
    serialized = json.dumps(event)
    for sensitive in (
        str(document_id),
        str(paper_lock.paper_lock_key(document_id)),
        "curator.secret@example.org",
        "/api/weaviate/documents/",
    ):
        assert sensitive not in serialized
        assert sensitive not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("body_cancelled", [False, True])
@pytest.mark.parametrize("release_fails", [False, True])
async def test_cancel_during_release_drains_and_preserves_cancellation(
    monkeypatch, caplog, body_cancelled, release_fails,
):
    document_id = uuid4()
    db_error = RuntimeError("private SQL release failure")
    connection = Mock()
    taken = Mock()
    taken.scalar_one.return_value = True
    connection.execute.side_effect = [taken, db_error if release_fails else None]
    monkeypatch.setattr("src.models.sql.database.engine.connect", lambda: connection)
    report = Mock()
    monkeypatch.setattr("src.lib.observability.runtime.report_runtime_exception", report)

    release_started = asyncio.Event()
    resume_release = asyncio.Event()

    async def run_worker(func, *args):
        if func is paper_lock._release:
            release_started.set()
            await resume_release.wait()
        return func(*args)

    monkeypatch.setattr(paper_lock.asyncio, "to_thread", run_worker)

    async def request():
        async with paper_lock.paper_operation_lock(document_id):
            if body_cancelled:
                raise asyncio.CancelledError("body cancelled")

    request_task = asyncio.create_task(request())
    await asyncio.wait_for(release_started.wait(), timeout=2)
    request_task.cancel("release cancelled")
    await asyncio.sleep(0)
    request_task.cancel("second release cancel")
    await asyncio.sleep(0)
    assert not request_task.done()

    resume_release.set()
    expected_match = "body cancelled" if body_cancelled else "release cancelled"
    with pytest.raises(asyncio.CancelledError, match=expected_match):
        await request_task

    connection.close.assert_called_once_with()
    if release_fails:
        connection.invalidate.assert_called_once_with()
        report.assert_called_once()
        (error,) = report.call_args.args
        assert report.call_args.kwargs == {
            "component": "document_files",
            "operation": "paper_lock_release_failed",
            "level": "warning",
            "fingerprint": ["paper_lock_release_failed"],
            "include_request": False,
        }
        assert type(db_error).__name__ in str(error)
        assert error.__traceback__ is not None
        assert error.__cause__ is error.__context__ is None
        assert str(db_error) not in repr(report.call_args)
        assert str(db_error) not in caplog.text
    else:
        connection.invalidate.assert_not_called()
        report.assert_not_called()
