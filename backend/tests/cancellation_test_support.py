"""Inject cancellation decisions without a Redis transport in review tests."""
from contextlib import asynccontextmanager


def install_cancel_reader(monkeypatch, check):
    @asynccontextmanager
    async def reader(session_id):
        async def read():
            return await check(session_id)
        yield read

    monkeypatch.setattr("src.lib.redis_client.cancellation_signal_reader", reader)
