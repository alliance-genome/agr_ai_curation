"""Lifecycle routing must preserve capability and feature-gate boundaries."""

from uuid import uuid4
import json
from itertools import product
from unittest.mock import AsyncMock, MagicMock, Mock

from fastapi import FastAPI, HTTPException
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
import pytest
from pydantic import TypeAdapter

from src.api import benchmark_jobs
from src.api.benchmark_auth import (
    require_benchmark_read, require_benchmark_cancel, require_benchmark_delete,
    require_benchmark_run,
)
from src.api.benchmark_curator import require_benchmark_curator
from src.lib.benchmarks.execution_context import BenchmarkCuratorContext
from src.lib.benchmarks.lifecycle import BenchmarkAdmissionResult
from src.lib.benchmarks.suites import resolve_suite, validate_suite
from tests.unit.lib.benchmarks.test_suites import _catalog, _payload


def submission_body():
    suite = _payload()
    plan = resolve_suite(validate_suite(suite), _catalog(), max_cases=100,
                         max_configurations=100, max_repetitions=100, max_cells=10000)
    return {"suite": suite, "plan": plan.model_dump(mode="json")}


def test_openapi_examples_match_canonical_request_and_response_models():
    app = FastAPI()
    app.include_router(benchmark_jobs.router)
    benchmark_jobs.examples.install_openapi_examples(app)
    document = app.openapi()
    for route in benchmark_jobs.router.routes:
        assert isinstance(route, APIRoute)
        for method in route.methods:
            operation = document["paths"][route.path][method.lower()]
            status = str(route.status_code or 200)
            response = operation["responses"][status]
            if status == "204":
                assert "content" not in response
            elif route.path.endswith("/events"):
                assert "event: benchmark.event" in response["content"]["text/event-stream"]["example"]
            else:
                example = response["content"]["application/json"]["example"]
                TypeAdapter(route.response_model).validate_json(json.dumps(example))
    submit = document["paths"]["/api/v1/benchmarks/jobs"]["post"]["requestBody"]["content"]["application/json"]["example"]
    body = benchmark_jobs.BenchmarkSubmitRequest.model_validate_json(json.dumps(submit))
    assert resolve_suite(body.suite, _catalog(), max_cases=1, max_configurations=1,
                         max_repetitions=1, max_cells=1) == body.plan
    rerun = document["paths"]["/api/v1/benchmarks/jobs/{job_id}/rerun"]["post"]["requestBody"]["content"]["application/json"]["example"]
    benchmark_jobs.BenchmarkRerunRequest.model_validate_json(json.dumps(rerun))


def admission_app(monkeypatch, *, human=True):
    app = FastAPI()
    app.dependency_overrides[require_benchmark_run] = lambda: {"sub": "service:portal", "client_id": "portal"}
    if human:
        app.dependency_overrides[require_benchmark_curator] = lambda: BenchmarkCuratorContext(
            subject="curator", auth_provider="oidc", db_user_id=42, active_groups=(),
        )
    runner = AsyncMock(return_value=BenchmarkAdmissionResult(uuid4(), False))
    monkeypatch.setattr(benchmark_jobs, "rerun_job", runner)
    monkeypatch.setattr(benchmark_jobs, "submit_job", runner)
    app.include_router(benchmark_jobs.router)
    return app, runner


@pytest.mark.parametrize("api,execution,worker", list(product((False, True), repeat=3)))
@pytest.mark.parametrize("operation", ["submit", "rerun"])
def test_admission_feature_matrix_preserves_worker_independence(monkeypatch, api, execution, worker, operation):
    for key, value in (("API", api), ("EXECUTION", execution), ("WORKER", worker)):
        monkeypatch.setenv(f"BENCHMARK_{key}_ENABLED", str(value).lower())
    app, runner = admission_app(monkeypatch)
    with TestClient(app) as client:
        path = "/api/v1/benchmarks/jobs" + (f"/{uuid4()}/rerun" if operation == "rerun" else "")
        response = client.post(path, json={} if operation == "rerun" else submission_body(), headers={"Idempotency-Key": "key"})
        assert response.status_code == (404 if not api else 409 if not execution else 202)
    assert runner.await_count == int(api and execution)


@pytest.mark.parametrize("operation", ["submit", "rerun"])
def test_m2m_only_admission_rejected_before_body_read(monkeypatch, operation):
    monkeypatch.setenv("BENCHMARK_API_ENABLED", "true")
    monkeypatch.setenv("BENCHMARK_EXECUTION_ENABLED", "true")
    app, runner = admission_app(monkeypatch, human=False)
    with TestClient(app) as client:
        path = "/api/v1/benchmarks/jobs" + (f"/{uuid4()}/rerun" if operation == "rerun" else "")
        response = client.post(path, content="invalid-secret-json", headers={"Idempotency-Key": "key"})
        assert response.status_code == 401
        assert "invalid-secret" not in response.text
    runner.assert_not_awaited()


@pytest.mark.parametrize("body,headers,expected", [
    ('{"cell_ids":[]}', {"Content-Type": "text/plain", "Idempotency-Key": "key"}, 415),
    ('{"curator_context":{"subject":"forged"}}', {"Content-Type": "application/json", "Idempotency-Key": "key"}, 422),
    ('{"cell_ids":["not-a-uuid"]}', {"Content-Type": "application/json", "Idempotency-Key": "key"}, 422),
    ('{}', {"Content-Type": "application/json"}, 422),
    ('{}', {"Content-Type": "application/json", "Idempotency-Key": "has space"}, 422),
    ('{}', {"Content-Type": "application/json", "Idempotency-Key": "key", "X-Benchmark-Delegated-Source-Authorization": "Bearer secret"}, 400),
])
def test_rerun_invalid_admission_never_calls_lifecycle(monkeypatch, body, headers, expected):
    monkeypatch.setenv("BENCHMARK_API_ENABLED", "true")
    monkeypatch.setenv("BENCHMARK_EXECUTION_ENABLED", "true")
    app, runner = admission_app(monkeypatch)
    with TestClient(app) as client:
        response = client.post(f"/api/v1/benchmarks/jobs/{uuid4()}/rerun", content=body, headers=headers)
        assert response.status_code == expected
        assert "forged" not in response.text
    runner.assert_not_awaited()


