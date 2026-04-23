"""Contract tests for health check endpoints."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture
def client(monkeypatch):
    """Create test client with mocked dependencies."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("EMBEDDING_MODEL", "text-embedding-3-small")
    monkeypatch.setenv("EMBEDDING_TOKEN_PREFLIGHT_ENABLED", "true")
    monkeypatch.setenv("EMBEDDING_MODEL_TOKEN_LIMIT", "8191")
    monkeypatch.setenv("EMBEDDING_TOKEN_SAFETY_MARGIN", "500")
    monkeypatch.setenv("CONTENT_PREVIEW_CHARS", "1600")

    from fastapi.testclient import TestClient
    import os
    import sys

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    from main import app

    return TestClient(app)


@pytest.fixture
def mock_weaviate_connection():
    """Mock module-local get_connection used by src.api.health."""
    with patch("src.api.health.get_connection") as mock:
        connection = MagicMock()
        mock.return_value = connection
        yield connection


class TestHealthEndpoint:
    """Tests for /weaviate/health endpoint."""

    def test_health_check_healthy(self, client, mock_weaviate_connection):
        mock_weaviate_connection.health_check = AsyncMock(
            return_value={"status": "healthy", "version": "1.19.0", "nodes": 1, "collections": 2}
        )

        response = client.get("/weaviate/health")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "healthy"
        assert data["service"] == "Weaviate Control Panel API"
        assert data["version"] == "0.1.0"
        assert "timestamp" in data
        assert data["checks"]["api"] == "healthy"
        assert data["checks"]["weaviate"] == "healthy"
        assert "weaviate" in data["details"]
        assert "environment" in data["details"]
        assert isinstance(data["cognito_configured"], bool)

    def test_health_check_weaviate_unhealthy(self, client, mock_weaviate_connection):
        mock_weaviate_connection.health_check = AsyncMock(
            return_value={"status": "unhealthy", "message": "Connection timeout"}
        )

        response = client.get("/weaviate/health")
        assert response.status_code == 503

        data = response.json()["detail"]
        assert data["status"] == "unhealthy"
        assert data["checks"]["weaviate"] == "unhealthy"

    def test_health_check_weaviate_error(self, client, mock_weaviate_connection):
        mock_weaviate_connection.health_check = AsyncMock(side_effect=Exception("Connection failed"))

        response = client.get("/weaviate/health")
        assert response.status_code == 503

        data = response.json()["detail"]
        assert data["status"] == "unhealthy"
        assert data["checks"]["weaviate"] == "unhealthy"
        assert data["details"]["weaviate"]["error"] == "Weaviate health check failed"
        assert data["details"]["weaviate"]["message"] == "Weaviate connection not ready"
        assert "connection failed" not in response.text.lower()

    def test_health_check_response_schema(self, client, mock_weaviate_connection):
        mock_weaviate_connection.health_check = AsyncMock(
            return_value={"status": "healthy", "version": "1.19.0"}
        )

        response = client.get("/weaviate/health")
        assert response.status_code == 200
        data = response.json()

        assert "status" in data
        assert "timestamp" in data
        assert "service" in data
        assert "version" in data
        assert "cognito_configured" in data
        assert "checks" in data
        assert "details" in data
        datetime.fromisoformat(data["timestamp"].replace("Z", "+00:00"))

    def test_health_check_cognito_configured_true(self, client, mock_weaviate_connection, monkeypatch):
        monkeypatch.setenv("COGNITO_USER_POOL_ID", "us-east-1_test123")
        monkeypatch.setenv("COGNITO_CLIENT_ID", "test-client-id-12345")
        mock_weaviate_connection.health_check = AsyncMock(return_value={"status": "healthy"})

        response = client.get("/weaviate/health")
        assert response.status_code == 200
        assert response.json()["cognito_configured"] is True

    def test_health_check_cognito_configured_false(self, client, mock_weaviate_connection, monkeypatch):
        monkeypatch.delenv("COGNITO_USER_POOL_ID", raising=False)
        monkeypatch.delenv("COGNITO_CLIENT_ID", raising=False)
        mock_weaviate_connection.health_check = AsyncMock(return_value={"status": "healthy"})

        response = client.get("/weaviate/health")
        assert response.status_code == 200
        assert response.json()["cognito_configured"] is False

    def test_health_check_no_auth_required(self, client, mock_weaviate_connection):
        mock_weaviate_connection.health_check = AsyncMock(return_value={"status": "healthy"})

        response = client.get("/weaviate/health")
        assert response.status_code == 200

    def test_health_check_no_auth_even_with_invalid_token(self, client, mock_weaviate_connection):
        mock_weaviate_connection.health_check = AsyncMock(return_value={"status": "healthy"})

        response = client.get(
            "/weaviate/health",
            headers={"Authorization": "Bearer invalid_or_expired_token_123"},
        )
        assert response.status_code == 200


