"""Contract tests for POST /weaviate/documents/upload.

Task: T013 - Contract test for POST /weaviate/documents/upload
Contract: specs/007-okta-login/contracts/document_endpoints.yaml lines 76-112

This test validates that the /weaviate/documents/upload endpoint:
1. Requires valid Okta JWT token (returns 401 if missing/invalid)
2. Accepts multipart/form-data PDF file uploads
3. Returns 201 with Document schema on success
4. Returns 400 for invalid file types or missing file parameter
5. Creates weaviate_tenant field with correct format (user_id with underscores)

Document schema (required fields):
{
  "document_id": "doc_abc123",
  "user_id": 123,
  "filename": "research_paper.pdf",
  "status": "PENDING|PROCESSING|COMPLETED|FAILED",
  "upload_timestamp": "2025-01-25T10:30:00Z",
  "weaviate_tenant": "00u1abc2_def3_ghi4_jkl5"
}

NOTE: This test will FAIL until document upload endpoint is implemented with Okta auth.

IMPORTANT: Uses app.dependency_overrides instead of @patch decorators to properly
mock FastAPI dependencies. Also mocks requests.get to prevent real JWKS fetches.
"""

import pytest
import io
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


def create_test_pdf_bytes(filename="test.pdf"):
    """Create minimal valid PDF file bytes for testing.

    Creates a simple but valid PDF with text "Test PDF".
    """
    # Minimal valid PDF structure
    pdf_content = b"""%PDF-1.4
1 0 obj
<<
/Type /Catalog
/Pages 2 0 R
>>
endobj
2 0 obj
<<
/Type /Pages
/Kids [3 0 R]
/Count 1
>>
endobj
3 0 obj
<<
/Type /Page
/Parent 2 0 R
/MediaBox [0 0 612 792]
/Contents 4 0 R
/Resources <<
/Font <<
/F1 <<
/Type /Font
/Subtype /Type1
/BaseFont /Helvetica
>>
>>
>>
>>
endobj
4 0 obj
<<
/Length 44
>>
stream
BT
/F1 12 Tf
100 700 Td
(Test PDF) Tj
ET
endstream
endobj
xref
0 5
0000000000 65535 f
0000000009 00000 n
0000000058 00000 n
0000000115 00000 n
0000000317 00000 n
trailer
<<
/Size 5
/Root 1 0 R
>>
startxref
410
%%EOF
"""
    return io.BytesIO(pdf_content)


