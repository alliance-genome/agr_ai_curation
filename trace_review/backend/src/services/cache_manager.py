"""
In-memory cache manager for trace data
Simple TTL-based cache with automatic expiration
"""
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, Any
import threading


class CacheEntry:
    """Single cache entry with TTL"""

    def __init__(self, data: Dict[str, Any], ttl_hours: float, cache_status: str):
        self.data = data
        self.cache_status = cache_status
        self.cached_at = datetime.now(timezone.utc)
        self.expires_at = self.cached_at + timedelta(hours=ttl_hours)

    def is_expired(self) -> bool:
        """Check if cache entry has expired"""
        return datetime.now(timezone.utc) > self.expires_at

    def to_dict(self) -> Dict[str, Any]:
        """Return data with metadata"""
        return {
            **self.data,
            "cached_at": self.cached_at.isoformat().replace("+00:00", "Z"),
            "expires_at": self.expires_at.isoformat().replace("+00:00", "Z")
        }


class CacheManager:
    """Thread-safe in-memory cache with TTL"""

    def __init__(self, ttl_hours: float = 1):
        self.cache: Dict[str, CacheEntry] = {}
        self.ttl_hours = ttl_hours
        self.lock = threading.Lock()

    def _get_active_entry(self, trace_id: str) -> Optional[CacheEntry]:
        entry = self.cache.get(trace_id)
        if not entry:
            return None

        if entry.is_expired():
            del self.cache[trace_id]
            return None

        return entry

    def get(self, trace_id: str) -> Optional[Dict[str, Any]]:
        """
        Get cached trace data by ID
        Returns None if not found or expired
        """
        with self.lock:
            entry = self._get_active_entry(trace_id)
            if entry is None:
                return None
            return entry.to_dict()

    def get_status(self, trace_id: str) -> Optional[str]:
        """Return the cached entry status when present."""
        with self.lock:
            entry = self._get_active_entry(trace_id)
            if entry is None:
                return None
            return entry.cache_status

    def set(
        self,
        trace_id: str,
        data: Dict[str, Any],
        *,
        cache_status: str = "stable",
        ttl_hours: Optional[float] = None,
        ttl_seconds: Optional[int] = None,
    ) -> None:
        """Store trace data in cache"""
        with self.lock:
            if ttl_seconds is not None:
                effective_ttl_hours = ttl_seconds / 3600
            elif ttl_hours is not None:
                effective_ttl_hours = ttl_hours
            else:
                effective_ttl_hours = self.ttl_hours

            self.cache[trace_id] = CacheEntry(data, effective_ttl_hours, cache_status)

    def clear_all(self) -> int:
        """
        Clear all cached data
        Returns number of entries removed
        """
        with self.lock:
            count = len(self.cache)
            self.cache.clear()
            return count

    def cleanup_expired(self) -> int:
        """
        Remove all expired entries
        Returns number of entries removed
        """
        with self.lock:
            expired_keys = [
                key for key, entry in self.cache.items()
                if entry.is_expired()
            ]
            for key in expired_keys:
                del self.cache[key]
            return len(expired_keys)

    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics"""
        with self.lock:
            total = len(self.cache)
            expired = sum(1 for entry in self.cache.values() if entry.is_expired())
            return {
                "total_entries": total,
                "active_entries": total - expired,
                "expired_entries": expired,
                "ttl_hours": self.ttl_hours
            }
