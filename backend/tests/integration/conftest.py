"""Shared fixtures for Phase 7 integration tests.

This conftest.py provides unified auth mocking and database cleanup to prevent
test interference when running the full integration suite.

Key fixtures:
- mock_auth_system: Unified auth mock that all tests use (autouse)
- cleanup_db: Database cleanup between tests
- get_mock_user: Helper to get mock users by ID
"""

import os

import pytest
from unittest.mock import patch, MagicMock
from typing import Dict, Any
from dataclasses import dataclass, field

def _default_database_url() -> str:
    """Build integration-test DB URL with host/container-aware defaults."""
    db_user = os.environ.get("TEST_DB_USER", "postgres")
    db_password = os.environ.get("TEST_DB_PASSWORD", "postgres")
    db_name = os.environ.get("TEST_DB_NAME", "ai_curation")
    if os.path.exists("/.dockerenv"):
        db_host = os.environ.get("TEST_DB_HOST", "postgres-test")
        db_port = os.environ.get("TEST_DB_PORT", "5432")
    else:
        db_host = os.environ.get("TEST_DB_HOST", "127.0.0.1")
        db_port = os.environ.get("TEST_DB_PORT", "15434")
    return f"postgresql://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"


# Integration tests run from the host (.venv) should target the Docker-mapped
# Postgres port instead of the in-network hostname, which is not resolvable
# from the host namespace.
if "DATABASE_URL" not in os.environ:
    os.environ["DATABASE_URL"] = _default_database_url()


@dataclass
class MockCognitoUser:
    """Mock user object matching AWS Cognito JWT token claims."""
    uid: str  # User ID (maps to Cognito 'sub' claim)
    sub: str  # Email/subject
    groups: list = field(default_factory=list)  # cognito:groups claim

    def _claims(self) -> Dict[str, Any]:
        """Return canonical claims dict expected by auth/provider code."""
        return {
            "uid": self.uid,
            "sub": self.uid,
            "email": self.sub,
            "name": self.sub,
            "groups": self.groups,
            "cognito:groups": self.groups,
        }

    def __getitem__(self, key: str) -> Any:
        """Allow dict-like access for backward compatibility."""
        return self._claims()[key]

    def get(self, key: str, default: Any = None) -> Any:
        """Allow dict-like .get() for backward compatibility."""
        return self._claims().get(key, default)

    @property
    def email(self) -> str:
        """Compatibility accessor used by legacy tests."""
        return self.sub

    def __iter__(self):
        """Support dict(MockCognitoUser) conversion in service code."""
        return iter(self._claims().items())

    def __len__(self):
        return len(self._claims())

    def keys(self):
        return self._claims().keys()

    def items(self):
        return self._claims().items()


# Registry of mock users for the test suite
MOCK_USERS: Dict[str, MockCognitoUser] = {}


def register_mock_user(user_id: str, email: str, uid: str) -> MockCognitoUser:
    """Register a mock user for the test suite."""
    user = MockCognitoUser(
        uid=uid,
        sub=email,
        groups=[]
    )
    MOCK_USERS[user_id] = user
    return user


# Pre-register common test users
register_mock_user("valid_user", "valid_curator@alliancegenome.org", "test_valid_user_00u1abc2def4")
register_mock_user("expired_user", "expired_curator@alliancegenome.org", "test_expired_user_00u1abc2def3")
register_mock_user("protected_user", "protected_test@alliancegenome.org", "test_protected_user")
register_mock_user("curator1", "curator1@alliancegenome.org", "test_curator1_00u1abc2def3")
register_mock_user("curator2", "curator2@alliancegenome.org", "test_curator2_00u4ghi5jkl6")
register_mock_user("chat1", "chat1@alliancegenome.org", "test_chat1_00u1abc2def")
register_mock_user("chat2", "chat2@alliancegenome.org", "test_chat2_00u4ghi5jkl")
register_mock_user("perf1", "perf1@alliancegenome.org", "test_perf1_00u1abc2def")
register_mock_user("perf2", "perf2@alliancegenome.org", "test_perf2_00u4ghi5jkl")
register_mock_user("data_user", "data@alliancegenome.org", "test_data_user_00u1abc2def")


