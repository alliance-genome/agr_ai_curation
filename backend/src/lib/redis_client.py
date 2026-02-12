"""Redis client for cross-worker state management.

This module provides a shared Redis connection for managing state
that needs to be accessible across multiple worker processes,
such as stream cancellation signals.
"""

import logging
import os
from typing import Optional

import redis.asyncio as redis

logger = logging.getLogger(__name__)

# Global async Redis client
_redis_client: Optional[redis.Redis] = None


def get_redis_url() -> str:
    """Get Redis URL from environment."""
    return os.getenv("REDIS_URL", "redis://localhost:6379/0")


async def get_redis() -> redis.Redis:
    """Get or create the async Redis client.

    Returns:
        Async Redis client instance
    """
    global _redis_client

    if _redis_client is None:
        redis_url = get_redis_url()
        logger.info('Connecting to Redis at %s', redis_url)
        _redis_client = redis.from_url(
            redis_url,
            encoding="utf-8",
            decode_responses=True
        )

    return _redis_client


async def close_redis() -> None:
    """Close the Redis connection."""
    global _redis_client

    if _redis_client is not None:
        await _redis_client.close()
        _redis_client = None
        logger.info("Redis connection closed")


# Stream cancellation helpers
CANCEL_KEY_PREFIX = "chat:cancel:"
CANCEL_TTL_SECONDS = 300  # 5 minutes - cleanup stale keys


async def set_cancel_signal(session_id: str) -> bool:
    """Set a cancellation signal for a session.

    Args:
        session_id: The chat session ID to cancel

    Returns:
        True if signal was set successfully
    """
    try:
        client = await get_redis()
        key = f"{CANCEL_KEY_PREFIX}{session_id}"
        await client.setex(key, CANCEL_TTL_SECONDS, "1")
        logger.info('Set cancel signal for session %s', session_id)
        return True
    except Exception as e:
        logger.error('Failed to set cancel signal: %s', e)
        return False


async def check_cancel_signal(session_id: str) -> bool:
    """Check if a cancellation signal exists for a session.

    Args:
        session_id: The chat session ID to check

    Returns:
        True if session should be cancelled
    """
    try:
        client = await get_redis()
        key = f"{CANCEL_KEY_PREFIX}{session_id}"
        result = await client.get(key)
        return result is not None
    except Exception as e:
        logger.error('Failed to check cancel signal: %s', e)
        return False


async def clear_cancel_signal(session_id: str) -> None:
    """Clear the cancellation signal for a session.

    Args:
        session_id: The chat session ID to clear
    """
    try:
        client = await get_redis()
        key = f"{CANCEL_KEY_PREFIX}{session_id}"
        await client.delete(key)
    except Exception as e:
        logger.error('Failed to clear cancel signal: %s', e)


async def register_active_stream(session_id: str) -> None:
    """Register that a stream is active for a session.

    This helps track which sessions have active streams.

    Args:
        session_id: The chat session ID
    """
    try:
        client = await get_redis()
        key = f"chat:active:{session_id}"
        await client.setex(key, CANCEL_TTL_SECONDS, "1")
    except Exception as e:
        logger.error('Failed to register active stream: %s', e)


async def unregister_active_stream(session_id: str) -> None:
    """Unregister an active stream for a session.

    Args:
        session_id: The chat session ID
    """
    try:
        client = await get_redis()
        key = f"chat:active:{session_id}"
        await client.delete(key)
    except Exception as e:
        logger.error('Failed to unregister active stream: %s', e)


async def is_stream_active(session_id: str) -> bool:
    """Check if a stream is currently active for a session.

    Args:
        session_id: The chat session ID to check

    Returns:
        True if stream is active
    """
    try:
        client = await get_redis()
        key = f"chat:active:{session_id}"
        result = await client.get(key)
        return result is not None
    except Exception as e:
        logger.error('Failed to check active stream: %s', e)
        return False