class TestDocumentsUploadEndpoint:
    """Contract tests for POST /weaviate/documents/upload endpoint."""

    def test_upload_endpoint_exists(self, client):
        """Test /weaviate/documents/upload endpoint exists at POST.

        This test will FAIL until document upload endpoint is created.
        Expected failure: 404 Not Found (endpoint doesn't exist yet)
        """
        response = client.post("/weaviate/documents/upload")

        # Should NOT be 404 after implementation
        # Will be 401 (auth required) or 400 (missing file) until we add proper request
        assert response.status_code != 404, "/weaviate/documents/upload endpoint not found - not implemented yet"

    def test_upload_requires_authentication(self, client):
        """Test upload endpoint requires valid authentication token.

        Contract requirement: Must validate Okta JWT token.
        Without token, should return 401 Unauthorized.
        """
        # Create test PDF file
        pdf_file = create_test_pdf_bytes()

        # Call without Authorization header
        response = client.post(
            "/weaviate/documents/upload",
            files={"file": ("test.pdf", pdf_file, "application/pdf")}
        )

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

    def test_upload_with_invalid_token(self, client):
        """Test upload endpoint rejects invalid JWT tokens."""
        pdf_file = create_test_pdf_bytes()

        response = client.post(
            "/weaviate/documents/upload",
            headers={"Authorization": "Bearer invalid_malformed_token"},
            files={"file": ("test.pdf", pdf_file, "application/pdf")}
        )

        assert response.status_code == 401

    def test_upload_response_schema(self, client):
        """Test upload endpoint returns correct Document schema on success.

        Contract schema (Document) - required fields:
        {
          "document_id": "doc_abc123",
          "user_id": 123,
          "filename": "research_paper.pdf",
          "status": "PENDING|PROCESSING|COMPLETED|FAILED",
          "upload_timestamp": "2025-01-25T10:30:00Z",
          "weaviate_tenant": "00u1abc2_def3_ghi4_jkl5"
        }

        This test will FAIL until upload endpoint is implemented.
        """
        from main import app
        from src.api.auth import auth, get_db

        # Mock OktaUser from token
        mock_user = MagicMock()
        mock_user.uid = "00u1abc2-def3-ghi4-jkl5"  # With hyphens
        mock_user.email = "curator@alliancegenome.org"
        mock_user.name = "Test Curator"
        mock_user.cid = None

        # Mock database user
        db_user = MagicMock()
        db_user.user_id = 123
        db_user.user_id = "00u1abc2-def3-ghi4-jkl5"
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
            # Create test PDF
            pdf_file = create_test_pdf_bytes()

            # Upload file
            response = client.post(
                "/weaviate/documents/upload",
                headers=get_valid_auth_header(),
                files={"file": ("research_paper.pdf", pdf_file, "application/pdf")}
            )

            # Should return 201 Created
            assert response.status_code == 201

            # Validate response schema
            data = response.json()

            # Required fields
            assert "document_id" in data
            assert isinstance(data["document_id"], str)
            assert len(data["document_id"]) > 0

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

            # Optional fields (nullable in contract)
            if "processing_started_at" in data and data["processing_started_at"] is not None:
                datetime.fromisoformat(data["processing_started_at"].replace('Z', '+00:00'))

            if "processing_completed_at" in data and data["processing_completed_at"] is not None:
                datetime.fromisoformat(data["processing_completed_at"].replace('Z', '+00:00'))

            if "file_size_bytes" in data:
                assert isinstance(data["file_size_bytes"], int)
                assert data["file_size_bytes"] > 0

            if "chunk_count" in data and data["chunk_count"] is not None:
                assert isinstance(data["chunk_count"], int)

            if "error_message" in data and data["error_message"] is not None:
                assert isinstance(data["error_message"], str)
        finally:
            app.dependency_overrides.clear()

    def test_upload_document_has_weaviate_tenant(self, client):
        """Test upload response includes weaviate_tenant field with correct format.

        Contract requirement (FR-016):
        weaviate_tenant field should be derived from user_id with hyphens replaced by underscores.

        Example: user_id "00u1abc2-def3-ghi4-jkl5" -> weaviate_tenant "00u1abc2_def3_ghi4_jkl5"

        This validates multi-tenancy approach where all users share collections but are
        isolated by tenant scope.
        """
        from main import app
        from src.api.auth import auth, get_db

        # Mock OktaUser with hyphenated user_id
        mock_user = MagicMock()
        mock_user.uid = "00u1abc2-def3-ghi4-jkl5"
        mock_user.email = "curator@alliancegenome.org"

        db_user = MagicMock()
        db_user.user_id = 123
        db_user.user_id = "00u1abc2-def3-ghi4-jkl5"
        db_user.email = "curator@alliancegenome.org"
        db_user.created_at = datetime.utcnow()
        db_user.is_active = True

        mock_db_session = MagicMock()
        mock_db_query = MagicMock()
        mock_db_query.filter_by.return_value.one_or_none.return_value = db_user
        mock_db_session.query.return_value = mock_db_query

        app.dependency_overrides[auth.get_user] = lambda *args, **kwargs: mock_user
        app.dependency_overrides[get_db] = lambda *args, **kwargs: mock_db_session

        try:
            pdf_file = create_test_pdf_bytes()

            response = client.post(
                "/weaviate/documents/upload",
                headers=get_valid_auth_header(),
                files={"file": ("test.pdf", pdf_file, "application/pdf")}
            )

            assert response.status_code == 201

            data = response.json()

            # Verify weaviate_tenant field exists
            assert "weaviate_tenant" in data

            # Verify format: hyphens replaced with underscores
            expected_tenant = "00u1abc2_def3_ghi4_jkl5"
            assert data["weaviate_tenant"] == expected_tenant

            # Verify no hyphens in tenant name
            assert "-" not in data["weaviate_tenant"]

            # Verify underscores present (not just stripped)
            assert "_" in data["weaviate_tenant"]
        finally:
            app.dependency_overrides.clear()

    def test_upload_invalid_file_type(self, client):
        """Test upload endpoint rejects non-PDF files.

        Contract requirement: Only PDF files allowed.
        Should return 400 Bad Request for other file types.
        """
        from main import app
        from src.api.auth import auth, get_db

        # Mock authentication
        mock_user = MagicMock()
        mock_user.uid = "00u1abc2-def3-ghi4-jkl5"
        mock_user.email = "curator@alliancegenome.org"

        db_user = MagicMock()
        db_user.user_id = 123
        db_user.user_id = "00u1abc2-def3-ghi4-jkl5"
        db_user.email = "curator@alliancegenome.org"
        db_user.created_at = datetime.utcnow()
        db_user.is_active = True

        mock_db_session = MagicMock()
        mock_db_query = MagicMock()
        mock_db_query.filter_by.return_value.one_or_none.return_value = db_user
        mock_db_session.query.return_value = mock_db_query

        app.dependency_overrides[auth.get_user] = lambda *args, **kwargs: mock_user
        app.dependency_overrides[get_db] = lambda *args, **kwargs: mock_db_session

        try:
            # Create a text file (not PDF)
            text_file = io.BytesIO(b"This is a text file, not a PDF")

            response = client.post(
                "/weaviate/documents/upload",
                headers=get_valid_auth_header(),
                files={"file": ("document.txt", text_file, "text/plain")}
            )

            # Should return 400
            assert response.status_code == 400

            # Verify error message format
            data = response.json()
            assert "detail" in data
            assert isinstance(data["detail"], str)
        finally:
            app.dependency_overrides.clear()

    def test_upload_missing_file(self, client):
        """Test upload endpoint returns 400 when file parameter is missing.

        Contract requirement: 'file' is required in multipart/form-data.
        """
        from main import app
        from src.api.auth import auth, get_db

        # Mock authentication
        mock_user = MagicMock()
        mock_user.uid = "00u1abc2-def3-ghi4-jkl5"
        mock_user.email = "curator@alliancegenome.org"

        db_user = MagicMock()
        db_user.user_id = 123
        db_user.user_id = "00u1abc2-def3-ghi4-jkl5"
        db_user.email = "curator@alliancegenome.org"
        db_user.created_at = datetime.utcnow()
        db_user.is_active = True

        mock_db_session = MagicMock()
        mock_db_query = MagicMock()
        mock_db_query.filter_by.return_value.one_or_none.return_value = db_user
        mock_db_session.query.return_value = mock_db_query

        app.dependency_overrides[auth.get_user] = lambda *args, **kwargs: mock_user
        app.dependency_overrides[get_db] = lambda *args, **kwargs: mock_db_session

        try:
            # Call without file parameter
            response = client.post(
                "/weaviate/documents/upload",
                headers=get_valid_auth_header()
                # No files parameter
            )

            # Should return 400 or 422 (Unprocessable Entity)
            assert response.status_code in [400, 422]

            # Verify error message format
            data = response.json()
            assert "detail" in data
        finally:
            app.dependency_overrides.clear()


