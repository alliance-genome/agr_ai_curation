"""Contract tests for GET /weaviate/documents/{document_id}.

Task: T014 - Contract test for GET /weaviate/documents/{document_id}
Contract: specs/007-okta-login/contracts/document_endpoints.yaml lines 114-145

This test validates that the GET /weaviate/documents/{document_id} endpoint:
1. Requires valid JWT token (returns 401 if missing/invalid)
2. Returns Document schema with all required fields including weaviate_tenant
3. Returns 404 for non-existent document_id
4. Returns 403 when user tries to access another user's document (cross-user access)
5. Enforces user-specific data isolation (FR-014, FR-015)

IMPORTANT: 403 vs 404 distinction:
- 403 Forbidden: Document exists but belongs to another user (cross-user access attempt)
- 404 Not Found: Document does not exist in the system at all

NOTE: This test will FAIL until T025 implements document router with GET /{document_id} endpoint.

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


class TestGetDocumentEndpoint:
    """Contract tests for GET /weaviate/documents/{document_id} endpoint."""

    def test_get_document_endpoint_exists(self, client):
        """Test GET /weaviate/documents/{document_id} endpoint exists.

        This test will FAIL until T025 creates the document router.
        Expected failure: 404 Not Found (endpoint doesn't exist yet)
        """
        response = client.get("/weaviate/documents/doc_abc123")

        # Should NOT be 404 after T025 implementation
        # Will be 401 (auth required) or 403/404 (document-specific error) until we add auth header
        assert response.status_code != 404, (
            "GET /weaviate/documents/{document_id} endpoint not found - T025 not implemented"
        )

    def test_get_document_requires_authentication(self, client):
        """Test GET /weaviate/documents/{document_id} requires authentication.

        Contract requirement: Must validate JWT token.
        Without token, should return 401 Unauthorized.
        """
        # Call without Authorization header
        response = client.get("/weaviate/documents/doc_abc123")

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

    def test_get_document_with_invalid_token(self, client):
        """Test GET /weaviate/documents/{document_id} rejects invalid JWT tokens."""
        response = client.get(
            "/weaviate/documents/doc_abc123",
            headers={"Authorization": "Bearer invalid_malformed_token"}
        )

        assert response.status_code == 401

    def test_get_document_response_schema(self, client):
        """Test GET /weaviate/documents/{document_id} returns correct Document schema.

        Contract schema (Document):
        {
          "document_id": "f35596eb-618d-4904-822f-a15eacc5ec94",
          "user_id": 123,
          "filename": "research_paper.pdf",
          "status": "COMPLETED",
          "upload_timestamp": "2025-01-25T10:30:00Z",
          "processing_started_at": "2025-01-25T10:30:05Z",  // nullable
          "processing_completed_at": "2025-01-25T10:35:00Z",  // nullable
          "file_size_bytes": 1048576,
          "weaviate_tenant": "00u1abc2_def3_ghi4_jkl5",
          "chunk_count": 42,  // nullable
          "error_message": null  // nullable
        }

        Required fields: document_id, user_id, filename, status,
                        upload_timestamp, weaviate_tenant

        This test will FAIL until T025 implements GET /{document_id} endpoint.
        """
        from main import app
        from src.api.auth import auth, get_db

        # Mock authenticated user from token
        mock_user = MagicMock()
        mock_user.uid = "00u1abc2-def3-ghi4-jkl5"
        mock_user.email = "curator@alliancegenome.org"
        mock_user.name = "Test Curator"
        mock_user.cid = None

        # Mock database document (owned by this user)
        db_document = MagicMock()
        db_document.document_id = "f35596eb-618d-4904-822f-a15eacc5ec94"
        db_document.user_id = 123
        db_document.filename = "research_paper.pdf"
        db_document.status = "COMPLETED"
        db_document.upload_timestamp = datetime(2025, 1, 25, 10, 30, 0)
        db_document.processing_started_at = datetime(2025, 1, 25, 10, 30, 5)
        db_document.processing_completed_at = datetime(2025, 1, 25, 10, 35, 0)
        db_document.file_size_bytes = 1048576
        db_document.weaviate_tenant = "00u1abc2_def3_ghi4_jkl5"
        db_document.chunk_count = 42
        db_document.error_message = None

        # Mock user lookup (to verify user_id = 123 owns this user)
        db_user = MagicMock()
        db_user.user_id = 123
        db_user.user_id = "00u1abc2-def3-ghi4-jkl5"

        mock_db_session = MagicMock()
        mock_db_query = MagicMock()

        # Configure query mock to return different results based on filter
        def query_side_effect(model):
            query_mock = MagicMock()
            if model.__name__ == 'PDFDocument':
                query_mock.filter_by.return_value.one_or_none.return_value = db_document
            elif model.__name__ == 'User':
                query_mock.filter_by.return_value.one_or_none.return_value = db_user
            return query_mock

        mock_db_session.query.side_effect = query_side_effect

        # Override dependencies
        app.dependency_overrides[auth.get_user] = lambda *args, **kwargs: mock_user
        app.dependency_overrides[get_db] = lambda *args, **kwargs: mock_db_session

        try:
            # Call endpoint
            response = client.get(
                "/weaviate/documents/doc_abc123",
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

            assert "user_id" in data
            assert isinstance(data["user_id"], int)
            assert data["user_id"] == 123

            assert "filename" in data
            assert isinstance(data["filename"], str)
            assert data["filename"] == "research_paper.pdf"

            assert "status" in data
            assert isinstance(data["status"], str)
            assert data["status"] in ["PENDING", "PROCESSING", "COMPLETED", "FAILED"]

            assert "upload_timestamp" in data
            assert isinstance(data["upload_timestamp"], str)
            # Validate ISO 8601 datetime format
            datetime.fromisoformat(data["upload_timestamp"].replace('Z', '+00:00'))

            assert "weaviate_tenant" in data
            assert isinstance(data["weaviate_tenant"], str)
            assert data["weaviate_tenant"] == "00u1abc2_def3_ghi4_jkl5"

            # Optional fields (nullable in contract)
            assert "processing_started_at" in data
            if data["processing_started_at"] is not None:
                assert isinstance(data["processing_started_at"], str)
                datetime.fromisoformat(data["processing_started_at"].replace('Z', '+00:00'))

            assert "processing_completed_at" in data
            if data["processing_completed_at"] is not None:
                assert isinstance(data["processing_completed_at"], str)
                datetime.fromisoformat(data["processing_completed_at"].replace('Z', '+00:00'))

            assert "file_size_bytes" in data
            if data["file_size_bytes"] is not None:
                assert isinstance(data["file_size_bytes"], int)

            assert "chunk_count" in data
            if data["chunk_count"] is not None:
                assert isinstance(data["chunk_count"], int)

            assert "error_message" in data
            if data["error_message"] is not None:
                assert isinstance(data["error_message"], str)
        finally:
            app.dependency_overrides.clear()

    def test_get_document_not_found(self, client):
        """Test GET /weaviate/documents/{document_id} returns 404 for non-existent document.

        Contract requirement: Return 404 when document does not exist in the system.

        IMPORTANT: 404 is for documents that don't exist at all.
        For documents that exist but belong to another user, use 403 (see next test).
        """
        from main import app
        from src.api.auth import auth, get_db

        # Mock authenticated user
        mock_user = MagicMock()
        mock_user.uid = "00u1abc2-def3-ghi4-jkl5"
        mock_user.email = "curator@alliancegenome.org"

        # Mock user lookup
        db_user = MagicMock()
        db_user.user_id = 123
        db_user.user_id = "00u1abc2-def3-ghi4-jkl5"

        mock_db_session = MagicMock()
        mock_db_query = MagicMock()

        # Configure query to return user but NO document
        def query_side_effect(model):
            query_mock = MagicMock()
            if model.__name__ == 'PDFDocument':
                query_mock.filter_by.return_value.one_or_none.return_value = None  # Document not found
            elif model.__name__ == 'User':
                query_mock.filter_by.return_value.one_or_none.return_value = db_user
            return query_mock

        mock_db_session.query.side_effect = query_side_effect

        # Override dependencies
        app.dependency_overrides[auth.get_user] = lambda *args, **kwargs: mock_user
        app.dependency_overrides[get_db] = lambda *args, **kwargs: mock_db_session

        try:
            response = client.get(
                "/weaviate/documents/nonexistent_doc_xyz",
                headers=get_valid_auth_header()
            )

            # Should return 404
            assert response.status_code == 404

            # Validate error response
            data = response.json()
            assert "detail" in data
            assert "not found" in data["detail"].lower() or "does not exist" in data["detail"].lower()
        finally:
            app.dependency_overrides.clear()

    def test_get_document_forbidden_for_other_user(self, client):
        """Test GET /weaviate/documents/{document_id} returns 403 for cross-user access.

        Contract requirement (FR-014, FR-015):
        "User can only access their own documents"

        When a user tries to access a document that exists but belongs to another user,
        the endpoint must return 403 Forbidden (not 404).

        IMPORTANT: 403 vs 404 distinction:
        - 403 Forbidden: Document exists but belongs to another user (reveals document exists)
        - 404 Not Found: Document does not exist at all in the system

        This prevents users from discovering other users' documents through enumeration.
        """
        from main import app
        from src.api.auth import auth, get_db

        # Mock authenticated user (user_id = 123)
        mock_user = MagicMock()
        mock_user.uid = "00u1abc2-def3-ghi4-jkl5"
        mock_user.email = "curator1@alliancegenome.org"

        # Mock current user lookup
        db_user = MagicMock()
        db_user.user_id = 123
        db_user.user_id = "00u1abc2-def3-ghi4-jkl5"

        # Mock database document owned by DIFFERENT user (user_id = 456)
        db_document = MagicMock()
        db_document.document_id = "doc_other_user"
        db_document.user_id = 456  # Different user!
        db_document.filename = "other_user_paper.pdf"
        db_document.status = "COMPLETED"
        db_document.weaviate_tenant = "00u9xyz8_new7_user6_abc"

        mock_db_session = MagicMock()
        mock_db_query = MagicMock()

        # Configure query to return document with different user_id
        def query_side_effect(model):
            query_mock = MagicMock()
            if model.__name__ == 'PDFDocument':
                query_mock.filter_by.return_value.one_or_none.return_value = db_document
            elif model.__name__ == 'User':
                query_mock.filter_by.return_value.one_or_none.return_value = db_user
            return query_mock

        mock_db_session.query.side_effect = query_side_effect

        # Override dependencies
        app.dependency_overrides[auth.get_user] = lambda *args, **kwargs: mock_user
        app.dependency_overrides[get_db] = lambda *args, **kwargs: mock_db_session

        try:
            response = client.get(
                "/weaviate/documents/doc_other_user",
                headers=get_valid_auth_header()
            )

            # Should return 403 (not 404)
            assert response.status_code == 403

            # Validate error response
            data = response.json()
            assert "detail" in data
            # Error message should indicate permission/ownership issue
            assert any(keyword in data["detail"].lower() for keyword in [
                "not own", "does not own", "forbidden", "permission", "access denied"
            ]), f"Expected ownership-related error message, got: {data['detail']}"
        finally:
            app.dependency_overrides.clear()

    def test_get_document_has_all_required_fields(self, client):
        """Test GET /weaviate/documents/{document_id} includes all required fields.

        Contract schema requires:
        - document_id (string)
        - user_id (integer)
        - filename (string)
        - status (enum: PENDING, PROCESSING, COMPLETED, FAILED)
        - upload_timestamp (ISO 8601 datetime)
        - weaviate_tenant (string) ← CRITICAL for multi-tenancy

        Optional fields (nullable):
        - processing_started_at
        - processing_completed_at
        - file_size_bytes
        - chunk_count
        - error_message
        """
        from main import app
        from src.api.auth import auth, get_db

        # Mock authenticated user
        mock_user = MagicMock()
        mock_user.uid = "00u1abc2-def3-ghi4-jkl5"
        mock_user.email = "curator@alliancegenome.org"

        # Mock minimal document (only required fields, nulls for optional)
        db_document = MagicMock()
        db_document.document_id = "doc_minimal"
        db_document.user_id = 123
        db_document.filename = "minimal.pdf"
        db_document.status = "PENDING"
        db_document.upload_timestamp = datetime(2025, 1, 25, 10, 30, 0)
        db_document.weaviate_tenant = "00u1abc2_def3_ghi4_jkl5"
        # All optional fields are None
        db_document.processing_started_at = None
        db_document.processing_completed_at = None
        db_document.file_size_bytes = None
        db_document.chunk_count = None
        db_document.error_message = None

        # Mock user lookup
        db_user = MagicMock()
        db_user.user_id = 123
        db_user.user_id = "00u1abc2-def3-ghi4-jkl5"

        mock_db_session = MagicMock()
        mock_db_query = MagicMock()

        def query_side_effect(model):
            query_mock = MagicMock()
            if model.__name__ == 'PDFDocument':
                query_mock.filter_by.return_value.one_or_none.return_value = db_document
            elif model.__name__ == 'User':
                query_mock.filter_by.return_value.one_or_none.return_value = db_user
            return query_mock

        mock_db_session.query.side_effect = query_side_effect

        # Override dependencies
        app.dependency_overrides[auth.get_user] = lambda *args, **kwargs: mock_user
        app.dependency_overrides[get_db] = lambda *args, **kwargs: mock_db_session

        try:
            response = client.get(
                "/weaviate/documents/doc_minimal",
                headers=get_valid_auth_header()
            )

            assert response.status_code == 200

            data = response.json()

            # Verify all required fields are present
            required_fields = [
                "document_id",
                "user_id",
                "filename",
                "status",
                "upload_timestamp",
                "weaviate_tenant"
            ]

            for field in required_fields:
                assert field in data, f"Required field '{field}' missing from response"
                assert data[field] is not None, f"Required field '{field}' is null"

            # Verify weaviate_tenant specifically (critical for multi-tenancy)
            assert data["weaviate_tenant"] == "00u1abc2_def3_ghi4_jkl5"

            # Optional fields can be present with null values
            optional_fields = [
                "processing_started_at",
                "processing_completed_at",
                "file_size_bytes",
                "chunk_count",
                "error_message"
            ]

            for field in optional_fields:
                # Field should be in response even if null
                assert field in data, f"Optional field '{field}' should be in response"
        finally:
            app.dependency_overrides.clear()


class TestGetDocumentEndpointEdgeCases:
    """Edge case tests for GET /weaviate/documents/{document_id} endpoint."""

    def test_get_document_without_bearer_prefix(self, client):
        """Test GET /weaviate/documents/{document_id} rejects tokens without 'Bearer' prefix."""
        response = client.get(
            "/weaviate/documents/doc_abc123",
            headers={"Authorization": "mock_token_no_bearer"}
        )

        assert response.status_code == 401

    def test_get_document_with_empty_authorization_header(self, client):
        """Test GET /weaviate/documents/{document_id} rejects empty Authorization header."""
        response = client.get(
            "/weaviate/documents/doc_abc123",
            headers={"Authorization": ""}
        )

        assert response.status_code == 401

    def test_get_document_response_content_type_json(self, client):
        """Test GET /weaviate/documents/{document_id} returns JSON content-type."""
        from main import app
        from src.api.auth import auth, get_db

        # Mock authentication and database
        mock_user = MagicMock()
        mock_user.uid = "00u1abc2-def3-ghi4-jkl5"
        mock_user.email = "curator@alliancegenome.org"

        db_user = MagicMock()
        db_user.user_id = 123
        db_user.user_id = "00u1abc2-def3-ghi4-jkl5"

        db_document = MagicMock()
        db_document.document_id = "f35596eb-618d-4904-822f-a15eacc5ec94"
        db_document.user_id = 123
        db_document.filename = "test.pdf"
        db_document.status = "COMPLETED"
        db_document.upload_timestamp = datetime.utcnow()
        db_document.weaviate_tenant = "00u1abc2_def3_ghi4_jkl5"

        mock_db_session = MagicMock()
        mock_db_query = MagicMock()

        def query_side_effect(model):
            query_mock = MagicMock()
            if model.__name__ == 'PDFDocument':
                query_mock.filter_by.return_value.one_or_none.return_value = db_document
            elif model.__name__ == 'User':
                query_mock.filter_by.return_value.one_or_none.return_value = db_user
            return query_mock

        mock_db_session.query.side_effect = query_side_effect

        # Override dependencies
        app.dependency_overrides[auth.get_user] = lambda *args, **kwargs: mock_user
        app.dependency_overrides[get_db] = lambda *args, **kwargs: mock_db_session

        try:
            response = client.get(
                "/weaviate/documents/doc_abc123",
                headers=get_valid_auth_header()
            )

            assert response.status_code == 200
            assert "application/json" in response.headers["content-type"]
        finally:
            app.dependency_overrides.clear()

    def test_get_document_failed_status_includes_error_message(self, client):
        """Test GET /weaviate/documents/{document_id} includes error_message for FAILED status.

        When document processing fails, the error_message field should contain
        diagnostic information about the failure.
        """
        from main import app
        from src.api.auth import auth, get_db

        # Mock authenticated user
        mock_user = MagicMock()
        mock_user.uid = "00u1abc2-def3-ghi4-jkl5"
        mock_user.email = "curator@alliancegenome.org"

        # Mock user lookup
        db_user = MagicMock()
        db_user.user_id = 123
        db_user.user_id = "00u1abc2-def3-ghi4-jkl5"

        # Mock failed document with error message
        db_document = MagicMock()
        db_document.document_id = "doc_failed"
        db_document.user_id = 123
        db_document.filename = "corrupted.pdf"
        db_document.status = "FAILED"
        db_document.upload_timestamp = datetime(2025, 1, 25, 10, 30, 0)
        db_document.weaviate_tenant = "00u1abc2_def3_ghi4_jkl5"
        db_document.processing_started_at = datetime(2025, 1, 25, 10, 30, 5)
        db_document.processing_completed_at = None
        db_document.error_message = "Docling extraction failed: PDF is password-protected"

        mock_db_session = MagicMock()
        mock_db_query = MagicMock()

        def query_side_effect(model):
            query_mock = MagicMock()
            if model.__name__ == 'PDFDocument':
                query_mock.filter_by.return_value.one_or_none.return_value = db_document
            elif model.__name__ == 'User':
                query_mock.filter_by.return_value.one_or_none.return_value = db_user
            return query_mock

        mock_db_session.query.side_effect = query_side_effect

        # Override dependencies
        app.dependency_overrides[auth.get_user] = lambda *args, **kwargs: mock_user
        app.dependency_overrides[get_db] = lambda *args, **kwargs: mock_db_session

        try:
            response = client.get(
                "/weaviate/documents/doc_failed",
                headers=get_valid_auth_header()
            )

            assert response.status_code == 200

            data = response.json()
            assert data["status"] == "FAILED"
            assert "error_message" in data
            assert data["error_message"] is not None
            assert "Docling extraction failed" in data["error_message"]
        finally:
            app.dependency_overrides.clear()
