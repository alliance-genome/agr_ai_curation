"""
Logs API Endpoint

Provides access to service logs via Loki for troubleshooting.
Used by Agent Studio's get_service_logs tool.
"""

from datetime import datetime, timedelta, timezone
import logging
from typing import Annotated, Any, NoReturn

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from src.lib import loki_client as loki
from src.lib.http_errors import raise_sanitized_http_exception


router = APIRouter()
logger = logging.getLogger(__name__)


class LogsResponse(BaseModel):
    """Response model for logs endpoint."""

    container: str
    lines: int
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
    """Flatten Loki results into chronological log lines for API consumers."""
    entries = loki.extract_timestamped_entries(payload)
    entries.sort(key=lambda item: (item[0], item[1]))
    return [line for _, _, line in entries]


def _format_loki_error(result: dict[str, str]) -> str:
    """Render a Loki client error into the endpoint's string detail payload."""
    detail = f"Failed to retrieve logs from Loki: {result['error']}"
    help_text = result.get("help")
    if help_text:
        return f"{detail} {help_text}"
    return detail


def _raise_loki_query_error(*, container: str, result: dict[str, str]) -> NoReturn:
    """Log the full Loki failure details while returning a stable client message."""

    logger.error(
        "Loki log query failed for container %s: %s",
        container,
        _format_loki_error(result),
    )
    raise HTTPException(status_code=500, detail="Failed to retrieve logs from Loki")


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
            _raise_loki_query_error(container=container, result=result)

        logs_text, returned_line_count = _tail_rendered_logs(result, line_limit=lines)

        return LogsResponse(
            container=container,
            lines=returned_line_count,
            logs=logs_text,
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