def test_rerun_body_limit_and_self_contained_openapi(monkeypatch):
    monkeypatch.setenv("BENCHMARK_API_ENABLED", "true")
    monkeypatch.setenv("BENCHMARK_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("BENCHMARK_ADMISSION_MAX_BYTES", "1")
    app, runner = admission_app(monkeypatch)
    with TestClient(app) as client:
        response = client.post(f"/api/v1/benchmarks/jobs/{uuid4()}/rerun", json={}, headers={"Idempotency-Key": "key"})
        assert response.status_code == 413
    runner.assert_not_awaited()
    operation = app.openapi()["paths"]["/api/v1/benchmarks/jobs/{job_id}/rerun"]["post"]
    schema = operation["requestBody"]["content"]["application/json"]["schema"]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["cell_ids"]["items"]["format"] == "uuid"
    assert "Idempotency-Key" in {item["name"] for item in operation["parameters"]}


def test_each_lifecycle_operation_has_its_scoped_capability():
    for route in benchmark_jobs.router.routes:
        assert isinstance(route, APIRoute)
        expected = (
            require_benchmark_run if route.path.endswith("/rerun") or (route.path.endswith("/jobs") and "POST" in route.methods) else
            require_benchmark_delete if "DELETE" in route.methods else
            require_benchmark_cancel if "POST" in route.methods else
            require_benchmark_read
        )
        assert expected in [dependency.call for dependency in route.dependant.dependencies]


@pytest.mark.parametrize("status,code", [(401, "authorization_required"), (403, "capability_required"), (503, "authorization_unavailable")])
def test_shared_auth_errors_have_versioned_envelope(monkeypatch, status, code):
    monkeypatch.setenv("BENCHMARK_API_ENABLED", "true")
    app = FastAPI()
    def deny():
        raise HTTPException(status, "synthetic-sensitive-provider-detail", headers={"WWW-Authenticate": "Bearer"})
    app.dependency_overrides[require_benchmark_read] = deny
    app.include_router(benchmark_jobs.router)
    with TestClient(app) as client:
        response = client.get("/api/v1/benchmarks/jobs")
    assert response.status_code == status
    assert response.json()["detail"]["code"] == code
    assert "sensitive" not in response.text
    assert response.headers["www-authenticate"] == "Bearer"
    errors = app.openapi()["paths"]["/api/v1/benchmarks/jobs"]["get"]["responses"]
    assert errors[str(status)]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/BenchmarkErrorResponse",
    }
    assert errors["422"]["content"]["application/json"]["schema"]["$ref"].endswith("BenchmarkErrorResponse")


def test_cancel_materializes_receipt_before_commit_unlocks_job(monkeypatch):
    session = MagicMock()
    session.__enter__.return_value = session
    monkeypatch.setattr(benchmark_jobs, "SessionLocal", Mock(return_value=session))
    repository = Mock()
    receipt = object()
    def get_job(**kwargs):
        session.commit.assert_not_called()
        return receipt
    repository.get_job.side_effect = get_job
    monkeypatch.setattr(benchmark_jobs, "BenchmarkRepository", Mock(return_value=repository))
    assert benchmark_jobs.cancel_job(uuid4(), {"sub": "owner"}) is receipt
    session.commit.assert_called_once()


@pytest.mark.parametrize("enabled", [False, True])
def test_disabled_or_unauthorized_requests_never_open_database(monkeypatch, enabled):
    monkeypatch.setenv("BENCHMARK_API_ENABLED", str(enabled).lower())
    session_factory = Mock(side_effect=AssertionError("unauthorized database access"))
    monkeypatch.setattr(benchmark_jobs, "SessionLocal", session_factory)
    app = FastAPI()
    def deny():
        raise HTTPException(401, "Not authenticated")
    for dependency in (require_benchmark_read, require_benchmark_cancel, require_benchmark_delete):
        app.dependency_overrides[dependency] = deny
    app.include_router(benchmark_jobs.router)
    path = f"/api/v1/benchmarks/jobs/{uuid4()}"
    with TestClient(app) as client:
        for method, suffix in (("GET", ""), ("GET", "/cells"), ("POST", "/cancel"), ("DELETE", "")):
            assert client.request(method, path + suffix).status_code == (401 if enabled else 404)
    session_factory.assert_not_called()


def test_invalid_requests_have_sanitized_errors_and_openapi_has_cursor_contract(monkeypatch):
    monkeypatch.setenv("BENCHMARK_API_ENABLED", "true")
    app = FastAPI()
    app.dependency_overrides[require_benchmark_read] = lambda: {"sub": "owner"}
    app.include_router(benchmark_jobs.router)
    with TestClient(app) as client:
        response = client.get("/api/v1/benchmarks/jobs/private-invalid-value")
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "invalid_request"
        assert "private-invalid-value" not in response.text
    operation = app.openapi()["paths"]["/api/v1/benchmarks/jobs"]["get"]
    assert {"cursor_created_at", "cursor_job_id", "limit", "status"} <= {
        parameter["name"] for parameter in operation["parameters"]
    }
