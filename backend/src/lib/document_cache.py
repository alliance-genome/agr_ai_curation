"""
Document metadata cache for reducing redundant Weaviate queries.

This module provides an in-memory TTL cache for document hierarchy and abstract data.
Every chat message would otherwise re-fetch this data from Weaviate, even though
the document hasn't changed.

Thread Safety:
    All cache operations are protected by RLock to prevent race conditions
    in FastAPI's async environment. This matches the pattern used by
    chat_state.py and conversation_manager.py in this codebase.

Usage:
    from src.lib.document_cache import get_cached_metadata, set_cached_metadata, invalidate_cache

    # Check cache first
    cached = get_cached_metadata(user_id, document_id)
    if cached:
        hierarchy = cached.hierarchy
        abstract = cached.abstract
    else:
        # Fetch from Weaviate
        hierarchy = fetch_document_hierarchy_sync(document_id, user_id)
        abstract = fetch_document_abstract_sync(document_id, user_id, hierarchy)
        # Cache for subsequent requests
        set_cached_metadata(user_id, document_id, hierarchy, abstract)

    # Invalidate on document load/reload
    invalidate_cache(user_id, document_id)

Performance Impact:
    Before: Every chat message → 2 Weaviate queries (hierarchy + abstract)
    After:  First message → 2 queries (cached), subsequent → 0 queries (cache hit)
"""

from dataclasses import dataclass
from threading import RLock
from typing import Dict, Any, Optional, Tuple
import time
import logging

logger = logging.getLogger(__name__)


@dataclass
class CachedDocumentMetadata:
    """Cached document hierarchy and abstract."""
    hierarchy: Optional[Dict[str, Any]]
    abstract: Optional[str]
    fetched_at: float


# Module-level cache: (user_id, document_id) -> CachedDocumentMetadata
_cache: Dict[Tuple[str, str], CachedDocumentMetadata] = {}

# Thread lock for safe concurrent access (matches pattern in chat_state.py)
_lock = RLock()

# Cache TTL in seconds (10 minutes)
_TTL_SECONDS = 600


def get_cached_metadata(user_id: str, document_id: str) -> Optional[CachedDocumentMetadata]:
    """
    Get cached document metadata if available and not expired.

    Args:
        user_id: The user's ID for tenant isolation
        document_id: The document UUID

    Returns:
        CachedDocumentMetadata if cache hit and not expired, None otherwise
    """
    key = (user_id, document_id)

    with _lock:
        cached = _cache.get(key)

        if cached and (time.time() - cached.fetched_at) < _TTL_SECONDS:
            logger.info(f"[DocumentCache] Cache HIT for document {document_id[:8]}...")
            return cached

        if cached:
            # Entry exists but is expired - remove it atomically
            logger.info(f"[DocumentCache] Cache EXPIRED for document {document_id[:8]}...")
            del _cache[key]

    return None


def set_cached_metadata(
    user_id: str,
    document_id: str,
    hierarchy: Optional[Dict[str, Any]],
    abstract: Optional[str]
) -> None:
    """
    Cache document metadata for subsequent requests.

    Args:
        user_id: The user's ID for tenant isolation
        document_id: The document UUID
        hierarchy: The document hierarchy (sections, subsections)
        abstract: The document abstract text
    """
    key = (user_id, document_id)

    with _lock:
        _cache[key] = CachedDocumentMetadata(
            hierarchy=hierarchy,
            abstract=abstract,
            fetched_at=time.time()
        )

    logger.info(f"[DocumentCache] Cached metadata for document {document_id[:8]}...")


def invalidate_cache(user_id: str, document_id: str) -> None:
    """
    Invalidate cache for a specific document.

    Call this when a document is loaded/reloaded/deleted to ensure fresh data.

    Args:
        user_id: The user's ID for tenant isolation
        document_id: The document UUID
    """
    key = (user_id, document_id)

    with _lock:
        if key in _cache:
            del _cache[key]
            logger.info(f"[DocumentCache] Invalidated cache for document {document_id[:8]}...")
