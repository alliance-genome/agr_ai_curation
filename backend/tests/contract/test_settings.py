"""Contract tests for Weaviate settings endpoints."""

from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest


@pytest.fixture
def client():
    """Create test client with authenticated contract user."""
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


def test_get_settings_response_shape(client: TestClient):
    response = client.get("/weaviate/settings")
    assert response.status_code == 200
    data = response.json()

    assert "embedding" in data
    assert "database" in data
    assert "available_models" in data

    embedding = data["embedding"]
    assert "model_provider" in embedding
    assert "model_name" in embedding
    assert "dimensions" in embedding
    assert "batch_size" in embedding


def test_put_settings_updates_embedding_config(client: TestClient):
    payload = {
        "embedding_config": {
            "model_provider": "openai",
            "model_name": "text-embedding-3-small",
            "dimensions": 1536,
            "batch_size": 8,
        }
    }
    response = client.put("/weaviate/settings", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert "embedding_configuration" in data["updated"]


def test_put_settings_updates_database_settings_acknowledged(client: TestClient):
    payload = {
        "database_settings": {
            "collection_name": "PDFDocument",
            "schema_version": "1.0.0",
            "replication_factor": 1,
            "consistency": "eventual",
            "vector_index_type": "hnsw",
        }
    }
    response = client.put("/weaviate/settings", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert "database_settings" in data["updated"]


def test_put_settings_no_payload_returns_warning(client: TestClient):
    response = client.put("/weaviate/settings", json={})
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is False
    assert any("no settings provided" in warning.lower() for warning in data["warnings"])


def test_put_settings_invalid_model_rejected(client: TestClient):
    payload = {
        "embedding_config": {
            "model_provider": "openai",
            "model_name": "not-a-real-model",
            "dimensions": 1536,
            "batch_size": 8,
        }
    }
    response = client.put("/weaviate/settings", json=payload)
    assert response.status_code == 400
    assert "not available" in response.json()["detail"].lower()


def test_settings_endpoints_require_auth_when_not_overridden():
    from main import app
    from src.api.auth import auth

    def _raise_unauth():
        raise HTTPException(status_code=401, detail="Not authenticated")

    app.dependency_overrides[auth.get_user] = _raise_unauth
    try:
        client = TestClient(app)
        assert client.get("/weaviate/settings").status_code == 401
        assert client.put("/weaviate/settings", json={}).status_code == 401
    finally:
        app.dependency_overrides.pop(auth.get_user, None)
