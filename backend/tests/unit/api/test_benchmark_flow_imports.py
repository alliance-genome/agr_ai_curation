"""Resolver flow import: signed for the caller, exact closure, plain curator outcomes."""

import base64
import json
import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from src.api import benchmark_curator
from src.api import benchmark_flow_imports as api
from src.lib.benchmarks.execution_context import BenchmarkCuratorContext
from src.lib.flow_transfer.config import FlowExportConfig
from src.lib.flow_transfer.importer import ImportConflict, ImportRefused, ImportResult
from src.lib.flow_transfer.signing import sign_bundle
from tests.unit.lib.flow_transfer.support import CURATOR_ISS, CURATOR_SUB, ISSUER, make_bundle

CURATOR = BenchmarkCuratorContext(subject=CURATOR_SUB, auth_provider="oidc", auth_issuer=CURATOR_ISS,
                                  db_user_id=42, active_groups=("group-alpha",))
PATH = "/api/v1/benchmarks/flow-imports"


@pytest.fixture
def setup(monkeypatch):
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    monkeypatch.setenv("BENCHMARK_API_ENABLED", "true")
    monkeypatch.setenv("FLOW_IMPORT_EXPORT_ISSUER", ISSUER)
    monkeypatch.setenv("FLOW_IMPORT_EXPORT_PUBLIC_KEY", base64.b64encode(public).decode())
    flow_id = uuid4()
    calls = SimpleNamespace(
        unchanged=Mock(return_value=None), dependencies=Mock(),
        flow=Mock(return_value=ImportResult(outcome="imported", flow_id=flow_id, version=1)),
        latest=Mock(return_value=None), latest_many=Mock(return_value=[]),
        contracts=Mock(return_value=SimpleNamespace(runnable=True, run_problem=None)),
    )
    monkeypatch.setattr(api, "SessionLocal", MagicMock())
    monkeypatch.setattr(api, "unchanged_import", calls.unchanged)
    monkeypatch.setattr(api, "import_dependencies", calls.dependencies)
    monkeypatch.setattr(api, "import_flow", calls.flow)
    monkeypatch.setattr(api, "latest_import", calls.latest)
    monkeypatch.setattr(api, "latest_imports", calls.latest_many)
    monkeypatch.setattr(api, "saved_flow_contracts", calls.contracts)
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[benchmark_curator.require_benchmark_curator] = lambda: CURATOR
    app.dependency_overrides[benchmark_curator.require_benchmark_read_curator] = lambda: CURATOR
    export = FlowExportConfig(signer=key, issuer=ISSUER, bearer_client_ids=("c",))
    return SimpleNamespace(client=TestClient(app), app=app, export=export, calls=calls, flow_id=flow_id)


def signed(setup, bundle=None, *, now=None, config=None):
    bundle = bundle or make_bundle()
    return {"bundle": bundle, "signature": sign_bundle(
        bundle, config=config or setup.export, now=now or datetime.now(timezone.utc))}


def post(setup, payload):
    return setup.client.post(PATH, content=json.dumps(payload),
                             headers={"Content-Type": "application/json"})


def test_an_import_runs_both_phases_and_reports_whether_the_flow_can_run(setup):
    response = post(setup, signed(setup))
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    body = response.json()
    assert (body["outcome"], body["version"], body["runnable"]) == ("imported", 1, True)
    assert body["flow_id"] == str(setup.flow_id) and body["reason"] is None
    ctx = setup.calls.dependencies.call_args.args[1]
    assert (ctx.user_id, ctx.subject, ctx.groups, ctx.export_issuer) == (42, CURATOR_SUB, ["group-alpha"], ISSUER)
    setup.calls.flow.assert_called_once()


def test_an_unchanged_version_writes_nothing(setup):
    setup.calls.unchanged.return_value = ImportResult(outcome="unchanged", flow_id=setup.flow_id, version=3)
    body = post(setup, signed(setup)).json()
    assert (body["outcome"], body["version"]) == ("unchanged", 3)
    setup.calls.dependencies.assert_not_called()


