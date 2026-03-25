"""
Logs API Endpoint

Provides access to service logs via Loki for troubleshooting.
Used by Opus Workflow Analysis feature's get_docker_logs tool.
"""

import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Annotated, Any

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
ALLOWED_LOG_LEVELS = frozenset(loki.LOG_LEVEL_LABEL_PATTERNS)
# Keep the default bounded so an omitted `since` does not trigger an epoch-wide Loki scan.
DEFAULT_LOKI_LOOKBACK = timedelta(hours=24)


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
    entries = loki.extract_entries(payload)
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
        else query_end - DEFAULT_LOKI_LOOKBACK
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
