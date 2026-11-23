"""Contract tests for GET /weaviate/documents/{document_id}/chunks."""

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
import pytest

from src.lib.document_files.readable import (
    ReadableFile,
    ReadableFiles,
    ReadableFilesError,
)

# A real document id: the endpoint answers 404 for anything that is not a UUID.
DOCUMENT_ID = "6f1c2a8e-3b4d-4e5f-9a7b-1c2d3e4f5a6b"
MAIN_FILE_ID = "0b9e8d7c-6a5f-4e3d-8c2b-1a0f9e8d7c6b"


def _readable(document_id: str = DOCUMENT_ID) -> ReadableFiles:
    """The shape resolve_readable_files returns for a paper with one ready main file."""
    return ReadableFiles(
        document_id=document_id,
        files=(
            ReadableFile(
                file_id=MAIN_FILE_ID,
                display_name="main.pdf",
                role="main",
                chunk_index_start=0,
                chunk_index_end=0,
                kind="PDF",
                label="main.pdf",
            ),
        ),
    )


def _patch_resolver(**kwargs):
    kwargs.setdefault("return_value", _readable())
    return patch("src.api.chunks.resolve_readable_files", **kwargs)


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
    with _patch_resolver() as resolver, patch(
        "src.api.chunks.get_chunks", new_callable=AsyncMock
    ) as mock_get_chunks:
        mock_get_chunks.return_value = {
            "chunks": [_sample_chunk(DOCUMENT_ID)],
            "total": 1,
        }
        response = client.get(f"/weaviate/documents/{DOCUMENT_ID}/chunks?page=1&page_size=20")

    assert response.status_code == 200
    data = response.json()
    assert data["document_id"] == DOCUMENT_ID
    assert data["pagination"]["current_page"] == 1
    assert data["pagination"]["page_size"] == 20
    assert data["pagination"]["total_items"] == 1
    assert data["pagination"]["total_pages"] == 1
    assert len(data["chunks"]) == 1
    assert data["chunks"][0]["id"] == "chunk-1"
    assert data["chunks"][0]["metadata"]["character_count"] == 13
    # The read is scoped to the paper's readable files for this curator.
    resolver.assert_called_once_with(DOCUMENT_ID, "contract-user")
    assert mock_get_chunks.await_args.kwargs["readable"] == _readable()


def test_get_chunks_returns_404_when_none_found(client: TestClient):
    with _patch_resolver(), patch(
        "src.api.chunks.get_chunks", new_callable=AsyncMock
    ) as mock_get_chunks:
        mock_get_chunks.return_value = {"chunks": [], "total": 0}
        response = client.get(f"/weaviate/documents/{DOCUMENT_ID}/chunks?page=1&page_size=20")

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
    with _patch_resolver(), patch(
        "src.api.chunks.get_chunks", new_callable=AsyncMock
    ) as mock_get_chunks:
        mock_get_chunks.side_effect = RuntimeError("backend blew up")
        response = client.get(f"/weaviate/documents/{DOCUMENT_ID}/chunks?page=1&page_size=20")

    assert response.status_code == 500
    assert response.json()["detail"] == "Failed to retrieve chunks"
    assert "backend blew up" not in response.text


def test_get_chunks_answers_404_without_reading_when_no_file_is_readable(client: TestClient):
    with _patch_resolver(side_effect=ReadableFilesError("no readable file")), patch(
        "src.api.chunks.get_chunks", new_callable=AsyncMock
    ) as mock_get_chunks:
        response = client.get(f"/weaviate/documents/{DOCUMENT_ID}/chunks?page=1&page_size=20")

    assert response.status_code == 404
    assert response.json()["detail"] == f"No chunks found for document {DOCUMENT_ID}"
    mock_get_chunks.assert_not_awaited()


def test_get_chunks_answers_404_for_an_id_that_is_not_a_uuid(client: TestClient):
    with _patch_resolver() as resolver, patch(
        "src.api.chunks.get_chunks", new_callable=AsyncMock
    ) as mock_get_chunks:
        response = client.get("/weaviate/documents/doc-123/chunks?page=1&page_size=20")

    assert response.status_code == 404
    assert response.json()["detail"] == "No chunks found for document doc-123"
    resolver.assert_not_called()
    mock_get_chunks.assert_not_awaited()
