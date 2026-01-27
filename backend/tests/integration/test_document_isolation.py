"""Integration test for user-specific document isolation (T049).

Tests that User A cannot access User B's documents across all document endpoints.
Verifies FR-014: System MUST prevent users from accessing other users' documents,
embeddings, or search results.

Requirements from quickstart.md:141-180 and tasks.md:T049:
- Test that User A cannot access User B's documents (403 Forbidden response)
- Upload document as User A, verify User B cannot see or access it
- Test isolation across all document endpoints: GET list, GET by ID, DELETE, download endpoints
- Verify User A's document list shows only their documents
- Verify User B's document list shows only their documents
- Verify Weaviate multi-tenancy isolation using .with_tenant()

CRITICAL: This test MUST FAIL before T049 implementation!

This test uses the CORRECT dependency override pattern from test_login_provisioning.py:
1. Patch get_auth_dependency BEFORE importing main.py
2. Use a mutable container to hold current user (allows switching between users)
3. Add helper methods to client for user switching
4. NO 503 errors - tests should only see 403 Forbidden or 200 OK responses
"""

import pytest
import io
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
from fastapi_okta import OktaUser


@pytest.fixture
def test_db():
    """Use actual PostgreSQL database from Docker Compose."""
    from src.models.sql.database import SessionLocal
    from src.models.sql.pdf_document import PDFDocument as ViewerPDFDocument

    # For integration tests, use the real PostgreSQL database
    db = SessionLocal()

    yield db

    # Cleanup: delete any test documents created during tests
    db.query(ViewerPDFDocument).filter(
        ViewerPDFDocument.filename.like("test_%")
    ).delete(synchronize_session=False)
    db.query(ViewerPDFDocument).filter(
        ViewerPDFDocument.filename.like("user_%")
    ).delete(synchronize_session=False)
    db.commit()
    db.close()


@pytest.fixture
def mock_weaviate():
    """Mock Weaviate client for document upload tests."""
    # Patch get_connection in both user_service AND documents module
    with patch("src.services.user_service.get_connection") as mock_user_connection, \
         patch("src.lib.weaviate_client.documents.get_connection") as mock_doc_connection:
        # Create mock client and collections
        mock_client = MagicMock()
        mock_session = MagicMock()

        # Mock DocumentChunk collection with tenant support
        mock_chunk_collection = MagicMock()
        mock_chunk_tenants = MagicMock()
        mock_chunk_collection.tenants = mock_chunk_tenants

        # Create tenant-scoped version that returns itself
        mock_chunk_with_tenant = MagicMock()
        mock_chunk_with_tenant.data = MagicMock()  # For .data.insert() calls
        mock_chunk_with_tenant.data.insert = MagicMock(return_value="mock-chunk-uuid")
        mock_chunk_collection.with_tenant = MagicMock(return_value=mock_chunk_with_tenant)

        # Mock PDFDocument collection with tenant support
        mock_pdf_collection = MagicMock()
        mock_pdf_tenants = MagicMock()
        mock_pdf_collection.tenants = mock_pdf_tenants

        # Create tenant-scoped version that returns itself
        mock_pdf_with_tenant = MagicMock()
        mock_pdf_with_tenant.data = MagicMock()  # For .data.insert() calls
        mock_pdf_with_tenant.data.insert = MagicMock(return_value="mock-pdf-uuid")
        mock_pdf_collection.with_tenant = MagicMock(return_value=mock_pdf_with_tenant)

        # Configure client to return base collections (without tenant)
        mock_client.collections.get.side_effect = lambda name: (
            mock_chunk_collection if name == "DocumentChunk" else mock_pdf_collection
        )

        # Configure session context manager
        mock_session.__enter__.return_value = mock_client
        mock_session.__exit__.return_value = None

        # Configure both connection mocks to return same session
        mock_user_connection.return_value.session.return_value = mock_session
        mock_doc_connection.return_value.session.return_value = mock_session

        yield {
            "user_connection": mock_user_connection,
            "doc_connection": mock_doc_connection,
            "client": mock_client,
            "chunk_collection": mock_chunk_collection,
            "chunk_tenants": mock_chunk_tenants,
            "pdf_collection": mock_pdf_collection,
            "pdf_tenants": mock_pdf_tenants,
        }


