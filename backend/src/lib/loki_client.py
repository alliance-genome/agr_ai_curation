"""Async Loki client helpers for backend log retrieval."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Callable, Literal, TypeAlias, TypedDict

import httpx

DEFAULT_LOKI_URL = "http://loki:3100"
DEFAULT_TIMEOUT_SECONDS = 10.0
DEFAULT_LIMIT = 2000
LOKI_QUERY_RANGE_PATH = "/loki/api/v1/query_range"
LOG_LEVEL_LABEL_PATTERNS = {
    "DEBUG": "(?i)^debug$",
    "INFO": "(?i)^info$",
    "WARN": "(?i)^warn(?:ing)?$",
    "ERROR": "(?i)^error$",
    "FATAL": "(?i)^fatal$",
}

TimeInput: TypeAlias = datetime | int | str


class LokiResponseError(ValueError):
    """Raised when Loki returns an unexpected response structure."""


class LokiQueryError(TypedDict):
    """Error result for Loki log queries."""

    status: Literal["error"]
    error: str
    help: str


LokiQueryResult: TypeAlias = list[str] | LokiQueryError
LokiEntry: TypeAlias = tuple[int, int, str]
LokiPayloadExtractor: TypeAlias = Callable[[dict[str, Any]], list[str]]


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


def normalize_time(value: TimeInput | None) -> str | None:
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


def escape_logql_literal(value: str) -> str:
    """Escape a string for safe inclusion in a LogQL quoted literal."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def build_query(service: str, level: str | None = None) -> str:
    """Build a Loki LogQL query for one service and optional level label filter."""
    normalized_service = service.strip()
    if not normalized_service:
        raise ValueError("Service label is required.")

    matchers = [f'service="{escape_logql_literal(normalized_service)}"']

    if level:
        normalized_level = level.strip().upper()
        level_pattern = LOG_LEVEL_LABEL_PATTERNS.get(normalized_level)
        if not normalized_level or level_pattern is None:
            allowed_levels = ", ".join(sorted(LOG_LEVEL_LABEL_PATTERNS))
            raise ValueError(
                f"Log level filter must be one of: {allowed_levels}."
            )
        matchers.append(f'level=~"{escape_logql_literal(level_pattern)}"')

    return "{" + ",".join(matchers) + "}"


def extract_entries(payload: dict[str, Any]) -> list[LokiEntry]:
    """Validate a Loki response and return `(timestamp, sequence, line)` tuples."""
    if not isinstance(payload, dict):
        raise LokiResponseError("Invalid Loki response format: expected a JSON object.")

    missing = object()

    data = payload.get("data", missing)
    if data is missing:
        raise LokiResponseError("Invalid Loki response format: missing data object.")
    if not isinstance(data, dict):
        raise LokiResponseError("Invalid Loki response format: expected data to be an object.")

    result = data.get("result", missing)
    if result is missing:
        raise LokiResponseError("Invalid Loki response format: missing data.result list.")
    if not isinstance(result, list):
        raise LokiResponseError("Invalid Loki response format: expected data.result to be a list.")

    entries: list[LokiEntry] = []
    sequence = 0
    for stream in result:
        if not isinstance(stream, dict):
            raise LokiResponseError(
                "Invalid Loki response format: expected each stream to be an object."
            )
        values = stream.get("values", missing)
        if values is missing:
            raise LokiResponseError("Invalid Loki response format: missing stream values list.")
        if not isinstance(values, list):
            raise LokiResponseError(
                "Invalid Loki response format: expected stream values to be a list."
            )

        for entry in values:
            if not isinstance(entry, list) or len(entry) < 2:
                raise LokiResponseError(
                    "Invalid Loki response format: expected each value entry to contain timestamp and line."
                )
            try:
                timestamp = int(str(entry[0]))
            except (TypeError, ValueError) as exc:
                raise LokiResponseError(
                    "Invalid Loki response format: expected each value entry timestamp to be a Unix nanosecond integer."
                ) from exc

            entries.append((timestamp, sequence, str(entry[1])))
            sequence += 1

    return entries


def _extract_lines(payload: dict[str, Any]) -> list[str]:
    """Flatten Loki query results into plain log lines."""
    return [line for _, _, line in extract_entries(payload)]


def error_result(error: str, help_text: str) -> LokiQueryError:
    """Build a consistent error result for Loki query failures."""
    return {
        "status": "error",
        "error": error,
        "help": help_text,
    }


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
        direction: Literal["backward", "forward"] | None = None,
        extractor: LokiPayloadExtractor | None = None,
    ) -> LokiQueryResult:
        """Query Loki logs for one service and return parsed log lines."""
        try:
            if limit < 1:
                raise ValueError("Limit must be greater than zero.")

            normalized_service = service.strip()
            start_ns = normalize_time(start)
            end_ns = normalize_time(end)

            if start_ns and end_ns and int(start_ns) > int(end_ns):
                raise ValueError("Start timestamp must be less than or equal to end timestamp.")

            query = build_query(normalized_service, level)
            params: dict[str, str | int] = {
                "query": query,
                "limit": limit,
            }

            if start_ns is not None:
                params["start"] = start_ns
            if end_ns is not None:
                params["end"] = end_ns
            if direction is not None:
                params["direction"] = direction

            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(
                    f"{self.base_url}{LOKI_QUERY_RANGE_PATH}",
                    params=params,
                )
                response.raise_for_status()

            try:
                payload = response.json()
            except json.JSONDecodeError:
                return error_result(
                    "Loki returned an invalid JSON response.",
                    "Check Loki service health and the query_range API response.",
                )

            parser = extractor or _extract_lines
            lines = parser(payload)
            return lines
        except LokiResponseError as exc:
            return error_result(
                str(exc),
                "Check Loki service health and confirm the query_range response format is valid.",
            )
        except ValueError as exc:
            return error_result(
                str(exc),
                "Provide a valid service label, positive limit, and ISO 8601 or Unix nanosecond timestamps.",
            )
        except httpx.TimeoutException:
            return error_result(
                f"Timed out querying Loki at {self.base_url}.",
                "Ensure the Loki service is running and responding on the configured LOKI_URL.",
            )
        except httpx.HTTPStatusError as exc:
            return error_result(
                f"Loki query failed with HTTP {exc.response.status_code}.",
                "Check Loki availability and confirm the query_range endpoint is reachable.",
            )
        except httpx.RequestError as exc:
            return error_result(
                f"Failed to reach Loki: {exc}.",
                "Ensure the Loki service is running and the configured LOKI_URL is correct.",
            )
        except Exception as exc:
            return error_result(
                f"Unexpected Loki client error: {exc}.",
                "Review Loki service health and client configuration.",
            )
