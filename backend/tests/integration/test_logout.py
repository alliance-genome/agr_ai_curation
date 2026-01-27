"""Integration test for end-to-end logout flow.

Task: T050 - Integration test for logout flow
Requirements: specs/007-okta-login/quickstart.md lines 162-180
              specs/007-okta-login/spec.md FR-009, FR-010

Tests the complete logout workflow:
1. Authenticate user with mock Okta credentials
2. Verify authenticated access to protected endpoint (GET /users/me)
3. Call POST /auth/logout to terminate session
4. Verify subsequent requests without re-auth return 401 Unauthorized
5. Verify httpOnly cookies are cleared (where applicable)
6. Verify user must re-authenticate after logout

Pattern follows: backend/tests/integration/test_login_provisioning.py lines 101-144
Contract reference: specs/007-okta-login/contracts/auth_endpoints.yaml lines 46-66

CRITICAL PATTERN:
- Patch get_auth_dependency BEFORE importing main.py
- Use mutable auth_state container with authenticated flag
- Use client.simulate_logout() to set authenticated=False
- Use client.simulate_login() to restore authenticated=True
- NO dependency_overrides manipulation after fixture setup
- NO 503 handling - tests should only see 200 or 401
"""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
from fastapi import Depends, HTTPException
from fastapi_okta import OktaUser

from src.models.sql.user import User


@pytest.fixture
def test_db():
    """Use actual PostgreSQL database from Docker Compose.

    Uses the main database (SessionLocal) which contains the users table.
    """
    from src.models.sql.database import SessionLocal

    # For integration tests, use the real PostgreSQL database
    # The database is already migrated via Alembic
    db = SessionLocal()

    yield db

    # Cleanup: delete any test users created during test
    db.query(User).filter(
        User.user_id.like("test_logout_%")
    ).delete(synchronize_session=False)
    db.commit()
    db.close()


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
def client(test_db, monkeypatch):
    """Create test client with controllable authentication state.

    This fixture implements the CORRECT pattern from test_login_provisioning.py:
    - Creates mutable auth_state container
    - Patches get_auth_dependency BEFORE importing main.py
    - Provides simulate_logout() and simulate_login() helper methods
    - Authentication state controlled via auth_state["authenticated"] flag
    """
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("UNSTRUCTURED_API_URL", "http://test-unstructured")

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

    # Create mock user
    test_user = OktaUser(**{
        "uid": "test_logout_user_00u1abc",
        "cid": "test_client",
        "sub": "test.curator.logout@alliancegenome.org",
        "Groups": []
    })

    # Mutable container to track authentication state
    auth_state = {"authenticated": True, "user": test_user}

    # Mock Okta class to prevent real Okta initialization
    class MockOkta:
        def __init__(self, *args, **kwargs):
            pass

        async def get_user(self):
            """Mock get_user that checks auth_state before returning user."""
            if not auth_state["authenticated"]:
                raise HTTPException(status_code=401, detail="Not authenticated")
            return auth_state["user"]

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

        test_client = TestClient(app)

        # Add helper methods to manage auth state
        test_client.simulate_logout = lambda: auth_state.update({"authenticated": False})
        test_client.simulate_login = lambda: auth_state.update({"authenticated": True})
        test_client.test_user = test_user
        test_client.auth_state = auth_state  # Expose for debugging

        yield test_client

        app.dependency_overrides.clear()


