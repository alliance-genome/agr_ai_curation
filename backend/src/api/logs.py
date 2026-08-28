"""
Logs API Endpoint

Provides access to service logs via Loki for troubleshooting.
Used by Agent Studio's get_service_logs tool.
"""

from datetime import datetime, timedelta, timezone
import json
import logging
from typing import Annotated, Any, NoReturn

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from src.lib import loki_client as loki
from src.lib.http_errors import raise_sanitized_http_exception
from src.lib.openai_agents.config import (
    get_agent_studio_service_log_default_lines,
    get_agent_studio_service_log_default_lookback_minutes,
    get_agent_studio_service_log_max_lines,
    get_agent_studio_service_log_max_lookback_minutes,
    get_agent_studio_service_log_page_max_chars,
)


router = APIRouter()
logger = logging.getLogger(__name__)


class LogsResponse(BaseModel):
    """Response model for logs endpoint."""

    container: str
    lines: int
    logs: str
    summary: dict[str, Any]
    filters: dict[str, Any]
    page: dict[str, Any]


class _LokiQueryError(RuntimeError):
    """Sanitized Loki query failure safe for Sentry exception capture."""


def _new_loki_query_error() -> _LokiQueryError:
    """Create a sanitized Loki query error with traceback context."""

    try:
        raise _LokiQueryError("Loki log query returned an error result")
    except _LokiQueryError as exc:
        return exc


# Whitelist of allowed containers (security measure)
ALLOWED_CONTAINERS = {
    "backend",
    "frontend",
    "weaviate",
    "postgres",
    "langfuse",
    "redis",
    "clickhouse",
    "minio",
    "trace_review_backend",
}

# Loki uses the Compose service name as the `service` label for these logs.
CONTAINER_TO_SERVICE_LABEL = {container: container for container in ALLOWED_CONTAINERS}
ALLOWED_LOG_LEVELS = frozenset(loki.LOG_LEVEL_LABEL_PATTERNS)
DEFAULT_LOG_LINES = get_agent_studio_service_log_default_lines()
MAX_LOG_LINES = get_agent_studio_service_log_max_lines()
MAX_LOG_CHARS = get_agent_studio_service_log_page_max_chars()
DEFAULT_LOKI_LOOKBACK_MINUTES = get_agent_studio_service_log_default_lookback_minutes()
MAX_LOKI_LOOKBACK_MINUTES = get_agent_studio_service_log_max_lookback_minutes()


def _normalize_log_level(level: str | None) -> str | None:
    """Normalize and validate an optional log level filter."""
    if level is None:
        return None

    normalized_level = level.strip().upper()
    if normalized_level in ALLOWED_LOG_LEVELS:
        return normalized_level

    raise HTTPException(
        status_code=400,
        detail=(
            "Invalid log level. Allowed values: "
            f"{', '.join(sorted(ALLOWED_LOG_LEVELS))}"
        ),
    )


def _join_log_lines(log_lines: list[str]) -> str:
    """Render Loki log lines into the newline-delimited payload expected by callers."""
    if not log_lines:
        return ""
    return "\n".join(log_lines) + "\n"


def _tail_rendered_logs(log_entries: list[str], *, line_limit: int) -> tuple[str, int]:
    """Apply the requested line limit after multiline entries are rendered."""
    rendered_logs = _join_log_lines(log_entries)
    if not rendered_logs:
        return "", 0

    rendered_lines = rendered_logs.splitlines(keepends=True)
    tailed_lines = rendered_lines[-line_limit:]
    return "".join(tailed_lines), len(tailed_lines)


def _extract_chronological_lines(payload: dict[str, Any]) -> list[str]:
    """Flatten Loki results into timestamp-addressable chronological entries."""
    entries = loki.extract_timestamped_entries(payload)
    entries.sort(key=lambda item: (item[0], item[1]))
    return [f"{timestamp}\0{line}" for timestamp, _, line in entries]


def _decode_timestamped_lines(entries: list[str]) -> list[tuple[int, str]]:
    decoded: list[tuple[int, str]] = []
    for entry in entries:
        timestamp, separator, line = entry.partition("\0")
        if not separator:
            raise ValueError("Loki timestamped entry is missing its internal separator")
        decoded.append((int(timestamp), line))
    return decoded


def _json_bounded_log_prefix(value: str, max_json_chars: int) -> str:
    """Return the longest prefix fitting the JSON-encoded content budget."""
    def encoded_chars(candidate: str) -> int:
        return max(0, len(json.dumps(candidate)) - 2)

    if encoded_chars(value) <= max_json_chars:
        return value
    low, high = 0, len(value)
    while low < high:
        midpoint = (low + high + 1) // 2
        if encoded_chars(value[:midpoint]) <= max_json_chars:
            low = midpoint
        else:
            high = midpoint - 1
    return value[:max(1, low)]


def _format_loki_error(result: loki.LokiQueryError) -> str:
    """Render a Loki client error into the endpoint's string detail payload."""
    detail = f"Failed to retrieve logs from Loki: {result['error']}"
    help_text = result.get("help")
    if help_text:
        return f"{detail} {help_text}"
    return detail


def _raise_loki_query_error(*, container: str, result: loki.LokiQueryError) -> NoReturn:
    """Log the full Loki failure details while returning a stable client message."""

    raise_sanitized_http_exception(
        logger,
        status_code=500,
        detail="Failed to retrieve logs from Loki",
        log_message=(
            f"Loki log query failed for container {container}: "
            f"{_format_loki_error(result)}"
        ),
        exc=_new_loki_query_error(),
    )


