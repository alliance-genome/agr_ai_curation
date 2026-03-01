"""Contract tests for GET /weaviate/documents/{document_id}/progress/stream."""

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
import pytest

from src.models.pipeline import PipelineStatus, ProcessingStage


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
