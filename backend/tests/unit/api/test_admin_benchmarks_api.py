import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute

import src.api.admin.benchmarks as benchmarks_api
from src.api.benchmark_auth import require_benchmark_read, require_benchmark_run
from src.lib.benchmarks.loader import BenchmarkCatalogError
from src.lib.benchmarks.models import (
    BenchmarkExecutionResponse,
    BenchmarkSelection,
)


def test_benchmark_routes_require_capability_specific_policy():
    dependencies = {
        route.path: [dependency.call for dependency in route.dependant.dependencies]
        for route in benchmarks_api.router.routes
        if isinstance(route, APIRoute)
    }
    assert require_benchmark_read in dependencies["/api/admin/benchmarks/profiles"]
    assert require_benchmark_read in dependencies["/api/admin/benchmarks/cases"]
    assert require_benchmark_read in dependencies["/api/admin/benchmarks/validate"]
    assert require_benchmark_run in dependencies["/api/admin/benchmarks/execute"]


def test_feature_gate_defaults_off_without_loading_catalog(monkeypatch):
    monkeypatch.delenv("BENCHMARK_ENABLED", raising=False)
    monkeypatch.setattr(
        benchmarks_api,
        "build_default_service",
        lambda: pytest.fail("disabled discovery must not load the catalog"),
    )
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(benchmarks_api.list_profiles(_principal={"sub": "operator"}))
    assert exc_info.value.status_code == 404


def test_enabled_admin_can_discover_profiles(monkeypatch):
    profile = SimpleNamespace(model_dump=lambda **_kwargs: {"profile_id": "profile-1"})
    service = SimpleNamespace(
        catalog=SimpleNamespace(profiles=[SimpleNamespace(profile=profile)])
    )
    monkeypatch.setattr(benchmarks_api, "get_benchmark_enabled", lambda: True)
    monkeypatch.setattr(benchmarks_api, "build_default_service", lambda: service)

    response = asyncio.run(
        benchmarks_api.list_profiles(_principal={"sub": "operator"})
    )
    assert response["profiles"] == [{"profile_id": "profile-1"}]


def test_validate_maps_bounded_selection_error(monkeypatch):
    service = SimpleNamespace(
        plan=lambda _selection: (_ for _ in ()).throw(
            BenchmarkCatalogError("Matrix contains 21 runs")
        )
    )
    monkeypatch.setattr(benchmarks_api, "get_benchmark_enabled", lambda: True)
    monkeypatch.setattr(benchmarks_api, "build_default_service", lambda: service)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            benchmarks_api.validate_selection(
                BenchmarkSelection(),
                _principal={"sub": "operator"},
            )
        )
    assert exc_info.value.status_code == 422
    assert "Matrix" in exc_info.value.detail


async def test_execute_invokes_shared_service(monkeypatch):
    expected = BenchmarkExecutionResponse(runs=[])

    class Service:
        async def execute(self, selection):
            assert selection == BenchmarkSelection(profile_ids=["profile-1"])
            return expected

    monkeypatch.setattr(benchmarks_api, "get_benchmark_enabled", lambda: True)
    monkeypatch.setattr(benchmarks_api, "build_default_service", Service)

    result = await benchmarks_api.execute_selection(
        BenchmarkSelection(profile_ids=["profile-1"]),
        _principal={"sub": "operator"},
    )
    assert result is expected
