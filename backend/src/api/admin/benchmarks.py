"""Feature-gated admin API for checked-in developer benchmarks."""

from fastapi import APIRouter, Depends, HTTPException

from src.api.benchmark_auth import require_benchmark_read, require_benchmark_run
from src.lib.benchmarks.loader import BenchmarkCatalogError
from src.lib.benchmarks.models import (
    BenchmarkExecutionResponse,
    BenchmarkSelection,
    DryRunPlan,
)
from src.lib.benchmarks.runtime import build_default_service
from src.lib.openai_agents.config import get_benchmark_enabled

router = APIRouter(prefix="/api/admin/benchmarks", tags=["Admin - Benchmarks"])


def _require_enabled() -> None:
    if not get_benchmark_enabled():
        raise HTTPException(status_code=404, detail="Benchmark API is disabled")


def _service():
    try:
        return build_default_service()
    except BenchmarkCatalogError as exc:
        raise HTTPException(
            status_code=500, detail="Benchmark catalog is invalid"
        ) from exc


@router.get("/profiles")
async def list_profiles(_principal: dict = Depends(require_benchmark_read)) -> dict:
    _require_enabled()
    service = _service()
    return {
        "schema_version": 1,
        "profiles": [
            loaded.profile.model_dump(mode="json")
            for loaded in service.catalog.profiles
        ],
    }


@router.get("/cases")
async def list_cases(_principal: dict = Depends(require_benchmark_read)) -> dict:
    _require_enabled()
    service = _service()
    return {
        "schema_version": 1,
        "cases": [
            {
                "profile_id": loaded.profile.profile_id,
                "case_id": case.case_id,
                "fixture_digest": case.fixture_digest,
                "fixture": case.fixture_path,
                "expected": case.expected_path,
            }
            for loaded in service.catalog.profiles
            for case in loaded.cases
        ],
    }


@router.post("/validate", response_model=DryRunPlan)
async def validate_selection(
    selection: BenchmarkSelection,
    _principal: dict = Depends(require_benchmark_read),
) -> DryRunPlan:
    _require_enabled()
    try:
        return _service().plan(selection)
    except BenchmarkCatalogError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/execute", response_model=BenchmarkExecutionResponse)
async def execute_selection(
    selection: BenchmarkSelection,
    _principal: dict = Depends(require_benchmark_run),
) -> BenchmarkExecutionResponse:
    _require_enabled()
    try:
        return await _service().execute(selection)
    except BenchmarkCatalogError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