async def _query_logs(
    loki_client: loki.LokiClient,
    *,
    service: str,
    start: datetime,
    end: loki.TimeInput,
    limit: int,
    level: str | None,
) -> loki.LokiQueryResult:
    """
    Query Loki through the shared client with endpoint-specific chronological rendering.
    """
    return await loki_client.query_logs(
        service=service,
        start=start,
        end=end,
        limit=limit,
        level=level,
        direction="backward",
        extractor=_extract_chronological_lines,
    )


@router.get("/logs/{container}", response_model=LogsResponse)
async def get_container_logs(
    container: str,
    lines: Annotated[
        int,
        Query(
            ge=1,
            le=MAX_LOG_LINES,
            description="Number of log lines to retrieve",
        ),
    ] = DEFAULT_LOG_LINES,
    level: Annotated[
        str | None,
        Query(
            description="Optional log level filter: DEBUG, INFO, WARN, ERROR, or FATAL",
        ),
    ] = None,
    since: Annotated[
        int | None,
        Query(
            ge=1,
            le=MAX_LOKI_LOOKBACK_MINUTES,
            description="Optional time filter in minutes ago",
        ),
    ] = None,
    line_cursor: Annotated[
        str | None,
        Query(description="Unix-nanosecond upper-bound cursor from page.next_call"),
    ] = None,
    char_cursor: Annotated[int, Query(ge=0, description="Exact character offset within this line page")] = 0,
    max_chars: Annotated[
        int,
        Query(ge=1, le=MAX_LOG_CHARS, description="Maximum JSON-encoded log content characters"),
    ] = MAX_LOG_CHARS,
) -> LogsResponse:
    """
    Get service logs from Loki.

    Args:
        container: Service/container name (must be in whitelist)
        lines: Environment-bounded number of logical log lines to retrieve
        level: Optional log level filter
        since: Optional time filter in minutes ago
        line_cursor: Exact Unix-nanosecond cursor returned by a prior page
        char_cursor: Exact character cursor within the selected line page

    Returns:
        LogsResponse with container name, line count, and logs

    Raises:
        HTTPException 400: Invalid container name or log level
        HTTPException 500: Failed to retrieve logs
    """
    # Validate container name against whitelist
    if container not in ALLOWED_CONTAINERS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid container name. Allowed: {', '.join(sorted(ALLOWED_CONTAINERS))}"
        )

    normalized_level = _normalize_log_level(level)
    service_label = CONTAINER_TO_SERVICE_LABEL[container]
    query_now = datetime.now(timezone.utc)
    query_end: loki.TimeInput = line_cursor or query_now
    try:
        query_end_ns = int(loki.normalize_time(query_end) or "0")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    lookback_minutes = since or DEFAULT_LOKI_LOOKBACK_MINUTES
    query_start = (
        query_now - timedelta(minutes=lookback_minutes)
    )

    try:
        loki_client = loki.LokiClient(timeout_seconds=10.0)
        result = await _query_logs(
            loki_client,
            service=service_label,
            start=query_start,
            end=query_end,
            limit=lines,
            level=normalized_level,
        )

        if not isinstance(result, list):
            _raise_loki_query_error(container=container, result=result)

        timestamped_lines = _decode_timestamped_lines(result)
        selected = timestamped_lines[-lines:]
        rendered = _join_log_lines([line for _, line in selected])
        safe_char_cursor = min(char_cursor, len(rendered))
        logs_text = _json_bounded_log_prefix(rendered[safe_char_cursor:], max_chars)
        next_char_cursor = safe_char_cursor + len(logs_text)
        character_complete = next_char_cursor >= len(rendered)
        oldest_timestamp = selected[0][0] if selected else None
        line_complete = len(timestamped_lines) < lines or not selected
        complete = character_complete and line_complete
        next_call = None
        if not character_complete:
            next_call = {
                "container": container,
                "lines": lines,
                "level": normalized_level,
                "since": lookback_minutes,
                "line_cursor": str(query_end_ns),
                "char_cursor": next_char_cursor,
            }
        elif not line_complete and oldest_timestamp is not None:
            next_call = {
                "container": container,
                "lines": lines,
                "level": normalized_level,
                "since": lookback_minutes,
                "line_cursor": str(max(0, oldest_timestamp - 1)),
                "char_cursor": 0,
            }

        return LogsResponse(
            container=container,
            lines=len(selected),
            logs=logs_text,
            summary={
                "matching_lines_in_page": len(selected),
                "first_timestamp_ns": oldest_timestamp,
                "last_timestamp_ns": selected[-1][0] if selected else None,
                "returned_char_count": len(logs_text),
            },
            filters={
                "container": container,
                "level": normalized_level,
                "since": lookback_minutes,
            },
            page={
                "line_cursor": str(query_end_ns),
                "char_cursor": safe_char_cursor,
                "next_char_cursor": None if character_complete else next_char_cursor,
                "complete": complete,
                "truncated": not complete,
                "next_call": next_call,
            },
        )

    except HTTPException:
        raise
    except Exception as e:
        raise_sanitized_http_exception(
            logger,
            status_code=500,
            detail="Failed to retrieve logs",
            log_message=f"Unexpected error retrieving logs for container {container}",
            exc=e,
        )
