"""Contract tests for DELETE /api/chat/session/{session_id}.

Task: T022 [P] - Contract test DELETE /api/chat/session/{session_id}

This test validates the FUTURE contract (not current implementation):
1. DELETE /api/chat/session/{session_id} requires authentication
2. Returns 204 No Content on successful deletion (no response body)
3. Returns 403 Forbidden when user tries to delete another user's session
4. Returns 404 Not Found when session doesn't exist
5. Deletes session and all associated messages
6. Enforces user ownership validation per FR-014

Contract response codes (lines 133-148):
- 204: Session deleted successfully (no response body)
- 401: Not authenticated / Invalid token
- 403: User does not own this session
- 404: Session not found

NOTE: All tests MUST fail until T027 implements the contract.

CRITICAL: Endpoint is /api/chat/session/{session_id} (NOT /api/chat/history/{session_id})!
"""

import pytest
from unittest.mock import MagicMock, patch


@pytest.fixture
def client(monkeypatch):
    """Create test client with mocked dependencies."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    with patch("requests.get") as mock_get:
        mock_response = MagicMock()
        mock_response.json.return_value = {"keys": []}
        mock_response.status_code = 200
        mock_get.return_value = mock_response

        from fastapi.testclient import TestClient
        import sys
        import os
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        from main import app

        app.dependency_overrides.clear()
        yield TestClient(app)
        app.dependency_overrides.clear()


def get_valid_auth_header():
    """Generate mock Authorization header with valid JWT token."""
    return {"Authorization": "Bearer mock_valid_token_12345"}


class TestDeleteSessionEndpoint:
    """Tests for DELETE /api/chat/session/{session_id} - CONTRACT COMPLIANCE."""

    def test_delete_session_requires_auth(self, client):
        """Test DELETE /api/chat/session/{session_id} returns 401 without authentication.

        Contract: chat_endpoints.yaml lines 136-137 (401 Unauthorized)

        CRITICAL: Endpoint is /api/chat/session/{session_id}
        """
        response = client.delete("/api/chat/session/session_abc123")

        # Must return 401 per contract
        assert response.status_code == 401, \
            "Contract requires 401 Unauthorized for missing auth"

        data = response.json()
        assert "detail" in data

    def test_delete_session_rejects_invalid_token(self, client):
        """Test invalid token returns 401.

        Contract: chat_endpoints.yaml lines 136-137
        """
        response = client.delete(
            "/api/chat/session/session_abc123",
            headers={"Authorization": "Bearer invalid_token_xyz"}
        )

        assert response.status_code == 401
        data = response.json()
        assert "detail" in data

    def test_delete_session_success_returns_204(self, client):
        """Test successful deletion returns 204 No Content.

        Contract: chat_endpoints.yaml lines 133-134 (204 response)

        CRITICAL CONTRACT REQUIREMENT:
        Must return 204 No Content on success (NO response body).

        NOT 200 OK with success message!
        """
        from main import app
        from src.api.auth import auth
        from dataclasses import dataclass

        @dataclass
        class MockCognitoUser:
            uid: str = "test_user_id"
            cid: str = "client_id"
            email: str = "test@example.com"
            name: str = "Test User"
            groups: list = None
            token: str = "mock_token"

            def __post_init__(self):
                if self.groups is None:
                    self.groups = []

        app.dependency_overrides[auth.get_user] = lambda: MockCognitoUser()

        response = client.delete(
            "/api/chat/session/session_abc123",
            headers=get_valid_auth_header()
        )

        # Contract requires 204 No Content
        assert response.status_code == 204, \
            "Contract requires 204 No Content on successful deletion"

        # 204 responses must have no body
        assert response.content == b"", \
            "Contract requires 204 response to have no body"

    def test_delete_session_not_found_returns_404(self, client):
        """Test deleting non-existent session returns 404.

        Contract: chat_endpoints.yaml lines 144-148 (404 response)
        """
        from main import app
        from src.api.auth import auth
        from dataclasses import dataclass

        @dataclass
        class MockCognitoUser:
            uid: str = "test_user_id"
            cid: str = "client_id"
            email: str = "test@example.com"
            name: str = "Test User"
            groups: list = None
            token: str = "mock_token"

            def __post_init__(self):
                if self.groups is None:
                    self.groups = []

        app.dependency_overrides[auth.get_user] = lambda: MockCognitoUser()

        response = client.delete(
            "/api/chat/session/nonexistent_session",
            headers=get_valid_auth_header()
        )

        # Contract requires 404 Not Found
        assert response.status_code == 404, \
            "Contract requires 404 Not Found for non-existent sessions"

        # Verify Error schema (contract lines 238-245)
        data = response.json()
        assert "detail" in data, \
            "Error responses must have detail field"
        assert isinstance(data["detail"], str)

    def test_delete_session_non_owner_returns_403(self, client):
        """Test User A cannot delete User B's session (returns 403).

        Contract: chat_endpoints.yaml lines 138-142 (403 Forbidden response)
        Requirement: FR-014 (data isolation)

        CRITICAL CONTRACT REQUIREMENT:
        If user tries to delete session they don't own, must return 403 Forbidden.
        """
        from main import app
        from src.api.auth import auth
        from dataclasses import dataclass

        @dataclass
        class MockCognitoUser:
            uid: str = "user_a_id"
            cid: str = "client_id"
            email: str = "userA@test.com"
            name: str = "User A"
            groups: list = None
            token: str = "mock_token"

            def __post_init__(self):
                if self.groups is None:
                    self.groups = []

        app.dependency_overrides[auth.get_user] = lambda: MockCognitoUser()

        # Try to delete User B's session
        response = client.delete(
            "/api/chat/session/user_b_session",
            headers=get_valid_auth_header()
        )

        # Contract requires 403 Forbidden for non-owners
        assert response.status_code == 403, \
            "Contract requires 403 Forbidden when user tries to delete non-owned session"

        # Verify Error schema
        data = response.json()
        assert "detail" in data
        # Contract example: "User does not own this session"
        assert "own" in data["detail"].lower() or "forbidden" in data["detail"].lower()


class TestDeleteSessionDataIsolation:
    """Tests for session ownership validation (FR-014)."""

    def test_delete_own_session_allowed(self, client):
        """Test user can delete their own session (204).

        Requirement: FR-014 (users manage their own data)
        """
        from main import app
        from src.api.auth import auth
        from dataclasses import dataclass

        @dataclass
        class MockCognitoUser:
            uid: str = "user_a_id"
            cid: str = "client_id"
            email: str = "userA@test.com"
            name: str = "User A"
            groups: list = None
            token: str = "mock_token"

            def __post_init__(self):
                if self.groups is None:
                    self.groups = []

        app.dependency_overrides[auth.get_user] = lambda: MockCognitoUser()

        response = client.delete(
            "/api/chat/session/user_a_own_session",
            headers=get_valid_auth_header()
        )

        # Should succeed with 204
        assert response.status_code == 204, \
            "Users must be able to delete their own sessions"
        assert response.content == b""

    def test_delete_prevents_cross_user_access(self, client):
        """Test User A cannot delete User B's session.

        Requirement: FR-014 (strict data isolation)

        After T027 implementation, verify:
        1. Endpoint queries database for session ownership
        2. Checks session.user_id == current_user.user_id
        3. Returns 403 if ownership check fails
        """
        from main import app
        from src.api.auth import auth
        from dataclasses import dataclass

        # User A tries to delete User B's session
        @dataclass
        class UserA:
            uid: str = "user_a_id"
            cid: str = "client_id"
            email: str = "userA@test.com"
            name: str = "User A"
            groups: list = None
            token: str = "token_a"

            def __post_init__(self):
                if self.groups is None:
                    self.groups = []

        app.dependency_overrides[auth.get_user] = lambda: UserA()

        response = client.delete(
            "/api/chat/session/user_b_session",
            headers=get_valid_auth_header()
        )

        # Must return 403 (not 204, not 404)
        assert response.status_code == 403, \
            "Contract requires 403 when attempting to delete another user's session"


class TestDeleteSessionErrorSchema:
    """Tests for error response schema compliance."""

    def test_error_responses_have_detail_field(self, client):
        """Test all error responses follow Error schema.

        Contract: chat_endpoints.yaml lines 238-245 (Error schema)

        All error responses (401, 403, 404) must have:
        - detail: string (error message)
        """
        from main import app
        from src.api.auth import auth
        from dataclasses import dataclass

        @dataclass
        class MockCognitoUser:
            uid: str = "test_user_id"
            cid: str = "client_id"
            email: str = "test@example.com"
            name: str = "Test User"
            groups: list = None
            token: str = "mock_token"

            def __post_init__(self):
                if self.groups is None:
                    self.groups = []

        # Test 401 error schema (no auth)
        response_401 = client.delete("/api/chat/session/test")
        assert response_401.status_code == 401
        data_401 = response_401.json()
        assert "detail" in data_401
        assert isinstance(data_401["detail"], str)

        # Test 404 error schema (session not found)
        app.dependency_overrides[auth.get_user] = lambda: MockCognitoUser()
        response_404 = client.delete(
            "/api/chat/session/nonexistent",
            headers=get_valid_auth_header()
        )
        if response_404.status_code == 404:
            data_404 = response_404.json()
            assert "detail" in data_404
            assert isinstance(data_404["detail"], str)

    def test_403_error_message_indicates_ownership_issue(self, client):
        """Test 403 error message clearly indicates ownership problem.

        Contract: chat_endpoints.yaml lines 140-142 (example error message)
        """
        from main import app
        from src.api.auth import auth
        from dataclasses import dataclass

        @dataclass
        class MockCognitoUser:
            uid: str = "user_a_id"
            cid: str = "client_id"
            email: str = "userA@test.com"
            name: str = "User A"
            groups: list = None
            token: str = "mock_token"

            def __post_init__(self):
                if self.groups is None:
                    self.groups = []

        app.dependency_overrides[auth.get_user] = lambda: MockCognitoUser()

        response = client.delete(
            "/api/chat/session/other_user_session",
            headers=get_valid_auth_header()
        )

        if response.status_code == 403:
            data = response.json()
            detail_lower = data["detail"].lower()
            # Contract example: "User does not own this session"
            assert "own" in detail_lower or "forbidden" in detail_lower or \
                   "permission" in detail_lower or "access" in detail_lower, \
                "403 error should clearly indicate ownership/permission issue"
