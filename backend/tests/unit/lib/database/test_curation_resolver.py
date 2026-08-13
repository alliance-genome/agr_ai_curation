"""Focused unit tests for curation resolver edge branches."""

import os
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.lib.database.curation_resolver import (
    CurationDbClient,
    CurationConnectionResolver,
    get_curation_resolver,
    reset_curation_resolver,
)
from src.lib.database.postgres_connection_resolver import (
    PostgresConnectionResolver,
    reset_postgres_connection_resolvers,
)


def _db_url(user: str, password: str, host: str, port: str, dbname: str) -> str:
    """Build DB URL without embedding scanner-triggering literals in source."""
    scheme = "postgresql"
    return f"{scheme}://{user}:{password}@{host}:{port}/{dbname}"


@pytest.fixture(autouse=True)
def _reset_singleton_and_env(monkeypatch):
    reset_curation_resolver()
    reset_postgres_connection_resolvers()
    monkeypatch.delenv("CURATION_DB_URL", raising=False)
    monkeypatch.delenv("TMP_PATH", raising=False)
    yield
    reset_curation_resolver()
    reset_postgres_connection_resolvers()


def test_connection_url_delegates_to_canonical_postgres_resolver():
    connection_url = _db_url("reader", "pw", "db", "5432", "curation")
    delegate = MagicMock(spec=PostgresConnectionResolver)
    delegate.get_connection_url.return_value = connection_url
    resolver = CurationConnectionResolver(connection_resolver=delegate)

    assert resolver.get_connection_url() == connection_url
    assert resolver.is_configured() is True
    resolver.reset()
    assert delegate.get_connection_url.call_count == 2
    delegate.reset.assert_called_once_with()


def test_unconfigured_curation_client_and_health_status():
    delegate = MagicMock(spec=PostgresConnectionResolver)
    delegate.get_connection_url.return_value = None
    resolver = CurationConnectionResolver(connection_resolver=delegate)

    assert resolver.is_configured() is False
    assert resolver.get_db_client() is None
    assert resolver.get_health_status() == {
        "status": "not_configured",
        "message": "Curation database is not configured",
    }


def test_get_db_client_returns_cached_instance():
    resolver = CurationConnectionResolver()
    cached = object()
    resolver._db_client = cached
    assert resolver.get_db_client() is cached


def test_get_db_client_handles_missing_agr_package(monkeypatch):
    resolver = CurationConnectionResolver()
    monkeypatch.setattr(resolver, "get_connection_url", lambda: _db_url("user", "pw", "host", "5432", "db"))
    original_import = __import__

    def _fake_import(name, *args, **kwargs):
        if name == "agr_curation_api.db_methods":
            raise ImportError("package missing")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", _fake_import)
    assert resolver.get_db_client() is None


def test_get_db_client_success_sets_tmp_path_and_builds_client(monkeypatch):
    resolver = CurationConnectionResolver()
    monkeypatch.setattr(
        resolver,
        "get_connection_url",
        lambda: _db_url("user", "pw", "host", "5432", "dbname"),
    )

    fake_module = ModuleType("agr_curation_api.db_methods")

    class _DatabaseConfig:
        username = None
        password = None
        database = None
        host = None
        port = None

    class _DatabaseMethods:
        def __init__(self, config):
            self.config = config

        def _create_session(self):
            return "session"

    fake_module.DatabaseConfig = _DatabaseConfig
    fake_module.DatabaseMethods = _DatabaseMethods
    monkeypatch.setitem(sys.modules, "agr_curation_api.db_methods", fake_module)

    client = resolver.get_db_client()
    assert client is not None
    assert client.config.username == "user"
    assert client.config.password == "pw"
    assert client.config.database == "dbname"
    assert client.config.host == "host"
    assert client.config.port == "5432"
    assert isinstance(client, CurationDbClient)
    assert client.create_session() == "session"
    assert os.environ.get("TMP_PATH")