class TestDocumentsUploadEndpointEdgeCases:
    """Edge case tests for /weaviate/documents/upload endpoint."""

    def test_upload_without_bearer_prefix(self, client):
        """Test upload rejects tokens without 'Bearer' prefix."""
        pdf_file = create_test_pdf_bytes()

        response = client.post(
            "/weaviate/documents/upload",
            headers={"Authorization": "mock_token_no_bearer"},
            files={"file": ("test.pdf", pdf_file, "application/pdf")}
        )

        assert response.status_code == 401

    def test_upload_with_empty_authorization_header(self, client):
        """Test upload rejects empty Authorization header."""
        pdf_file = create_test_pdf_bytes()

        response = client.post(
            "/weaviate/documents/upload",
            headers={"Authorization": ""},
            files={"file": ("test.pdf", pdf_file, "application/pdf")}
        )

        assert response.status_code == 401

    def test_upload_response_content_type_json(self, client):
        """Test upload endpoint returns JSON content-type."""
        from main import app
        from src.api.auth import auth, get_db

        # Mock authentication and database
        mock_user = MagicMock()
        mock_user.uid = "00u1abc2-def3-ghi4-jkl5"
        mock_user.email = "curator@alliancegenome.org"

        db_user = MagicMock()
        db_user.user_id = 123
        db_user.user_id = "00u1abc2-def3-ghi4-jkl5"
        db_user.email = "curator@alliancegenome.org"
        db_user.created_at = datetime.utcnow()
        db_user.is_active = True

        mock_db_session = MagicMock()
        mock_db_query = MagicMock()
        mock_db_query.filter_by.return_value.one_or_none.return_value = db_user
        mock_db_session.query.return_value = mock_db_query

        app.dependency_overrides[auth.get_user] = lambda *args, **kwargs: mock_user
        app.dependency_overrides[get_db] = lambda *args, **kwargs: mock_db_session

        try:
            pdf_file = create_test_pdf_bytes()

            response = client.post(
                "/weaviate/documents/upload",
                headers=get_valid_auth_header(),
                files={"file": ("test.pdf", pdf_file, "application/pdf")}
            )

            # Should return JSON content type regardless of success/failure
            assert "application/json" in response.headers["content-type"]
        finally:
            app.dependency_overrides.clear()

    def test_upload_empty_pdf_file(self, client):
        """Test upload endpoint handles empty PDF files gracefully."""
        from main import app
        from src.api.auth import auth, get_db

        # Mock authentication
        mock_user = MagicMock()
        mock_user.uid = "00u1abc2-def3-ghi4-jkl5"
        mock_user.email = "curator@alliancegenome.org"

        db_user = MagicMock()
        db_user.user_id = 123
        db_user.user_id = "00u1abc2-def3-ghi4-jkl5"
        db_user.email = "curator@alliancegenome.org"
        db_user.created_at = datetime.utcnow()
        db_user.is_active = True

        mock_db_session = MagicMock()
        mock_db_query = MagicMock()
        mock_db_query.filter_by.return_value.one_or_none.return_value = db_user
        mock_db_session.query.return_value = mock_db_query

        app.dependency_overrides[auth.get_user] = lambda *args, **kwargs: mock_user
        app.dependency_overrides[get_db] = lambda *args, **kwargs: mock_db_session

        try:
            # Create empty file
            empty_file = io.BytesIO(b"")

            response = client.post(
                "/weaviate/documents/upload",
                headers=get_valid_auth_header(),
                files={"file": ("empty.pdf", empty_file, "application/pdf")}
            )

            # Should return 400 (invalid file)
            assert response.status_code == 400

            data = response.json()
            assert "detail" in data
        finally:
            app.dependency_overrides.clear()

    def test_upload_pdf_with_special_characters_in_filename(self, client):
        """Test upload handles filenames with special characters."""
        from main import app
        from src.api.auth import auth, get_db

        # Mock authentication
        mock_user = MagicMock()
        mock_user.uid = "00u1abc2-def3-ghi4-jkl5"
        mock_user.email = "curator@alliancegenome.org"

        db_user = MagicMock()
        db_user.user_id = 123
        db_user.user_id = "00u1abc2-def3-ghi4-jkl5"
        db_user.email = "curator@alliancegenome.org"
        db_user.created_at = datetime.utcnow()
        db_user.is_active = True

        mock_db_session = MagicMock()
        mock_db_query = MagicMock()
        mock_db_query.filter_by.return_value.one_or_none.return_value = db_user
        mock_db_session.query.return_value = mock_db_query

        app.dependency_overrides[auth.get_user] = lambda *args, **kwargs: mock_user
        app.dependency_overrides[get_db] = lambda *args, **kwargs: mock_db_session

        try:
            pdf_file = create_test_pdf_bytes()

            # Filename with spaces, special chars
            response = client.post(
                "/weaviate/documents/upload",
                headers=get_valid_auth_header(),
                files={"file": ("My Research (Draft) #1.pdf", pdf_file, "application/pdf")}
            )

            # Should handle gracefully (201 or 400, not 500)
            assert response.status_code in [201, 400]
        finally:
            app.dependency_overrides.clear()
