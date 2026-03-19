"""Unit tests for admin connections health endpoints."""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import src.api.admin.connections as admin_connections


def test_check_all_connections_requires_initialized(monkeypatch):
    monkeypatch.setattr("src.lib.config.connections_loader.is_initialized", lambda: False)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(admin_connections.check_all_connections())

    assert exc.value.status_code == 503


def test_check_all_connections_stays_healthy_when_optional_service_is_not_configured(monkeypatch):
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

    monkeypatch.setattr("src.lib.config.connections_loader.check_all_health", _check_all_health)

    result = asyncio.run(admin_connections.check_all_connections())
    assert result.status == "healthy"
    assert result.total_services == 2
    assert result.healthy_count == 1
    assert result.unhealthy_count == 0
    assert result.unknown_count == 1
    assert result.required_healthy is True


def test_check_all_connections_reports_degraded_when_optional_service_is_configured_but_fails(monkeypatch):
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

    monkeypatch.setattr("src.lib.config.connections_loader.check_all_health", _check_all_health)

    result = asyncio.run(admin_connections.check_all_connections())
    assert result.status == "degraded"
    assert result.total_services == 2
    assert result.healthy_count == 1
    assert result.unhealthy_count == 1
    assert result.required_healthy is True


def test_check_all_connections_reports_unhealthy_when_required_service_fails(monkeypatch):
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

    monkeypatch.setattr("src.lib.config.connections_loader.check_all_health", _check_all_health)

    result = asyncio.run(admin_connections.check_all_connections())
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
