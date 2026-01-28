"""Integration test for Cognito login flow and user provisioning.

Task: T048 - Integration test for login flow and user provisioning

Tests the complete workflow:
1. User authenticates via AWS Cognito (mocked)
2. GET /users/me triggers user provisioning
3. User record created in PostgreSQL database
4. Weaviate tenants created for user's document collections

Requirements: FR-005, FR-006 (automatic user provisioning)

CRITICAL: This test MUST PASS to validate user auto-provisioning works correctly.
"""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
from datetime import datetime

from src.models.sql.user import User
from conftest import MockCognitoUser


@pytest.fixture
def test_db():
    """Use actual PostgreSQL database from Docker Compose.

    Uses the main database (get_db) which contains the users table.
    """
    from src.models.sql.database import SessionLocal

    # For integration tests, use the real PostgreSQL database
    # The database is already migrated via Alembic
    db = SessionLocal()

    yield db

    # Cleanup: delete any test users created during test
    db.query(User).filter(
        User.user_id.like("test_%")
    ).delete(synchronize_session=False)
    db.commit()
    db.close()


@pytest.fixture
def mock_cognito_user():
    """Create a mock Cognito user for testing.

    Returns a MockCognitoUser instance with test credentials.
    """
    return MockCognitoUser(
        uid="test_00u1abc2def3ghi4jkl5",
        sub="test_curator@alliancegenome.org",
        groups=[]
    )


@pytest.fixture
def mock_weaviate():
    """Mock Weaviate client for tenant provisioning tests."""
    with patch("src.services.user_service.get_connection") as mock_connection:
        # Create mock client and collections
        mock_client = MagicMock()
        mock_session = MagicMock()

        # Mock DocumentChunk collection
        mock_chunk_collection = MagicMock()
        mock_chunk_tenants = MagicMock()
        mock_chunk_collection.tenants = mock_chunk_tenants

        # Mock PDFDocument collection
        mock_pdf_collection = MagicMock()
        mock_pdf_tenants = MagicMock()
        mock_pdf_collection.tenants = mock_pdf_tenants

        # Configure client to return collections
        mock_client.collections.get.side_effect = lambda name: (
            mock_chunk_collection if name == "DocumentChunk" else mock_pdf_collection
        )

        # Configure session context manager
        mock_session.__enter__.return_value = mock_client
        mock_session.__exit__.return_value = None
        mock_connection.return_value.session.return_value = mock_session

        yield {
            "connection": mock_connection,
            "client": mock_client,
            "chunk_collection": mock_chunk_collection,
            "chunk_tenants": mock_chunk_tenants,
            "pdf_collection": mock_pdf_collection,
            "pdf_tenants": mock_pdf_tenants,
        }


@pytest.fixture
def client(test_db, mock_cognito_user, monkeypatch):
    """Create test client with mocked Cognito authentication.

    This client bypasses Cognito OAuth flow and directly provides a mock user.
    We patch get_auth_dependency at import time to avoid the 503 error.
    """
    # Set required environment variables
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("UNSTRUCTURED_API_URL", "http://test-unstructured")

    import sys
    import os
    from fastapi import Depends

    sys.path.insert(
        0,
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    )

    # Create a simple dependency that returns our mock user
    def get_mock_user():
        return mock_cognito_user

    # Patch get_auth_dependency BEFORE importing the app
    # This ensures the users router registers with our mocked dependency
    with patch("src.api.auth.get_auth_dependency") as mock_get_auth_dep:
        # Make get_auth_dependency() return a Depends that yields our mock user
        mock_get_auth_dep.return_value = Depends(get_mock_user)

        # Now import the app (which will load routes with our patched dependency)
        from main import app
        from src.models.sql.database import get_db

        # Override database dependency to use test database
        def override_get_db():
            yield test_db

        app.dependency_overrides[get_db] = override_get_db

        yield TestClient(app)

        # Clean up dependency overrides
        app.dependency_overrides.clear()


