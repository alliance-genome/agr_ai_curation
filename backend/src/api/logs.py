"""
Logs API Endpoint

Provides access to Docker container logs for troubleshooting.
Used by Opus Workflow Analysis feature's get_docker_logs tool.
"""

import asyncio
import os
from typing import Optional
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel


router = APIRouter()


class LogsResponse(BaseModel):
    """Response model for logs endpoint."""
    container: str
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
    "trace_review_backend"
}


@router.get("/logs/{container}", response_model=LogsResponse)
async def get_container_logs(
    container: str,
    lines: int = Query(default=2000, ge=100, le=5000, description="Number of log lines to retrieve")
) -> LogsResponse:
    """
    Get Docker container logs.

    Args:
        container: Container name (must be in whitelist)
        lines: Number of lines to retrieve (100-5000)

    Returns:
        LogsResponse with container name, line count, and logs

    Raises:
        HTTPException 400: Invalid container name
        HTTPException 500: Failed to retrieve logs
    """
    # Validate container name against whitelist
    if container not in ALLOWED_CONTAINERS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid container name. Allowed: {', '.join(sorted(ALLOWED_CONTAINERS))}"
        )

    # Map service name to actual container name
    # Container naming pattern: ai_curation_prototype-{service}-1
    project_name = os.getenv("COMPOSE_PROJECT_NAME", "ai_curation_prototype")
    container_name = f"{project_name}-{container}-1"

    try:
        # Execute docker logs command (not compose logs, since we're inside a container)
        cmd = ["docker", "logs", "--tail", str(lines), container_name]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            shell=False
        )

        # Wait for command to complete with timeout
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10.0)

        if proc.returncode != 0:
            error_msg = stderr.decode('utf-8', errors='replace') if stderr else "Unknown error"
            raise HTTPException(
                status_code=500,
                detail=f"Failed to retrieve logs: {error_msg}"
            )

        logs_text = stdout.decode('utf-8', errors='replace')
        lines_returned = len(logs_text.splitlines())

        return LogsResponse(
            container=container,
            lines_returned=lines_returned,
            logs=logs_text
        )

    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=500,
            detail=f"Timeout retrieving logs for container '{container}' (10s limit exceeded)"
        )
    except FileNotFoundError:
        raise HTTPException(
            status_code=500,
            detail="Docker CLI not found. Ensure Docker is installed and socket is mounted."
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Unexpected error: {str(e)}"
        )
