import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute

import src.api.admin.benchmarks as benchmarks_api
from src.api.admin.auth import require_admin
from src.lib.benchmarks.loader import BenchmarkCatalogError
from src.lib.benchmarks.models import (
    BenchmarkExecutionResponse,
    BenchmarkSelection,
)


def test_all_benchmark_routes_require_canonical_admin_policy():
    routes = [
        route for route in benchmarks_api.router.routes if isinstance(route, APIRoute)
    ]
    assert routes
    for route in routes:
        assert require_admin in [
            dependency.call for dependency in route.dependant.dependencies
        ]


def test_feature_gate_defaults_off_without_loading_catalog(monkeypatch):
    monkeypatch.delenv("BENCHMARK_ENABLED", raising=False)
    monkeypatch.setattr(
        benchmarks_api,
        "build_default_service",
        lambda: pytest.fail("disabled discovery must not load the catalog"),
    )
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(benchmarks_api.list_profiles(_admin={"email": "admin@example.org"}))
    assert exc_info.value.status_code == 404


def test_enabled_admin_can_discover_profiles(monkeypatch):
    profile = SimpleNamespace(model_dump=lambda **_kwargs: {"profile_id": "profile-1"})
    service = SimpleNamespace(
        catalog=SimpleNamespace(profiles=[SimpleNamespace(profile=profile)])
    )
    monkeypatch.setattr(benchmarks_api, "get_benchmark_enabled", lambda: True)
    monkeypatch.setattr(benchmarks_api, "build_default_service", lambda: service)

    response = asyncio.run(
        benchmarks_api.list_profiles(_admin={"email": "admin@example.org"})
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
                _admin={"email": "admin@example.org"},
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
        _admin={"email": "admin@example.org"},
    )
    assert result is expected
