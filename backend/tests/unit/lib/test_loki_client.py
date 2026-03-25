"""Unit tests for the Loki client helpers."""

from datetime import datetime, timezone

import httpx
import pytest

from src.lib import loki_client as loki


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
async def test_query_logs_returns_plain_lines_from_loki_values(
    patch_loki_async_client, loki_response
):
    capture = {}
    patch_loki_async_client(
        loki,
        response=loki_response(
            loki,
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
async def test_query_logs_returns_empty_list_for_empty_results(
    patch_loki_async_client, loki_response
):
    patch_loki_async_client(
        loki,
        response=loki_response(loki, {"data": {"result": []}}),
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
    patch_loki_async_client, exc, expected_error, expected_help
):
    patch_loki_async_client(loki, exc=exc)

    client = loki.LokiClient(base_url="http://test-loki:3100", timeout_seconds=5.0)
    result = await client.query_logs(service="backend")

    assert result["status"] == "error"
    assert result["error"] == expected_error
    assert result["help"] == expected_help
