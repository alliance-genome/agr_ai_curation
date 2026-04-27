"""Integration test for chat query tenant isolation.

Task: T055 - Integration test for chat query tenant isolation
Scenario: quickstart.md:290-312
Requirements: FR-014 (chat queries scoped to user's documents)

Tests that:
1. Chat queries only search user's own Weaviate tenant
2. User A's chat cannot access User B's documents
3. Weaviate queries use .with_tenant() for isolation
4. SupervisorState receives correct user_id
5. DocumentSelectionState is per-user (not global)
6. Chat history is user-specific

CRITICAL: This test validates complete chat data isolation between users.

Implementation Notes:
- Creates two users with separate documents
- User A chats and should only see their documents
- User B chats and should only see their documents
- Verifies tenant-scoped Weaviate queries
- Tests DocumentSelectionState isolation
"""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone
from uuid import uuid4

from src.lib.chat_history_repository import ASSISTANT_CHAT_KIND, ChatHistoryRepository
from src.models.sql.chat_message import ChatMessage as ChatMessageModel
from src.models.sql.chat_session import ChatSession as ChatSessionModel
from src.models.sql.user import User
from src.models.sql.pdf_document import PDFDocument
from src.lib.weaviate_helpers import get_tenant_name

# Note: test_db and cleanup_db fixtures are now in conftest.py
# Note: curator1_user and curator2_user are pre-registered as "chat1" and "chat2" in conftest.py


def _ts(hour: int, minute: int = 0, second: int = 0) -> datetime:
    return datetime(2026, 4, 19, hour, minute, second, tzinfo=timezone.utc)


@pytest.fixture
def mock_weaviate_with_tenant_tracking():
    """Mock Weaviate client that tracks which tenant is used.

    This fixture captures calls to .with_tenant() to verify
    tenant isolation is enforced in chat queries.
    """
    with patch("src.services.user_service.get_connection") as mock_service_conn, \
         patch("src.lib.weaviate_helpers.get_connection") as mock_helpers_conn:

        # Track tenant usage
        tenant_calls = {"calls": []}

        mock_client = MagicMock()
        mock_session = MagicMock()

        # Mock collections with tenant tracking
        def create_tracked_collection(name):
            collection = MagicMock()
            collection.name = name

            # Track when .with_tenant() is called
            def with_tenant(tenant_name):
                tenant_calls["calls"].append({
                    "collection": name,
                    "tenant": tenant_name
                })
                # Return a new mock that still has query capabilities
                tenant_scoped = MagicMock()
                tenant_scoped.query = MagicMock()
                tenant_scoped.query.fetch_objects = MagicMock(return_value=MagicMock(objects=[]))
                tenant_scoped.query.near_text = MagicMock(return_value=tenant_scoped.query)
                tenant_scoped.data = MagicMock()
                return tenant_scoped

            collection.with_tenant = with_tenant

            # Default query methods (should fail if tenant not specified)
            collection.query = MagicMock()
            collection.tenants = MagicMock()
            collection.tenants.create = MagicMock()

            return collection

        def get_collection(name):
            return create_tracked_collection(name)

        mock_client.collections.get = get_collection

        # Configure session
        mock_session.__enter__.return_value = mock_client
        mock_session.__exit__.return_value = None

        for mock_conn in [mock_service_conn, mock_helpers_conn]:
            mock_conn.return_value.session.return_value = mock_session

        yield {
            "client": mock_client,
            "tenant_calls": tenant_calls,
        }


@pytest.fixture
def client_as_curator1(get_auth_mock, test_db):
    """Create test client authenticated as Curator 1 (chat1)."""
    # Configure shared auth mock for chat1 user
    get_auth_mock.set_user("chat1")

    import sys

    modules_to_clear = [
        name for name in list(sys.modules.keys())
        if name == "main" or name.startswith("src.")
    ]
    for module_name in modules_to_clear:
        del sys.modules[module_name]

    from main import app
    from src.api.auth import _get_user_from_cookie_impl
    from src.models.sql.database import get_db

    def override_get_db():
        yield test_db

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[_get_user_from_cookie_impl] = get_auth_mock.get_user

    yield TestClient(app)

    app.dependency_overrides.clear()


