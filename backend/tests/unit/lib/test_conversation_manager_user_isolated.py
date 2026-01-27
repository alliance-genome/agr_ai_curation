"""Unit tests for user-isolated ConversationManager.

Task: FR-014 - User-specific data isolation
Tests the user-isolated conversation memory implementation.

CRITICAL: These tests verify that:
1. Different users have isolated session storage with unique session IDs
2. Reset only affects the target user
3. Memory stats are user-specific
4. SessionAccessError is raised for cross-user access to same session ID
5. Thread-safe concurrent access

Design Decision: Session IDs are globally unique. If User A creates "session1",
User B cannot create a session with the same ID. This is STRICT isolation.
"""

import pytest
import threading
from collections import deque
from unittest.mock import patch

from src.lib.conversation_manager import ConversationManager, SessionAccessError


@pytest.fixture
def manager():
    """Create a fresh ConversationManager for each test."""
    return ConversationManager()


@pytest.fixture
def manager_with_data(manager):
    """Create a manager with pre-populated data for multiple users.

    Uses UNIQUE session IDs per user to avoid cross-user conflicts.
    """
    # User A adds 3 exchanges in session_a1
    for i in range(3):
        manager.add_exchange(
            user_id="user_a",
            session_id="session_a1",
            user_message=f"User A message {i}",
            assistant_response=f"Assistant response to A {i}"
        )

    # User B adds 1 exchange in session_b1 (different session ID)
    manager.add_exchange(
        user_id="user_b",
        session_id="session_b1",
        user_message="User B message",
        assistant_response="Assistant response to B"
    )

    # User A adds to session_a2
    manager.add_exchange(
        user_id="user_a",
        session_id="session_a2",
        user_message="User A session2 message",
        assistant_response="Assistant response to A session2"
    )

    return manager


class TestUserIsolatedSessions:
    """Tests for user-isolated session storage."""

    def test_different_users_have_isolated_sessions(self, manager):
        """Test that User A's history is not visible to User B.

        Validates FR-014: User-specific data isolation.
        Uses unique session IDs to avoid collision.
        """
        # User A adds history in their session
        history_a = manager.get_session_history("user_a", "user_a_session")
        history_a.append({"role": "user", "content": "Hello from A"})

        # User B should have empty history for their own session
        history_b = manager.get_session_history("user_b", "user_b_session")
        assert len(history_b) == 0, "User B should have empty session"

        # User A's history should still have their message
        assert len(history_a) == 1, "User A should have 1 message"
        assert history_a[0]["content"] == "Hello from A"

    def test_session_id_collision_raises_error(self, manager):
        """Test that same session ID from different users raises error.

        This is STRICT isolation - session IDs are globally unique.
        """
        # User A creates a session
        manager.add_exchange("user_a", "shared_session", "A's message", "Response to A")

        # User B tries to use the same session ID - should raise error
        with pytest.raises(SessionAccessError) as exc_info:
            manager.add_exchange("user_b", "shared_session", "B's message", "Response to B")

        assert "belongs to different user" in str(exc_info.value)

    def test_user_cannot_access_other_users_sessions(self, manager_with_data):
        """Test that users cannot access each other's sessions.

        Validates FR-014: Access control for session ownership.
        """
        # User A should access their own session fine
        stats_a = manager_with_data.get_session_stats("user_a", "session_a1")
        assert stats_a["exchange_count"] == 3

        # User B should NOT be able to access user A's session
        with pytest.raises(SessionAccessError):
            manager_with_data.get_session_stats("user_b", "session_a1")


