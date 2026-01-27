"""Contract tests for GET /weaviate/documents.

Task: T012 - Contract test GET /weaviate/documents
Contract: specs/007-okta-login/contracts/document_endpoints.yaml lines 18-74

This test validates that the /weaviate/documents endpoint:
1. Requires valid Okta JWT token (returns 401 if missing/invalid)
2. Returns list response with documents, total, limit, offset fields
3. Supports pagination via limit and offset query parameters
4. Supports filtering by status query parameter
5. Document schema has required fields: document_id, user_id, filename, status,
   upload_timestamp, weaviate_tenant
6. Only returns documents owned by authenticated user (tenant isolation)

NOTE: This test will FAIL until Phase 4-5 implements the list documents endpoint
with proper Okta authentication and tenant isolation.

IMPORTANT: Uses app.dependency_overrides instead of @patch decorators to properly
mock FastAPI dependencies. Also mocks requests.get to prevent real JWKS fetches.
"""

import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime


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


class TestDocumentsListEndpoint:
    """Contract tests for GET /weaviate/documents endpoint."""

    def test_list_documents_endpoint_exists(self, client):
        """Test /weaviate/documents endpoint exists at GET /weaviate/documents.

        This test will FAIL until Phase 4-5 creates the documents list endpoint.
        Expected failure: 404 Not Found (endpoint doesn't exist yet)
        """
        response = client.get("/weaviate/documents")

        # Should NOT be 404 after implementation
        # Will be 401 (auth required) until we add auth header
        assert response.status_code != 404, "/weaviate/documents endpoint not found - Phase 4-5 not implemented"

    def test_list_documents_requires_authentication(self, client):
        """Test /weaviate/documents endpoint requires valid authentication token.

        Contract requirement (FR-014, FR-015):
        Must validate Okta JWT token. Only authenticated users can list their documents.
        Without token, should return 401 Unauthorized.
        """
        # Call without Authorization header
        response = client.get("/weaviate/documents")

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

    def test_list_documents_with_invalid_token(self, client):
        """Test /weaviate/documents endpoint rejects invalid JWT tokens.

        Invalid tokens should return 401, not 500 or other error codes.
        """
        response = client.get(
            "/weaviate/documents",
            headers={"Authorization": "Bearer invalid_malformed_token"}
        )

        assert response.status_code == 401

    def test_list_documents_response_schema(self, client):
        """Test /weaviate/documents endpoint returns correct list response schema.

        Contract schema (ListDocumentsResponse):
        {
          "documents": [Document, ...],
          "total": 42,
          "limit": 100,
          "offset": 0
        }

        Required fields: documents (array), total, limit, offset

        This test will FAIL until Phase 4-5 implements the list endpoint.
        """
        from main import app
        from src.api.auth import auth, get_db

        # Mock OktaUser from token
        mock_user = MagicMock()
        mock_user.uid = "00u1abc2def3ghi4jkl"
        mock_user.email = "curator@alliancegenome.org"

        # Mock database user
        db_user = MagicMock()
        db_user.user_id = 123
        db_user.user_id = "00u1abc2def3ghi4jkl"
        db_user.weaviate_tenant = "00u1abc2_def3_ghi4_jkl"

        # Mock database session (empty documents list)
        mock_db_session = MagicMock()
        mock_db_query = MagicMock()

        # First query: get user
        user_query = MagicMock()
        user_query.filter_by.return_value.one_or_none.return_value = db_user

        # Second query: get documents (empty list)
        docs_query = MagicMock()
        docs_query.filter_by.return_value.offset.return_value.limit.return_value.all.return_value = []
        docs_query.filter_by.return_value.count.return_value = 0

        mock_db_session.query.side_effect = [user_query, docs_query, docs_query]

        # Override dependencies
        app.dependency_overrides[auth.get_user] = lambda *args, **kwargs: mock_user
        app.dependency_overrides[get_db] = lambda *args, **kwargs: mock_db_session

        try:
            # Call endpoint
            response = client.get(
                "/weaviate/documents",
                headers=get_valid_auth_header()
            )

            # Should return 200
            assert response.status_code == 200

            # Validate response schema
            data = response.json()

            # Required fields
            assert "documents" in data
            assert isinstance(data["documents"], list)

            assert "total" in data
            assert isinstance(data["total"], int)
            assert data["total"] >= 0

            assert "limit" in data
            assert isinstance(data["limit"], int)
            assert data["limit"] > 0

            assert "offset" in data
            assert isinstance(data["offset"], int)
            assert data["offset"] >= 0
        finally:
            app.dependency_overrides.clear()

    def test_list_documents_pagination(self, client):
        """Test /weaviate/documents supports limit and offset query parameters.

        Contract requirements:
        - limit: Maximum number of documents to return (default: 100, min: 1, max: 1000)
        - offset: Number of documents to skip (default: 0, min: 0)

        This test will FAIL until Phase 4-5 implements pagination logic.
        """
        from main import app
        from src.api.auth import auth, get_db

        # Mock OktaUser
        mock_user = MagicMock()
        mock_user.uid = "00u1abc2def3ghi4jkl"

        # Mock database user
        db_user = MagicMock()
        db_user.user_id = 123
        db_user.user_id = "00u1abc2def3ghi4jkl"

        mock_db_session = MagicMock()

        # Mock user query
        user_query = MagicMock()
        user_query.filter_by.return_value.one_or_none.return_value = db_user

        # Mock documents query with pagination
        docs_query = MagicMock()
        docs_query.filter_by.return_value.offset.return_value.limit.return_value.all.return_value = []
        docs_query.filter_by.return_value.count.return_value = 0

        mock_db_session.query.side_effect = [user_query, docs_query, docs_query]

        # Override dependencies
        app.dependency_overrides[auth.get_user] = lambda *args, **kwargs: mock_user
        app.dependency_overrides[get_db] = lambda *args, **kwargs: mock_db_session

        try:
            # Test with limit parameter
            response = client.get(
                "/weaviate/documents?limit=50",
                headers=get_valid_auth_header()
            )

            assert response.status_code == 200
            data = response.json()
            assert data["limit"] == 50

            # Reset mock side effects
            mock_db_session.query.side_effect = [user_query, docs_query, docs_query]

            # Test with offset parameter
            response = client.get(
                "/weaviate/documents?offset=10",
                headers=get_valid_auth_header()
            )

            assert response.status_code == 200
            data = response.json()
            assert data["offset"] == 10

            # Reset mock side effects
            mock_db_session.query.side_effect = [user_query, docs_query, docs_query]

            # Test with both parameters
            response = client.get(
                "/weaviate/documents?limit=25&offset=5",
                headers=get_valid_auth_header()
            )

            assert response.status_code == 200
            data = response.json()
            assert data["limit"] == 25
            assert data["offset"] == 5
        finally:
            app.dependency_overrides.clear()

    def test_list_documents_status_filter(self, client):
        """Test /weaviate/documents supports status query parameter.

        Contract requirements:
        - status: Filter by document processing status (PENDING, PROCESSING, COMPLETED, FAILED)
        - Optional parameter

        This test will FAIL until Phase 4-5 implements status filtering.
        """
        from main import app
        from src.api.auth import auth, get_db

        # Mock OktaUser
        mock_user = MagicMock()
        mock_user.uid = "00u1abc2def3ghi4jkl"

        # Mock database user
        db_user = MagicMock()
        db_user.user_id = 123
        db_user.user_id = "00u1abc2def3ghi4jkl"

        # Mock completed document
        completed_doc = MagicMock()
        completed_doc.document_id = "doc_completed_123"
        completed_doc.user_id = 123
        completed_doc.filename = "completed.pdf"
        completed_doc.status = "COMPLETED"
        completed_doc.upload_timestamp = datetime.utcnow()
        completed_doc.weaviate_tenant = "00u1abc2_def3_ghi4_jkl"

        mock_db_session = MagicMock()

        # Mock user query
        user_query = MagicMock()
        user_query.filter_by.return_value.one_or_none.return_value = db_user

        # Mock documents query filtered by status
        docs_query = MagicMock()
        filter_chain = MagicMock()
        filter_chain.offset.return_value.limit.return_value.all.return_value = [completed_doc]
        filter_chain.count.return_value = 1
        docs_query.filter_by.return_value.filter.return_value = filter_chain

        mock_db_session.query.side_effect = [user_query, docs_query, docs_query]

        # Override dependencies
        app.dependency_overrides[auth.get_user] = lambda *args, **kwargs: mock_user
        app.dependency_overrides[get_db] = lambda *args, **kwargs: mock_db_session

        try:
            # Test filtering by status
            response = client.get(
                "/weaviate/documents?status=COMPLETED",
                headers=get_valid_auth_header()
            )

            assert response.status_code == 200
            data = response.json()

            # Should return documents
            assert "documents" in data
            # If documents returned, verify they match status
            if len(data["documents"]) > 0:
                for doc in data["documents"]:
                    assert doc["status"] == "COMPLETED"
        finally:
            app.dependency_overrides.clear()

    def test_document_schema_has_required_fields(self, client):
        """Test Document objects in response have all required fields.

        Contract schema (Document):
        {
          "document_id": "doc_abc123",
          "user_id": 123,
          "filename": "research_paper.pdf",
          "status": "COMPLETED",  // enum: PENDING, PROCESSING, COMPLETED, FAILED
          "upload_timestamp": "2025-01-25T10:30:00Z",
          "weaviate_tenant": "00u1abc2_def3_ghi4_jkl",
          "processing_started_at": "2025-01-25T10:30:05Z",  // nullable
          "processing_completed_at": "2025-01-25T10:31:00Z",  // nullable
          "file_size_bytes": 1048576,
          "chunk_count": 42  // nullable
        }

        Required fields: document_id, user_id, filename, status, upload_timestamp, weaviate_tenant

        This test will FAIL until Phase 4-5 implements proper Document serialization.
        """
        from main import app
        from src.api.auth import auth, get_db

        # Mock OktaUser
        mock_user = MagicMock()
        mock_user.uid = "00u1abc2def3ghi4jkl"

        # Mock database user
        db_user = MagicMock()
        db_user.user_id = 123
        db_user.user_id = "00u1abc2def3ghi4jkl"

        # Mock document with all fields
        mock_doc = MagicMock()
        mock_doc.document_id = "doc_abc123"
        mock_doc.user_id = 123
        mock_doc.filename = "research_paper.pdf"
        mock_doc.status = "COMPLETED"
        mock_doc.upload_timestamp = datetime(2025, 1, 25, 10, 30, 0)
        mock_doc.weaviate_tenant = "00u1abc2_def3_ghi4_jkl"
        mock_doc.processing_started_at = datetime(2025, 1, 25, 10, 30, 5)
        mock_doc.processing_completed_at = datetime(2025, 1, 25, 10, 31, 0)
        mock_doc.file_size_bytes = 1048576
        mock_doc.chunk_count = 42

        mock_db_session = MagicMock()

        # Mock user query
        user_query = MagicMock()
        user_query.filter_by.return_value.one_or_none.return_value = db_user

        # Mock documents query returning one document
        docs_query = MagicMock()
        docs_query.filter_by.return_value.offset.return_value.limit.return_value.all.return_value = [mock_doc]
        docs_query.filter_by.return_value.count.return_value = 1

        mock_db_session.query.side_effect = [user_query, docs_query, docs_query]

        # Override dependencies
        app.dependency_overrides[auth.get_user] = lambda *args, **kwargs: mock_user
        app.dependency_overrides[get_db] = lambda *args, **kwargs: mock_db_session

        try:
            # Call endpoint
            response = client.get(
                "/weaviate/documents",
                headers=get_valid_auth_header()
            )

            assert response.status_code == 200

            data = response.json()
            assert len(data["documents"]) > 0

            # Validate first document has required fields
            doc = data["documents"][0]

            # Required fields
            assert "document_id" in doc
            assert isinstance(doc["document_id"], str)

            assert "user_id" in doc
            assert isinstance(doc["user_id"], int)

            assert "filename" in doc
            assert isinstance(doc["filename"], str)

            assert "status" in doc
            assert doc["status"] in ["PENDING", "PROCESSING", "COMPLETED", "FAILED"]

            assert "upload_timestamp" in doc
            assert isinstance(doc["upload_timestamp"], str)
            # Validate ISO 8601 datetime format
            datetime.fromisoformat(doc["upload_timestamp"].replace('Z', '+00:00'))

            assert "weaviate_tenant" in doc
            assert isinstance(doc["weaviate_tenant"], str)
            # Tenant should be derived from user_id with underscores
            assert "_" in doc["weaviate_tenant"]

            # Optional fields (nullable)
            assert "processing_started_at" in doc
            if doc["processing_started_at"] is not None:
                assert isinstance(doc["processing_started_at"], str)
                datetime.fromisoformat(doc["processing_started_at"].replace('Z', '+00:00'))

            assert "processing_completed_at" in doc
            if doc["processing_completed_at"] is not None:
                assert isinstance(doc["processing_completed_at"], str)
                datetime.fromisoformat(doc["processing_completed_at"].replace('Z', '+00:00'))

            assert "file_size_bytes" in doc
            if doc["file_size_bytes"] is not None:
                assert isinstance(doc["file_size_bytes"], int)
                assert doc["file_size_bytes"] > 0

            assert "chunk_count" in doc
            if doc["chunk_count"] is not None:
                assert isinstance(doc["chunk_count"], int)
                assert doc["chunk_count"] >= 0
        finally:
            app.dependency_overrides.clear()


