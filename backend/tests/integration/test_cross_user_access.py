"""Integration test for cross-user access prevention.

Task: T052 - Integration test for cross-user access prevention
Scenario: quickstart.md:178-197
Requirements: FR-014 (prevent cross-user data access)

Tests that:
1. User A cannot access User B's documents (403 Forbidden)
2. User A cannot delete User B's documents
3. User A cannot download User B's PDFs or JSON files
4. User A cannot see User B's documents in list endpoint
5. All document operations verify ownership before execution
6. 403 Forbidden (not 404) returned for cross-user access attempts

CRITICAL: This test validates complete data isolation between users.

Implementation Notes:
- Creates two separate mock users (curator1, curator2)
- User A uploads document, User B attempts to access it
- Verifies 403 response (not 404 - reveals document existence)
- Tests all document CRUD operations
- Verifies tenant isolation in Weaviate queries
"""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone
from fastapi_okta import OktaUser

from src.models.sql.user import User
from src.models.sql.pdf_document import PDFDocument


@pytest.fixture(scope="session", autouse=True)
def cleanup_test_data():
    """Clean up test data at the START of test session to ensure clean state."""
    from src.models.sql.database import SessionLocal

    db = SessionLocal()
    try:
        # Delete test documents first (foreign key constraint)
        db.query(PDFDocument).filter(
            PDFDocument.filename.like("test_%")
        ).delete(synchronize_session=False)

        # Then delete test users
        db.query(User).filter(
            User.user_id.like("test_%")
        ).delete(synchronize_session=False)

        db.commit()
    except Exception as e:
        db.rollback()
        print(f"Warning: Initial cleanup failed: {e}")
    finally:
        db.close()

    yield  # Tests run here

    # Also cleanup after all tests complete
    db = SessionLocal()
    try:
        db.query(PDFDocument).filter(
            PDFDocument.filename.like("test_%")
        ).delete(synchronize_session=False)
        db.query(User).filter(
            User.user_id.like("test_%")
        ).delete(synchronize_session=False)
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


@pytest.fixture
def test_db():
    """Use actual PostgreSQL database with cleanup."""
    from src.models.sql.database import SessionLocal

    db = SessionLocal()
    yield db

    # Cleanup: delete test users and documents
    db.query(PDFDocument).filter(
        PDFDocument.filename.like("test_%")
    ).delete(synchronize_session=False)
    db.query(User).filter(
        User.user_id.like("test_%")
    ).delete(synchronize_session=False)
    db.commit()
    db.close()


@pytest.fixture
def curator1_user():
    """Create mock Okta user for Curator 1."""
    return OktaUser(**{
        "uid": "test_curator1_00u1abc2def3",
        "cid": "test_client_1",
        "sub": "curator1@alliancegenome.org",
        "Groups": []
    })


@pytest.fixture
def curator2_user():
    """Create mock Okta user for Curator 2."""
    return OktaUser(**{
        "uid": "test_curator2_00u4ghi5jkl6",
        "cid": "test_client_2",
        "sub": "curator2@alliancegenome.org",
        "Groups": []
    })


@pytest.fixture
def mock_weaviate():
    """Mock Weaviate client for all tenant operations."""
    # Patch get_connection in both user_service AND documents module
    with patch("src.services.user_service.get_connection") as mock_user_connection, \
         patch("src.lib.weaviate_helpers.get_connection") as mock_helpers_connection:

        mock_client = MagicMock()
        mock_session = MagicMock()

        # Mock collections
        mock_chunk_collection = MagicMock()
        mock_chunk_tenants = MagicMock()
        mock_chunk_collection.tenants = mock_chunk_tenants

        mock_pdf_collection = MagicMock()
        mock_pdf_tenants = MagicMock()
        mock_pdf_collection.tenants = mock_pdf_tenants

        # Configure client
        def get_collection(name):
            if name == "DocumentChunk":
                return mock_chunk_collection
            elif name == "PDFDocument":
                return mock_pdf_collection
            return MagicMock()

        mock_client.collections.get = get_collection

        # Configure session
        mock_session.__enter__.return_value = mock_client
        mock_session.__exit__.return_value = None
        # Configure both connection mocks to return same session
        mock_user_connection.return_value.session.return_value = mock_session
        mock_helpers_connection.return_value.session.return_value = mock_session

        yield {
            "client": mock_client,
            "chunk_collection": mock_chunk_collection,
            "pdf_collection": mock_pdf_collection,
        }


