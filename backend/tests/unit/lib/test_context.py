"""Unit tests for src.lib.context module.

Tests for request-scoped context variables used by file output tools.
"""
import pytest

from src.lib.context import (
    set_current_trace_id,
    get_current_trace_id,
    set_current_session_id,
    get_current_session_id,
    set_current_user_id,
    get_current_user_id,
    clear_context,
)


class TestContextVariables:
    """Tests for context variable get/set functions."""

    def setup_method(self):
        """Clear context before each test."""
        clear_context()

    def teardown_method(self):
        """Clear context after each test."""
        clear_context()

    def test_trace_id_default_is_none(self):
        """trace_id should be None by default."""
        assert get_current_trace_id() is None

    def test_trace_id_can_be_set_and_retrieved(self):
        """trace_id can be set and retrieved."""
        set_current_trace_id("abc123def456")
        assert get_current_trace_id() == "abc123def456"

    def test_session_id_default_is_none(self):
        """session_id should be None by default."""
        assert get_current_session_id() is None

    def test_session_id_can_be_set_and_retrieved(self):
        """session_id can be set and retrieved."""
        set_current_session_id("session-uuid-1234")
        assert get_current_session_id() == "session-uuid-1234"

    def test_user_id_default_is_none(self):
        """user_id should be None by default."""
        assert get_current_user_id() is None

    def test_user_id_can_be_set_and_retrieved(self):
        """user_id can be set and retrieved."""
        set_current_user_id("user@example.com")
        assert get_current_user_id() == "user@example.com"

    def test_clear_context_resets_all_values(self):
        """clear_context resets all context variables to None."""
        set_current_trace_id("trace-123")
        set_current_session_id("session-456")
        set_current_user_id("user-789")

        clear_context()

        assert get_current_trace_id() is None
        assert get_current_session_id() is None
        assert get_current_user_id() is None

    def test_context_vars_are_independent(self):
        """Each context variable is independent of others."""
        set_current_trace_id("trace-only")

        assert get_current_trace_id() == "trace-only"
        assert get_current_session_id() is None
        assert get_current_user_id() is None

    def test_overwriting_context_value(self):
        """Setting a context value twice overwrites the previous value."""
        set_current_trace_id("first-trace")
        assert get_current_trace_id() == "first-trace"

        set_current_trace_id("second-trace")
        assert get_current_trace_id() == "second-trace"
