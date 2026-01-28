"""Unit tests for the document pipeline orchestrator."""

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

    with patch("src.lib.pipeline.docling_parser.parse_pdf_document", new=AsyncMock(return_value={
        "elements": [{"text": "Foo"}],
        "docling_json_path": "docling/doc.json",
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

    mock_parser.assert_awaited_once()
    mock_chunk.assert_awaited_once()
    mock_store.assert_awaited_once()
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
async def test_process_pdf_document_parsing_error(orchestrator, sample_pdf):
    with patch("src.lib.pipeline.docling_parser.parse_pdf_document", new=AsyncMock(side_effect=RuntimeError("boom"))):
        result = await orchestrator.process_pdf_document(sample_pdf, "doc-1", "test_user", validate_first=False)

    assert result.success is False
    assert ProcessingStage.PARSING not in result.stages_completed
    assert "boom" in result.error


def test_validate_pipeline_requirements(orchestrator):
    checks = orchestrator.validate_pipeline_requirements()
    assert checks["weaviate_connected"] is True
    assert checks["embedding_service_available"] is True
    assert isinstance(checks["storage_writable"], bool)


@pytest.mark.asyncio
async def test_process_pdf_document_helper(mock_weaviate_client, sample_pdf):
    with patch("src.lib.pipeline.docling_parser.parse_pdf_document", new=AsyncMock(return_value={
        "elements": [],
        "docling_json_path": None,
        "processed_json_path": None,
    })), patch("src.lib.pipeline.chunk.chunk_parsed_document", new=AsyncMock(return_value=[])), patch(
        "src.lib.pipeline.store.store_to_weaviate",
        new=AsyncMock()
    ):
        result = await process_pdf_document(sample_pdf, "doc-xyz", mock_weaviate_client, "test_user")

    assert result.success is True
    assert result.total_chunks == 0
