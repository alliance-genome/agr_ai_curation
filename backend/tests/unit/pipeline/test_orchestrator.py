"""Unit tests for the document pipeline orchestrator."""

import logging
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from src.lib.pipeline.orchestrator import DocumentPipelineOrchestrator, process_pdf_document
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
    orchestrator._sync_sql_document_status.assert_any_await(document_id, status="processing")
    orchestrator._sync_sql_document_status.assert_any_await(document_id, status="completed")

    mock_parser.assert_awaited_once()
    mock_chunk.assert_awaited_once()
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


def test_validate_pipeline_requirements(orchestrator):
    checks = orchestrator.validate_pipeline_requirements()
    assert checks["weaviate_connected"] is True
    assert checks["embedding_service_available"] is True
    assert isinstance(checks["storage_writable"], bool)


@pytest.mark.asyncio
async def test_process_pdf_document_helper(mock_weaviate_client, sample_pdf):
    with patch("src.lib.pipeline.pdfx_parser.parse_pdf_document", new=AsyncMock(return_value={
        "elements": [],
        "pdfx_json_path": None,
        "processed_json_path": None,
    })), patch("src.lib.pipeline.chunk.chunk_parsed_document", new=AsyncMock(return_value=[])), patch(
        "src.lib.pipeline.store.store_to_weaviate",
        new=AsyncMock()
    ):
        result = await process_pdf_document(sample_pdf, "doc-xyz", mock_weaviate_client, "test_user")

    assert result.success is True
    assert result.total_chunks == 0
