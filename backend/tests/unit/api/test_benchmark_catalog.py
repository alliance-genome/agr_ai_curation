"""Read-only preview shares curator visibility/planning, never execution."""

from copy import deepcopy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

from fastapi import FastAPI, HTTPException
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from pydantic import TypeAdapter
import pytest

from src.api import benchmark_catalog as api
from src.api import benchmark_curator
from src.api.benchmark_auth import require_benchmark_read, require_benchmark_run
from src.lib.benchmarks.execution_context import BenchmarkCuratorContext
from src.lib.benchmarks.lifecycle import authoritative_plan
from src.lib.benchmarks.models import BenchmarkSuite
from src.lib.benchmarks.suites import _digest
from src.schemas.benchmark_catalog import BenchmarkPlanPreviewRequest, BenchmarkPlanPreviewResponse
from src.schemas.benchmark_jobs import BenchmarkErrorResponse
from tests.unit.lib.benchmarks.test_suites import _catalog, _payload


def _app():
    app = FastAPI()
    app.include_router(api.router)
    return app


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("BENCHMARK_API_ENABLED", "true")
    monkeypatch.setenv("BENCHMARK_EXECUTION_ENABLED", "false")
    monkeypatch.setenv("BENCHMARK_WORKER_ENABLED", "false")
    monkeypatch.setenv("BENCHMARK_ENVIRONMENT_ID", "synthetic-target")
    catalog = _catalog()
    suite = BenchmarkSuite.model_validate(_payload())
    monkeypatch.setattr(api, "load_checked_in_suites", lambda _root: (suite,))
    resolvers = SimpleNamespace(resolver_ids=("checked_in_fixture",), materialize=Mock(side_effect=AssertionError("no source I/O")))
    monkeypatch.setattr(api, "input_resolver_catalog", lambda _request: resolvers)
    factory = MagicMock()
    monkeypatch.setattr(api, "SessionLocal", factory)
    build = Mock(return_value=catalog)
    monkeypatch.setattr(api, "build_curator_route_catalog", build)
    app = _app()
    app.dependency_overrides[benchmark_curator.require_benchmark_read_curator] = lambda: BenchmarkCuratorContext(
        subject="curator", auth_provider="oidc", db_user_id=42, active_groups=("group-a",),
    )
    return TestClient(app), catalog, suite, factory, build, resolvers


def _request(catalog, suite):
    return {"catalog_digest": _digest(catalog.model_dump(mode="json")), "suite": suite.model_dump(mode="json")}


def test_catalog_uses_curator_context_and_reconstructs_authoritative_catalog(configured):
    client, catalog, _, factory, build, resolvers = configured
    reconstructed = {"schema_version": 1}
    for section in ("targets", "models", "route_slots"):
        data = client.get("/api/v1/benchmarks/catalog", params={"section": section}).json()
        reconstructed[section] = data["items"]
        assert data["catalog_digest"] == _digest(catalog.model_dump(mode="json"))
        assert data["environment_id"] == "synthetic-target"
        assert data["execution_enabled"] is False and data["worker_enabled"] is False
    assert reconstructed == catalog.model_dump(mode="json")
    context = build.call_args.args[1]
    assert context.db_user_id == 42 and context.active_groups == ("group-a",)
    factory.return_value.__enter__.return_value.commit.assert_not_called()
    factory.return_value.__enter__.return_value.add.assert_not_called()
    resolvers.materialize.assert_not_called()


def test_pagination_is_digest_bound_and_limits_configurable(configured, monkeypatch):
    client = configured[0]
    monkeypatch.setenv("BENCHMARK_DEFAULT_PAGE_SIZE", "1")
    monkeypatch.setenv("BENCHMARK_MAX_PAGE_SIZE", "1")
    path = "/api/v1/benchmarks/catalog"
    first = client.get(path, params={"section": "models", "limit": 50}).json()
    assert len(first["items"]) == 1 and first["total_items"] == 2
    params = {"section": "models", "cursor": first["next_cursor"], "catalog_digest": first["catalog_digest"]}
    second = client.get(path, params=params).json()
    assert second["next_cursor"] is None and second["items"] != first["items"]
    assert client.get(path, params={**params, "catalog_digest": "sha256:" + "0" * 64}).status_code == 409
    assert client.get(path, params={"cursor": first["next_cursor"]}).status_code == 422
    assert client.get(path, params={**params, "cursor": "invalid"}).status_code == 422
    assert client.get(path, params={"limit": 0}).status_code == 422