class TestResetIsolation:
    """Tests for user-isolated reset functionality."""

    def test_reset_only_affects_target_user(self, manager_with_data):
        """Test that resetting User A doesn't affect User B.

        Validates FR-014: Reset is user-scoped.
        """
        # Verify both users have data
        stats_a_before = manager_with_data.get_memory_stats("user_a")
        stats_b_before = manager_with_data.get_memory_stats("user_b")

        assert stats_a_before["memory_sizes"]["short_term"]["file_count"] > 0
        assert stats_b_before["memory_sizes"]["short_term"]["file_count"] > 0

        # Reset user A
        result = manager_with_data.reset_conversation("user_a")
        assert result is True

        # User A should be empty
        stats_a_after = manager_with_data.get_memory_stats("user_a")
        assert stats_a_after["memory_sizes"]["short_term"]["file_count"] == 0, \
            "User A should have 0 exchanges after reset"
        assert stats_a_after["session_count"] == 0, \
            "User A should have 0 sessions after reset"

        # User B should still have their data
        stats_b_after = manager_with_data.get_memory_stats("user_b")
        assert stats_b_after["memory_sizes"]["short_term"]["file_count"] == 1, \
            "User B should still have 1 exchange"
        assert stats_b_after["session_count"] == 1, \
            "User B should still have 1 session"

    def test_reset_nonexistent_user_succeeds(self, manager):
        """Test that resetting a nonexistent user returns success (idempotent)."""
        result = manager.reset_conversation("nonexistent_user")
        assert result is True, "Reset should succeed even for nonexistent user"


class TestMemoryStatsUserSpecific:
    """Tests for user-specific memory statistics."""

    def test_memory_stats_are_user_specific(self, manager):
        """Test that stats are per-user, not global.

        Validates FR-014: User-specific statistics.
        Uses unique session IDs to avoid collision.
        """
        # User A adds 3 exchanges in their session
        for i in range(3):
            manager.add_exchange("user_a", "session_a", f"A msg {i}", f"Response {i}")

        # User B adds 1 exchange in their session
        manager.add_exchange("user_b", "session_b", "B msg", "Response")

        stats_a = manager.get_memory_stats("user_a")
        stats_b = manager.get_memory_stats("user_b")

        assert stats_a["memory_sizes"]["short_term"]["file_count"] == 3, \
            "User A should have 3 exchanges"
        assert stats_b["memory_sizes"]["short_term"]["file_count"] == 1, \
            "User B should have 1 exchange"

        # Verify user_id is in stats
        assert stats_a["user_id"] == "user_a"
        assert stats_b["user_id"] == "user_b"

    def test_all_sessions_stats_are_user_specific(self, manager_with_data):
        """Test that get_all_sessions_stats returns only user's sessions."""
        stats_a = manager_with_data.get_all_sessions_stats("user_a")
        stats_b = manager_with_data.get_all_sessions_stats("user_b")

        # User A has 2 sessions (session_a1 and session_a2)
        assert stats_a["total_sessions"] == 2
        assert "session_a1" in stats_a["sessions"]
        assert "session_a2" in stats_a["sessions"]

        # User B has 1 session
        assert stats_b["total_sessions"] == 1
        assert "session_b1" in stats_b["sessions"]

        # User B should NOT see user A's sessions
        assert "session_a1" not in stats_b["sessions"]
        assert "session_a2" not in stats_b["sessions"]

    def test_global_stats_without_user_id(self, manager_with_data):
        """Test that global stats (no user_id) returns aggregate data."""
        global_stats = manager_with_data.get_memory_stats(user_id=None)

        # Should show totals across all users
        assert "total_users" in global_stats
        assert "total_sessions" in global_stats
        assert global_stats["total_users"] == 2
        assert global_stats["total_sessions"] == 3  # 2 for user_a, 1 for user_b


class TestSessionAccessError:
    """Tests for SessionAccessError on cross-user access attempts."""

    def test_session_access_error_on_cross_user_clear(self, manager):
        """Test that clearing another user's session raises SessionAccessError."""
        # User A creates a session
        manager.add_exchange("user_a", "private_session", "msg", "response")

        # User B tries to clear User A's session - should raise error
        with pytest.raises(SessionAccessError) as exc_info:
            manager.clear_session_history("user_b", "private_session")

        assert "belongs to different user" in str(exc_info.value)
        assert "Access denied" in str(exc_info.value)

    def test_session_access_error_on_cross_user_get_stats(self, manager):
        """Test that getting another user's session stats raises SessionAccessError."""
        # User A creates a session
        manager.add_exchange("user_a", "private_session", "msg", "response")

        # User B tries to get stats for User A's session - should raise error
        with pytest.raises(SessionAccessError) as exc_info:
            manager.get_session_stats("user_b", "private_session")

        assert "belongs to different user" in str(exc_info.value)

    def test_session_access_error_on_cross_user_get_history(self, manager):
        """Test that getting another user's session history raises SessionAccessError."""
        # User A creates a session
        manager.add_exchange("user_a", "private_session", "msg", "response")

        # User B tries to get history for User A's session - should raise error
        with pytest.raises(SessionAccessError) as exc_info:
            manager.get_session_history("user_b", "private_session")

        assert "belongs to different user" in str(exc_info.value)

    def test_session_access_error_on_cross_user_add_exchange(self, manager):
        """Test that adding to another user's session raises SessionAccessError."""
        # User A creates a session
        manager.add_exchange("user_a", "private_session", "msg", "response")

        # User B tries to add to User A's session - should raise error
        with pytest.raises(SessionAccessError) as exc_info:
            manager.add_exchange("user_b", "private_session", "B msg", "B response")

        assert "belongs to different user" in str(exc_info.value)


