"""Contract tests for POST /weaviate/documents/{document_id}/reprocess."""

from datetime import datetime, timezone
from pathlib import Path
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


def _ready_document(processing_status: str = "completed", filename: str = "paper.pdf") -> dict:
    return {
        "document": {
            "id": "11111111-1111-1111-1111-111111111111",
            "filename": filename,
            "processing_status": processing_status,
        },
        "total_chunks": 3,
    }


def test_reprocess_success_returns_operation_result(client: TestClient, tmp_path: Path):
    document_id = "11111111-1111-1111-1111-111111111111"
    base_storage = tmp_path / "pdf_storage"
    file_path = base_storage / "contract-user" / document_id / "paper.pdf"
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_bytes(b"%PDF-1.7")

    with patch("src.api.processing.get_document", new_callable=AsyncMock) as mock_get_document, \
         patch("src.api.processing.get_pdf_storage_path", return_value=base_storage), \
         patch("src.api.processing.update_document_status", new_callable=AsyncMock) as mock_update_status, \
         patch("src.api.processing.pipeline_tracker.track_pipeline_progress", new_callable=AsyncMock) as mock_track, \
         patch("src.api.processing.BackgroundTasks.add_task", autospec=True, return_value=None):
        mock_get_document.return_value = _ready_document()
        response = client.post(
            f"/weaviate/documents/{document_id}/reprocess",
            json={"strategy_name": "research", "force_reparse": False},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["document_id"] == document_id
    assert "reprocessing initiated" in data["message"].lower()
    mock_update_status.assert_awaited_once()
    mock_track.assert_awaited_once()


def test_reprocess_returns_404_when_document_missing(client: TestClient):
    with patch("src.api.processing.get_document", new_callable=AsyncMock) as mock_get_document:
        mock_get_document.return_value = None
        response = client.post(
            "/weaviate/documents/doc-missing/reprocess",
            json={"strategy_name": "research", "force_reparse": False},
        )

    assert response.status_code == 404


def test_reprocess_returns_409_when_processing_active(client: TestClient):
    document_id = "11111111-1111-1111-1111-111111111111"
    now = datetime.now(timezone.utc)
    with patch("src.api.processing.get_document", new_callable=AsyncMock) as mock_get_document, \
         patch("src.api.processing.pipeline_tracker.get_pipeline_status", new_callable=AsyncMock) as mock_pipeline_status:
        mock_get_document.return_value = _ready_document(processing_status="processing")
        mock_pipeline_status.return_value = PipelineStatus(
            document_id="doc-1",
            current_stage=ProcessingStage.CHUNKING,
            started_at=now,
            updated_at=now,
            progress_percentage=50,
            message="chunking",
        )
        response = client.post(
            f"/weaviate/documents/{document_id}/reprocess",
            json={"strategy_name": "research", "force_reparse": False},
        )

    assert response.status_code == 409
    assert "currently being processed" in response.json()["detail"].lower()


def test_reprocess_returns_404_when_source_file_missing(client: TestClient, tmp_path: Path):
    document_id = "11111111-1111-1111-1111-111111111111"
    base_storage = tmp_path / "pdf_storage"
    with patch("src.api.processing.get_document", new_callable=AsyncMock) as mock_get_document, \
         patch("src.api.processing.get_pdf_storage_path", return_value=base_storage):
        mock_get_document.return_value = _ready_document(filename="missing.pdf")
        response = client.post(
            f"/weaviate/documents/{document_id}/reprocess",
            json={"strategy_name": "research", "force_reparse": True},
        )

    assert response.status_code == 404
    assert "source file not found" in response.json()["detail"].lower()
