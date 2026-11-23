"""Unit tests for the document pipeline orchestrator."""

import asyncio
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest
from sqlalchemy import event
from sqlalchemy.orm import sessionmaker

from src.lib.pipeline.orchestrator import DocumentPipelineOrchestrator, process_pdf_document
from src.lib.exceptions import PDFCancellationError, PDFParsingError
from src.lib.pipeline.pdfx_parser import PDFX_PROVIDER_FAILURE_MESSAGE
from src.models.pipeline import ProcessingStage


@pytest.fixture
def mock_weaviate_client():
    client = MagicMock()
    client.is_ready.return_value = True
    return client


@pytest.fixture
def orchestrator(mock_weaviate_client):
    return DocumentPipelineOrchestrator(mock_weaviate_client)


MAIN_FILE_ID = "main-file-1"


@pytest.fixture(autouse=True)
def index_connection(monkeypatch):
    """Exercise the real status helper with an isolated collection."""
    connection = MagicMock()
    monkeypatch.setattr("src.lib.weaviate_client.documents.get_connection", lambda: connection)
    return connection


@pytest.fixture(autouse=True)
def file_steps(monkeypatch):
    """Stub the main-file steps; chunk dicts keep their indices (start 0)."""
    steps = SimpleNamespace(
        main_file_id=MagicMock(return_value=MAIN_FILE_ID),
        reserve=MagicMock(return_value=0),
        ready=MagicMock(),
        failed=MagicMock(),
        metadata=AsyncMock(),
        offsets=[],
    )
    monkeypatch.setattr("src.lib.document_files.service.main_file_id", steps.main_file_id)
    monkeypatch.setattr("src.lib.document_files.service.reserve_file_chunk_range", steps.reserve)
    monkeypatch.setattr("src.lib.document_files.service.mark_file_ready", steps.ready)
    monkeypatch.setattr("src.lib.document_files.service.mark_file_failed", steps.failed)
    monkeypatch.setattr("src.lib.pipeline.store.update_document_metadata", steps.metadata)

    def offset(chunks, start):
        steps.offsets.append(start)
        return chunks

    monkeypatch.setattr("src.lib.pipeline.chunk.offset_chunk_indices", offset)
    return steps


@pytest.fixture
def sample_pdf(tmp_path: Path) -> Path:
    pdf = tmp_path / "sample.pdf"
    pdf.write_bytes(b"%PDF-1.5\nTest content\n%%EOF")
    return pdf


@pytest.fixture
def file_db(monkeypatch):
    """Run the writers' SQLAlchemy transactions with an isolated row and commit hook."""
    sessions = sessionmaker()
    row = SimpleNamespace()
    get_row = MagicMock(return_value=row)
    commit = MagicMock()
    rollback = MagicMock()
    monkeypatch.setattr(sessions.class_, "get", get_row)
    event.listen(sessions, "before_commit", commit)
    event.listen(sessions, "after_rollback", rollback)
    monkeypatch.setattr("src.models.sql.database.SessionLocal", sessions)
    monkeypatch.setattr(
        "src.lib.observability.cost_context.execution_context",
        lambda **kwargs: {"run_id": "test-run", "activity": "background"},
    )
    return SimpleNamespace(row=row, get=get_row, commit=commit, rollback=rollback)


