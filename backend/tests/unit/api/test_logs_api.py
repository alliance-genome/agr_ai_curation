"""Unit tests for the Loki-backed logs API endpoint."""

from datetime import datetime, timedelta, timezone
import json
import logging

import httpx
import pytest
from fastapi import HTTPException

from src.api import logs as logs_api
from src.lib import http_errors


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
        frozen_now - timedelta(minutes=logs_api.DEFAULT_LOKI_LOOKBACK_MINUTES)
    )
    assert capture["params"]["end"] == logs_api.loki.normalize_time(frozen_now)
    assert payload.container == "backend"
    assert payload.lines == 2
    assert payload.logs == "earlier line\nlater line\n"
    assert payload.summary["matching_lines_in_page"] == 2
    assert payload.page["complete"] is True
    assert payload.page["next_call"] is None


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
    assert payload.container == "backend"
    assert payload.lines == 1
    assert payload.logs == "FATAL line\n"
    assert payload.filters == {"container": "backend", "level": "FATAL", "since": 15}


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
    assert payload.container == "backend"
    assert payload.lines == 0
    assert payload.logs == ""
    assert payload.page["complete"] is True


@pytest.mark.asyncio
async def test_get_container_logs_exact_char_cursor_reconstructs_oversized_line(monkeypatch):
    oversized = "quoted \\\" log 😀 " * 1200

    async def _fake_query_logs(*_args, **_kwargs):
        return [f"1742903100000000000\0{oversized}"]

    monkeypatch.setattr(logs_api, "_query_logs", _fake_query_logs)
    chunks = []
    char_cursor = 0
    while True:
        payload = await logs_api.get_container_logs(
            "backend",
            lines=1,
            since=15,
            char_cursor=char_cursor,
            max_chars=logs_api.MAX_LOG_CHARS,
        )
        chunks.append(payload.logs)
        provider_result = {"status": "success", "data": payload.model_dump(), "error": None}
        assert len(json.dumps(provider_result)) < 12_000
        if payload.page["next_char_cursor"] is None:
            break
        next_call = payload.page["next_call"]
        assert next_call["char_cursor"] > char_cursor
        char_cursor = next_call["char_cursor"]

    assert "".join(chunks) == oversized + "\n"


@pytest.mark.asyncio
async def test_get_container_logs_line_cursor_is_exact_and_non_overlapping(monkeypatch):
    monkeypatch.setattr(logs_api, "MAX_LOG_LINES", 2)
    query_ends = []
    results = [
        ["100\0older", "200\0newer"],
        [],
    ]

    async def _fake_query_logs(*_args, **kwargs):
        query_ends.append(kwargs["end"])
        return results.pop(0)

    monkeypatch.setattr(logs_api, "_query_logs", _fake_query_logs)
    first = await logs_api.get_container_logs("backend", lines=2, since=15)
    next_call = first.page["next_call"]
    assert next_call["line_cursor"] == "100"
    assert next_call["line_cursor_offset"] == 1
    assert next_call["char_cursor"] == 0

    second = await logs_api.get_container_logs(
        "backend",
        lines=next_call["lines"],
        since=next_call["since"],
        line_cursor=next_call["line_cursor"],
        line_cursor_offset=next_call["line_cursor_offset"],
        char_cursor=next_call["char_cursor"],
    )
    assert query_ends[1] == "100"
    assert second.page["complete"] is True


@pytest.mark.asyncio
async def test_get_container_logs_continues_within_equal_timestamp_group(monkeypatch):
    monkeypatch.setattr(logs_api, "MAX_LOG_LINES", 5)
    entries = [
        (90, "older"),
        *((100, f"same-{index}") for index in range(5)),
    ]

    async def _fake_query_logs(*_args, **kwargs):
        end = kwargs["end"]
        end_ns = int(end) if isinstance(end, str) else 10**30
        eligible = [entry for entry in entries if entry[0] <= end_ns]
        return [
            f"{timestamp}\0{line}"
            for timestamp, line in eligible[-kwargs["limit"]:]
        ]

    monkeypatch.setattr(logs_api, "_query_logs", _fake_query_logs)
    returned = []
    next_call = {
        "container": "backend",
        "lines": 2,
        "since": 15,
        "line_cursor": None,
        "line_cursor_offset": 0,
        "char_cursor": 0,
    }

    while True:
        payload = await logs_api.get_container_logs(**next_call)
        returned.extend(payload.logs.splitlines())
        if payload.page["complete"]:
            break
        next_call = payload.page["next_call"]

    assert returned == ["same-3", "same-4", "same-1", "same-2", "same-0", "older"]
    assert len(returned) == len(set(returned)) == len(entries)


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
    monkeypatch, patch_loki_async_client, exc, expected_error, expected_help, caplog
):
    report_calls = []
    patch_loki_async_client(logs_api.loki, exc=exc)

    def _fake_report_runtime_exception(exc, **kwargs):
        report_calls.append((exc, kwargs))
        return True

    monkeypatch.setattr(http_errors, "report_runtime_exception", _fake_report_runtime_exception)
    caplog.set_level(logging.ERROR, logger=logs_api.logger.name)

    with pytest.raises(HTTPException) as error:
        await logs_api.get_container_logs("backend", lines=200)

    assert error.value.status_code == 500
    assert error.value.detail == "Failed to retrieve logs from Loki"
    assert expected_error in caplog.text
    assert expected_help in caplog.text
    assert len(report_calls) == 1
    assert isinstance(report_calls[0][0], logs_api._LokiQueryError)
    assert str(report_calls[0][0]) == "Loki log query returned an error result"
    assert report_calls[0][0].__traceback__ is not None
    assert report_calls[0][1]["component"] == "api"
    assert report_calls[0][1]["operation"] == "sanitized_http_exception"
    assert report_calls[0][1]["context"]["logger_name"] == logs_api.logger.name
    assert report_calls[0][1]["context"]["status_code"] == 500


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