@pytest.fixture
def client(test_db, monkeypatch):
    """Create test client with mocked authentication for two users.

    This fixture uses the CORRECT pattern:
    1. Patches get_auth_dependency BEFORE importing main.py
    2. Uses a mutable container to hold current user
    3. Adds helper methods to switch between users
    4. No dependency_overrides for Security objects (they don't work)
    """
    # Set required environment variables
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("UNSTRUCTURED_API_URL", "http://test-unstructured")
    monkeypatch.setenv("WEAVIATE_HOST", "weaviate")
    monkeypatch.setenv("WEAVIATE_PORT", "8080")

    # CRITICAL: Set Okta env vars so auth.py thinks Okta is configured
    # This prevents auth = None and get_auth_dependency() returning Depends(raise_503)
    monkeypatch.setenv("OKTA_DOMAIN", "test.okta.com")
    monkeypatch.setenv("OKTA_API_AUDIENCE", "test-audience")

    import sys
    import os
    from fastapi import Security

    sys.path.insert(
        0,
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    )

    # Create two mock users for isolation testing
    user_a = OktaUser(**{
        "uid": "test_user_a_00u1abc",
        "cid": "test_client_a",
        "sub": "curator_a@test.com",
        "Groups": []
    })

    user_b = OktaUser(**{
        "uid": "test_user_b_00u2def",
        "cid": "test_client_b",
        "sub": "curator_b@test.com",
        "Groups": []
    })

    # Store current user in a mutable container so we can switch between users
    current_user = {"user": user_a}

    # Mock Okta class to prevent real Okta initialization
    class MockOkta:
        def __init__(self, *args, **kwargs):
            pass

        async def get_user(self):
            """Mock get_user that returns the current test user."""
            return current_user["user"]

    # CRITICAL: Clear module cache to prevent test contamination
    # Each test needs a fresh app instance with its own auth dependency
    # Clear main and ALL src.* modules to ensure complete isolation
    modules_to_clear = []
    for module_name in list(sys.modules.keys()):
        if module_name == 'main' or module_name.startswith('src.'):
            modules_to_clear.append(module_name)

    for module_name in modules_to_clear:
        del sys.modules[module_name]

    # Patch BOTH Okta class AND get_auth_dependency BEFORE importing the app
    with patch("fastapi_okta.Okta", MockOkta), \
         patch("src.api.auth.get_auth_dependency") as mock_get_auth_dep:

        mock_auth_instance = MockOkta()
        mock_get_auth_dep.return_value = Security(mock_auth_instance.get_user)

        # Now import the app
        from main import app
        from src.models.sql.database import get_db

        # Override database dependency
        def override_get_db():
            yield test_db

        app.dependency_overrides[get_db] = override_get_db

        client = TestClient(app)

        # Add helper methods to switch users
        client.switch_to_user_a = lambda: current_user.update({"user": user_a})
        client.switch_to_user_b = lambda: current_user.update({"user": user_b})
        client.user_a = user_a
        client.user_b = user_b

        yield client

        app.dependency_overrides.clear()


