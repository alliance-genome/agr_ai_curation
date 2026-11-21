"""The backend refuses a search index engine version other than the pinned one."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.lib.weaviate_client import connection as connection_module
from src.lib.weaviate_client.engine_version import (
    WeaviateEngineVersionMismatch,
    require_expected_engine_version,
    server_engine_version,
)


def _client(version: str) -> MagicMock:
    client = MagicMock()
    client.get_meta.return_value = {"version": version}
    return client


def test_matching_engine_version_passes_and_is_recorded_once(monkeypatch):
    from src.lib.weaviate_client import engine_version

    monkeypatch.setenv("WEAVIATE_EXPECTED_VERSION", "1.31.18")
    monkeypatch.setattr(engine_version, "_recorded_version", None)

    assert require_expected_engine_version(_client("1.31.18")) == "1.31.18"
    assert engine_version.recorded_engine_version() == "1.31.18"


def test_mismatch_records_nothing(monkeypatch):
    from src.lib.weaviate_client import engine_version

    monkeypatch.setenv("WEAVIATE_EXPECTED_VERSION", "1.31.18")
    monkeypatch.setattr(engine_version, "_recorded_version", None)

    with pytest.raises(WeaviateEngineVersionMismatch):
        require_expected_engine_version(_client("1.40.7"))
    assert engine_version.recorded_engine_version() is None


def test_mismatched_engine_version_raises_named_error(monkeypatch):
    monkeypatch.setenv("WEAVIATE_EXPECTED_VERSION", "1.31.18")

    with pytest.raises(WeaviateEngineVersionMismatch) as exc_info:
        require_expected_engine_version(_client("1.40.7"))

    message = str(exc_info.value)
    assert "the server reports 1.40.7" in message
    assert "WEAVIATE_EXPECTED_VERSION is 1.31.18" in message


def test_missing_expected_version_is_a_startup_error(monkeypatch):
    monkeypatch.delenv("WEAVIATE_EXPECTED_VERSION", raising=False)

    with pytest.raises(RuntimeError, match="WEAVIATE_EXPECTED_VERSION is not set"):
        require_expected_engine_version(_client("1.31.18"))


def test_server_engine_version_reads_meta():
    assert server_engine_version(_client("1.31.18")) == "1.31.18"


def test_lazy_connection_refuses_a_mismatched_engine(monkeypatch):
    """Processes without the API startup (the benchmark worker) connect through get_connection."""
    monkeypatch.setenv("WEAVIATE_EXPECTED_VERSION", "1.31.18")
    monkeypatch.setattr(connection_module, "_connection", None)
    fake = MagicMock()
    fake.connect.return_value = _client("1.40.7")
    monkeypatch.setattr(connection_module, "WeaviateConnection", lambda **_kwargs: fake)

    with pytest.raises(WeaviateEngineVersionMismatch):
        connection_module.get_connection()
    assert connection_module._connection is None


def test_lazy_connection_starts_on_the_pinned_engine(monkeypatch):
    monkeypatch.setenv("WEAVIATE_EXPECTED_VERSION", "1.31.18")
    monkeypatch.setattr(connection_module, "_connection", None)
    fake = MagicMock()
    fake.connect.return_value = _client("1.31.18")
    monkeypatch.setattr(connection_module, "WeaviateConnection", lambda **_kwargs: fake)

    assert connection_module.get_connection() is fake
    monkeypatch.setattr(connection_module, "_connection", None)
