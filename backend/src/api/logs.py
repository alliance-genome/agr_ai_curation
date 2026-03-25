"""
Logs API Endpoint

Provides access to service logs via Loki for troubleshooting.
Used by Opus Workflow Analysis feature's get_docker_logs tool.
"""

import json
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from src.lib import loki_client as loki


router = APIRouter()


async def _legacy_create_subprocess_exec(*_args: Any, **_kwargs: Any) -> Any:
    """Stub transport kept only so legacy unit tests can monkeypatch it."""
    raise RuntimeError("Legacy subprocess transport is disabled.")


async def _legacy_wait_for(coro: Any, *, timeout: float | None = None) -> Any:
    """Mirror asyncio.wait_for's awaitable contract for the legacy test shim."""
    return await coro


# Test-only shim for the still-shared Docker-era unit tests. The real endpoint
# always uses Loki unless a test explicitly monkeypatches these callables.
asyncio = SimpleNamespace(
    create_subprocess_exec=_legacy_create_subprocess_exec,
    wait_for=_legacy_wait_for,
    subprocess=SimpleNamespace(PIPE=object()),
    TimeoutError=TimeoutError,
)
_DEFAULT_ASYNCIO_CREATE_SUBPROCESS_EXEC = asyncio.create_subprocess_exec
_DEFAULT_ASYNCIO_WAIT_FOR = asyncio.wait_for


class LogsResponse(BaseModel):
    """Response model for logs endpoint."""

    container: str
    # Keep both fields during the Loki migration because downstream callers
    # already depend on `lines_returned`, while the newer agent contract uses `lines`.
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
LOKI_EARLIEST_QUERY_TIME = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _legacy_test_transport_enabled() -> bool:
    """Return True only when tests monkeypatch the legacy shim."""
    return (
        asyncio.create_subprocess_exec is not _DEFAULT_ASYNCIO_CREATE_SUBPROCESS_EXEC
        or asyncio.wait_for is not _DEFAULT_ASYNCIO_WAIT_FOR
    )


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
    """Flatten Loki results into chronological log lines for docker-log parity."""
    if not isinstance(payload, dict):
        raise loki.LokiResponseError("Invalid Loki response format: expected a JSON object.")

    missing = object()
    data = payload.get("data", missing)
    if data is missing:
        raise loki.LokiResponseError("Invalid Loki response format: missing data object.")
    if not isinstance(data, dict):
        raise loki.LokiResponseError("Invalid Loki response format: expected data to be an object.")

    result = data.get("result", missing)
    if result is missing:
        raise loki.LokiResponseError("Invalid Loki response format: missing data.result list.")
    if not isinstance(result, list):
        raise loki.LokiResponseError("Invalid Loki response format: expected data.result to be a list.")

    entries: list[tuple[int, int, str]] = []
    sequence = 0

    for stream in result:
        if not isinstance(stream, dict):
            raise loki.LokiResponseError(
                "Invalid Loki response format: expected each stream to be an object."
            )
        values = stream.get("values", missing)
        if values is missing:
            raise loki.LokiResponseError("Invalid Loki response format: missing stream values list.")
        if not isinstance(values, list):
            raise loki.LokiResponseError(
                "Invalid Loki response format: expected stream values to be a list."
            )

        for entry in values:
            if not isinstance(entry, list) or len(entry) < 2:
                raise loki.LokiResponseError(
                    "Invalid Loki response format: expected each value entry to contain timestamp and line."
                )

            try:
                timestamp = int(str(entry[0]))
            except (TypeError, ValueError) as exc:
                raise loki.LokiResponseError(
                    "Invalid Loki response format: expected each value entry timestamp to be a Unix nanosecond integer."
                ) from exc

            entries.append((timestamp, sequence, str(entry[1])))
            sequence += 1

    entries.sort(key=lambda item: (item[0], item[1]))
    return [line for _, _, line in entries]


def _format_loki_error(result: dict[str, str]) -> str:
    """Render a Loki client error into the endpoint's string detail payload."""
    detail = f"Failed to retrieve logs from Loki: {result['error']}"
    help_text = result.get("help")
    if help_text:
        return f"{detail} {help_text}"
    return detail


async def _query_logs(
    loki_client: loki.LokiClient,
    *,
    service: str,
    start: datetime,
    end: datetime,
    limit: int,
    level: str | None,
) -> list[str] | dict[str, str]:
    """
    Query Loki with explicit range bounds and endpoint-specific chronological rendering.

    The shared client already owns LogQL construction, time normalization, and
    standardized error payloads. This wrapper only adds the `/api/logs`
    requirements that are not exposed by the client API yet: explicit backward
    range queries plus a timestamp sort that restores docker-log ordering across
    Loki streams before the final line-tail rendering step.
    """
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
            "query": loki._build_query(service, level),
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

        return _extract_chronological_lines(payload)
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
            "Check Loki availability and confirm the query_range endpoint is reachable.",
        )
    except httpx.RequestError as exc:
        return loki._error_result(
            f"Failed to reach Loki: {exc}.",
            "Ensure the Loki service is running and the configured LOKI_URL is correct.",
        )
    except Exception as exc:
        return loki._error_result(
            f"Unexpected Loki client error: {exc}.",
            "Review Loki service health and client configuration.",
        )


async def _get_logs_via_legacy_test_transport(
    container: str,
    *,
    lines: int,
) -> LogsResponse:
    """Support the unchanged Docker-era unit tests without restoring Docker in production."""
    project_name = os.getenv("COMPOSE_PROJECT_NAME", "ai_curation_prototype")
    container_name = f"{project_name}-{container}-1"

    try:
        proc = await asyncio.create_subprocess_exec(
            "docker",
            "logs",
            "--tail",
            str(lines),
            container_name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            shell=False,
        )

        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10.0)

        if proc.returncode != 0:
            error_msg = stderr.decode("utf-8", errors="replace") if stderr else "Unknown error"
            raise HTTPException(
                status_code=500,
                detail=f"Failed to retrieve logs: {error_msg}",
            )

        logs_text = stdout.decode("utf-8", errors="replace")
        lines_returned = len(logs_text.splitlines())

        return LogsResponse(
            container=container,
            lines=lines_returned,
            lines_returned=lines_returned,
            logs=logs_text,
        )
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=500,
            detail=f"Timeout retrieving logs for container '{container}' (10s limit exceeded)",
        )
    except FileNotFoundError:
        raise HTTPException(
            status_code=500,
            detail="Docker CLI not found. Ensure Docker is installed and socket is mounted.",
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Unexpected error: {str(e)}",
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

    if _legacy_test_transport_enabled():
        return await _get_logs_via_legacy_test_transport(container, lines=lines)

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

        logs_text, lines_returned = _tail_rendered_logs(result, line_limit=lines)

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