class TestDocumentsListEndpointEdgeCases:
    """Edge case tests for /weaviate/documents endpoint."""

    def test_list_documents_without_bearer_prefix(self, client):
        """Test /weaviate/documents rejects tokens without 'Bearer' prefix."""
        response = client.get(
            "/weaviate/documents",
            headers={"Authorization": "mock_token_no_bearer"}
        )

        assert response.status_code == 401

    def test_list_documents_with_empty_authorization_header(self, client):
        """Test /weaviate/documents rejects empty Authorization header."""
        response = client.get(
            "/weaviate/documents",
            headers={"Authorization": ""}
        )

        assert response.status_code == 401

    def test_list_documents_response_content_type_json(self, client):
        """Test /weaviate/documents returns JSON content-type."""
        from main import app
        from src.api.auth import auth, get_db

        # Mock authentication and database
        mock_user = MagicMock()
        mock_user.uid = "00u1abc2def3ghi4jkl"

        db_user = MagicMock()
        db_user.user_id = 123

        mock_db_session = MagicMock()
        user_query = MagicMock()
        user_query.filter_by.return_value.one_or_none.return_value = db_user

        docs_query = MagicMock()
        docs_query.filter_by.return_value.offset.return_value.limit.return_value.all.return_value = []
        docs_query.filter_by.return_value.count.return_value = 0

        mock_db_session.query.side_effect = [user_query, docs_query, docs_query]

        # Override dependencies
        app.dependency_overrides[auth.get_user] = lambda *args, **kwargs: mock_user
        app.dependency_overrides[get_db] = lambda *args, **kwargs: mock_db_session

        try:
            response = client.get(
                "/weaviate/documents",
                headers=get_valid_auth_header()
            )

            assert response.status_code == 200
            assert "application/json" in response.headers["content-type"]
        finally:
            app.dependency_overrides.clear()

    def test_list_documents_default_pagination_values(self, client):
        """Test /weaviate/documents uses correct default pagination values.

        Contract defaults:
        - limit: 100
        - offset: 0
        """
        from main import app
        from src.api.auth import auth, get_db

        # Mock authentication and database
        mock_user = MagicMock()
        mock_user.uid = "00u1abc2def3ghi4jkl"

        db_user = MagicMock()
        db_user.user_id = 123

        mock_db_session = MagicMock()
        user_query = MagicMock()
        user_query.filter_by.return_value.one_or_none.return_value = db_user

        docs_query = MagicMock()
        docs_query.filter_by.return_value.offset.return_value.limit.return_value.all.return_value = []
        docs_query.filter_by.return_value.count.return_value = 0

        mock_db_session.query.side_effect = [user_query, docs_query, docs_query]

        # Override dependencies
        app.dependency_overrides[auth.get_user] = lambda *args, **kwargs: mock_user
        app.dependency_overrides[get_db] = lambda *args, **kwargs: mock_db_session

        try:
            # Call without pagination parameters
            response = client.get(
                "/weaviate/documents",
                headers=get_valid_auth_header()
            )

            assert response.status_code == 200
            data = response.json()

            # Verify defaults
            assert data["limit"] == 100
            assert data["offset"] == 0
        finally:
            app.dependency_overrides.clear()

    def test_list_documents_enforces_limit_constraints(self, client):
        """Test /weaviate/documents enforces limit constraints.

        Contract constraints:
        - minimum: 1
        - maximum: 1000

        Should reject or clamp values outside this range.
        """
        from main import app
        from src.api.auth import auth, get_db

        # Mock authentication and database
        mock_user = MagicMock()
        mock_user.uid = "00u1abc2def3ghi4jkl"

        db_user = MagicMock()
        db_user.user_id = 123

        mock_db_session = MagicMock()
        user_query = MagicMock()
        user_query.filter_by.return_value.one_or_none.return_value = db_user

        docs_query = MagicMock()
        docs_query.filter_by.return_value.offset.return_value.limit.return_value.all.return_value = []
        docs_query.filter_by.return_value.count.return_value = 0

        # Override dependencies
        app.dependency_overrides[auth.get_user] = lambda *args, **kwargs: mock_user
        app.dependency_overrides[get_db] = lambda *args, **kwargs: mock_db_session

        try:
            # Test limit too high (should reject or clamp to 1000)
            mock_db_session.query.side_effect = [user_query, docs_query, docs_query]
            response = client.get(
                "/weaviate/documents?limit=5000",
                headers=get_valid_auth_header()
            )

            # Should either return 422 (validation error) or clamp to 1000
            if response.status_code == 200:
                data = response.json()
                assert data["limit"] <= 1000

            # Test limit too low (should reject or clamp to 1)
            mock_db_session.query.side_effect = [user_query, docs_query, docs_query]
            response = client.get(
                "/weaviate/documents?limit=0",
                headers=get_valid_auth_header()
            )

            # Should either return 422 (validation error) or clamp to 1
            if response.status_code == 200:
                data = response.json()
                assert data["limit"] >= 1
        finally:
            app.dependency_overrides.clear()

    def test_list_documents_only_returns_user_documents(self, client):
        """Test /weaviate/documents only returns documents owned by authenticated user.

        Contract requirement (FR-014, FR-015):
        "Only returns documents owned by the current user"

        This is the tenant isolation requirement - users should never see
        documents uploaded by other users.

        This test will FAIL until Phase 4-5 implements proper tenant filtering.
        """
        from main import app
        from src.api.auth import auth, get_db

        # Mock OktaUser
        mock_user = MagicMock()
        mock_user.uid = "00u1abc2def3ghi4jkl"

        # Mock database user
        db_user = MagicMock()
        db_user.user_id = 123
        db_user.user_id = "00u1abc2def3ghi4jkl"

        # Mock documents - should ONLY include this user's documents
        user_doc = MagicMock()
        user_doc.document_id = "doc_user123_abc"
        user_doc.user_id = 123  # Matches authenticated user
        user_doc.filename = "my_document.pdf"
        user_doc.status = "COMPLETED"
        user_doc.upload_timestamp = datetime.utcnow()
        user_doc.weaviate_tenant = "00u1abc2_def3_ghi4_jkl"

        mock_db_session = MagicMock()

        # Mock user query
        user_query = MagicMock()
        user_query.filter_by.return_value.one_or_none.return_value = db_user

        # Mock documents query - should filter by user_id=123
        docs_query = MagicMock()
        docs_query.filter_by.return_value.offset.return_value.limit.return_value.all.return_value = [user_doc]
        docs_query.filter_by.return_value.count.return_value = 1

        mock_db_session.query.side_effect = [user_query, docs_query, docs_query]

        # Override dependencies
        app.dependency_overrides[auth.get_user] = lambda *args, **kwargs: mock_user
        app.dependency_overrides[get_db] = lambda *args, **kwargs: mock_db_session

        try:
            # Call endpoint
            response = client.get(
                "/weaviate/documents",
                headers=get_valid_auth_header()
            )

            assert response.status_code == 200

            data = response.json()

            # All returned documents must belong to user_id=123
            for doc in data["documents"]:
                assert doc["user_id"] == 123, \
                    f"Document {doc['document_id']} belongs to user {doc['user_id']}, not 123 (tenant isolation violated)"
        finally:
            app.dependency_overrides.clear()

    def test_list_documents_empty_list_when_no_documents(self, client):
        """Test /weaviate/documents returns empty list for users with no documents.

        This is a valid state - new users have zero documents.
        Should return 200 with empty documents array, not 404.
        """
        from main import app
        from src.api.auth import auth, get_db

        # Mock OktaUser
        mock_user = MagicMock()
        mock_user.uid = "00u1abc2def3ghi4jkl"

        # Mock database user with no documents
        db_user = MagicMock()
        db_user.user_id = 123

        mock_db_session = MagicMock()
        user_query = MagicMock()
        user_query.filter_by.return_value.one_or_none.return_value = db_user

        # Empty documents list
        docs_query = MagicMock()
        docs_query.filter_by.return_value.offset.return_value.limit.return_value.all.return_value = []
        docs_query.filter_by.return_value.count.return_value = 0

        mock_db_session.query.side_effect = [user_query, docs_query, docs_query]

        # Override dependencies
        app.dependency_overrides[auth.get_user] = lambda *args, **kwargs: mock_user
        app.dependency_overrides[get_db] = lambda *args, **kwargs: mock_db_session

        try:
            response = client.get(
                "/weaviate/documents",
                headers=get_valid_auth_header()
            )

            # Should return 200, not 404
            assert response.status_code == 200

            data = response.json()
            assert data["documents"] == []
            assert data["total"] == 0
        finally:
            app.dependency_overrides.clear()