class TestLoginProvisioning:
    """Integration tests for login flow and user provisioning."""

    def test_first_login_creates_user_and_weaviate_tenants(
        self, client, test_db, mock_cognito_user, mock_weaviate
    ):
        """Test that first Cognito login creates user record and Weaviate tenants.

        This is the main integration test for FR-005 and FR-006.

        Flow:
        1. Call GET /users/me with mocked Cognito auth
        2. Verify user record created in users table
        3. Verify Weaviate tenants created in both collections
        """
        # Ensure user doesn't exist before test
        existing_user = test_db.query(User).filter_by(
            user_id=mock_cognito_user.uid
        ).first()
        assert existing_user is None, "Test user should not exist before test"

        # Call GET /users/me (triggers auto-provisioning)
        response = client.get("/users/me")

        # Debug: print actual response if test fails
        if response.status_code != 200:
            print(f"Response status: {response.status_code}")
            print(f"Response body: {response.json()}")

        # Should return user information
        assert response.status_code == 200
        data = response.json()
        assert data["user_id"] == mock_cognito_user.uid
        assert data["email"] == mock_cognito_user.email
        assert data["is_active"] is True
        assert "user_id" in data
        assert "created_at" in data
        assert "last_login" in data

        # Verify user record created in database
        db_user = test_db.query(User).filter_by(user_id=mock_cognito_user.uid).first()
        assert db_user is not None
        assert db_user.user_id == mock_cognito_user.uid
        assert db_user.email == mock_cognito_user.email
        assert db_user.is_active is True
        assert db_user.created_at is not None
        assert db_user.last_login is not None

        # Verify Weaviate tenants created
        # Expected tenant name: "test_00u1abc2def3ghi4jkl5" (hyphens replaced with underscores)
        expected_tenant_name = "test_00u1abc2def3ghi4jkl5"

        # Verify DocumentChunk tenant created
        mock_weaviate["chunk_tenants"].create.assert_called_once()
        chunk_call_args = mock_weaviate["chunk_tenants"].create.call_args
        created_chunk_tenant = chunk_call_args[0][0]  # First positional arg
        assert created_chunk_tenant.name == expected_tenant_name

        # Verify PDFDocument tenant created
        mock_weaviate["pdf_tenants"].create.assert_called_once()
        pdf_call_args = mock_weaviate["pdf_tenants"].create.call_args
        created_pdf_tenant = pdf_call_args[0][0]  # First positional arg
        assert created_pdf_tenant.name == expected_tenant_name

    def test_subsequent_login_updates_last_login(
        self, client, test_db, mock_cognito_user, mock_weaviate
    ):
        """Test that subsequent logins update last_login timestamp.

        Verifies FR-005 behavior for existing users.
        """
        # Create user record (simulate first login)
        # Use timezone-aware datetime to match what the database stores
        from datetime import timezone
        initial_login_time = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        existing_user = User(
            user_id=mock_cognito_user.uid,
            email=mock_cognito_user.email,
            display_name=mock_cognito_user.email,
            created_at=initial_login_time,
            last_login=initial_login_time,
            is_active=True
        )
        test_db.add(existing_user)
        test_db.commit()
        test_db.refresh(existing_user)

        initial_user_id = existing_user.user_id

        # Call GET /users/me (simulates second login)
        response = client.get("/users/me")

        assert response.status_code == 200

        # Verify last_login was updated
        test_db.refresh(existing_user)
        # Convert both to UTC timezone for comparison
        assert existing_user.last_login.replace(tzinfo=timezone.utc) > initial_login_time
        assert existing_user.user_id == initial_user_id  # Same user record
        assert existing_user.user_id == mock_cognito_user.uid

    def test_email_update_from_cognito(
        self, client, test_db, mock_cognito_user, mock_weaviate
    ):
        """Test that email changes in Cognito are synced to user record.

        Verifies that set_global_user_from_cognito() updates changed fields.

        Note: This test creates a user with a different email than the mock_cognito_user,
        then verifies that on next login, the email is updated to match Cognito.
        """
        # Create user with old email (simulating email changed in Cognito)
        old_email = "old_email@example.com"
        existing_user = User(
            user_id=mock_cognito_user.uid,
            email=old_email,
            display_name=old_email,
            created_at=datetime.utcnow(),
            last_login=datetime.utcnow(),
            is_active=True
        )
        test_db.add(existing_user)
        test_db.commit()
        test_db.refresh(existing_user)

        # Call GET /users/me (should update email to match mock_cognito_user.email)
        response = client.get("/users/me")

        assert response.status_code == 200
        data = response.json()
        # Email should now match what's in Cognito (mock_cognito_user.email)
        assert data["email"] == mock_cognito_user.email

        # Verify email updated in database
        test_db.refresh(existing_user)
        assert existing_user.email == mock_cognito_user.email
        assert existing_user.email != old_email

    def test_weaviate_provisioning_failure_does_not_break_user_creation(
        self, client, test_db, mock_cognito_user
    ):
        """Test that Weaviate provisioning failures don't prevent user creation.

        Verifies graceful degradation when Weaviate is unavailable.
        """
        # Mock Weaviate to raise exception
        with patch("src.services.user_service.get_connection") as mock_connection:
            mock_connection.side_effect = Exception("Weaviate connection failed")

            # Call GET /users/me
            response = client.get("/users/me")

            # Should still succeed (user creation is separate from tenant provisioning)
            assert response.status_code == 200

            # Verify user record created despite Weaviate failure
            db_user = test_db.query(User).filter_by(user_id=mock_cognito_user.uid).first()
            assert db_user is not None
            assert db_user.user_id == mock_cognito_user.uid

    def test_tenant_name_format(self, mock_cognito_user):
        """Test that tenant names follow naming convention.

        Verifies that get_tenant_name() replaces hyphens with underscores.
        """
        from src.lib.weaviate_helpers import get_tenant_name

        # Test with Cognito ID containing hyphens
        user_id_with_hyphens = "00u1abc2-def3-ghi4-jkl5"
        tenant_name = get_tenant_name(user_id_with_hyphens)

        # Should replace hyphens with underscores
        assert tenant_name == "00u1abc2_def3_ghi4_jkl5"
        assert "-" not in tenant_name

    def test_user_provisioning_idempotent(
        self, client, test_db, mock_cognito_user, mock_weaviate
    ):
        """Test that multiple calls to provisioning are idempotent.

        Verifies that calling GET /users/me multiple times doesn't create duplicates.
        """
        # First call - creates user
        response1 = client.get("/users/me")
        assert response1.status_code == 200
        user_id_1 = response1.json()["user_id"]

        # Second call - should return same user
        response2 = client.get("/users/me")
        assert response2.status_code == 200
        user_id_2 = response2.json()["user_id"]

        # Should be same user ID
        assert user_id_1 == user_id_2

        # Should only have one user record in database
        user_count = test_db.query(User).filter_by(
            user_id=mock_cognito_user.uid
        ).count()
        assert user_count == 1
