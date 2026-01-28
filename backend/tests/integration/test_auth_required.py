"""Integration test for authentication requirement across all endpoints.

Tests that all protected endpoints return 401 without authentication token,
while public endpoints (like health checks) remain accessible.

Validates FR-001: System MUST redirect all unauthenticated requests before
allowing access to application resources.

Requirements from quickstart.md:59-72 and tasks.md:T047:
- Test that all endpoints return 401 without authentication token (except health)
- Verify /weaviate/health is accessible without auth (returns 200)
- Test document endpoints (/weaviate/documents, /weaviate/documents/upload, etc.)
- Test chat endpoints (/api/chat, /api/chat/history)
- Test user profile endpoint (/users/me)
- Verify 401 responses include appropriate error message

CRITICAL: This test MUST PASS after T039-T046 implementation (Cognito authentication).

Pattern: Patch get_auth_dependency() BEFORE importing app to return a dependency
that raises 401 for all protected endpoints. This simulates missing auth token
without trying to contact Cognito JWKS (which would cause 503 errors).
"""

from typing import Optional

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch


@pytest.fixture
def client(monkeypatch):
    """Create test client that simulates unauthenticated requests.

    This fixture patches auth initialization BEFORE importing the app,
    making it successfully initialize but always reject auth with 401.

    The approach:
    1. Mock auth class to create a fake auth object
    2. Mock auth.get_user to raise 401 (simulating missing/invalid token)
    3. Patch get_auth_dependency() to return Security(mock_auth.get_user)
    4. Import app (routes register with our mocked dependencies)
    5. All protected endpoints now raise 401 instead of 503

    This properly simulates authentication being configured but the user
    not having a valid token.
    """
    # Set minimum required environment variables for app to start
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("UNSTRUCTURED_API_URL", "http://test-unstructured")

    import sys
    import os
    from fastapi import HTTPException, Security

    sys.path.insert(
        0,
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    )

    class MockUnauthenticatedAuth:
        def __init__(self, *args, **kwargs):
            pass

        async def get_user(self):
            """Mock get_user that always raises 401.

            Note: No parameters needed - the Security() wrapper handles dependency injection.
            Adding parameters here causes FastAPI to treat them as query params → 422 errors.
            """
            raise HTTPException(
                status_code=401,
                detail="Not authenticated"
            )

    # Patch dependencies BEFORE importing the app so routes capture them
    with patch("src.api.auth.get_auth_dependency") as mock_get_auth_dep:

        mock_auth_instance = MockUnauthenticatedAuth()
        mock_get_auth_dep.return_value = Security(mock_auth_instance.get_user)

        # Now import the app (which will create routes with mocked auth)
        from main import app

        yield TestClient(app)

        # Clean up dependency overrides
        app.dependency_overrides.clear()


