"""Integration test for comprehensive protected endpoint verification.

Task: T054 - Integration test for all protected API endpoints
Scenario: quickstart.md:246-288
Requirements: FR-025, FR-026, FR-027 (all API endpoints protected)

Tests that:
1. All document endpoints require authentication
2. All chat endpoints require authentication
3. All user endpoints require authentication
4. All settings endpoints require authentication
5. All feedback endpoints require authentication
6. Health and root endpoints remain public
7. 401 Unauthorized returned for missing authentication
8. Appropriate error messages for authentication failures

CRITICAL: This test validates complete API surface protection.

Implementation Notes:
- Comprehensive endpoint coverage
- Tests both authenticated and unauthenticated access
- Verifies error response format
- Ensures no endpoints accidentally exposed
"""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch
from fastapi import HTTPException, Security, Depends

from conftest import MockCognitoUser


@pytest.fixture
def unauthenticated_client(monkeypatch):
    """Create test client without authentication (simulates missing token)."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("UNSTRUCTURED_API_URL", "http://test-unstructured")

    import sys
    import os

    sys.path.insert(
        0,
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    )

    class MockUnauthenticatedAuth:
        def __init__(self, *args, **kwargs):
            pass

        async def get_user(self):
            """Raise 401 for unauthenticated requests."""
            raise HTTPException(status_code=401, detail="Not authenticated")

    with patch("src.api.auth.get_auth_dependency") as mock_get_auth_dep:

        mock_auth = MockUnauthenticatedAuth()
        mock_get_auth_dep.return_value = Security(mock_auth.get_user)

        from main import app

        yield TestClient(app)

        app.dependency_overrides.clear()


@pytest.fixture
def authenticated_client(monkeypatch):
    """Create test client with valid authentication."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("UNSTRUCTURED_API_URL", "http://test-unstructured")

    import sys
    import os

    sys.path.insert(
        0,
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    )

    class MockValidAuth:
        def __init__(self, *args, **kwargs):
            pass

        async def get_user(self):
            """Mock get_user that returns valid user."""
            return MockCognitoUser(
                uid="test_protected_user",
                sub="protected_test@alliancegenome.org",
                groups=[]
            )

    # Patch the auth object itself BEFORE importing app
    # This ensures routes capture the mocked auth at import time
    with patch("src.api.auth.auth", MockValidAuth()):
        # Also mock provision_weaviate_tenants to prevent real tenant creation
        with patch("src.services.user_service.provision_weaviate_tenants", return_value=True):
            with patch("src.services.user_service.get_connection"):
                from main import app

                yield TestClient(app)

                app.dependency_overrides.clear()