class TestLogoutFlowIntegration:
    """Integration tests for complete logout flow.

    These tests validate the complete logout workflow using the mutable
    auth_state pattern to simulate authentication state changes.
    """

    def test_end_to_end_logout_flow(self, client, test_db):
        """Test complete logout flow: authenticate → access → logout → verify denied.

        Requirements: FR-009, FR-010

        Workflow:
        1. User is authenticated via fixture (auth_state["authenticated"] = True)
        2. Access protected endpoint (GET /users/me) → should succeed (200)
        3. Call POST /auth/logout → should return 200 with status="logged_out"
        4. Call client.simulate_logout() to set auth_state["authenticated"] = False
        5. Access protected endpoint again → should fail with 401
        6. Verify error message indicates authentication required
        """
        # Step 1: Verify authenticated access works
        response = client.get("/users/me")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        user_data = response.json()
        assert user_data["user_id"] == "test_logout_user_00u1abc"
        assert user_data["email"] == "test.curator.logout@alliancegenome.org"

        # Step 2: Call logout endpoint (contract compliance)
        logout_response = client.post("/auth/logout")
        assert logout_response.status_code == 200, f"Expected 200, got {logout_response.status_code}: {logout_response.text}"
        logout_data = logout_response.json()
        assert logout_data["status"] == "logged_out"
        assert "message" in logout_data

        # Step 3: Simulate session termination
        client.simulate_logout()

        # Step 4: Attempt to access protected endpoint without authentication
        unauth_response = client.get("/users/me")
        assert unauth_response.status_code == 401, f"Expected 401 after logout, got {unauth_response.status_code}: {unauth_response.text}"
        error_data = unauth_response.json()
        assert "detail" in error_data
        assert "not authenticated" in error_data["detail"].lower()

    def test_logout_clears_session_and_requires_reauth(self, client, test_db):
        """Test that logout terminates session and subsequent requests fail.

        Requirements: FR-009, FR-010
        """
        # Verify initial access works
        initial_response = client.get("/users/me")
        assert initial_response.status_code == 200, f"Expected 200, got {initial_response.status_code}"

        # Logout (contract compliance)
        logout_response = client.post("/auth/logout")
        assert logout_response.status_code == 200, f"Expected 200, got {logout_response.status_code}"
        assert logout_response.json()["status"] == "logged_out"

        # Simulate session termination
        client.simulate_logout()

        # Verify subsequent requests fail with 401
        response = client.get("/users/me")
        assert response.status_code == 401, f"Expected 401 after logout, got {response.status_code}"

    def test_logout_without_authentication_fails(self, client, test_db):
        """Test that logout endpoint requires authentication.

        Requirements: Contract requirement that POST /auth/logout requires valid token
        """
        # Simulate unauthenticated state
        client.simulate_logout()

        # Call logout WITHOUT authentication
        response = client.post("/auth/logout")

        # Should fail with 401
        assert response.status_code == 401, f"Expected 401, got {response.status_code}: {response.text}"
        data = response.json()
        assert "detail" in data

    def test_logout_response_contract_compliance(self, client):
        """Test that logout response matches contract specification.

        Contract: specs/007-okta-login/contracts/auth_endpoints.yaml lines 46-66

        Validates response schema:
        {
          "status": "logged_out",
          "message": "User session terminated successfully"
        }
        """
        # Call logout (user is authenticated via fixture)
        response = client.post("/auth/logout")

        # Verify status code
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"

        # Verify response schema
        data = response.json()
        assert "status" in data, f"Missing 'status' field in response: {data}"
        assert data["status"] == "logged_out", f"Expected status='logged_out', got {data['status']}"
        assert "message" in data, f"Missing 'message' field in response: {data}"
        assert isinstance(data["message"], str)
        assert len(data["message"]) > 0

        # Verify content-type is JSON
        assert "application/json" in response.headers["content-type"]

    def test_logout_and_reauth_flow(self, client, test_db):
        """Test complete logout and re-authentication cycle.

        Requirements: FR-009, FR-010
        """
        # Step 1: Verify initial access
        response = client.get("/users/me")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"

        # Step 2: Logout
        logout_response = client.post("/auth/logout")
        assert logout_response.status_code == 200, f"Expected 200, got {logout_response.status_code}"

        # Step 3: Simulate session termination
        client.simulate_logout()

        # Verify access denied
        response = client.get("/users/me")
        assert response.status_code == 401, f"Expected 401, got {response.status_code}"

        # Step 4: Re-authenticate (simulates new login)
        client.simulate_login()

        # Verify access restored
        response = client.get("/users/me")
        assert response.status_code == 200, f"Expected 200 after re-auth, got {response.status_code}"
        user_data = response.json()
        assert user_data["user_id"] == "test_logout_user_00u1abc"

    def test_logout_with_database_user_auto_provisioning(self, client, test_db, mock_weaviate):
        """Test logout flow with auto-provisioned database user.

        Requirements: FR-005 (auto-provisioning), FR-009 (logout)

        Validates that:
        - User is auto-provisioned on first access
        - Logout does NOT delete user from database
        - User record persists after logout
        """
        # Trigger auto-provisioning
        response = client.get("/users/me")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"

        # Verify user created in database
        db_user = test_db.query(User).filter(
            User.user_id == "test_logout_user_00u1abc"
        ).first()
        assert db_user is not None, "User should be auto-provisioned"
        assert db_user.email == "test.curator.logout@alliancegenome.org"
        user_id = db_user.user_id

        # Logout
        logout_response = client.post("/auth/logout")
        assert logout_response.status_code == 200, f"Expected 200, got {logout_response.status_code}"

        # Verify user record still exists after logout
        test_db.expire_all()  # Force fresh query
        db_user_after = test_db.query(User).filter(
            User.user_id == "test_logout_user_00u1abc"
        ).first()
        assert db_user_after is not None, "User should persist after logout"
        assert db_user_after.user_id == user_id, "User ID should not change"

        # Simulate session termination
        client.simulate_logout()

        # Verify access denied after logout
        response = client.get("/users/me")
        assert response.status_code == 401, f"Expected 401, got {response.status_code}"


