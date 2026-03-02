"""Unit tests for Weaviate connection lifecycle helpers."""

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from src.lib.weaviate_client import connection as wc


@pytest.fixture(autouse=True)
def _reset_singletons():
    wc.WeaviateConnection._instance = None
    wc._connection = None
    yield
    wc.WeaviateConnection._instance = None
    wc._connection = None


def test_connect_reuses_ready_existing_client():
    conn = wc.WeaviateConnection(url="http://localhost:8080")
    ready_client = SimpleNamespace(is_ready=lambda: True)
    conn._client = ready_client
    assert conn.connect() is ready_client


def test_connect_uses_local_defaults_for_localhost(monkeypatch):
    conn = wc.WeaviateConnection(url="http://localhost:8080")
    client = SimpleNamespace(is_ready=lambda: True)

    monkeypatch.setattr(wc.weaviate, "connect_to_local", lambda: client)

    assert conn.connect() is client


def test_connect_uses_local_docker_hostname(monkeypatch):
    conn = wc.WeaviateConnection(url="http://weaviate:8080")
    called = {}
    client = SimpleNamespace(is_ready=lambda: True)

    def _fake_connect_to_local(host=None, port=None):
        called["host"] = host
        called["port"] = port
        return client

    monkeypatch.setattr(wc.weaviate, "connect_to_local", _fake_connect_to_local)
    assert conn.connect() is client
    assert called == {"host": "weaviate", "port": 8080}


def test_connect_uses_remote_custom_connection_with_auth(monkeypatch):
    conn = wc.WeaviateConnection(url="https://remote.example.org:9443", api_key="secret")
    called = {}
    client = SimpleNamespace(is_ready=lambda: True)

    # Ensure service-level defaults from docker-compose env do not leak into this unit expectation.
    monkeypatch.delenv("WEAVIATE_GRPC_HOST", raising=False)
    monkeypatch.delenv("WEAVIATE_GRPC_PORT", raising=False)
    monkeypatch.delenv("WEAVIATE_GRPC_SECURE", raising=False)

    monkeypatch.setattr(wc.Auth, "api_key", lambda key: f"auth:{key}")

    def _fake_connect_to_custom(**kwargs):
        called.update(kwargs)
        return client

    monkeypatch.setattr(wc.weaviate, "connect_to_custom", _fake_connect_to_custom)

    assert conn.connect() is client
    assert called["http_host"] == "remote.example.org"
    assert called["http_port"] == 9443
    assert called["http_secure"] is True
    assert called["grpc_host"] == "remote.example.org"
    assert called["grpc_port"] == 50051
    assert called["grpc_secure"] is True
    assert called["auth_credentials"] == "auth:secret"


def test_connect_uses_env_overrides_for_grpc_in_custom_mode(monkeypatch):
    conn = wc.WeaviateConnection(url="http://127.0.0.1:18080")
    called = {}
    client = SimpleNamespace(is_ready=lambda: True)

    monkeypatch.setenv("WEAVIATE_GRPC_HOST", "127.0.0.1")
    monkeypatch.setenv("WEAVIATE_GRPC_PORT", "15051")
    monkeypatch.setenv("WEAVIATE_GRPC_SECURE", "false")

    def _fake_connect_to_custom(**kwargs):
        called.update(kwargs)
        return client

    monkeypatch.setattr(wc.weaviate, "connect_to_custom", _fake_connect_to_custom)

    assert conn.connect() is client
    assert called["http_host"] == "127.0.0.1"
    assert called["http_port"] == 18080
    assert called["grpc_host"] == "127.0.0.1"
    assert called["grpc_port"] == 15051
    assert called["grpc_secure"] is False


