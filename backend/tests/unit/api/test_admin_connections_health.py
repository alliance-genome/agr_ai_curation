"""Unit tests for admin connections health endpoints."""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import src.api.admin.connections as admin_connections
from src.lib.config.connections_loader import (
    ConnectionDefinition,
    CredentialsConfig,
    HealthCheck,
)


def test_check_all_connections_requires_initialized(monkeypatch, request_stub):
    request = request_stub
    monkeypatch.setattr("src.lib.config.connections_loader.is_initialized", lambda: False)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(admin_connections.check_all_connections(request))

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


def test_check_single_connection_requires_initialized(monkeypatch):
    monkeypatch.setattr("src.lib.config.connections_loader.is_initialized", lambda: False)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(admin_connections.check_single_connection("postgres"))

    assert exc.value.status_code == 503


def test_check_single_connection_returns_404_for_unknown_service(monkeypatch):
    monkeypatch.setattr("src.lib.config.connections_loader.is_initialized", lambda: True)
    monkeypatch.setattr("src.lib.config.connections_loader.get_connection", lambda _service_id: None)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(admin_connections.check_single_connection("unknown"))

    assert exc.value.status_code == 404
    assert "Unknown service" in str(exc.value.detail)


def test_check_single_connection_returns_sanitized_response(monkeypatch):
    monkeypatch.setattr("src.lib.config.connections_loader.is_initialized", lambda: True)

    conn = SimpleNamespace(
        service_id="postgres",
        description="Postgres DB",
        display_url="postgres://***@db:5432/app",
        credentials=None,
        required=True,
        is_healthy=False,
        last_error="postgres://user:<redacted>@db:5432/app is down",
    )
    monkeypatch.setattr("src.lib.config.connections_loader.get_connection", lambda _service_id: conn)

    calls = {"checked": []}

    async def _check_service_health(service_id: str):
        calls["checked"].append(service_id)
        return False

    monkeypatch.setattr("src.lib.config.connections_loader.check_service_health", _check_service_health)
    monkeypatch.setattr("src.lib.config.connections_loader.sanitize_error_message", lambda err: f"sanitized:{err}")

    result = asyncio.run(admin_connections.check_single_connection("postgres"))
    assert result.service_id == "postgres"
    assert result.url == "postgres://***@db:5432/app"
    assert result.last_error.startswith("sanitized:")
    assert calls["checked"] == ["postgres"]


def test_check_single_connection_uses_effective_redacted_url(monkeypatch):
    monkeypatch.setattr("src.lib.config.connections_loader.is_initialized", lambda: True)

    conn = ConnectionDefinition(
        service_id="curation_db",
        description="Curation DB",
        health_check=HealthCheck(method="CONNECT"),
        credentials=CredentialsConfig(source="env"),
        required=False,
        is_healthy=True,
        last_error=None,
    )
    monkeypatch.setattr(
        "src.lib.config.connections_loader.get_connection",
        lambda _service_id: conn,
    )

    async def _check_service_health(_service_id: str):
        conn.effective_display_url = (
            "testdb://***:***@db.example:5432/curation"
        )
        return True

    monkeypatch.setattr(
        "src.lib.config.connections_loader.check_service_health",
        _check_service_health,
    )
    result = asyncio.run(admin_connections.check_single_connection("curation_db"))

    assert result.url == "testdb://***:***@db.example:5432/curation"


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
async def test_public_http_snapshots_keep_status_counts_and_timestamp(
    monkeypatch, required_health, optional_health, expected
):
    import httpx
    from fastapi import FastAPI
    from unittest.mock import AsyncMock
    from src.lib.config import connections_loader as loader
    from src.lib.config.connections_monitor import ConnectionsHealthMonitor

    loader.reset_cache()
    monkeypatch.setattr(loader, "_initialized", True)
    scheme, user, password, host = "postgresql", "user", "secret", "host"
    secret_url = f"{scheme}://{user}:{password}@{host}"
    connections = {
        "required": ConnectionDefinition(
            service_id="required", required=True, url=secret_url,
            health_check=HealthCheck(endpoint="/health"),
        ),
        "optional": ConnectionDefinition(
            service_id="optional", required=False, url="http://optional",
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
    app.include_router(admin_connections.router)
    owner = ConnectionsHealthMonitor()
    app.state.connections_health_monitor = owner
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            first = await client.get("/api/admin/health/connections")
            second = await client.get("/api/admin/health/connections")
            assert first.status_code == second.status_code == 200
            data = first.json()
            assert data == second.json()
            assert data["status"] == expected
            assert data["total_services"] == 2
            assert data["healthy_count"] + data["unhealthy_count"] + data["unknown_count"] == 2
            assert "secret" not in first.text
            assert data["observed_at"]
            assert probe_mock.await_count == 2
            direct = await client.get("/api/admin/health/connections/required")
            assert direct.status_code == 200
            assert probe_mock.await_count == 3
            assert (await client.get("/api/admin/health/connections")).json() == data
    finally:
        await owner.close()
        loader.reset_cache()


@pytest.mark.asyncio
async def test_aborted_snapshot_returns_sanitized_503(monkeypatch, request_stub):
    from src.lib.config.connections_monitor import SnapshotUnavailable
    from unittest.mock import AsyncMock
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
    import httpx
    from fastapi import FastAPI
    from unittest.mock import AsyncMock, MagicMock
    from src.lib.config import connections_loader as loader
    from src.lib.config import connection_probe_process as process_module
    from src.lib.config.connections_monitor import ConnectionsHealthMonitor

    loader.reset_cache()
    monkeypatch.setattr(loader, "_initialized", True)
    secret = "private-probe-password"
    secret_url = f"http://user:{secret}@service"
    conn = ConnectionDefinition(
        service_id="failing", required=required, url=secret_url,
        timeout_seconds=1,
        health_check=HealthCheck(
            method="GET" if failure == "http_timeout" else "CONNECT",
            endpoint="/health",
        ),
    )
    healthy = ConnectionDefinition(service_id="healthy", required=True, url="http://healthy")
    monkeypatch.setattr(loader, "_connection_registry", {"failing": conn, "healthy": healthy})
    cleaned = asyncio.Event()

    async def http_probe(definition):
        if definition.service_id == "healthy":
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
    app.include_router(admin_connections.router)
    owner = ConnectionsHealthMonitor()
    app.state.connections_health_monitor = owner
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            first = await client.get("/api/admin/health/connections")
            cached = await client.get("/api/admin/health/connections")
        assert first.status_code == cached.status_code == 200
        assert first.json() == cached.json()
        data = first.json()
        assert data["status"] == ("unhealthy" if required else "degraded")
        assert data["healthy_count"] == data["unhealthy_count"] == 1
        assert data["unknown_count"] == 0
        assert data["required_healthy"] is (not required)
        assert secret not in first.text
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
