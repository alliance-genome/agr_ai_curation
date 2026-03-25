"""Unit tests for the Loki client helpers."""

from datetime import datetime, timezone

import httpx
import pytest

from src.lib import loki_client as loki


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
        f"{loki.DEFAULT_LOKI_URL}{loki.LOKI_QUERY_RANGE_PATH}",
    )
    return httpx.Response(status_code, json=payload, request=request)


def _patch_async_client(monkeypatch, *, response=None, exc=None, capture=None):
    monkeypatch.setattr(
        loki.httpx,
        "AsyncClient",
        lambda timeout=None: _FakeAsyncClient(
            response=response,
            exc=exc,
            capture=capture,
            timeout=timeout,
        ),
    )


def test_extract_timestamped_entries_parses_nanosecond_timestamps():
    payload = {
        "data": {
            "result": [
                {
                    "stream": {"service": "backend"},
                    "values": [
                        ["1742903100000000000", "first line"],
                        ["1742903101000000000", "second line"],
                    ],
                },
                {
                    "stream": {"service": "backend"},
                    "values": [["1742903100500000000", "third line"]],
                },
            ]
        }
    }

    assert loki.extract_timestamped_entries(payload) == [
        (1742903100000000000, 0, "first line"),
        (1742903101000000000, 1, "second line"),
        (1742903100500000000, 2, "third line"),
    ]


@pytest.mark.asyncio
async def test_query_logs_returns_plain_lines_from_loki_values(monkeypatch):
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
                                ["1742903100000000000", "first line"],
                                ["1742903101000000000", "second line"],
                            ],
                        }
                    ]
                }
            }
        ),
        capture=capture,
    )

    client = loki.LokiClient(base_url="http://test-loki:3100", timeout_seconds=5.0)
    start = datetime(2026, 3, 25, 11, 45, tzinfo=timezone.utc)
    end = datetime(2026, 3, 25, 12, 0, tzinfo=timezone.utc)

    result = await client.query_logs(
        service="backend",
        start=start,
        end=end,
        limit=25,
        level="error",
    )

    assert result == ["first line", "second line"]
    assert capture["url"] == "http://test-loki:3100/loki/api/v1/query_range"
    assert isinstance(capture["timeout"], httpx.Timeout)
    assert capture["params"]["query"] == (
        '{service="backend",level=~"(?i)^error$"}'
    )
    assert capture["params"]["limit"] == 25
    assert capture["params"]["start"] == loki.normalize_time(start)
    assert capture["params"]["end"] == loki.normalize_time(end)


@pytest.mark.asyncio
async def test_query_logs_returns_empty_list_for_empty_results(monkeypatch):
    _patch_async_client(
        monkeypatch,
        response=_loki_response({"data": {"result": []}}),
    )

    client = loki.LokiClient(base_url="http://test-loki:3100", timeout_seconds=5.0)
    result = await client.query_logs(service="backend", limit=10)

    assert result == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("exc", "expected_error", "expected_help"),
    [
        (
            httpx.TimeoutException("timeout"),
            "Timed out querying Loki at http://test-loki:3100.",
            "Ensure the Loki service is running and responding on the configured LOKI_URL.",
        ),
        (
            httpx.ConnectError(
                "connection refused",
                request=httpx.Request(
                    "GET",
                    "http://test-loki:3100/loki/api/v1/query_range",
                ),
            ),
            "Failed to reach Loki: connection refused.",
            "Ensure the Loki service is running and the configured LOKI_URL is correct.",
        ),
    ],
)
async def test_query_logs_returns_error_results_when_loki_is_unavailable(
    monkeypatch, exc, expected_error, expected_help
):
    _patch_async_client(monkeypatch, exc=exc)

    client = loki.LokiClient(base_url="http://test-loki:3100", timeout_seconds=5.0)
    result = await client.query_logs(service="backend")

    assert result["status"] == "error"
    assert result["error"] == expected_error
    assert result["help"] == expected_help
