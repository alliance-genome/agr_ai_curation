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


@pytest.mark.parametrize("enabled, allowed", [(True, True), (True, False), (False, True)])
@pytest.mark.parametrize("job_summary", [False, True])
def test_accounting_requires_read_capability_and_api_gate(monkeypatch, enabled, allowed, job_summary):
    from src.lib.cost_ledger.facts import TokenUsage
    from src.schemas.cost_ledger import BenchmarkJobAccounting, CostFactsProjection, CostLedgerReference

    monkeypatch.setenv("BENCHMARK_API_ENABLED", str(enabled).lower())
    app = FastAPI()
    def principal():
        if not allowed:
            raise HTTPException(403, "read capability required")
        return {"sub": "service:portal"}
    app.dependency_overrides[require_benchmark_read] = principal
    app.include_router(benchmark_jobs.router)
    projection = CostFactsProjection(
        schema_version=1,
        reference=CostLedgerReference(schema_version=1, deployment_id="fixture", attempt_id=uuid4(), fact_revision=0),
        usage=TokenUsage(), usage_status="missing", usage_issues=(), recorded_charge=None,
    )
    job, cell, invocation = uuid4(), uuid4(), uuid4()
    if job_summary:
        projection = BenchmarkJobAccounting(job_id=job, invocation_count=0, attempt_count=0, usage={},
                                            inconsistent_usage_attempts=0, recorded_charges=(), unknown_charge_attempts=0)
    reader = Mock(return_value=projection)
    sessions = MagicMock()
    monkeypatch.setattr(benchmark_jobs, "SessionLocal", sessions)
    monkeypatch.setattr(benchmark_jobs, "read_benchmark_job_accounting" if job_summary else "read_benchmark_accounting", reader)
    with TestClient(app) as client:
        suffix = "" if job_summary else f"/cells/{cell}/invocations/{invocation}"
        result = client.get(f"/api/v1/benchmarks/jobs/{job}{suffix}/accounting?revision=0")
    if enabled and allowed:
        assert result.status_code == 200 and result.headers["cache-control"] == "no-store"
        assert result.json() == projection.model_dump(mode="json")
        expected = dict(job_id=job, owner_subject="service:portal")
        if not job_summary:
            expected.update(cell_id=cell, invocation_id=invocation, revision=0)
        assert reader.call_args.kwargs == expected
        if job_summary:
            assert str(sessions.return_value.__enter__.return_value.execute.call_args.args[0]) == "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
        sessions.return_value.__enter__.return_value.commit.assert_not_called()
    else:
        assert result.status_code == (404 if not enabled else 403)
        reader.assert_not_called()
        sessions.assert_not_called()


@pytest.mark.parametrize("failure, expected", [("missing", 404), ("unbound", 503), ("database", 503), ("inconsistent", 503)])
@pytest.mark.parametrize("job_summary", [False, True])
def test_accounting_errors_do_not_leak_or_fabricate_cost(monkeypatch, failure, expected, job_summary):
    from sqlalchemy.exc import OperationalError

    monkeypatch.setenv("BENCHMARK_API_ENABLED", "true")
    app = FastAPI()
    app.dependency_overrides[require_benchmark_read] = lambda: {"sub": "service:portal"}
    app.include_router(benchmark_jobs.router)
    errors = {
        "missing": LookupError("private source"),
        "unbound": benchmark_jobs.BenchmarkAccountingUnavailable("private source"),
        "database": OperationalError("secret SQL", {"secret": "private"}, Exception("credentials")),
        "inconsistent": ValueError("private facts"),
    }
    monkeypatch.setattr(benchmark_jobs, "SessionLocal", MagicMock())
    reader_name = "read_benchmark_job_accounting" if job_summary else "read_benchmark_accounting"
    monkeypatch.setattr(benchmark_jobs, reader_name, Mock(side_effect=errors[failure]))
    reporter = Mock()
    monkeypatch.setattr(benchmark_jobs, "report_runtime_exception", reporter)
    with TestClient(app) as client:
        suffix = "" if job_summary else f"/cells/{uuid4()}/invocations/{uuid4()}"
        result = client.get(f"/api/v1/benchmarks/jobs/{uuid4()}{suffix}/accounting")
    assert result.status_code == expected
    assert "private" not in result.text and "secret" not in result.text and "credentials" not in result.text
    assert "recorded_charge" not in result.json()
    if expected == 503:
        assert result.headers["cache-control"] == "no-store"
        assert result.json()["detail"]["code"] == "accounting_unavailable"
    if reporter.called:
        safe_error = reporter.call_args.args[0]
        assert safe_error.__context__ is None and safe_error.__cause__ is None
        assert "secret" not in str(safe_error) and "private" not in str(safe_error)


@pytest.mark.parametrize("allowed", [True, False])
def test_stage_page_preserves_unknowns_and_requires_read_capability(monkeypatch, allowed):
    from datetime import datetime, timezone
    from types import SimpleNamespace

    monkeypatch.setenv("BENCHMARK_API_ENABLED", "true")
    app = FastAPI()
    def principal():
        if not allowed:
            raise HTTPException(403, "read capability required")
        return {"sub": "service:portal", "client_id": "portal"}
    app.dependency_overrides[require_benchmark_read] = principal
    app.include_router(benchmark_jobs.router)
    job, cell = uuid4(), uuid4()
    row = SimpleNamespace(
        id=uuid4(), cell_id=cell, ordinal=0, attempt=1, stage_id="extractor",
        role="extraction", node_id=None, source_node_id=None, binding_id=None, agent_id="agent",
        parent_execution_id=None, parent_invocation_sequence=None, status="interrupted",
        started_at=datetime.now(timezone.utc), completed_at=None, elapsed_ms=None,
        failure_type="WorkerLeaseExpired",
    )
    repository = MagicMock()
    repository.list_stages.side_effect = [(row,), ()]
    monkeypatch.setattr(benchmark_jobs, "SessionLocal", MagicMock())
    constructor = Mock(return_value=repository)
    monkeypatch.setattr(benchmark_jobs, "BenchmarkRepository", constructor)
    with TestClient(app) as client:
        response = client.get(f"/api/v1/benchmarks/jobs/{job}/cells/{cell}/stages?limit=1")
    if not allowed:
        assert response.status_code == 403
        constructor.assert_not_called()
    else:
        assert response.status_code == 200
        body = response.json()
        assert body["schema_version"] == 1 and body["next_after_ordinal"] is None
        assert body["items"][0]["elapsed_ms"] is None
        assert body["items"][0]["completed_at"] is None
        assert repository.list_stages.call_args_list[1].kwargs["after_ordinal"] == 0
        assert repository.list_stages.call_args_list[0].kwargs["owner_subject"] == benchmark_jobs._owner(principal())


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
            elif route.path.endswith("/result"):
                assert response["content"]["application/json"]["example"]["invocations"] == []
                assert "X-Benchmark-Result-Digest" in response["headers"]
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
