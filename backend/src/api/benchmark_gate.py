"""Shared availability gates for the deployment-local benchmark API."""

from fastapi import Depends, HTTPException

from src.lib.openai_agents.config import (
    get_benchmark_api_enabled,
    get_benchmark_execution_enabled,
)


def require_benchmark_api() -> None:
    """Allow read/catalog operations independently of provider execution."""
    if not get_benchmark_api_enabled():
        raise HTTPException(
            status_code=404,
            detail={"code": "api_disabled", "message": "Benchmark API is disabled"},
        )


def require_benchmark_execution(
    _api: None = Depends(require_benchmark_api),
) -> None:
    """Gate new work; worker polling has its separate existing gate."""
    if not get_benchmark_execution_enabled():
        raise HTTPException(
            status_code=409,
            detail={
                "code": "execution_disabled",
                "message": "Benchmark execution is disabled",
            },
        )
