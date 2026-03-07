"""Contract tests for GET /weaviate/documents/{document_id}/progress/stream."""

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
import pytest

from src.models.pipeline import PipelineStatus, ProcessingStage
from src.models.sql.pdf_processing_job import PdfJobStatus


@pytest.fixture
def client():
    from main import app
    from src.api.auth import auth

    app.dependency_overrides[auth.get_user] = lambda: {
        "sub": "contract-user",
        "uid": "contract-user",
        "email": "contract@test.local",
        "name": "Contract User",
        "groups": ["developers"],
        "cognito:groups": ["developers"],
    }
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(auth.get_user, None)


def _sse_events(response) -> list[dict]:
    events: list[dict] = []
    for line in response.iter_lines():
        if not line:
            continue
        if line.startswith("data: "):
            events.append(json.loads(line[6:]))
    return events


def test_progress_stream_returns_not_found_event_when_document_missing(client: TestClient):
    with patch("src.api.documents.get_document", new_callable=AsyncMock) as mock_get_document:
        mock_get_document.return_value = None
        with client.stream("GET", "/weaviate/documents/doc-missing/progress/stream") as response:
            events = _sse_events(response)

    assert response.status_code == 200
    assert events
    assert events[0]["error"] == "Document not found"
    assert events[0]["document_id"] == "doc-missing"


def test_progress_stream_emits_waiting_and_timeout(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PDF_PROCESSING_SSE_POLL_INTERVAL_SECONDS", "1")
    monkeypatch.setenv("PDF_PROCESSING_SSE_TIMEOUT_SECONDS", "1")

    with patch("src.api.documents.get_document", new_callable=AsyncMock) as mock_get_document, \
         patch("src.api.documents.pipeline_tracker.get_pipeline_status", new_callable=AsyncMock) as mock_status, \
         patch("src.api.documents.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        mock_get_document.return_value = {"document": {"id": "doc-1"}}
        mock_status.return_value = None
        mock_sleep.return_value = None
        with client.stream("GET", "/weaviate/documents/doc-1/progress/stream") as response:
            events = _sse_events(response)

    assert response.status_code == 200
    assert any(event.get("stage") == "waiting" for event in events)
    timeout_events = [event for event in events if event.get("stage") == "timeout"]
    assert timeout_events
    assert timeout_events[-1]["final"] is True


def test_progress_stream_emits_completed_final_event(client: TestClient):
    now = datetime.now(timezone.utc)
    completed_status = PipelineStatus(
        document_id="doc-2",
        current_stage=ProcessingStage.COMPLETED,
        started_at=now,
        updated_at=now,
        progress_percentage=100,
        message="done",
    )

    with patch("src.api.documents.get_document", new_callable=AsyncMock) as mock_get_document, \
         patch("src.api.documents.pipeline_tracker.get_pipeline_status", new_callable=AsyncMock) as mock_status:
        mock_get_document.return_value = {"document": {"id": "doc-2"}}
        mock_status.return_value = completed_status
        with client.stream("GET", "/weaviate/documents/doc-2/progress/stream") as response:
            events = _sse_events(response)

    assert response.status_code == 200
    assert any(event.get("stage") == "completed" for event in events)
    final_events = [event for event in events if event.get("final") is True]
    assert final_events
    assert final_events[-1]["stage"] == "completed"
    assert final_events[-1]["source"] == "pipeline"


def test_progress_stream_pipeline_terminal_fallback_message_and_source(client: TestClient):
    now = datetime.now(timezone.utc)
    completed_status = PipelineStatus(
        document_id="doc-2b",
        current_stage=ProcessingStage.COMPLETED,
        started_at=now,
        updated_at=now,
        progress_percentage=100,
        message="",
    )

    with patch("src.api.documents.get_document", new_callable=AsyncMock) as mock_get_document, \
         patch("src.api.documents.pipeline_tracker.get_pipeline_status", new_callable=AsyncMock) as mock_status:
        mock_get_document.return_value = {"document": {"id": "doc-2b"}}
        mock_status.return_value = completed_status
        with client.stream("GET", "/weaviate/documents/doc-2b/progress/stream") as response:
            events = _sse_events(response)

    assert response.status_code == 200
    final_events = [event for event in events if event.get("final") is True]
    assert final_events
    assert final_events[-1]["message"] == "Processing completed successfully"
    assert final_events[-1]["source"] == "pipeline"


def test_progress_stream_prefers_terminal_durable_job_over_pipeline(client: TestClient):
    document_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    now = datetime.now(timezone.utc)
    running_status = PipelineStatus(
        document_id=document_id,
        current_stage=ProcessingStage.EMBEDDING,
        started_at=now,
        updated_at=now,
        progress_percentage=70,
        message="tracker still running",
    )
    terminal_job = SimpleNamespace(
        status=PdfJobStatus.FAILED.value,
        current_stage="embedding",
        progress_percentage=70,
        message="job failed",
        error_message="durable failure",
        updated_at=now,
    )

    with patch("src.api.documents.get_document", new_callable=AsyncMock) as mock_get_document, \
         patch("src.api.documents.pipeline_tracker.get_pipeline_status", new_callable=AsyncMock) as mock_status, \
         patch("src.api.documents.pdf_job_service.get_latest_job_for_document") as mock_job:
        mock_get_document.return_value = {"document": {"id": document_id}}
        mock_status.return_value = running_status
        mock_job.return_value = terminal_job
        with client.stream("GET", f"/weaviate/documents/{document_id}/progress/stream") as response:
            events = _sse_events(response)

    assert response.status_code == 200
    final_events = [event for event in events if event.get("final") is True]
    assert final_events
    assert final_events[-1]["stage"] == "failed"
    assert "durable failure" in final_events[-1]["message"]
    assert final_events[-1]["source"] == "job"


def test_progress_stream_ignores_stale_terminal_pipeline_when_job_active(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    document_id = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    now = datetime.now(timezone.utc)
    stale_terminal_status = PipelineStatus(
        document_id=document_id,
        current_stage=ProcessingStage.COMPLETED,
        started_at=now,
        updated_at=now,
        progress_percentage=100,
        message="stale completed tracker",
    )
    active_job = SimpleNamespace(
        status=PdfJobStatus.RUNNING.value,
        current_stage="parsing",
        progress_percentage=22,
        message="durable job running",
        error_message=None,
        updated_at=now,
    )

    monkeypatch.setenv("PDF_PROCESSING_SSE_POLL_INTERVAL_SECONDS", "1")
    monkeypatch.setenv("PDF_PROCESSING_SSE_TIMEOUT_SECONDS", "1")

    with patch("src.api.documents.get_document", new_callable=AsyncMock) as mock_get_document, \
         patch("src.api.documents.pipeline_tracker.get_pipeline_status", new_callable=AsyncMock) as mock_status, \
         patch("src.api.documents.pdf_job_service.get_latest_job_for_document") as mock_job, \
         patch("src.api.documents.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        mock_get_document.return_value = {"document": {"id": document_id}}
        mock_status.return_value = stale_terminal_status
        mock_job.return_value = active_job
        mock_sleep.return_value = None
        with client.stream("GET", f"/weaviate/documents/{document_id}/progress/stream") as response:
            events = _sse_events(response)

    assert response.status_code == 200
    assert any(event.get("stage") == "parsing" for event in events)
    final_events = [event for event in events if event.get("final") is True]
    assert final_events
    assert final_events[-1]["stage"] == "timeout"
