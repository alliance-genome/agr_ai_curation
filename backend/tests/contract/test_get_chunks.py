"""Contract tests for GET /weaviate/documents/{document_id}/chunks."""

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
import pytest


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


def _sample_chunk(document_id: str = "doc-1") -> dict:
    return {
        "id": "chunk-1",
        "document_id": document_id,
        "chunk_index": 0,
        "content": "chunk content",
        "element_type": "Title",
        "page_number": 1,
        "metadata": {
            "character_count": 13,
            "word_count": 2,
            "has_table": False,
            "has_image": False,
        },
    }


def test_get_chunks_success_shape_and_pagination(client: TestClient):
    with patch("src.api.chunks.get_chunks", new_callable=AsyncMock) as mock_get_chunks:
        mock_get_chunks.return_value = {
            "chunks": [_sample_chunk("doc-123")],
            "total": 1,
        }
        response = client.get("/weaviate/documents/doc-123/chunks?page=1&page_size=20")

    assert response.status_code == 200
    data = response.json()
    assert data["document_id"] == "doc-123"
    assert data["pagination"]["current_page"] == 1
    assert data["pagination"]["page_size"] == 20
    assert data["pagination"]["total_items"] == 1
    assert len(data["chunks"]) == 1
    assert data["chunks"][0]["id"] == "chunk-1"
    assert data["chunks"][0]["metadata"]["character_count"] == 13


def test_get_chunks_returns_404_when_none_found(client: TestClient):
    with patch("src.api.chunks.get_chunks", new_callable=AsyncMock) as mock_get_chunks:
        mock_get_chunks.return_value = {"chunks": [], "total": 0}
        response = client.get("/weaviate/documents/doc-404/chunks?page=1&page_size=20")

    assert response.status_code == 404
    assert "No chunks found" in response.json()["detail"]


def test_get_chunks_requires_authenticated_sub(client: TestClient):
    from main import app
    from src.api.auth import auth

    app.dependency_overrides[auth.get_user] = lambda: {}
    try:
        response = client.get("/weaviate/documents/doc-1/chunks?page=1&page_size=20")
    finally:
        app.dependency_overrides.pop(auth.get_user, None)

    assert response.status_code == 401
    assert "authentication required" in response.json()["detail"].lower()


def test_get_chunks_bubbles_internal_error_as_500(client: TestClient):
    with patch("src.api.chunks.get_chunks", new_callable=AsyncMock) as mock_get_chunks:
        mock_get_chunks.side_effect = RuntimeError("backend blew up")
        response = client.get("/weaviate/documents/doc-500/chunks?page=1&page_size=20")

    assert response.status_code == 500
    assert response.json()["detail"] == "Failed to retrieve chunks"
    assert "backend blew up" not in response.text
