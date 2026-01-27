"""Contract tests for GET /weaviate/documents/{document_id}/download/docling_json.

Task: T018 - Contract test GET /weaviate/documents/{document_id}/download/docling_json
Contract: specs/007-okta-login/contracts/document_endpoints.yaml lines 253-285

This test validates that the /weaviate/documents/{document_id}/download/docling_json endpoint:
1. Requires valid Okta JWT token (returns 401 if missing/invalid)
2. Returns raw Docling JSON output (application/json Content-Type)
3. Returns valid JSON object (not array or primitive)
4. Returns 403 when user attempts to download another user's document (FR-014)
5. Returns 404 when document doesn't exist

NOTE: This test will FAIL until endpoint implementation is complete.
This is the RAW Docling output, not the processed JSON.

IMPORTANT: Uses app.dependency_overrides instead of @patch decorators to properly
mock FastAPI dependencies. Also mocks requests.get to prevent real JWKS fetches.
"""

import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime
import json
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


class TestDownloadDoclingJsonEndpoint:
    """Contract tests for GET /weaviate/documents/{document_id}/download/docling_json endpoint."""

    def test_download_docling_json_endpoint_exists(self, client):
        """Test /weaviate/documents/{document_id}/download/docling_json endpoint exists.

        This test will FAIL until endpoint is implemented.
        Expected failure: 404 Not Found (endpoint doesn't exist yet)
        """
        response = client.get("/weaviate/documents/f35596eb-618d-4904-822f-a15eacc5ec94/download/docling_json")

        # Should NOT be 404 after implementation
        # Will be 401 (auth required) until we add auth header
        assert response.status_code != 404, "/weaviate/documents/{document_id}/download/docling_json endpoint not found - not implemented yet"

    def test_download_docling_json_requires_authentication(self, client):
        """Test endpoint requires valid authentication token.

        Contract requirement: Must validate Okta JWT token.
        Without token, should return 401 Unauthorized.
        """
        # Call without Authorization header
        response = client.get("/weaviate/documents/f35596eb-618d-4904-822f-a15eacc5ec94/download/docling_json")

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

    def test_download_docling_json_with_invalid_token(self, client):
        """Test endpoint rejects invalid JWT tokens."""
        response = client.get(
            "/weaviate/documents/f35596eb-618d-4904-822f-a15eacc5ec94/download/docling_json",
            headers={"Authorization": "Bearer invalid_malformed_token"}
        )

        assert response.status_code == 401

    def test_download_docling_json_content_type(self, client):
        """Test endpoint returns application/json Content-Type.

        Contract requirement: Response must be application/json.
        This is the raw Docling extraction output in JSON format.
        """
        from main import app
        from src.api.auth import auth, get_db

        # Mock OktaUser
        mock_user = MagicMock()
        mock_user.uid = "00u1abc2def3ghi4jkl"
        mock_user.email = "curator@alliancegenome.org"

        # Mock database user
        db_user = MagicMock()
        db_user.user_id = 123
        db_user.user_id = "00u1abc2def3ghi4jkl"

        # Mock document owned by this user
        db_document = MagicMock()
        db_document.id = 1
        db_document.user_id = 123  # Same as mock_user.user_id
        db_document.filename = "test.pdf"
        db_document.status = "completed"
        db_document.docling_json_path = "/tmp/test_docling.json"

        mock_db_session = MagicMock()
        mock_db_query = MagicMock()

        # Setup query mocks for user and document lookups
        def mock_query_side_effect(model):
            query_mock = MagicMock()
            if hasattr(model, '__tablename__'):
                if model.__tablename__ == 'users':
                    query_mock.filter_by.return_value.one_or_none.return_value = db_user
                elif model.__tablename__ == 'documents':
                    query_mock.filter_by.return_value.first.return_value = db_document
            return query_mock

        mock_db_session.query.side_effect = mock_query_side_effect

        # Override dependencies
        app.dependency_overrides[auth.get_user] = lambda *args, **kwargs: mock_user
        app.dependency_overrides[get_db] = lambda *args, **kwargs: mock_db_session

        try:
            # Call endpoint
            response = client.get(
                "/weaviate/documents/f35596eb-618d-4904-822f-a15eacc5ec94/download/docling_json",
                headers=get_valid_auth_header()
            )

            # Should return 200 with application/json content-type
            assert response.status_code == 200
            assert "application/json" in response.headers["content-type"]
        finally:
            app.dependency_overrides.clear()

    def test_download_docling_json_is_valid_json(self, client):
        """Test endpoint returns valid JSON object.

        Contract requirement: Response schema is 'type: object'.
        Must be a valid JSON object (not array, not primitive).
        """
        from main import app
        from src.api.auth import auth, get_db

        # Mock OktaUser
        mock_user = MagicMock()
        mock_user.uid = "00u1abc2def3ghi4jkl"
        mock_user.email = "curator@alliancegenome.org"

        # Mock database user
        db_user = MagicMock()
        db_user.user_id = 123
        db_user.user_id = "00u1abc2def3ghi4jkl"

        # Mock document owned by this user
        db_document = MagicMock()
        db_document.id = 1
        db_document.user_id = 123
        db_document.filename = "test.pdf"
        db_document.status = "completed"
        db_document.docling_json_path = "/tmp/test_docling.json"

        mock_db_session = MagicMock()

        def mock_query_side_effect(model):
            query_mock = MagicMock()
            if hasattr(model, '__tablename__'):
                if model.__tablename__ == 'users':
                    query_mock.filter_by.return_value.one_or_none.return_value = db_user
                elif model.__tablename__ == 'documents':
                    query_mock.filter_by.return_value.first.return_value = db_document
            return query_mock

        mock_db_session.query.side_effect = mock_query_side_effect

        # Override dependencies
        app.dependency_overrides[auth.get_user] = lambda *args, **kwargs: mock_user
        app.dependency_overrides[get_db] = lambda *args, **kwargs: mock_db_session

        try:
            # Call endpoint
            response = client.get(
                "/weaviate/documents/f35596eb-618d-4904-822f-a15eacc5ec94/download/docling_json",
                headers=get_valid_auth_header()
            )

            # Should return 200
            assert response.status_code == 200

            # Validate JSON response
            data = response.json()

            # Must be a dictionary (JSON object), not list or primitive
            assert isinstance(data, dict), "Response must be a JSON object, not array or primitive"

            # Try to re-serialize to ensure valid JSON
            json_str = json.dumps(data)
            assert json_str is not None
        finally:
            app.dependency_overrides.clear()

    def test_download_docling_json_forbidden_for_other_user(self, client):
        """Test endpoint returns 403 when user attempts to download another user's document.

        Contract requirement (FR-014): "User can only download their own documents"
        When document.user_id != current_user.user_id, return 403 Forbidden.
        """
        from main import app
        from src.api.auth import auth, get_db

        # Mock OktaUser (user_id = 123)
        mock_user = MagicMock()
        mock_user.uid = "00u1abc2def3ghi4jkl"
        mock_user.email = "curator@alliancegenome.org"

        # Mock database user
        db_user = MagicMock()
        db_user.user_id = 123
        db_user.user_id = "00u1abc2def3ghi4jkl"

        # Mock document owned by DIFFERENT user (user_id = 456)
        db_document = MagicMock()
        db_document.id = 1
        db_document.user_id = 456  # Different user!
        db_document.filename = "other_user_document.pdf"
        db_document.status = "completed"
        db_document.docling_json_path = "/tmp/other_docling.json"

        mock_db_session = MagicMock()

        def mock_query_side_effect(model):
            query_mock = MagicMock()
            if hasattr(model, '__tablename__'):
                if model.__tablename__ == 'users':
                    query_mock.filter_by.return_value.one_or_none.return_value = db_user
                elif model.__tablename__ == 'documents':
                    query_mock.filter_by.return_value.first.return_value = db_document
            return query_mock

        mock_db_session.query.side_effect = mock_query_side_effect

        # Override dependencies
        app.dependency_overrides[auth.get_user] = lambda *args, **kwargs: mock_user
        app.dependency_overrides[get_db] = lambda *args, **kwargs: mock_db_session

        try:
            # Call endpoint
            response = client.get(
                "/weaviate/documents/f35596eb-618d-4904-822f-a15eacc5ec94/download/docling_json",
                headers=get_valid_auth_header()
            )

            # Should return 403 Forbidden
            assert response.status_code == 403

            # Check error response
            data = response.json()
            assert "detail" in data
            assert "does not own" in data["detail"].lower() or "forbidden" in data["detail"].lower()
        finally:
            app.dependency_overrides.clear()

    def test_download_docling_json_not_found(self, client):
        """Test endpoint returns 404 when document doesn't exist.

        Contract requirement: Return 404 when document_id doesn't exist in database.
        """
        from main import app
        from src.api.auth import auth, get_db

        # Mock OktaUser
        mock_user = MagicMock()
        mock_user.uid = "00u1abc2def3ghi4jkl"
        mock_user.email = "curator@alliancegenome.org"

        # Mock database user
        db_user = MagicMock()
        db_user.user_id = 123
        db_user.user_id = "00u1abc2def3ghi4jkl"

        # Mock database - document NOT found
        mock_db_session = MagicMock()

        def mock_query_side_effect(model):
            query_mock = MagicMock()
            if hasattr(model, '__tablename__'):
                if model.__tablename__ == 'users':
                    query_mock.filter_by.return_value.one_or_none.return_value = db_user
                elif model.__tablename__ == 'documents':
                    query_mock.filter_by.return_value.first.return_value = None  # Document not found
            return query_mock

        mock_db_session.query.side_effect = mock_query_side_effect

        # Override dependencies
        app.dependency_overrides[auth.get_user] = lambda *args, **kwargs: mock_user
        app.dependency_overrides[get_db] = lambda *args, **kwargs: mock_db_session

        try:
            # Call endpoint with non-existent document_id
            response = client.get(
                "/weaviate/documents/925e41a8-c4e4-484b-92eb-4028d8543623/download/docling_json",
                headers=get_valid_auth_header()
            )

            # Should return 404 Not Found
            assert response.status_code == 404

            # Check error response
            data = response.json()
            assert "detail" in data
            assert "not found" in data["detail"].lower()
        finally:
            app.dependency_overrides.clear()


class TestDownloadDoclingJsonEdgeCases:
    """Edge case tests for /weaviate/documents/{document_id}/download/docling_json endpoint."""

    def test_download_docling_json_without_bearer_prefix(self, client):
        """Test endpoint rejects tokens without 'Bearer' prefix."""
        response = client.get(
            "/weaviate/documents/f35596eb-618d-4904-822f-a15eacc5ec94/download/docling_json",
            headers={"Authorization": "mock_token_no_bearer"}
        )

        assert response.status_code == 401

    def test_download_docling_json_with_empty_authorization_header(self, client):
        """Test endpoint rejects empty Authorization header."""
        response = client.get(
            "/weaviate/documents/f35596eb-618d-4904-822f-a15eacc5ec94/download/docling_json",
            headers={"Authorization": ""}
        )

        assert response.status_code == 401

