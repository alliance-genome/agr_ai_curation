"""Shared state for chat interactions.

Task: T034 - Update DocumentSelectionState to per-user dictionary
Requirements: FR-014 (user-specific data isolation), data-model.md:342-388
"""

from __future__ import annotations

from threading import RLock
from typing import Optional, Dict, Any


class DocumentSelectionState:
    """Thread-safe per-user document state storage.

    **CRITICAL**: This class now stores documents per-user to prevent concurrent
    users from overwriting each other's active document selections.

    Previous Implementation (INSECURE):
        - _document: Optional[Dict[str, Any]] = None  # Shared across ALL users!

    Current Implementation (SECURE):
        - _user_documents: Dict[str, Optional[Dict[str, Any]]]  # Keyed by user_id

    Requirements:
        - FR-014: User-specific data isolation
        - All API endpoints must call with user_id parameter
        - Thread-safe for concurrent multi-user access
    """

    def __init__(self) -> None:
        self._lock = RLock()
        self._user_documents: Dict[str, Optional[Dict[str, Any]]] = {}

    def set_document(self, user_id: str, document: Dict[str, Any]) -> None:
        """Set active document for specific user.

        Args:
            user_id: User identifier (user_id from authenticated request)
            document: Document metadata dictionary

        Thread Safety:
            Multiple users can set different documents concurrently without
            overwriting each other's selections.
        """
        with self._lock:
            # Store a shallow copy to avoid accidental external mutation
            self._user_documents[user_id] = dict(document)

    def get_document(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Get active document for specific user.

        Args:
            user_id: User identifier (user_id from authenticated request)

        Returns:
            Document metadata dict if user has an active document, None otherwise
        """
        with self._lock:
            doc = self._user_documents.get(user_id)
            return dict(doc) if doc is not None else None

    def clear_document(self, user_id: str) -> None:
        """Clear active document for specific user.

        Args:
            user_id: User identifier (user_id from authenticated request)
        """
        with self._lock:
            self._user_documents.pop(user_id, None)


# Module-level singleton (now safe for multi-user)
document_state = DocumentSelectionState()

__all__ = ["document_state", "DocumentSelectionState"]