@pytest.mark.asyncio
async def test_process_pdf_document_success(orchestrator, sample_pdf, file_steps, file_db):
    document_id = "doc-123"
    orchestrator._sync_sql_document_status = AsyncMock()

    with patch("src.lib.pipeline.pdfx_parser.parse_pdf_document", new=AsyncMock(return_value={
        "elements": [{"text": "Foo"}],
        "pdfx_json_path": "pdfx/doc.json",
        "processed_json_path": "processed/doc.json",
    })) as mock_parser, patch(
        "src.lib.pipeline.chunk.chunk_parsed_document",
        new=AsyncMock(return_value=[{"chunk_index": 0, "content": "Foo"}])
    ) as mock_chunk, patch(
        "src.lib.pipeline.figure_locator_resolution.resolve_figure_locators",
        new=AsyncMock(side_effect=lambda chunks: chunks),
    ) as mock_figure_locators, patch(
        "src.lib.pipeline.store.store_to_weaviate",
        new=AsyncMock()
    ) as mock_store:
        result = await orchestrator.process_pdf_document(sample_pdf, document_id, "test_user", validate_first=False)

    assert result.success is True
    assert result.total_chunks == 1
    assert result.total_embeddings == 1
    assert result.stages_completed == [
        ProcessingStage.PARSING,
        ProcessingStage.CHUNKING,
        ProcessingStage.STORING,
    ]
    assert result.observability_receipt["outcome"] == "completed"
    assert result.observability_receipt["stages"]["chunking"]["status"] == "completed"
    assert result.observability_receipt["stages"]["figure_locator"]["status"] == "completed"
    assert result.observability_receipt["stages"]["embedding_storage"]["status"] == "completed"
    assert result.observability_receipt["stages"]["total"]["status"] == "completed"
    orchestrator._sync_sql_document_status.assert_any_await(document_id, status="processing")
    orchestrator._sync_sql_document_status.assert_any_await(document_id, status="completed")

    mock_parser.assert_awaited_once()
    mock_chunk.assert_awaited_once()
    mock_figure_locators.assert_awaited_once_with(
        [{"chunk_index": 0, "content": "Foo"}]
    )
    mock_store.assert_awaited_once()
    assert mock_store.await_args is not None
    args, kwargs = mock_store.await_args
    assert args[0] == [{"chunk_index": 0, "content": "Foo"}]
    assert args[1] == document_id
    assert kwargs == {"chunk_range": (0, 0)}
    # The PDF pipeline processes the paper's main file.
    file_steps.main_file_id.assert_called_once_with(document_id)
    assert file_db.row.pdfx_json_path == "pdfx/doc.json"
    assert file_db.row.processed_json_path == "processed/doc.json"
    file_steps.reserve.assert_called_once_with(document_id, MAIN_FILE_ID, 1)
    assert file_steps.offsets == [0]
    file_steps.ready.assert_called_once_with(MAIN_FILE_ID, text_bytes=3)
    file_steps.metadata.assert_awaited_once()
    assert file_steps.metadata.await_args.args[:2] == (
        document_id, {"stored_chunks": 1, "failed_chunks": 0}
    )
    file_steps.failed.assert_not_called()


@pytest.fixture
def parsed_pdf(orchestrator, monkeypatch):
    orchestrator._sync_sql_document_status = AsyncMock()
    hierarchy = MagicMock(top_level_sections=["Results"])
    hierarchy.model_dump.return_value = {"top_level_sections": ["Results"]}
    monkeypatch.setattr("src.lib.pipeline.pdfx_parser.parse_pdf_document", AsyncMock(return_value={
        "elements": [{"text": "Foo"}],
        "pdfx_json_path": "pdfx/doc.json",
        "processed_json_path": "processed/doc.json",
    }))
    monkeypatch.setattr(
        "src.lib.pipeline.hierarchy_resolution.resolve_document_hierarchy",
        AsyncMock(side_effect=lambda elements: (elements, hierarchy)),
    )
    monkeypatch.setattr(
        "src.lib.pipeline.chunk.chunk_parsed_document",
        AsyncMock(return_value=[{"chunk_index": 0, "content": "Foo"}]),
    )
    monkeypatch.setattr(
        "src.lib.pipeline.figure_locator_resolution.resolve_figure_locators",
        AsyncMock(side_effect=lambda chunks: chunks),
    )
    monkeypatch.setattr("src.lib.pipeline.store.store_to_weaviate", AsyncMock())
    return hierarchy


@pytest.fixture(params=["reported", "reporter_raises"])
def failure_reporter(request, monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="src.lib.pipeline.orchestrator")
    reporter = MagicMock(return_value=True)
    if request.param == "reporter_raises":
        reporter.side_effect = RuntimeError("PRIVATE_REPORTER_DETAIL")
    monkeypatch.setattr("src.lib.pipeline.orchestrator.report_runtime_exception", reporter)
    return reporter


def assert_write_failure_logs_are_safe(caplog):
    # A promoted companion warning would duplicate the owner's explicit event.
    for record in caplog.records:
        if record.name == "src.lib.pipeline.orchestrator" and record.levelno >= logging.WARNING:
            assert getattr(record, "sentry_skip_event", False)
    assert "PRIVATE_DATABASE_DETAIL" not in caplog.text
    assert "PRIVATE_REPORTER_DETAIL" not in caplog.text