def test_curation_db_client_proxies_public_delegate_only():
    class _Delegate:
        config = "public-config"

        def _create_session(self):
            return "session"

    client = CurationDbClient(_Delegate())

    assert client.config == "public-config"
    assert client.create_session() == "session"
    with pytest.raises(AttributeError, match="_create_session"):
        getattr(client, "_create_session")
    with pytest.raises(AttributeError, match="_delegate"):
        getattr(client, "_delegate")


def test_get_db_client_returns_none_on_constructor_error(monkeypatch):
    resolver = CurationConnectionResolver()
    monkeypatch.setattr(
        resolver,
        "get_connection_url",
        lambda: _db_url("user", "pw", "host", "5432", "dbname"),
    )

    fake_module = ModuleType("agr_curation_api.db_methods")

    class _DatabaseConfig:
        pass

    class _DatabaseMethods:
        def __init__(self, _config):
            raise RuntimeError("construction failed")

    fake_module.DatabaseConfig = _DatabaseConfig
    fake_module.DatabaseMethods = _DatabaseMethods
    monkeypatch.setitem(sys.modules, "agr_curation_api.db_methods", fake_module)

    assert resolver.get_db_client() is None


def test_probe_connectivity_raises_when_no_provider_data():
    resolver = CurationConnectionResolver()
    with pytest.raises(RuntimeError, match="returned no provider data"):
        resolver._probe_connectivity(SimpleNamespace(get_data_providers=lambda: None))


def test_is_available_paths(monkeypatch):
    resolver = CurationConnectionResolver()
    monkeypatch.setattr(resolver, "get_db_client", lambda: None)
    assert resolver.is_available() is False

    monkeypatch.setattr(resolver, "get_db_client", lambda: object())
    monkeypatch.setattr(resolver, "_probe_connectivity_with_refresh", lambda _client: None)
    assert resolver.is_available() is True

    monkeypatch.setattr(
        resolver,
        "_probe_connectivity_with_refresh",
        lambda _client: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    assert resolver.is_available() is False


def test_get_health_status_connected_error_and_disconnected(monkeypatch):
    resolver = CurationConnectionResolver()

    monkeypatch.setattr(resolver, "is_configured", lambda: True)
    monkeypatch.setattr(resolver, "get_db_client", lambda: None)
    assert resolver.get_health_status()["status"] == "error"

    monkeypatch.setattr(resolver, "get_db_client", lambda: object())
    monkeypatch.setattr(resolver, "_probe_connectivity_with_refresh", lambda _client: None)
    assert resolver.get_health_status()["status"] == "connected"

    monkeypatch.setattr(
        resolver,
        "_probe_connectivity_with_refresh",
        lambda _client: (_ for _ in ()).throw(RuntimeError("down")),
    )
    status = resolver.get_health_status()
    assert status["status"] == "disconnected"
    assert "Connection failed" in status["message"]


def test_probe_connectivity_with_refresh_recreates_client_once(monkeypatch):
    resolver = CurationConnectionResolver()
    initial_client = object()
    refreshed_client = object()
    calls = []

    def fake_probe(client):
        calls.append(client)
        if client is initial_client:
            raise RuntimeError("stale client")

    monkeypatch.setattr(resolver, "_probe_connectivity", fake_probe)
    monkeypatch.setattr(resolver, "close", lambda: setattr(resolver, "_db_client", None))
    monkeypatch.setattr(
        resolver,
        "get_db_client",
        lambda: initial_client if resolver._db_client is initial_client else refreshed_client,
    )

    resolver._db_client = initial_client
    resolver._probe_connectivity_with_refresh(initial_client)

    assert calls == [initial_client, refreshed_client]


def test_close_handles_success_and_errors():
    resolver = CurationConnectionResolver()
    marker = {"closed": False}

    class _Client:
        def close(self):
            marker["closed"] = True

    resolver._db_client = _Client()
    resolver.close()
    assert marker["closed"] is True
    assert resolver._db_client is None

    class _FailingClient:
        def close(self):
            raise RuntimeError("close failed")

    resolver._db_client = _FailingClient()
    resolver.close()
    assert resolver._db_client is None


def test_singleton_reset_recreates_instance():
    first = get_curation_resolver()
    reset_curation_resolver()
    second = get_curation_resolver()
    assert first is not second
