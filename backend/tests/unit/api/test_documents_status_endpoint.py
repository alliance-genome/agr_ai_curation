"""Unit tests for document status endpoint behavior."""

from datetime import datetime, timezone

import pytest

from src.api import documents
from src.models.pipeline import PipelineStatus, ProcessingStage


class _DummySession:
    def close(self):
        return None


@pytest.mark.asyncio
async def test_status_endpoint_prefers_pipeline_failed_stage(monkeypatch):
    async def fake_get_document(_user_sub, _document_id):
        return {
            "document": {
                "processing_status": "pending",
                "embedding_status": "failed",
                "vector_count": 0,
            },
            "total_chunks": 0,
        }

    async def fake_pipeline_status(_document_id):
        now = datetime.now(timezone.utc)
        return PipelineStatus(
            document_id="doc-1",
            current_stage=ProcessingStage.FAILED,
            started_at=now,
            updated_at=now,
            progress_percentage=30,
            message="Pipeline failed",
        )

    monkeypatch.setattr(documents, "SessionLocal", lambda: _DummySession())
    monkeypatch.setattr(documents, "verify_document_ownership", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(documents, "get_document", fake_get_document)
    monkeypatch.setattr(documents.pipeline_tracker, "get_pipeline_status", fake_pipeline_status)

    result = await documents.get_document_processing_status("doc-1", {"sub": "dev-user-123"})
    assert result["processing_status"] == "failed"


@pytest.mark.asyncio
async def test_status_endpoint_maps_upload_stage_to_processing(monkeypatch):
    async def fake_get_document(_user_sub, _document_id):
        return {
            "document": {
                "processing_status": "pending",
                "embedding_status": "pending",
                "vector_count": 0,
            },
            "total_chunks": 0,
        }

    async def fake_pipeline_status(_document_id):
        now = datetime.now(timezone.utc)
        return PipelineStatus(
            document_id="doc-2",
            current_stage=ProcessingStage.UPLOAD,
            started_at=now,
            updated_at=now,
            progress_percentage=5,
            message="Upload started",
        )

    monkeypatch.setattr(documents, "SessionLocal", lambda: _DummySession())
    monkeypatch.setattr(documents, "verify_document_ownership", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(documents, "get_document", fake_get_document)
    monkeypatch.setattr(documents.pipeline_tracker, "get_pipeline_status", fake_pipeline_status)

    result = await documents.get_document_processing_status("doc-2", {"sub": "dev-user-123"})
    assert result["processing_status"] == "processing"

