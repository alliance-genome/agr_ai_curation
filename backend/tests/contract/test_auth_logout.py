"""Contract tests for POST /auth/logout.

Task: T010 - Contract test POST /auth/logout
Contract: specs/007-okta-login/contracts/auth_endpoints.yaml lines 46-66

This test validates that the logout endpoint:
1. Requires valid Okta JWT token (returns 401 if missing/invalid)
2. Returns {"status": "logged_out", "message": "..."} on success
3. Clears user session data (FR-009, FR-010)

NOTE: This test will FAIL until T023 implements auth router with logout endpoint.

IMPORTANT: Uses app.dependency_overrides instead of @patch decorators to properly
mock FastAPI dependencies. Also mocks requests.get to prevent real JWKS fetches.
"""

import pytest
from unittest.mock import MagicMock, patch


@pytest.fixture
def client(monkeypatch):
    """Create test client with mocked dependencies and JWKS requests."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OKTA_DOMAIN", "dev-test.okta.com")
    monkeypatch.setenv("OKTA_API_AUDIENCE", "https://api.alliancegenome.org")

    # Mock requests.get BEFORE importing main/auth modules
    # This prevents real JWKS fetches when Okta() is initialized
    with patch("requests.get") as mock_get:
        mock_response = MagicMock()
        mock_response.json.return_value = {"keys": []}  # Empty JWKS
        mock_response.status_code = 200
        mock_get.return_value = mock_response

        from fastapi.testclient import TestClient
        import sys
        import os
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        from main import app

        # Clear any existing dependency overrides
        app.dependency_overrides.clear()

        yield TestClient(app)

        # Cleanup after test
        app.dependency_overrides.clear()


def get_valid_auth_header():
    """Generate mock Authorization header with valid JWT token."""
    # In real tests, this would be a properly signed JWT
    # For contract tests, we mock the auth validation
    return {"Authorization": "Bearer mock_valid_token_12345"}


class TestLogoutEndpoint:
    """Contract tests for POST /auth/logout endpoint."""

    def test_logout_endpoint_exists(self, client):
        """Test logout endpoint exists at POST /auth/logout.

        This test will FAIL until T023 creates the auth router.
        Expected failure: 404 Not Found (endpoint doesn't exist yet)
        """
        response = client.post("/auth/logout")

        # Should NOT be 404 after T023 implementation
        # Will be 401 (auth required) until we add auth header
        assert response.status_code != 404, "Logout endpoint not found - T023 not implemented"

    def test_logout_requires_authentication(self, client):
        """Test logout endpoint requires valid authentication token.

        Contract requirement: Endpoint must validate Okta JWT token.
        Without token, should return 401 Unauthorized.

        This test will FAIL until T023 implements auth protection.
        """
        # Call logout WITHOUT Authorization header
        response = client.post("/auth/logout")

        # Contract specifies 401 for missing/invalid auth
        assert response.status_code == 401

        # Check error response format
        data = response.json()
        assert "detail" in data
        # Contract examples: "Not authenticated", "Invalid authentication token"
        assert data["detail"] in [
            "Not authenticated",
            "Invalid authentication token",
            "Token has expired"
        ]

    def test_logout_with_invalid_token(self, client):
        """Test logout endpoint rejects invalid JWT tokens.

        Contract requirement: Must validate Okta JWT signature.
        Invalid tokens should return 401.
        """
        # Send malformed token
        response = client.post(
            "/auth/logout",
            headers={"Authorization": "Bearer invalid_malformed_token"}
        )

        assert response.status_code == 401

        data = response.json()
        assert "detail" in data

    def test_logout_with_expired_token(self, client):
        """Test logout endpoint rejects expired JWT tokens.

        Contract requirement: Validate token expiration.
        Expired tokens should return 401 with "Token has expired" message.
        """
        # Send expired token (in real test, would be a properly signed but expired JWT)
        response = client.post(
            "/auth/logout",
            headers={"Authorization": "Bearer expired_token_12345"}
        )

        assert response.status_code == 401

        data = response.json()
        assert "detail" in data
        # Contract example shows "Token has expired" as possible error
        # Will accept any 401 auth error for contract validation

    def test_logout_success_response_schema(self, client):
        """Test logout endpoint returns correct schema on successful logout.

        Contract schema:
        {
          "status": "logged_out",
          "message": "User session terminated successfully"
        }

        This test will FAIL until T023 implements logout endpoint logic.

        Uses app.dependency_overrides to mock auth.get_user dependency.
        """
        from main import app
        from src.api.auth import auth

        # Mock OktaUser for successful authentication
        mock_user = MagicMock()
        mock_user.uid = "00u1abc2def3ghi4jkl"
        mock_user.email = "curator@alliancegenome.org"

        # Override the auth.get_user dependency
        # When T023 implements the endpoint with Depends(auth.get_user),
        # this will be called instead of the real implementation
        # IMPORTANT: Must accept *args, **kwargs for FastAPI's dependency injection
        app.dependency_overrides[auth.get_user] = lambda *args, **kwargs: mock_user

        try:
            # Call logout with valid token
            response = client.post(
                "/auth/logout",
                headers=get_valid_auth_header()
            )

            # Should return 200 on success
            assert response.status_code == 200

            # Validate response schema
            data = response.json()
            assert "status" in data
            assert data["status"] == "logged_out"

            # Message field is optional (contract only requires status)
            if "message" in data:
                assert isinstance(data["message"], str)
                assert len(data["message"]) > 0
        finally:
            # Clean up override
            app.dependency_overrides.clear()

    def test_logout_clears_session_data(self, client):
        """Test logout endpoint terminates user session.

        Contract requirements (FR-009, FR-010):
        - Terminate user session
        - Clear all client-side session data

        Backend should clear any server-side session state.
        Client must delete auth token after receiving success response.
        """
        from main import app
        from src.api.auth import auth

        # Mock successful authentication
        mock_user = MagicMock()
        mock_user.uid = "00u1abc2def3ghi4jkl"
        app.dependency_overrides[auth.get_user] = lambda *args, **kwargs: mock_user

        try:
            # Call logout
            response = client.post(
                "/auth/logout",
                headers=get_valid_auth_header()
            )

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "logged_out"

            # Note: Actual session cleanup verification would require
            # integration tests. Contract test only validates API contract.
        finally:
            app.dependency_overrides.clear()

    def test_logout_multiple_times_allowed(self, client):
        """Test logout endpoint is idempotent (can be called multiple times).

        Calling logout multiple times should not cause errors.
        Each call should return 200 with logged_out status.
        """
        from main import app
        from src.api.auth import auth

        # Mock successful authentication
        mock_user = MagicMock()
        mock_user.uid = "00u1abc2def3ghi4jkl"
        app.dependency_overrides[auth.get_user] = lambda *args, **kwargs: mock_user

        try:
            # First logout
            response1 = client.post(
                "/auth/logout",
                headers=get_valid_auth_header()
            )
            assert response1.status_code == 200

            # Second logout (idempotent)
            response2 = client.post(
                "/auth/logout",
                headers=get_valid_auth_header()
            )
            assert response2.status_code == 200

            # Both should return same schema
            assert response1.json()["status"] == "logged_out"
            assert response2.json()["status"] == "logged_out"
        finally:
            app.dependency_overrides.clear()


class TestLogoutEndpointEdgeCases:
    """Edge case tests for logout endpoint."""

    def test_logout_without_bearer_prefix(self, client):
        """Test logout endpoint rejects tokens without 'Bearer' prefix."""
        # Send token without Bearer prefix
        response = client.post(
            "/auth/logout",
            headers={"Authorization": "mock_token_no_bearer"}
        )

        # Should reject malformed Authorization header
        assert response.status_code == 401

    def test_logout_with_empty_authorization_header(self, client):
        """Test logout endpoint rejects empty Authorization header."""
        response = client.post(
            "/auth/logout",
            headers={"Authorization": ""}
        )

        assert response.status_code == 401

    def test_logout_case_sensitive_bearer(self, client):
        """Test logout endpoint accepts 'Bearer' with correct capitalization."""
        # FastAPI/Okta typically requires 'Bearer' not 'bearer'
        response = client.post(
            "/auth/logout",
            headers={"Authorization": "bearer lowercase_token"}  # wrong case
        )

        # Should reject (case-sensitive)
        assert response.status_code == 401

    def test_logout_response_content_type_json(self, client):
        """Test logout endpoint returns JSON content-type."""
        from main import app
        from src.api.auth import auth

        # Mock successful authentication
        mock_user = MagicMock()
        mock_user.uid = "00u1abc2def3ghi4jkl"
        app.dependency_overrides[auth.get_user] = lambda *args, **kwargs: mock_user

        try:
            response = client.post(
                "/auth/logout",
                headers=get_valid_auth_header()
            )

            assert response.status_code == 200
            assert "application/json" in response.headers["content-type"]
        finally:
            app.dependency_overrides.clear()