@pytest.mark.asyncio
async def test_process_pdf_document_stores_hierarchy_on_the_main_file(orchestrator, sample_pdf, parsed_pdf, file_db):
    from src.models.sql.pdf_document import PDFDocumentFile

    result = await orchestrator.process_pdf_document(sample_pdf, "doc-h", "test_user", validate_first=False)

    assert result.success is True
    assert [lookup.args for lookup in file_db.get.call_args_list] == [(PDFDocumentFile, MAIN_FILE_ID)] * 2
    assert file_db.row.hierarchy_metadata == parsed_pdf.model_dump.return_value
    assert file_db.commit.call_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["commit", "missing_row"])
async def test_path_write_failure_stops_processing(
    orchestrator, sample_pdf, parsed_pdf, file_db, file_steps, failure_reporter, caplog, failure,
):
    if failure == "commit":
        file_db.commit.side_effect = RuntimeError("PRIVATE_DATABASE_DETAIL")
    else:
        file_db.get.return_value = None

    result = await orchestrator.process_pdf_document(sample_pdf, "doc-h", "test_user", validate_first=False)

    public_error = "The paper's processed files could not be saved. Please upload it again."
    assert result.success is False
    assert result.error == public_error
    file_steps.ready.assert_not_called()
    file_steps.failed.assert_called_once_with(MAIN_FILE_ID, error_message=public_error)
    assert orchestrator._sync_sql_document_status.await_args_list == [
        call("doc-h", status="processing"), call("doc-h", status="failed", error_message=public_error),
    ]
    assert orchestrator.tracker.processing_errors["doc-h"][0].error_message == public_error
    file_db.rollback.assert_called_once()
    failure_reporter.assert_called_once()
    (error,) = failure_reporter.call_args.args
    assert str(error) == public_error
    assert error.__cause__ is error.__context__ is None
    assert failure_reporter.call_args.kwargs["operation"] == "process_pdf_document_failed"
    for private_value in ("PRIVATE_DATABASE_DETAIL", MAIN_FILE_ID, "test_user"):
        assert private_value not in repr(failure_reporter.call_args)
    assert_write_failure_logs_are_safe(caplog)


@pytest.mark.asyncio
async def test_hierarchy_write_failure_allows_completion(
    orchestrator, sample_pdf, parsed_pdf, file_db, file_steps, failure_reporter, caplog,
):
    file_db.commit.side_effect = [None, RuntimeError("PRIVATE_DATABASE_DETAIL")]

    result = await orchestrator.process_pdf_document(sample_pdf, "doc-h", "test_user", validate_first=False)

    assert result.success is True
    assert result.error is None
    file_steps.ready.assert_called_once_with(MAIN_FILE_ID, text_bytes=3)
    file_steps.failed.assert_not_called()
    orchestrator._sync_sql_document_status.assert_awaited_with("doc-h", status="completed")
    file_db.rollback.assert_called_once()
    failure_reporter.assert_called_once()
    (error,) = failure_reporter.call_args.args
    assert str(error) == "Pipeline hierarchy metadata write failed"
    assert error.__cause__ is error.__context__ is None
    assert error.__traceback__ is not None
    assert failure_reporter.call_args.kwargs == {
        "component": "document_pipeline",
        "operation": "pipeline_hierarchy_metadata_write_failed",
        "context": {"exception_type": "RuntimeError"},
    }
    assert_write_failure_logs_are_safe(caplog)


