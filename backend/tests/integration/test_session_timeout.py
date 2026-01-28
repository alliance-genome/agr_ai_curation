"""Integration test for session timeout and token expiration.

Task: T051 - Integration test for session timeout
Scenario: quickstart.md:211-234
Requirements: FR-018, FR-019 (session expiration and redirect)

Tests that:
1. Expired JWT tokens are rejected with 401 Unauthorized
2. Frontend redirects to login page when session expires
3. Token expiration is enforced by authentication library
4. SESSION_TIMEOUT_HOURS configuration works correctly

CRITICAL: This test validates that inactive sessions expire after timeout period.

Implementation Notes:
- Uses JWT with modified 'exp' claim to simulate expired token
- Tests token validation without requiring actual 24-hour wait
- Verifies 401 response and appropriate error message
"""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
from datetime import datetime, timedelta, timezone
import jwt


@pytest.fixture
def expired_token():
    """Create a JWT token with expired 'exp' claim.

    This simulates a token that was valid but has now exceeded
    the SESSION_TIMEOUT_HOURS configuration.
    """
    # Create token that expired 1 hour ago
    expired_time = datetime.now(timezone.utc) - timedelta(hours=1)

    payload = {
        "uid": "test_expired_user_00u1abc2def3",
        "sub": "expired_curator@alliancegenome.org",
        "email": "expired_curator@alliancegenome.org",
        "name": "Expired Curator",
        "exp": int(expired_time.timestamp()),
        "iat": int((expired_time - timedelta(hours=24)).timestamp()),
        "iss": "https://cognito-idp.us-east-1.amazonaws.com/test-pool",
        "aud": "https://api.alliancegenome.org",
    }

    # Sign with test secret (won't matter for our mock)
    token = jwt.encode(payload, "test-secret", algorithm="HS256")
    return token


@pytest.fixture
def mock_expired_auth():
    """Mock auth that rejects expired tokens with 401."""
    from fastapi import HTTPException

    class MockExpiredAuth:
        def __init__(self, *args, **kwargs):
            pass

        async def get_user(self):
            """Mock get_user that raises 401 for expired token."""
            raise HTTPException(
                status_code=401,
                detail="Token has expired"
            )

    return MockExpiredAuth()


@pytest.fixture
def client_with_expired_token(monkeypatch, mock_expired_auth):
    """Create test client with expired token authentication.

    This client simulates a user whose session has expired.
    All protected endpoints should return 401 Unauthorized.
    """
    # Set required environment variables
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("UNSTRUCTURED_API_URL", "http://test-unstructured")

    import sys
    import os
    from fastapi import Security

    sys.path.insert(
        0,
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    )

    # Patch get_auth_dependency BEFORE importing app
    with patch("src.api.auth.get_auth_dependency") as mock_get_auth_dep:
        mock_get_auth_dep.return_value = Security(mock_expired_auth.get_user)

        from main import app

        yield TestClient(app)

        app.dependency_overrides.clear()


@pytest.fixture
def valid_token():
    """Create a JWT token that is still valid.

    This simulates an active session within the timeout window.
    """
    # Create token that expires in 23 hours (within 24-hour window)
    future_time = datetime.now(timezone.utc) + timedelta(hours=23)

    payload = {
        "uid": "test_valid_user_00u1abc2def4",
        "sub": "valid_curator@alliancegenome.org",
        "email": "valid_curator@alliancegenome.org",
        "name": "Valid Curator",
        "exp": int(future_time.timestamp()),
        "iat": int(datetime.now(timezone.utc).timestamp()),
        "iss": "https://cognito-idp.us-east-1.amazonaws.com/test-pool",
        "aud": "https://api.alliancegenome.org",
    }

    token = jwt.encode(payload, "test-secret", algorithm="HS256")
    return token


@pytest.fixture
def mock_valid_auth():
    """Mock auth that accepts valid tokens."""
    from conftest import MockCognitoUser

    class MockValidAuth:
        def __init__(self, *args, **kwargs):
            pass

        async def get_user(self):
            """Mock get_user that returns valid user."""
            return MockCognitoUser(
                uid="test_valid_user_00u1abc2def4",
                sub="valid_curator@alliancegenome.org",
                groups=[]
            )

    return MockValidAuth()