@pytest.fixture
def test_pdf():
    """Create a simple test PDF file in memory."""
    # Create a minimal valid PDF
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
(Test Document) Tj
ET
endstream
endobj
xref
0 5
0000000000 65535 f
0000000009 00000 n
0000000058 00000 n
0000000115 00000 n
0000000214 00000 n
trailer
<<
/Size 5
/Root 1 0 R
>>
startxref
306
%%EOF
"""
    return io.BytesIO(pdf_content)


class TestDocumentIsolation:
    """Integration tests for user-specific document isolation."""

    def test_upload_as_user_a_list_as_user_b_sees_nothing(
        self, client, test_pdf
    ):
        """Test that User B cannot see User A's documents in list endpoint.

        VERIFY: This test should FAIL initially (no isolation implemented yet).
        """
        # Start as User A
        client.switch_to_user_a()

        # Upload document as User A
        test_pdf.seek(0)
        response = client.post(
            "/weaviate/documents/upload",
            files={"file": ("test_doc_a.pdf", test_pdf, "application/pdf")},
        )

        # Debug: print response if upload fails
        if response.status_code != 201:
            print(f"Upload response status: {response.status_code}")
            print(f"Upload response body: {response.json()}")

        assert response.status_code == 201, f"User A should be able to upload document. Got {response.status_code}: {response.json()}"
        data = response.json()
        document_id_a = data["document_id"]

        # Verify User A can see their document
        response = client.get("/weaviate/documents")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] >= 1
        doc_ids = [doc["document_id"] for doc in data["documents"]]
        assert document_id_a in doc_ids, "User A should see their own document"

        # Switch to User B authentication
        client.switch_to_user_b()

        # User B lists documents - should NOT see User A's document
        response = client.get("/weaviate/documents")
        assert response.status_code == 200
        data = response.json()

        # User B should see no documents (or only their own)
        doc_ids = [doc["document_id"] for doc in data["documents"]]
        assert document_id_a not in doc_ids, (
            f"User B should not see User A's document {document_id_a} in list. "
            f"This indicates tenant isolation is not working."
        )

    def test_upload_as_user_a_get_by_id_as_user_b_gets_403(
        self, client, test_pdf
    ):
        """Test that User B gets 403 Forbidden when trying to access User A's document by ID.

        VERIFY: This test should FAIL initially (no ownership checks implemented yet).
        """
        # Upload document as User A
        client.switch_to_user_a()

        test_pdf.seek(0)
        response = client.post(
            "/weaviate/documents/upload",
            files={"file": ("test_doc_a.pdf", test_pdf, "application/pdf")},
        )

        assert response.status_code == 201, f"User A should be able to upload. Got {response.status_code}: {response.json()}"
        document_id_a = response.json()["document_id"]

        # Attempt to access as User B
        client.switch_to_user_b()

        response = client.get(f"/weaviate/documents/{document_id_a}")

        # Should return 403 Forbidden, NOT 404 (per data-model.md:452-486)
        assert response.status_code == 403, (
            f"Expected 403 Forbidden for cross-user document access, got {response.status_code}. "
            f"Response: {response.json() if response.status_code != 403 else 'OK'}"
        )

        # Verify error message
        data = response.json()
        assert "permission" in data.get("detail", "").lower() or "forbidden" in data.get("detail", "").lower()

    def test_upload_as_user_a_delete_as_user_b_gets_403(
        self, client, test_pdf
    ):
        """Test that User B cannot delete User A's document.

        VERIFY: This test should FAIL initially (no ownership checks implemented yet).
        """
        # Upload document as User A
        client.switch_to_user_a()

        test_pdf.seek(0)
        response = client.post(
            "/weaviate/documents/upload",
            files={"file": ("test_doc_a.pdf", test_pdf, "application/pdf")},
        )

        assert response.status_code == 201, f"User A should be able to upload. Got {response.status_code}"
        document_id_a = response.json()["document_id"]

        # Attempt to delete as User B
        client.switch_to_user_b()

        response = client.delete(f"/weaviate/documents/{document_id_a}")

        # Should return 403 Forbidden
        assert response.status_code == 403, (
            f"Expected 403 Forbidden for cross-user document deletion, got {response.status_code}. "
            f"Response: {response.json() if response.status_code != 403 else 'OK'}"
        )

        # Verify document still exists for User A
        client.switch_to_user_a()

        response = client.get(f"/weaviate/documents/{document_id_a}")
        assert response.status_code == 200, (
            "Document should still exist after failed cross-user deletion attempt"
        )

    def test_upload_as_user_a_download_as_user_b_gets_403(
        self, client, test_pdf
    ):
        """Test that User B cannot download User A's document files.

        VERIFY: This test should FAIL initially (no ownership checks implemented yet).
        """
        # Upload document as User A
        client.switch_to_user_a()

        test_pdf.seek(0)
        response = client.post(
            "/weaviate/documents/upload",
            files={"file": ("test_doc_a.pdf", test_pdf, "application/pdf")},
        )

        assert response.status_code == 201, f"User A should be able to upload. Got {response.status_code}"
        document_id_a = response.json()["document_id"]

        # Attempt to download PDF as User B
        client.switch_to_user_b()

        response = client.get(f"/weaviate/documents/{document_id_a}/download/pdf")

        # Should return 403 Forbidden
        assert response.status_code == 403, (
            f"Expected 403 Forbidden for cross-user PDF download, got {response.status_code}"
        )

        # Attempt to get download info as User B
        response = client.get(f"/weaviate/documents/{document_id_a}/download-info")

        # Should return 403 Forbidden
        assert response.status_code == 403, (
            f"Expected 403 Forbidden for cross-user download-info, got {response.status_code}"
        )

        # Attempt to download docling_json as User B
        response = client.get(f"/weaviate/documents/{document_id_a}/download/docling_json")

        # Should return 403 Forbidden
        assert response.status_code == 403, (
            f"Expected 403 Forbidden for cross-user docling_json download, got {response.status_code}"
        )

        # Attempt to download processed_json as User B
        response = client.get(f"/weaviate/documents/{document_id_a}/download/processed_json")

        # Should return 403 Forbidden
        assert response.status_code == 403, (
            f"Expected 403 Forbidden for cross-user processed_json download, got {response.status_code}"
        )

    def test_both_users_upload_see_only_their_own(
        self, client, test_pdf
    ):
        """Test that each user sees only their own documents when both have uploaded.

        VERIFY: This test should FAIL initially (no tenant isolation implemented yet).
        """
        # User A uploads document
        client.switch_to_user_a()

        test_pdf.seek(0)
        response = client.post(
            "/weaviate/documents/upload",
            files={"file": ("user_a_doc.pdf", test_pdf, "application/pdf")},
        )

        assert response.status_code == 201, f"User A should be able to upload. Got {response.status_code}"
        document_id_a = response.json()["document_id"]

        # User B uploads document (need to create new PDF bytes)
        client.switch_to_user_b()

        test_pdf.seek(0)
        response = client.post(
            "/weaviate/documents/upload",
            files={"file": ("user_b_doc.pdf", test_pdf, "application/pdf")},
        )

        assert response.status_code == 201, f"User B should be able to upload. Got {response.status_code}"
        document_id_b = response.json()["document_id"]

        # Verify User A sees only their document
        client.switch_to_user_a()

        response = client.get("/weaviate/documents")
        assert response.status_code == 200
        data = response.json()

        doc_ids = [doc["document_id"] for doc in data["documents"]]
        assert document_id_a in doc_ids, "User A should see their own document"
        assert document_id_b not in doc_ids, "User A should not see User B's document"

        # Verify User B sees only their document
        client.switch_to_user_b()

        response = client.get("/weaviate/documents")
        assert response.status_code == 200
        data = response.json()

        doc_ids = [doc["document_id"] for doc in data["documents"]]
        assert document_id_b in doc_ids, "User B should see their own document"
        assert document_id_a not in doc_ids, "User B should not see User A's document"

    def test_document_status_endpoint_cross_user_403(
        self, client, test_pdf
    ):
        """Test that document status endpoint enforces ownership.

        VERIFY: This test should FAIL initially (no ownership checks implemented yet).
        """
        # Upload document as User A
        client.switch_to_user_a()

        test_pdf.seek(0)
        response = client.post(
            "/weaviate/documents/upload",
            files={"file": ("test_doc_a.pdf", test_pdf, "application/pdf")},
        )

        assert response.status_code == 201, f"User A should be able to upload. Got {response.status_code}"
        document_id_a = response.json()["document_id"]

        # Attempt to check status as User B
        client.switch_to_user_b()

        response = client.get(f"/weaviate/documents/{document_id_a}/status")

        # Should return 403 Forbidden
        assert response.status_code == 403, (
            f"Expected 403 Forbidden for cross-user status check, got {response.status_code}"
        )

    def test_user_a_can_access_own_document_after_upload(
        self, client, test_pdf
    ):
        """Test that User A can successfully access their own document (positive test).

        This verifies that isolation doesn't break normal access patterns.
        """
        # Upload document as User A
        client.switch_to_user_a()

        test_pdf.seek(0)
        response = client.post(
            "/weaviate/documents/upload",
            files={"file": ("test_doc_a.pdf", test_pdf, "application/pdf")},
        )

        assert response.status_code == 201
        document_id_a = response.json()["document_id"]

        # User A should be able to access their own document
        response = client.get(f"/weaviate/documents/{document_id_a}")
        assert response.status_code == 200, "User A should be able to access their own document"

        # User A should be able to get status
        response = client.get(f"/weaviate/documents/{document_id_a}/status")
        assert response.status_code == 200, "User A should be able to check status of their own document"

        # User A should be able to download
        response = client.get(f"/weaviate/documents/{document_id_a}/download/pdf")
        assert response.status_code == 200, "User A should be able to download their own document"

        # User A should be able to delete
        response = client.delete(f"/weaviate/documents/{document_id_a}")
        assert response.status_code == 200, "User A should be able to delete their own document"