def _untrusted(setup, payload, caplog):
    response = post(setup, payload)
    assert response.status_code == 400 and response.json()["detail"] == {"code": "untrusted_bundle"}
    assert response.headers["Cache-Control"] == "no-store"
    setup.calls.dependencies.assert_not_called()
    assert payload["signature"] not in caplog.text


def test_a_bundle_signed_for_another_curator_is_untrusted(setup, caplog):
    bundle = make_bundle()
    bundle["exported_for"]["sub"] = "someone-else"
    _untrusted(setup, signed(setup, bundle), caplog)


def test_a_changed_bundle_is_untrusted(setup, caplog):
    payload = signed(setup)
    payload["bundle"]["flow"]["name"] = "Changed after signing"
    _untrusted(setup, payload, caplog)


def test_an_expired_or_foreign_signature_is_untrusted(setup, caplog):
    _untrusted(setup, signed(setup, now=datetime.now(timezone.utc) - timedelta(hours=1)), caplog)
    other = FlowExportConfig(signer=Ed25519PrivateKey.generate(), issuer=ISSUER, bearer_client_ids=("c",))
    _untrusted(setup, signed(setup, config=other), caplog)


def test_an_untrusted_bundle_logs_at_error(setup, caplog):
    payload = signed(setup)
    payload["bundle"]["flow"]["name"] = "Changed after signing"
    with caplog.at_level(logging.INFO, logger=api.logger.name):
        post(setup, payload)
    assert [(r.levelno, r.getMessage()) for r in caplog.records] == [(logging.ERROR, "flow_import_untrusted")]


def test_a_malformed_bundle_is_invalid(setup):
    bundle = make_bundle()
    bundle["agents"].clear()
    response = post(setup, signed(setup, bundle))
    assert response.status_code == 400 and response.json()["detail"] == {"code": "invalid_bundle"}


def _with_nan(payload: dict) -> bytes:
    """Python's json writes NaN tokens; a bundle can't be signed with one in it."""
    payload["bundle"]["flow"]["not_a_number"] = float("nan")
    return json.dumps(payload).encode()


@pytest.mark.parametrize("signer", ["ours", "foreign"])
def test_a_bundle_with_a_non_finite_number_is_invalid(setup, caplog, signer):
    config = setup.export if signer == "ours" else FlowExportConfig(
        signer=Ed25519PrivateKey.generate(), issuer=ISSUER, bearer_client_ids=("c",))
    raw = _with_nan(signed(setup, config=config))
    assert b"NaN" in raw
    with caplog.at_level(logging.INFO, logger=api.logger.name):
        response = setup.client.post(PATH, content=raw, headers={"Content-Type": "application/json"})
    assert response.status_code == 400 and response.json()["detail"] == {"code": "invalid_bundle"}
    assert response.headers["Cache-Control"] == "no-store"
    assert [(r.levelno, r.getMessage()) for r in caplog.records] == [(logging.ERROR, "flow_import_invalid")]
    setup.calls.dependencies.assert_not_called()


def test_an_oversized_body_is_refused_before_parsing(setup, monkeypatch):
    monkeypatch.setattr(api, "REQUEST_MAX_BYTES", 100)
    response = post(setup, signed(setup))
    assert response.status_code == 413 and response.json()["detail"] == {"code": "bundle_too_large"}
    assert response.headers["Cache-Control"] == "no-store"


def test_without_config_import_is_unavailable(setup, monkeypatch):
    monkeypatch.delenv("FLOW_IMPORT_EXPORT_ISSUER")
    monkeypatch.delenv("FLOW_IMPORT_EXPORT_PUBLIC_KEY")
    response = post(setup, signed(setup))
    assert response.status_code == 503 and response.json()["detail"] == {"code": "flow_import_not_configured"}


def test_an_unexpected_failure_is_a_sanitized_503(setup):
    setup.calls.flow.side_effect = RuntimeError("database detail that must not leak")
    response = post(setup, signed(setup))
    assert response.status_code == 503 and response.json()["detail"] == {"code": "flow_import_unavailable"}
    assert response.headers["Cache-Control"] == "no-store"
    assert "must not leak" not in response.text


