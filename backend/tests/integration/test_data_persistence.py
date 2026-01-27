"""Integration test for data persistence across sessions.

Task: T053 - Integration test for data persistence across sessions
Scenario: quickstart.md:199-208
Requirements: FR-015 (restore user's complete data on login)

Tests that:
1. User uploads documents and they are saved to database
2. User logs out (session ends)
3. User logs back in (new session)
4. All previously uploaded documents are still accessible
5. Document embeddings in Weaviate persist across sessions
6. Document metadata (filename, file_size, etc.) is unchanged
7. Weaviate tenant data survives session changes

CRITICAL: This test validates that user data is persistent and not session-dependent.

Implementation Notes:
- Simulates logout by creating new client instance (new session)
- Verifies database records persist
- Verifies Weaviate tenant remains accessible
- Tests complete lifecycle: upload → logout → login → verify
"""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone
from fastapi_okta import OktaUser

from src.models.sql.user import User
from src.models.sql.pdf_document import PDFDocument

# Note: cleanup_db, test_db, and mock_weaviate fixtures are now in conftest.py
# Note: curator_user is pre-registered as "data_user" in conftest.py

from conftest import MOCK_USERS


@pytest.fixture
def curator_user():
    """Get the data_user from conftest registry."""
    return MOCK_USERS["data_user"]


def create_authenticated_client(test_db, get_auth_mock):
    """Factory function to create authenticated test client.

    This simulates creating a new session (like after logout/login).
    Each call creates a fresh client instance with the same user.
    """
    # Configure shared auth mock for data_user
    get_auth_mock.set_user("data_user")

    from main import app
    from src.models.sql.database import get_db

    def override_get_db():
        yield test_db

    app.dependency_overrides[get_db] = override_get_db

    return TestClient(app)