class TestLogoutFlowEdgeCases:
    """Edge case tests for logout flow."""

    def test_protected_endpoints_after_logout(self, client):
        """Test that all Okta-protected endpoints require re-auth after logout.

        Requirements: FR-009, FR-010
        """
        # Verify access to multiple endpoints works when authenticated
        # Only test auth-protected endpoints that don't require Weaviate/DB setup
        endpoints = ["/users/me"]
        for endpoint in endpoints:
            response = client.get(endpoint)
            # Should succeed with 200
            assert response.status_code == 200, f"{endpoint} should return 200 when authenticated, got {response.status_code}"

        # Logout
        logout_response = client.post("/auth/logout")
        assert logout_response.status_code == 200, f"Expected 200, got {logout_response.status_code}"

        # Simulate session termination
        client.simulate_logout()

        # Verify all endpoints now require re-auth
        for endpoint in endpoints:
            response = client.get(endpoint)
            assert response.status_code == 401, f"{endpoint} should return 401 after logout, got {response.status_code}"

    def test_multiple_logout_calls(self, client, test_db):
        """Test that calling logout multiple times is idempotent.

        While the first logout succeeds, subsequent calls without re-auth
        should fail with 401.
        """
        # First logout succeeds
        logout_response = client.post("/auth/logout")
        assert logout_response.status_code == 200

        # Simulate session termination
        client.simulate_logout()

        # Second logout fails (no authentication)
        logout_response2 = client.post("/auth/logout")
        assert logout_response2.status_code == 401, f"Second logout should fail with 401, got {logout_response2.status_code}"

    def test_logout_preserves_database_integrity(self, client, test_db):
        """Test that logout does not corrupt database state.

        Requirements: FR-009 (logout should not affect data integrity)
        """
        # Create user
        response = client.get("/users/me")
        assert response.status_code == 200

        # Get initial user count
        initial_count = test_db.query(User).filter(
            User.user_id == "test_logout_user_00u1abc"
        ).count()
        assert initial_count == 1

        # Logout
        logout_response = client.post("/auth/logout")
        assert logout_response.status_code == 200

        # Verify user count unchanged
        test_db.expire_all()
        final_count = test_db.query(User).filter(
            User.user_id == "test_logout_user_00u1abc"
        ).count()
        assert final_count == initial_count, "Logout should not delete user from database"

        # Verify user data unchanged
        db_user = test_db.query(User).filter(
            User.user_id == "test_logout_user_00u1abc"
        ).first()
        assert db_user is not None
        assert db_user.email == "test.curator.logout@alliancegenome.org"

    def test_concurrent_logout_attempts(self, client, test_db):
        """Test behavior when logout is called while authenticated but before simulate_logout.

        This validates that the endpoint itself just returns success,
        and the actual session termination is simulated separately.
        """
        # Call logout endpoint twice while still authenticated
        logout1 = client.post("/auth/logout")
        assert logout1.status_code == 200

        logout2 = client.post("/auth/logout")
        assert logout2.status_code == 200  # Both succeed while authenticated

        # Now simulate the session termination
        client.simulate_logout()

        # Subsequent access fails
        response = client.get("/users/me")
        assert response.status_code == 401
