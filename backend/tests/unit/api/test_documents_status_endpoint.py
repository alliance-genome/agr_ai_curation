"""Unit tests for document status endpoint behavior."""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src.api import documents
from src.models.pipeline import PipelineStatus, ProcessingStage
from src.models.sql.pdf_processing_job import PdfJobStatus


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


@pytest.mark.asyncio
async def test_status_endpoint_prefers_terminal_job_over_pipeline_conflict(monkeypatch):
    document_id = "33333333-3333-3333-3333-333333333333"

    async def fake_get_document(_user_sub, _document_id):
        return {
            "document": {
                "processing_status": "processing",
                "embedding_status": "pending",
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
            progress_percentage=70,
            message="stale tracker failure",
        )

    now = datetime.now(timezone.utc)
    terminal_job = SimpleNamespace(
        status=PdfJobStatus.COMPLETED.value,
        current_stage="completed",
        progress_percentage=100,
        message="Job complete",
        error_message=None,
        cancel_requested=False,
        updated_at=now,
        started_at=now,
        completed_at=now,
        document_id=document_id,
        job_id="job-333",
    )

    monkeypatch.setattr(documents, "SessionLocal", lambda: _DummySession())
    monkeypatch.setattr(
        documents,
        "verify_document_ownership",
        lambda *_args, **_kwargs: SimpleNamespace(status="processing"),
    )
    monkeypatch.setattr(documents, "provision_user", lambda *_args, **_kwargs: SimpleNamespace(id="user-1"))
    monkeypatch.setattr(documents, "get_document", fake_get_document)
    monkeypatch.setattr(documents.pipeline_tracker, "get_pipeline_status", fake_pipeline_status)
    monkeypatch.setattr(documents.pdf_job_service, "get_latest_job_for_document", lambda **_kwargs: terminal_job)

    result = await documents.get_document_processing_status(document_id, {"sub": "dev-user-123"})

    assert result["processing_status"] == "completed"
    assert result["pipeline_status"]["current_stage"] == "completed"
    assert result["job_status"] == "completed"


@pytest.mark.asyncio
async def test_status_endpoint_prefers_active_job_when_pipeline_is_stale_terminal(monkeypatch):
    document_id = "44444444-4444-4444-4444-444444444444"

    async def fake_get_document(_user_sub, _document_id):
        return {
            "document": {
                "processing_status": "processing",
                "embedding_status": "pending",
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
            progress_percentage=88,
            message="stale terminal tracker",
        )

    now = datetime.now(timezone.utc)
    active_job = SimpleNamespace(
        status=PdfJobStatus.RUNNING.value,
        current_stage="parsing",
        progress_percentage=22,
        message="Job still running",
        error_message=None,
        cancel_requested=False,
        updated_at=now,
        started_at=now,
        completed_at=None,
        document_id=document_id,
        job_id="job-444",
    )

    monkeypatch.setattr(documents, "SessionLocal", lambda: _DummySession())
    monkeypatch.setattr(
        documents,
        "verify_document_ownership",
        lambda *_args, **_kwargs: SimpleNamespace(status="processing"),
    )
    monkeypatch.setattr(documents, "provision_user", lambda *_args, **_kwargs: SimpleNamespace(id="user-1"))
    monkeypatch.setattr(documents, "get_document", fake_get_document)
    monkeypatch.setattr(documents.pipeline_tracker, "get_pipeline_status", fake_pipeline_status)
    monkeypatch.setattr(documents.pdf_job_service, "get_latest_job_for_document", lambda **_kwargs: active_job)

    result = await documents.get_document_processing_status(document_id, {"sub": "dev-user-123"})

    assert result["processing_status"] == "processing"
    assert result["pipeline_status"]["current_stage"] == "parsing"
    assert result["job_status"] == "running"