class TestHistoryForPrompt:
    """Tests for user-specific history formatted for prompts."""

    def test_history_for_prompt_is_user_specific(self, manager_with_data):
        """Test that get_history_for_prompt only returns user's own history."""
        prompt_a = manager_with_data.get_history_for_prompt("user_a", "session_a1")
        prompt_b = manager_with_data.get_history_for_prompt("user_b", "session_b1")

        # User A should see their 3 exchanges
        assert "User A message 0" in prompt_a
        assert "User A message 1" in prompt_a
        assert "User A message 2" in prompt_a
        assert "User B message" not in prompt_a

        # User B should see their 1 exchange
        assert "User B message" in prompt_b
        assert "User A message" not in prompt_b

    def test_history_for_prompt_cross_user_raises_error(self, manager):
        """Test that cross-user history access raises SessionAccessError."""
        # User A creates a session
        manager.add_exchange("user_a", "private_session", "A msg", "A response")

        # User B trying to get history for User A's session should raise error
        with pytest.raises(SessionAccessError):
            manager.get_history_for_prompt("user_b", "private_session")

    def test_history_for_prompt_empty_session(self, manager):
        """Test that empty session returns empty string."""
        prompt = manager.get_history_for_prompt("new_user", "new_session")
        assert prompt == "", "Empty session should return empty string"


class TestThreadSafety:
    """Tests for thread-safe concurrent access."""

    def test_concurrent_users_dont_interfere(self, manager):
        """Test that concurrent access from multiple users is safe.

        Validates: Thread-safe multi-user access.
        Uses unique session IDs per user to avoid collision.
        """
        errors = []
        results = {"user_a": [], "user_b": []}

        def user_a_work():
            try:
                for i in range(100):
                    manager.add_exchange("user_a", "session_a", f"A{i}", f"Response{i}")
                history = manager.get_session_history("user_a", "session_a")
                results["user_a"].append(len(history))
            except Exception as e:
                errors.append(f"User A error: {e}")

        def user_b_work():
            try:
                for i in range(100):
                    manager.add_exchange("user_b", "session_b", f"B{i}", f"Response{i}")
                history = manager.get_session_history("user_b", "session_b")
                results["user_b"].append(len(history))
            except Exception as e:
                errors.append(f"User B error: {e}")

        # Run concurrent threads
        threads = [
            threading.Thread(target=user_a_work),
            threading.Thread(target=user_b_work),
        ]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # No errors should occur
        assert len(errors) == 0, f"Concurrent access errors: {errors}"

        # Each user should have exactly 100 exchanges (capped by maxlen)
        # Note: maxlen is 20 by default, so we expect min(100, 20) = 20
        max_exchanges = manager.max_exchanges

        stats_a = manager.get_session_stats("user_a", "session_a")
        stats_b = manager.get_session_stats("user_b", "session_b")

        expected_count = min(100, max_exchanges)
        assert stats_a["exchange_count"] == expected_count
        assert stats_b["exchange_count"] == expected_count

        # Verify isolation - A's history should only have A's messages
        for exchange in stats_a["history"]:
            assert exchange["user"].startswith("A"), \
                f"User A's history contains non-A message: {exchange}"

        for exchange in stats_b["history"]:
            assert exchange["user"].startswith("B"), \
                f"User B's history contains non-B message: {exchange}"