@pytest.mark.asyncio
@pytest.mark.parametrize("index_failure", ["collection", "session"])
async def test_failed_status_reconciliation_reports_each_write(
    orchestrator, monkeypatch, file_steps, failure_reporter, caplog, index_failure,
):
    document_id = "e5639b2e-61b7-4c77-bbd4-5dbb76129947"
    orchestrator._current_file_id = "PRIVATE_FILE_ID"
    orchestrator._current_user_id = "PRIVATE_USER_ID"
    orchestrator.tracker = AsyncMock()
    file_steps.failed.side_effect = OSError("PRIVATE_FILE_ERROR")
    session = MagicMock()
    session.commit.side_effect = ValueError("PRIVATE_SQL_ERROR")
    monkeypatch.setattr("src.models.sql.database.SessionLocal", lambda: session)
    collection = MagicMock()
    monkeypatch.setattr("src.lib.weaviate_helpers.get_user_collections", lambda *_: (None, collection))
    if index_failure == "collection":
        collection.data.update.side_effect = ConnectionError("PRIVATE_INDEX_ERROR")
        index_context = {"error_code": "STATUS_UPDATE_FAILED"}
    else:
        from src.lib.weaviate_client.connection import WeaviateConnection

        monkeypatch.setattr(WeaviateConnection, "_instance", None)
        connection = WeaviateConnection()
        monkeypatch.setattr("src.lib.weaviate_client.documents.get_connection", lambda: connection)
        monkeypatch.setattr("weaviate.connect_to_local", MagicMock(side_effect=ConnectionError("PRIVATE_INDEX_ERROR")))
        # The shared connection wraps its SDK exception before the caller sees it.
        index_context = {"exception_type": "Exception"}

    primary = RuntimeError("original processing failure")
    await orchestrator._handle_failure(document_id, primary)

    file_steps.failed.assert_called_once_with("PRIVATE_FILE_ID", error_message=str(primary))
    session.rollback.assert_called_once_with()
    session.close.assert_called_once_with()
    if index_failure == "collection":
        collection.data.update.assert_called_once_with(uuid=document_id, properties={"embeddingStatus": "failed"})
    orchestrator.tracker.handle_pipeline_failure.assert_awaited_once_with(
        document_id, primary, stage=ProcessingStage.FAILED, sentry_reported=False,
    )
    reports = failure_reporter.call_args_list
    assert [report.kwargs["operation"] for report in reports] == [
        "pipeline_failure_file_status_write_failed",
        "pipeline_failure_sql_status_write_failed",
        "pipeline_failure_index_status_write_failed",
    ]
    assert [report.kwargs["context"] for report in reports] == [
        {"exception_type": "OSError"},
        {"exception_type": "ValueError"},
        index_context,
    ]
    for report in reports:
        assert report.kwargs["component"] == "document_pipeline"
        (error,) = report.args
        assert error.__cause__ is error.__context__ is None
        assert error.__traceback__ is not None
    assert "PRIVATE_" not in repr(reports) + caplog.text
    assert document_id not in repr(reports) + caplog.text
    assert all(record.sentry_skip_event for record in caplog.records if record.levelno >= logging.WARNING)


@pytest.mark.parametrize("status", ["processing", "completed"])
def test_nonfailed_sql_status_rethrows_original_write_error(orchestrator, monkeypatch, status):
    session = MagicMock()
    error = RuntimeError("commit failed")
    session.commit.side_effect = error
    monkeypatch.setattr("src.models.sql.database.SessionLocal", lambda: session)
    with patch("src.lib.pipeline.orchestrator.report_runtime_exception") as report:
        with pytest.raises(RuntimeError) as raised:
            orchestrator._write_sql_document_status("e5639b2e-61b7-4c77-bbd4-5dbb76129947", status=status)
    assert raised.value is error
    session.rollback.assert_called_once_with()
    session.close.assert_called_once_with()
    report.assert_not_called()


@pytest.mark.asyncio
async def test_index_status_log_ownership_is_limited_to_reconciliation(orchestrator, monkeypatch, caplog):
    from src.lib.weaviate_client import documents

    orchestrator._current_file_id = None
    orchestrator._current_user_id = "test_user"
    orchestrator._sync_sql_document_status = AsyncMock()
    collection = MagicMock()
    collection.data.update.side_effect = OSError("collection rejected write")
    monkeypatch.setattr("src.lib.weaviate_helpers.get_user_collections", lambda *_: (None, collection))
    with patch("src.lib.pipeline.orchestrator.report_runtime_exception") as report:
        _, other_result = await asyncio.gather(
            orchestrator._reconcile_failed_status("reconciling-paper", "failed"),
            documents.update_document_status_detailed("other-paper", "test_user", embedding_status="processing"),
        )

    assert not other_result["success"]
    report.assert_called_once()
    producer_logs = [record for record in caplog.records if record.name == documents.__name__]
    assert len(producer_logs) == 1
    assert "other-paper" in producer_logs[0].getMessage()
    assert "reconciling-paper" not in caplog.text


@pytest.mark.asyncio
async def test_cancelled_result_survives_index_status_and_reporter_failure(
    orchestrator, sample_pdf, index_connection, monkeypatch,
):
    orchestrator._sync_sql_document_status = AsyncMock()
    index_connection.session.side_effect = OSError("PRIVATE_INDEX_ERROR")
    reporter = MagicMock(side_effect=RuntimeError("PRIVATE_REPORTER_DETAIL"))
    monkeypatch.setattr("src.lib.pipeline.orchestrator.report_runtime_exception", reporter)
    result = await orchestrator.process_pdf_document(
        sample_pdf, "doc-cancelled", "test_user", validate_first=False,
        cancel_requested_callback=AsyncMock(return_value=True),
    )
    assert result.cancelled and not result.success
    assert result.error == "Processing cancelled by user request"
    reporter.assert_called_once()
    assert reporter.call_args.kwargs["operation"] == "pipeline_failure_index_status_write_failed"