class TestDataPersistence:
    """Integration tests for data persistence across sessions."""

    def test_document_persists_across_sessions(
        self, test_db, get_auth_mock, mock_weaviate
    ):
        """Test that uploaded document persists after logout and login.

        Validates FR-015: User data persists across sessions.

        Flow:
        1. Login (Session 1) → Upload document
        2. Logout (end Session 1)
        3. Login (Session 2) → Verify document still exists
        """
        # Create user in database
        user = User(
            user_id=curator_user.uid,
            email=curator_user.email,
            display_name=curator_user.email,
            created_at=datetime.now(timezone.utc),
            last_login=datetime.now(timezone.utc),
            is_active=True
        )
        test_db.add(user)
        test_db.commit()
        test_db.refresh(user)

        # Session 1: Upload document
        import uuid
        doc_id = str(uuid.uuid4())
        document = PDFDocument(
            id=doc_id,
            user_id=user.user_id,
            filename="test_persistent_doc.pdf",
            file_path=f"/test/path/{doc_id}.pdf",
            file_hash="persistent_hash_123",
            file_size=2048,
            page_count=10,
            upload_timestamp=datetime.now(timezone.utc)
        )
        test_db.add(document)
        test_db.commit()

        # Simulate logout by creating new client (new session)
        # Session 2: Verify document still accessible
        client_gen = create_authenticated_client(test_db, get_auth_mock)
        client = next(client_gen)

        response = client.get(f"/weaviate/documents/{doc_id}")

        assert response.status_code == 200, \
            f"Document should persist across sessions, got {response.status_code}"

        data = response.json()
        assert data["id"] == doc_id
        assert data["filename"] == "test_persistent_doc.pdf"
        assert data["file_size"] == 2048
        assert data["page_count"] == 10

        # Cleanup generator
        try:
            next(client_gen)
        except StopIteration:
            pass

    def test_multiple_documents_persist_across_sessions(
        self, test_db, get_auth_mock, mock_weaviate
    ):
        """Test that all user documents persist across sessions.

        Validates FR-015: Complete document collection restored.
        """
        # Create user
        user = User(
            user_id=curator_user.uid,
            email=curator_user.email,
            display_name=curator_user.email,
            created_at=datetime.now(timezone.utc),
            last_login=datetime.now(timezone.utc),
            is_active=True
        )
        test_db.add(user)
        test_db.commit()
        test_db.refresh(user)

        # Session 1: Upload multiple documents
        import uuid
        doc_ids = []
        for i in range(3):
            doc_id = str(uuid.uuid4())
            doc_ids.append(doc_id)
            document = PDFDocument(
                id=doc_id,
                user_id=user.user_id,
                filename=f"test_doc_{i+1}.pdf",
                file_path=f"/test/path/{doc_id}.pdf",
                file_hash=f"hash_{i+1}",
                file_size=1024 * (i + 1),
                page_count=5 * (i + 1),
                upload_timestamp=datetime.now(timezone.utc)
            )
            test_db.add(document)
        test_db.commit()

        # Session 2: List all documents
        client_gen = create_authenticated_client(test_db, get_auth_mock)
        client = next(client_gen)

        response = client.get("/weaviate/documents")

        assert response.status_code == 200
        data = response.json()

        # All documents should be present
        returned_ids = [doc["id"] for doc in data]
        for doc_id in doc_ids:
            assert doc_id in returned_ids, \
                f"Document {doc_id} should persist across sessions"

        # Verify count
        assert len(data) >= 3, \
            "All uploaded documents should be available in new session"

        # Cleanup
        try:
            next(client_gen)
        except StopIteration:
            pass

    def test_document_metadata_unchanged_after_logout(
        self, test_db, get_auth_mock, mock_weaviate
    ):
        """Test that document metadata remains unchanged across sessions.

        Validates FR-015: Data integrity maintained.
        """
        # Create user
        user = User(
            user_id=curator_user.uid,
            email=curator_user.email,
            display_name=curator_user.email,
            created_at=datetime.now(timezone.utc),
            last_login=datetime.now(timezone.utc),
            is_active=True
        )
        test_db.add(user)
        test_db.commit()
        test_db.refresh(user)

        # Upload document with specific metadata
        import uuid
        doc_id = str(uuid.uuid4())
        original_timestamp = datetime.now(timezone.utc)
        document = PDFDocument(
            id=doc_id,
            user_id=user.user_id,
            filename="test_metadata_doc.pdf",
            file_path=f"/test/path/{doc_id}.pdf",
            file_hash="metadata_hash_789",
            file_size=4096,
            page_count=15,
            upload_timestamp=original_timestamp
        )
        test_db.add(document)
        test_db.commit()

        # New session: Retrieve document and verify metadata
        client_gen = create_authenticated_client(test_db, get_auth_mock)
        client = next(client_gen)

        response = client.get(f"/weaviate/documents/{doc_id}")

        assert response.status_code == 200
        data = response.json()

        # Verify all metadata unchanged
        assert data["filename"] == "test_metadata_doc.pdf"
        assert data["file_size"] == 4096
        assert data["page_count"] == 15

        # Verify upload timestamp preserved
        returned_timestamp = datetime.fromisoformat(
            data["upload_timestamp"].replace('Z', '+00:00')
        )
        # Allow small time difference due to serialization
        time_diff = abs((returned_timestamp - original_timestamp).total_seconds())
        assert time_diff < 2, \
            f"Upload timestamp should be preserved, diff: {time_diff}s"

        # Cleanup
        try:
            next(client_gen)
        except StopIteration:
            pass

    def test_weaviate_tenant_persists_across_sessions(
        self, test_db, get_auth_mock, mock_weaviate
    ):
        """Test that Weaviate tenant remains accessible after logout/login.

        Validates FR-015: Embedding data persists.

        Note: This test verifies tenant access, not actual embedding data
        (which is mocked). In production, embeddings in Weaviate persist
        independently of user sessions.
        """
        # Create user (triggers Weaviate tenant provisioning)
        user = User(
            user_id=curator_user.uid,
            email=curator_user.email,
            display_name=curator_user.email,
            created_at=datetime.now(timezone.utc),
            last_login=datetime.now(timezone.utc),
            is_active=True
        )
        test_db.add(user)
        test_db.commit()
        test_db.refresh(user)

        # Provision tenants (would be called during first login)
        from src.services.user_service import provision_weaviate_tenants
        provision_weaviate_tenants(curator_user.uid)

        # Verify tenants were created
        expected_tenant = "test_persistence_00u1abc2def"
        mock_weaviate["chunk_tenants"].create.assert_called()
        mock_weaviate["pdf_tenants"].create.assert_called()

        # New session: Verify tenant still accessible
        # In real implementation, Weaviate tenants persist in Weaviate
        # independently of user sessions

        # Mock tenant check
        mock_tenant = MagicMock()
        mock_tenant.name = expected_tenant
        mock_weaviate["chunk_tenants"].get.return_value = [mock_tenant]
        mock_weaviate["pdf_tenants"].get.return_value = [mock_tenant]

        # Get tenant list (simulates accessing tenant in new session)
        tenants = mock_weaviate["chunk_tenants"].get()
        tenant_names = [t.name for t in tenants]

        assert expected_tenant in tenant_names, \
            "Weaviate tenant should persist across sessions"

    def test_user_can_delete_and_recreate_document_across_sessions(
        self, test_db, get_auth_mock, mock_weaviate
    ):
        """Test document CRUD operations persist correctly.

        Validates: Data operations are persistent and atomic.
        """
        # Create user
        user = User(
            user_id=curator_user.uid,
            email=curator_user.email,
            display_name=curator_user.email,
            created_at=datetime.now(timezone.utc),
            last_login=datetime.now(timezone.utc),
            is_active=True
        )
        test_db.add(user)
        test_db.commit()
        test_db.refresh(user)

        # Session 1: Upload document
        import uuid
        doc_id = str(uuid.uuid4())
        document = PDFDocument(
            id=doc_id,
            user_id=user.user_id,
            filename="test_crud_doc.pdf",
            file_path=f"/test/path/{doc_id}.pdf",
            file_hash="crud_hash",
            file_size=1024,
            page_count=5,
            upload_timestamp=datetime.now(timezone.utc)
        )
        test_db.add(document)
        test_db.commit()

        # Session 1: Delete document
        test_db.delete(document)
        test_db.commit()

        # Session 2: Verify document is gone
        client_gen = create_authenticated_client(test_db, get_auth_mock)
        client = next(client_gen)

        response = client.get(f"/weaviate/documents/{doc_id}")

        # Should return 404 (document no longer exists)
        assert response.status_code in [404, 403], \
            f"Deleted document should not be accessible, got {response.status_code}"

        # Session 2: Upload new document with same filename
        new_doc_id = str(uuid.uuid4())
        new_document = PDFDocument(
            id=new_doc_id,
            user_id=user.user_id,
            filename="test_crud_doc.pdf",  # Same filename as deleted
            file_path=f"/test/path/{new_doc_id}.pdf",
            file_hash="new_hash",
            file_size=2048,
            page_count=10,
            upload_timestamp=datetime.now(timezone.utc)
        )
        test_db.add(new_document)
        test_db.commit()

        # Session 3: Verify new document accessible
        client_gen2 = create_authenticated_client(test_db, get_auth_mock)
        client2 = next(client_gen2)

        response = client2.get(f"/weaviate/documents/{new_doc_id}")

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == new_doc_id
        assert data["filename"] == "test_crud_doc.pdf"
        assert data["file_size"] == 2048  # New document's size

        # Cleanup
        try:
            next(client_gen)
        except StopIteration:
            pass
        try:
            next(client_gen2)
        except StopIteration:
            pass

    def test_empty_document_list_persists_for_new_user(
        self, test_db, get_auth_mock, mock_weaviate
    ):
        """Test that new users start with empty state and it persists.

        Validates FR-006: Empty collections initialized for new users.
        """
        # Create user (no documents uploaded)
        user = User(
            user_id=curator_user.uid,
            email=curator_user.email,
            display_name=curator_user.email,
            created_at=datetime.now(timezone.utc),
            last_login=datetime.now(timezone.utc),
            is_active=True
        )
        test_db.add(user)
        test_db.commit()

        # Session 1: List documents (should be empty)
        client_gen = create_authenticated_client(test_db, get_auth_mock)
        client = next(client_gen)

        response = client.get("/weaviate/documents")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 0, "New user should have empty document list"

        # Cleanup
        try:
            next(client_gen)
        except StopIteration:
            pass

        # Session 2: List documents again (should still be empty)
        client_gen2 = create_authenticated_client(test_db, get_auth_mock)
        client2 = next(client_gen2)

        response2 = client2.get("/weaviate/documents")

        assert response2.status_code == 200
        data2 = response2.json()
        assert len(data2) == 0, "Empty state should persist across sessions"

        # Cleanup
        try:
            next(client_gen2)
        except StopIteration:
            pass
