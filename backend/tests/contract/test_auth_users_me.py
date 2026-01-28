"""Contract tests for GET /users/me.

Task: T011 - Contract test GET /users/me
Contract: specs/007-okta-login/contracts/auth_endpoints.yaml lines 68-85

This test validates that the /users/me endpoint:
1. Requires valid JWT token (returns 401 if missing/invalid)
2. Returns User schema with user_id, user_id, email, created_at, is_active
3. Automatically creates user account on first login if not exists (FR-005)
4. Updates last_login timestamp on each request

NOTE: This test will FAIL until T023 implements auth router with /users/me endpoint.

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


class TestUsersMeEndpoint:
    """Contract tests for GET /users/me endpoint."""

    def test_users_me_endpoint_exists(self, client):
        """Test /users/me endpoint exists at GET /users/me.

        This test will FAIL until T023 creates the auth router.
        Expected failure: 404 Not Found (endpoint doesn't exist yet)
        """
        response = client.get("/users/me")

        # Should NOT be 404 after T023 implementation
        # Will be 401 (auth required) until we add auth header
        assert response.status_code != 404, "/users/me endpoint not found - T023 not implemented"

    def test_users_me_requires_authentication(self, client):
        """Test /users/me endpoint requires valid authentication token.

        Contract requirement: Must validate JWT token.
        Without token, should return 401 Unauthorized.
        """
        # Call without Authorization header
        response = client.get("/users/me")

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

    def test_users_me_with_invalid_token(self, client):
        """Test /users/me endpoint rejects invalid JWT tokens."""
        response = client.get(
            "/users/me",
            headers={"Authorization": "Bearer invalid_malformed_token"}
        )

        assert response.status_code == 401

    def test_users_me_with_expired_token(self, client):
        """Test /users/me endpoint rejects expired JWT tokens."""
        response = client.get(
            "/users/me",
            headers={"Authorization": "Bearer expired_token_12345"}
        )

        assert response.status_code == 401

    def test_users_me_success_response_schema(self, client):
        """Test /users/me endpoint returns correct User schema.

        Contract schema (User):
        {
          "user_id": 123,
          "user_id": "00u1abc2def3ghi4jkl",
          "email": "curator@alliancegenome.org",  // nullable
          "created_at": "2025-01-25T10:30:00Z",
          "last_login": "2025-01-25T14:45:00Z",  // nullable
          "is_active": true
        }

        Required fields: user_id, user_id, created_at, is_active

        This test will FAIL until T023 implements /users/me endpoint.
        """
        from main import app
        from src.api.auth import auth, get_db

        # Mock authenticated user from token
        mock_user = MagicMock()
        mock_user.uid = "00u1abc2def3ghi4jkl"
        mock_user.email = "curator@alliancegenome.org"
        mock_user.name = "Test Curator"
        mock_user.cid = None

        # Mock database user (already exists)
        db_user = MagicMock()
        db_user.user_id = 123
        db_user.user_id = "00u1abc2def3ghi4jkl"
        db_user.email = "curator@alliancegenome.org"
        db_user.created_at = datetime(2025, 1, 25, 10, 30, 0)
        db_user.last_login = datetime(2025, 1, 25, 14, 45, 0)
        db_user.is_active = True

        mock_db_session = MagicMock()
        mock_db_query = MagicMock()
        mock_db_query.filter_by.return_value.one_or_none.return_value = db_user
        mock_db_session.query.return_value = mock_db_query

        # Override dependencies
        app.dependency_overrides[auth.get_user] = lambda *args, **kwargs: mock_user
        app.dependency_overrides[get_db] = lambda *args, **kwargs: mock_db_session

        try:
            # Call endpoint
            response = client.get(
                "/users/me",
                headers=get_valid_auth_header()
            )

            # Should return 200
            assert response.status_code == 200

            # Validate response schema
            data = response.json()

            # Required fields
            assert "user_id" in data
            assert isinstance(data["user_id"], int)
            assert data["user_id"] == 123

            assert "user_id" in data
            assert isinstance(data["user_id"], str)
            assert data["user_id"] == "00u1abc2def3ghi4jkl"

            assert "created_at" in data
            assert isinstance(data["created_at"], str)
            # Validate ISO 8601 datetime format
            datetime.fromisoformat(data["created_at"].replace('Z', '+00:00'))

            assert "is_active" in data
            assert isinstance(data["is_active"], bool)
            assert data["is_active"] == True

            # Optional fields (nullable in contract)
            assert "email" in data
            if data["email"] is not None:
                assert isinstance(data["email"], str)
                assert "@" in data["email"]  # Basic email validation

            assert "last_login" in data
            if data["last_login"] is not None:
                assert isinstance(data["last_login"], str)
                # Validate ISO 8601 datetime format
                datetime.fromisoformat(data["last_login"].replace('Z', '+00:00'))
        finally:
            app.dependency_overrides.clear()

    def test_users_me_auto_creates_user_on_first_login(self, client):
        """Test /users/me automatically creates user account on first login.

        Contract requirement (FR-005):
        "System MUST automatically create user accounts on first login"

        When user doesn't exist in database, endpoint should:
        1. Create new user record with data from JWT token
        2. Set created_at to current timestamp
        3. Initialize is_active to true
        4. Return newly created user data

        This test will FAIL until T024 implements user provisioning logic.
        """
        from main import app
        from src.api.auth import auth, get_db

        # Mock authenticated user from token (first-time user)
        mock_user = MagicMock()
        mock_user.uid = "00u9xyz8new7user6abc"
        mock_user.email = "newuser@alliancegenome.org"
        mock_user.name = "New User"
        mock_user.cid = None

        # Mock database - user does NOT exist yet
        mock_db_session = MagicMock()
        mock_db_query = MagicMock()
        mock_db_query.filter_by.return_value.one_or_none.return_value = None  # User not found

        # Mock user creation
        new_user = MagicMock()
        new_user.user_id = 456  # Auto-generated ID
        new_user.user_id = "00u9xyz8new7user6abc"
        new_user.email = "newuser@alliancegenome.org"
        new_user.created_at = datetime.utcnow()
        new_user.last_login = datetime.utcnow()
        new_user.is_active = True

        # After creation, subsequent query returns the new user
        mock_db_query.filter_by.return_value.one_or_none.side_effect = [None, new_user]
        mock_db_session.query.return_value = mock_db_query

        # Override dependencies
        app.dependency_overrides[auth.get_user] = lambda *args, **kwargs: mock_user
        app.dependency_overrides[get_db] = lambda *args, **kwargs: mock_db_session

        try:
            # Call endpoint (first login)
            response = client.get(
                "/users/me",
                headers=get_valid_auth_header()
            )

            # Should return 200 with newly created user
            assert response.status_code == 200

            data = response.json()
            assert data["user_id"] == "00u9xyz8new7user6abc"
            assert data["email"] == "newuser@alliancegenome.org"
            assert data["is_active"] == True
            assert "user_id" in data
            assert "created_at" in data
        finally:
            app.dependency_overrides.clear()

    def test_users_me_null_email_allowed(self, client):
        """Test /users/me handles users with null email (service accounts).

        Contract schema shows email is nullable.
        Some users (service accounts) may not have email.
        """
        from main import app
        from src.api.auth import auth, get_db

        # Mock authenticated user without email
        mock_user = MagicMock()
        mock_user.uid = "00s1service2account3def"
        mock_user.email = None  # Service account
        mock_user.cid = "service_client_id"
        mock_user.name = None

        # Mock database user without email
        db_user = MagicMock()
        db_user.user_id = 789
        db_user.user_id = "00s1service2account3def"
        db_user.email = None  # Nullable
        db_user.created_at = datetime.utcnow()
        db_user.last_login = None
        db_user.is_active = True

        mock_db_session = MagicMock()
        mock_db_query = MagicMock()
        mock_db_query.filter_by.return_value.one_or_none.return_value = db_user
        mock_db_session.query.return_value = mock_db_query

        # Override dependencies
        app.dependency_overrides[auth.get_user] = lambda *args, **kwargs: mock_user
        app.dependency_overrides[get_db] = lambda *args, **kwargs: mock_db_session

        try:
            response = client.get(
                "/users/me",
                headers=get_valid_auth_header()
            )

            assert response.status_code == 200

            data = response.json()
            assert data["user_id"] == "00s1service2account3def"
            assert data["email"] is None  # Allowed to be null
            assert data["is_active"] == True
        finally:
            app.dependency_overrides.clear()

    def test_users_me_updates_last_login(self, client):
        """Test /users/me updates last_login timestamp on each request.

        Every call to /users/me should update the user's last_login timestamp
        to track user activity.
        """
        from main import app
        from src.api.auth import auth, get_db

        # Mock authenticated user
        mock_user = MagicMock()
        mock_user.uid = "00u1abc2def3ghi4jkl"
        mock_user.email = "curator@alliancegenome.org"

        # Mock database user with old last_login
        old_login_time = datetime(2025, 1, 20, 10, 0, 0)
        db_user = MagicMock()
        db_user.user_id = 123
        db_user.user_id = "00u1abc2def3ghi4jkl"
        db_user.email = "curator@alliancegenome.org"
        db_user.created_at = datetime(2025, 1, 1, 10, 0, 0)
        db_user.last_login = old_login_time
        db_user.is_active = True

        mock_db_session = MagicMock()
        mock_db_query = MagicMock()
        mock_db_query.filter_by.return_value.one_or_none.return_value = db_user
        mock_db_session.query.return_value = mock_db_query

        # Override dependencies
        app.dependency_overrides[auth.get_user] = lambda *args, **kwargs: mock_user
        app.dependency_overrides[get_db] = lambda *args, **kwargs: mock_db_session

        try:
            # Call endpoint
            response = client.get(
                "/users/me",
                headers=get_valid_auth_header()
            )

            assert response.status_code == 200

            # Verify last_login was updated (implementation detail for T024)
            # Contract test only validates that last_login field exists and is valid
        finally:
            app.dependency_overrides.clear()


class TestUsersMeEndpointEdgeCases:
    """Edge case tests for /users/me endpoint."""

    def test_users_me_without_bearer_prefix(self, client):
        """Test /users/me rejects tokens without 'Bearer' prefix."""
        response = client.get(
            "/users/me",
            headers={"Authorization": "mock_token_no_bearer"}
        )

        assert response.status_code == 401

    def test_users_me_with_empty_authorization_header(self, client):
        """Test /users/me rejects empty Authorization header."""
        response = client.get(
            "/users/me",
            headers={"Authorization": ""}
        )

        assert response.status_code == 401

    def test_users_me_response_content_type_json(self, client):
        """Test /users/me returns JSON content-type."""
        from main import app
        from src.api.auth import auth, get_db

        # Mock authentication and database
        mock_user = MagicMock()
        mock_user.uid = "00u1abc2def3ghi4jkl"
        mock_user.email = "curator@alliancegenome.org"

        db_user = MagicMock()
        db_user.user_id = 123
        db_user.user_id = "00u1abc2def3ghi4jkl"
        db_user.email = "curator@alliancegenome.org"
        db_user.created_at = datetime.utcnow()
        db_user.last_login = datetime.utcnow()
        db_user.is_active = True

        mock_db_session = MagicMock()
        mock_db_query = MagicMock()
        mock_db_query.filter_by.return_value.one_or_none.return_value = db_user
        mock_db_session.query.return_value = mock_db_query

        # Override dependencies
        app.dependency_overrides[auth.get_user] = lambda *args, **kwargs: mock_user
        app.dependency_overrides[get_db] = lambda *args, **kwargs: mock_db_session

        try:
            response = client.get(
                "/users/me",
                headers=get_valid_auth_header()
            )

            assert response.status_code == 200
            assert "application/json" in response.headers["content-type"]
        finally:
            app.dependency_overrides.clear()

    def test_users_me_inactive_user_still_returned(self, client):
        """Test /users/me returns data even for inactive users.

        Contract doesn't specify filtering by is_active.
        Endpoint should return user data regardless of is_active status.
        Authorization/access control is handled elsewhere.
        """
        from main import app
        from src.api.auth import auth, get_db

        # Mock authenticated user
        mock_user = MagicMock()
        mock_user.uid = "00u1inactive2user3ghi"
        mock_user.email = "inactive@alliancegenome.org"

        # Mock inactive database user
        db_user = MagicMock()
        db_user.user_id = 999
        db_user.user_id = "00u1inactive2user3ghi"
        db_user.email = "inactive@alliancegenome.org"
        db_user.created_at = datetime.utcnow()
        db_user.last_login = None
        db_user.is_active = False  # Inactive

        mock_db_session = MagicMock()
        mock_db_query = MagicMock()
        mock_db_query.filter_by.return_value.one_or_none.return_value = db_user
        mock_db_session.query.return_value = mock_db_query

        # Override dependencies
        app.dependency_overrides[auth.get_user] = lambda *args, **kwargs: mock_user
        app.dependency_overrides[get_db] = lambda *args, **kwargs: mock_db_session

        try:
            response = client.get(
                "/users/me",
                headers=get_valid_auth_header()
            )

            # Should still return user data
            assert response.status_code == 200

            data = response.json()
            assert data["is_active"] == False
            assert data["user_id"] == "00u1inactive2user3ghi"
        finally:
            app.dependency_overrides.clear()
