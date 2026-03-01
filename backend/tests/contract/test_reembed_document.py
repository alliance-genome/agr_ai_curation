"""Contract tests for POST /weaviate/documents/{document_id}/reembed."""

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


def _ready_document(processing_status: str = "completed", total_chunks: int = 3) -> dict:
    return {
        "document": {
            "id": "doc-1",
            "filename": "paper.pdf",
            "processing_status": processing_status,
        },
        "total_chunks": total_chunks,
    }


def test_reembed_success_with_custom_embedding_config(client: TestClient):
    request_body = {
        "embedding_config": {
            "model_provider": "openai",
            "model_name": "text-embedding-3-small",
            "dimensions": 1536,
            "batch_size": 7,
        },
        "batch_size": 11,
    }

    with patch("src.api.processing.get_document", new_callable=AsyncMock) as mock_get_document, \
         patch("src.api.processing.update_document_status", new_callable=AsyncMock) as mock_update_status, \
         patch("src.api.processing.re_embed_document", new_callable=AsyncMock) as mock_reembed, \
         patch("src.api.processing.pipeline_tracker.track_pipeline_progress", new_callable=AsyncMock) as mock_track:
        mock_get_document.return_value = _ready_document()
        mock_reembed.return_value = {"total_chunks": 3}
        response = client.post("/weaviate/documents/doc-1/reembed", json=request_body)

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["document_id"] == "doc-1"
    mock_update_status.assert_awaited_once()
    mock_reembed.assert_awaited_once()
    args = mock_reembed.await_args.args
    kwargs = mock_reembed.await_args.kwargs
    assert args == ("doc-1", "contract-user")
    assert kwargs["batch_size"] == 11
    assert kwargs["embedding_config"].model_provider == "openai"
    assert kwargs["embedding_config"].model_name == "text-embedding-3-small"
    assert kwargs["embedding_config"].dimensions == 1536
    assert kwargs["embedding_config"].batch_size == 7
    mock_track.assert_awaited_once()


def test_reembed_returns_404_when_document_missing(client: TestClient):
    with patch("src.api.processing.get_document", new_callable=AsyncMock) as mock_get_document:
        mock_get_document.return_value = None
        response = client.post("/weaviate/documents/doc-missing/reembed", json={})

    assert response.status_code == 404


def test_reembed_returns_409_when_processing_active(client: TestClient):
    now = datetime.now(timezone.utc)
    with patch("src.api.processing.get_document", new_callable=AsyncMock) as mock_get_document, \
         patch("src.api.processing.pipeline_tracker.get_pipeline_status", new_callable=AsyncMock) as mock_pipeline_status:
        mock_get_document.return_value = _ready_document(processing_status="processing")
        mock_pipeline_status.return_value = PipelineStatus(
            document_id="doc-1",
            current_stage=ProcessingStage.EMBEDDING,
            started_at=now,
            updated_at=now,
            progress_percentage=40,
            message="embedding",
        )
        response = client.post("/weaviate/documents/doc-1/reembed", json={})

    assert response.status_code == 409
    assert "currently being processed" in response.json()["detail"].lower()


def test_reembed_returns_400_when_no_chunks(client: TestClient):
    with patch("src.api.processing.get_document", new_callable=AsyncMock) as mock_get_document:
        mock_get_document.return_value = _ready_document(total_chunks=0)
        response = client.post("/weaviate/documents/doc-1/reembed", json={})

    assert response.status_code == 400
    assert "no chunks" in response.json()["detail"].lower()