def test_a_failed_runnability_report_keeps_the_committed_import(setup, caplog):
    setup.calls.contracts.side_effect = RuntimeError("report detail that must not leak")
    with caplog.at_level(logging.INFO, logger=api.logger.name):
        response = post(setup, signed(setup))
    assert response.status_code == 200
    body = response.json()
    assert (body["outcome"], body["version"], body["flow_id"]) == ("imported", 1, str(setup.flow_id))
    assert body["runnable"] is None and body["run_problem"] is None and body["reason"] is None
    assert [(r.levelno, r.getMessage()) for r in caplog.records] == [
        (logging.ERROR, "flow_import_report_failed"), (logging.INFO, "flow_import_done")]
    assert caplog.records[0].error_type == "RuntimeError"
    assert "must not leak" not in caplog.text and "must not leak" not in response.text
    assert all("must not leak" not in str(vars(record)) for record in caplog.records)


def test_a_refusal_is_a_200_with_its_reason_and_the_current_copy(setup, caplog):
    setup.calls.flow.side_effect = ImportRefused("fields_need_choosing", "layout")
    setup.calls.latest.return_value = SimpleNamespace(flow_id=setup.flow_id, version=2)
    with caplog.at_level(logging.INFO, logger=api.logger.name):
        body = post(setup, signed(setup)).json()
    assert (body["outcome"], body["reason"], body["version"]) == ("refused", "fields_need_choosing", 2)
    assert body["runnable"] is None
    assert [(r.levelno, r.getMessage()) for r in caplog.records] == [(logging.INFO, "flow_import_refused")]


def test_a_taken_revision_number_is_a_conflict(setup, caplog):
    setup.calls.dependencies.side_effect = ImportConflict("agent revision number")
    with caplog.at_level(logging.INFO, logger=api.logger.name):
        response = post(setup, signed(setup))
    assert response.status_code == 409 and response.json()["detail"] == {"code": "import_conflict"}
    assert response.headers["Cache-Control"] == "no-store"
    assert [(r.levelno, r.getMessage()) for r in caplog.records] == [(logging.ERROR, "flow_import_conflict")]


def test_a_curator_who_is_not_onboarded_gets_the_existing_403(setup):
    def not_onboarded():
        raise HTTPException(403, {"code": "curator_authorization_required",
                                  "message": "Verified current AI Curation curator authorization required"})
    setup.app.dependency_overrides[benchmark_curator.require_benchmark_curator] = not_onboarded
    response = post(setup, signed(setup))
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "curator_authorization_required"
    assert response.headers["Cache-Control"] == "no-store"


def test_the_list_shows_only_the_callers_latest_imports(setup):
    source = uuid4()
    setup.calls.latest_many.return_value = [SimpleNamespace(
        source_flow_id=source, flow_id=setup.flow_id, version=2, source_version="sha256:" + "1" * 64,
        imported_at=datetime(2026, 10, 6, tzinfo=timezone.utc))]
    response = setup.client.get(PATH, params={"source_flow_ids": f"{source},{uuid4()}"})
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert [item["version"] for item in response.json()["items"]] == [2]
    kwargs = setup.calls.latest_many.call_args.kwargs
    assert kwargs["user_id"] == 42 and kwargs["export_issuer"] == ISSUER and source in kwargs["source_flow_ids"]


def test_the_list_takes_at_most_fifty_ids(setup):
    many = ",".join(str(uuid4()) for _ in range(51))
    for value in (many, "not-a-uuid"):
        response = setup.client.get(PATH, params={"source_flow_ids": value})
        assert response.status_code == 422 and response.json()["detail"] == {"code": "invalid_request"}
        assert response.headers["Cache-Control"] == "no-store"


def test_the_routes_are_off_without_the_benchmark_api(setup, monkeypatch):
    monkeypatch.setenv("BENCHMARK_API_ENABLED", "false")
    response = post(setup, signed(setup))
    assert response.status_code == 404
    assert response.headers["Cache-Control"] == "no-store"
