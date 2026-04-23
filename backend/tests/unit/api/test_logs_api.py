"""Unit tests for the Loki-backed logs API endpoint."""

from datetime import datetime, timedelta, timezone
import logging

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


def test_allowed_log_levels_track_loki_patterns():
    assert logs_api.ALLOWED_LOG_LEVELS == frozenset(logs_api.loki.LOG_LEVEL_LABEL_PATTERNS)


@pytest.mark.asyncio
async def test_get_container_logs_queries_loki_with_default_lookback_and_service_label(
    frozen_now, patch_loki_async_client, loki_response
):
    capture = {}
    patch_loki_async_client(
        logs_api.loki,
        response=loki_response(
            logs_api.loki,
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
    assert payload.model_dump() == {
        "container": "backend",
        "lines": 2,
        "logs": "earlier line\nlater line\n",
    }


@pytest.mark.asyncio
async def test_get_container_logs_passes_since_level_and_limit_to_loki(
    frozen_now, patch_loki_async_client, loki_response
):
    capture = {}
    patch_loki_async_client(
        logs_api.loki,
        response=loki_response(
            logs_api.loki,
            {
                "data": {
                    "result": [
                        {
                            "stream": {"service": "backend", "level": "FATAL"},
                            "values": [["1742903100000000000", "FATAL line"]],
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
        level="fatal",
        since=15,
    )

    assert capture["params"]["query"] == (
        '{service="backend",level=~"(?i)^fatal$"}'
    )
    assert capture["params"]["limit"] == 150
    assert capture["params"]["start"] == logs_api.loki.normalize_time(
        frozen_now - timedelta(minutes=15)
    )
    assert capture["params"]["end"] == logs_api.loki.normalize_time(frozen_now)
    assert payload.model_dump() == {
        "container": "backend",
        "lines": 1,
        "logs": "FATAL line\n",
    }


@pytest.mark.asyncio
async def test_get_container_logs_returns_empty_payload_for_no_logs(
    patch_loki_async_client, loki_response
):
    capture = {}
    patch_loki_async_client(
        logs_api.loki,
        response=loki_response(logs_api.loki, {"data": {"result": []}}),
        capture=capture,
    )

    payload = await logs_api.get_container_logs("backend", lines=100)

    assert capture["params"]["query"] == '{service="backend"}'
    assert capture["params"]["limit"] == 100
    assert payload.model_dump() == {
        "container": "backend",
        "lines": 0,
        "logs": "",
    }


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
    patch_loki_async_client, exc, expected_error, expected_help, caplog
):
    patch_loki_async_client(logs_api.loki, exc=exc)
    caplog.set_level(logging.ERROR, logger=logs_api.logger.name)

    with pytest.raises(HTTPException) as error:
        await logs_api.get_container_logs("backend", lines=200)

    assert error.value.status_code == 500
    assert error.value.detail == "Failed to retrieve logs from Loki"
    assert expected_error in caplog.text
    assert expected_help in caplog.text


@pytest.mark.asyncio
async def test_get_container_logs_wraps_unexpected_errors(monkeypatch, caplog):
    async def _fake_query_logs(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(logs_api, "_query_logs", _fake_query_logs)
    caplog.set_level(logging.ERROR, logger=logs_api.logger.name)

    with pytest.raises(HTTPException) as exc:
        await logs_api.get_container_logs("backend", lines=200)

    assert exc.value.status_code == 500
    assert exc.value.detail == "Failed to retrieve logs"
    assert "boom" in caplog.text
