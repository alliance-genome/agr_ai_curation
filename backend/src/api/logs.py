"""
Logs API Endpoint

Provides access to service logs via Loki for troubleshooting.
Used by Opus Workflow Analysis feature's get_docker_logs tool.
"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from src.lib.loki_client import LokiClient


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


@router.get("/logs/{container}", response_model=LogsResponse)
async def get_container_logs(
    container: str,
    lines: int = Query(
        default=2000,
        ge=100,
        le=5000,
        description="Number of log lines to retrieve",
    ),
    level: str | None = Query(
        default=None,
        description="Optional log level filter: DEBUG, INFO, WARN, ERROR, or FATAL",
    ),
    since: int | None = Query(
        default=None,
        ge=1,
        description="Optional time filter in minutes ago",
    ),
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
    query_start = query_end - timedelta(minutes=since) if since is not None else None

    try:
        loki_client = LokiClient(timeout_seconds=10.0)
        result = await loki_client.query_logs(
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
