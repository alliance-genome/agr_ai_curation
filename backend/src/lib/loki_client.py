"""Async Loki client helpers for backend log retrieval."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Literal, TypeAlias, TypedDict

import httpx

DEFAULT_LOKI_URL = "http://loki:3100"
DEFAULT_TIMEOUT_SECONDS = 10.0
DEFAULT_LIMIT = 2000
LOKI_QUERY_RANGE_PATH = "/loki/api/v1/query_range"

TimeInput: TypeAlias = datetime | int | str


class LokiQueryData(TypedDict):
    """Successful Loki query payload returned to callers."""

    service: str
    query: str
    start: str | None
    end: str | None
    limit: int
    lines: list[str]
    line_count: int


class LokiQuerySuccess(TypedDict):
    """Success result for Loki log queries."""

    status: Literal["success"]
    data: LokiQueryData
    error: None


class LokiQueryError(TypedDict):
    """Error result for Loki log queries."""

    status: Literal["error"]
    data: None
    error: str
    help: str


LokiQueryResult: TypeAlias = LokiQuerySuccess | LokiQueryError


def get_loki_url() -> str:
    """Return the configured Loki base URL."""
    return os.getenv("LOKI_URL", DEFAULT_LOKI_URL).rstrip("/")


def _as_utc(dt_value: datetime) -> datetime:
    """Normalize a datetime to UTC."""
    if dt_value.tzinfo is None:
        return dt_value.replace(tzinfo=timezone.utc)
    return dt_value.astimezone(timezone.utc)


def _datetime_to_unix_ns(dt_value: datetime) -> str:
    """Convert a datetime to a Unix nanosecond timestamp string."""
    normalized = _as_utc(dt_value)
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    delta = normalized - epoch
    total_nanoseconds = (
        ((delta.days * 24 * 60 * 60) + delta.seconds) * 1_000_000_000
        + (delta.microseconds * 1_000)
    )
    return str(total_nanoseconds)


def _normalize_time(value: TimeInput | None) -> str | None:
    """Normalize an ISO 8601 or Unix nanoseconds value for Loki."""
    if value is None:
        return None

    if isinstance(value, datetime):
        return _datetime_to_unix_ns(value)

    if isinstance(value, int):
        if value < 0:
            raise ValueError("Unix nanosecond timestamps must be positive integers.")
        return str(value)

    normalized = str(value).strip()
    if not normalized:
        raise ValueError("Timestamp values cannot be blank.")

    if normalized.isdigit():
        return normalized

    iso_value = normalized.replace("Z", "+00:00") if normalized.endswith("Z") else normalized

    try:
        parsed = datetime.fromisoformat(iso_value)
    except ValueError as exc:
        raise ValueError(
            "Timestamp values must be ISO 8601 strings or Unix nanosecond integers."
        ) from exc

    return _datetime_to_unix_ns(parsed)


def _escape_logql_literal(value: str) -> str:
    """Escape a string for safe inclusion in a LogQL quoted literal."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _build_query(service: str, level: str | None = None) -> str:
    """Build a Loki LogQL query for one service and optional level filter."""
    normalized_service = service.strip()
    if not normalized_service:
        raise ValueError("Service label is required.")

    query = f'{{service="{_escape_logql_literal(normalized_service)}"}}'

    if level:
        normalized_level = level.strip().upper()
        if not normalized_level:
            raise ValueError("Log level filter cannot be blank.")
        query = f'{query} |= "{_escape_logql_literal(normalized_level)}"'

    return query


def _extract_lines(payload: dict[str, Any]) -> list[str]:
    """Flatten Loki query results into plain log lines."""
    if not isinstance(payload, dict):
        raise ValueError("Invalid Loki response format: expected a JSON object.")

    data = payload.get("data", {})
    if not isinstance(data, dict):
        raise ValueError("Invalid Loki response format: expected data to be an object.")

    result = data.get("result", [])
    if not isinstance(result, list):
        raise ValueError("Invalid Loki response format: expected data.result to be a list.")

    lines: list[str] = []
    for stream in result:
        if not isinstance(stream, dict):
            raise ValueError("Invalid Loki response format: expected each stream to be an object.")
        values = stream.get("values", [])
        if not isinstance(values, list):
            raise ValueError("Invalid Loki response format: expected stream values to be a list.")

        for entry in values:
            if not isinstance(entry, list) or len(entry) < 2:
                raise ValueError(
                    "Invalid Loki response format: expected each value entry to contain timestamp and line."
                )
            lines.append(str(entry[1]))

    return lines


class LokiClient:
    """Async wrapper around Loki's `query_range` HTTP API."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        """Initialize the client with configurable base URL and timeout."""
        self.base_url = (base_url or get_loki_url()).rstrip("/")
        self.timeout = httpx.Timeout(timeout_seconds)

    async def query_logs(
        self,
        *,
        service: str = "backend",
        start: TimeInput | None = None,
        end: TimeInput | None = None,
        limit: int = DEFAULT_LIMIT,
        level: str | None = None,
    ) -> LokiQueryResult:
        """Query Loki logs for one service and return parsed log lines."""
        try:
            if limit < 1:
                raise ValueError("Limit must be greater than zero.")

            normalized_service = service.strip()
            start_ns = _normalize_time(start)
            end_ns = _normalize_time(end)

            if start_ns and end_ns and int(start_ns) > int(end_ns):
                raise ValueError("Start timestamp must be less than or equal to end timestamp.")

            query = _build_query(normalized_service, level)
            params: dict[str, str | int] = {
                "query": query,
                "limit": limit,
            }

            if start_ns is not None:
                params["start"] = start_ns
            if end_ns is not None:
                params["end"] = end_ns

            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(
                    f"{self.base_url}{LOKI_QUERY_RANGE_PATH}",
                    params=params,
                )
                response.raise_for_status()

            try:
                payload = response.json()
            except json.JSONDecodeError:
                return {
                    "status": "error",
                    "data": None,
                    "error": "Loki returned an invalid JSON response.",
                    "help": "Check Loki service health and the query_range API response.",
                }

            lines = _extract_lines(payload)
            return {
                "status": "success",
                "data": {
                    "service": normalized_service,
                    "query": query,
                    "start": start_ns,
                    "end": end_ns,
                    "limit": limit,
                    "lines": lines,
                    "line_count": len(lines),
                },
                "error": None,
            }
        except ValueError as exc:
            return {
                "status": "error",
                "data": None,
                "error": str(exc),
                "help": "Provide a valid service label, positive limit, and ISO 8601 or Unix nanosecond timestamps.",
            }
        except httpx.TimeoutException:
            return {
                "status": "error",
                "data": None,
                "error": f"Timed out querying Loki at {self.base_url}.",
                "help": "Ensure the Loki service is running and responding on the configured LOKI_URL.",
            }
        except httpx.HTTPStatusError as exc:
            return {
                "status": "error",
                "data": None,
                "error": f"Loki query failed with HTTP {exc.response.status_code}.",
                "help": "Check Loki availability and confirm the query_range endpoint is reachable.",
            }
        except httpx.RequestError as exc:
            return {
                "status": "error",
                "data": None,
                "error": f"Failed to reach Loki: {exc}.",
                "help": "Ensure the Loki service is running and the configured LOKI_URL is correct.",
            }
        except Exception as exc:
            return {
                "status": "error",
                "data": None,
                "error": f"Unexpected Loki client error: {exc}.",
                "help": "Review Loki service health and client configuration.",
            }
