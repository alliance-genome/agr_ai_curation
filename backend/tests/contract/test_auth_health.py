"""Contract tests for GET /weaviate/health (auth feature).

Task: T009 - Contract test GET /weaviate/health (no auth required)
Contract: specs/007-okta-login/contracts/auth_endpoints.yaml lines 17-44

This test validates that the health endpoint:
1. Does NOT require authentication (security: [] override)
2. Returns status="ok" on success
3. Includes cognito_configured field (added for auth feature)
4. Maintains backward compatibility with existing infrastructure
"""

import pytest
from unittest.mock import patch, AsyncMock, MagicMock


@pytest.fixture
def client(monkeypatch):
    """Create test client with mocked dependencies."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("EMBEDDING_MODEL", "text-embedding-3-small")
    monkeypatch.setenv("EMBEDDING_TOKEN_PREFLIGHT_ENABLED", "true")
    monkeypatch.setenv("EMBEDDING_MODEL_TOKEN_LIMIT", "8191")
    monkeypatch.setenv("EMBEDDING_TOKEN_SAFETY_MARGIN", "500")
    monkeypatch.setenv("CONTENT_PREVIEW_CHARS", "1600")
    # Set Cognito env vars to test cognito_configured field
    monkeypatch.setenv("COGNITO_USER_POOL_ID", "us-east-1_test123")
    monkeypatch.setenv("COGNITO_CLIENT_ID", "test-client-id-12345")

    from fastapi.testclient import TestClient
    import sys
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    from main import app
    return TestClient(app)


@pytest.fixture
def mock_weaviate_connection():
    """Mock WeaviateConnection for tests."""
    # Patch module-local import target used by src.api.health.
    with patch("src.api.health.get_connection") as mock:
        connection = MagicMock()
        mock.return_value = connection
        yield connection


class TestAuthHealthEndpoint:
    """Contract tests for /weaviate/health endpoint (auth feature)."""

    def test_health_no_auth_required(self, client, mock_weaviate_connection):
        """Test health endpoint accessible WITHOUT authentication token.

        Contract requirement: security: [] override means no auth required.
        This ensures backward compatibility with monitoring infrastructure.
        """
        # Mock healthy Weaviate
        mock_weaviate_connection.health_check = AsyncMock(return_value={
            "status": "healthy",
            "version": "1.19.0"
        })

        # Call health endpoint WITHOUT Authorization header
        response = client.get("/weaviate/health")

        # Should return 200, not 401
        assert response.status_code == 200

    def test_health_response_schema_with_cognito_field(self, client, mock_weaviate_connection):
        """Test health endpoint returns correct schema with cognito_configured field.

        Contract schema:
        {
          "status": "ok",
          "cognito_configured": true/false  // NEW field for auth feature
        }

        Note: Existing health endpoint returns more detailed schema.
        The contract specifies minimum required fields.
        """
        # Mock healthy Weaviate
        mock_weaviate_connection.health_check = AsyncMock(return_value={
            "status": "healthy",
            "version": "1.19.0"
        })

        response = client.get("/weaviate/health")
        assert response.status_code == 200

        data = response.json()

        # Required field from existing implementation
        assert "status" in data

        # NEW field from auth feature contract (T028 will implement)
        # This assertion will FAIL until T028 adds cognito_configured field
        assert "cognito_configured" in data, "cognito_configured field missing - T028 not yet implemented"
        assert isinstance(data["cognito_configured"], bool)

    def test_health_with_cognito_configured_true(self, client, mock_weaviate_connection, monkeypatch):
        """Test health endpoint reports cognito_configured=true when Cognito env vars set."""
        # Set Cognito environment variables
        monkeypatch.setenv("COGNITO_USER_POOL_ID", "us-east-1_test123")
        monkeypatch.setenv("COGNITO_CLIENT_ID", "test-client-id-12345")

        # Mock healthy Weaviate
        mock_weaviate_connection.health_check = AsyncMock(return_value={
            "status": "healthy"
        })

        response = client.get("/weaviate/health")
        assert response.status_code == 200

        data = response.json()
        # This will FAIL until T028 implements cognito_configured detection
        assert data["cognito_configured"] == True

    def test_health_with_cognito_not_configured(self, client, mock_weaviate_connection, monkeypatch):
        """Test health endpoint reports cognito_configured=false when Cognito env vars missing."""
        # Remove Cognito environment variables
        monkeypatch.delenv("COGNITO_USER_POOL_ID", raising=False)
        monkeypatch.delenv("COGNITO_CLIENT_ID", raising=False)

        # Mock healthy Weaviate
        mock_weaviate_connection.health_check = AsyncMock(return_value={
            "status": "healthy"
        })

        response = client.get("/weaviate/health")
        assert response.status_code == 200

        data = response.json()
        # This will FAIL until T028 implements cognito_configured detection
        assert data["cognito_configured"] == False

    def test_health_endpoint_path_backward_compatible(self, client, mock_weaviate_connection):
        """Test /weaviate/health path maintained for backward compatibility.

        Contract note: "Existing endpoint path maintained for backward compatibility.
        Frontend nginx proxies /health to this endpoint."
        """
        # Mock healthy Weaviate
        mock_weaviate_connection.health_check = AsyncMock(return_value={
            "status": "healthy"
        })

        # Endpoint must be at /weaviate/health, not /health
        response = client.get("/weaviate/health")
        assert response.status_code == 200

    def test_health_no_auth_even_with_invalid_token(self, client, mock_weaviate_connection):
        """Test health endpoint ignores invalid/expired tokens (no auth required).

        Even if a client sends a malformed or expired token, health endpoint
        should still work because security: [] override exempts it from auth.
        """
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


class TestAuthHealthEndpointFailureCases:
    """Test health endpoint failure cases (unrelated to auth)."""

    def test_health_weaviate_unhealthy_no_auth_still_works(self, client, mock_weaviate_connection):
        """Test health endpoint returns 503 for unhealthy Weaviate, still no auth required."""
        # Mock unhealthy Weaviate
        mock_weaviate_connection.health_check = AsyncMock(return_value={
            "status": "unhealthy",
            "message": "Connection failed"
        })

        # Call WITHOUT auth token
        response = client.get("/weaviate/health")

        # Should return 503 (unhealthy), not 401 (auth required)
        assert response.status_code == 503

        # Verify it's a health failure, not auth failure
        data = response.json()
        assert "detail" in data  # Error format from existing implementation


class TestAuthProviderConfigHelpers:
    """Provider configuration helper tests (no API server required)."""

    def test_is_auth_configured_for_cognito(self, monkeypatch):
        pytest.importorskip("dotenv")
        from src.config import is_auth_configured

        monkeypatch.setenv("AUTH_PROVIDER", "cognito")
        monkeypatch.setenv("COGNITO_USER_POOL_ID", "us-east-1_pool")
        monkeypatch.setenv("COGNITO_CLIENT_ID", "client-id")
        assert is_auth_configured() is True

    def test_is_auth_configured_for_oidc(self, monkeypatch):
        pytest.importorskip("dotenv")
        from src.config import is_auth_configured

        monkeypatch.setenv("AUTH_PROVIDER", "oidc")
        monkeypatch.setenv("OIDC_ISSUER_URL", "https://issuer.example.org")
        monkeypatch.setenv("OIDC_CLIENT_ID", "oidc-client")
        monkeypatch.setenv("OIDC_REDIRECT_URI", "http://localhost:3002/auth/callback")
        assert is_auth_configured() is True

    def test_is_auth_configured_for_oidc_requires_redirect_uri(self, monkeypatch):
        pytest.importorskip("dotenv")
        from src.config import is_auth_configured

        monkeypatch.setenv("AUTH_PROVIDER", "oidc")
        monkeypatch.setenv("OIDC_ISSUER_URL", "https://issuer.example.org")
        monkeypatch.setenv("OIDC_CLIENT_ID", "oidc-client")
        monkeypatch.delenv("OIDC_REDIRECT_URI", raising=False)
        assert is_auth_configured() is False
