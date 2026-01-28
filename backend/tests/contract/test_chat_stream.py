"""Contract tests for POST /api/chat (streaming).

Task: T020 [P] - Contract test POST /api/chat (streaming)

This test validates the FUTURE contract (not current implementation):
1. POST /api/chat (NOT /api/chat/stream) requires authentication
2. Returns SSE stream with contract-specified event types: token, done
3. Events match contract examples exactly (NOT current OpenAI Agents SDK events)
4. Query scoped to user's Weaviate tenant (FR-014)

Contract SSE format (lines 60-66):
  data: {"type": "token", "content": "The gene ent-1"}
  data: {"type": "token", "content": " is an equilibrative"}
  data: {"type": "done", "session_id": "session_abc123"}

NOT the current TEXT_MESSAGE_CONTENT/RUN_STARTED format!

NOTE: All tests MUST fail until T027 implements the contract.
"""

import pytest
import json
from unittest.mock import MagicMock, patch, AsyncMock


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


class TestChatStreamEndpoint:
    """Tests for POST /api/chat (streaming) - CONTRACT COMPLIANCE."""

    def test_chat_requires_auth(self, client):
        """Test that POST /api/chat returns 401 without authentication.

        Contract: chat_endpoints.yaml lines 74-75 (401 Unauthorized)

        CRITICAL: Endpoint is /api/chat (NOT /api/chat/stream).
        Current implementation uses /api/chat/stream - this must change.
        """
        # Contract endpoint path
        response = client.post(
            "/api/chat",  # Contract path, not /api/chat/stream
            json={"message": "What is the gene ent-1?"}
        )

        # Must return 401 per contract (currently returns 200 - MUST fail)
        assert response.status_code == 401, \
            "Contract requires 401 Unauthorized for missing auth"

        # Verify error response structure per contract
        data = response.json()
        assert "detail" in data
        assert data["detail"] in [
            "Not authenticated",
            "Invalid authentication token",
            "Token has expired"
        ], f"Got unexpected error: {data.get('detail')}"

    def test_chat_rejects_invalid_token(self, client):
        """Test that invalid token returns 401.

        Contract: chat_endpoints.yaml lines 234-237 (invalid_token example)
        """
        response = client.post(
            "/api/chat",
            headers={"Authorization": "Bearer invalid_token_xyz"},
            json={"message": "test"}
        )

        assert response.status_code == 401
        data = response.json()
        assert "detail" in data
        # Contract example: "Invalid authentication token"
        assert "invalid" in data["detail"].lower() or "authentication" in data["detail"].lower()

    def test_chat_returns_sse_stream(self, client):
        """Test that authenticated request returns SSE stream.

        Contract: chat_endpoints.yaml lines 50-66 (text/event-stream response)

        CRITICAL: Must return text/event-stream content type.
        """
        from main import app
        from src.api.auth import auth

        # Mock authentication (for testing response format)
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

        # Note: Current endpoint is /api/chat/stream, contract is /api/chat
        # This test validates contract requirement
        response = client.post(
            "/api/chat",  # Contract path
            headers=get_valid_auth_header(),
            json={"message": "test"}
        )

        # Contract requires text/event-stream (currently 404 - MUST fail)
        assert response.status_code == 200, \
            "Contract requires 200 OK for authenticated streaming"
        assert "text/event-stream" in response.headers.get("content-type", ""), \
            "Contract requires text/event-stream content type"

    def test_chat_sse_event_format(self, client):
        """Test SSE events match contract specification EXACTLY.

        Contract: chat_endpoints.yaml lines 60-66 (event examples)

        CRITICAL CONTRACT REQUIREMENT:
        Events must be: {"type": "token", "content": "..."}
                    or: {"type": "done", "session_id": "..."}

        NOT the current OpenAI Agents SDK format (TEXT_MESSAGE_CONTENT, RUN_STARTED, etc.)
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

        response = client.post(
            "/api/chat",
            headers=get_valid_auth_header(),
            json={"message": "test", "session_id": "session_123"}
        )

        # Parse SSE events (contract format: "data: {...}\n\n")
        events = []
        for line in response.text.split('\n'):
            if line.startswith('data:'):
                json_str = line[5:].strip()
                if json_str:
                    try:
                        event = json.loads(json_str)
                        events.append(event)
                    except json.JSONDecodeError:
                        pass

        # CONTRACT REQUIREMENT: Must have token and done events
        token_events = [e for e in events if e.get("type") == "token"]
        done_events = [e for e in events if e.get("type") == "done"]

        assert len(token_events) > 0, \
            "Contract requires token events with type='token'"
        assert len(done_events) == 1, \
            "Contract requires exactly one done event with type='done'"

        # Validate token event structure (contract lines 62-63)
        for event in token_events:
            assert "type" in event and event["type"] == "token", \
                "Token events must have type='token'"
            assert "content" in event, \
                "Token events must have content field"

        # Validate done event structure (contract line 65)
        done_event = done_events[0]
        assert "type" in done_event and done_event["type"] == "done", \
            "Done event must have type='done'"
        assert "session_id" in done_event, \
            "Done event must include session_id per contract"

    def test_chat_request_validation(self, client):
        """Test request validation per contract schema.

        Contract: chat_endpoints.yaml lines 34-38 (message required, minLength: 1)
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

        # Missing required 'message' field
        response = client.post(
            "/api/chat",
            headers=get_valid_auth_header(),
            json={"session_id": "test"}  # No message
        )

        assert response.status_code == 422, \
            "Contract requires 422 for missing required field"

    def test_chat_empty_message_rejected(self, client):
        """Test empty message rejected per contract.

        Contract: chat_endpoints.yaml line 38 (minLength: 1)
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

        response = client.post(
            "/api/chat",
            headers=get_valid_auth_header(),
            json={"message": ""}  # Empty string
        )

        assert response.status_code == 422, \
            "Contract requires 422 for empty message (minLength: 1)"

    def test_chat_session_id_optional(self, client):
        """Test session_id is optional per contract.

        Contract: chat_endpoints.yaml lines 39-42 (session_id optional)
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

        # Request without session_id
        response = client.post(
            "/api/chat",
            headers=get_valid_auth_header(),
            json={"message": "test"}  # No session_id
        )

        # Contract requires 200 OK with SSE stream (session_id is optional)
        assert response.status_code == 200, \
            "Contract requires 200 OK for authenticated request (session_id is optional)"
        assert "text/event-stream" in response.headers.get("content-type", ""), \
            "Contract requires text/event-stream content type"

    def test_chat_invalid_json_rejected(self, client):
        """Test invalid JSON returns 400.

        Contract: chat_endpoints.yaml lines 68-72 (400 Bad Request)
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

        response = client.post(
            "/api/chat",
            headers=get_valid_auth_header(),
            data="not json"  # Invalid JSON
        )

        assert response.status_code in [400, 422], \
            "Contract requires 400/422 for invalid JSON"


class TestChatTenantIsolation:
    """Tests for tenant-scoped queries (FR-014)."""

    def test_chat_uses_user_weaviate_tenant(self, client):
        """Test queries scoped to user's Weaviate tenant.

        Contract: chat_endpoints.yaml lines 21-24
        Requirement: FR-014 (data isolation via multi-tenancy)

        CRITICAL: This test enforces that authenticated requests succeed.
        After T027 + T033 implementation, the backend MUST:
        1. SupervisorFlow receives current_user from auth
        2. All Weaviate queries use .with_tenant(get_tenant_name(user.user_id))
        3. User A cannot search User B's documents
        """
        from main import app
        from src.api.auth import auth
        from dataclasses import dataclass
        from unittest.mock import patch, MagicMock

        @dataclass
        class MockCognitoUser:
            uid: str = "user_a_user_id"
            cid: str = "client_id"
            email: str = "userA@test.com"
            name: str = "User A"
            groups: list = None
            token: str = "mock_token"

            def __post_init__(self):
                if self.groups is None:
                    self.groups = []

        mock_user = MockCognitoUser()
        app.dependency_overrides[auth.get_user] = lambda: mock_user

        # Mock the chat flow to verify tenant parameter is passed
        with patch("src.api.chat.generate_chat_response") as mock_generate:
            # Mock returns SSE stream
            async def mock_sse_generator():
                yield 'data: {"type": "token", "content": "test"}\n\n'
                yield 'data: {"type": "done", "session_id": "session_123"}\n\n'

            mock_generate.return_value = mock_sse_generator()

            response = client.post(
                "/api/chat",
                headers=get_valid_auth_header(),
                json={"message": "test query"}
            )

            # Contract requires 200 OK with SSE stream
            assert response.status_code == 200, \
                "Contract requires 200 OK for authenticated tenant-scoped request"
            assert "text/event-stream" in response.headers.get("content-type", ""), \
                "Contract requires text/event-stream content type"

            # FR-014 CRITICAL: Verify generate_chat_response was called with user identity
            mock_generate.assert_called_once()
            call_args = mock_generate.call_args.args if mock_generate.call_args else ()

            # Contract requirement: Tenant isolation MUST be enforced via user_id (second parameter)
            assert len(call_args) >= 2, \
                "FR-014 requires user_id to be passed as second parameter to generate_chat_response for tenant isolation"

            # Verify the correct user identity was passed (second positional argument)
            user_id_arg = call_args[1]
            assert user_id_arg == "user_a_user_id", \
                f"FR-014 requires authenticated user's ID to be passed for tenant scoping (got {user_id_arg})"

    def test_chat_prevents_cross_user_data_access(self, client):
        """Test User A cannot access User B's documents via chat.

        Requirement: FR-014 (strict data isolation)

        CRITICAL: This test enforces distinct tenant identifiers for each user.
        After T027 + T033 implementation, the backend MUST:
        - User A's query → searches only user_a_tenant in Weaviate
        - User B's query → searches only user_b_tenant in Weaviate
        - No query can access multiple tenants
        """
        from main import app
        from src.api.auth import auth
        from dataclasses import dataclass
        from unittest.mock import patch

        # User A chat
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

        with patch("src.api.chat.generate_chat_response") as mock_generate_a:
            async def mock_sse_a():
                yield 'data: {"type": "token", "content": "User A result"}\n\n'
                yield 'data: {"type": "done", "session_id": "session_a"}\n\n'

            mock_generate_a.return_value = mock_sse_a()

            response_a = client.post(
                "/api/chat",
                headers=get_valid_auth_header(),
                json={"message": "query"}
            )

            # Contract requires 200 OK for authenticated User A
            assert response_a.status_code == 200, \
                "Contract requires 200 OK for authenticated User A request"

            # FR-014: Verify User A's identity was passed
            mock_generate_a.assert_called_once()
            call_a_kwargs = mock_generate_a.call_args.kwargs if mock_generate_a.call_args else {}
            assert "current_user" in call_a_kwargs, \
                "FR-014 requires current_user for User A"
            user_a_identity = call_a_kwargs["current_user"].uid

        # User B chat
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

        with patch("src.api.chat.generate_chat_response") as mock_generate_b:
            async def mock_sse_b():
                yield 'data: {"type": "token", "content": "User B result"}\n\n'
                yield 'data: {"type": "done", "session_id": "session_b"}\n\n'

            mock_generate_b.return_value = mock_sse_b()

            response_b = client.post(
                "/api/chat",
                headers=get_valid_auth_header(),
                json={"message": "query"}
            )

            # Contract requires 200 OK for authenticated User B
            assert response_b.status_code == 200, \
                "Contract requires 200 OK for authenticated User B request"

            # FR-014: Verify User B's identity was passed
            mock_generate_b.assert_called_once()
            call_b_kwargs = mock_generate_b.call_args.kwargs if mock_generate_b.call_args else {}
            assert "current_user" in call_b_kwargs, \
                "FR-014 requires current_user for User B"
            user_b_identity = call_b_kwargs["current_user"].uid

        # FR-014 CRITICAL: Verify distinct tenant identifiers
        assert user_a_identity == "user_a_id", \
            "User A's tenant must be derived from user_a_id"
        assert user_b_identity == "user_b_id", \
            "User B's tenant must be derived from user_b_id"
        assert user_a_identity != user_b_identity, \
            "FR-014 violation: User A and User B must have distinct tenant identifiers"
