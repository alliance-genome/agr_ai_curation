"""Contract tests for health check endpoints."""

import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from datetime import datetime


@pytest.fixture
def client(monkeypatch):
    """Create test client with mocked dependencies."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("UNSTRUCTURED_API_URL", "http://test-unstructured")

    from fastapi.testclient import TestClient
    import sys
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    from main import app
    return TestClient(app)


@pytest.fixture
def mock_weaviate_connection():
    """Mock WeaviateConnection for tests."""
    # Patch get_connection since health.py uses get_connection() not WeaviateConnection directly
    with patch("src.lib.weaviate_helpers.get_connection") as mock:
        connection = MagicMock()
        mock.return_value = connection
        yield connection


class TestHealthEndpoint:
    """Tests for /weaviate/health endpoint."""

    def test_health_check_healthy(self, client, mock_weaviate_connection):
        """Test health check when all services are healthy."""
        # Mock healthy Weaviate connection
        mock_weaviate_connection.health_check = AsyncMock(return_value={
            "status": "healthy",
            "version": "1.19.0",
            "nodes": 1,
            "collections": 2
        })

        response = client.get("/weaviate/health")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "healthy"
        assert data["service"] == "Weaviate Control Panel API"
        assert data["version"] == "0.1.0"
        assert "timestamp" in data

        # Check service statuses
        assert data["checks"]["api"] == "healthy"
        assert data["checks"]["weaviate"] == "healthy"
        assert data["checks"]["unstructured"] == "configured"

        # Check details
        assert "weaviate" in data["details"]
        assert data["details"]["weaviate"]["version"] == "1.19.0"
        assert data["details"]["weaviate"]["nodes"] == 1
        assert data["details"]["weaviate"]["collections"] == 2

        assert "unstructured" in data["details"]
        assert data["details"]["unstructured"]["configured"] == True

        assert "environment" in data["details"]
        # Check that Python version starts with 3.11
        python_version = data["details"]["environment"]["python_version"]
        assert python_version.startswith("3.11") or python_version == "3.11+"

    def test_health_check_weaviate_unhealthy(self, client, mock_weaviate_connection):
        """Test health check when Weaviate is unhealthy."""
        # Mock unhealthy Weaviate connection
        mock_weaviate_connection.health_check = AsyncMock(return_value={
            "status": "unhealthy",
            "message": "Connection timeout"
        })

        response = client.get("/weaviate/health")
        assert response.status_code == 503

        data = response.json()["detail"]
        assert data["status"] == "unhealthy"
        assert data["checks"]["weaviate"] == "unhealthy"
        assert "error" in data["details"]["weaviate"]

    def test_health_check_weaviate_error(self, client, mock_weaviate_connection):
        """Test health check when Weaviate connection raises error."""
        # Mock Weaviate connection error
        mock_weaviate_connection.health_check = AsyncMock(
            side_effect=Exception("Connection failed")
        )

        response = client.get("/weaviate/health")
        assert response.status_code == 503

        data = response.json()["detail"]
        assert data["status"] == "unhealthy"
        assert data["checks"]["weaviate"] == "unhealthy"
        assert "error" in data["details"]["weaviate"]

    def test_health_check_unstructured_not_configured(self, client, mock_weaviate_connection, monkeypatch):
        """Test health check when Unstructured is not configured."""
        # Remove Unstructured configuration
        monkeypatch.delenv("UNSTRUCTURED_API_URL", raising=False)

        # Mock healthy Weaviate
        mock_weaviate_connection.health_check = AsyncMock(return_value={
            "status": "healthy",
            "version": "1.19.0"
        })

        response = client.get("/weaviate/health")
        assert response.status_code == 200

        data = response.json()
        assert data["checks"]["unstructured"] == "not_configured"
        assert data["details"]["unstructured"]["configured"] == False

    def test_health_check_response_schema(self, client, mock_weaviate_connection):
        """Test health check response has expected schema."""
        # Mock healthy Weaviate
        mock_weaviate_connection.health_check = AsyncMock(return_value={
            "status": "healthy",
            "version": "1.19.0"
        })

        response = client.get("/weaviate/health")
        assert response.status_code == 200

        data = response.json()

        # Required top-level fields
        assert "status" in data
        assert "timestamp" in data
        assert "service" in data
        assert "version" in data
        assert "okta_configured" in data  # T028: Auth feature field
        assert "checks" in data
        assert "details" in data

        # Validate timestamp format
        timestamp = data["timestamp"]
        datetime.fromisoformat(timestamp.replace('Z', '+00:00'))

        # Check structure
        assert isinstance(data["checks"], dict)
        assert isinstance(data["details"], dict)

        # T028: Verify okta_configured is a boolean
        assert isinstance(data["okta_configured"], bool)

    def test_health_check_okta_configured_true(self, client, mock_weaviate_connection, monkeypatch):
        """Test health check reports okta_configured=true when Okta env vars set."""
        # Set Okta environment variables
        monkeypatch.setenv("OKTA_DOMAIN", "dev-test.okta.com")
        monkeypatch.setenv("OKTA_API_AUDIENCE", "https://api.alliancegenome.org")

        # Mock healthy Weaviate
        mock_weaviate_connection.health_check = AsyncMock(return_value={
            "status": "healthy"
        })

        response = client.get("/weaviate/health")
        assert response.status_code == 200

        data = response.json()
        assert data["okta_configured"] == True

    def test_health_check_okta_configured_false(self, client, mock_weaviate_connection, monkeypatch):
        """Test health check reports okta_configured=false when Okta env vars missing."""
        # Remove Okta environment variables
        monkeypatch.delenv("OKTA_DOMAIN", raising=False)
        monkeypatch.delenv("OKTA_API_AUDIENCE", raising=False)

        # Mock healthy Weaviate
        mock_weaviate_connection.health_check = AsyncMock(return_value={
            "status": "healthy"
        })

        response = client.get("/weaviate/health")
        assert response.status_code == 200

        data = response.json()
        assert data["okta_configured"] == False

    def test_health_check_no_auth_required(self, client, mock_weaviate_connection):
        """Test health endpoint accessible WITHOUT authentication token."""
        # Mock healthy Weaviate
        mock_weaviate_connection.health_check = AsyncMock(return_value={
            "status": "healthy"
        })

        # Call health endpoint WITHOUT Authorization header
        response = client.get("/weaviate/health")

        # Should return 200, not 401 (no auth required)
        assert response.status_code == 200

    def test_health_check_no_auth_even_with_invalid_token(self, client, mock_weaviate_connection):
        """Test health endpoint ignores invalid/expired tokens (no auth required)."""
        # Mock healthy Weaviate
        mock_weaviate_connection.health_check = AsyncMock(return_value={
            "status": "healthy"
        })

        # Send request with invalid/expired token
        response = client.get(
            "/weaviate/health",
            headers={"Authorization": "Bearer invalid_or_expired_token_123"}
        )

        # Should still return 200 (auth not enforced on this endpoint)
        assert response.status_code == 200


class TestReadinessEndpoint:
    """Tests for /weaviate/readiness endpoint."""

    def test_readiness_check_ready(self, client, mock_weaviate_connection):
        """Test readiness check when service is ready."""
        # Mock healthy Weaviate connection
        mock_weaviate_connection.health_check = AsyncMock(return_value={
            "status": "healthy"
        })

        response = client.get("/weaviate/readiness")
        assert response.status_code == 200

        data = response.json()
        assert data["ready"] == True
        assert "timestamp" in data

        # Validate timestamp
        datetime.fromisoformat(data["timestamp"].replace('Z', '+00:00'))

    def test_readiness_check_not_ready(self, client, mock_weaviate_connection):
        """Test readiness check when Weaviate is not ready."""
        # Mock unhealthy Weaviate
        mock_weaviate_connection.health_check = AsyncMock(return_value={
            "status": "unhealthy"
        })

        response = client.get("/weaviate/readiness")
        assert response.status_code == 503

        data = response.json()["detail"]
        assert data["ready"] == False
        assert data["reason"] == "Weaviate connection not ready"

    def test_readiness_check_connection_error(self, client, mock_weaviate_connection):
        """Test readiness check when connection fails."""
        # Mock connection error
        mock_weaviate_connection.health_check = AsyncMock(
            side_effect=Exception("Connection timeout")
        )

        response = client.get("/weaviate/readiness")
        assert response.status_code == 503

        data = response.json()["detail"]
        assert data["ready"] == False
        assert "Connection timeout" in data["reason"]

    def test_readiness_check_none_response(self, client, mock_weaviate_connection):
        """Test readiness check when health check returns None."""
        # Mock None response
        mock_weaviate_connection.health_check = AsyncMock(return_value=None)

        response = client.get("/weaviate/readiness")
        assert response.status_code == 503

        data = response.json()["detail"]
        assert data["ready"] == False
        assert data["reason"] == "Weaviate connection not ready"

    def test_readiness_is_lightweight(self, client, mock_weaviate_connection):
        """Test that readiness check is more lightweight than health check."""
        # Mock healthy Weaviate
        mock_weaviate_connection.health_check = AsyncMock(return_value={
            "status": "healthy"
        })

        # Call readiness
        response = client.get("/weaviate/readiness")
        assert response.status_code == 200

        data = response.json()

        # Readiness should have minimal fields
        assert len(data.keys()) == 2  # Only 'ready' and 'timestamp'
        assert "ready" in data
        assert "timestamp" in data

        # Should not include detailed information
        assert "checks" not in data
        assert "details" not in data
        assert "service" not in data


class TestHealthEndpointEdgeCases:
    """Edge case tests for health endpoints."""

    def test_health_check_partial_degradation(self, client, mock_weaviate_connection):
        """Test health check shows degraded when one service is down."""
        # Mock Weaviate as unhealthy but API is running
        mock_weaviate_connection.health_check = AsyncMock(return_value={
            "status": "unhealthy",
            "message": "Cluster degraded"
        })

        response = client.get("/weaviate/health")
        assert response.status_code == 503

        data = response.json()["detail"]
        assert data["status"] == "unhealthy"
        assert data["checks"]["api"] == "healthy"
        assert data["checks"]["weaviate"] == "unhealthy"

    def test_health_check_debug_mode(self, client, mock_weaviate_connection, monkeypatch):
        """Test health check includes debug mode in environment details."""
        # Set debug mode
        monkeypatch.setenv("DEBUG", "true")

        # Mock healthy Weaviate
        mock_weaviate_connection.health_check = AsyncMock(return_value={
            "status": "healthy"
        })

        response = client.get("/weaviate/health")
        assert response.status_code == 200

        data = response.json()
        assert data["details"]["environment"]["debug_mode"] == True

    def test_health_check_docker_environment(self, client, mock_weaviate_connection, monkeypatch):
        """Test health check detects Docker environment."""
        # Mock Docker environment
        import os
        with patch.object(os.path, 'exists', return_value=True):
            # Mock healthy Weaviate
            mock_weaviate_connection.health_check = AsyncMock(return_value={
                "status": "healthy"
            })

            response = client.get("/weaviate/health")
            assert response.status_code == 200

            data = response.json()
            assert data["details"]["environment"]["docker"] == True