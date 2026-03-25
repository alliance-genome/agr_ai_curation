"""Unit tests for the Loki-backed logs API endpoint."""

from datetime import datetime, timedelta, timezone

import httpx
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


class _FakeAsyncClient:
    def __init__(self, *, response=None, exc=None, capture=None, timeout=None):
        self._response = response
        self._exc = exc
        self._capture = capture if capture is not None else {}
        self._capture["timeout"] = timeout

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, params=None):
        self._capture["url"] = url
        self._capture["params"] = params
        if self._exc is not None:
            raise self._exc
        return self._response


def _loki_response(payload, *, status_code=200):
    request = httpx.Request(
        "GET",
        f"{logs_api.loki.DEFAULT_LOKI_URL}{logs_api.loki.LOKI_QUERY_RANGE_PATH}",
    )
    return httpx.Response(status_code, json=payload, request=request)


def _patch_async_client(monkeypatch, *, response=None, exc=None, capture=None):
    monkeypatch.setattr(
        logs_api.loki.httpx,
        "AsyncClient",
        lambda timeout=None: _FakeAsyncClient(
            response=response,
            exc=exc,
            capture=capture,
            timeout=timeout,
        ),
    )


@pytest.fixture(autouse=True)
def clear_loki_url(monkeypatch):
    monkeypatch.delenv("LOKI_URL", raising=False)


@pytest.fixture
def frozen_now(monkeypatch):
    monkeypatch.setattr(logs_api, "datetime", _FrozenDateTime)
    return _FrozenDateTime._now


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
async def test_get_container_logs_queries_loki_with_default_lookback_and_service_label(
    monkeypatch, frozen_now
):
    capture = {}
    _patch_async_client(
        monkeypatch,
        response=_loki_response(
            {
                "data": {
                    "result": [
                        {
                            "stream": {"service": "backend"},
                            "values": [
                                ["1742903999000000000", "later line"],
                                ["1742903998000000000", "earlier line"],
                            ],
                        }
                    ]
                }
            }
        ),
        capture=capture,
    )

    payload = await logs_api.get_container_logs("backend", lines=120)

    assert capture["url"] == (
        f"{logs_api.loki.DEFAULT_LOKI_URL}{logs_api.loki.LOKI_QUERY_RANGE_PATH}"
    )
    assert isinstance(capture["timeout"], httpx.Timeout)
    assert capture["timeout"].connect == 10.0
    assert capture["timeout"].read == 10.0
    assert capture["params"]["query"] == '{service="backend"}'
    assert capture["params"]["limit"] == 120
    assert capture["params"]["direction"] == "backward"
    assert capture["params"]["start"] == logs_api.loki.normalize_time(
        frozen_now - logs_api.DEFAULT_LOKI_LOOKBACK
    )
    assert capture["params"]["end"] == logs_api.loki.normalize_time(frozen_now)
    assert payload.container == "backend"
    assert payload.lines == 2
    assert payload.lines_returned == 2
    assert payload.logs == "earlier line\nlater line\n"


@pytest.mark.asyncio
async def test_get_container_logs_passes_since_level_and_limit_to_loki(
    monkeypatch, frozen_now
):
    capture = {}
    _patch_async_client(
        monkeypatch,
        response=_loki_response(
            {
                "data": {
                    "result": [
                        {
                            "stream": {"service": "backend", "level": "ERROR"},
                            "values": [["1742903100000000000", "ERROR line"]],
                        }
                    ]
                }
            }
        ),
        capture=capture,
    )

    payload = await logs_api.get_container_logs(
        "backend",
        lines=150,
        level="error",
        since=15,
    )

    assert capture["params"]["query"] == (
        '{service="backend",level=~"(?i)^error$"}'
    )
    assert capture["params"]["limit"] == 150
    assert capture["params"]["start"] == logs_api.loki.normalize_time(
        frozen_now - timedelta(minutes=15)
    )
    assert capture["params"]["end"] == logs_api.loki.normalize_time(frozen_now)
    assert payload.lines == 1
    assert payload.lines_returned == 1
    assert payload.logs == "ERROR line\n"


@pytest.mark.asyncio
async def test_get_container_logs_returns_empty_payload_for_no_logs(monkeypatch):
    capture = {}
    _patch_async_client(
        monkeypatch,
        response=_loki_response({"data": {"result": []}}),
        capture=capture,
    )

    payload = await logs_api.get_container_logs("backend", lines=100)

    assert capture["params"]["query"] == '{service="backend"}'
    assert capture["params"]["limit"] == 100
    assert payload.lines == 0
    assert payload.lines_returned == 0
    assert payload.logs == ""


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("exc", "expected_error", "expected_help"),
    [
        (
            httpx.TimeoutException("timeout"),
            "Timed out querying Loki at",
            "Ensure the Loki service is running and responding on the configured LOKI_URL.",
        ),
        (
            httpx.ConnectError(
                "connection refused",
                request=httpx.Request(
                    "GET",
                    f"{logs_api.loki.DEFAULT_LOKI_URL}{logs_api.loki.LOKI_QUERY_RANGE_PATH}",
                ),
            ),
            "Failed to reach Loki: connection refused.",
            "Ensure the Loki service is running and the configured LOKI_URL is correct.",
        ),
    ],
)
async def test_get_container_logs_formats_loki_unavailable_errors(
    monkeypatch, exc, expected_error, expected_help
):
    _patch_async_client(monkeypatch, exc=exc)

    with pytest.raises(HTTPException) as error:
        await logs_api.get_container_logs("backend", lines=200)

    assert error.value.status_code == 500
    assert error.value.detail.startswith("Failed to retrieve logs from Loki: ")
    assert expected_error in error.value.detail
    assert expected_help in error.value.detail


@pytest.mark.asyncio
async def test_get_container_logs_wraps_unexpected_errors(monkeypatch):
    async def _fake_query_logs(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(logs_api, "_query_logs", _fake_query_logs)

    with pytest.raises(HTTPException) as exc:
        await logs_api.get_container_logs("backend", lines=200)

    assert exc.value.status_code == 500
    assert exc.value.detail == "Unexpected error: boom"