@pytest.mark.asyncio
async def test_process_pdf_document_storage_failure_fails_the_main_file(orchestrator, sample_pdf, file_steps):
    orchestrator._sync_sql_document_status = AsyncMock()
    orchestrator._update_file_paths = AsyncMock()

    with patch("src.lib.pipeline.pdfx_parser.parse_pdf_document", new=AsyncMock(return_value={
        "elements": [{"text": "Foo"}],
        "pdfx_json_path": None,
        "processed_json_path": None,
    })), patch(
        "src.lib.pipeline.chunk.chunk_parsed_document",
        new=AsyncMock(return_value=[{"chunk_index": 0, "content": "Foo"}]),
    ), patch(
        "src.lib.pipeline.figure_locator_resolution.resolve_figure_locators",
        new=AsyncMock(side_effect=lambda chunks: chunks),
    ), patch("src.lib.pipeline.store.store_to_weaviate", new=AsyncMock(side_effect=RuntimeError("store boom"))):
        result = await orchestrator.process_pdf_document(sample_pdf, "doc-s", "test_user", validate_first=False)

    assert result.success is False
    file_steps.failed.assert_called_once_with(MAIN_FILE_ID, error_message="store boom")
    file_steps.ready.assert_not_called()
    file_steps.metadata.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_pdf_document_validation_failure(orchestrator, sample_pdf):
    with patch("src.lib.pipeline.upload.validate_pdf", return_value={
        "is_valid": False,
        "errors": ["invalid"]
    }):
        result = await orchestrator.process_pdf_document(sample_pdf, "doc-1", "test_user", validate_first=True)

    assert result.success is False
    assert ProcessingStage.PARSING not in result.stages_completed


@pytest.mark.asyncio
async def test_process_pdf_document_parsing_error(orchestrator, sample_pdf, monkeypatch, caplog, file_steps):
    orchestrator._sync_sql_document_status = AsyncMock()
    runtime_reports = []
    monkeypatch.setattr(
        "src.lib.pipeline.orchestrator.report_runtime_exception",
        lambda exc, **kwargs: runtime_reports.append((exc, kwargs)) or True,
    )
    caplog.set_level(logging.WARNING, logger="src.lib.pipeline.orchestrator")

    with patch("src.lib.pipeline.pdfx_parser.parse_pdf_document", new=AsyncMock(side_effect=RuntimeError("boom"))):
        result = await orchestrator.process_pdf_document(sample_pdf, "doc-1", "test_user", validate_first=False)

    assert result.success is False
    assert ProcessingStage.PARSING not in result.stages_completed
    assert "boom" in result.error
    orchestrator._sync_sql_document_status.assert_any_await(
        "doc-1",
        status="failed",
        error_message="boom",
    )
    file_steps.failed.assert_called_once_with(MAIN_FILE_ID, error_message="boom")
    assert len(runtime_reports) == 1
    reported_exc, report_kwargs = runtime_reports[0]
    assert isinstance(reported_exc, RuntimeError)
    assert str(reported_exc) == "boom"
    assert report_kwargs == {
        "component": "document_pipeline",
        "operation": "process_pdf_document_failed",
        "context": {
            "document_id": "doc-1",
            "stages_completed_count": 0,
            "stages_completed": [],
            "validate_first": False,
            "extraction_strategy": "auto",
        },
    }
    failure_logs = [
        record for record in caplog.records if record.message.startswith("Pipeline failed")
    ]
    assert len(failure_logs) == 1
    assert failure_logs[0].levelno == logging.WARNING
    assert failure_logs[0].exc_info is not None