class TestProtectedEndpoints:
    """Integration tests for comprehensive endpoint protection."""

    def test_all_document_endpoints_require_auth(self, unauthenticated_client):
        """Test that all document endpoints require authentication.

        Validates FR-025: All backend API endpoints must be protected.
        """
        document_endpoints = [
            ("GET", "/weaviate/documents", None),
            ("POST", "/weaviate/documents/upload", {
                "files": {"file": ("test.pdf", b"content", "application/pdf")}
            }),
            ("GET", "/weaviate/documents/fake-id", None),
            ("DELETE", "/weaviate/documents/fake-id", None),
            ("GET", "/weaviate/documents/fake-id/status", None),
            ("GET", "/weaviate/documents/fake-id/download/pdf", None),
            ("GET", "/weaviate/documents/fake-id/download/docling_json", None),
            ("GET", "/weaviate/documents/fake-id/download/processed_json", None),
        ]

        for method, endpoint, payload in document_endpoints:
            if method == "GET":
                response = unauthenticated_client.get(endpoint)
            elif method == "POST":
                if "files" in payload:
                    response = unauthenticated_client.post(endpoint, **payload)
                else:
                    response = unauthenticated_client.post(endpoint, json=payload)
            elif method == "DELETE":
                response = unauthenticated_client.delete(endpoint)

            assert response.status_code == 401, \
                f"{method} {endpoint} must require auth, got {response.status_code}"

            data = response.json()
            assert "detail" in data, \
                f"{method} {endpoint} must include error detail"

    def test_all_chat_endpoints_require_auth(self, unauthenticated_client):
        """Test that all chat endpoints require authentication.

        Validates FR-025: Chat API endpoints protected.
        """
        chat_endpoints = [
            ("POST", "/api/chat", {"message": "test", "session_id": "test"}),
            ("GET", "/api/chat/history", None),
            ("GET", "/api/chat/history/fake-session", None),
            ("DELETE", "/api/chat/session/fake-session", None),
        ]

        for method, endpoint, payload in chat_endpoints:
            if method == "GET":
                response = unauthenticated_client.get(endpoint)
            elif method == "POST":
                response = unauthenticated_client.post(endpoint, json=payload)
            elif method == "DELETE":
                response = unauthenticated_client.delete(endpoint)

            assert response.status_code == 401, \
                f"{method} {endpoint} must require auth, got {response.status_code}"

            data = response.json()
            assert "detail" in data

    def test_user_endpoints_require_auth(self, unauthenticated_client):
        """Test that user profile endpoints require authentication.

        Validates FR-025: User endpoints protected.
        """
        user_endpoints = [
            ("GET", "/users/me", None),
        ]

        for method, endpoint, payload in user_endpoints:
            response = unauthenticated_client.get(endpoint)

            assert response.status_code == 401, \
                f"{method} {endpoint} must require auth, got {response.status_code}"

            data = response.json()
            assert "detail" in data

    def test_settings_endpoints_require_auth(self, unauthenticated_client):
        """Test that settings endpoints require authentication.

        Validates FR-025: Settings API protected.

        NOTE: This test will FAIL if settings endpoints lack auth protection.
        """
        settings_endpoints = [
            ("GET", "/weaviate/settings", None),
            ("PUT", "/weaviate/settings", {
                "embedding": {
                    "model": "text-embedding-3-small",
                    "provider": "openai"
                }
            }),
        ]

        for method, endpoint, payload in settings_endpoints:
            if method == "GET":
                response = unauthenticated_client.get(endpoint)
            elif method == "PUT":
                response = unauthenticated_client.put(endpoint, json=payload)

            assert response.status_code == 401, \
                f"{method} {endpoint} MUST require auth (FR-025). Got {response.status_code}. " \
                f"If this fails, the endpoint is not protected!"

            data = response.json()
            assert "detail" in data

    def test_feedback_endpoints_require_auth(self, unauthenticated_client):
        """Test that feedback endpoints require authentication.

        Validates FR-025: Feedback API protected.

        NOTE: This test will FAIL if feedback endpoint lacks auth protection.
        """
        feedback_payload = {
            "session_id": "test",
            "curator_id": "test",
            "feedback_text": "test",
            "trace_ids": []
        }

        response = unauthenticated_client.post("/api/feedback/submit", json=feedback_payload)

        assert response.status_code == 401, \
            f"Feedback endpoint MUST require auth (FR-025). Got {response.status_code}. " \
            f"If this fails, the endpoint is not protected!"

        data = response.json()
        assert "detail" in data

    def test_health_endpoint_remains_public(self, unauthenticated_client):
        """Test that health endpoint is accessible without authentication.

        Validates: Health endpoint exemption (monitoring requirement).
        """
        response = unauthenticated_client.get("/weaviate/health")

        # Should NOT return 401
        assert response.status_code != 401, \
            "Health endpoint should not require authentication"

        # Should return 200 (ok) or 503 (service unavailable)
        assert response.status_code in [200, 503], \
            f"Health endpoint should return 200 or 503, got {response.status_code}"

    def test_root_endpoint_remains_public(self, unauthenticated_client):
        """Test that root API info endpoint is public.

        Validates: Root endpoint for API discoverability.
        """
        response = unauthenticated_client.get("/")

        assert response.status_code == 200, \
            f"Root endpoint should be public, got {response.status_code}"

        data = response.json()
        assert "service" in data

    def test_authenticated_access_succeeds(self, authenticated_client):
        """Test that valid authentication allows access to protected endpoints.

        Validates: Authentication allows legitimate access.
        """
        # Test a few representative endpoints with valid auth
        response = authenticated_client.get("/users/me")

        # Should succeed (200) or return appropriate success/error (not 401)
        assert response.status_code != 401, \
            f"Valid auth should not return 401, got {response.status_code}"

    def test_error_messages_indicate_auth_issue(self, unauthenticated_client):
        """Test that 401 error messages clearly indicate authentication problem.

        Validates FR-027: Appropriate error messages for auth failures.
        """
        test_endpoints = [
            "/weaviate/documents",
            "/api/chat/history",
            "/users/me",
        ]

        for endpoint in test_endpoints:
            response = unauthenticated_client.get(endpoint)

            assert response.status_code == 401
            data = response.json()

            assert "detail" in data, \
                f"{endpoint} should include error detail"

            error_msg = data["detail"].lower()
            assert any(keyword in error_msg for keyword in [
                "authenticate", "not authenticated", "unauthorized", "auth"
            ]), f"{endpoint} error should indicate auth issue: {data['detail']}"

    def test_comprehensive_endpoint_coverage(self, unauthenticated_client):
        """Test comprehensive list of all protected endpoints.

        Validates: Complete API surface protection.

        NOTE: This test documents ALL endpoints that should be protected.
        If a new endpoint is added, it should be added to this list.
        """
        all_protected_endpoints = [
            # Document endpoints
            ("GET", "/weaviate/documents"),
            ("POST", "/weaviate/documents/upload"),
            ("GET", "/weaviate/documents/{id}"),
            ("DELETE", "/weaviate/documents/{id}"),
            ("GET", "/weaviate/documents/{id}/status"),
            ("GET", "/weaviate/documents/{id}/download/pdf"),
            ("GET", "/weaviate/documents/{id}/download/docling_json"),
            ("GET", "/weaviate/documents/{id}/download/processed_json"),

            # Chat endpoints
            ("POST", "/api/chat"),
            ("GET", "/api/chat/history"),
            ("GET", "/api/chat/history/{session_id}"),
            ("DELETE", "/api/chat/session/{session_id}"),

            # User endpoints
            ("GET", "/users/me"),

            # Settings endpoints (should be protected)
            ("GET", "/weaviate/settings"),
            ("PUT", "/weaviate/settings"),

            # Feedback endpoints (should be protected)
            ("POST", "/api/feedback/submit"),
        ]

        failures = []

        for method, endpoint_template in all_protected_endpoints:
            # Replace path parameters with test values
            endpoint = endpoint_template.replace("{id}", "fake-id")
            endpoint = endpoint.replace("{session_id}", "fake-session")

            try:
                if method == "GET":
                    response = unauthenticated_client.get(endpoint)
                elif method == "POST":
                    # Provide minimal valid payload for each endpoint type
                    if "chat" in endpoint and "/api/chat" == endpoint:
                        payload = {"message": "test", "session_id": "test"}
                    elif "feedback" in endpoint:
                        payload = {
                            "session_id": "test",
                            "curator_id": "test",
                            "feedback_text": "test",
                            "trace_ids": []
                        }
                    elif "upload" in endpoint:
                        response = unauthenticated_client.post(
                            endpoint,
                            files={"file": ("test.pdf", b"content", "application/pdf")}
                        )
                        if response.status_code != 401:
                            failures.append(f"{method} {endpoint}: got {response.status_code}")
                        continue
                    else:
                        payload = {}

                    response = unauthenticated_client.post(endpoint, json=payload)
                elif method == "PUT":
                    if "settings" in endpoint:
                        payload = {"embedding": {"model": "test", "provider": "openai"}}
                    else:
                        payload = {}
                    response = unauthenticated_client.put(endpoint, json=payload)
                elif method == "DELETE":
                    response = unauthenticated_client.delete(endpoint)

                if response.status_code != 401:
                    failures.append(
                        f"{method} {endpoint_template}: expected 401, got {response.status_code}"
                    )

            except Exception as e:
                failures.append(f"{method} {endpoint_template}: error {str(e)}")

        # Report all failures at once for easier debugging
        if failures:
            failure_msg = "\n".join(failures)
            pytest.fail(
                f"The following endpoints are not properly protected:\n{failure_msg}\n\n"
                f"All protected endpoints MUST return 401 without authentication (FR-025)."
            )

    def test_public_endpoints_list(self, unauthenticated_client):
        """Test and document all endpoints that should be public.

        Validates: Only authorized endpoints are public.
        """
        public_endpoints = [
            ("GET", "/"),  # Root API info
            ("GET", "/weaviate/health"),  # Health check
            # Add any other legitimately public endpoints here
        ]

        for method, endpoint in public_endpoints:
            response = unauthenticated_client.get(endpoint)

            assert response.status_code != 401, \
                f"Public endpoint {endpoint} should not require auth, got {response.status_code}"

            # Document why this endpoint is public
            endpoint_reasons = {
                "/": "API discovery and information",
                "/weaviate/health": "Service monitoring and health checks",
            }

            reason = endpoint_reasons.get(endpoint, "Unknown reason")
            print(f"✓ {endpoint} is public: {reason}")

    def test_no_information_disclosure_in_401_errors(self, unauthenticated_client):
        """Test that 401 errors don't disclose sensitive information.

        Validates: Security best practice (information disclosure prevention).
        """
        # Test with various invalid document IDs
        test_ids = [
            "real-looking-uuid-12345678",
            "00000000-0000-0000-0000-000000000000",
            "../../../etc/passwd",  # Path traversal attempt
            "' OR '1'='1",  # SQL injection attempt
        ]

        for doc_id in test_ids:
            response = unauthenticated_client.get(f"/weaviate/documents/{doc_id}")

            assert response.status_code == 401, \
                f"Should return 401 before validating document ID"

            data = response.json()

            # Error message should not reveal:
            # - Whether document exists
            # - Database structure
            # - Internal paths
            # - Other implementation details

            detail = data.get("detail", "").lower()
            assert "not authenticated" in detail or "unauthorized" in detail, \
                f"Error message should be generic, got: {data.get('detail')}"

            # Should NOT contain implementation details
            forbidden_terms = ["database", "table", "column", "weaviate", "tenant", "path"]
            for term in forbidden_terms:
                assert term not in detail, \
                    f"Error message should not disclose '{term}': {data.get('detail')}"
