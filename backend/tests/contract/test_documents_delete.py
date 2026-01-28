"""Contract tests for DELETE /weaviate/documents/{document_id}.

Task: T015 - Contract test DELETE /weaviate/documents/{document_id}
Contract: specs/007-okta-login/contracts/document_endpoints.yaml lines 147-174

This test validates that the DELETE endpoint:
1. Requires valid JWT token (returns 401 if missing/invalid)
2. Returns 204 No Content on successful deletion
3. Returns 404 for non-existent documents
4. Returns 403 when trying to delete another user's document (FR-016)
5. Cascades deletion to all associated data (PDF, Docling JSON, processed JSON, Weaviate embeddings)

NOTE: This test will FAIL until T025 implements document deletion endpoint.

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


class TestDeleteDocumentEndpoint:
    """Contract tests for DELETE /weaviate/documents/{document_id} endpoint."""

    def test_delete_document_endpoint_exists(self, client):
        """Test DELETE /weaviate/documents/{document_id} endpoint exists.

        This test will FAIL until T025 creates the delete document endpoint.
        Expected failure: 404 Not Found (endpoint doesn't exist yet)
        """
        response = client.delete(f"/weaviate/documents/{TEST_DOC_UUID_1}")

        # Should NOT be 404 after T025 implementation
        # Will be 401 (auth required) until we add auth header
        assert response.status_code != 404, "/weaviate/documents/{document_id} DELETE endpoint not found - T025 not implemented"

    def test_delete_requires_authentication(self, client):
        """Test DELETE endpoint requires valid authentication token.

        Contract requirement: Must validate JWT token.
        Without token, should return 401 Unauthorized.
        """
        # Call without Authorization header
        response = client.delete(f"/weaviate/documents/{TEST_DOC_UUID_1}")

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

    def test_delete_with_invalid_token(self, client):
        """Test DELETE endpoint rejects invalid JWT tokens."""
        response = client.delete(
            f"/weaviate/documents/{TEST_DOC_UUID_1}",
            headers={"Authorization": "Bearer invalid_malformed_token"}
        )

        assert response.status_code == 401

    def test_delete_document_success(self, client):
        """Test DELETE endpoint returns 204 No Content on successful deletion.

        Contract response (204):
          description: Document deleted successfully

        User deletes their own document successfully.

        This test will FAIL until T025 implements document deletion endpoint.
        """
        from main import app
        from src.api.auth import auth, get_db

        # Mock authenticated user (document owner)
        mock_user = MagicMock()
        mock_user.uid = "00u1abc2def3ghi4jkl"
        mock_user.email = "curator@alliancegenome.org"

        # Mock database user
        db_user = MagicMock()
        db_user.user_id = 123
        db_user.user_id = "00u1abc2def3ghi4jkl"

        # Mock document owned by this user
        mock_document = MagicMock()
        mock_document.id = uuid.UUID(TEST_DOC_UUID_2)
        mock_document.user_id = 123  # Matches db_user.user_id
        mock_document.filename = "test.pdf"

        mock_db_session = MagicMock()

        # Setup query mocks
        mock_user_query = MagicMock()
        mock_user_query.filter_by.return_value.one_or_none.return_value = db_user

        mock_doc_query = MagicMock()
        mock_doc_query.filter_by.return_value.first.return_value = mock_document

        # Configure query to return different mocks based on model type
        def query_side_effect(model):
            if "User" in str(model):
                return mock_user_query
            else:  # Document
                return mock_doc_query

        mock_db_session.query.side_effect = query_side_effect

        # Override dependencies
        app.dependency_overrides[auth.get_user] = lambda *args, **kwargs: mock_user
        app.dependency_overrides[get_db] = lambda *args, **kwargs: mock_db_session

        try:
            # Delete document
            response = client.delete(
                f"/weaviate/documents/{TEST_DOC_UUID_2}",
                headers=get_valid_auth_header()
            )

            # Should return 204 No Content
            assert response.status_code == 204

            # 204 responses should have no content
            assert response.text == ""
        finally:
            app.dependency_overrides.clear()

    def test_delete_document_not_found(self, client):
        """Test DELETE endpoint returns 404 for non-existent document.

        Contract response (404):
          description: Document not found
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/Error'

        This test will FAIL until T025 implements document deletion endpoint.
        """
        from main import app
        from src.api.auth import auth, get_db

        # Mock authenticated user
        mock_user = MagicMock()
        mock_user.uid = "00u1abc2def3ghi4jkl"
        mock_user.email = "curator@alliancegenome.org"

        # Mock database user
        db_user = MagicMock()
        db_user.user_id = 123
        db_user.user_id = "00u1abc2def3ghi4jkl"

        mock_db_session = MagicMock()

        # Setup query mocks
        mock_user_query = MagicMock()
        mock_user_query.filter_by.return_value.one_or_none.return_value = db_user

        mock_doc_query = MagicMock()
        mock_doc_query.filter_by.return_value.first.return_value = None  # Document not found

        # Configure query to return different mocks based on model type
        def query_side_effect(model):
            if "User" in str(model):
                return mock_user_query
            else:  # Document
                return mock_doc_query

        mock_db_session.query.side_effect = query_side_effect

        # Override dependencies
        app.dependency_overrides[auth.get_user] = lambda *args, **kwargs: mock_user
        app.dependency_overrides[get_db] = lambda *args, **kwargs: mock_db_session

        try:
            # Try to delete non-existent document
            response = client.delete(
                f"/weaviate/documents/{TEST_DOC_UUID_NONEXISTENT}",
                headers=get_valid_auth_header()
            )

            # Should return 404
            assert response.status_code == 404

            # Validate error response format
            data = response.json()
            assert "detail" in data or "message" in data
            error_msg = data.get("detail", data.get("message", "")).lower()
            assert "not found" in error_msg or "does not exist" in error_msg
        finally:
            app.dependency_overrides.clear()

    def test_delete_forbidden_for_other_user(self, client):
        """Test DELETE endpoint returns 403 when trying to delete another user's document.

        Contract requirement (FR-016):
        "Users can only delete their own documents"

        Contract response (403):
          description: User does not own this document
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/Error'

        This test will FAIL until T025 implements ownership validation.
        """
        from main import app
        from src.api.auth import auth, get_db

        # Mock authenticated user (requesting user)
        mock_user = MagicMock()
        mock_user.uid = "00u1abc2def3ghi4jkl"
        mock_user.email = "curator1@alliancegenome.org"

        # Mock database user (user_id = 123)
        db_user = MagicMock()
        db_user.user_id = 123
        db_user.user_id = "00u1abc2def3ghi4jkl"

        # Mock document owned by DIFFERENT user (user_id = 456)
        mock_document = MagicMock()
        mock_document.id = uuid.UUID(TEST_DOC_UUID_3)
        mock_document.user_id = 456  # Different user!
        mock_document.filename = "other_user_doc.pdf"

        mock_db_session = MagicMock()

        # Setup query mocks
        mock_user_query = MagicMock()
        mock_user_query.filter_by.return_value.one_or_none.return_value = db_user

        mock_doc_query = MagicMock()
        mock_doc_query.filter_by.return_value.first.return_value = mock_document

        # Configure query to return different mocks based on model type
        def query_side_effect(model):
            if "User" in str(model):
                return mock_user_query
            else:  # Document
                return mock_doc_query

        mock_db_session.query.side_effect = query_side_effect

        # Override dependencies
        app.dependency_overrides[auth.get_user] = lambda *args, **kwargs: mock_user
        app.dependency_overrides[get_db] = lambda *args, **kwargs: mock_db_session

        try:
            # Try to delete another user's document
            response = client.delete(
                f"/weaviate/documents/{TEST_DOC_UUID_3}",
                headers=get_valid_auth_header()
            )

            # Should return 403 Forbidden
            assert response.status_code == 403

            # Validate error response format
            data = response.json()
            assert "detail" in data or "message" in data
            error_msg = data.get("detail", data.get("message", "")).lower()
            assert "not own" in error_msg or "forbidden" in error_msg or "permission" in error_msg
        finally:
            app.dependency_overrides.clear()

    def test_delete_cascades_all_data(self, client):
        """Test DELETE endpoint cascades deletion to all associated data.

        Contract requirement:
        "Delete a document and all associated data (PDF, Docling JSON,
        processed JSON, Weaviate embeddings)"

        This test DOCUMENTS the cascade deletion requirement but does NOT
        verify actual file/database cleanup. Full cascade verification will be
        done in integration tests.

        The endpoint should trigger deletion of:
        1. Database record (documents table)
        2. PDF file (pdf_storage/pdfs/<user_id>/<document_id>.pdf)
        3. Docling JSON (pdf_storage/docling_json/<user_id>/<document_id>.json)
        4. Processed JSON (pdf_storage/processed_json/<user_id>/<document_id>.json)
        5. Weaviate collection/embeddings (user-specific collection)

        This test only verifies that the endpoint exists and returns proper status.
        Full cascade testing requires integration tests with actual file system
        and Weaviate interactions.
        """
        from main import app
        from src.api.auth import auth, get_db

        # Mock authenticated user (document owner)
        mock_user = MagicMock()
        mock_user.uid = "00u1abc2def3ghi4jkl"
        mock_user.email = "curator@alliancegenome.org"

        # Mock database user
        db_user = MagicMock()
        db_user.user_id = 123
        db_user.user_id = "00u1abc2def3ghi4jkl"

        # Mock document owned by this user
        mock_document = MagicMock()
        mock_document.id = uuid.UUID(TEST_DOC_UUID_2)
        mock_document.user_id = 123
        mock_document.filename = "test_cascade.pdf"

        mock_db_session = MagicMock()

        # Setup query mocks
        mock_user_query = MagicMock()
        mock_user_query.filter_by.return_value.one_or_none.return_value = db_user

        mock_doc_query = MagicMock()
        mock_doc_query.filter_by.return_value.first.return_value = mock_document

        # Configure query to return different mocks based on model type
        def query_side_effect(model):
            if "User" in str(model):
                return mock_user_query
            else:  # Document
                return mock_doc_query

        mock_db_session.query.side_effect = query_side_effect

        # Override dependencies
        app.dependency_overrides[auth.get_user] = lambda *args, **kwargs: mock_user
        app.dependency_overrides[get_db] = lambda *args, **kwargs: mock_db_session

        try:
            # Delete document (should cascade all data)
            response = client.delete(
                f"/weaviate/documents/{TEST_DOC_UUID_2}",
                headers=get_valid_auth_header()
            )

            # Contract test only verifies endpoint behavior, not cascade implementation
            # Should return 204 No Content indicating successful deletion
            assert response.status_code == 204

            # NOTE: This test does NOT verify that files/embeddings were actually deleted.
            # Full cascade verification requires integration tests that:
            # 1. Upload a document (creating all files/embeddings)
            # 2. Verify files exist on filesystem and Weaviate
            # 3. Delete the document
            # 4. Verify all associated data is gone
            #
            # See integration tests for cascade deletion verification.
        finally:
            app.dependency_overrides.clear()


class TestDeleteDocumentEndpointEdgeCases:
    """Edge case tests for DELETE /weaviate/documents/{document_id} endpoint."""

    def test_delete_without_bearer_prefix(self, client):
        """Test DELETE endpoint rejects tokens without 'Bearer' prefix."""
        response = client.delete(
            f"/weaviate/documents/{TEST_DOC_UUID_1}",
            headers={"Authorization": "mock_token_no_bearer"}
        )

        assert response.status_code == 401

    def test_delete_with_empty_authorization_header(self, client):
        """Test DELETE endpoint rejects empty Authorization header."""
        response = client.delete(
            f"/weaviate/documents/{TEST_DOC_UUID_1}",
            headers={"Authorization": ""}
        )

        assert response.status_code == 401