@pytest.mark.asyncio
async def test_pdfx_failure_keeps_operator_evidence_but_persists_safe_message(
    orchestrator,
    sample_pdf,
    monkeypatch,
):
    sentinel = "PRIVATE_PROVIDER_SENTINEL"
    orchestrator._sync_sql_document_status = AsyncMock()
    runtime_reports = []
    monkeypatch.setattr(
        "src.lib.pipeline.orchestrator.report_runtime_exception",
        lambda exc, **kwargs: runtime_reports.append((exc, kwargs)) or True,
    )
    provider_error = PDFParsingError(
        "PDF extraction failed for process_id=proc-safe status=failed error_code=publish_failed",
        details={
            "public_message": PDFX_PROVIDER_FAILURE_MESSAGE,
            "pdfx_failure": {
                "failure_category": "provider_terminal_failure",
                "failure_boundary": "poll",
                "process_id": "proc-safe",
                "provider_status": "failed",
                "provider_error_code": "publish_failed",
                "http_status": 200,
                "submit_attempt_count": 1,
                "poll_attempt_count": 4,
                "timeout_seconds": 300,
                "provider_error": sentinel,
            },
        },
    )

    with patch(
        "src.lib.pipeline.pdfx_parser.parse_pdf_document",
        new=AsyncMock(side_effect=provider_error),
    ):
        result = await orchestrator.process_pdf_document(
            sample_pdf,
            "doc-safe",
            "test_user",
            validate_first=False,
        )

    assert result.success is False
    assert result.error == PDFX_PROVIDER_FAILURE_MESSAGE
    orchestrator._sync_sql_document_status.assert_any_await(
        "doc-safe",
        status="failed",
        error_message=PDFX_PROVIDER_FAILURE_MESSAGE,
    )
    assert len(runtime_reports) == 1
    assert runtime_reports[0][0] is provider_error
    assert sentinel not in str(runtime_reports[0])
    assert runtime_reports[0][1]["context"] == {
        "document_id": "doc-safe",
        "stages_completed_count": 0,
        "stages_completed": [],
        "validate_first": False,
        "extraction_strategy": "auto",
        "pdfx_failure_category": "provider_terminal_failure",
        "pdfx_failure_boundary": "poll",
        "pdfx_process_id": "proc-safe",
        "pdfx_provider_status": "failed",
        "pdfx_provider_error_code": "publish_failed",
        "pdfx_http_status": 200,
        "pdfx_submit_attempt_count": 1,
        "pdfx_poll_attempt_count": 4,
        "pdfx_timeout_seconds": 300,
    }
    tracked_errors = orchestrator.tracker.processing_errors["doc-safe"]
    assert tracked_errors[0].error_message == PDFX_PROVIDER_FAILURE_MESSAGE
    assert "publish_failed" not in tracked_errors[0].error_message
    assert sentinel not in str(result.observability_receipt)


@pytest.mark.asyncio
async def test_process_pdf_document_cancellation_does_not_report_runtime_exception(
    orchestrator,
    sample_pdf,
    monkeypatch,
    file_steps,
    index_connection,
):
    orchestrator._sync_sql_document_status = AsyncMock()
    runtime_reports = []
    monkeypatch.setattr(
        "src.lib.pipeline.orchestrator.report_runtime_exception",
        lambda exc, **kwargs: runtime_reports.append((exc, kwargs)) or True,
    )

    with patch(
        "src.lib.pipeline.pdfx_parser.parse_pdf_document",
        new=AsyncMock(side_effect=PDFCancellationError("cancelled")),
    ):
        result = await orchestrator.process_pdf_document(
            sample_pdf,
            "doc-cancelled",
            "test_user",
            validate_first=False,
        )

    assert result.success is False
    assert result.cancelled is True
    assert result.observability_receipt["outcome"] == "cancelled"
    assert result.observability_receipt["stages"]["total"]["status"] == "cancelled"
    assert runtime_reports == []
    client = index_connection.session.return_value.__enter__.return_value
    collection = client.collections.get.return_value.with_tenant.return_value
    collection.data.update.assert_called_once_with(uuid="doc-cancelled", properties={"embeddingStatus": "failed"})
    file_steps.failed.assert_called_once_with(MAIN_FILE_ID, error_message="cancelled")


@pytest.mark.asyncio
async def test_process_pdf_document_chunking_error_reports_completed_stage_context(
    file_db,
    orchestrator,
    sample_pdf,
    monkeypatch,
):
    orchestrator._sync_sql_document_status = AsyncMock()
    runtime_reports = []
    monkeypatch.setattr(
        "src.lib.pipeline.orchestrator.report_runtime_exception",
        lambda exc, **kwargs: runtime_reports.append((exc, kwargs)) or True,
    )

    with patch(
        "src.lib.pipeline.pdfx_parser.parse_pdf_document",
        new=AsyncMock(return_value={
            "elements": [{"text": "Foo"}],
            "pdfx_json_path": "pdfx/doc.json",
            "processed_json_path": "processed/doc.json",
        }),
    ), patch(
        "src.lib.pipeline.chunk.chunk_parsed_document",
        new=AsyncMock(side_effect=RuntimeError("chunk boom")),
    ):
        result = await orchestrator.process_pdf_document(
            sample_pdf,
            "doc-chunk",
            "test_user",
            validate_first=False,
        )

    assert result.success is False
    assert result.stages_completed == [ProcessingStage.PARSING]
    assert result.observability_receipt["outcome"] == "failed"
    assert result.observability_receipt["stages"]["chunking"]["status"] == "failed"
    assert len(runtime_reports) == 1
    assert runtime_reports[0][1] == {
        "component": "document_pipeline",
        "operation": "process_pdf_document_failed",
        "context": {
            "document_id": "doc-chunk",
            "stages_completed_count": 1,
            "stages_completed": ["parsing"],
            "validate_first": False,
            "extraction_strategy": "auto",
        },
    }