class UnifiedAuthMock:
    """Unified auth mock that can return different users or raise exceptions."""

    def __init__(self):
        self.current_user_id = "valid_user"  # Default
        self.should_fail = False
        self.fail_message = "Not authenticated"

    def set_user(self, user_id: str):
        """Set which user should be returned by get_user()."""
        self.current_user_id = user_id
        self.should_fail = False

    def set_failure(self, message: str = "Not authenticated"):
        """Make get_user() raise HTTPException."""
        self.should_fail = True
        self.fail_message = message

    async def get_user(self):
        """Return current user or raise exception."""
        if self.should_fail:
            from fastapi import HTTPException
            raise HTTPException(status_code=401, detail=self.fail_message)

        if self.current_user_id not in MOCK_USERS:
            raise ValueError(f"Unknown user_id: {self.current_user_id}")

        return MOCK_USERS[self.current_user_id]


# Global instance that all tests will share
_unified_auth = UnifiedAuthMock()


@pytest.fixture(scope="function", autouse=True)
def mock_auth_system():
    """Unified auth mock system that runs once for the entire test session.

    This fixture:
    1. Patches src.api.auth.auth BEFORE any test imports main
    2. Provides a shared auth mock that tests can reconfigure
    3. Mocks Weaviate and tenant provisioning

    Tests can call get_auth_mock() to reconfigure the mock for their needs.
    """
    import os

    # Set required environment variables (do it directly, not via monkeypatch)
    os.environ["OPENAI_API_KEY"] = "test-key"
    os.environ["UNSTRUCTURED_API_URL"] = "http://test-unstructured"
    # Note: Cognito configuration is handled via environment variables in .env
    # No mock Cognito config needed here - auth is fully mocked

    # Reset mutable auth state between tests.
    _unified_auth.set_user("valid_user")

    # Patch auth BEFORE any imports of main
    with patch("src.api.auth.auth", _unified_auth):
        # Mock Weaviate operations
        with patch("src.services.user_service.provision_weaviate_tenants", return_value=True):
            with patch("src.services.user_service.get_connection"):
                with patch("src.lib.weaviate_helpers.get_connection"):
                    yield _unified_auth


@pytest.fixture
def get_auth_mock():
    """Get the unified auth mock instance for test configuration.

    Usage:
        def test_something(get_auth_mock):
            get_auth_mock.set_user("curator1")
            # Test with curator1 user

            get_auth_mock.set_failure("Token expired")
            # Test with auth failure
    """
    return _unified_auth


@pytest.fixture
def curator1_user():
    """Get the curator1 (chat1) mock user object.

    Returns the MockCognitoUser object for use in tests that need user attributes.
    """
    return MOCK_USERS["chat1"]


@pytest.fixture
def curator2_user():
    """Get the curator2 (chat2) mock user object.

    Returns the MockCognitoUser object for use in tests that need user attributes.
    """
    return MOCK_USERS["chat2"]


@pytest.fixture
def cleanup_db():
    """Clean up test data from database before and after each test.

    This fixture:
    1. Deletes all test users and documents BEFORE test runs
    2. Yields to let test run
    3. Deletes all test users and documents AFTER test completes

    Prevents duplicate key errors and test pollution.
    """
    from src.models.sql.database import SessionLocal
    from src.models.sql.user import User
    from src.models.sql.pdf_document import PDFDocument

    db = SessionLocal()
    try:
        # Cleanup BEFORE test
        db.query(PDFDocument).filter(
            PDFDocument.filename.like("test_%")
        ).delete(synchronize_session=False)
        db.query(User).filter(
            User.auth_sub.like("test_%")
        ).delete(synchronize_session=False)
        db.commit()
    except Exception as e:
        db.rollback()
        print(f"Warning: Pre-test cleanup failed: {e}")
    finally:
        db.close()

    yield  # Test runs here

    # Cleanup AFTER test
    db = SessionLocal()
    try:
        db.query(PDFDocument).filter(
            PDFDocument.filename.like("test_%")
        ).delete(synchronize_session=False)
        db.query(User).filter(
            User.auth_sub.like("test_%")
        ).delete(synchronize_session=False)
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


@pytest.fixture
def test_db(cleanup_db):
    """Get database session with automatic cleanup.

    Depends on cleanup_db to ensure clean state.
    """
    from src.models.sql.database import SessionLocal

    db = SessionLocal()
    yield db
    db.close()


@pytest.fixture
def mock_weaviate():
    """Mock Weaviate client for tenant operations."""
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

        # Mock collection retrieval
        mock_client.collections = MagicMock()
        mock_client.collections.get = MagicMock(side_effect=lambda name: {
            "PdfDocChunk": mock_chunk_collection,
            "PdfDocument": mock_pdf_collection
        }.get(name))

        # Configure connection mocks
        mock_user_connection.return_value.session.return_value = mock_session
        mock_helpers_connection.return_value.session.return_value = mock_session

        yield {
            "client": mock_client,
            "chunk_collection": mock_chunk_collection,
            "pdf_collection": mock_pdf_collection,
        }
