"""Contract tests for GET /weaviate/documents/{document_id}/status.

Task: T016 - Contract test for GET /weaviate/documents/{document_id}/status
Contract: specs/007-okta-login/contracts/document_endpoints.yaml lines 176-217

This test validates that the /weaviate/documents/{document_id}/status endpoint:
1. Requires valid JWT token (returns 401 if missing/invalid)
2. Returns status schema with document_id, status, progress, error_message
3. Enforces user ownership (returns 403 for cross-user access)
4. Returns 404 for non-existent documents
5. Validates status enum values (PENDING, PROCESSING, COMPLETED, FAILED)
6. Validates progress range (0-100)

NOTE: This test will FAIL until T025 implements document status endpoint with tenant-scoping.

IMPORTANT: Uses app.dependency_overrides instead of @patch decorators to properly
mock FastAPI dependencies.
"""

import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime
import uuid
from .test_uuids import (
    TEST_DOC_UUID_1,
    TEST_DOC_UUID_2,
    TEST_DOC_UUID_3,
    TEST_DOC_UUID_NONEXISTENT
)



@pytest.fixture
def client(monkeypatch):
    """Create test client with mocked dependencies and JWKS requests."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    # Mock requests.get BEFORE importing main/auth modules
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


class TestDocumentStatusEndpoint:
    """Contract tests for GET /weaviate/documents/{document_id}/status endpoint."""

    def test_status_endpoint_exists(self, client):
        """Test /weaviate/documents/{document_id}/status endpoint exists.

        This test will FAIL until T025 creates the document status endpoint.
        Expected failure: 404 Not Found (endpoint doesn't exist yet)
        """
        response = client.get("/weaviate/documents/doc_abc123/status")

        # Should NOT be 404 after T025 implementation
        # Will be 401 (auth required) until we add auth header
        assert response.status_code != 404, "/weaviate/documents/{document_id}/status endpoint not found - T025 not implemented"

    def test_status_requires_authentication(self, client):
        """Test /weaviate/documents/{document_id}/status requires authentication.

        Contract requirement: Must validate JWT token.
        Without token, should return 401 Unauthorized.
        """
        # Call without Authorization header
        response = client.get("/weaviate/documents/doc_abc123/status")

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

    def test_status_with_invalid_token(self, client):
        """Test /weaviate/documents/{document_id}/status rejects invalid tokens."""
        response = client.get(
            "/weaviate/documents/doc_abc123/status",
            headers={"Authorization": "Bearer invalid_malformed_token"}
        )

        assert response.status_code == 401

    def test_status_response_schema(self, client):
        """Test /weaviate/documents/{document_id}/status returns correct schema.

        Contract schema:
        {
          "document_id": "f35596eb-618d-4904-822f-a15eacc5ec94",
          "status": "PROCESSING",
          "progress": 75,
          "error_message": null
        }

        Required fields: document_id, status, progress, error_message
        status must be one of: PENDING, PROCESSING, COMPLETED, FAILED
        progress must be 0-100
        error_message is nullable

        This test will FAIL until T025 implements document status endpoint.
        """
        from main import app
        from src.api.auth import auth, get_db

        # Mock authenticated user from token
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
        db_user.is_active = True

        # Mock database session for user lookup
        mock_db_session = MagicMock()
        mock_db_query = MagicMock()
        mock_db_query.filter_by.return_value.one_or_none.return_value = db_user
        mock_db_session.query.return_value = mock_db_query

        # Mock document in database (owned by user)
        mock_document = MagicMock()
        mock_document.document_id = "f35596eb-618d-4904-822f-a15eacc5ec94"
        mock_document.user_id = 123  # Same as db_user.user_id
        mock_document.status = "PROCESSING"
        mock_document.progress = 75
        mock_document.error_message = None

        # Setup query to return document
        doc_query = MagicMock()
        doc_query.filter.return_value.first.return_value = mock_document
        mock_db_session.query.side_effect = lambda model: (
            mock_db_query if model.__name__ == "User" else doc_query
        )

        # Override dependencies
        app.dependency_overrides[auth.get_user] = lambda *args, **kwargs: mock_user
        app.dependency_overrides[get_db] = lambda *args, **kwargs: mock_db_session

        try:
            # Call endpoint
            response = client.get(
                "/weaviate/documents/doc_abc123/status",
                headers=get_valid_auth_header()
            )

            # Should return 200
            assert response.status_code == 200

            # Validate response schema
            data = response.json()

            # Required fields
            assert "document_id" in data
            assert isinstance(data["document_id"], str)
            assert data["document_id"] == "f35596eb-618d-4904-822f-a15eacc5ec94"

            assert "status" in data
            assert isinstance(data["status"], str)

            assert "progress" in data
            assert isinstance(data["progress"], int)

            assert "error_message" in data
            # error_message can be string or null
            if data["error_message"] is not None:
                assert isinstance(data["error_message"], str)
        finally:
            app.dependency_overrides.clear()

    def test_status_enum_values(self, client):
        """Test /weaviate/documents/{document_id}/status validates status enum.

        Contract requirement: status must be one of:
        - PENDING
        - PROCESSING
        - COMPLETED
        - FAILED

        This test verifies the status field contains a valid enum value.
        """
        from main import app
        from src.api.auth import auth, get_db

        # Mock authentication
        mock_user = MagicMock()
        mock_user.uid = "00u1abc2def3ghi4jkl"
        mock_user.email = "curator@alliancegenome.org"

        db_user = MagicMock()
        db_user.user_id = 123
        db_user.user_id = "00u1abc2def3ghi4jkl"
        db_user.is_active = True

        mock_db_session = MagicMock()
        mock_db_query = MagicMock()
        mock_db_query.filter_by.return_value.one_or_none.return_value = db_user
        mock_db_session.query.return_value = mock_db_query

        # Mock document
        mock_document = MagicMock()
        mock_document.document_id = "f35596eb-618d-4904-822f-a15eacc5ec94"
        mock_document.user_id = 123
        mock_document.status = "COMPLETED"  # Valid enum value
        mock_document.progress = 100
        mock_document.error_message = None

        doc_query = MagicMock()
        doc_query.filter.return_value.first.return_value = mock_document
        mock_db_session.query.side_effect = lambda model: (
            mock_db_query if model.__name__ == "User" else doc_query
        )

        # Override dependencies
        app.dependency_overrides[auth.get_user] = lambda *args, **kwargs: mock_user
        app.dependency_overrides[get_db] = lambda *args, **kwargs: mock_db_session

        try:
            response = client.get(
                "/weaviate/documents/doc_abc123/status",
                headers=get_valid_auth_header()
            )

            assert response.status_code == 200

            data = response.json()
            assert data["status"] in ["PENDING", "PROCESSING", "COMPLETED", "FAILED"]
        finally:
            app.dependency_overrides.clear()

    def test_status_progress_range(self, client):
        """Test /weaviate/documents/{document_id}/status validates progress range.

        Contract requirement: progress must be 0-100 (inclusive).
        """
        from main import app
        from src.api.auth import auth, get_db

        # Mock authentication
        mock_user = MagicMock()
        mock_user.uid = "00u1abc2def3ghi4jkl"
        mock_user.email = "curator@alliancegenome.org"

        db_user = MagicMock()
        db_user.user_id = 123
        db_user.user_id = "00u1abc2def3ghi4jkl"
        db_user.is_active = True

        mock_db_session = MagicMock()
        mock_db_query = MagicMock()
        mock_db_query.filter_by.return_value.one_or_none.return_value = db_user
        mock_db_session.query.return_value = mock_db_query

        # Mock document
        mock_document = MagicMock()
        mock_document.document_id = "f35596eb-618d-4904-822f-a15eacc5ec94"
        mock_document.user_id = 123
        mock_document.status = "PROCESSING"
        mock_document.progress = 75  # Valid: 0-100
        mock_document.error_message = None

        doc_query = MagicMock()
        doc_query.filter.return_value.first.return_value = mock_document
        mock_db_session.query.side_effect = lambda model: (
            mock_db_query if model.__name__ == "User" else doc_query
        )

        # Override dependencies
        app.dependency_overrides[auth.get_user] = lambda *args, **kwargs: mock_user
        app.dependency_overrides[get_db] = lambda *args, **kwargs: mock_db_session

        try:
            response = client.get(
                "/weaviate/documents/doc_abc123/status",
                headers=get_valid_auth_header()
            )

            assert response.status_code == 200

            data = response.json()
            assert 0 <= data["progress"] <= 100
        finally:
            app.dependency_overrides.clear()

    def test_status_forbidden_for_other_user(self, client):
        """Test /weaviate/documents/{document_id}/status returns 403 for cross-user access.

        Contract requirement (FR-016):
        "User can only check status of their own documents"

        Should return 403 Forbidden when user tries to access another user's document.
        """
        from main import app
        from src.api.auth import auth, get_db

        # Mock authenticated user (user_id 123)
        mock_user = MagicMock()
        mock_user.uid = "00u1abc2def3ghi4jkl"
        mock_user.email = "curator@alliancegenome.org"

        db_user = MagicMock()
        db_user.user_id = 123
        db_user.user_id = "00u1abc2def3ghi4jkl"
        db_user.is_active = True

        mock_db_session = MagicMock()
        mock_db_query = MagicMock()
        mock_db_query.filter_by.return_value.one_or_none.return_value = db_user
        mock_db_session.query.return_value = mock_db_query

        # Mock document owned by DIFFERENT user (user_id 456)
        mock_document = MagicMock()
        mock_document.document_id = "doc_xyz789"
        mock_document.user_id = 456  # Different from db_user.user_id
        mock_document.status = "COMPLETED"
        mock_document.progress = 100
        mock_document.error_message = None

        doc_query = MagicMock()
        doc_query.filter.return_value.first.return_value = mock_document
        mock_db_session.query.side_effect = lambda model: (
            mock_db_query if model.__name__ == "User" else doc_query
        )

        # Override dependencies
        app.dependency_overrides[auth.get_user] = lambda *args, **kwargs: mock_user
        app.dependency_overrides[get_db] = lambda *args, **kwargs: mock_db_session

        try:
            # Try to access another user's document
            response = client.get(
                "/weaviate/documents/doc_xyz789/status",
                headers=get_valid_auth_header()
            )

            # Should return 403 Forbidden
            assert response.status_code == 403

            data = response.json()
            assert "detail" in data or "message" in data
        finally:
            app.dependency_overrides.clear()

    def test_status_not_found(self, client):
        """Test /weaviate/documents/{document_id}/status returns 404 for non-existent document.

        Should return 404 when document doesn't exist in database.
        """
        from main import app
        from src.api.auth import auth, get_db

        # Mock authentication
        mock_user = MagicMock()
        mock_user.uid = "00u1abc2def3ghi4jkl"
        mock_user.email = "curator@alliancegenome.org"

        db_user = MagicMock()
        db_user.user_id = 123
        db_user.user_id = "00u1abc2def3ghi4jkl"
        db_user.is_active = True

        mock_db_session = MagicMock()
        mock_db_query = MagicMock()
        mock_db_query.filter_by.return_value.one_or_none.return_value = db_user
        mock_db_session.query.return_value = mock_db_query

        # Mock document query returns None (document doesn't exist)
        doc_query = MagicMock()
        doc_query.filter.return_value.first.return_value = None  # Not found
        mock_db_session.query.side_effect = lambda model: (
            mock_db_query if model.__name__ == "User" else doc_query
        )

        # Override dependencies
        app.dependency_overrides[auth.get_user] = lambda *args, **kwargs: mock_user
        app.dependency_overrides[get_db] = lambda *args, **kwargs: mock_db_session

        try:
            # Try to access non-existent document
            response = client.get(
                "/weaviate/documents/nonexistent_doc/status",
                headers=get_valid_auth_header()
            )

            # Should return 404 Not Found
            assert response.status_code == 404

            data = response.json()
            assert "detail" in data or "message" in data
        finally:
            app.dependency_overrides.clear()


class TestDocumentStatusEndpointEdgeCases:
    """Edge case tests for /weaviate/documents/{document_id}/status endpoint."""

    def test_status_without_bearer_prefix(self, client):
        """Test endpoint rejects tokens without 'Bearer' prefix."""
        response = client.get(
            "/weaviate/documents/doc_abc123/status",
            headers={"Authorization": "mock_token_no_bearer"}
        )

        assert response.status_code == 401

    def test_status_with_empty_authorization_header(self, client):
        """Test endpoint rejects empty Authorization header."""
        response = client.get(
            "/weaviate/documents/doc_abc123/status",
            headers={"Authorization": ""}
        )

        assert response.status_code == 401

    def test_status_response_content_type_json(self, client):
        """Test endpoint returns JSON content-type."""
        from main import app
        from src.api.auth import auth, get_db

        # Mock authentication and database
        mock_user = MagicMock()
        mock_user.uid = "00u1abc2def3ghi4jkl"
        mock_user.email = "curator@alliancegenome.org"

        db_user = MagicMock()
        db_user.user_id = 123
        db_user.user_id = "00u1abc2def3ghi4jkl"
        db_user.is_active = True

        mock_db_session = MagicMock()
        mock_db_query = MagicMock()
        mock_db_query.filter_by.return_value.one_or_none.return_value = db_user
        mock_db_session.query.return_value = mock_db_query

        # Mock document
        mock_document = MagicMock()
        mock_document.document_id = "f35596eb-618d-4904-822f-a15eacc5ec94"
        mock_document.user_id = 123
        mock_document.status = "PENDING"
        mock_document.progress = 0
        mock_document.error_message = None

        doc_query = MagicMock()
        doc_query.filter.return_value.first.return_value = mock_document
        mock_db_session.query.side_effect = lambda model: (
            mock_db_query if model.__name__ == "User" else doc_query
        )

        # Override dependencies
        app.dependency_overrides[auth.get_user] = lambda *args, **kwargs: mock_user
        app.dependency_overrides[get_db] = lambda *args, **kwargs: mock_db_session

        try:
            response = client.get(
                "/weaviate/documents/doc_abc123/status",
                headers=get_valid_auth_header()
            )

            assert response.status_code == 200
            assert "application/json" in response.headers["content-type"]
        finally:
            app.dependency_overrides.clear()

    def test_status_with_error_message(self, client):
        """Test endpoint correctly returns error_message when document failed.

        When status is FAILED, error_message should contain failure reason.
        """
        from main import app
        from src.api.auth import auth, get_db

        # Mock authentication
        mock_user = MagicMock()
        mock_user.uid = "00u1abc2def3ghi4jkl"
        mock_user.email = "curator@alliancegenome.org"

        db_user = MagicMock()
        db_user.user_id = 123
        db_user.user_id = "00u1abc2def3ghi4jkl"
        db_user.is_active = True

        mock_db_session = MagicMock()
        mock_db_query = MagicMock()
        mock_db_query.filter_by.return_value.one_or_none.return_value = db_user
        mock_db_session.query.return_value = mock_db_query

        # Mock failed document with error message
        mock_document = MagicMock()
        mock_document.document_id = "doc_failed"
        mock_document.user_id = 123
        mock_document.status = "FAILED"
        mock_document.progress = 50
        mock_document.error_message = "Failed to process PDF: Corrupted file"

        doc_query = MagicMock()
        doc_query.filter.return_value.first.return_value = mock_document
        mock_db_session.query.side_effect = lambda model: (
            mock_db_query if model.__name__ == "User" else doc_query
        )

        # Override dependencies
        app.dependency_overrides[auth.get_user] = lambda *args, **kwargs: mock_user
        app.dependency_overrides[get_db] = lambda *args, **kwargs: mock_db_session

        try:
            response = client.get(
                "/weaviate/documents/doc_failed/status",
                headers=get_valid_auth_header()
            )

            assert response.status_code == 200

            data = response.json()
            assert data["status"] == "FAILED"
            assert data["error_message"] is not None
            assert isinstance(data["error_message"], str)
            assert len(data["error_message"]) > 0
        finally:
            app.dependency_overrides.clear()

    def test_status_null_error_message_for_success(self, client):
        """Test endpoint returns null error_message for successful documents.

        When status is COMPLETED/PROCESSING/PENDING, error_message should be null.
        """
        from main import app
        from src.api.auth import auth, get_db

        # Mock authentication
        mock_user = MagicMock()
        mock_user.uid = "00u1abc2def3ghi4jkl"
        mock_user.email = "curator@alliancegenome.org"

        db_user = MagicMock()
        db_user.user_id = 123
        db_user.user_id = "00u1abc2def3ghi4jkl"
        db_user.is_active = True

        mock_db_session = MagicMock()
        mock_db_query = MagicMock()
        mock_db_query.filter_by.return_value.one_or_none.return_value = db_user
        mock_db_session.query.return_value = mock_db_query

        # Mock completed document
        mock_document = MagicMock()
        mock_document.document_id = "doc_success"
        mock_document.user_id = 123
        mock_document.status = "COMPLETED"
        mock_document.progress = 100
        mock_document.error_message = None  # Null for success

        doc_query = MagicMock()
        doc_query.filter.return_value.first.return_value = mock_document
        mock_db_session.query.side_effect = lambda model: (
            mock_db_query if model.__name__ == "User" else doc_query
        )

        # Override dependencies
        app.dependency_overrides[auth.get_user] = lambda *args, **kwargs: mock_user
        app.dependency_overrides[get_db] = lambda *args, **kwargs: mock_db_session

        try:
            response = client.get(
                "/weaviate/documents/doc_success/status",
                headers=get_valid_auth_header()
            )

            assert response.status_code == 200

            data = response.json()
            assert data["status"] == "COMPLETED"
            assert data["error_message"] is None
        finally:
            app.dependency_overrides.clear()
