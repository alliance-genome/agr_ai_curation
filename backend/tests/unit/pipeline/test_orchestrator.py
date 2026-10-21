"""Unit tests for the document pipeline orchestrator."""

import logging
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

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


@pytest.fixture
def sample_pdf(tmp_path: Path) -> Path:
    pdf = tmp_path / "sample.pdf"
    pdf.write_bytes(b"%PDF-1.5\nTest content\n%%EOF")
    return pdf


@pytest.mark.asyncio
async def test_process_pdf_document_success(orchestrator, sample_pdf):
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
async def test_process_pdf_document_parsing_error(orchestrator, sample_pdf, monkeypatch, caplog):
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


@pytest.mark.asyncio
async def test_process_pdf_document_chunking_error_reports_completed_stage_context(
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
async def test_process_pdf_document_helper(mock_weaviate_client, sample_pdf, monkeypatch):
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
                 patch("src.lib.weaviate_client.documents.update_document_status_detailed", new=AsyncMock()), \
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
