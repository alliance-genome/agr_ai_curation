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
@pytest.mark.parametrize("failing_step", ["pieces", "stored_files", "piece_count", "file_row", "cache"])
async def test_cleanup_failure_reports_sanitized_sentry_event_once_with_no_sensitive_leaks(
    monkeypatch, caplog, failing_step
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

    monkeypatch.setenv("SENTRY_CONTENT_REDACTION_ENABLED", "true")
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
