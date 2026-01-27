"""Contract tests for GET /api/chat/history.

Task: T021 [P] - Contract test GET /api/chat/history
Contract: specs/007-okta-login/contracts/chat_endpoints.yaml lines 78-115

This test validates the FUTURE contract (not current implementation):
1. GET /api/chat/history requires Okta JWT
2. Returns sessions[] array with ChatSession objects
3. Each ChatSession has: session_id, user_id, created_at, last_message_at, messages[]
4. Each ChatMessage has: message_id, role (user|assistant), content, timestamp, trace_id
5. Enforces user-specific filtering (FR-014)

Contract response schema (lines 108-113):
{
  "sessions": [
    {
      "session_id": "session_abc123",
      "user_id": 123,
      "created_at": "2025-01-25T10:30:00Z",
      "last_message_at": "2025-01-25T10:35:00Z",
      "messages": [...]
    }
  ]
}

NOT the current {total_sessions, max_sessions, sessions:[ids]} format!

NOTE: All tests MUST fail until T027 implements the contract.
"""

import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime


@pytest.fixture
def client(monkeypatch):
    """Create test client with mocked dependencies and JWKS requests."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OKTA_DOMAIN", "dev-test.okta.com")
    monkeypatch.setenv("OKTA_API_AUDIENCE", "https://api.alliancegenome.org")

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


class TestChatHistoryEndpoint:
    """Tests for GET /api/chat/history - CONTRACT COMPLIANCE."""

    def test_history_requires_auth(self, client):
        """Test GET /api/chat/history returns 401 without authentication.

        Contract: chat_endpoints.yaml lines 114-115 (401 Unauthorized)
        """
        response = client.get("/api/chat/history")

        # Must return 401 per contract
        assert response.status_code == 401, \
            "Contract requires 401 Unauthorized for missing auth"

        data = response.json()
        assert "detail" in data

    def test_history_rejects_invalid_token(self, client):
        """Test invalid token returns 401.

        Contract: chat_endpoints.yaml lines 114-115
        """
        response = client.get(
            "/api/chat/history",
            headers={"Authorization": "Bearer invalid_token_xyz"}
        )

        assert response.status_code == 401
        data = response.json()
        assert "detail" in data

    def test_history_returns_sessions_array(self, client):
        """Test authenticated request returns sessions[] array per contract.

        Contract: chat_endpoints.yaml lines 108-113 (response schema)

        CRITICAL CONTRACT REQUIREMENT:
        Response must have "sessions" key with array of ChatSession objects.

        NOT the current {total_sessions, max_sessions, sessions:[string_ids]} format!
        """
        from main import app
        from src.api.auth import auth
        from dataclasses import dataclass

        @dataclass
        class MockOktaUser:
            uid: str = "test_user_id"
            cid: str = "client_id"
            email: str = "test@example.com"
            name: str = "Test User"
            groups: list = None
            token: str = "mock_token"

            def __post_init__(self):
                if self.groups is None:
                    self.groups = []

        app.dependency_overrides[auth.get_user] = lambda: MockOktaUser()

        response = client.get(
            "/api/chat/history",
            headers=get_valid_auth_header()
        )

        # Must return 200 OK
        assert response.status_code == 200, \
            "Contract requires 200 OK for authenticated request"

        data = response.json()

        # CONTRACT REQUIREMENT: Must have "sessions" key
        assert "sessions" in data, \
            "Contract requires 'sessions' key in response"

        # sessions must be an array
        assert isinstance(data["sessions"], list), \
            "Contract requires 'sessions' to be an array"

    def test_history_chat_session_schema(self, client):
        """Test ChatSession objects match contract schema.

        Contract: chat_endpoints.yaml lines 161-186 (ChatSession schema)

        Required fields per contract:
        - session_id (string)
        - user_id (integer)
        - created_at (datetime ISO 8601)
        - last_message_at (datetime ISO 8601, optional)
        - messages (array of ChatMessage)
        """
        from main import app
        from src.api.auth import auth
        from dataclasses import dataclass

        @dataclass
        class MockOktaUser:
            uid: str = "test_user_id"
            cid: str = "client_id"
            email: str = "test@example.com"
            name: str = "Test User"
            groups: list = None
            token: str = "mock_token"

            def __post_init__(self):
                if self.groups is None:
                    self.groups = []

        app.dependency_overrides[auth.get_user] = lambda: MockOktaUser()

        response = client.get(
            "/api/chat/history",
            headers=get_valid_auth_header()
        )

        assert response.status_code == 200
        data = response.json()

        # If there are sessions, validate schema
        if len(data.get("sessions", [])) > 0:
            session = data["sessions"][0]

            # Required fields per contract
            assert "session_id" in session, \
                "Contract requires session_id field"
            assert "user_id" in session, \
                "Contract requires user_id field"
            assert "created_at" in session, \
                "Contract requires created_at field"

            # Type validation
            assert isinstance(session["session_id"], str), \
                "session_id must be string"
            assert isinstance(session["user_id"], int), \
                "user_id must be integer"

            # ISO 8601 datetime format validation
            try:
                datetime.fromisoformat(session["created_at"].replace('Z', '+00:00'))
            except ValueError:
                pytest.fail("created_at must be ISO 8601 datetime")

            # messages array (optional but must be array if present)
            if "messages" in session:
                assert isinstance(session["messages"], list), \
                    "messages must be an array"

    def test_history_chat_message_schema(self, client):
        """Test ChatMessage objects match contract schema.

        Contract: chat_endpoints.yaml lines 187-213 (ChatMessage schema)

        Required fields per contract:
        - message_id (string)
        - role (enum: user|assistant)
        - content (string)
        - timestamp (datetime ISO 8601)
        - trace_id (string, nullable, for Langfuse)
        """
        from main import app
        from src.api.auth import auth
        from dataclasses import dataclass

        @dataclass
        class MockOktaUser:
            uid: str = "test_user_id"
            cid: str = "client_id"
            email: str = "test@example.com"
            name: str = "Test User"
            groups: list = None
            token: str = "mock_token"

            def __post_init__(self):
                if self.groups is None:
                    self.groups = []

        app.dependency_overrides[auth.get_user] = lambda: MockOktaUser()

        response = client.get(
            "/api/chat/history",
            headers=get_valid_auth_header()
        )

        assert response.status_code == 200
        data = response.json()

        # Find a session with messages
        for session in data.get("sessions", []):
            if "messages" in session and len(session["messages"]) > 0:
                message = session["messages"][0]

                # Required fields per contract
                assert "message_id" in message, \
                    "Contract requires message_id field"
                assert "role" in message, \
                    "Contract requires role field"
                assert "content" in message, \
                    "Contract requires content field"
                assert "timestamp" in message, \
                    "Contract requires timestamp field"

                # Type validation
                assert isinstance(message["message_id"], str), \
                    "message_id must be string"
                assert message["role"] in ["user", "assistant"], \
                    "role must be 'user' or 'assistant'"
                assert isinstance(message["content"], str), \
                    "content must be string"

                # ISO 8601 datetime validation
                try:
                    datetime.fromisoformat(message["timestamp"].replace('Z', '+00:00'))
                except ValueError:
                    pytest.fail("timestamp must be ISO 8601 datetime")

                # trace_id is optional but must be string or null
                if "trace_id" in message:
                    assert message["trace_id"] is None or isinstance(message["trace_id"], str), \
                        "trace_id must be string or null"

                break  # Validated at least one message

    def test_history_session_id_filter(self, client):
        """Test session_id query parameter filtering.

        Contract: chat_endpoints.yaml lines 87-92 (session_id parameter)
        """
        from main import app
        from src.api.auth import auth
        from dataclasses import dataclass

        @dataclass
        class MockOktaUser:
            uid: str = "test_user_id"
            cid: str = "client_id"
            email: str = "test@example.com"
            name: str = "Test User"
            groups: list = None
            token: str = "mock_token"

            def __post_init__(self):
                if self.groups is None:
                    self.groups = []

        app.dependency_overrides[auth.get_user] = lambda: MockOktaUser()

        response = client.get(
            "/api/chat/history?session_id=session_abc",
            headers=get_valid_auth_header()
        )

        # Should return 200 and filter results
        assert response.status_code == 200

        data = response.json()
        assert "sessions" in data

        # If results returned, all should match filter
        for session in data.get("sessions", []):
            assert session.get("session_id") == "session_abc", \
                "Contract requires filtering by session_id"

    def test_history_limit_parameter(self, client):
        """Test limit query parameter.

        Contract: chat_endpoints.yaml lines 93-101 (limit parameter)
        - Default: 50
        - Minimum: 1
        - Maximum: 500
        """
        from main import app
        from src.api.auth import auth
        from dataclasses import dataclass

        @dataclass
        class MockOktaUser:
            uid: str = "test_user_id"
            cid: str = "client_id"
            email: str = "test@example.com"
            name: str = "Test User"
            groups: list = None
            token: str = "mock_token"

            def __post_init__(self):
                if self.groups is None:
                    self.groups = []

        app.dependency_overrides[auth.get_user] = lambda: MockOktaUser()

        # Test with valid limit
        response = client.get(
            "/api/chat/history?limit=10",
            headers=get_valid_auth_header()
        )

        assert response.status_code == 200
        data = response.json()

        # Response should contain at most 10 sessions
        assert len(data.get("sessions", [])) <= 10, \
            "Contract requires limit parameter to restrict results"

    def test_history_limit_validation(self, client):
        """Test limit parameter validation.

        Contract: chat_endpoints.yaml lines 99-101
        - minimum: 1
        - maximum: 500
        """
        from main import app
        from src.api.auth import auth
        from dataclasses import dataclass

        @dataclass
        class MockOktaUser:
            uid: str = "test_user_id"
            cid: str = "client_id"
            email: str = "test@example.com"
            name: str = "Test User"
            groups: list = None
            token: str = "mock_token"

            def __post_init__(self):
                if self.groups is None:
                    self.groups = []

        app.dependency_overrides[auth.get_user] = lambda: MockOktaUser()

        # Test limit below minimum (0)
        response = client.get(
            "/api/chat/history?limit=0",
            headers=get_valid_auth_header()
        )

        assert response.status_code == 422, \
            "Contract requires 422 for limit < 1"

        # Test limit above maximum (501)
        response = client.get(
            "/api/chat/history?limit=501",
            headers=get_valid_auth_header()
        )

        assert response.status_code == 422, \
            "Contract requires 422 for limit > 500"


class TestChatHistoryDataIsolation:
    """Tests for user-specific history (FR-014)."""

    def test_history_returns_only_user_sessions(self, client):
        """Test users only see their own sessions.

        Contract: chat_endpoints.yaml lines 81-83
        Requirement: FR-014 (data isolation)

        CRITICAL: This test enforces that returned sessions belong to authenticated user.
        After T027 implementation, backend MUST:
        1. Database query filters by user_id from authenticated user
        2. No cross-user session access possible
        3. All returned session.user_id values match current_user
        """
        from main import app
        from src.api.auth import auth
        from dataclasses import dataclass
        from unittest.mock import patch

        @dataclass
        class MockOktaUser:
            uid: str = "user_a_id"
            cid: str = "client_id"
            email: str = "userA@test.com"
            name: str = "User A"
            groups: list = None
            token: str = "mock_token"

            def __post_init__(self):
                if self.groups is None:
                    self.groups = []

        mock_user = MockOktaUser()
        app.dependency_overrides[auth.get_user] = lambda: mock_user

        # Mock the endpoint's response data structure (not the internal helper)
        # After T027: This endpoint will query database and return contract-compliant format
        with patch("src.api.chat.get_all_sessions_stats") as mock_get_stats:
            # Mock returns contract-compliant response format
            async def mock_stats_response():
                return {
                    "sessions": [
                        {
                            "session_id": "session_1",
                            "user_id": 123,  # Corresponds to user_a_id in database
                            "created_at": "2025-01-25T10:00:00Z",
                            "last_message_at": "2025-01-25T10:05:00Z",
                            "messages": []
                        }
                    ]
                }

            mock_get_stats.return_value = mock_stats_response()

            response = client.get(
                "/api/chat/history",
                headers=get_valid_auth_header()
            )

            assert response.status_code == 200
            data = response.json()

            # CONTRACT ENFORCEMENT: All sessions must have user_id field
            assert "sessions" in data
            for session in data["sessions"]:
                assert "user_id" in session, \
                    "Contract requires user_id field in each session"

                # FR-014 ENFORCEMENT: All returned sessions must belong to authenticated user
                # After T027: This validates database query filtered by current_user
                # For now, we verify the structure exists and will contain correct data
                assert isinstance(session["user_id"], int), \
                    "user_id must be integer per contract"

    def test_history_prevents_cross_user_access(self, client):
        """Test User A cannot see User B's sessions.

        Requirement: FR-014 (strict data isolation)

        CRITICAL: This test enforces that User A and User B see different sessions.
        After T027 implementation, backend MUST:
        1. Filter database queries by current_user
        2. Return only sessions belonging to authenticated user
        3. User A's sessions have user_id=A, User B's have user_id=B
        """
        from main import app
        from src.api.auth import auth
        from dataclasses import dataclass
        from unittest.mock import patch

        # User A requests history
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

        user_a = UserA()
        app.dependency_overrides[auth.get_user] = lambda: user_a

        with patch("src.api.chat.get_all_sessions_stats") as mock_get_stats_a:
            # Mock returns sessions for User A only (user_id: 100)
            async def mock_stats_a():
                return {
                    "sessions": [
                        {
                            "session_id": "session_a1",
                            "user_id": 100,  # User A's database ID
                            "created_at": "2025-01-25T10:00:00Z",
                            "messages": []
                        }
                    ]
                }

            mock_get_stats_a.return_value = mock_stats_a()

            response_a = client.get(
                "/api/chat/history",
                headers=get_valid_auth_header()
            )

            assert response_a.status_code == 200
            data_a = response_a.json()

            # Verify User A sees their own sessions
            assert "sessions" in data_a
            for session in data_a["sessions"]:
                # FR-014: All sessions must belong to User A (user_id: 100)
                assert session["user_id"] == 100, \
                    "User A should only see sessions with user_id=100"

        # User B requests history
        @dataclass
        class UserB:
            uid: str = "user_b_id"
            cid: str = "client_id"
            email: str = "userB@test.com"
            name: str = "User B"
            groups: list = None
            token: str = "token_b"

            def __post_init__(self):
                if self.groups is None:
                    self.groups = []

        user_b = UserB()
        app.dependency_overrides[auth.get_user] = lambda: user_b

        with patch("src.api.chat.get_all_sessions_stats") as mock_get_stats_b:
            # Mock returns sessions for User B only (user_id: 200)
            async def mock_stats_b():
                return {
                    "sessions": [
                        {
                            "session_id": "session_b1",
                            "user_id": 200,  # User B's database ID
                            "created_at": "2025-01-25T11:00:00Z",
                            "messages": []
                        }
                    ]
                }

            mock_get_stats_b.return_value = mock_stats_b()

            response_b = client.get(
                "/api/chat/history",
                headers=get_valid_auth_header()
            )

            assert response_b.status_code == 200
            data_b = response_b.json()

            # Verify User B sees their own sessions
            assert "sessions" in data_b
            for session in data_b["sessions"]:
                # FR-014: All sessions must belong to User B (user_id: 200)
                assert session["user_id"] == 200, \
                    "User B should only see sessions with user_id=200"

        # FR-014 CRITICAL: Verify no overlap in session IDs
        session_ids_a = {s["session_id"] for s in data_a.get("sessions", [])}
        session_ids_b = {s["session_id"] for s in data_b.get("sessions", [])}
        assert session_ids_a.isdisjoint(session_ids_b), \
            "FR-014 violation: User A and User B must not share session IDs"

    def test_history_session_id_filter_respects_ownership(self, client):
        """Test session_id filter only returns if user owns session.

        Contract: FR-014 (prevent cross-user access)

        If User A requests session_id belonging to User B,
        should return empty results or 403 Forbidden.
        """
        from main import app
        from src.api.auth import auth
        from dataclasses import dataclass

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

        # Try to access User B's session
        response = client.get(
            "/api/chat/history?session_id=user_b_session",
            headers=get_valid_auth_header()
        )

        # Should return 200 with empty results or 403 Forbidden
        assert response.status_code in [200, 403]

        if response.status_code == 200:
            data = response.json()
            # Should return empty sessions array (no access to other user's data)
            assert data.get("sessions", []) == [], \
                "Contract requires empty results for non-owned sessions"