def test_preview_is_identical_to_admission_and_has_no_writes_or_source_calls(configured):
    client, catalog, suite, factory, _, resolvers = configured
    response = client.post("/api/v1/benchmarks/plans/validate", json=_request(catalog, suite))
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    preview = BenchmarkPlanPreviewResponse.model_validate_json(response.content)
    assert preview.cell_count == len(suite.cases) * len(suite.configurations) * suite.repetitions
    _, admitted = authoritative_plan(suite_value=suite.model_dump(mode="json"), submitted_plan=preview.plan, catalog=catalog)
    assert admitted == preview.plan
    assert preview.warnings[0].code == "inputs_not_materialized"
    factory.return_value.__enter__.return_value.commit.assert_not_called()
    factory.return_value.__enter__.return_value.add.assert_not_called()
    resolvers.materialize.assert_not_called()


@pytest.mark.parametrize("mutation", ["query", "model", "resolver", "url", "gold", "identity", "both", "neither"])
def test_invalid_preview_fails_with_sanitized_machine_error(configured, mutation):
    client, catalog, suite, *_ = configured
    payload = _request(catalog, suite)
    if mutation == "query":
        payload["suite"]["cases"][0]["target"] = {"kind": "agent", "id": "extractor"}
        payload["suite"]["cases"][0]["user_query"] = "  "
    elif mutation == "model":
        payload["suite"]["configurations"][0]["routes"] = {"supervisor": {"provider": "provider-a", "model": "not-installed"}}
    elif mutation == "resolver":
        payload["suite"]["cases"][0]["input"]["resolver"] = "unknown"
    elif mutation == "url":
        payload["suite"]["cases"][0]["input"]["reference"] = "https://private.invalid/paper"
    elif mutation in {"gold", "identity"}:
        payload[mutation] = "private-paper-text"
    elif mutation == "both":
        payload["checked_in_suite"] = {"suite_id": suite.suite_id, "suite_digest": _digest(suite.model_dump(mode="json"))}
    else:
        payload.pop("suite")
    response = client.post("/api/v1/benchmarks/plans/validate", json=payload)
    assert response.status_code == 422
    BenchmarkErrorResponse.model_validate(response.json())
    assert "private" not in response.text


def test_suite_discovery_detail_reference_preview_and_drift(configured, monkeypatch):
    client, catalog, suite, *_ = configured
    hidden_payload = deepcopy(suite.model_dump(mode="json"))
    hidden_payload["suite_id"] = "hidden"
    hidden_payload["cases"][0]["target"]["id"] = "not-visible"
    monkeypatch.setattr(api, "load_checked_in_suites", lambda _root: (BenchmarkSuite.model_validate(hidden_payload), suite))
    page = client.get("/api/v1/benchmarks/suites").json()
    assert [item["suite_id"] for item in page["items"]] == [suite.suite_id]
    assert client.get("/api/v1/benchmarks/suites/hidden").status_code == 404
    detail = client.get(f"/api/v1/benchmarks/suites/{suite.suite_id}").json()
    assert detail["suite"] == suite.model_dump(mode="json")
    payload = {"catalog_digest": _digest(catalog.model_dump(mode="json")), "checked_in_suite": {
        "suite_id": suite.suite_id, "suite_digest": detail["suite_digest"],
    }}
    assert client.post("/api/v1/benchmarks/plans/validate", json=payload).status_code == 200
    payload["checked_in_suite"]["suite_digest"] = "sha256:" + "0" * 64
    assert client.post("/api/v1/benchmarks/plans/validate", json=payload).json()["detail"]["code"] == "suite_drift"
    payload = _request(catalog, suite)
    payload["catalog_digest"] = "sha256:" + "0" * 64
    assert client.post("/api/v1/benchmarks/plans/validate", json=payload).status_code == 409


def test_request_response_and_plan_bounds(configured, monkeypatch):
    client, catalog, suite, *_ = configured
    path = "/api/v1/benchmarks/plans/validate"
    assert client.post(path, content="{}").status_code == 415
    monkeypatch.setenv("BENCHMARK_MAX_CELLS", "1")
    payload = _request(catalog, suite)
    payload["suite"]["repetitions"] = 2
    assert client.post(path, json=payload).status_code == 422
    monkeypatch.setenv("BENCHMARK_ADMISSION_MAX_BYTES", "1")
    assert client.post(path, json=payload).status_code == 413
    monkeypatch.setenv("BENCHMARK_CATALOG_MAX_RESPONSE_BYTES", "1")
    assert client.get("/api/v1/benchmarks/catalog").status_code == 413
    monkeypatch.setenv("BENCHMARK_ADMISSION_MAX_BYTES", "1048576")
    monkeypatch.setenv("BENCHMARK_MAX_CELLS", "250")
    assert client.post(path, json=payload).status_code == 413


