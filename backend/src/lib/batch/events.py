"""Batch event broadcasting for SSE streaming.

Provides an in-memory event broadcaster that allows the batch processor
(running in a background thread) to publish audit events that can be
consumed by the SSE endpoint.

Architecture:
    - BatchEventBroadcaster maintains a dict of batch_id -> list of subscribers
    - Each subscriber is an asyncio.Queue
    - Processor calls publish_sync() to send events to all subscribers
    - SSE endpoint calls subscribe() to get a queue, then reads from it

Thread Safety Notes:
    - Subscriber lists are copied before iteration to avoid race conditions
    - asyncio.Queue.put_nowait() is used for cross-thread communication
    - While asyncio.Queue is designed for single-threaded async code,
      put_nowait() is effectively thread-safe for appending due to CPython's GIL
    - Queues have a maxsize to prevent unbounded memory growth
"""
import asyncio
import logging
import threading
from collections import defaultdict
from typing import Any, Dict, List, Optional
from uuid import UUID

logger = logging.getLogger(__name__)

# Maximum events to queue per subscriber to prevent memory issues
DEFAULT_QUEUE_MAXSIZE = 1000


class BatchEventBroadcaster:
    """Thread-safe event broadcaster for batch processing.

    Allows background tasks to publish events that SSE endpoints can consume.
    Uses asyncio.Queue for async-safe communication with bounded queues to
    prevent memory issues.
    """

    def __init__(self, queue_maxsize: int = DEFAULT_QUEUE_MAXSIZE):
        # batch_id -> list of subscriber queues
        self._subscribers: Dict[str, List[asyncio.Queue]] = defaultdict(list)
        # Threading lock for thread-safe subscriber management (works from any thread)
        self._thread_lock = threading.RLock()
        # Queue maxsize for bounded memory usage
        self._queue_maxsize = queue_maxsize

    async def subscribe(self, batch_id: UUID) -> asyncio.Queue:
        """Subscribe to events for a batch.

        Args:
            batch_id: UUID of the batch to subscribe to

        Returns:
            asyncio.Queue that will receive events
        """
        with self._thread_lock:
            queue: asyncio.Queue = asyncio.Queue(maxsize=self._queue_maxsize)
            self._subscribers[str(batch_id)].append(queue)
            logger.debug("Subscriber added for batch %s (total: %d)",
                        batch_id, len(self._subscribers[str(batch_id)]))
            return queue

    async def unsubscribe(self, batch_id: UUID, queue: asyncio.Queue) -> None:
        """Unsubscribe from events for a batch.

        Args:
            batch_id: UUID of the batch
            queue: The queue returned from subscribe()
        """
        with self._thread_lock:
            batch_key = str(batch_id)
            if batch_key in self._subscribers:
                try:
                    self._subscribers[batch_key].remove(queue)
                    logger.debug("Subscriber removed for batch %s (remaining: %d)",
                                batch_id, len(self._subscribers[batch_key]))
                except ValueError:
                    pass  # Queue not in list

                # Cleanup empty subscriber lists
                if not self._subscribers[batch_key]:
                    del self._subscribers[batch_key]

    def publish(self, batch_id: UUID, event: Dict[str, Any]) -> None:
        """Publish an event to all subscribers of a batch.

        This method is thread-safe and can be called from sync or async code.

        Args:
            batch_id: UUID of the batch
            event: Event dict to publish
        """
        # Copy subscriber list under lock to prevent race conditions
        with self._thread_lock:
            batch_key = str(batch_id)
            subscribers = list(self._subscribers.get(batch_key, []))

        if not subscribers:
            return

        # Publish to each subscriber queue
        for queue in subscribers:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.debug("Event queue full for batch %s, dropping event", batch_id)
            except Exception as e:
                logger.debug("Could not publish to queue: %s", e)

    def publish_sync(self, batch_id: UUID, event: Dict[str, Any]) -> None:
        """Synchronous publish for use from background threads.

        This is the preferred method when calling from BackgroundTasks.
        Thread-safe: copies subscriber list before iteration.

        Note: asyncio.Queue.put_nowait() is effectively thread-safe for
        appending in CPython due to the GIL, though technically asyncio.Queue
        is designed for single-threaded async code.

        Args:
            batch_id: UUID of the batch
            event: Event dict to publish
        """
        # Copy subscriber list under lock to prevent race conditions
        with self._thread_lock:
            batch_key = str(batch_id)
            subscribers = list(self._subscribers.get(batch_key, []))

        if not subscribers:
            return

        for queue in subscribers:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.debug("Event queue full for batch %s, dropping event", batch_id)
            except Exception as e:
                logger.debug("Could not publish to queue: %s", e)

    def has_subscribers(self, batch_id: UUID) -> bool:
        """Check if a batch has any subscribers.

        Args:
            batch_id: UUID of the batch

        Returns:
            True if there are active subscribers
        """
        with self._thread_lock:
            return bool(self._subscribers.get(str(batch_id)))

    async def publish_completion(self, batch_id: UUID) -> None:
        """Publish a completion marker to signal end of events.

        Args:
            batch_id: UUID of the batch
        """
        completion_event = {"type": "BATCH_STREAM_COMPLETE", "batch_id": str(batch_id)}
        self.publish_sync(batch_id, completion_event)


# Global singleton instance
_broadcaster: Optional[BatchEventBroadcaster] = None


def get_batch_broadcaster() -> BatchEventBroadcaster:
    """Get the global batch event broadcaster instance."""
    global _broadcaster
    if _broadcaster is None:
        _broadcaster = BatchEventBroadcaster()
    return _broadcaster
