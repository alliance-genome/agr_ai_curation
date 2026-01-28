"""Contract tests for GET /weaviate/documents/{document_id}/download/pdf.

Task: T017 - Contract test GET /weaviate/documents/{document_id}/download/pdf
Contract: specs/007-okta-login/contracts/document_endpoints.yaml lines 219-251

This test validates that the PDF download endpoint:
1. Requires valid JWT token (returns 401 if missing/invalid)
2. Returns application/pdf Content-Type with binary data
3. Enforces user-specific access (returns 403 for other users' documents)
4. Returns 404 for non-existent documents
5. Serves files from user-specific storage paths (FR-014)

NOTE: This test will FAIL until T025 implements document download endpoints.

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


class TestDocumentsDownloadPdfEndpoint:
    """Contract tests for GET /weaviate/documents/{document_id}/download/pdf endpoint."""

    def test_download_pdf_endpoint_exists(self, client):
        """Test PDF download endpoint exists at GET /weaviate/documents/{document_id}/download/pdf.

        This test will FAIL until T025 creates the document download endpoint.
        Expected failure: 404 Not Found (endpoint doesn't exist yet)
        """
        response = client.get("/weaviate/documents/doc_123/download/pdf")

        # Should NOT be 404 after T025 implementation
        # Will be 401 (auth required) until we add auth header
        assert response.status_code != 404, "/weaviate/documents/{document_id}/download/pdf endpoint not found - T025 not implemented"

    def test_download_pdf_requires_authentication(self, client):
        """Test PDF download endpoint requires valid authentication token.

        Contract requirement: Must validate JWT token.
        Without token, should return 401 Unauthorized.
        """
        # Call without Authorization header
        response = client.get("/weaviate/documents/doc_123/download/pdf")

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

    def test_download_pdf_with_invalid_token(self, client):
        """Test PDF download endpoint rejects invalid JWT tokens."""
        response = client.get(
            "/weaviate/documents/doc_123/download/pdf",
            headers={"Authorization": "Bearer invalid_malformed_token"}
        )

        assert response.status_code == 401

    def test_download_pdf_content_type(self, client):
        """Test PDF download endpoint returns application/pdf Content-Type.

        Contract requirement: Response must have Content-Type: application/pdf
        per line 240 of document_endpoints.yaml.

        This test will FAIL until T025 implements PDF download endpoint.
        """
        from main import app
        from src.api.auth import auth, get_db

        # Mock authenticated user from token
        mock_user = MagicMock()
        mock_user.uid = "00u1abc2def3ghi4jkl"
        mock_user.email = "owner@alliancegenome.org"
        mock_user.name = "Document Owner"
        mock_user.cid = None

        # Mock database user
        db_user = MagicMock()
        db_user.user_id = 123
        db_user.user_id = "00u1abc2def3ghi4jkl"
        db_user.email = "owner@alliancegenome.org"
        db_user.created_at = datetime(2025, 1, 25, 10, 30, 0)
        db_user.is_active = True

        # Mock document owned by this user
        db_document = MagicMock()
        db_document.document_id = "doc_123"
        db_document.user_id = 123  # Owned by authenticated user
        db_document.filename = "test.pdf"
        db_document.status = "completed"

        mock_db_session = MagicMock()

        # Setup query mocks for both user and document lookups
        def mock_query(model):
            mock_result = MagicMock()
            if "User" in str(model):
                mock_result.filter_by.return_value.one_or_none.return_value = db_user
            elif "Document" in str(model):
                mock_result.filter_by.return_value.first.return_value = db_document
            return mock_result

        mock_db_session.query.side_effect = mock_query

        # Mock file system - PDF exists
        mock_pdf_content = b"%PDF-1.4 mock pdf content"

        # Override dependencies
        app.dependency_overrides[auth.get_user] = lambda *args, **kwargs: mock_user
        app.dependency_overrides[get_db] = lambda *args, **kwargs: mock_db_session

        try:
            # Mock file reading
            with patch("builtins.open", MagicMock(return_value=MagicMock(__enter__=lambda self: self, __exit__=lambda *args: None, read=lambda: mock_pdf_content))):
                with patch("os.path.exists", return_value=True):
                    # Call endpoint
                    response = client.get(
                        "/weaviate/documents/doc_123/download/pdf",
                        headers=get_valid_auth_header()
                    )

                    # Should return 200
                    assert response.status_code == 200

                    # Validate Content-Type header
                    assert "content-type" in response.headers
                    assert response.headers["content-type"] == "application/pdf"
        finally:
            app.dependency_overrides.clear()

    def test_download_pdf_returns_binary(self, client):
        """Test PDF download endpoint returns binary PDF data.

        Contract requirement: Response body should be binary PDF data
        (schema type: string, format: binary per line 242-243).

        This test will FAIL until T025 implements PDF download endpoint.
        """
        from main import app
        from src.api.auth import auth, get_db

        # Mock authenticated user from token
        mock_user = MagicMock()
        mock_user.uid = "00u1abc2def3ghi4jkl"
        mock_user.email = "owner@alliancegenome.org"

        # Mock database user
        db_user = MagicMock()
        db_user.user_id = 123
        db_user.user_id = "00u1abc2def3ghi4jkl"
        db_user.email = "owner@alliancegenome.org"
        db_user.created_at = datetime.utcnow()
        db_user.is_active = True

        # Mock document owned by this user
        db_document = MagicMock()
        db_document.document_id = "doc_123"
        db_document.user_id = 123  # Owned by authenticated user
        db_document.filename = "test.pdf"
        db_document.status = "completed"

        mock_db_session = MagicMock()

        # Setup query mocks
        def mock_query(model):
            mock_result = MagicMock()
            if "User" in str(model):
                mock_result.filter_by.return_value.one_or_none.return_value = db_user
            elif "Document" in str(model):
                mock_result.filter_by.return_value.first.return_value = db_document
            return mock_result

        mock_db_session.query.side_effect = mock_query

        # Mock PDF file content
        mock_pdf_content = b"%PDF-1.4\n%mock binary pdf content\x00\x01\x02"

        # Override dependencies
        app.dependency_overrides[auth.get_user] = lambda *args, **kwargs: mock_user
        app.dependency_overrides[get_db] = lambda *args, **kwargs: mock_db_session

        try:
            # Mock file reading
            with patch("builtins.open", MagicMock(return_value=MagicMock(__enter__=lambda self: self, __exit__=lambda *args: None, read=lambda: mock_pdf_content))):
                with patch("os.path.exists", return_value=True):
                    # Call endpoint
                    response = client.get(
                        "/weaviate/documents/doc_123/download/pdf",
                        headers=get_valid_auth_header()
                    )

                    # Should return 200
                    assert response.status_code == 200

                    # Validate response is binary
                    assert isinstance(response.content, bytes)

                    # Validate PDF signature (all PDFs start with %PDF)
                    assert response.content.startswith(b"%PDF")

                    # Validate contains expected content
                    assert len(response.content) > 0
        finally:
            app.dependency_overrides.clear()

    def test_download_pdf_forbidden_for_other_user(self, client):
        """Test PDF download endpoint returns 403 for other users' documents.

        Contract requirement: "User can only download their own documents"
        per line 229-230 of document_endpoints.yaml.

        Should return 403 Forbidden when user tries to access another user's document.

        This test will FAIL until T025 implements ownership validation.
        """
        from main import app
        from src.api.auth import auth, get_db

        # Mock authenticated user from token (user ID 123)
        mock_user = MagicMock()
        mock_user.uid = "00u1abc2def3ghi4jkl"
        mock_user.email = "user1@alliancegenome.org"

        # Mock database user (user ID 123)
        db_user = MagicMock()
        db_user.user_id = 123
        db_user.user_id = "00u1abc2def3ghi4jkl"
        db_user.email = "user1@alliancegenome.org"
        db_user.created_at = datetime.utcnow()
        db_user.is_active = True

        # Mock document owned by DIFFERENT user (user ID 456)
        db_document = MagicMock()
        db_document.document_id = "doc_123"
        db_document.user_id = 456  # Owned by DIFFERENT user
        db_document.filename = "test.pdf"
        db_document.status = "completed"

        mock_db_session = MagicMock()

        # Setup query mocks
        def mock_query(model):
            mock_result = MagicMock()
            if "User" in str(model):
                mock_result.filter_by.return_value.one_or_none.return_value = db_user
            elif "Document" in str(model):
                mock_result.filter_by.return_value.first.return_value = db_document
            return mock_result

        mock_db_session.query.side_effect = mock_query

        # Override dependencies
        app.dependency_overrides[auth.get_user] = lambda *args, **kwargs: mock_user
        app.dependency_overrides[get_db] = lambda *args, **kwargs: mock_db_session

        try:
            # Call endpoint - user 123 trying to access user 456's document
            response = client.get(
                "/weaviate/documents/doc_123/download/pdf",
                headers=get_valid_auth_header()
            )

            # Should return 403 Forbidden
            assert response.status_code == 403

            # Validate error response
            data = response.json()
            assert "detail" in data
            assert "does not own" in data["detail"].lower() or "forbidden" in data["detail"].lower()
        finally:
            app.dependency_overrides.clear()

    def test_download_pdf_not_found(self, client):
        """Test PDF download endpoint returns 404 for non-existent documents.

        Contract requirement: Returns 404 when document doesn't exist
        per line 252-253 of document_endpoints.yaml.

        This test will FAIL until T025 implements document lookup and 404 handling.
        """
        from main import app
        from src.api.auth import auth, get_db

        # Mock authenticated user from token
        mock_user = MagicMock()
        mock_user.uid = "00u1abc2def3ghi4jkl"
        mock_user.email = "user@alliancegenome.org"

        # Mock database user
        db_user = MagicMock()
        db_user.user_id = 123
        db_user.user_id = "00u1abc2def3ghi4jkl"
        db_user.email = "user@alliancegenome.org"
        db_user.created_at = datetime.utcnow()
        db_user.is_active = True

        # Mock database - document does NOT exist
        mock_db_session = MagicMock()

        # Setup query mocks
        def mock_query(model):
            mock_result = MagicMock()
            if "User" in str(model):
                mock_result.filter_by.return_value.one_or_none.return_value = db_user
            elif "Document" in str(model):
                mock_result.filter_by.return_value.first.return_value = None  # Document not found
            return mock_result

        mock_db_session.query.side_effect = mock_query

        # Override dependencies
        app.dependency_overrides[auth.get_user] = lambda *args, **kwargs: mock_user
        app.dependency_overrides[get_db] = lambda *args, **kwargs: mock_db_session

        try:
            # Call endpoint with non-existent document ID
            response = client.get(
                "/weaviate/documents/nonexistent_doc_999/download/pdf",
                headers=get_valid_auth_header()
            )

            # Should return 404 Not Found
            assert response.status_code == 404

            # Validate error response
            data = response.json()
            assert "detail" in data
            assert "not found" in data["detail"].lower()
        finally:
            app.dependency_overrides.clear()

    def test_download_pdf_uses_user_specific_path(self, client):
        """Test PDF download endpoint uses user-specific storage paths.

        Contract requirement (FR-014):
        "System MUST isolate user data with tenant-specific paths"

        PDF files should be served from:
        pdf_storage/{user_id}/pdf/{filename}

        This test documents that the implementation should:
        1. Look up authenticated user's user_id
        2. Construct path: pdf_storage/{user_id}/pdf/{filename}
        3. Serve file only if it exists at user-specific path

        This test will FAIL until T025 implements user-specific path resolution.
        """
        from main import app
        from src.api.auth import auth, get_db

        # Mock authenticated user from token
        mock_user = MagicMock()
        mock_user.uid = "00u1abc2def3ghi4jkl"
        mock_user.email = "owner@alliancegenome.org"

        # Mock database user
        db_user = MagicMock()
        db_user.user_id = 123
        db_user.user_id = "00u1abc2def3ghi4jkl"  # This should be in file path
        db_user.email = "owner@alliancegenome.org"
        db_user.created_at = datetime.utcnow()
        db_user.is_active = True

        # Mock document
        db_document = MagicMock()
        db_document.document_id = "doc_123"
        db_document.user_id = 123
        db_document.filename = "research_paper.pdf"
        db_document.status = "completed"

        mock_db_session = MagicMock()

        # Setup query mocks
        def mock_query(model):
            mock_result = MagicMock()
            if "User" in str(model):
                mock_result.filter_by.return_value.one_or_none.return_value = db_user
            elif "Document" in str(model):
                mock_result.filter_by.return_value.first.return_value = db_document
            return mock_result

        mock_db_session.query.side_effect = mock_query

        # Expected user-specific path
        expected_path = "pdf_storage/00u1abc2def3ghi4jkl/pdf/research_paper.pdf"

        # Mock PDF content
        mock_pdf_content = b"%PDF-1.4 test content"

        # Override dependencies
        app.dependency_overrides[auth.get_user] = lambda *args, **kwargs: mock_user
        app.dependency_overrides[get_db] = lambda *args, **kwargs: mock_db_session

        try:
            # Track which path was accessed
            accessed_paths = []

            def mock_open_wrapper(path, *args, **kwargs):
                accessed_paths.append(path)
                return MagicMock(
                    __enter__=lambda self: self,
                    __exit__=lambda *args: None,
                    read=lambda: mock_pdf_content
                )

            # Mock file system
            with patch("builtins.open", side_effect=mock_open_wrapper):
                with patch("os.path.exists", return_value=True):
                    # Call endpoint
                    response = client.get(
                        "/weaviate/documents/doc_123/download/pdf",
                        headers=get_valid_auth_header()
                    )

                    # Should return 200
                    assert response.status_code == 200

                    # Verify user-specific path was used
                    # Implementation should access: pdf_storage/{user_id}/pdf/{filename}
                    assert len(accessed_paths) > 0, "No file paths were accessed"

                    # Path should contain user's user_id for tenant isolation
                    actual_path = accessed_paths[0]
                    assert "00u1abc2def3ghi4jkl" in actual_path, \
                        f"Path should contain user's user_id for isolation. Got: {actual_path}"

                    # Path should contain filename
                    assert "research_paper.pdf" in actual_path, \
                        f"Path should contain document filename. Got: {actual_path}"
        finally:
            app.dependency_overrides.clear()


class TestDocumentsDownloadPdfEdgeCases:
    """Edge case tests for PDF download endpoint."""

    def test_download_pdf_without_bearer_prefix(self, client):
        """Test PDF download endpoint rejects tokens without 'Bearer' prefix."""
        response = client.get(
            "/weaviate/documents/doc_123/download/pdf",
            headers={"Authorization": "mock_token_no_bearer"}
        )

        assert response.status_code == 401

    def test_download_pdf_with_empty_authorization_header(self, client):
        """Test PDF download endpoint rejects empty Authorization header."""
        response = client.get(
            "/weaviate/documents/doc_123/download/pdf",
            headers={"Authorization": ""}
        )

        assert response.status_code == 401

    def test_download_pdf_with_expired_token(self, client):
        """Test PDF download endpoint rejects expired JWT tokens."""
        response = client.get(
            "/weaviate/documents/doc_123/download/pdf",
            headers={"Authorization": "Bearer expired_token_12345"}
        )

        assert response.status_code == 401

    def test_download_pdf_with_special_characters_in_document_id(self, client):
        """Test PDF download endpoint handles document IDs with special characters.

        Document IDs may contain hyphens, underscores, and alphanumeric characters.
        """
        from main import app
        from src.api.auth import auth, get_db

        # Mock authentication
        mock_user = MagicMock()
        mock_user.uid = "00u1abc2def3ghi4jkl"
        mock_user.email = "user@alliancegenome.org"

        db_user = MagicMock()
        db_user.user_id = 123
        db_user.user_id = "00u1abc2def3ghi4jkl"
        db_user.email = "user@alliancegenome.org"
        db_user.created_at = datetime.utcnow()
        db_user.is_active = True

        # Mock document with special characters in ID
        special_doc_id = "doc-123_test-456"
        db_document = MagicMock()
        db_document.document_id = special_doc_id
        db_document.user_id = 123
        db_document.filename = "test.pdf"
        db_document.status = "completed"

        mock_db_session = MagicMock()

        def mock_query(model):
            mock_result = MagicMock()
            if "User" in str(model):
                mock_result.filter_by.return_value.one_or_none.return_value = db_user
            elif "Document" in str(model):
                mock_result.filter_by.return_value.first.return_value = db_document
            return mock_result

        mock_db_session.query.side_effect = mock_query

        app.dependency_overrides[auth.get_user] = lambda *args, **kwargs: mock_user
        app.dependency_overrides[get_db] = lambda *args, **kwargs: mock_db_session

        try:
            with patch("builtins.open", MagicMock(return_value=MagicMock(__enter__=lambda self: self, __exit__=lambda *args: None, read=lambda: b"%PDF-1.4"))):
                with patch("os.path.exists", return_value=True):
                    response = client.get(
                        f"/weaviate/documents/{special_doc_id}/download/pdf",
                        headers=get_valid_auth_header()
                    )

                    # Should handle special characters gracefully
                    assert response.status_code in [200, 404], \
                        "Endpoint should handle document IDs with hyphens/underscores"
        finally:
            app.dependency_overrides.clear()
