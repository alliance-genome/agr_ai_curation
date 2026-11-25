"""Unit tests for public and admin connections health endpoints."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import src.api.admin.auth as admin_auth
import src.api.admin.connections as admin_connections
import src.api.auth as auth_api
from src.lib.config.connections_loader import (
    ConnectionDefinition,
    HealthCheck,
)


def _configure_api_key_identity(monkeypatch, *, email: str, api_key: str = "test-secret-key"):
    monkeypatch.setenv("DEV_MODE", "false")
    monkeypatch.setenv("TESTING_API_KEY", api_key)
    monkeypatch.setenv("TESTING_API_KEY_EMAIL", email)
    monkeypatch.setenv("TESTING_API_KEY_USER", "api-tester")
    return {"X-API-Key": api_key}


def test_check_all_connections_requires_initialized(monkeypatch, request_stub):
    request = request_stub
    monkeypatch.setattr("src.lib.config.connections_loader.is_initialized", lambda: False)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(admin_connections.check_all_connections(request))

    assert exc.value.status_code == 503


def test_check_public_connections_requires_initialized(monkeypatch, request_stub):
    monkeypatch.setattr("src.lib.config.connections_loader.is_initialized", lambda: False)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(admin_connections.check_public_connections_status(request_stub))

    assert exc.value.status_code == 503


def test_check_public_connections_returns_503_when_snapshot_unavailable(monkeypatch, request_stub):
    from src.lib.config.connections_monitor import SnapshotUnavailable

    monkeypatch.setattr("src.lib.config.connections_loader.is_initialized", lambda: True)
    request_stub.app.state.connections_health_monitor.get = AsyncMock(
        side_effect=SnapshotUnavailable("Connection monitoring sweep did not complete")
    )

    with pytest.raises(HTTPException) as exc:
        asyncio.run(admin_connections.check_public_connections_status(request_stub))

    assert exc.value.status_code == 503


def test_check_all_connections_stays_healthy_when_optional_service_is_not_configured(monkeypatch, request_stub):
    request = request_stub
    monkeypatch.setattr("src.lib.config.connections_loader.is_initialized", lambda: True)

    async def _check_all_health():
        return {
            "postgres": {
                "service_id": "postgres",
                "description": "Postgres",
                "url": "postgres://***@db:5432/app",
                "required": True,
                "is_healthy": True,
                "last_error": None,
            },
            "curation_db": {
                "service_id": "curation_db",
                "description": "Curation DB",
                "url": "",
                "required": False,
                "is_healthy": None,
                "last_error": None,
            },
        }

    async def snapshot():
        return SimpleNamespace(services=await _check_all_health(), observed_at="2026-10-17T00:00:00+00:00")

    request.app.state.connections_health_monitor.get = snapshot

    result = asyncio.run(admin_connections.check_all_connections(request))
    assert result.status == "healthy"
    assert result.total_services == 2
    assert result.healthy_count == 1
    assert result.unhealthy_count == 0
    assert result.unknown_count == 1
    assert result.required_healthy is True


def test_check_all_connections_reports_degraded_when_optional_service_is_configured_but_fails(monkeypatch, request_stub):
    request = request_stub
    monkeypatch.setattr("src.lib.config.connections_loader.is_initialized", lambda: True)

    async def _check_all_health():
        return {
            "postgres": {
                "service_id": "postgres",
                "description": "Postgres",
                "url": "postgres://***@db:5432/app",
                "required": True,
                "is_healthy": True,
                "last_error": None,
            },
            "langfuse": {
                "service_id": "langfuse",
                "description": "Langfuse",
                "url": "https://langfuse.local",
                "required": False,
                "is_healthy": False,
                "last_error": "timeout",
            },
        }

    async def snapshot():
        return SimpleNamespace(services=await _check_all_health(), observed_at="2026-10-17T00:00:00+00:00")

    request.app.state.connections_health_monitor.get = snapshot

    result = asyncio.run(admin_connections.check_all_connections(request))
    assert result.status == "degraded"
    assert result.total_services == 2
    assert result.healthy_count == 1
    assert result.unhealthy_count == 1
    assert result.required_healthy is True


def test_check_all_connections_reports_unhealthy_when_required_service_fails(monkeypatch, request_stub):
    request = request_stub
    monkeypatch.setattr("src.lib.config.connections_loader.is_initialized", lambda: True)

    async def _check_all_health():
        return {
            "postgres": {
                "service_id": "postgres",
                "description": "Postgres",
                "url": "postgres://***@db:5432/app",
                "required": True,
                "is_healthy": False,
                "last_error": "refused",
            },
            "redis": {
                "service_id": "redis",
                "description": "Redis",
                "url": "redis://***@cache:6379",
                "required": True,
                "is_healthy": True,
                "last_error": None,
            },
        }

    async def snapshot():
        return SimpleNamespace(services=await _check_all_health(), observed_at="2026-10-17T00:00:00+00:00")

    request.app.state.connections_health_monitor.get = snapshot

    result = asyncio.run(admin_connections.check_all_connections(request))
    assert result.status == "unhealthy"
    assert result.required_healthy is False


def test_check_single_connection_requires_initialized(monkeypatch, request_stub):
    monkeypatch.setattr("src.lib.config.connections_loader.is_initialized", lambda: False)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(admin_connections.check_single_connection("postgres", request_stub))

    assert exc.value.status_code == 503


def _cached_postgres_snapshot():
    return SimpleNamespace(
        observed_at="2026-11-20T00:00:00+00:00",
        services={
            "postgres": {
                "service_id": "postgres",
                "description": "Postgres DB",
                "url": "postgres://***@db:5432/app",
                "required": True,
                "is_healthy": False,
                "last_error": "connection refused",
            },
        },
    )


def _fail_on_live_probe(*_args, **_kwargs):
    raise AssertionError("single-service route started a live probe")


def test_check_single_connection_returns_cached_entry_without_probing(monkeypatch, request_stub):
    monkeypatch.setattr("src.lib.config.connections_loader.is_initialized", lambda: True)
    monkeypatch.setattr("src.lib.config.connections_loader.check_service_health", _fail_on_live_probe)

    async def snapshot():
        return _cached_postgres_snapshot()

    request_stub.app.state.connections_health_monitor.get = snapshot

    result = asyncio.run(admin_connections.check_single_connection("postgres", request_stub))

    assert result.service_id == "postgres"
    assert result.url == "postgres://***@db:5432/app"
    assert result.is_healthy is False
    assert result.last_error == "connection refused"


def test_check_single_connection_returns_404_for_service_missing_from_cache(monkeypatch, request_stub):
    monkeypatch.setattr("src.lib.config.connections_loader.is_initialized", lambda: True)

    async def snapshot():
        return _cached_postgres_snapshot()

    request_stub.app.state.connections_health_monitor.get = snapshot

    with pytest.raises(HTTPException) as exc:
        asyncio.run(admin_connections.check_single_connection("unknown", request_stub))

    assert exc.value.status_code == 404
    assert "Unknown service" in str(exc.value.detail)


def test_check_single_connection_returns_503_when_snapshot_unavailable(monkeypatch, request_stub):
    from src.lib.config.connections_monitor import SnapshotUnavailable

    monkeypatch.setattr("src.lib.config.connections_loader.is_initialized", lambda: True)
    request_stub.app.state.connections_health_monitor.get = AsyncMock(
        side_effect=SnapshotUnavailable("Connection monitoring sweep did not complete")
    )

    with pytest.raises(HTTPException) as exc:
        asyncio.run(admin_connections.check_single_connection("postgres", request_stub))

    assert exc.value.status_code == 503


_CONFIGURATION_HEALTH_ROUTES = [
    ("/api/admin/health/llm-providers", "src.lib.config.provider_validation.build_provider_runtime_report"),
    ("/api/admin/health/packages", "src.lib.packages.health.build_package_health_report"),
]

_ADMIN_CONNECTION_ROUTES = [
    "/api/admin/health/connections",
    "/api/admin/health/connections/postgres",
]


def _anonymous_get(monkeypatch, path, builder):
    monkeypatch.setenv("DEV_MODE", "false")
    monkeypatch.delenv("TESTING_API_KEY", raising=False)

    def _builder_must_not_run(*_args, **_kwargs):
        raise AssertionError("anonymous request reached the report builder")

    monkeypatch.setattr(builder, _builder_must_not_run)
    app = FastAPI()
    app.include_router(admin_connections.router)
    return TestClient(app).get(path)


@pytest.mark.parametrize("path,builder", _CONFIGURATION_HEALTH_ROUTES)
def test_configuration_health_routes_require_authentication(monkeypatch, path, builder):
    monkeypatch.delenv("AUTH_PROVIDER", raising=False)

    response = _anonymous_get(monkeypatch, path, builder)

    assert response.status_code == 401


@pytest.mark.parametrize("path,builder", _CONFIGURATION_HEALTH_ROUTES)
def test_configuration_health_routes_refuse_missing_cookie_when_auth_is_configured(
    monkeypatch, path, builder
):
    monkeypatch.setattr(auth_api, "is_auth_configured", lambda: True)

    def _provider_must_not_be_used():
        raise AssertionError("request without a cookie reached token validation")

    monkeypatch.setattr(auth_api, "_get_provider_or_503", _provider_must_not_be_used)

    response = _anonymous_get(monkeypatch, path, builder)

    assert response.status_code == 401
    assert response.json() == {"detail": "Not authenticated"}


@pytest.mark.parametrize("path", _ADMIN_CONNECTION_ROUTES)
def test_admin_connection_routes_reject_unauthenticated_before_monitor(monkeypatch, path):
    monkeypatch.setenv("DEV_MODE", "false")
    monkeypatch.delenv("TESTING_API_KEY", raising=False)
    monkeypatch.delenv("AUTH_PROVIDER", raising=False)
    monkeypatch.setattr(admin_auth, "get_admin_emails", lambda: {"admin@example.org"})

    def _must_not_run(*_args, **_kwargs):
        raise AssertionError("unauthorized request reached connections health handler")

    monkeypatch.setattr("src.lib.config.connections_loader.is_initialized", _must_not_run)

    app = FastAPI()
    app.include_router(admin_connections.router)
    app.state.connections_health_monitor = SimpleNamespace(get=AsyncMock(side_effect=_must_not_run))

    response = TestClient(app).get(path)

    assert response.status_code == 401
    assert response.json() == {"detail": "Not authenticated"}


@pytest.mark.parametrize("path", _ADMIN_CONNECTION_ROUTES)
def test_admin_connection_routes_reject_missing_cookie_when_auth_configured(monkeypatch, path):
    monkeypatch.setenv("DEV_MODE", "false")
    monkeypatch.delenv("TESTING_API_KEY", raising=False)
    monkeypatch.setattr(auth_api, "is_auth_configured", lambda: True)
    monkeypatch.setattr(admin_auth, "get_admin_emails", lambda: {"admin@example.org"})

    def _provider_must_not_be_used():
        raise AssertionError("request without a cookie reached token validation")

    monkeypatch.setattr(auth_api, "_get_provider_or_503", _provider_must_not_be_used)

    app = FastAPI()
    app.include_router(admin_connections.router)

    response = TestClient(app).get(path)

    assert response.status_code == 401
    assert response.json() == {"detail": "Not authenticated"}


@pytest.mark.parametrize("path", _ADMIN_CONNECTION_ROUTES)
@pytest.mark.parametrize("admin_emails", [{"admin@example.org"}, set()])
def test_admin_connection_routes_reject_non_admin_curators_with_403_before_monitor(
    monkeypatch, path, admin_emails
):
    headers = _configure_api_key_identity(monkeypatch, email="curator@example.org")
    monkeypatch.setattr(admin_auth, "get_admin_emails", lambda: admin_emails)

    def _must_not_run(*_args, **_kwargs):
        raise AssertionError("non-admin request reached connections health handler")

    monkeypatch.setattr("src.lib.config.connections_loader.is_initialized", _must_not_run)

    app = FastAPI()
    app.include_router(admin_connections.router)
    app.state.connections_health_monitor = SimpleNamespace(get=AsyncMock(side_effect=_must_not_run))

    response = TestClient(app).get(path, headers=headers)

    assert response.status_code == 403


@pytest.mark.parametrize("path", _ADMIN_CONNECTION_ROUTES)
def test_admin_connection_routes_allow_admin_users(monkeypatch, path):
    headers = _configure_api_key_identity(monkeypatch, email="Admin@Example.org")
    monkeypatch.setattr(admin_auth, "get_admin_emails", lambda: {"admin@example.org"})
    monkeypatch.setattr("src.lib.config.connections_loader.is_initialized", lambda: True)

    async def snapshot():
        return _cached_postgres_snapshot()

    app = FastAPI()
    app.include_router(admin_connections.router)
    app.state.connections_health_monitor = SimpleNamespace(get=snapshot)

    response = TestClient(app).get(path, headers=headers)

    assert response.status_code == 200
    payload = response.json()
    if path.endswith("/postgres"):
        assert payload["service_id"] == "postgres"
        assert payload["url"] == "postgres://***@db:5432/app"
        assert payload["last_error"] == "connection refused"
    else:
        assert payload["status"] == "unhealthy"
        assert payload["services"]["postgres"]["url"] == "postgres://***@db:5432/app"


def test_public_connections_route_allows_anonymous_and_redacts_internal_details(monkeypatch):
    monkeypatch.setenv("DEV_MODE", "false")
    monkeypatch.delenv("TESTING_API_KEY", raising=False)
    monkeypatch.setattr(admin_auth, "get_admin_emails", lambda: {"admin@example.org"})
    monkeypatch.setattr("src.lib.config.connections_loader.is_initialized", lambda: True)

    async def snapshot():
        return SimpleNamespace(
            observed_at="2026-11-25T00:00:00+00:00",
            services={
                "internal_postgres_primary": {
                    "service_id": "internal_postgres_primary",
                    "description": "Application database",
                    "url": "postgres://***@db-internal.corp.local:5432/prod_db",
                    "required": True,
                    "is_healthy": False,
                    "last_error": "FATAL: connection refused to db-internal.corp.local:5432",
                },
                "internal_langfuse": {
                    "service_id": "internal_langfuse",
                    "description": "LLM observability and tracing",
                    "url": "http://langfuse-internal:3000",
                    "required": False,
                    "is_healthy": False,
                    "last_error": "502 Bad Gateway from langfuse-internal:3000",
                },
                "internal_weaviate": {
                    "service_id": "internal_weaviate",
                    "description": "Vector database",
                    "url": "http://weaviate-internal:8080",
                    "required": True,
                    "is_healthy": True,
                    "last_error": None,
                },
                "internal_curation_db": {
                    "service_id": "internal_curation_db",
                    "description": "Optional curation database",
                    "url": "",
                    "required": False,
                    "is_healthy": None,
                    "last_error": None,
                },
            },
        )

    app = FastAPI()
    app.include_router(admin_connections.public_router)
    app.state.connections_health_monitor = SimpleNamespace(get=snapshot)

    response = TestClient(app).get("/api/health/connections")

    assert response.status_code == 200
    data = response.json()
    assert set(data.keys()) == {"observed_at", "status", "required_healthy", "unhealthy_services"}
    assert data == {
        "observed_at": "2026-11-25T00:00:00+00:00",
        "status": "unhealthy",
        "required_healthy": False,
        "unhealthy_services": [
            {"description": "Application database", "required": True},
            {"description": "LLM observability and tracing", "required": False},
        ],
    }

    for item in data["unhealthy_services"]:
        assert set(item.keys()) == {"description", "required"}

    raw_text = response.text
    for forbidden in (
        "url",
        "last_error",
        "service_id",
        "internal_postgres_primary",
        "internal_langfuse",
        "internal_weaviate",
        "internal_curation_db",
        "db-internal.corp.local",
        "langfuse-internal",
        "weaviate-internal",
        "5432",
        "3000",
        "8080",
        "postgres://",
        "http://",
        "connection refused",
        "Bad Gateway",
        "Vector database",
        "Optional curation database",
    ):
        assert forbidden not in raw_text


@pytest.fixture
def request_stub():
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        connections_health_monitor=SimpleNamespace()
    )))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "required_health,optional_health,expected",
    [(True, None, "healthy"), (True, False, "degraded"), (False, True, "unhealthy"), (None, True, "unhealthy")],
)
async def test_public_and_admin_http_snapshots_share_cache_status_and_timestamp(
    monkeypatch, required_health, optional_health, expected
):
    from src.lib.config import connections_loader as loader
    from src.lib.config.connections_monitor import ConnectionsHealthMonitor

    loader.reset_cache()
    monkeypatch.setattr(loader, "_initialized", True)
    admin_headers = _configure_api_key_identity(monkeypatch, email="admin@example.org")
    monkeypatch.setattr(admin_auth, "get_admin_emails", lambda: {"admin@example.org"})

    scheme, user, password, host = "postgresql", "user", "secret", "internal-db.local"
    secret_url = f"{scheme}://{user}:{password}@{host}:5432/app"
    connections = {
        "required_svc": ConnectionDefinition(
            service_id="required_svc",
            description="Primary database",
            required=True,
            url=secret_url,
            health_check=HealthCheck(endpoint="/health"),
        ),
        "optional_svc": ConnectionDefinition(
            service_id="optional_svc",
            description="Optional telemetry",
            required=False,
            url="http://optional-host.local:9000",
            health_check=HealthCheck(endpoint="/health"),
        ),
    }
    monkeypatch.setattr(loader, "_connection_registry", connections)

    async def probe(conn):
        state = required_health if conn.required else optional_health
        return state, secret_url if state is False else None

    probe_mock = AsyncMock(side_effect=probe)
    monkeypatch.setattr(loader, "_check_http_health", probe_mock)

    app = FastAPI()
    app.include_router(admin_connections.public_router)
    app.include_router(admin_connections.router)
    owner = ConnectionsHealthMonitor()
    app.state.connections_health_monitor = owner
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            public_first = await client.get("/api/health/connections")
            public_second = await client.get("/api/health/connections")
            assert public_first.status_code == public_second.status_code == 200
            public_data = public_first.json()
            assert public_data == public_second.json()
            assert set(public_data.keys()) == {"observed_at", "status", "required_healthy", "unhealthy_services"}
            assert public_data["status"] == expected
            assert public_data["observed_at"]
            for forbidden in (
                "secret",
                "internal-db.local",
                "optional-host.local",
                "5432",
                "9000",
                "required_svc",
                "optional_svc",
                "url",
                "last_error",
            ):
                assert forbidden not in public_first.text
            assert probe_mock.await_count == 2

            # Admin routes share the exact same cached snapshot without extra probes
            admin_first = await client.get("/api/admin/health/connections", headers=admin_headers)
            assert admin_first.status_code == 200
            admin_data = admin_first.json()
            assert admin_data["observed_at"] == public_data["observed_at"]
            assert admin_data["status"] == expected
            assert admin_data["required_healthy"] is public_data["required_healthy"]
            assert admin_data["total_services"] == 2
            assert admin_data["healthy_count"] + admin_data["unhealthy_count"] + admin_data["unknown_count"] == 2
            assert "secret" not in admin_first.text
            assert probe_mock.await_count == 2

            direct = await client.get("/api/admin/health/connections/required_svc", headers=admin_headers)
            assert direct.status_code == 200
            assert direct.json() == admin_data["services"]["required_svc"]
            assert probe_mock.await_count == 2
            assert (await client.get("/api/admin/health/connections", headers=admin_headers)).json() == admin_data
    finally:
        await owner.close()
        loader.reset_cache()


@pytest.mark.asyncio
async def test_aborted_snapshot_returns_sanitized_503(monkeypatch, request_stub):
    from src.lib.config.connections_monitor import SnapshotUnavailable

    monkeypatch.setattr("src.lib.config.connections_loader.is_initialized", lambda: True)
    request_stub.app.state.connections_health_monitor.get = AsyncMock(
        side_effect=SnapshotUnavailable("Connection monitoring sweep did not complete")
    )
    with pytest.raises(HTTPException) as exc:
        await admin_connections.check_all_connections(request_stub)
    assert exc.value.status_code == 503


@pytest.mark.asyncio
@pytest.mark.parametrize("required", [True, False])
@pytest.mark.parametrize("failure", ["http_timeout", "child_timeout", "child_exit"])
async def test_probe_failure_remains_public_health_observation(monkeypatch, required, failure):
    from src.lib.config import connections_loader as loader
    from src.lib.config import connection_probe_process as process_module
    from src.lib.config.connections_monitor import ConnectionsHealthMonitor

    loader.reset_cache()
    monkeypatch.setattr(loader, "_initialized", True)
    admin_headers = _configure_api_key_identity(monkeypatch, email="admin@example.org")
    monkeypatch.setattr(admin_auth, "get_admin_emails", lambda: {"admin@example.org"})

    secret = "private-probe-password"
    secret_url = f"http://user:{secret}@probe-host.internal:7443"
    conn = ConnectionDefinition(
        service_id="failing_svc",
        description="Failing dependency",
        required=required,
        url=secret_url,
        timeout_seconds=1,
        health_check=HealthCheck(
            method="GET" if failure == "http_timeout" else "CONNECT",
            endpoint="/health",
        ),
    )
    healthy = ConnectionDefinition(
        service_id="healthy_svc",
        description="Healthy dependency",
        required=True,
        url="http://healthy-host.internal:8080",
    )
    monkeypatch.setattr(loader, "_connection_registry", {"failing_svc": conn, "healthy_svc": healthy})
    cleaned = asyncio.Event()

    async def http_probe(definition):
        if definition.service_id == "healthy_svc":
            return True, None
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()

    monkeypatch.setattr(loader, "_check_http_health", http_probe)
    process = AsyncMock()
    process.pid = 123456
    process.returncode = 1
    if failure == "child_timeout":
        async def stuck(*args):
            await asyncio.Event().wait()
        process.communicate.side_effect = stuck
    else:
        process.communicate.return_value = (secret.encode(), None)
    spawn = AsyncMock(return_value=process)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    kill = MagicMock()
    monkeypatch.setattr(process_module.os, "killpg", kill)
    report = MagicMock()
    monkeypatch.setattr("src.lib.observability.runtime.report_runtime_exception", report)

    app = FastAPI()
    app.include_router(admin_connections.public_router)
    app.include_router(admin_connections.router)
    owner = ConnectionsHealthMonitor()
    app.state.connections_health_monitor = owner
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            public_first = await client.get("/api/health/connections")
            public_cached = await client.get("/api/health/connections")
            admin_cached = await client.get("/api/admin/health/connections", headers=admin_headers)
        assert public_first.status_code == public_cached.status_code == admin_cached.status_code == 200
        assert public_first.json() == public_cached.json()
        public_data = public_first.json()
        assert public_data["status"] == ("unhealthy" if required else "degraded")
        assert public_data["required_healthy"] is (not required)
        assert public_data["unhealthy_services"] == [
            {"description": "Failing dependency", "required": required}
        ]
        for forbidden in (secret, "probe-host.internal", "7443", "failing_svc", "healthy_svc", "url", "last_error"):
            assert forbidden not in public_first.text

        admin_data = admin_cached.json()
        assert admin_data["status"] == public_data["status"]
        assert admin_data["healthy_count"] == admin_data["unhealthy_count"] == 1
        assert admin_data["unknown_count"] == 0
        assert admin_data["required_healthy"] is (not required)
        assert secret not in admin_cached.text
        assert not owner._pending
        if failure == "http_timeout":
            assert cleaned.is_set()
            spawn.assert_not_called()
        else:
            spawn.assert_awaited_once()
            kill.assert_called_once_with(process.pid, process_module.signal.SIGKILL)
            process.wait.assert_awaited_once()
        if failure == "child_exit":
            report.assert_called_once()
            error = report.call_args.args[0]
            assert str(error) == "Isolated connection probe failed"
            assert error.__context__ is None
            assert error.__cause__ is None
            assert secret not in repr(report.call_args)
        else:
            report.assert_not_called()
    finally:
        await owner.close()
        loader.reset_cache()
