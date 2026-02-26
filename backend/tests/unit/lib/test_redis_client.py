"""Unit tests for Redis client helpers used by chat streaming."""

import pytest

from src.lib import redis_client


class _FakeRedis:
    def __init__(self):
        self.store = {}
        self.set_calls = []
        self.setex_calls = []
        self.delete_calls = []
        self.eval_calls = []
        self.closed = False
        self.raise_on = set()

    async def setex(self, key, ttl, value):
        if "setex" in self.raise_on:
            raise RuntimeError("setex failed")
        self.setex_calls.append((key, ttl, value))
        self.store[key] = value

    async def set(self, key, value, ex=None, nx=False):
        if "set" in self.raise_on:
            raise RuntimeError("set failed")
        self.set_calls.append((key, value, ex, nx))
        if nx and key in self.store:
            return False
        self.store[key] = value
        return True

    async def get(self, key):
        if "get" in self.raise_on:
            raise RuntimeError("get failed")
        return self.store.get(key)

    async def delete(self, key):
        if "delete" in self.raise_on:
            raise RuntimeError("delete failed")
        self.delete_calls.append(key)
        self.store.pop(key, None)

    async def eval(self, script, numkeys, *keys_and_args):
        if "eval" in self.raise_on:
            raise RuntimeError("eval failed")
        self.eval_calls.append((script, numkeys, keys_and_args))
        return 1

    async def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def _reset_redis_client_state():
    redis_client._redis_client = None
    yield
    redis_client._redis_client = None


def test_get_redis_url_default(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    assert redis_client.get_redis_url() == "redis://localhost:6379/0"


def test_get_redis_url_env(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "redis://example:6379/2")
    assert redis_client.get_redis_url() == "redis://example:6379/2"


@pytest.mark.asyncio
async def test_get_redis_initializes_once(monkeypatch):
    fake = _FakeRedis()
    calls = []

    def _from_url(url, encoding=None, decode_responses=None):
        calls.append((url, encoding, decode_responses))
        return fake

    monkeypatch.setenv("REDIS_URL", "redis://cache.local:6379/1")
    monkeypatch.setattr(redis_client.redis, "from_url", _from_url)

    client_a = await redis_client.get_redis()
    client_b = await redis_client.get_redis()

    assert client_a is fake
    assert client_b is fake
    assert len(calls) == 1
    assert calls[0][0] == "redis://cache.local:6379/1"
    assert calls[0][1] == "utf-8"
    assert calls[0][2] is True


@pytest.mark.asyncio
async def test_close_redis_resets_global_client():
    fake = _FakeRedis()
    redis_client._redis_client = fake

    await redis_client.close_redis()

    assert fake.closed is True
    assert redis_client._redis_client is None


@pytest.mark.asyncio
async def test_set_cancel_signal_sets_key_and_ttl():
    fake = _FakeRedis()
    redis_client._redis_client = fake

    result = await redis_client.set_cancel_signal("sess-1")

    assert result is True
    assert fake.setex_calls == [("chat:cancel:sess-1", redis_client.CANCEL_TTL_SECONDS, "1")]


@pytest.mark.asyncio
async def test_set_cancel_signal_returns_false_on_error():
    fake = _FakeRedis()
    fake.raise_on.add("setex")
    redis_client._redis_client = fake

    result = await redis_client.set_cancel_signal("sess-2")
    assert result is False


@pytest.mark.asyncio
async def test_check_cancel_signal_handles_presence_and_errors():
    fake = _FakeRedis()
    fake.store["chat:cancel:sess-1"] = "1"
    redis_client._redis_client = fake

    assert await redis_client.check_cancel_signal("sess-1") is True
    assert await redis_client.check_cancel_signal("sess-unknown") is False

    fake.raise_on.add("get")
    assert await redis_client.check_cancel_signal("sess-1") is False


@pytest.mark.asyncio
async def test_clear_cancel_signal_best_effort_no_raise():
    fake = _FakeRedis()
    fake.store["chat:cancel:sess-1"] = "1"
    redis_client._redis_client = fake

    await redis_client.clear_cancel_signal("sess-1")
    assert "chat:cancel:sess-1" in fake.delete_calls
    assert "chat:cancel:sess-1" not in fake.store

    fake.raise_on.add("delete")
    await redis_client.clear_cancel_signal("sess-2")


@pytest.mark.asyncio
async def test_register_active_stream_conflict_and_success_paths():
    fake = _FakeRedis()
    redis_client._redis_client = fake

    ok = await redis_client.register_active_stream("sess-1", user_id="user-a", stream_token="tok-1")
    assert ok is True
    assert fake.store["chat:owner:sess-1"] == "user-a|tok-1"
    assert fake.store["chat:active:sess-1"] == "1"
    assert await redis_client.get_stream_owner("sess-1") == "user-a"

    fake.store["chat:owner:sess-2"] = "user-a"
    ok_same_owner = await redis_client.register_active_stream("sess-2", user_id="user-a", stream_token="tok-2")
    assert ok_same_owner is True
    assert fake.store["chat:owner:sess-2"] == "user-a|tok-2"

    fake.store["chat:owner:sess-3"] = "user-a"
    ok_conflict = await redis_client.register_active_stream("sess-3", user_id="user-b")
    assert ok_conflict is False


@pytest.mark.asyncio
async def test_register_active_stream_degrades_gracefully_on_redis_errors():
    fake = _FakeRedis()
    fake.raise_on.add("set")
    redis_client._redis_client = fake

    result = await redis_client.register_active_stream("sess-err", user_id="user-a")
    assert result is True


@pytest.mark.asyncio
async def test_unregister_active_stream_paths():
    fake = _FakeRedis()
    redis_client._redis_client = fake

    await redis_client.unregister_active_stream("sess-1", user_id="user-a")
    assert len(fake.eval_calls) == 1
    assert fake.eval_calls[0][1] == 2
    assert fake.eval_calls[0][2][-1] == "user-a"

    await redis_client.unregister_active_stream("sess-token", user_id="user-a", stream_token="tok-1")
    assert len(fake.eval_calls) == 2
    assert fake.eval_calls[1][2][-1] == "user-a|tok-1"

    await redis_client.unregister_active_stream("sess-2")
    assert "chat:active:sess-2" in fake.delete_calls
    assert "chat:owner:sess-2" in fake.delete_calls

    fake.raise_on.add("eval")
    await redis_client.unregister_active_stream("sess-3", user_id="user-a")


@pytest.mark.asyncio
async def test_is_stream_active_and_get_stream_owner():
    fake = _FakeRedis()
    fake.store["chat:active:sess-1"] = "1"
    fake.store["chat:owner:sess-1"] = "user-a"
    redis_client._redis_client = fake

    assert await redis_client.is_stream_active("sess-1") is True
    assert await redis_client.is_stream_active("sess-2") is False
    assert await redis_client.get_stream_owner("sess-1") == "user-a"
    assert await redis_client.get_stream_owner("sess-2") is None

    fake.raise_on.add("get")
    assert await redis_client.is_stream_active("sess-1") is False
    assert await redis_client.get_stream_owner("sess-1") is None