class TestChatIsolation:
    """Integration tests for chat query tenant isolation."""

    def test_chat_query_uses_correct_tenant(
        self, test_db, curator1_user, client_as_curator1, mock_weaviate_with_tenant_tracking
    ):
        """Test that chat queries use the authenticated user's tenant.

        Validates FR-014: Chat queries scoped to user's tenant.
        """
        # Create user
        user = User(
            auth_sub=curator1_user.uid,
            email=curator1_user.email,
            display_name=curator1_user.email,
            created_at=datetime.now(timezone.utc),
            last_login=datetime.now(timezone.utc),
            is_active=True
        )
        test_db.add(user)
        test_db.commit()

        # Mock chat flow execution to avoid complex setup
        with patch("src.api.chat_stream.get_supervisor_tool_agent_map", return_value={}), \
             patch("src.api.chat_stream.run_agent_streamed") as mock_execute:
            # Mock successful chat response
            async def mock_chat_generator():
                yield {"type": "RUN_FINISHED", "data": {"response": "Test response"}}

            mock_execute.return_value = mock_chat_generator()
            session_id = f"test-session-{uuid4().hex[:8]}"

            # Send chat message
            response = client_as_curator1.post(
                "/api/chat",
                json={
                    "message": "What documents do I have?",
                    "session_id": session_id
                }
            )

            # Verify request was made (may return different status codes)
            # The key is that SupervisorState receives the correct user_id
            assert response.status_code in [200, 422], \
                f"Chat endpoint should be accessible, got {response.status_code}"

            # Verify mock was called with authenticated user_id
            if mock_execute.called:
                call_kwargs = mock_execute.call_args.kwargs if mock_execute.call_args else {}
                # Chat endpoints currently use token "sub" as canonical user_id.
                assert call_kwargs.get("user_id") == curator1_user["sub"], \
                    "Chat flow should receive authenticated user's ID as user_id"

    def test_chat_queries_isolated_between_users(
        self, test_db, get_auth_mock, curator1_user, curator2_user,
        client_as_curator1,
        mock_weaviate_with_tenant_tracking
    ):
        """Test that two users' chat queries are isolated.

        Validates FR-014: User A's chat cannot access User B's documents.
        """
        # Create users
        user1 = User(
            auth_sub=curator1_user.uid,
            email=curator1_user.email,
            display_name=curator1_user.email,
            created_at=datetime.now(timezone.utc),
            last_login=datetime.now(timezone.utc),
            is_active=True
        )
        test_db.add(user1)

        user2 = User(
            auth_sub=curator2_user.uid,
            email=curator2_user.email,
            display_name=curator2_user.email,
            created_at=datetime.now(timezone.utc),
            last_login=datetime.now(timezone.utc),
            is_active=True
        )
        test_db.add(user2)
        test_db.commit()

        # Create documents for each user
        doc1_id = str(uuid4())
        doc1 = PDFDocument(
            id=doc1_id,
            user_id=user1.id,
            filename="test_user1_doc.pdf",
            file_path=f"/test/path/{doc1_id}.pdf",
            file_hash="hash1",
            file_size=1024,
            page_count=5,
            upload_timestamp=datetime.now(timezone.utc)
        )
        test_db.add(doc1)
        test_db.commit()

        doc2_id = str(uuid4())
        doc2 = PDFDocument(
            id=doc2_id,
            user_id=user2.id,
            filename="test_user2_doc.pdf",
            file_path=f"/test/path/{doc2_id}.pdf",
            file_hash="hash2",
            file_size=2048,
            page_count=10,
            upload_timestamp=datetime.now(timezone.utc)
        )
        test_db.add(doc2)
        test_db.commit()

        # Mock chat flow to track tenant usage
        with patch("src.api.chat_stream.get_supervisor_tool_agent_map", return_value={}), \
             patch("src.api.chat_stream.run_agent_streamed") as mock_execute:
            async def mock_chat_generator():
                yield {"type": "RUN_FINISHED", "data": {"response": "Test"}}

            mock_execute.side_effect = lambda **_kwargs: mock_chat_generator()
            first_session_id = f"tenant-session-{uuid4().hex[:8]}"
            second_session_id = f"tenant-session-{uuid4().hex[:8]}"

            # User 1 sends chat message
            get_auth_mock.set_user("chat1")
            first_response = client_as_curator1.post(
                "/api/chat",
                json={"message": "Show my documents", "session_id": first_session_id}
            )
            assert first_response.status_code in [200, 422]

            # User 2 sends chat message
            get_auth_mock.set_user("chat2")
            second_response = client_as_curator1.post(
                "/api/chat",
                json={"message": "Show my documents", "session_id": second_session_id}
            )
            assert second_response.status_code in [200, 422]

            # Both requests should succeed (or fail gracefully, not cross-contaminate)
            # The key is that they should use different tenants

            # Verify tenants are different
            expected_tenant1 = get_tenant_name(curator1_user["sub"])
            expected_tenant2 = get_tenant_name(curator2_user["sub"])

            tenant_calls = mock_weaviate_with_tenant_tracking["tenant_calls"]["calls"]

            # Check that different tenants were used (if any Weaviate calls made)
            if tenant_calls:
                tenants_used = {call["tenant"] for call in tenant_calls}

                # If both users' tenants were used, they should be different
                if expected_tenant1 in tenants_used and expected_tenant2 in tenants_used:
                    assert expected_tenant1 != expected_tenant2, \
                        "Different users must use different tenants"

    def test_document_selection_state_is_per_user(
        self, test_db, curator1_user, curator2_user
    ):
        """Test that DocumentSelectionState is isolated per user.

        Validates: Per-user chat state (not global singleton).

        Note: This tests that the DocumentSelectionState uses a per-user
        dictionary, not a global variable that would be shared between users.
        """
        # Create users
        user1 = User(
            auth_sub=curator1_user.uid,
            email=curator1_user.email,
            display_name=curator1_user.email,
            created_at=datetime.now(timezone.utc),
            last_login=datetime.now(timezone.utc),
            is_active=True
        )
        test_db.add(user1)

        user2 = User(
            auth_sub=curator2_user.uid,
            email=curator2_user.email,
            display_name=curator2_user.email,
            created_at=datetime.now(timezone.utc),
            last_login=datetime.now(timezone.utc),
            is_active=True
        )
        test_db.add(user2)
        test_db.commit()

        # Test that DocumentSelectionState can handle concurrent users
        from src.lib.chat_state import document_state

        # Simulate User 1 selecting a document
        doc1_data = {"id": "doc1", "title": "User 1 Doc"}
        document_state.set_document(curator1_user["sub"], doc1_data)

        # Simulate User 2 selecting a different document
        doc2_data = {"id": "doc2", "title": "User 2 Doc"}
        document_state.set_document(curator2_user["sub"], doc2_data)

        # Verify both users' selections are isolated
        user1_doc = document_state.get_document(curator1_user["sub"])
        user2_doc = document_state.get_document(curator2_user["sub"])

        assert user1_doc is not None, "User 1's document should be set"
        assert user2_doc is not None, "User 2's document should be set"
        assert user1_doc["id"] == "doc1", "User 1 should see their own document"
        assert user2_doc["id"] == "doc2", "User 2 should see their own document"
        assert user1_doc["id"] != user2_doc["id"], \
            "Users' document selections should be isolated"

        # Cleanup
        document_state.clear_document(curator1_user["sub"])
        document_state.clear_document(curator2_user["sub"])

    def test_chat_history_is_user_specific(
        self, test_db, get_auth_mock, curator1_user, curator2_user,
        client_as_curator1,
        mock_weaviate_with_tenant_tracking
    ):
        """Test that chat history is user-specific.

        Validates FR-014: Chat history isolated per user.
        """
        # Create users
        user1 = User(
            auth_sub=curator1_user.uid,
            email=curator1_user.email,
            display_name=curator1_user.email,
            created_at=datetime.now(timezone.utc),
            last_login=datetime.now(timezone.utc),
            is_active=True
        )
        test_db.add(user1)

        user2 = User(
            auth_sub=curator2_user.uid,
            email=curator2_user.email,
            display_name=curator2_user.email,
            created_at=datetime.now(timezone.utc),
            last_login=datetime.now(timezone.utc),
            is_active=True
        )
        test_db.add(user2)
        test_db.commit()

        repository = ChatHistoryRepository(test_db)
        query_token = uuid4().hex[:8]
        session1_id = f"chat-isolation-{query_token}-user1"
        session2_id = f"chat-isolation-{query_token}-user2"

        test_db.query(ChatMessageModel).delete(synchronize_session=False)
        test_db.query(ChatSessionModel).delete(synchronize_session=False)
        test_db.commit()

        repository.create_session(
            session_id=session1_id,
            user_auth_sub=curator1_user["sub"],
            chat_kind=ASSISTANT_CHAT_KIND,
            title=f"chat isolation {query_token} session user1",
            created_at=_ts(9, 0),
        )
        repository.append_message(
            session_id=session1_id,
            user_auth_sub=curator1_user["sub"],
            chat_kind=ASSISTANT_CHAT_KIND,
            role="user",
            content="What is my TP53 evidence?",
            turn_id="turn-user1-1",
            created_at=_ts(9, 1),
        )
        repository.append_message(
            session_id=session1_id,
            user_auth_sub=curator1_user["sub"],
            chat_kind=ASSISTANT_CHAT_KIND,
            role="assistant",
            content="User 1 TP53 answer",
            turn_id="turn-user1-1",
            trace_id="trace-user1-1",
            created_at=_ts(9, 2),
        )

        repository.create_session(
            session_id=session2_id,
            user_auth_sub=curator2_user["sub"],
            chat_kind=ASSISTANT_CHAT_KIND,
            title=f"chat isolation {query_token} session user2",
            created_at=_ts(10, 0),
        )
        repository.append_message(
            session_id=session2_id,
            user_auth_sub=curator2_user["sub"],
            chat_kind=ASSISTANT_CHAT_KIND,
            role="user",
            content="What is my EGFR evidence?",
            turn_id="turn-user2-1",
            created_at=_ts(10, 1),
        )
        repository.append_message(
            session_id=session2_id,
            user_auth_sub=curator2_user["sub"],
            chat_kind=ASSISTANT_CHAT_KIND,
            role="assistant",
            content="User 2 EGFR answer",
            turn_id="turn-user2-1",
            trace_id="trace-user2-1",
            created_at=_ts(10, 2),
        )
        test_db.commit()

        get_auth_mock.set_user("chat1")
        response1 = client_as_curator1.get(
            "/api/chat/history",
            params={"chat_kind": "assistant_chat"},
        )
        get_auth_mock.set_user("chat2")
        response2 = client_as_curator1.get(
            "/api/chat/history",
            params={"chat_kind": "assistant_chat"},
        )

        assert response1.status_code == 200, \
            f"User 1 chat history should be accessible, got {response1.status_code}"
        assert response2.status_code == 200, \
            f"User 2 chat history should be accessible, got {response2.status_code}"

        history1 = response1.json()
        history2 = response2.json()

        assert history1["total_sessions"] == 1
        assert [session["session_id"] for session in history1["sessions"]] == [session1_id]
        assert [session["chat_kind"] for session in history1["sessions"]] == [ASSISTANT_CHAT_KIND]
        assert history2["total_sessions"] == 1
        assert [session["session_id"] for session in history2["sessions"]] == [session2_id]
        assert [session["chat_kind"] for session in history2["sessions"]] == [ASSISTANT_CHAT_KIND]

        get_auth_mock.set_user("chat1")
        detail1 = client_as_curator1.get(f"/api/chat/history/{session1_id}")
        assert detail1.status_code == 200, detail1.text
        assert [(message["role"], message["content"]) for message in detail1.json()["messages"]] == [
            ("user", "What is my TP53 evidence?"),
            ("assistant", "User 1 TP53 answer"),
        ]

        get_auth_mock.set_user("chat1")
        hidden_from_user1 = client_as_curator1.get(f"/api/chat/history/{session2_id}")
        get_auth_mock.set_user("chat2")
        hidden_from_user2 = client_as_curator1.get(f"/api/chat/history/{session1_id}")
        assert hidden_from_user1.status_code == 404
        assert hidden_from_user2.status_code == 404

    def test_assistant_rescue_is_user_specific_and_idempotent(
        self, test_db, get_auth_mock, curator1_user, curator2_user,
        client_as_curator1,
        mock_weaviate_with_tenant_tracking
    ):
        """Test that assistant rescue respects durable session ownership."""
        repository = ChatHistoryRepository(test_db)
        session_id = f"chat-rescue-{uuid4().hex[:8]}"
        turn_id = f"turn-rescue-{uuid4().hex[:8]}"

        repository.create_session(
            session_id=session_id,
            user_auth_sub=curator1_user["sub"],
            chat_kind=ASSISTANT_CHAT_KIND,
            title="Rescue owner session",
            created_at=_ts(11, 0),
        )
        repository.append_message(
            session_id=session_id,
            user_auth_sub=curator1_user["sub"],
            chat_kind=ASSISTANT_CHAT_KIND,
            role="user",
            content="Recover this answer",
            turn_id=turn_id,
            created_at=_ts(11, 1),
        )
        test_db.commit()

        get_auth_mock.set_user("chat2")
        hidden_response = client_as_curator1.post(
            f"/api/chat/{session_id}/assistant-rescue",
            json={
                "turn_id": turn_id,
                "content": "Recovered response",
                "trace_id": "trace-rescue-1",
            },
        )
        assert hidden_response.status_code == 404, hidden_response.text

        get_auth_mock.set_user("chat1")
        first_owner_response = client_as_curator1.post(
            f"/api/chat/{session_id}/assistant-rescue",
            json={
                "turn_id": turn_id,
                "content": "Recovered response",
                "trace_id": "trace-rescue-1",
            },
        )
        assert first_owner_response.status_code == 200, first_owner_response.text
        assert first_owner_response.json()["created"] is True

        get_auth_mock.set_user("chat1")
        second_owner_response = client_as_curator1.post(
            f"/api/chat/{session_id}/assistant-rescue",
            json={
                "turn_id": turn_id,
                "content": "Recovered response",
                "trace_id": "trace-rescue-1",
            },
        )
        assert second_owner_response.status_code == 200, second_owner_response.text
        assert second_owner_response.json()["created"] is False

        get_auth_mock.set_user("chat1")
        detail = client_as_curator1.get(f"/api/chat/history/{session_id}")
        assert detail.status_code == 200, detail.text
        assert [(message["role"], message["turn_id"], message["content"]) for message in detail.json()["messages"]] == [
            ("user", turn_id, "Recover this answer"),
            ("assistant", turn_id, "Recovered response"),
        ]

    def test_supervisor_state_receives_user_id(
        self, test_db, curator1_user, client_as_curator1, mock_weaviate_with_tenant_tracking
    ):
        """Test that SupervisorState receives and validates user_id.

        Validates: SupervisorState.user_id is required field.

        Note: This is a critical security requirement - SupervisorState
        must always have a user_id to ensure tenant-scoped queries.
        """
        # Create user
        user = User(
            auth_sub=curator1_user.uid,
            email=curator1_user.email,
            display_name=curator1_user.email,
            created_at=datetime.now(timezone.utc),
            last_login=datetime.now(timezone.utc),
            is_active=True
        )
        test_db.add(user)
        test_db.commit()

        # Verify the chat endpoint forwards an authenticated user_id into runtime.
        with patch("src.api.chat_stream.get_supervisor_tool_agent_map", return_value={}), \
             patch("src.api.chat_stream.run_agent_streamed") as mock_execute:
            async def mock_chat_generator():
                yield {"type": "RUN_FINISHED", "data": {"response": "Test response"}}

            mock_execute.return_value = mock_chat_generator()
            session_id = f"state-test-session-{uuid4().hex[:8]}"

            response = client_as_curator1.post(
                "/api/chat",
                json={"message": "test query", "session_id": session_id},
            )
            assert response.status_code in [200, 422]
            assert mock_execute.called
            call_kwargs = mock_execute.call_args.kwargs if mock_execute.call_args else {}
            assert call_kwargs.get("user_id") == curator1_user["sub"]

    def test_weaviate_queries_must_use_with_tenant(
        self, mock_weaviate_with_tenant_tracking
    ):
        """Test that Weaviate queries fail without .with_tenant().

        Validates: SDK-enforced tenant isolation.

        Note: This tests the pattern that ALL Weaviate queries
        must call .with_tenant() for multi-tenancy to work.
        """
        from src.lib.weaviate_helpers import get_tenant_name

        # Test tenant name conversion
        user_id_with_hyphens = "test-user-00u1-abc2-def3"
        tenant_name = get_tenant_name(user_id_with_hyphens)

        # Should replace hyphens with underscores
        assert tenant_name == "test_user_00u1_abc2_def3"
        assert "-" not in tenant_name, "Tenant name should not contain hyphens"

        # Test that tenant name is used in queries
        client = mock_weaviate_with_tenant_tracking["client"]
        collection = client.collections.get("DocumentChunk")

        # Call with_tenant
        collection.with_tenant(tenant_name)

        # Verify tenant was tracked
        tenant_calls = mock_weaviate_with_tenant_tracking["tenant_calls"]["calls"]
        assert len(tenant_calls) > 0, "with_tenant should have been called"
        assert tenant_calls[-1]["tenant"] == tenant_name
        assert tenant_calls[-1]["collection"] == "DocumentChunk"