def test_validate_pipeline_requirements(orchestrator):
    checks = orchestrator.validate_pipeline_requirements()
    assert checks["weaviate_connected"] is True
    assert checks["embedding_service_available"] is True
    assert isinstance(checks["storage_writable"], bool)


@pytest.mark.asyncio
async def test_process_pdf_document_helper(mock_weaviate_client, sample_pdf, monkeypatch, file_db):
    monkeypatch.setattr(DocumentPipelineOrchestrator, "_sync_sql_document_status", AsyncMock())
    with patch("src.lib.pipeline.pdfx_parser.parse_pdf_document", new=AsyncMock(return_value={
        "elements": [],
        "pdfx_json_path": None,
        "processed_json_path": None,
    })), patch("src.lib.pipeline.chunk.chunk_parsed_document", new=AsyncMock(return_value=[])), patch(
        "src.lib.pipeline.figure_locator_resolution.resolve_figure_locators",
        new=AsyncMock(side_effect=lambda chunks: chunks),
    ), patch(
        "src.lib.pipeline.store.store_to_weaviate",
        new=AsyncMock()
    ):
        result = await process_pdf_document(sample_pdf, "doc-xyz", mock_weaviate_client, "test_user")

    assert result.success is True
    assert result.total_chunks == 0


@pytest.mark.asyncio
async def test_processing_status_failure_prevents_source_replacement(orchestrator, sample_pdf):
    orchestrator._initialize_pipeline = AsyncMock()
    orchestrator._sync_sql_document_status = AsyncMock(side_effect=RuntimeError("SQL unavailable"))
    with patch("src.lib.pipeline.pdfx_parser.parse_pdf_document", new=AsyncMock()) as parser, \
         patch("src.lib.pipeline.store.store_to_weaviate", new=AsyncMock()) as storage:
        with pytest.raises(RuntimeError, match="SQL unavailable"):
            await orchestrator.process_pdf_document(sample_pdf, "doc-123", "test-user", validate_first=False)
    parser.assert_not_awaited()
    storage.assert_not_awaited()


