"""Real Redis Stop reads across batch loops must not poison HTTP connections."""
import asyncio
from uuid import uuid4

import pytest

from src.lib import redis_client
from src.lib.validation_review.chat import ChatReview
from src.lib.validation_review.stops import ReviewInterrupted


def test_review_stop_across_worker_loops_and_http_loop(monkeypatch):
    monkeypatch.setattr(redis_client, "_redis_client", None)
    session = "test-review-loop-" + uuid4().hex
    key = redis_client.CANCEL_KEY_PREFIX + session

    async def document():
        finished = asyncio.Event()

        async def work():
            try:
                await asyncio.Event().wait()
            finally:
                finished.set()

        review = ChatReview("owner", "doc", session, "turn", None, (), "review")
        with pytest.raises(ReviewInterrupted):
            await asyncio.wait_for(review.until_stop(work()), timeout=5)
        assert review.interrupted
        assert finished.is_set()

    def documents():
        # Reproduce the real batch lifecycle: a fresh loop per document, on
        # a worker thread while the HTTP loop remains alive in this process.
        asyncio.run(document())
        asyncio.run(document())

    async def http():
        client = await redis_client.get_redis()
        try:
            await client.set(key, "1")
            assert await client.get(key) == "1"
            await asyncio.to_thread(documents)
            assert await redis_client.get_redis() is client
            assert await client.ping()
            assert await redis_client.check_cancel_signal(session)
        finally:
            await client.delete(key)
            await client.aclose()

    asyncio.run(http())