def test_suite_pages_are_stable_and_reject_changed_inventory(configured, monkeypatch):
    client, _, suite, *_ = configured
    second = suite.model_copy(update={"suite_id": "suite-2"})
    monkeypatch.setattr(api, "load_checked_in_suites", lambda _root: (second, suite))
    path = "/api/v1/benchmarks/suites"
    first = client.get(path, params={"limit": 1}).json()
    assert first["items"][0]["suite_id"] == "suite-1" and first["next_cursor"] == "suite-1"
    params = {"limit": 1, "cursor": first["next_cursor"], "suite_catalog_digest": first["suite_catalog_digest"]}
    next_page = client.get(path, params=params).json()
    assert next_page["items"][0]["suite_id"] == "suite-2" and next_page["next_cursor"] is None
    monkeypatch.setattr(api, "load_checked_in_suites", lambda _root: (suite,))
    assert client.get(path, params=params).status_code == 409


@pytest.mark.parametrize("api_enabled,execution,worker", [(False, True, True), (True, False, False), (True, True, True)])
def test_discovery_and_preview_do_not_depend_on_execution_gates(configured, monkeypatch, api_enabled, execution, worker):
    client, catalog, suite, _, _, resolvers = configured
    for key, value in (("BENCHMARK_API_ENABLED", api_enabled), ("BENCHMARK_EXECUTION_ENABLED", execution), ("BENCHMARK_WORKER_ENABLED", worker)):
        monkeypatch.setenv(key, str(value).lower())
    expected = 200 if api_enabled else 404
    assert client.get("/api/v1/benchmarks/catalog").status_code == expected
    assert client.post("/api/v1/benchmarks/plans/validate", json=_request(catalog, suite)).status_code == expected
    resolvers.materialize.assert_not_called()


def test_api_gate_and_shared_auth_errors(configured, monkeypatch):
    client = configured[0]
    monkeypatch.setenv("BENCHMARK_API_ENABLED", "false")
    assert client.get("/api/v1/benchmarks/catalog").status_code == 404
    monkeypatch.setenv("BENCHMARK_API_ENABLED", "true")
    def denied():
        raise HTTPException(403, "private-auth-detail", headers={"X-Test": "preserved"})
    client.app.dependency_overrides[benchmark_curator.require_benchmark_read_curator] = denied
    response = client.get("/api/v1/benchmarks/catalog")
    assert response.status_code == 403 and response.headers["x-test"] == "preserved"
    assert "private" not in response.text


def test_m2m_read_needs_human_but_not_run_scope(configured, monkeypatch):
    client, _, _, _, build, _ = configured
    client.app.dependency_overrides.clear()
    read = Mock(return_value={"sub": "service:portal", "client_id": "portal", "token_use": "access"})
    client.app.dependency_overrides[require_benchmark_read] = lambda: read()
    run = Mock(side_effect=AssertionError("run capability is not a preview dependency"))
    client.app.dependency_overrides[require_benchmark_run] = lambda: run()
    assert client.get("/api/v1/benchmarks/catalog").status_code == 401
    build.assert_not_called()
    human = BenchmarkCuratorContext(subject="curator", auth_provider="oidc", db_user_id=42, active_groups=())
    verifier = AsyncMock(return_value=human)
    monkeypatch.setattr(benchmark_curator, "verify_benchmark_curator", verifier)
    assert client.get("/api/v1/benchmarks/catalog", headers={"X-Benchmark-Curator-Authorization": "Bearer human"}).status_code == 200
    assert verifier.call_args.args[2] == "Bearer human"
    read.assert_called()
    run.assert_not_called()


def test_catalog_failure_reporting_does_not_expose_dependency_text(configured, monkeypatch, caplog):
    client, _, _, _, build, _ = configured
    build.side_effect = RuntimeError("private-paper-text token-value")
    reporter = Mock()
    monkeypatch.setattr("src.lib.http_errors.report_runtime_exception", reporter)
    response = client.get("/api/v1/benchmarks/catalog")
    assert response.status_code == 503
    captured = reporter.call_args.args[0]
    assert captured.__traceback__ and captured.__context__ is None and captured.__cause__ is None
    assert "private-paper-text" not in response.text + str(captured) + caplog.text


def test_openapi_examples_use_canonical_models():
    document = _app().openapi()
    for route in api.router.routes:
        assert isinstance(route, APIRoute)
        operation = document["paths"][route.path][next(iter(route.methods)).lower()]
        example = operation["responses"]["200"]["content"]["application/json"]["example"]
        TypeAdapter(route.response_model).validate_json(json.dumps(example))
        for status in ("401", "403", "409", "422", "503"):
            BenchmarkErrorResponse.model_validate(operation["responses"][status]["content"]["application/json"]["example"])
    request_body = document["paths"]["/api/v1/benchmarks/plans/validate"]["post"]["requestBody"]["content"]["application/json"]
    BenchmarkPlanPreviewRequest.model_validate_json(json.dumps(request_body["example"]))
    assert "#/$defs" not in json.dumps(request_body["schema"])