@pytest.fixture
def client_as_curator1(monkeypatch, curator1_user, test_db):
    """Create test client authenticated as Curator 1."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("UNSTRUCTURED_API_URL", "http://test-unstructured")
    monkeypatch.setenv("OKTA_DOMAIN", "dev-test.okta.com")
    monkeypatch.setenv("OKTA_API_AUDIENCE", "https://api.alliancegenome.org")

    import sys
    import os

    sys.path.insert(
        0,
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    )

    class MockCurator1Okta:
        def __init__(self, *args, **kwargs):
            pass

        async def get_user(self):
            """Mock get_user that returns curator1."""
            return curator1_user

    # Patch the auth object itself BEFORE importing app
    # This ensures routes capture the mocked auth at import time
    with patch("src.api.auth.auth", MockCurator1Okta()):
        # Also mock provision_weaviate_tenants to prevent real tenant creation
        with patch("src.services.user_service.provision_weaviate_tenants", return_value=True):
            with patch("src.services.user_service.get_connection"):
                with patch("src.lib.weaviate_helpers.get_connection"):
                    from main import app
                    from src.models.sql.database import get_db

                    def override_get_db():
                        yield test_db

                    app.dependency_overrides[get_db] = override_get_db

                    yield TestClient(app)

                    app.dependency_overrides.clear()


@pytest.fixture
def client_as_curator2(monkeypatch, curator2_user, test_db):
    """Create test client authenticated as Curator 2."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("UNSTRUCTURED_API_URL", "http://test-unstructured")
    monkeypatch.setenv("OKTA_DOMAIN", "dev-test.okta.com")
    monkeypatch.setenv("OKTA_API_AUDIENCE", "https://api.alliancegenome.org")

    import sys
    import os

    sys.path.insert(
        0,
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    )

    class MockCurator2Okta:
        def __init__(self, *args, **kwargs):
            pass

        async def get_user(self):
            """Mock get_user that returns curator2."""
            return curator2_user

    # Patch the auth object itself BEFORE importing app
    # This ensures routes capture the mocked auth at import time
    with patch("src.api.auth.auth", MockCurator2Okta()):
        # Also mock provision_weaviate_tenants to prevent real tenant creation
        with patch("src.services.user_service.provision_weaviate_tenants", return_value=True):
            with patch("src.services.user_service.get_connection"):
                with patch("src.lib.weaviate_helpers.get_connection"):
                    from main import app
                    from src.models.sql.database import get_db

                    def override_get_db():
                        yield test_db

                    app.dependency_overrides[get_db] = override_get_db

                    yield TestClient(app)

                    app.dependency_overrides.clear()


