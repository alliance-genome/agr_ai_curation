"""Unit tests for generic config-defined PostgreSQL URL resolution."""

import json
import sys
from types import ModuleType, SimpleNamespace

import pytest

from src.lib.database.postgres_connection_resolver import (
    PostgresConnectionResolver,
    get_postgres_connection_resolver,
    reset_postgres_connection_resolvers,
)


def _db_url(user: str, password: str, host: str, port: str, dbname: str) -> str:
    scheme = "postgresql"
    return f"{scheme}://{user}:{password}@{host}:{port}/{dbname}"


@pytest.fixture(autouse=True)
def _reset_resolvers():
    reset_postgres_connection_resolvers()
    yield
    reset_postgres_connection_resolvers()


def test_rejects_empty_service_id():
    with pytest.raises(ValueError, match="service_id must not be empty"):
        PostgresConnectionResolver(" ")


def test_prefers_direct_config_url(monkeypatch):
    resolver = PostgresConnectionResolver("external_production_db")
    connection = SimpleNamespace(
        url=_db_url("reader", "pw", "bridge", "15432", "production"),
        credentials=None,
    )
    monkeypatch.setattr(
        "src.lib.config.connections_loader.get_connection",
        lambda service_id: (
            connection if service_id == "external_production_db" else None
        ),
    )

    assert resolver.get_connection_url() == connection.url


def test_env_source_without_resolved_url_is_unconfigured(monkeypatch):
    resolver = PostgresConnectionResolver("external_reporting_db")
    connection = SimpleNamespace(
        url="",
        credentials=SimpleNamespace(source="env"),
    )
    monkeypatch.setattr(
        "src.lib.config.connections_loader.get_connection",
        lambda _service_id: connection,
    )

    assert resolver.get_connection_url() is None


def test_fetches_and_escapes_aws_secret(monkeypatch):
    resolver = PostgresConnectionResolver("external_reporting_db")
    credentials = SimpleNamespace(
        source="aws_secrets",
        aws_profile="dev",
        aws_region="us-east-1",
        aws_secret_id="external-reporting-secret",
    )
    connection = SimpleNamespace(url="", credentials=credentials)
    monkeypatch.setattr(
        "src.lib.config.connections_loader.get_connection",
        lambda _service_id: connection,
    )

    class _Client:
        def get_secret_value(self, SecretId):
            assert SecretId == "external-reporting-secret"
            fields = ("username", "password", "host", "port", "dbname")
            values = (
                "read user",
                "".join(("p", "@", "ss word")),
                "db.example.org",
                "15432",
                "reporting archive",
            )
            return {"SecretString": json.dumps(dict(zip(fields, values, strict=True)))}

    class _Session:
        def __init__(self, profile_name=None):
            assert profile_name == "dev"

        def client(self, service, region_name=None):
            assert service == "secretsmanager"
            assert region_name == "us-east-1"
            return _Client()

    fake_boto3 = ModuleType("boto3")
    setattr(fake_boto3, "Session", _Session)
    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)

    assert resolver.get_connection_url() == _db_url(
        "read%20user",
        "p%40ss%20word",
        "db.example.org",
        "15432",
        "reporting%20archive",
    )


def test_invalid_aws_secret_raises_service_specific_error(monkeypatch):
    resolver = PostgresConnectionResolver("external_production_db")
    credentials = SimpleNamespace(
        source="aws_secrets",
        aws_profile="",
        aws_region="us-east-1",
        aws_secret_id="external-production-secret",
    )
    connection = SimpleNamespace(url="", credentials=credentials)
    monkeypatch.setattr(
        "src.lib.config.connections_loader.get_connection",
        lambda _service_id: connection,
    )

    class _Client:
        def get_secret_value(self, SecretId):
            return {"SecretString": '{"username":"reader"}'}

    class _Session:
        def client(self, service, region_name=None):
            return _Client()

    fake_boto3 = ModuleType("boto3")
    setattr(fake_boto3, "Session", _Session)
    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)

    with pytest.raises(
        ValueError,
        match="Failed to resolve external_production_db credentials",
    ):
        resolver.get_connection_url()


def test_singleton_registry_is_per_service():
    production = get_postgres_connection_resolver("external_production_db")
    same_production = get_postgres_connection_resolver("external_production_db")
    reporting = get_postgres_connection_resolver("external_reporting_db")

    assert production is same_production
    assert production is not reporting
