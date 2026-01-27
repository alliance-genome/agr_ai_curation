"""Context variables for request-scoped data.

This module provides context variables for tracking request-scoped data
across async boundaries. These are set by API endpoints and accessed by
tools during agent execution.

Usage:
    # In API endpoint (chat.py, executor.py):
    set_current_session_id(session_id)
    set_current_user_id(user_id)

    # In runner.py when trace is created:
    set_current_trace_id(trace_id)

    # In tools (file_output_tools.py):
    trace_id = get_current_trace_id()
    session_id = get_current_session_id()
    curator_id = get_current_user_id()

Note: These use contextvars which are properly isolated per async task.
Each request gets its own set of context variables.
"""

from contextvars import ContextVar
from typing import Optional

# Context variables set by API layer at start of each request
_current_trace_id: ContextVar[Optional[str]] = ContextVar('trace_id', default=None)
_current_session_id: ContextVar[Optional[str]] = ContextVar('session_id', default=None)
_current_user_id: ContextVar[Optional[str]] = ContextVar('user_id', default=None)


def set_current_trace_id(trace_id: str) -> None:
    """Set the current Langfuse trace ID.

    Called by runner.py when the Langfuse trace is created.

    Args:
        trace_id: Langfuse trace ID for the current request
    """
    _current_trace_id.set(trace_id)


def get_current_trace_id() -> Optional[str]:
    """Get the current Langfuse trace ID.

    Returns:
        The trace ID if set, None otherwise
    """
    return _current_trace_id.get()


def set_current_session_id(session_id: str) -> None:
    """Set the current chat session ID.

    Called by chat.py and flows/executor.py at request start.

    Args:
        session_id: UUID session identifier
    """
    _current_session_id.set(session_id)


def get_current_session_id() -> Optional[str]:
    """Get the current chat session ID.

    Returns:
        The session ID if set, None otherwise
    """
    return _current_session_id.get()


def set_current_user_id(user_id: str) -> None:
    """Set the current user/curator ID.

    Called by chat.py and flows/executor.py at request start.
    This is typically the Cognito sub claim.

    Args:
        user_id: User identifier (Cognito sub claim)
    """
    _current_user_id.set(user_id)


def get_current_user_id() -> Optional[str]:
    """Get the current user/curator ID.

    Returns:
        The user ID if set, None otherwise
    """
    return _current_user_id.get()


def clear_context() -> None:
    """Clear all context variables.

    Useful for testing or cleanup. In production, context variables
    are automatically isolated per request/async task.
    """
    _current_trace_id.set(None)
    _current_session_id.set(None)
    _current_user_id.set(None)