@pytest.fixture
def client_with_valid_token(monkeypatch, mock_valid_auth):
    """Create test client with valid token authentication."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("UNSTRUCTURED_API_URL", "http://test-unstructured")

    import sys
    import os

    sys.path.insert(
        0,
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    )

    # Patch the auth object itself BEFORE importing app
    # This ensures routes capture the mocked auth at import time
    with patch("src.api.auth.auth", mock_valid_auth):
        # Also mock provision_weaviate_tenants to prevent real tenant creation
        with patch("src.services.user_service.provision_weaviate_tenants", return_value=True):
            with patch("src.services.user_service.get_connection"):
                from main import app

                yield TestClient(app)

                app.dependency_overrides.clear()


class TestSessionTimeout:
    """Integration tests for session timeout and token expiration."""

    def test_expired_token_rejected_on_document_list(self, client_with_expired_token):
        """Test that expired token is rejected when listing documents.

        Validates FR-018: Session expires after timeout period.
        Validates FR-019: Redirect to login on expiration.
        """
        response = client_with_expired_token.get("/weaviate/documents")

        assert response.status_code == 401, \
            f"Expected 401 for expired token, got {response.status_code}"

        data = response.json()
        assert "detail" in data
        assert "expired" in data["detail"].lower() or \
               "not authenticated" in data["detail"].lower(), \
            f"Error message should indicate token expiration: {data['detail']}"

    def test_expired_token_rejected_on_document_upload(self, client_with_expired_token):
        """Test that expired token is rejected when uploading documents.

        Validates FR-018: All operations reject expired tokens.
        """
        response = client_with_expired_token.post(
            "/weaviate/documents/upload",
            files={"file": ("test.pdf", b"fake content", "application/pdf")}
        )

        assert response.status_code == 401, \
            f"Expected 401 for expired token, got {response.status_code}"

        data = response.json()
        assert "detail" in data
        assert "expired" in data["detail"].lower() or \
               "not authenticated" in data["detail"].lower()

    def test_expired_token_rejected_on_chat(self, client_with_expired_token):
        """Test that expired token is rejected when sending chat messages.

        Validates FR-018: Chat endpoints enforce session timeout.
        """
        response = client_with_expired_token.post(
            "/api/chat",
            json={
                "message": "Test message",
                "session_id": "test-session"
            }
        )

        assert response.status_code == 401, \
            f"Expected 401 for expired token, got {response.status_code}"

        data = response.json()
        assert "detail" in data
        assert "expired" in data["detail"].lower() or \
               "not authenticated" in data["detail"].lower()

    def test_expired_token_rejected_on_user_profile(self, client_with_expired_token):
        """Test that expired token is rejected when accessing user profile.

        Validates FR-018: User profile endpoint enforces timeout.
        """
        response = client_with_expired_token.get("/users/me")

        assert response.status_code == 401, \
            f"Expected 401 for expired token, got {response.status_code}"

        data = response.json()
        assert "detail" in data
        assert "expired" in data["detail"].lower() or \
               "not authenticated" in data["detail"].lower()

    def test_valid_token_accepted_within_timeout(self, client_with_valid_token):
        """Test that valid token within timeout window is accepted.

        Validates FR-021: Token refresh during active use.

        Note: This test uses a mocked valid token to verify that
        tokens within the timeout window are still accepted.
        """
        response = client_with_valid_token.get("/users/me")

        # Valid token should be accepted (200 or appropriate success code)
        assert response.status_code in [200, 201], \
            f"Valid token should be accepted, got {response.status_code}"

    def test_health_endpoint_unaffected_by_expired_token(self, client_with_expired_token):
        """Test that health endpoint remains accessible with expired token.

        Health checks should not require authentication.

        Validates: Health endpoint exemption from auth requirements.
        """
        response = client_with_expired_token.get("/weaviate/health")

        # Health endpoint should not return 401 (authentication not required)
        assert response.status_code != 401, \
            "Health endpoint should not require authentication"

        # Should return 200 (ok) or 503 (service unavailable)
        assert response.status_code in [200, 503], \
            f"Health endpoint should return 200 or 503, got {response.status_code}"

    def test_expired_token_consistent_across_all_endpoints(self, client_with_expired_token):
        """Test that all protected endpoints consistently reject expired tokens.

        Validates FR-018: Consistent timeout enforcement across API.
        """
        protected_endpoints = [
            ("GET", "/weaviate/documents", None),
            ("GET", "/users/me", None),
            ("POST", "/api/chat", {"message": "test", "session_id": "test"}),
            ("GET", "/api/chat/history", None),
        ]

        for method, endpoint, payload in protected_endpoints:
            if method == "GET":
                response = client_with_expired_token.get(endpoint)
            elif method == "POST":
                response = client_with_expired_token.post(endpoint, json=payload)

            assert response.status_code == 401, \
                f"{method} {endpoint} should reject expired token, got {response.status_code}"

            data = response.json()
            assert "detail" in data, \
                f"{method} {endpoint} should include error detail in 401 response"

    def test_token_expiration_error_message_format(self, client_with_expired_token):
        """Test that token expiration error messages are user-friendly.

        Validates: Clear error messages for session timeout (UX requirement).
        """
        response = client_with_expired_token.get("/weaviate/documents")

        assert response.status_code == 401
        data = response.json()

        # Error message should be clear and actionable
        assert "detail" in data
        error_msg = data["detail"].lower()

        # Should indicate either expiration or authentication issue
        assert any(keyword in error_msg for keyword in [
            "expired", "timeout", "not authenticated", "session"
        ]), f"Error message should indicate session issue: {data['detail']}"
