"""
Logs API Endpoint

Provides access to service logs via Loki for troubleshooting.
Used by Opus Workflow Analysis feature's get_docker_logs tool.
"""

import json
from datetime import datetime, timedelta, timezone
from typing import Annotated

import httpx
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from src.lib import loki_client as loki


router = APIRouter()


class LogsResponse(BaseModel):
    """Response model for logs endpoint."""

    container: str
    lines: int
    lines_returned: int
    logs: str


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
ALLOWED_LOG_LEVELS = {"DEBUG", "INFO", "WARN", "ERROR", "FATAL"}
LOG_LEVEL_LABEL_MATCHERS = {
    "DEBUG": "(?i:debug)",
    "INFO": "(?i:info)",
    "WARN": "(?i:warn(?:ing)?)",
    "ERROR": "(?i:error)",
    "FATAL": "(?i:fatal)",
}
LOKI_EARLIEST_QUERY_TIME = datetime(1970, 1, 1, tzinfo=timezone.utc)


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


def _format_loki_error(result: dict[str, str]) -> str:
    """Render a Loki client error into the endpoint's string detail payload."""
    detail = f"Failed to retrieve logs from Loki: {result['error']}"
    help_text = result.get("help")
    if help_text:
        return f"{detail} {help_text}"
    return detail


def _build_log_query(service: str, level: str | None) -> str:
    """Build a LogQL selector for one service and optional extracted level label."""
    normalized_service = service.strip()
    if not normalized_service:
        raise ValueError("Service label is required.")

    matchers = [f'service="{loki._escape_logql_literal(normalized_service)}"']

    if level is not None:
        level_pattern = LOG_LEVEL_LABEL_MATCHERS[level]
        matchers.append(f'level=~"{loki._escape_logql_literal(level_pattern)}"')

    return "{" + ",".join(matchers) + "}"


async def _query_logs(
    loki_client: loki.LokiClient,
    *,
    service: str,
    start: datetime,
    end: datetime,
    limit: int,
    level: str | None,
) -> list[str] | dict[str, str]:
    """Query Loki with explicit range bounds and optional level-label filtering."""
    try:
        if limit < 1:
            raise ValueError("Limit must be greater than zero.")

        start_ns = loki._normalize_time(start)
        end_ns = loki._normalize_time(end)
        if start_ns is None or end_ns is None:
            raise ValueError("Start and end timestamps are required.")
        if int(start_ns) > int(end_ns):
            raise ValueError("Start timestamp must be less than or equal to end timestamp.")

        params: dict[str, str | int] = {
            "query": _build_log_query(service, level),
            "limit": limit,
            "start": start_ns,
            "end": end_ns,
            "direction": "backward",
        }

        async with httpx.AsyncClient(timeout=loki_client.timeout) as client:
            response = await client.get(
                f"{loki_client.base_url}{loki.LOKI_QUERY_RANGE_PATH}",
                params=params,
            )
            response.raise_for_status()

        try:
            payload = response.json()
        except json.JSONDecodeError:
            return loki._error_result(
                "Loki returned an invalid JSON response.",
                "Check Loki service health and the query_range API response.",
            )

        return loki._extract_lines(payload)
    except loki.LokiResponseError as exc:
        return loki._error_result(
            str(exc),
            "Check Loki service health and confirm the query_range response format is valid.",
        )
    except ValueError as exc:
        return loki._error_result(
            str(exc),
            "Provide a valid service label, positive limit, and ISO 8601 or Unix nanosecond timestamps.",
        )
    except httpx.TimeoutException:
        return loki._error_result(
            f"Timed out querying Loki at {loki_client.base_url}.",
            "Ensure the Loki service is running and responding on the configured LOKI_URL.",
        )
    except httpx.HTTPStatusError as exc:
        status_code = exc.response.status_code if exc.response is not None else "unknown"
        return loki._error_result(
            f"Loki query failed with HTTP {status_code}.",
            "Check Loki service health and the query_range API response.",
        )
    except httpx.RequestError as exc:
        return loki._error_result(
            f"Could not connect to Loki at {loki_client.base_url}: {exc}.",
            "Ensure the Loki service is running and reachable from the backend container.",
        )


@router.get("/logs/{container}", response_model=LogsResponse)
async def get_container_logs(
    container: str,
    lines: Annotated[
        int,
        Query(
            ge=100,
            le=5000,
            description="Number of log lines to retrieve",
        ),
    ] = 2000,
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
            description="Optional time filter in minutes ago",
        ),
    ] = None,
) -> LogsResponse:
    """
    Get service logs from Loki.

    Args:
        container: Service/container name (must be in whitelist)
        lines: Number of lines to retrieve (100-5000)
        level: Optional log level filter
        since: Optional time filter in minutes ago

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
    query_end = datetime.now(timezone.utc)
    query_start = (
        query_end - timedelta(minutes=since)
        if since is not None
        else LOKI_EARLIEST_QUERY_TIME
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

        if isinstance(result, dict) and result.get("status") == "error":
            raise HTTPException(
                status_code=500,
                detail=_format_loki_error(result),
            )

        logs_text = _join_log_lines(result)
        lines_returned = len(result)

        return LogsResponse(
            container=container,
            lines=lines_returned,
            lines_returned=lines_returned,
            logs=logs_text,
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Unexpected error: {str(e)}",
        )