def test_connect_wraps_connection_errors(monkeypatch):
    conn = wc.WeaviateConnection(url="http://localhost:8080")

    monkeypatch.setattr(
        wc.weaviate,
        "connect_to_local",
        lambda: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    with pytest.raises(Exception, match="Connection failed: boom"):
        conn.connect()


def test_disconnect_closes_and_clears_client_even_on_close_error():
    closed = {"called": 0}

    class _Client:
        def close(self):
            closed["called"] += 1
            raise RuntimeError("close error")

    conn = wc.WeaviateConnection(url="http://localhost:8080")
    conn._client = _Client()
    conn.disconnect()
    assert closed["called"] == 1
    assert conn._client is None


def test_is_connected_handles_missing_client_ready_and_exceptions():
    conn = wc.WeaviateConnection(url="http://localhost:8080")
    assert conn.is_connected() is False

    conn._client = SimpleNamespace(is_ready=lambda: True)
    assert conn.is_connected() is True

    conn._client = SimpleNamespace(is_ready=lambda: (_ for _ in ()).throw(RuntimeError("bad")))
    assert conn.is_connected() is False


@pytest.mark.asyncio
async def test_async_connect_and_close_use_executor(monkeypatch):
    calls = []

    class _Loop:
        async def run_in_executor(self, _executor, func):
            calls.append(func.__name__)
            return func()

    conn = wc.WeaviateConnection(url="http://localhost:8080")
    monkeypatch.setattr(conn, "connect", lambda: calls.append("connect-ran"))
    monkeypatch.setattr(conn, "disconnect", lambda: calls.append("disconnect-ran"))
    monkeypatch.setattr(wc.asyncio, "get_event_loop", lambda: _Loop())

    await conn.connect_to_weaviate()
    await conn.close()
    assert "connect-ran" in calls
    assert "disconnect-ran" in calls


def test_session_context_yields_connected_client(monkeypatch):
    conn = wc.WeaviateConnection(url="http://localhost:8080")
    fake_client = object()
    monkeypatch.setattr(conn, "connect", lambda: fake_client)

    with conn.session() as yielded:
        assert yielded is fake_client


@pytest.mark.asyncio
async def test_async_health_check_paths(monkeypatch):
    class _Loop:
        async def run_in_executor(self, _executor, func):
            return func()

    conn = wc.WeaviateConnection(url="http://localhost:8080")
    monkeypatch.setattr(wc.asyncio, "get_event_loop", lambda: _Loop())

    conn._client = SimpleNamespace(
        is_ready=lambda: True,
        cluster=SimpleNamespace(nodes=lambda: ["n1", "n2"]),
        collections=SimpleNamespace(list_all=lambda: ["c1"]),
    )
    healthy = await conn.health_check()
    assert healthy["status"] == "healthy"
    assert healthy["nodes"] == 2
    assert healthy["collections"] == 1

    conn._client = SimpleNamespace(is_ready=lambda: False)
    unhealthy = await conn.health_check()
    assert unhealthy == {"status": "unhealthy", "message": "Client is not ready"}

    conn._client = SimpleNamespace(is_ready=lambda: (_ for _ in ()).throw(RuntimeError("bad")))
    errored = await conn.health_check()
    assert errored["status"] == "unhealthy"
    assert "bad" in errored["message"]


def test_get_connection_initializes_from_env_and_connects(monkeypatch):
    monkeypatch.setenv("WEAVIATE_HOST", "weaviate.example.org")
    monkeypatch.setenv("WEAVIATE_PORT", "9090")
    monkeypatch.setenv("WEAVIATE_SCHEME", "https")

    created = {}

    class _FakeConnection:
        def __init__(self, url, api_key=None):
            created["url"] = url
            created["api_key"] = api_key
            self.connected = False

        def connect(self):
            self.connected = True

    monkeypatch.setattr(wc, "WeaviateConnection", _FakeConnection)

    conn = wc.get_connection()
    assert created["url"] == "https://weaviate.example.org:9090"
    assert conn.connected is True
    assert wc.get_connection() is conn


def test_set_connect_and_close_connection_helpers(monkeypatch):
    class _FakeConnection:
        def __init__(self):
            self.disconnected = False

        def disconnect(self):
            self.disconnected = True

    conn = _FakeConnection()
    wc.set_connection(conn)
    assert wc._connection is conn

    class _FactoryConnection:
        def __init__(self, url, api_key):
            self.url = url
            self.api_key = api_key

        def connect(self):
            return "client"

    monkeypatch.setattr(wc, "WeaviateConnection", _FactoryConnection)
    assert wc.connect_to_weaviate("http://localhost:8080", api_key="k") == "client"

    wc._connection = conn
    wc.close_connection()
    assert conn.disconnected is True
    assert wc._connection is None


def test_module_health_check_handles_status_and_errors():
    class _Client:
        def __init__(self, ready=True, nodes=None, fail=False):
            self._ready = ready
            self._nodes = nodes or []
            self._fail = fail
            self.cluster = SimpleNamespace(nodes=self._nodes_fn)

        def _nodes_fn(self):
            if self._fail:
                raise RuntimeError("cluster down")
            return self._nodes

        def is_ready(self):
            return self._ready

    class _Conn:
        def __init__(self, client):
            self._client = client

        @contextmanager
        def session(self):
            yield self._client

    with pytest.raises(RuntimeError, match="No Weaviate connection established"):
        wc.health_check()

    wc._connection = _Conn(_Client(ready=False))
    assert wc.health_check() == {"healthy": False, "error": "Client is not ready"}

    wc._connection = _Conn(_Client(ready=True, nodes=[{"name": "node-a"}]))
    healthy = wc.health_check()
    assert healthy["healthy"] is True
    assert healthy["nodes"] == [{"name": "node-a"}]

    wc._connection = _Conn(_Client(ready=True, fail=True))
    failed = wc.health_check()
    assert failed["healthy"] is False
    assert "cluster down" in failed["error"]


def test_get_collection_info_paths():
    with pytest.raises(RuntimeError, match="No Weaviate connection established"):
        wc.get_collection_info("DocumentChunk")

    config = SimpleNamespace(properties=[{"name": "text"}], vectorizer="none")
    collection = SimpleNamespace(
        config=SimpleNamespace(get=lambda: config),
        aggregate=SimpleNamespace(over_all=lambda total_count: SimpleNamespace(total_count=12)),
    )
    client = SimpleNamespace(collections=SimpleNamespace(get=lambda _name: collection))

    class _Conn:
        @contextmanager
        def session(self):
            yield client

    wc._connection = _Conn()
    info = wc.get_collection_info("DocumentChunk")
    assert info["name"] == "DocumentChunk"
    assert info["object_count"] == 12
    assert info["schema"]["class"] == "DocumentChunk"

    class _FailConn:
        @contextmanager
        def session(self):
            yield SimpleNamespace(
                collections=SimpleNamespace(
                    get=lambda _name: (_ for _ in ()).throw(RuntimeError("missing collection"))
                )
            )

    wc._connection = _FailConn()
    error = wc.get_collection_info("MissingCollection")
    assert "missing collection" in error["error"]