class TestReadinessEndpoint:
    """Tests for /weaviate/readiness endpoint."""

    def test_readiness_check_ready(self, client, mock_weaviate_connection):
        mock_weaviate_connection.health_check = AsyncMock(return_value={"status": "healthy"})

        response = client.get("/weaviate/readiness")
        assert response.status_code == 200
        data = response.json()
        assert data["ready"] is True
        datetime.fromisoformat(data["timestamp"].replace("Z", "+00:00"))

    def test_readiness_check_not_ready(self, client, mock_weaviate_connection):
        mock_weaviate_connection.health_check = AsyncMock(return_value={"status": "unhealthy"})

        response = client.get("/weaviate/readiness")
        assert response.status_code == 503
        data = response.json()["detail"]
        assert data["ready"] is False
        assert data["reason"] == "Weaviate connection not ready"

    def test_readiness_check_connection_error(self, client, mock_weaviate_connection):
        mock_weaviate_connection.health_check = AsyncMock(side_effect=Exception("Connection timeout"))

        response = client.get("/weaviate/readiness")
        assert response.status_code == 503
        data = response.json()["detail"]
        assert data["ready"] is False
        assert data["reason"] == "Weaviate connection not ready"
        assert "connection timeout" not in response.text.lower()

    def test_readiness_check_none_response(self, client, mock_weaviate_connection):
        mock_weaviate_connection.health_check = AsyncMock(return_value=None)

        response = client.get("/weaviate/readiness")
        assert response.status_code == 503
        data = response.json()["detail"]
        assert data["ready"] is False
        assert data["reason"] == "Weaviate connection not ready"

    def test_readiness_is_lightweight(self, client, mock_weaviate_connection):
        mock_weaviate_connection.health_check = AsyncMock(return_value={"status": "healthy"})

        response = client.get("/weaviate/readiness")
        assert response.status_code == 200
        data = response.json()
        assert sorted(data.keys()) == ["ready", "timestamp"]


class TestMainHealthEndpoint:
    """Tests for the main app health endpoints."""

    def test_liveness_health_is_lightweight(self, client):
        response = client.get("/health")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "healthy"
        assert data["service"] == "AI Curation Platform API"
        assert data["checks"] == {"app": "running"}
        assert "services" not in data
        assert "timestamp" in data

    def test_liveness_alias_matches_health(self, client):
        response = client.get("/health/live")
        assert response.status_code == 200
        assert response.json()["checks"] == {"app": "running"}

    def test_liveness_health_does_not_depend_on_deep_services(self, client, monkeypatch):
        def _unexpected_call(*args, **kwargs):
            raise AssertionError("deep dependency should not be touched by /health")

        monkeypatch.setattr("src.lib.weaviate_client.connection.get_connection", _unexpected_call)
        monkeypatch.setattr("src.lib.database.curation_resolver.get_curation_resolver", _unexpected_call)

        async def _unexpected_redis(*args, **kwargs):
            raise AssertionError("redis should not be touched by /health")

        monkeypatch.setattr("src.lib.redis_client.get_redis", _unexpected_redis)

        for path in ("/health", "/health/live"):
            response = client.get(path)
            assert response.status_code == 200
            assert response.json()["checks"] == {"app": "running"}

    def test_deep_health_reports_curation_db_not_configured(self, client, monkeypatch):
        monkeypatch.setenv("CURATION_DB_CREDENTIALS_SOURCE", "env")
        monkeypatch.delenv("CURATION_DB_URL", raising=False)
        monkeypatch.delenv("PERSISTENT_STORE_DB_HOST", raising=False)
        monkeypatch.delenv("PERSISTENT_STORE_DB_PORT", raising=False)
        monkeypatch.delenv("PERSISTENT_STORE_DB_NAME", raising=False)
        monkeypatch.delenv("PERSISTENT_STORE_DB_USERNAME", raising=False)
        monkeypatch.delenv("PERSISTENT_STORE_DB_PASSWORD", raising=False)

        from src.lib.config.connections_loader import reset_cache as reset_connections_cache
        from src.lib.database.curation_resolver import reset_curation_resolver

        reset_connections_cache()
        reset_curation_resolver()

        response = client.get("/health/deep")
        assert response.status_code == 200
        data = response.json()
        assert data["services"]["curation_db"] == "not_configured"

        reset_connections_cache()
        reset_curation_resolver()

    def test_deep_health_reports_degraded_when_curation_db_disconnected(self, client, monkeypatch):
        monkeypatch.setenv("CURATION_DB_CREDENTIALS_SOURCE", "env")
        monkeypatch.setenv("CURATION_DB_URL", "postgresql://127.0.0.1:59999/nonexistent")

        from src.lib.config.connections_loader import reset_cache as reset_connections_cache
        from src.lib.database.curation_resolver import reset_curation_resolver

        reset_connections_cache()
        reset_curation_resolver()

        response = client.get("/health/deep")
        assert response.status_code == 200

        data = response.json()
        assert data["services"]["curation_db"] in ("error", "disconnected")
        assert data["status"] == "degraded"

        reset_connections_cache()
        reset_curation_resolver()


class TestHealthEndpointEdgeCases:
    """Edge case tests for /weaviate/health."""

    def test_health_check_debug_mode(self, client, mock_weaviate_connection, monkeypatch):
        monkeypatch.setenv("DEBUG", "true")
        mock_weaviate_connection.health_check = AsyncMock(return_value={"status": "healthy"})

        response = client.get("/weaviate/health")
        assert response.status_code == 200
        assert response.json()["details"]["environment"]["debug_mode"] is True

    def test_health_check_docker_environment(self, client, mock_weaviate_connection):
        mock_weaviate_connection.health_check = AsyncMock(return_value={"status": "healthy"})

        with patch("os.path.exists", return_value=True):
            response = client.get("/weaviate/health")
            assert response.status_code == 200
            assert response.json()["details"]["environment"]["docker"] is True
