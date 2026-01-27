"""Conversation memory management for chat history.

Task: User-Isolated Conversation Memory
Requirements: FR-014 (user-specific data isolation)

**CRITICAL**: This class stores conversation history per-user to prevent
concurrent users from accessing each other's chat sessions.

Previous Implementation (INSECURE):
    - _sessions: OrderedDict[str, deque]  # Shared across ALL users!

Current Implementation (SECURE):
    - _user_sessions: Dict[str, OrderedDict[str, deque]]  # Keyed by user_id

Pattern follows DocumentSelectionState in chat_state.py.
"""

import os
import logging
import threading
from typing import Dict, Optional, List
from datetime import datetime
from collections import OrderedDict, deque

logger = logging.getLogger(__name__)


class SessionAccessError(Exception):
    """Raised when user tries to access a session they don't own."""
    pass


class ConversationManager:
    """Thread-safe per-user conversation history storage.

    **CRITICAL**: This class stores conversation history per-user to prevent
    concurrent users from accessing each other's chat sessions.

    Requirements:
        - FR-014: User-specific data isolation
        - All API endpoints must provide user_id parameter
        - Thread-safe for concurrent multi-user access
    """

    def __init__(self):
        """Initialize conversation manager with per-user session storage."""
        # Configuration from environment
        self.history_enabled = os.getenv('CHAT_HISTORY_ENABLED', 'true').lower() == 'true'
        self.max_exchanges = int(os.getenv('CHAT_MAX_HISTORY_EXCHANGES', '20'))
        self.include_in_routing = os.getenv('CHAT_HISTORY_IN_ROUTING', 'true').lower() == 'true'
        self.include_in_response = os.getenv('CHAT_HISTORY_IN_RESPONSE', 'true').lower() == 'true'

        # Per-user session limits
        self.max_sessions_per_user = int(os.getenv('CHAT_MAX_SESSIONS_PER_USER', '50'))

        # Thread-safe storage for chat history
        # Structure: {user_id: OrderedDict[session_id, deque[exchange]]}
        self._sessions_lock = threading.RLock()
        self._user_sessions: Dict[str, OrderedDict[str, deque]] = {}

        # Track conversation state
        self._conversation_id: Optional[str] = None

        logger.info(
            f"Chat history initialized: enabled={self.history_enabled}, "
            f"max_exchanges={self.max_exchanges}, max_sessions_per_user={self.max_sessions_per_user}"
        )

    def _get_user_sessions(self, user_id: str) -> OrderedDict:
        """Get or create session dictionary for a user (internal, assumes lock held)."""
        if user_id not in self._user_sessions:
            self._user_sessions[user_id] = OrderedDict()
        return self._user_sessions[user_id]

    def _validate_session_ownership(
        self,
        user_id: str,
        session_id: str
    ) -> Optional[str]:
        """Check if session exists and who owns it.

        Args:
            user_id: User requesting access
            session_id: Session to check

        Returns:
            Owner's user_id if session exists, None if session doesn't exist

        Raises:
            SessionAccessError: If session belongs to different user
        """
        with self._sessions_lock:
            for uid, sessions in self._user_sessions.items():
                if session_id in sessions:
                    if uid != user_id:
                        raise SessionAccessError(
                            f"Session {session_id} belongs to different user. Access denied."
                        )
                    return uid
            return None

    def get_session_history(self, user_id: str, session_id: str) -> deque:
        """Get or create history deque for a user's session (thread-safe).

        Args:
            user_id: User identifier (Cognito sub claim)
            session_id: Session identifier

        Returns:
            Conversation history deque for the session

        Side Effects:
            - Creates session if it doesn't exist (claimed by user_id)
            - Moves session to end (LRU tracking)
            - May evict oldest session if user exceeds max_sessions_per_user

        Raises:
            SessionAccessError: If session belongs to different user
        """
        # Validate ownership if session exists
        owner = self._validate_session_ownership(user_id, session_id)
        if owner is not None and owner != user_id:
            raise SessionAccessError(
                f"Session {session_id} belongs to different user. Access denied."
            )

        with self._sessions_lock:
            user_sessions = self._get_user_sessions(user_id)

            if session_id not in user_sessions:
                # Create new session
                user_sessions[session_id] = deque(maxlen=self.max_exchanges)
                user_sessions.move_to_end(session_id)

                # Enforce per-user session limit (LRU eviction)
                while len(user_sessions) > self.max_sessions_per_user:
                    oldest_session = next(iter(user_sessions))
                    del user_sessions[oldest_session]
                    logger.debug(
                        f"Evicted oldest session for user {user_id[:8]}...: {oldest_session}"
                    )
            else:
                # Move to end (LRU)
                user_sessions.move_to_end(session_id)

            return user_sessions[session_id]

    def add_exchange(
        self,
        user_id: str,
        session_id: str,
        user_message: str,
        assistant_response: str
    ) -> None:
        """Add a user-assistant exchange to session history (thread-safe).

        Args:
            user_id: User identifier (Cognito sub claim)
            session_id: Session identifier
            user_message: User's message content
            assistant_response: Assistant's response content

        Raises:
            SessionAccessError: If session belongs to different user
        """
        if not self.history_enabled:
            return

        history = self.get_session_history(user_id, session_id)

        exchange = {
            'user': user_message,
            'assistant': assistant_response,
            'timestamp': datetime.utcnow().isoformat()
        }

        with self._sessions_lock:
            history.append(exchange)

        logger.debug(
            f"Added exchange to session {session_id} for user {user_id[:8]}..., "
            f"total exchanges: {len(history)}"
        )

    def get_history_for_prompt(
        self,
        user_id: str,
        session_id: str,
        task_type: str = 'routing'
    ) -> str:
        """Get formatted history for injection into task prompts.

        Args:
            user_id: User identifier (Cognito sub claim)
            session_id: Session identifier
            task_type: 'routing', 'response', or 'pdf_extraction'

        Returns:
            Formatted history string ready for prompt injection

        Raises:
            SessionAccessError: If session belongs to different user
        """
        if not self.history_enabled:
            return ""

        # Check configuration for this task type
        if task_type == 'routing' and not self.include_in_routing:
            return ""
        if task_type in ['response', 'pdf_extraction'] and not self.include_in_response:
            return ""

        history = self.get_session_history(user_id, session_id)

        if not history:
            return ""

        # Format history for prompt
        history_parts = []
        for exchange in history:
            history_parts.append(f"User: {exchange['user']}")
            history_parts.append(f"Assistant: {exchange['assistant']}")

        formatted_history = "\n".join(history_parts)

        # Return with clear section header
        return f"""
Previous conversation context:
{formatted_history}

Please consider this conversation history when generating your response.
"""

    def clear_session_history(self, user_id: str, session_id: str) -> None:
        """Clear history for a specific session (thread-safe).

        Args:
            user_id: User identifier (Cognito sub claim)
            session_id: Session identifier to clear

        Raises:
            SessionAccessError: If session belongs to different user
        """
        # Validate ownership
        owner = self._validate_session_ownership(user_id, session_id)
        if owner is not None and owner != user_id:
            raise SessionAccessError(
                f"Session {session_id} belongs to different user. Access denied."
            )

        with self._sessions_lock:
            user_sessions = self._get_user_sessions(user_id)
            if session_id in user_sessions:
                del user_sessions[session_id]
                logger.info(
                    f"Cleared chat history for session {session_id} "
                    f"(user {user_id[:8]}...)"
                )

    def get_session_stats(self, user_id: str, session_id: str) -> dict:
        """Get statistics for a specific session.

        Args:
            user_id: User identifier (Cognito sub claim)
            session_id: Session identifier

        Returns:
            Dict with session_id, user_id, exchange_count, max_exchanges, history

        Raises:
            SessionAccessError: If session belongs to different user
        """
        history = self.get_session_history(user_id, session_id)

        return {
            'session_id': session_id,
            'user_id': user_id,
            'exchange_count': len(history),
            'max_exchanges': self.max_exchanges,
            'history': list(history)
        }

    def get_all_sessions_stats(self, user_id: str) -> dict:
        """Get statistics for all sessions belonging to user.

        Args:
            user_id: User identifier (Cognito sub claim)

        Returns:
            Dict with total_sessions, session IDs, and user-specific stats
        """
        with self._sessions_lock:
            user_sessions = self._get_user_sessions(user_id)

            return {
                'total_sessions': len(user_sessions),
                'max_sessions': self.max_sessions_per_user,
                'history_enabled': self.history_enabled,
                'max_exchanges_per_session': self.max_exchanges,
                'sessions': list(user_sessions.keys()),
                'user_id': user_id
            }

    def reset_conversation(self, user_id: str) -> bool:
        """Reset conversation memory for specific user only.

        Args:
            user_id: User identifier (Cognito sub claim)

        Returns:
            True if successful, False on error

        Note:
            This clears ONLY the specified user's in-memory chat history.
            Other users' conversations are not affected.
        """
        try:
            with self._sessions_lock:
                if user_id in self._user_sessions:
                    session_count = len(self._user_sessions[user_id])
                    del self._user_sessions[user_id]
                    logger.info(
                        f"Conversation memory reset for user {user_id[:8]}... "
                        f"({session_count} sessions cleared)"
                    )
                else:
                    logger.info(f"No sessions to reset for user {user_id[:8]}...")

            return True

        except Exception as e:
            logger.error(f"Failed to reset conversation memory for user {user_id[:8]}...: {e}")
            return False

    def get_memory_stats(self, user_id: Optional[str] = None) -> dict:
        """Get statistics about current memory usage.

        Args:
            user_id: Optional user ID to get stats for. If None, returns global stats.

        Returns:
            Dict with memory statistics
        """
        stats = {
            "conversation_id": self._conversation_id,
            "memory_sizes": {}
        }

        try:
            with self._sessions_lock:
                if user_id:
                    # User-specific stats
                    user_sessions = self._user_sessions.get(user_id, {})
                    total_exchanges = sum(len(history) for history in user_sessions.values())
                    stats["memory_sizes"]["short_term"] = {
                        "file_count": total_exchanges,
                        "size_bytes": 0,
                        "size_mb": 0
                    }
                    stats["user_id"] = user_id
                    stats["session_count"] = len(user_sessions)
                else:
                    # Global stats (for monitoring)
                    total_users = len(self._user_sessions)
                    total_sessions = sum(
                        len(sessions) for sessions in self._user_sessions.values()
                    )
                    total_exchanges = sum(
                        sum(len(history) for history in sessions.values())
                        for sessions in self._user_sessions.values()
                    )
                    stats["memory_sizes"]["short_term"] = {
                        "file_count": total_exchanges,
                        "size_bytes": 0,
                        "size_mb": 0
                    }
                    stats["total_users"] = total_users
                    stats["total_sessions"] = total_sessions

                # Add empty stats for legacy directories (no longer used)
                for dir_name in ["long_term", "entity", "cache"]:
                    stats["memory_sizes"][dir_name] = {
                        "file_count": 0,
                        "size_bytes": 0,
                        "size_mb": 0
                    }

        except Exception as e:
            logger.error(f"Failed to get memory stats: {e}")

        return stats


# Global conversation manager instance
conversation_manager = ConversationManager()
