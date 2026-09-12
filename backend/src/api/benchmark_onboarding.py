"""Explicit executor-local onboarding for a verified initiating human."""

from typing import Any

from anyio.to_thread import run_sync
from fastapi import APIRouter, Depends, Header, Request, Response

from src.api.benchmark_auth import require_benchmark_read
from src.api.benchmark_curator import _failure, _unavailable, verify_benchmark_human
from src.api.benchmark_gate import require_benchmark_api
from src.lib.benchmarks.curator_onboarding import (
    BenchmarkCuratorOnboardingReceipt,
    onboard_benchmark_curator,
)

router = APIRouter(
    prefix="/api/v1/benchmarks/curator",
    tags=["Benchmarks - Curator onboarding"],
    dependencies=[Depends(require_benchmark_api)],
)


@router.post("/onboard", response_model=BenchmarkCuratorOnboardingReceipt)
async def onboard_curator(
    request: Request,
    response: Response,
    orchestration: dict[str, Any] = Depends(require_benchmark_read),
    curator_authorization: str | None = Header(
        default=None, alias="X-Benchmark-Curator-Authorization"
    ),
) -> BenchmarkCuratorOnboardingReceipt:
    principal = await verify_benchmark_human(
        request, orchestration, curator_authorization
    )
    try:
        receipt = await run_sync(onboard_benchmark_curator, principal)
    except PermissionError:
        raise _failure(403, "curator_authorization_required") from None
    except Exception as error:
        _unavailable(error, "curator_onboarding")
    response.headers["Cache-Control"] = "no-store"
    return receipt
