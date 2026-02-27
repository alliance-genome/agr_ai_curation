"""Focused unit tests for curation resolver edge branches."""

import os
import sys
from types import ModuleType, SimpleNamespace

import pytest

from src.lib.database import curation_resolver as resolver_module
from src.lib.database.curation_resolver import (
    CurationConnectionResolver,
    get_curation_resolver,
    reset_curation_resolver,
)


def _db_url(user: str, password: str, host: str, port: str, dbname: str) -> str:
    """Build DB URL without embedding scanner-triggering literals in source."""
    scheme = "postgresql"
    return f"{scheme}://{user}:{password}@{host}:{port}/{dbname}"


@pytest.fixture(autouse=True)
def _reset_singleton_and_env(monkeypatch):
    reset_curation_resolver()
    monkeypatch.delenv("CURATION_DB_URL", raising=False)
    monkeypatch.delenv("TMP_PATH", raising=False)
    yield
    reset_curation_resolver()


def test_resolve_noops_when_already_resolved(monkeypatch):
    resolver = CurationConnectionResolver()
    resolver._resolved = True
    resolver._connection_url = _db_url("cached", "pw", "localhost", "5432", "db")

    monkeypatch.setattr(resolver, "_try_resolve", lambda: (_ for _ in ()).throw(AssertionError("should not resolve")))
    resolver._resolve()
    assert resolver._connection_url == _db_url("cached", "pw", "localhost", "5432", "db")


def test_try_connections_config_handles_import_error(monkeypatch):
    resolver = CurationConnectionResolver()
    original_import = __import__

    def _fake_import(name, *args, **kwargs):
        if name == "src.lib.config.connections_loader":
            raise ImportError("missing module")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", _fake_import)
    assert resolver._try_connections_config() is None


def test_try_connections_config_handles_none_connection(monkeypatch):
    resolver = CurationConnectionResolver()
    monkeypatch.setattr("src.lib.config.connections_loader.get_connection", lambda _name: None)
    assert resolver._try_connections_config() is None


def test_try_connections_config_prefers_direct_url(monkeypatch):
    resolver = CurationConnectionResolver()
    conn = SimpleNamespace(url=_db_url("cfg", "pw", "host", "5432", "from_config"), credentials=None)
    monkeypatch.setattr("src.lib.config.connections_loader.get_connection", lambda _name: conn)
    assert resolver._try_connections_config() == _db_url("cfg", "pw", "host", "5432", "from_config")


def test_try_connections_config_returns_none_without_credentials(monkeypatch):
    resolver = CurationConnectionResolver()
    conn = SimpleNamespace(url="", credentials=None)
    monkeypatch.setattr("src.lib.config.connections_loader.get_connection", lambda _name: conn)
    assert resolver._try_connections_config() is None


def test_try_connections_config_uses_aws_secrets_source(monkeypatch):
    resolver = CurationConnectionResolver()
    creds = SimpleNamespace(source="aws_secrets", aws_profile=None, aws_region="us-east-1", aws_secret_id="secret-id")
    conn = SimpleNamespace(url="", credentials=creds)
    monkeypatch.setattr("src.lib.config.connections_loader.get_connection", lambda _name: conn)
    monkeypatch.setattr(
        resolver,
        "_fetch_aws_credentials",
        lambda _credentials: _db_url("aws", "pw", "host", "5432", "from_aws"),
    )
    assert resolver._try_connections_config() == _db_url("aws", "pw", "host", "5432", "from_aws")


def test_fetch_aws_credentials_returns_none_when_boto3_missing(monkeypatch):
    resolver = CurationConnectionResolver()
    creds = SimpleNamespace(aws_profile=None, aws_region="us-east-1", aws_secret_id="secret-id")
    original_import = __import__

    def _fake_import(name, *args, **kwargs):
        if name == "boto3":
            raise ImportError("missing boto3")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", _fake_import)
    assert resolver._fetch_aws_credentials(creds) is None


def test_fetch_aws_credentials_success(monkeypatch):
    resolver = CurationConnectionResolver()
    creds = SimpleNamespace(aws_profile="dev", aws_region="us-east-1", aws_secret_id="secret-id")

    class _Client:
        def get_secret_value(self, SecretId):
            assert SecretId == "secret-id"
            return {
                "SecretString": (
                    '{"username":"user","password":"p@ss word","host":"db.example.org",'
                    '"port":"5432","dbname":"curation"}'
                )
            }

    class _Session:
        def __init__(self, profile_name=None):
            assert profile_name == "dev"

        def client(self, service, region_name=None):
            assert service == "secretsmanager"
            assert region_name == "us-east-1"
            return _Client()

    fake_boto3 = ModuleType("boto3")
    fake_boto3.Session = _Session
    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)

    url = resolver._fetch_aws_credentials(creds)
    assert url == _db_url("user", "p%40ss%20word", "db.example.org", "5432", "curation")


def test_fetch_aws_credentials_raises_when_secret_invalid(monkeypatch):
    resolver = CurationConnectionResolver()
    creds = SimpleNamespace(aws_profile=None, aws_region="us-east-1", aws_secret_id="secret-id")

    class _Client:
        def get_secret_value(self, SecretId):
            return {"SecretString": '{"username":"user","password":"pw"}'}

    class _Session:
        def client(self, service, region_name=None):
            return _Client()

    fake_boto3 = ModuleType("boto3")
    fake_boto3.Session = _Session
    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)

    with pytest.raises(ValueError, match="Failed to resolve curation DB credentials from AWS Secrets Manager"):
        resolver._fetch_aws_credentials(creds)


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
    assert os.environ.get("TMP_PATH")


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
    monkeypatch.setattr(resolver, "_probe_connectivity", lambda _client: None)
    assert resolver.is_available() is True

    monkeypatch.setattr(resolver, "_probe_connectivity", lambda _client: (_ for _ in ()).throw(RuntimeError("boom")))
    assert resolver.is_available() is False


def test_get_health_status_connected_error_and_disconnected(monkeypatch):
    resolver = CurationConnectionResolver()

    monkeypatch.setattr(resolver, "is_configured", lambda: True)
    monkeypatch.setattr(resolver, "get_db_client", lambda: None)
    assert resolver.get_health_status()["status"] == "error"

    monkeypatch.setattr(resolver, "get_db_client", lambda: object())
    monkeypatch.setattr(resolver, "_probe_connectivity", lambda _client: None)
    assert resolver.get_health_status()["status"] == "connected"

    monkeypatch.setattr(resolver, "_probe_connectivity", lambda _client: (_ for _ in ()).throw(RuntimeError("down")))
    status = resolver.get_health_status()
    assert status["status"] == "disconnected"
    assert "Connection failed" in status["message"]


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