@pytest.mark.parametrize("capture_succeeds", [True, False])
@pytest.mark.parametrize("failure_shape", ["pending", "progress", "generic"])
@pytest.mark.parametrize("log_event_level", ["ERROR", "WARNING"])
def test_pipeline_failure_has_one_sentry_owner_with_real_logging(capture_succeeds, failure_shape, log_event_level):
    """Exercise orchestrator, job tracker and managed task with the real SDK."""
    import subprocess
    import sys
    import textwrap

    script = textwrap.dedent("""
        import asyncio
        import logging
        import sys
        import os
        import json
        os.environ["SENTRY_CONTENT_REDACTION_ENABLED"] = "false"
        from contextlib import nullcontext
        from pathlib import Path
        from unittest.mock import AsyncMock, MagicMock, patch
        import sentry_sdk
        from sentry_sdk.integrations.logging import LoggingIntegration
        from src.lib.observability.sentry import before_send, hash_sentry_identifier
        from src.lib.observability.background_tasks import observed_background_task
        from src.lib.pdf_jobs.upload_execution_service import JobAwarePipelineTracker
        from src.lib.pipeline.orchestrator import DocumentPipelineOrchestrator
        from src.lib.pipeline.tracker import PipelineTracker
        from src.lib.exceptions import PDFParsingError
        from src.models.pipeline import ProcessingStage
        events = []
        records = []
        class RecordLog(logging.Handler):
            def emit(self, record):
                records.append(record)
        logging.getLogger("src.lib.pipeline.tracker").addHandler(RecordLog())
        sentry_sdk.init(dsn="http://public@example.invalid/1", transport=events.append,
            before_send=before_send, include_local_variables=False, traces_sample_rate=1.0,
            integrations=[LoggingIntegration(level=logging.INFO, event_level=getattr(logging, sys.argv[3]))],
            default_integrations=False)
        async def run():
            tracker = PipelineTracker()
            job_tracker = JobAwarePipelineTracker(tracker, "test-job")
            shape = sys.argv[2]
            document_id = "doc-" + shape
            pipeline = DocumentPipelineOrchestrator(MagicMock(), tracker=tracker if shape == "pending" else job_tracker)
            pipeline._sync_sql_document_status = AsyncMock()
            file_steps = (
                patch("src.lib.document_files.service.main_file_id", return_value="main-file-1"),
                patch("src.lib.document_files.service.mark_file_failed"),
            )
            for step in file_steps:
                step.start()
            failure = PDFParsingError("private pipeline detail", details={
                "public_message": "PDF reader timed out",
                "pdfx_failure": {
                    "failure_category": "polling_timeout", "failure_boundary": "poll_queue" if shape == "pending" else "poll_processing", "timeout_seconds": 5400 if shape == "pending" else 3600,
                    "process_id": "process-" + shape, "provider_status": shape,
                    "poll_attempt_count": 120, "provider_error": "PRIVATE_UNAPPROVED_PROVIDER_DETAIL",
                },
            })
            if shape == "generic":
                failure = RuntimeError("private pipeline detail")
            public_message = "private pipeline detail" if shape == "generic" else "PDF reader timed out"
            capture_succeeds = sys.argv[1] == "True"
            capture = nullcontext() if capture_succeeds else patch("src.lib.pipeline.orchestrator.report_runtime_exception", return_value=False)
            with sentry_sdk.start_transaction(op="pdf.processing", name="test", sampled=False) as transaction, capture, patch("src.lib.pipeline.pdfx_parser.parse_pdf_document", new=AsyncMock(side_effect=failure)), \
                 patch("src.lib.weaviate_client.documents.update_document_status_detailed", new=AsyncMock(return_value={"success": True})), \
                 patch("src.lib.pdf_jobs.upload_execution_service.pdf_job_service.update_progress"), \
                 patch("src.lib.pdf_jobs.upload_execution_service.pdf_job_service.is_cancel_requested", return_value=False), \
                 patch("src.lib.observability.cost_context.execution_context", return_value={"run_id": "test-run", "activity": "background"}):
                execute = pipeline.process_pdf_document if shape == "pending" else observed_background_task(pipeline.process_pdf_document)
                result = await execute(Path("unused.pdf"), document_id, "user-test", validate_first=False)
            assert not result.success
            assert result.error == public_message
            assert len(records) == 1
            assert public_message in records[0].getMessage()
            assert records[0].sentry_skip_event is capture_succeeds
            assert len(tracker.processing_errors[document_id]) == 1
            sentry_sdk.flush(timeout=1)
            assert len(events) == 1, events
            assert events[0]["contexts"]["trace"]["trace_id"] == transaction.trace_id
            assert "PRIVATE_UNAPPROVED_PROVIDER_DETAIL" not in json.dumps(events)
            assert document_id not in json.dumps(events)
            if capture_succeeds:
                assert events[0]["tags"]["operation"] == "process_pdf_document_failed"
                context = events[0]["contexts"]["runtime_exception"]
                assert context["document_id"] == hash_sentry_identifier(document_id)
                assert events[0]["exception"]["values"][-1]["type"] == type(failure).__name__
                if shape != "generic":
                    assert context["pdfx_failure_boundary"] == ("poll_queue" if shape == "pending" else "poll_processing")
                    assert context["pdfx_failure_category"] == "polling_timeout"
                    assert context["pdfx_timeout_seconds"] == (5400 if shape == "pending" else 3600)
                    assert context["pdfx_process_id"] == "process-" + shape
                    assert context["pdfx_provider_status"] == shape
                    assert context["pdfx_poll_attempt_count"] == 120
            else:
                assert events[0]["logger"] == "src.lib.pipeline.tracker"
            events.clear()
            await tracker.handle_pipeline_failure("standalone", RuntimeError("independent error"), ProcessingStage.PARSING)
            sentry_sdk.flush(timeout=1)
            assert len(events) == 1, events
        asyncio.run(run())
    """)
    result = subprocess.run([sys.executable, "-c", script, str(capture_succeeds), failure_shape, log_event_level], text=True, capture_output=True)
    assert result.returncode == 0, result.stderr + result.stdout