class TestLRUEviction:
    """Tests for per-user LRU session eviction."""

    def test_lru_eviction_is_per_user(self, manager):
        """Test that LRU eviction only affects the user's own sessions."""
        # Set a low max for testing
        manager.max_sessions_per_user = 3

        # User A creates 4 sessions (should evict oldest)
        for i in range(4):
            manager.add_exchange("user_a", f"session_a_{i}", f"msg{i}", f"resp{i}")

        # User B creates 2 sessions (unique session IDs)
        for i in range(2):
            manager.add_exchange("user_b", f"session_b_{i}", f"msg{i}", f"resp{i}")

        stats_a = manager.get_all_sessions_stats("user_a")
        stats_b = manager.get_all_sessions_stats("user_b")

        # User A should have 3 sessions (oldest evicted)
        assert stats_a["total_sessions"] == 3
        assert "session_a_0" not in stats_a["sessions"], "session_a_0 should be evicted"
        assert "session_a_1" in stats_a["sessions"]
        assert "session_a_2" in stats_a["sessions"]
        assert "session_a_3" in stats_a["sessions"]

        # User B should have 2 sessions (no eviction needed)
        assert stats_b["total_sessions"] == 2
        assert "session_b_0" in stats_b["sessions"]
        assert "session_b_1" in stats_b["sessions"]


class TestAddExchange:
    """Tests for add_exchange method."""

    def test_add_exchange_requires_user_id(self, manager):
        """Test that add_exchange properly handles user_id."""
        # Add exchange with user_id
        manager.add_exchange("user_a", "session1", "Hello", "Hi there")

        # Verify it was added to correct user's session
        stats = manager.get_session_stats("user_a", "session1")
        assert stats["exchange_count"] == 1
        assert stats["user_id"] == "user_a"
        assert stats["history"][0]["user"] == "Hello"
        assert stats["history"][0]["assistant"] == "Hi there"

    def test_add_exchange_includes_timestamp(self, manager):
        """Test that exchanges include timestamp."""
        manager.add_exchange("user_a", "session1", "Hello", "Hi there")

        stats = manager.get_session_stats("user_a", "session1")
        assert "timestamp" in stats["history"][0]

    @patch.dict("os.environ", {"CHAT_HISTORY_ENABLED": "false"})
    def test_add_exchange_respects_disabled_history(self):
        """Test that add_exchange does nothing when history is disabled."""
        manager = ConversationManager()
        manager.add_exchange("user_a", "session1", "Hello", "Hi there")

        # Should not create any session since history is disabled
        stats = manager.get_memory_stats("user_a")
        # With history disabled, the session might not be created
        # The exchange count should be 0
        assert stats["memory_sizes"]["short_term"]["file_count"] == 0


class TestClearSessionHistory:
    """Tests for clear_session_history method."""

    def test_clear_session_history_user_specific(self, manager_with_data):
        """Test that clearing session only affects owner's session."""
        # Clear user A's session_a1
        manager_with_data.clear_session_history("user_a", "session_a1")

        # User A's session_a1 should be gone
        stats_a = manager_with_data.get_all_sessions_stats("user_a")
        assert "session_a1" not in stats_a["sessions"]
        assert "session_a2" in stats_a["sessions"]  # Other sessions preserved

        # User B's session should still exist
        stats_b = manager_with_data.get_all_sessions_stats("user_b")
        assert "session_b1" in stats_b["sessions"]

    def test_clear_nonexistent_session_succeeds(self, manager):
        """Test that clearing nonexistent session succeeds (idempotent)."""
        # Should not raise an error
        manager.clear_session_history("user_a", "nonexistent_session")


class TestSessionOwnershipValidation:
    """Tests for _validate_session_ownership method."""

    def test_validate_session_ownership_returns_owner(self, manager):
        """Test that validation returns owner's user_id for existing sessions."""
        # Create session for user A
        manager.add_exchange("user_a", "session1", "msg", "resp")

        # User A should be returned as owner
        owner = manager._validate_session_ownership("user_a", "session1")
        assert owner == "user_a"

    def test_validate_session_ownership_returns_none_for_new_session(self, manager):
        """Test that validation returns None for non-existent sessions."""
        owner = manager._validate_session_ownership("user_a", "new_session")
        assert owner is None

    def test_validate_session_ownership_raises_for_wrong_user(self, manager):
        """Test that validation raises for wrong user."""
        # Create session for user A
        manager.add_exchange("user_a", "session1", "msg", "resp")

        # User B should get error
        with pytest.raises(SessionAccessError):
            manager._validate_session_ownership("user_b", "session1")
