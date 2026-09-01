"""Focused API tests for scoped benchmark source materialization."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Annotated
from uuid import UUID

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from pydantic import Field, RootModel

import src.api.benchmark_sources as sources_api
from src.api import benchmark_auth
from src.api.benchmark_auth import require_benchmark_source_read
from src.lib.benchmarks.input_resolvers import (
    BenchmarkInputResolverCatalog,
    BenchmarkResolverRegistrationError,
    BenchmarkSourceError,
    CheckedInFixtureResolver,
    DelegatedAuthorizationCapability,
)
from src.lib.benchmarks.loader import BenchmarkCatalogError
from src.models.sql.database import get_db


class _DummyDb:
    def commit(self):
        pass

    def rollback(self):
        pass


class _SnapshotRepository:
    def __init__(self, _db, _store):
        pass

    def freeze_input(self, source, *, owner_subject, service_principal):
        return SimpleNamespace(
            id=UUID("00000000-0000-4000-8000-000000000001"),
            digest=source.digest,
            source_version=source.version,
            content_type=source.metadata.content_type,
            content_bytes=source.metadata.content_bytes,
            resolver_id=source.resolver,
            source_reference=source.reference,
            sanitized_provenance=source.provenance.model_dump(mode="json"),
            owner_subject=owner_subject,
            service_principal=service_principal,
            blob_reference="sha256/00/canonical",
            created_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        )

    def receipt(self, snapshot):
        return {
            "snapshot_id": snapshot.id,
            "digest": snapshot.digest,
            "source_version": snapshot.source_version,
            "content_type": snapshot.content_type,
            "content_bytes": snapshot.content_bytes,
            "resolver_id": snapshot.resolver_id,
            "source_reference": snapshot.source_reference,
            "sanitized_provenance": snapshot.sanitized_provenance,
            "owner_subject": snapshot.owner_subject,
            "service_principal": snapshot.service_principal,
            "blob_reference": snapshot.blob_reference,
            "created_at": snapshot.created_at,
        }


def _stub_snapshots(application, monkeypatch):
    monkeypatch.setattr(sources_api, "BenchmarkSnapshotRepository", _SnapshotRepository)
    monkeypatch.setattr(sources_api, "configured_benchmark_snapshot_store", object)
    application.dependency_overrides[get_db] = lambda: _DummyDb()


def _app(tmp_path, monkeypatch) -> FastAPI:
    monkeypatch.setattr(sources_api, "get_benchmark_enabled", lambda: True)
    application = FastAPI()
    application.state.benchmark_input_resolvers = BenchmarkInputResolverCatalog(
        [CheckedInFixtureResolver(tmp_path, allowed_references={"input.json"})],
        timeout_seconds=1,
        max_input_bytes=1024,
    )
    application.dependency_overrides[require_benchmark_source_read] = lambda: {
        "sub": "operator",
        "client_id": "portal-client",
    }
    _stub_snapshots(application, monkeypatch)
    application.include_router(sources_api.router)
    return application


def test_materialize_route_requires_benchmark_source_read_capability():
    route = next(
        route
        for route in sources_api.router.routes
        if isinstance(route, APIRoute) and route.path.endswith("/materialize")
    )
    assert require_benchmark_source_read in [
        dependency.call for dependency in route.dependant.dependencies
    ]


def test_install_registers_extensions_without_loading_checked_in_suites(
    monkeypatch,
):
    monkeypatch.setattr(
        sources_api,
        "load_checked_in_suites",
        lambda _root: (_ for _ in ()).throw(AssertionError("must load lazily")),
    )
    application = FastAPI()

    sources_api.install_benchmark_input_resolvers(application)

    assert application.state.benchmark_input_resolver_extensions == ()
    assert not hasattr(application.state, "benchmark_input_resolvers")


def test_install_rejects_duplicate_extension_id():
    class DuplicateLocalDocumentResolver:
        resolver_id = "local_document"
        reference_schema = RootModel[str]
        delegated_authorization = DelegatedAuthorizationCapability.UNSUPPORTED

        async def materialize(self, *args, **kwargs):
            raise AssertionError("duplicate registration must fail before use")

    with pytest.raises(BenchmarkResolverRegistrationError) as exc_info:
        sources_api.install_benchmark_input_resolvers(
            FastAPI(), extra_resolvers=(DuplicateLocalDocumentResolver(),)
        )

    assert exc_info.value.code == "duplicate_resolver"


def test_materialize_endpoint_returns_token_free_snapshot_receipt(tmp_path, monkeypatch):
    payload = b'{"messages": []}\n'
    (tmp_path / "input.json").write_bytes(payload)
    digest = f"sha256:{hashlib.sha256(payload).hexdigest()}"

    response = TestClient(_app(tmp_path, monkeypatch)).post(
        "/api/v1/benchmarks/sources/materialize",
        json={
            "resolver": "checked_in_fixture",
            "reference": "input.json",
            "version": "1",
            "digest": digest,
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "snapshot_id": "00000000-0000-4000-8000-000000000001",
        "digest": digest,
        "source_version": "1",
        "content_type": "application/json",
        "content_bytes": len(payload),
        "resolver_id": "checked_in_fixture",
        "source_reference": "input.json",
        "sanitized_provenance": {
            "resolver": "checked_in_fixture",
            "reference": "input.json",
            "version": "1",
            "digest": digest,
        },
        "owner_subject": "operator",
        "service_principal": "portal-client",
        "blob_reference": "sha256/00/canonical",
        "created_at": "2026-09-01T00:00:00Z",
    }


def test_materialize_endpoint_supports_browser_session_principal(
    tmp_path, monkeypatch
):
    payload = b'{"messages": []}\n'
    (tmp_path / "input.json").write_bytes(payload)
    digest = f"sha256:{hashlib.sha256(payload).hexdigest()}"
    application = _app(tmp_path, monkeypatch)
    application.dependency_overrides.pop(require_benchmark_source_read)

    async def browser_user(_request, _scopes):
        return {
            "sub": "human-operator",
            "uid": "operator-uid",
            "email": "operator@example.test",
            "groups": ["benchmark-source-readers"],
        }

    monkeypatch.setattr(
        benchmark_auth.browser_auth, "_get_user_from_cookie_impl", browser_user
    )
    monkeypatch.setattr(
        benchmark_auth,
        "get_benchmark_operator_capability_groups",
        lambda capability: ("benchmark-source-readers",)
        if capability == benchmark_auth.BENCHMARK_SOURCE_READ
        else (),
    )

    response = TestClient(application).post(
        "/api/v1/benchmarks/sources/materialize",
        json={
            "resolver": "checked_in_fixture",
            "reference": "input.json",
            "version": "1",
            "digest": digest,
        },
    )

    assert response.status_code == 200
    assert response.json()["owner_subject"] == "human-operator"
    assert (
        response.json()["service_principal"]
        == benchmark_auth.BENCHMARK_BROWSER_SESSION_CLIENT_ID
    )


def test_materialize_endpoint_builds_and_memoizes_catalog_lazily(
    tmp_path, monkeypatch
):
    payload = b'{"messages": []}\n'
    (tmp_path / "input.json").write_bytes(payload)
    digest = f"sha256:{hashlib.sha256(payload).hexdigest()}"
    catalog = BenchmarkInputResolverCatalog(
        [CheckedInFixtureResolver(tmp_path, allowed_references={"input.json"})],
        timeout_seconds=1,
        max_input_bytes=1024,
    )
    build_calls = []
    monkeypatch.setattr(sources_api, "get_benchmark_enabled", lambda: True)
    monkeypatch.setattr(
        sources_api,
        "build_default_input_resolver_catalog",
        lambda *, extra_resolvers: build_calls.append(tuple(extra_resolvers)) or catalog,
    )
    application = FastAPI()
    sources_api.install_benchmark_input_resolvers(application)
    application.dependency_overrides[require_benchmark_source_read] = lambda: {
        "sub": "operator",
        "client_id": "portal-client",
    }
    _stub_snapshots(application, monkeypatch)
    application.include_router(sources_api.router)
    client = TestClient(application)
    request = {
        "resolver": "checked_in_fixture",
        "reference": "input.json",
        "version": "1",
        "digest": digest,
    }

    first_response = client.post(
        "/api/v1/benchmarks/sources/materialize", json=request
    )
    second_response = client.post(
        "/api/v1/benchmarks/sources/materialize", json=request
    )

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert build_calls == [()]


def test_invalid_lazy_catalog_returns_sanitized_unavailable_error(monkeypatch):
    captured = {}

    def report_and_raise(_logger, *, status_code, detail, log_message, exc):
        captured.update(exc=exc, detail=detail)
        raise HTTPException(status_code=status_code, detail=detail)

    monkeypatch.setattr(sources_api, "get_benchmark_enabled", lambda: True)
    monkeypatch.setattr(
        sources_api,
        "build_default_input_resolver_catalog",
        lambda **_kwargs: (_ for _ in ()).throw(
            BenchmarkCatalogError("/private/package/suites is unavailable")
        ),
    )
    monkeypatch.setattr(
        sources_api, "raise_sanitized_http_exception", report_and_raise
    )
    application = FastAPI()
    sources_api.install_benchmark_input_resolvers(application)
    application.dependency_overrides[require_benchmark_source_read] = lambda: {
        "sub": "operator",
        "client_id": "portal-client",
    }
    application.include_router(sources_api.router)

    response = TestClient(application).post(
        "/api/v1/benchmarks/sources/materialize",
        json={
            "resolver": "checked_in_fixture",
            "reference": "input.json",
            "version": "1",
            "digest": "sha256:" + "0" * 64,
        },
    )

    assert response.status_code == 503
    assert response.json()["detail"]["error"] == "source_unavailable"
    assert captured["exc"].__context__ is None
    assert captured["exc"].__cause__ is None
    assert "private/package" not in str(captured["exc"])


def test_materialize_endpoint_normalizes_invalid_and_unknown_references(
    tmp_path, monkeypatch
):
    client = TestClient(_app(tmp_path, monkeypatch))
    invalid = client.post(
        "/api/v1/benchmarks/sources/materialize",
        json={
            "url": "https://example.test/paper",
            "resolver": "python.module:callable",
        },
    )
    assert invalid.status_code == 422
    assert invalid.json()["detail"]["error"] == "invalid_reference"

    unknown = client.post(
        "/api/v1/benchmarks/sources/materialize",
        json={
            "resolver": "private_remote",
            "reference": "approved-reference",
            "version": "1",
            "digest": "sha256:" + "0" * 64,
        },
    )
    assert unknown.status_code == 422
    assert unknown.json()["detail"]["error"] == "unknown_resolver"


@pytest.mark.parametrize(
    "header",
    ["", "opaque-token", "Basic opaque-token", "Bearer ", "bearer opaque-token"],
)
def test_materialize_endpoint_rejects_malformed_delegated_header(
    tmp_path, monkeypatch, header
):
    client = TestClient(_app(tmp_path, monkeypatch))
    response = client.post(
        "/api/v1/benchmarks/sources/materialize",
        headers={"X-Benchmark-Delegated-Source-Authorization": header},
        json={
            "resolver": "checked_in_fixture",
            "reference": "input.json",
            "version": "1",
            "digest": "sha256:" + "0" * 64,
        },
    )
    assert response.status_code == 400
    assert response.json()["detail"]["error"] == "invalid_delegated_authorization"


def test_materialize_endpoint_rejects_oversized_or_unexpected_delegated_header(
    tmp_path, monkeypatch
):
    client = TestClient(_app(tmp_path, monkeypatch))
    monkeypatch.setattr(
        sources_api, "get_benchmark_delegated_source_auth_max_bytes", lambda: 12
    )
    payload = {
        "resolver": "checked_in_fixture",
        "reference": "input.json",
        "version": "1",
        "digest": "sha256:" + "0" * 64,
    }
    oversized = client.post(
        "/api/v1/benchmarks/sources/materialize",
        headers={"X-Benchmark-Delegated-Source-Authorization": "Bearer too-long-token"},
        json=payload,
    )
    assert oversized.status_code == 400
    assert oversized.json()["detail"]["error"] == "invalid_delegated_authorization"

    monkeypatch.setattr(
        sources_api, "get_benchmark_delegated_source_auth_max_bytes", lambda: 8192
    )
    unexpected = client.post(
        "/api/v1/benchmarks/sources/materialize",
        headers={"X-Benchmark-Delegated-Source-Authorization": "Bearer opaque-token"},
        json=payload,
    )
    assert unexpected.status_code == 400
    assert unexpected.json()["detail"]["error"] == "unexpected_delegated_authorization"


def test_source_authorization_failure_prevents_materialization(tmp_path, monkeypatch):
    application = _app(tmp_path, monkeypatch)

    def deny_source_read():
        raise HTTPException(status_code=403, detail="Benchmark capability required")

    application.dependency_overrides[require_benchmark_source_read] = deny_source_read
    response = TestClient(application).post(
        "/api/v1/benchmarks/sources/materialize",
        json={
            "resolver": "checked_in_fixture",
            "reference": "input.json",
            "version": "1",
            "digest": "sha256:" + "0" * 64,
        },
    )
    assert response.status_code == 403


def test_unavailable_source_reports_only_sanitized_failure(monkeypatch):
    captured = {}

    def report_and_raise(_logger, *, status_code, detail, log_message, exc):
        captured.update(
            status_code=status_code,
            detail=detail,
            log_message=log_message,
            exc=exc,
        )
        raise HTTPException(status_code=status_code, detail=detail)

    monkeypatch.setattr(
        sources_api, "raise_sanitized_http_exception", report_and_raise
    )
    try:
        raise OSError("/private/path/secret-paper.json")
    except OSError as raw:
        source_error = BenchmarkSourceError(
            "source_unavailable", "Benchmark input source is unavailable"
        )
        source_error.__cause__ = raw

    with pytest.raises(HTTPException) as exc_info:
        sources_api._raise_source_error(source_error)

    assert exc_info.value.status_code == 503
    assert captured["detail"]["error"] == "source_unavailable"
    assert captured["exc"].__context__ is None
    assert captured["exc"].__cause__ is None
    assert "secret-paper" not in str(captured["exc"])


def test_snapshot_failure_preserves_internal_signal_and_sanitizes_response(
    tmp_path, monkeypatch
):
    payload = b'{"messages": []}\n'
    (tmp_path / "input.json").write_bytes(payload)
    digest = f"sha256:{hashlib.sha256(payload).hexdigest()}"
    captured = {}

    class FailingSnapshotRepository:
        def __init__(self, _db, _store):
            pass

        def freeze_input(self, *_args, **_kwargs):
            raise sources_api.BenchmarkSnapshotError(
                "Snapshot object store versioning must be enabled"
            )

    def report_and_raise(_logger, *, status_code, detail, log_message, exc):
        captured.update(
            status_code=status_code,
            detail=detail,
            log_message=log_message,
            exc=exc,
        )
        raise HTTPException(status_code=status_code, detail=detail)

    application = _app(tmp_path, monkeypatch)
    monkeypatch.setattr(
        sources_api, "BenchmarkSnapshotRepository", FailingSnapshotRepository
    )
    monkeypatch.setattr(
        sources_api, "raise_sanitized_http_exception", report_and_raise
    )

    response = TestClient(application).post(
        "/api/v1/benchmarks/sources/materialize",
        json={
            "resolver": "checked_in_fixture",
            "reference": "input.json",
            "version": "1",
            "digest": digest,
        },
    )

    assert response.status_code == 503
    assert response.json()["detail"] == {
        "error": "source_unavailable",
        "message": "Benchmark input snapshot could not be committed",
    }
    assert captured["status_code"] == 503
    assert (
        captured["log_message"]
        == "Benchmark input snapshot could not be committed"
    )
    assert str(captured["exc"]) == "Snapshot object store versioning must be enabled"


def test_missing_source_returns_404_without_server_failure_reporting(monkeypatch):
    monkeypatch.setattr(
        sources_api,
        "raise_sanitized_http_exception",
        lambda *_args, **_kwargs: pytest.fail("4xx source errors must not be reported"),
    )

    with pytest.raises(HTTPException) as exc_info:
        sources_api._raise_source_error(
            BenchmarkSourceError("missing_source", "Local document was not found")
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail["error"] == "missing_source"


def test_unexpected_private_resolver_failure_returns_sanitized_source_error(
    tmp_path, monkeypatch
):
    class PrivateReference(RootModel[str]):
        root: Annotated[str, Field(min_length=1)]

    class FailingPrivateResolver:
        resolver_id = "private_source"
        reference_schema = PrivateReference
        delegated_authorization = DelegatedAuthorizationCapability.OPTIONAL

        async def materialize(self, *args, **kwargs):
            raise RuntimeError("token=private-resolver-secret")

    captured = {}

    def report_and_raise(_logger, *, status_code, detail, log_message, exc):
        captured.update(exc=exc, detail=detail)
        raise HTTPException(status_code=status_code, detail=detail)

    monkeypatch.setattr(
        sources_api, "raise_sanitized_http_exception", report_and_raise
    )
    application = _app(tmp_path, monkeypatch)
    application.state.benchmark_input_resolvers = BenchmarkInputResolverCatalog(
        [FailingPrivateResolver()], timeout_seconds=1, max_input_bytes=1024
    )

    response = TestClient(application).post(
        "/api/v1/benchmarks/sources/materialize",
        json={
            "resolver": "private_source",
            "reference": "approved-id",
            "version": "v1",
            "digest": "sha256:" + "0" * 64,
        },
    )

    assert response.status_code == 503
    assert response.json()["detail"] == {
        "error": "source_unavailable",
        "message": "Benchmark input source is unavailable",
    }
    assert captured["exc"].__context__ is None
    assert captured["exc"].__cause__ is None
    assert "private-resolver-secret" not in str(captured["exc"])
