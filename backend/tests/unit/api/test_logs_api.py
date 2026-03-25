"""Unit tests for the Loki-backed logs API endpoint."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from src.api import logs as logs_api


class _FrozenDateTime(datetime):
    _now = datetime(2026, 3, 25, 12, 0, tzinfo=timezone.utc)

    @classmethod
    def now(cls, tz=None):
        if tz is None:
            return cls._now.replace(tzinfo=None)
        return cls._now.astimezone(tz)


class _FakeLokiClient:
    def __init__(self, *, timeout_seconds):
        self.timeout_seconds = timeout_seconds


@pytest.fixture
def frozen_now(monkeypatch):
    monkeypatch.setattr(logs_api, "datetime", _FrozenDateTime)
    return _FrozenDateTime._now


@pytest.fixture
def fake_loki_client(monkeypatch):
    created_clients = []

    def _factory(*, timeout_seconds):
        client = _FakeLokiClient(timeout_seconds=timeout_seconds)
        created_clients.append(client)
        return client

    monkeypatch.setattr(logs_api.loki, "LokiClient", _factory)
    return created_clients


@pytest.mark.asyncio
async def test_get_container_logs_rejects_invalid_container():
    with pytest.raises(HTTPException) as exc:
        await logs_api.get_container_logs("not-allowed", lines=2000)

    assert exc.value.status_code == 400
    assert "Invalid container name" in exc.value.detail


@pytest.mark.asyncio
async def test_get_container_logs_rejects_invalid_level():
    with pytest.raises(HTTPException) as exc:
        await logs_api.get_container_logs("backend", lines=2000, level="trace")

    assert exc.value.status_code == 400
    assert "Invalid log level" in exc.value.detail


@pytest.mark.asyncio
async def test_get_container_logs_uses_default_lookback_and_returns_logs(
    monkeypatch, frozen_now, fake_loki_client
):
    captured = {}

    async def _fake_query_logs(loki_client, *, service, start, end, limit, level):
        captured["loki_client"] = loki_client
        captured["service"] = service
        captured["start"] = start
        captured["end"] = end
        captured["limit"] = limit
        captured["level"] = level
        return ["line1", "line2"]

    monkeypatch.setattr(logs_api, "_query_logs", _fake_query_logs)

    payload = await logs_api.get_container_logs("backend", lines=120)

    assert len(fake_loki_client) == 1
    assert fake_loki_client[0].timeout_seconds == 10.0
    assert captured["loki_client"] is fake_loki_client[0]
    assert captured["service"] == "backend"
    assert captured["start"] == frozen_now - logs_api.DEFAULT_LOKI_LOOKBACK
    assert captured["end"] == frozen_now
    assert captured["limit"] == 120
    assert captured["level"] is None
    assert payload.container == "backend"
    assert payload.lines == 2
    assert payload.lines_returned == 2
    assert payload.logs == "line1\nline2\n"


@pytest.mark.asyncio
async def test_get_container_logs_passes_since_and_normalized_level(
    monkeypatch, frozen_now
):
    captured = {}

    async def _fake_query_logs(_loki_client, *, service, start, end, limit, level):
        captured["service"] = service
        captured["start"] = start
        captured["end"] = end
        captured["limit"] = limit
        captured["level"] = level
        return ["debug context", "ERROR line"]

    monkeypatch.setattr(logs_api, "_query_logs", _fake_query_logs)

    payload = await logs_api.get_container_logs(
        "backend",
        lines=100,
        level="error",
        since=15,
    )

    assert captured["service"] == "backend"
    assert captured["start"] == frozen_now - timedelta(minutes=15)
    assert captured["end"] == frozen_now
    assert captured["limit"] == 100
    assert captured["level"] == "ERROR"
    assert payload.lines_returned == 2
    assert payload.logs == "debug context\nERROR line\n"


@pytest.mark.asyncio
async def test_get_container_logs_formats_loki_error_response(monkeypatch):
    async def _fake_query_logs(*_args, **_kwargs):
        return {
            "status": "error",
            "error": "Timed out querying Loki.",
            "help": "Ensure the Loki service is running.",
        }

    monkeypatch.setattr(logs_api, "_query_logs", _fake_query_logs)

    with pytest.raises(HTTPException) as exc:
        await logs_api.get_container_logs("backend", lines=200)

    assert exc.value.status_code == 500
    assert exc.value.detail == (
        "Failed to retrieve logs from Loki: Timed out querying Loki. "
        "Ensure the Loki service is running."
    )


@pytest.mark.asyncio
async def test_get_container_logs_wraps_unexpected_errors(monkeypatch):
    async def _fake_query_logs(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(logs_api, "_query_logs", _fake_query_logs)

    with pytest.raises(HTTPException) as exc:
        await logs_api.get_container_logs("backend", lines=200)

    assert exc.value.status_code == 500
    assert exc.value.detail == "Unexpected error: boom"
