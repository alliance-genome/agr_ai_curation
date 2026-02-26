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
OWNER_KEY_PREFIX = "chat:owner:"
CANCEL_TTL_SECONDS = 300  # 5 minutes - cleanup stale keys


def _build_owner_value(user_id: str, stream_token: Optional[str]) -> str:
    if stream_token:
        return f"{user_id}|{stream_token}"
    return user_id


def _owner_user(owner_value: Optional[str]) -> Optional[str]:
    if not owner_value:
        return None
    return owner_value.split("|", 1)[0]


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


async def register_active_stream(
    session_id: str,
    user_id: Optional[str] = None,
    stream_token: Optional[str] = None,
) -> bool:
    """Register that a stream is active for a session.

    This helps track which sessions have active streams.

    Args:
        session_id: The chat session ID
    """
    try:
        client = await get_redis()
        owner_key = f"{OWNER_KEY_PREFIX}{session_id}"
        active_key = f"chat:active:{session_id}"

        if user_id:
            owner_value = _build_owner_value(user_id, stream_token)
            claimed = await client.set(
                owner_key,
                owner_value,
                ex=CANCEL_TTL_SECONDS,
                nx=True,
            )
            if not claimed:
                existing_owner = await client.get(owner_key)
                existing_owner_user = _owner_user(existing_owner)
                if existing_owner_user and existing_owner_user != user_id:
                    logger.warning(
                        "Refusing to register session %s for user %s (owned by %s)",
                        session_id, user_id, existing_owner
                    )
                    return False
                # Same user may reconnect; refresh owner with current stream token.
                await client.setex(owner_key, CANCEL_TTL_SECONDS, owner_value)

        await client.setex(active_key, CANCEL_TTL_SECONDS, "1")
        return True
    except Exception as e:
        logger.error('Failed to register active stream: %s', e)
        # Degrade gracefully to avoid blocking the request path on Redis outages.
        return True


async def unregister_active_stream(
    session_id: str,
    user_id: Optional[str] = None,
    stream_token: Optional[str] = None,
) -> None:
    """Unregister an active stream for a session.

    Args:
        session_id: The chat session ID
    """
    try:
        client = await get_redis()
        key = f"chat:active:{session_id}"
        owner_key = f"{OWNER_KEY_PREFIX}{session_id}"

        if user_id and stream_token:
            # Atomic exact-owner compare-and-delete to avoid deleting newer stream keys.
            expected_owner = _build_owner_value(user_id, stream_token)
            script = """
            local active_key = KEYS[1]
            local owner_key = KEYS[2]
            local expected_owner = ARGV[1]
            local owner = redis.call('GET', owner_key)
            if owner ~= expected_owner then
                return 0
            end
            redis.call('DEL', active_key)
            redis.call('DEL', owner_key)
            return 1
            """
            await client.eval(script, 2, key, owner_key, expected_owner)
        elif user_id:
            # Atomic compare-and-delete to avoid deleting a newer owner's keys.
            script = """
            local active_key = KEYS[1]
            local owner_key = KEYS[2]
            local expected_owner = ARGV[1]
            local owner = redis.call('GET', owner_key)
            local owner_user = owner
            if owner then
                local sep = string.find(owner, '|', 1, true)
                if sep then
                    owner_user = string.sub(owner, 1, sep - 1)
                end
            end
            if owner and owner_user ~= expected_owner then
                return 0
            end
            redis.call('DEL', active_key)
            redis.call('DEL', owner_key)
            return 1
            """
            await client.eval(script, 2, key, owner_key, user_id)
        else:
            await client.delete(key)
            await client.delete(owner_key)
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


async def get_stream_owner(session_id: str) -> Optional[str]:
    """Get the owner user ID for an active session stream, if known."""
    try:
        client = await get_redis()
        key = f"{OWNER_KEY_PREFIX}{session_id}"
        return _owner_user(await client.get(key))
    except Exception as e:
        logger.error('Failed to get stream owner: %s', e)
        return None
