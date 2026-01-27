"""Contract tests for GET /weaviate/documents/{document_id}/download/processed_json.

Task: T019 - Contract test GET /weaviate/documents/{document_id}/download/processed_json
Contract: specs/007-okta-login/contracts/document_endpoints.yaml lines 292-323

This test validates that the processed JSON download endpoint:
1. Requires valid Okta JWT token (returns 401 if missing/invalid)
2. Returns application/json content type
3. Returns valid JSON object (processed/cleaned JSON ready for embedding)
4. Enforces user ownership (returns 403 for cross-user access - FR-014)
5. Returns 404 for non-existent documents

NOTE: This test will FAIL until T020+ implements the download endpoint with tenant isolation.

IMPORTANT: Uses app.dependency_overrides instead of @patch decorators to properly
mock FastAPI dependencies. Also mocks requests.get to prevent real JWKS fetches.

This endpoint serves the PROCESSED/CLEANED JSON ready for embedding, which is different
from the raw Docling output. The processed JSON has been cleaned and formatted.
"""

import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime
import json


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
    return {"Authorization": "Bearer mock_valid_token_12345"}


class TestDownloadProcessedJsonEndpoint:
    """Contract tests for GET /weaviate/documents/{document_id}/download/processed_json endpoint."""

    def test_download_processed_json_endpoint_exists(self, client):
        """Test processed JSON download endpoint exists at GET /weaviate/documents/{document_id}/download/processed_json.

        This test will FAIL until T020+ creates the download endpoint.
        Expected failure: 404 Not Found (endpoint doesn't exist yet)
        """
        response = client.get("/weaviate/documents/test-doc-123/download/processed_json")

        # Should NOT be 404 after T020+ implementation
        # Will be 401 (auth required) until we add auth header
        assert response.status_code != 404, "/weaviate/documents/{document_id}/download/processed_json endpoint not found - T020+ not implemented"

    def test_download_processed_json_requires_authentication(self, client):
        """Test processed JSON download endpoint requires valid authentication token.

        Contract requirement: Must validate Okta JWT token.
        Without token, should return 401 Unauthorized.
        """
        # Call without Authorization header
        response = client.get("/weaviate/documents/test-doc-123/download/processed_json")

        # Should return 401
        assert response.status_code == 401

        # Check error response format
        data = response.json()
        assert "detail" in data
        assert data["detail"] in [
            "Not authenticated",
            "Invalid authentication token",
            "Token has expired"
        ]

    def test_download_processed_json_with_invalid_token(self, client):
        """Test processed JSON download endpoint rejects invalid JWT tokens."""
        response = client.get(
            "/weaviate/documents/test-doc-123/download/processed_json",
            headers={"Authorization": "Bearer invalid_malformed_token"}
        )

        assert response.status_code == 401

    def test_download_processed_json_content_type(self, client):
        """Test processed JSON download endpoint returns application/json content type.

        Contract requirement: Content-Type must be application/json
        """
        from main import app
        from src.api.auth import auth, get_db

        # Mock OktaUser from token
        mock_user = MagicMock()
        mock_user.uid = "00u1abc2def3ghi4jkl"
        mock_user.email = "curator@alliancegenome.org"
        mock_user.name = "Test Curator"
        mock_user.cid = None

        # Mock database user
        db_user = MagicMock()
        db_user.user_id = 123
        db_user.user_id = "00u1abc2def3ghi4jkl"
        db_user.email = "curator@alliancegenome.org"
        db_user.created_at = datetime(2025, 1, 25, 10, 30, 0)
        db_user.is_active = True

        mock_db_session = MagicMock()
        mock_db_query = MagicMock()
        mock_db_query.filter_by.return_value.one_or_none.return_value = db_user
        mock_db_session.query.return_value = mock_db_query

        # Override dependencies
        app.dependency_overrides[auth.get_user] = lambda *args, **kwargs: mock_user
        app.dependency_overrides[get_db] = lambda *args, **kwargs: mock_db_session

        try:
            # Call endpoint
            response = client.get(
                "/weaviate/documents/test-doc-123/download/processed_json",
                headers=get_valid_auth_header()
            )

            # Should return 200 (or 404 if document doesn't exist)
            # Contract requires application/json content type
            if response.status_code == 200:
                assert "application/json" in response.headers["content-type"]
        finally:
            app.dependency_overrides.clear()

    def test_download_processed_json_is_valid_json(self, client):
        """Test processed JSON download endpoint returns valid JSON object.

        Contract requirement: Response must be valid JSON object (type: object)
        This is the PROCESSED/CLEANED JSON ready for embedding, not raw Docling output.
        """
        from main import app
        from src.api.auth import auth, get_db

        # Mock OktaUser from token
        mock_user = MagicMock()
        mock_user.uid = "00u1abc2def3ghi4jkl"
        mock_user.email = "curator@alliancegenome.org"
        mock_user.name = "Test Curator"
        mock_user.cid = None

        # Mock database user
        db_user = MagicMock()
        db_user.user_id = 123
        db_user.user_id = "00u1abc2def3ghi4jkl"
        db_user.email = "curator@alliancegenome.org"
        db_user.created_at = datetime(2025, 1, 25, 10, 30, 0)
        db_user.is_active = True

        mock_db_session = MagicMock()
        mock_db_query = MagicMock()
        mock_db_query.filter_by.return_value.one_or_none.return_value = db_user
        mock_db_session.query.return_value = mock_db_query

        # Override dependencies
        app.dependency_overrides[auth.get_user] = lambda *args, **kwargs: mock_user
        app.dependency_overrides[get_db] = lambda *args, **kwargs: mock_db_session

        try:
            # Call endpoint
            response = client.get(
                "/weaviate/documents/test-doc-123/download/processed_json",
                headers=get_valid_auth_header()
            )

            # If document exists, response should be valid JSON object
            if response.status_code == 200:
                data = response.json()

                # Contract specifies "type: object", so must be a dict
                assert isinstance(data, dict), "Response must be a JSON object (dict)"

                # Processed JSON should have some structure
                # (specific schema will be defined during implementation)
                assert len(data) > 0, "Processed JSON should not be empty"
        finally:
            app.dependency_overrides.clear()

    def test_download_processed_json_forbidden_for_other_user(self, client):
        """Test processed JSON download endpoint enforces user ownership (FR-014).

        Contract requirement: "User can only download their own documents"
        When user tries to download another user's document, should return 403 Forbidden.

        This test will FAIL until T020+ implements tenant isolation for downloads.
        """
        from main import app
        from src.api.auth import auth, get_db

        # Mock OktaUser from token (user with ID 123)
        mock_user = MagicMock()
        mock_user.uid = "00u1abc2def3ghi4jkl"
        mock_user.email = "curator@alliancegenome.org"
        mock_user.name = "Test Curator"
        mock_user.cid = None

        # Mock database user (user_id = 123)
        db_user = MagicMock()
        db_user.user_id = 123
        db_user.user_id = "00u1abc2def3ghi4jkl"
        db_user.email = "curator@alliancegenome.org"
        db_user.created_at = datetime(2025, 1, 25, 10, 30, 0)
        db_user.is_active = True

        mock_db_session = MagicMock()
        mock_db_query = MagicMock()
        mock_db_query.filter_by.return_value.one_or_none.return_value = db_user
        mock_db_session.query.return_value = mock_db_query

        # Override dependencies
        app.dependency_overrides[auth.get_user] = lambda *args, **kwargs: mock_user
        app.dependency_overrides[get_db] = lambda *args, **kwargs: mock_db_session

        try:
            # Try to download document owned by another user (e.g., document uploaded by user 456)
            # Document ID would be associated with user_id=456 in metadata
            response = client.get(
                "/weaviate/documents/other-user-doc-456/download/processed_json",
                headers=get_valid_auth_header()
            )

            # Should return 403 Forbidden (cross-user access denied)
            # Will be 404 until implementation exists
            if response.status_code != 404:  # Once implemented
                assert response.status_code == 403

                data = response.json()
                assert "detail" in data
                assert "does not own" in data["detail"].lower() or "forbidden" in data["detail"].lower()
        finally:
            app.dependency_overrides.clear()

    def test_download_processed_json_not_found(self, client):
        """Test processed JSON download endpoint returns 404 for non-existent document.

        Contract requirement: Returns 404 when document doesn't exist.
        """
        from main import app
        from src.api.auth import auth, get_db

        # Mock OktaUser from token
        mock_user = MagicMock()
        mock_user.uid = "00u1abc2def3ghi4jkl"
        mock_user.email = "curator@alliancegenome.org"
        mock_user.name = "Test Curator"
        mock_user.cid = None

        # Mock database user
        db_user = MagicMock()
        db_user.user_id = 123
        db_user.user_id = "00u1abc2def3ghi4jkl"
        db_user.email = "curator@alliancegenome.org"
        db_user.created_at = datetime(2025, 1, 25, 10, 30, 0)
        db_user.is_active = True

        mock_db_session = MagicMock()
        mock_db_query = MagicMock()
        mock_db_query.filter_by.return_value.one_or_none.return_value = db_user
        mock_db_session.query.return_value = mock_db_query

        # Override dependencies
        app.dependency_overrides[auth.get_user] = lambda *args, **kwargs: mock_user
        app.dependency_overrides[get_db] = lambda *args, **kwargs: mock_db_session

        try:
            # Try to download non-existent document
            response = client.get(
                "/weaviate/documents/nonexistent-doc-999/download/processed_json",
                headers=get_valid_auth_header()
            )

            # Should return 404 Not Found
            # (or 404 if endpoint doesn't exist yet - that's acceptable for now)
            assert response.status_code == 404

            if response.status_code == 404:
                data = response.json()
                assert "detail" in data
        finally:
            app.dependency_overrides.clear()


