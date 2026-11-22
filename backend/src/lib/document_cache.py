"""
Document metadata cache for reducing redundant Weaviate queries.

This module provides an in-memory TTL cache for document hierarchy data.
Every chat message would otherwise re-fetch this data from Weaviate, even though
the document hasn't changed.

Thread Safety:
    All cache operations are protected by RLock to prevent race conditions
    in FastAPI's async environment. This matches the pattern used by
    chat_state.py and other module-level runtime state in this codebase.

Cold preparations run independently. Active fill leases are revoked on
invalidation, preventing obsolete work from repopulating the cache. Leases
are removed on completion/cancellation; no per-document tombstones accumulate.
A missing (None) hierarchy is cached with the same TTL as a found one.

An entry records the readable files it was built from (their fingerprint); a
lookup for a different readable set misses, so a batch run (main file only)
and a chat run on the same paper never share a hierarchy, and a file that
became ready is seen at once.
"""

from contextlib import contextmanager
from dataclasses import dataclass
from threading import RLock
from typing import Dict, Any, Optional, Tuple
import time
import logging

logger = logging.getLogger(__name__)


@dataclass
class CachedDocumentMetadata:
    """Cached document hierarchy, for one set of readable files."""
    hierarchy: Optional[Dict[str, Any]]
    readable_fingerprint: Tuple[Tuple[str, int, int], ...]
    fetched_at: float


# Module-level cache: (user_id, document_id) -> CachedDocumentMetadata
_cache: Dict[Tuple[str, str], CachedDocumentMetadata] = {}

# Thread lock for safe concurrent access (matches pattern in chat_state.py)
_lock = RLock()

_pending_fills: Dict[Tuple[str, str], set] = {}


def get_cached_metadata(
    user_id: str,
    document_id: str,
    readable_fingerprint: Tuple[Tuple[str, int, int], ...],
) -> Optional[CachedDocumentMetadata]:
    """
    Get cached document metadata if available, not expired and built from the same readable files.

    Args:
        user_id: The user's ID for tenant isolation
        document_id: The document UUID
        readable_fingerprint: ``ReadableFiles.fingerprint`` of the run asking

    Returns:
        CachedDocumentMetadata if cache hit and not expired, None otherwise
    """
    from .openai_agents.config import get_document_context_cache_ttl_seconds

    key = (user_id, document_id)

    with _lock:
        cached = _cache.get(key)

        if cached and cached.readable_fingerprint != readable_fingerprint:
            logger.info('[DocumentCache] Cache MISS (other readable files) for document %s...', document_id[:8])
            return None

        if cached and (time.time() - cached.fetched_at) < get_document_context_cache_ttl_seconds():
            logger.info('[DocumentCache] Cache HIT for document %s...', document_id[:8])
            return cached

        if cached:
            # Entry exists but is expired - remove it atomically
            logger.info('[DocumentCache] Cache EXPIRED for document %s...', document_id[:8])
            del _cache[key]

    return None


def set_cached_metadata(
    user_id: str,
    document_id: str,
    hierarchy: Optional[Dict[str, Any]],
    readable_fingerprint: Tuple[Tuple[str, int, int], ...],
) -> None:
    """
    Cache document metadata for subsequent requests.

    Args:
        user_id: The user's ID for tenant isolation
        document_id: The document UUID
        hierarchy: The document hierarchy (sections, subsections)
        readable_fingerprint: ``ReadableFiles.fingerprint`` the hierarchy was built from
    """
    key = (user_id, document_id)

    with _lock:
        _cache[key] = CachedDocumentMetadata(
            hierarchy=hierarchy,
            readable_fingerprint=readable_fingerprint,
            fetched_at=time.time()
        )

    logger.info('[DocumentCache] Cached metadata for document %s...', document_id[:8])


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
        _pending_fills.pop(key, None)
        if key in _cache:
            del _cache[key]
            logger.info('[DocumentCache] Invalidated cache for document %s...', document_id[:8])


@contextmanager
def metadata_cache_fill(
    user_id: str,
    document_id: str,
    readable_fingerprint: Tuple[Tuple[str, int, int], ...],
):
    """Register a revocable fill; never hold the lock while fetching."""
    key = (user_id, document_id)
    token = object()
    with _lock:
        _pending_fills.setdefault(key, set()).add(token)

    def publish(hierarchy):
        with _lock:
            if token in _pending_fills.get(key, ()):
                set_cached_metadata(user_id, document_id, hierarchy, readable_fingerprint)

    try:
        yield publish
    finally:
        with _lock:
            tokens = _pending_fills.get(key)
            if tokens is not None:
                tokens.discard(token)
                if not tokens:
                    del _pending_fills[key]