class TestAuthenticationRequired:
    """Integration tests for authentication requirement enforcement."""

    def test_health_endpoint_accessible_without_auth(self, client):
        """Test that /weaviate/health is accessible without authentication.

        Public health endpoints should be accessible for monitoring/orchestration
        without requiring authentication.

        Validates: FR-001 exception for health checks
        """
        response = client.get("/weaviate/health")

        # Health endpoint should return proper status (200 or 503 depending on services)
        # But should NOT return 401 (authentication required)
        assert response.status_code != 401, \
            "Health endpoint should not require authentication"

        if response.status_code == 200:
            # Success case - verify proper health check response
            data = response.json()
            assert "status" in data, "Health response should include status"
            assert data["status"] in ["ok", "degraded"], \
                f"Health status should be 'ok' or 'degraded', got {data['status']}"
        elif response.status_code == 503:
            # Service unavailable case - verify proper error structure
            data = response.json()
            assert "detail" in data or "status" in data, \
                "503 response should include error detail or status"
        else:
            # Unexpected status code
            pytest.fail(f"Health endpoint returned unexpected status: {response.status_code}")

    def test_document_list_requires_auth(self, client):
        """Test that GET /weaviate/documents returns 401 without auth.

        Document listing is a protected resource that requires authentication.

        Validates: FR-001 - Document endpoints require authentication
        """
        response = client.get("/weaviate/documents")

        assert response.status_code == 401, \
            f"Expected 401 Unauthorized, got {response.status_code}"

        # Verify error message indicates authentication issue
        data = response.json()
        assert "detail" in data, "Response should include error detail"
        assert "authenticate" in data["detail"].lower() or \
               "not authenticated" in data["detail"].lower(), \
            f"Error message should indicate authentication issue: {data['detail']}"

    def test_document_upload_requires_auth(self, client):
        """Test that POST /weaviate/documents/upload returns 401 without auth.

        Document upload is a protected operation that requires authentication.

        Validates: FR-001 - Upload endpoints require authentication
        """
        # Attempt to upload a document without authentication
        # We don't need a real file - the auth check happens before file processing
        response = client.post(
            "/weaviate/documents/upload",
            files={"file": ("test.pdf", b"fake pdf content", "application/pdf")}
        )

        assert response.status_code == 401, \
            f"Expected 401 Unauthorized, got {response.status_code}"

        data = response.json()
        assert "detail" in data, "Response should include error detail"
        assert "authenticate" in data["detail"].lower() or \
               "not authenticated" in data["detail"].lower(), \
            f"Error message should indicate authentication issue: {data['detail']}"

    def test_document_detail_requires_auth(self, client):
        """Test that GET /weaviate/documents/{id} returns 401 without auth.

        Individual document access requires authentication.

        Validates: FR-001 - Document detail endpoints require authentication
        """
        # Use a fake document ID - auth check happens before document lookup
        response = client.get("/weaviate/documents/fake-doc-id")

        assert response.status_code == 401, \
            f"Expected 401 Unauthorized, got {response.status_code}"

        data = response.json()
        assert "detail" in data
        assert "authenticate" in data["detail"].lower() or \
               "not authenticated" in data["detail"].lower()

    def test_chat_endpoint_requires_auth(self, client):
        """Test that POST /api/chat returns 401 without auth.

        Chat functionality requires user authentication for session tracking
        and tenant isolation.

        Validates: FR-001, FR-011 - Chat endpoints require authentication
        """
        response = client.post(
            "/api/chat",
            json={
                "message": "Test message",
                "session_id": "test-session"
            }
        )

        assert response.status_code == 401, \
            f"Expected 401 Unauthorized, got {response.status_code}"

        data = response.json()
        assert "detail" in data
        assert "authenticate" in data["detail"].lower() or \
               "not authenticated" in data["detail"].lower()

    def test_chat_history_requires_auth(self, client):
        """Test that GET /api/chat/history returns 401 without auth.

        Chat history is user-specific and requires authentication.

        Validates: FR-001, FR-014 - Chat history requires authentication
        """
        response = client.get("/api/chat/history")

        assert response.status_code == 401, \
            f"Expected 401 Unauthorized, got {response.status_code}"

        data = response.json()
        assert "detail" in data
        assert "authenticate" in data["detail"].lower() or \
               "not authenticated" in data["detail"].lower()

    def test_chat_history_by_session_requires_auth(self, client):
        """Test that GET /api/chat/history/{session_id} returns 401 without auth.

        Session-specific chat history requires authentication.

        Validates: FR-001, FR-014 - Session history requires authentication
        """
        response = client.get("/api/chat/history/fake-session-id")

        assert response.status_code == 401, \
            f"Expected 401 Unauthorized, got {response.status_code}"

        data = response.json()
        assert "detail" in data
        assert "authenticate" in data["detail"].lower() or \
               "not authenticated" in data["detail"].lower()

    def test_user_profile_requires_auth(self, client):
        """Test that GET /users/me returns 401 without auth.

        User profile endpoint requires authentication to identify the user.

        Validates: FR-001, FR-008 - User profile requires authentication
        """
        response = client.get("/users/me")

        assert response.status_code == 401, \
            f"Expected 401 Unauthorized, got {response.status_code}"

        data = response.json()
        assert "detail" in data
        assert "authenticate" in data["detail"].lower() or \
               "not authenticated" in data["detail"].lower()

    def test_document_delete_requires_auth(self, client):
        """Test that DELETE /weaviate/documents/{id} returns 401 without auth.

        Document deletion is a protected operation requiring authentication.

        Validates: FR-001 - Delete endpoints require authentication
        """
        response = client.delete("/weaviate/documents/fake-doc-id")

        assert response.status_code == 401, \
            f"Expected 401 Unauthorized, got {response.status_code}"

        data = response.json()
        assert "detail" in data
        assert "authenticate" in data["detail"].lower() or \
               "not authenticated" in data["detail"].lower()

    def test_feedback_submit_requires_auth(self, client):
        """Test that /api/feedback/submit requires authentication.

        Validates: FR-001, FR-025 - All backend API endpoints must be protected

        NOTE: This test will FAIL until feedback endpoint adds get_auth_dependency().
        This is intentional - the test should fail to highlight the missing auth.
        """
        feedback_payload = {
            "session_id": "test_session",
            "curator_id": "test_curator",
            "feedback_text": "Test feedback",
            "trace_ids": []
        }

        response = client.post("/api/feedback/submit", json=feedback_payload)

        # Should require authentication (401), not accept the request
        assert response.status_code == 401, \
            f"Feedback endpoint MUST require auth (FR-001, FR-025). Got {response.status_code}. " \
            f"If this test fails with 200, the endpoint is not protected!"

        data = response.json()
        assert "detail" in data
        assert "authenticate" in data["detail"].lower() or \
               "not authenticated" in data["detail"].lower(), \
               f"Expected auth error message, got: {data['detail']}"

    def test_settings_get_requires_auth(self, client):
        """Test that GET /weaviate/settings requires authentication.

        Validates: FR-001, FR-025 - All backend API endpoints must be protected

        NOTE: This test will FAIL until settings endpoint adds get_auth_dependency().
        This is intentional - the test should fail to highlight the missing auth.
        """
        response = client.get("/weaviate/settings")

        # Should require authentication (401), not return settings
        assert response.status_code == 401, \
            f"Settings endpoint MUST require auth (FR-001, FR-025). Got {response.status_code}. " \
            f"If this test fails with 200, the endpoint is not protected!"

        data = response.json()
        assert "detail" in data
        assert "authenticate" in data["detail"].lower() or \
               "not authenticated" in data["detail"].lower(), \
               f"Expected auth error message, got: {data['detail']}"

    def test_settings_put_requires_auth(self, client):
        """Test that PUT /weaviate/settings requires authentication.

        Validates: FR-001, FR-025 - All backend API endpoints must be protected

        NOTE: This test will FAIL until settings endpoint adds get_auth_dependency().
        This is intentional - the test should fail to highlight the missing auth.
        """
        settings_payload = {
            "embedding": {
                "model": "text-embedding-3-small",
                "provider": "openai"
            }
        }

        response = client.put("/weaviate/settings", json=settings_payload)

        # Should require authentication (401), not accept the update
        assert response.status_code == 401, \
            f"Settings update MUST require auth (FR-001, FR-025). Got {response.status_code}. " \
            f"If this test fails with 200, the endpoint is not protected!"

        data = response.json()
        assert "detail" in data
        assert "authenticate" in data["detail"].lower() or \
               "not authenticated" in data["detail"].lower(), \
               f"Expected auth error message, got: {data['detail']}"

    def test_root_endpoint_accessible(self, client):
        """Test that root endpoint (/) is accessible without auth.

        The root API information endpoint should be public for discoverability.

        Validates: FR-001 exception for API discovery
        """
        response = client.get("/")

        # Root endpoint should be accessible (informational only)
        assert response.status_code == 200, \
            f"Root endpoint should be accessible, got {response.status_code}"

        data = response.json()
        assert "service" in data, "Root endpoint should return service info"

    def test_multiple_endpoints_consistently_reject_unauthenticated(self, client):
        """Test that multiple protected endpoints consistently return 401.

        This ensures authentication enforcement is consistent across the API.

        Validates: FR-001 - Consistent authentication enforcement

        NOTE: Includes ALL protected endpoints. Some may currently lack auth
        (feedback, settings) and will cause this test to FAIL until fixed.
        """
        protected_endpoints = [
            ("GET", "/weaviate/documents", None),
            ("POST", "/api/chat", {"message": "test", "session_id": "test"}),
            ("GET", "/api/chat/history", None),
            ("GET", "/users/me", None),
            ("POST", "/api/feedback/submit", {
                "session_id": "test",
                "curator_id": "test",
                "feedback_text": "test",
                "trace_ids": []
            }),
            ("GET", "/weaviate/settings", None),
        ]

        for method, endpoint, payload in protected_endpoints:
            if method == "GET":
                response = client.get(endpoint)
            elif method == "POST":
                response = client.post(endpoint, json=payload)

            assert response.status_code == 401, \
                f"{method} {endpoint} should require authentication, got {response.status_code}"

            data = response.json()
            assert "detail" in data, \
                f"{method} {endpoint} should include error detail in 401 response"

            # Verify error message indicates authentication issue
            assert "authenticate" in data["detail"].lower() or \
                   "not authenticated" in data["detail"].lower(), \
                f"{method} {endpoint} error message should indicate auth issue: {data['detail']}"