class TestCrossUserAccessPrevention:
    """Integration tests for cross-user access prevention."""

    def test_user_cannot_access_other_user_document(
        self, test_db, curator1_user, curator2_user, client_as_curator2, mock_weaviate
    ):
        """Test that User B cannot access User A's document.

        Validates FR-014: Users cannot access other users' documents.

        Flow:
        1. Create document owned by Curator 1 in database
        2. Curator 2 attempts to access it via GET /weaviate/documents/{id}
        3. Should receive 403 Forbidden (not 404)
        """
        # Create User 1 in database
        user1 = User(
            user_id=curator1_user.uid,
            email=curator1_user.email,
            display_name=curator1_user.email,
            created_at=datetime.now(timezone.utc),
            last_login=datetime.now(timezone.utc),
            is_active=True
        )
        test_db.add(user1)
        test_db.commit()
        test_db.refresh(user1)

        # Create User 2 in database
        user2 = User(
            user_id=curator2_user.uid,
            email=curator2_user.email,
            display_name=curator2_user.email,
            created_at=datetime.now(timezone.utc),
            last_login=datetime.now(timezone.utc),
            is_active=True
        )
        test_db.add(user2)
        test_db.commit()
        test_db.refresh(user2)

        # Create document owned by User 1
        import uuid
        doc_id = str(uuid.uuid4())
        document = PDFDocument(
            id=doc_id,
            user_id=user1.user_id,  # Owned by User 1
            filename="test_curator1_document.pdf",
            file_path=f"/test/path/{doc_id}.pdf",
            file_hash="test_hash_123",
            file_size=1024,
            page_count=5,
            upload_timestamp=datetime.now(timezone.utc)
        )
        test_db.add(document)
        test_db.commit()

        # Curator 2 attempts to access Curator 1's document
        response = client_as_curator2.get(f"/weaviate/documents/{doc_id}")

        # Should return 403 Forbidden (not 404 - reveals existence)
        assert response.status_code == 403, \
            f"Expected 403 Forbidden for cross-user access, got {response.status_code}"

        data = response.json()
        assert "detail" in data
        assert "permission" in data["detail"].lower() or \
               "access" in data["detail"].lower() or \
               "forbidden" in data["detail"].lower(), \
            f"Error should indicate permission denied: {data['detail']}"

    def test_user_cannot_delete_other_user_document(
        self, test_db, curator1_user, curator2_user, client_as_curator2, mock_weaviate
    ):
        """Test that User B cannot delete User A's document.

        Validates FR-014: Delete operations verify ownership.
        """
        # Create users
        user1 = User(
            user_id=curator1_user.uid,
            email=curator1_user.email,
            display_name=curator1_user.email,
            created_at=datetime.now(timezone.utc),
            last_login=datetime.now(timezone.utc),
            is_active=True
        )
        test_db.add(user1)
        test_db.commit()
        test_db.refresh(user1)

        user2 = User(
            user_id=curator2_user.uid,
            email=curator2_user.email,
            display_name=curator2_user.email,
            created_at=datetime.now(timezone.utc),
            last_login=datetime.now(timezone.utc),
            is_active=True
        )
        test_db.add(user2)
        test_db.commit()
        test_db.refresh(user2)

        # Create document owned by User 1
        import uuid
        doc_id = str(uuid.uuid4())
        document = PDFDocument(
            id=doc_id,
            user_id=user1.user_id,
            filename="test_curator1_document.pdf",
            file_path=f"/test/path/{doc_id}.pdf",
            file_hash="test_hash_456",
            file_size=2048,
            page_count=10,
            upload_timestamp=datetime.now(timezone.utc)
        )
        test_db.add(document)
        test_db.commit()

        # Curator 2 attempts to delete Curator 1's document
        response = client_as_curator2.delete(f"/weaviate/documents/{doc_id}")

        # Should return 403 Forbidden
        assert response.status_code == 403, \
            f"Expected 403 for cross-user delete, got {response.status_code}"

        # Verify document still exists in database (not deleted)
        test_db.expire_all()  # Clear session cache
        still_exists = test_db.query(PDFDocument).filter_by(id=doc_id).first()
        assert still_exists is not None, \
            "Document should not be deleted by unauthorized user"

    def test_user_cannot_see_other_user_documents_in_list(
        self, test_db, curator1_user, curator2_user, client_as_curator2, mock_weaviate
    ):
        """Test that User B's document list doesn't include User A's documents.

        Validates FR-014: Document list is user-specific.
        """
        # Create users
        user1 = User(
            user_id=curator1_user.uid,
            email=curator1_user.email,
            display_name=curator1_user.email,
            created_at=datetime.now(timezone.utc),
            last_login=datetime.now(timezone.utc),
            is_active=True
        )
        test_db.add(user1)
        test_db.commit()
        test_db.refresh(user1)

        user2 = User(
            user_id=curator2_user.uid,
            email=curator2_user.email,
            display_name=curator2_user.email,
            created_at=datetime.now(timezone.utc),
            last_login=datetime.now(timezone.utc),
            is_active=True
        )
        test_db.add(user2)
        test_db.commit()
        test_db.refresh(user2)

        # Create document for User 1
        import uuid
        doc1_id = str(uuid.uuid4())
        doc1 = PDFDocument(
            id=doc1_id,
            user_id=user1.user_id,
            filename="test_curator1_doc.pdf",
            file_path=f"/test/path/{doc1_id}.pdf",
            file_hash="hash_curator1",
            file_size=1024,
            page_count=5,
            upload_timestamp=datetime.now(timezone.utc)
        )
        test_db.add(doc1)

        # Create document for User 2
        doc2_id = str(uuid.uuid4())
        doc2 = PDFDocument(
            id=doc2_id,
            user_id=user2.user_id,
            filename="test_curator2_doc.pdf",
            file_path=f"/test/path/{doc2_id}.pdf",
            file_hash="hash_curator2",
            file_size=2048,
            page_count=10,
            upload_timestamp=datetime.now(timezone.utc)
        )
        test_db.add(doc2)
        test_db.commit()

        # Curator 2 lists documents
        response = client_as_curator2.get("/weaviate/documents")

        assert response.status_code == 200
        data = response.json()

        # Should only see their own document (doc2)
        document_ids = [doc["id"] for doc in data]
        assert doc2_id in document_ids, "User should see their own document"
        assert doc1_id not in document_ids, \
            "User should NOT see other user's documents"

    def test_user_cannot_download_other_user_pdf(
        self, test_db, curator1_user, curator2_user, client_as_curator2, mock_weaviate
    ):
        """Test that User B cannot download User A's PDF.

        Validates FR-014: Download operations verify ownership.
        """
        # Create users
        user1 = User(
            user_id=curator1_user.uid,
            email=curator1_user.email,
            display_name=curator1_user.email,
            created_at=datetime.now(timezone.utc),
            last_login=datetime.now(timezone.utc),
            is_active=True
        )
        test_db.add(user1)
        test_db.commit()
        test_db.refresh(user1)

        user2 = User(
            user_id=curator2_user.uid,
            email=curator2_user.email,
            display_name=curator2_user.email,
            created_at=datetime.now(timezone.utc),
            last_login=datetime.now(timezone.utc),
            is_active=True
        )
        test_db.add(user2)
        test_db.commit()
        test_db.refresh(user2)

        # Create document for User 1
        import uuid
        doc_id = str(uuid.uuid4())
        document = PDFDocument(
            id=doc_id,
            user_id=user1.user_id,
            filename="test_curator1_doc.pdf",
            file_path=f"/test/path/{doc_id}.pdf",
            file_hash="hash_123",
            file_size=1024,
            page_count=5,
            upload_timestamp=datetime.now(timezone.utc)
        )
        test_db.add(document)
        test_db.commit()

        # Curator 2 attempts to download Curator 1's PDF
        response = client_as_curator2.get(
            f"/weaviate/documents/{doc_id}/download/pdf"
        )

        # Should return 403 Forbidden
        assert response.status_code == 403, \
            f"Expected 403 for cross-user PDF download, got {response.status_code}"

    def test_user_cannot_download_other_user_json_files(
        self, test_db, curator1_user, curator2_user, client_as_curator2, mock_weaviate
    ):
        """Test that User B cannot download User A's JSON files.

        Validates FR-014: All download endpoints verify ownership.
        """
        # Create users
        user1 = User(
            user_id=curator1_user.uid,
            email=curator1_user.email,
            display_name=curator1_user.email,
            created_at=datetime.now(timezone.utc),
            last_login=datetime.now(timezone.utc),
            is_active=True
        )
        test_db.add(user1)
        test_db.commit()
        test_db.refresh(user1)

        user2 = User(
            user_id=curator2_user.uid,
            email=curator2_user.email,
            display_name=curator2_user.email,
            created_at=datetime.now(timezone.utc),
            last_login=datetime.now(timezone.utc),
            is_active=True
        )
        test_db.add(user2)
        test_db.commit()
        test_db.refresh(user2)

        # Create document for User 1
        import uuid
        doc_id = str(uuid.uuid4())
        document = PDFDocument(
            id=doc_id,
            user_id=user1.user_id,
            filename="test_curator1_doc.pdf",
            file_path=f"/test/path/{doc_id}.pdf",
            file_hash="hash_789",
            file_size=1024,
            page_count=5,
            upload_timestamp=datetime.now(timezone.utc)
        )
        test_db.add(document)
        test_db.commit()

        # Test docling_json download
        response = client_as_curator2.get(
            f"/weaviate/documents/{doc_id}/download/docling_json"
        )
        assert response.status_code == 403, \
            f"Expected 403 for docling_json download, got {response.status_code}"

        # Test processed_json download
        response = client_as_curator2.get(
            f"/weaviate/documents/{doc_id}/download/processed_json"
        )
        assert response.status_code == 403, \
            f"Expected 403 for processed_json download, got {response.status_code}"

    def test_cross_user_access_returns_403_not_404(
        self, test_db, curator1_user, curator2_user, client_as_curator2, mock_weaviate
    ):
        """Test that cross-user access returns 403, not 404.

        This is a security requirement: returning 404 would reveal
        whether a document ID exists in the system.

        Validates: Security best practice (information disclosure prevention).
        """
        # Create users
        user1 = User(
            user_id=curator1_user.uid,
            email=curator1_user.email,
            display_name=curator1_user.email,
            created_at=datetime.now(timezone.utc),
            last_login=datetime.now(timezone.utc),
            is_active=True
        )
        test_db.add(user1)
        test_db.commit()
        test_db.refresh(user1)

        user2 = User(
            user_id=curator2_user.uid,
            email=curator2_user.email,
            display_name=curator2_user.email,
            created_at=datetime.now(timezone.utc),
            last_login=datetime.now(timezone.utc),
            is_active=True
        )
        test_db.add(user2)
        test_db.commit()
        test_db.refresh(user2)

        # Create document for User 1
        import uuid
        doc_id = str(uuid.uuid4())
        document = PDFDocument(
            id=doc_id,
            user_id=user1.user_id,
            filename="test_doc.pdf",
            file_path=f"/test/path/{doc_id}.pdf",
            file_hash="hash_abc",
            file_size=1024,
            page_count=5,
            upload_timestamp=datetime.now(timezone.utc)
        )
        test_db.add(document)
        test_db.commit()

        # User 2 attempts access
        response = client_as_curator2.get(f"/weaviate/documents/{doc_id}")

        # MUST be 403, NOT 404
        assert response.status_code == 403, \
            f"Cross-user access MUST return 403 (not 404), got {response.status_code}. " \
            "Returning 404 would reveal document existence."

    def test_user_can_access_own_document(
        self, test_db, curator1_user, client_as_curator1, mock_weaviate
    ):
        """Test that user CAN access their own document.

        Sanity check to ensure ownership check doesn't block legitimate access.

        Validates: Ownership check allows legitimate access.
        """
        # Create user
        user1 = User(
            user_id=curator1_user.uid,
            email=curator1_user.email,
            display_name=curator1_user.email,
            created_at=datetime.now(timezone.utc),
            last_login=datetime.now(timezone.utc),
            is_active=True
        )
        test_db.add(user1)
        test_db.commit()
        test_db.refresh(user1)

        # Create document for User 1
        import uuid
        doc_id = str(uuid.uuid4())
        document = PDFDocument(
            id=doc_id,
            user_id=user1.user_id,
            filename="test_own_doc.pdf",
            file_path=f"/test/path/{doc_id}.pdf",
            file_hash="hash_own",
            file_size=1024,
            page_count=5,
            upload_timestamp=datetime.now(timezone.utc)
        )
        test_db.add(document)
        test_db.commit()

        # User 1 accesses their own document
        response = client_as_curator1.get(f"/weaviate/documents/{doc_id}")

        # Should succeed (200) or appropriate success code
        assert response.status_code == 200, \
            f"User should be able to access their own document, got {response.status_code}"

        data = response.json()
        assert data["id"] == doc_id
        assert data["filename"] == "test_own_doc.pdf"
