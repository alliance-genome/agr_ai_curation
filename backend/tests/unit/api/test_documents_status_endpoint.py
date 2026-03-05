"""Unit tests for document status endpoint behavior."""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src.api import documents
from src.models.pipeline import PipelineStatus, ProcessingStage


class _DummySession:
    def close(self):
        return None


@pytest.mark.asyncio
async def test_status_endpoint_prefers_pipeline_failed_stage(monkeypatch):
    document_id = "11111111-1111-1111-1111-111111111111"

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
            document_id=document_id,
            current_stage=ProcessingStage.FAILED,
            started_at=now,
            updated_at=now,
            progress_percentage=30,
            message="Pipeline failed",
        )

    monkeypatch.setattr(documents, "SessionLocal", lambda: _DummySession())
    monkeypatch.setattr(documents, "verify_document_ownership", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(documents, "provision_user", lambda *_args, **_kwargs: SimpleNamespace(id="user-1"))
    monkeypatch.setattr(documents, "get_document", fake_get_document)
    monkeypatch.setattr(documents.pipeline_tracker, "get_pipeline_status", fake_pipeline_status)
    monkeypatch.setattr(documents.pdf_job_service, "get_latest_job_for_document", lambda **_kwargs: None)

    result = await documents.get_document_processing_status(document_id, {"sub": "dev-user-123"})
    assert result["processing_status"] == "failed"


@pytest.mark.asyncio
async def test_status_endpoint_maps_upload_stage_to_processing(monkeypatch):
    document_id = "22222222-2222-2222-2222-222222222222"

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
            document_id=document_id,
            current_stage=ProcessingStage.UPLOAD,
            started_at=now,
            updated_at=now,
            progress_percentage=5,
            message="Upload started",
        )

    monkeypatch.setattr(documents, "SessionLocal", lambda: _DummySession())
    monkeypatch.setattr(documents, "verify_document_ownership", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(documents, "provision_user", lambda *_args, **_kwargs: SimpleNamespace(id="user-1"))
    monkeypatch.setattr(documents, "get_document", fake_get_document)
    monkeypatch.setattr(documents.pipeline_tracker, "get_pipeline_status", fake_pipeline_status)
    monkeypatch.setattr(documents.pdf_job_service, "get_latest_job_for_document", lambda **_kwargs: None)

    result = await documents.get_document_processing_status(document_id, {"sub": "dev-user-123"})
    assert result["processing_status"] == "processing"