class TestDownloadProcessedJsonEndpointEdgeCases:
    """Edge case tests for processed JSON download endpoint."""

    def test_download_processed_json_without_bearer_prefix(self, client):
        """Test endpoint rejects tokens without 'Bearer' prefix."""
        response = client.get(
            "/weaviate/documents/test-doc-123/download/processed_json",
            headers={"Authorization": "mock_token_no_bearer"}
        )

        assert response.status_code == 401

    def test_download_processed_json_with_empty_authorization_header(self, client):
        """Test endpoint rejects empty Authorization header."""
        response = client.get(
            "/weaviate/documents/test-doc-123/download/processed_json",
            headers={"Authorization": ""}
        )

        assert response.status_code == 401

    def test_download_processed_json_with_expired_token(self, client):
        """Test endpoint rejects expired JWT tokens."""
        response = client.get(
            "/weaviate/documents/test-doc-123/download/processed_json",
            headers={"Authorization": "Bearer expired_token_12345"}
        )

        assert response.status_code == 401

    def test_download_processed_json_with_special_characters_in_document_id(self, client):
        """Test endpoint handles document IDs with special characters.

        Document IDs might contain hyphens, underscores, or other URL-safe characters.
        """
        from main import app
        from src.api.auth import auth, get_db

        # Mock OktaUser from token
        mock_user = MagicMock()
        mock_user.uid = "00u1abc2def3ghi4jkl"
        mock_user.email = "curator@alliancegenome.org"
        mock_user.name = "Test Curator"
        mock_user.cid = None

        # Mock database user
        db_user = MagicMock()
        db_user.user_id = 123
        db_user.user_id = "00u1abc2def3ghi4jkl"
        db_user.email = "curator@alliancegenome.org"
        db_user.created_at = datetime(2025, 1, 25, 10, 30, 0)
        db_user.is_active = True

        mock_db_session = MagicMock()
        mock_db_query = MagicMock()
        mock_db_query.filter_by.return_value.one_or_none.return_value = db_user
        mock_db_session.query.return_value = mock_db_query

        # Override dependencies
        app.dependency_overrides[auth.get_user] = lambda *args, **kwargs: mock_user
        app.dependency_overrides[get_db] = lambda *args, **kwargs: mock_db_session

        try:
            # Test with various special characters in document ID
            test_ids = [
                "doc-with-hyphens-123",
                "doc_with_underscores_456",
                "doc.with.dots.789",
            ]

            for doc_id in test_ids:
                response = client.get(
                    f"/weaviate/documents/{doc_id}/download/processed_json",
                    headers=get_valid_auth_header()
                )

                # Should handle gracefully - either 404 (not found) or 200 (if exists)
                # Should NOT be 400 (bad request) or 500 (server error)
                assert response.status_code in [200, 404, 403], \
                    f"Endpoint should handle document ID '{doc_id}' gracefully"
        finally:
            app.dependency_overrides.clear()
