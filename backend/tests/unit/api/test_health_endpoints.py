"""Unit tests for API health and readiness endpoints."""

import logging

import pytest
from fastapi import HTTPException

from src.api import health


class _DummyConnection:
    def __init__(self, payload=None, error: Exception | None = None):
        self.payload = payload if payload is not None else {"status": "healthy"}
        self.error = error

    async def health_check(self):
        if self.error:
            raise self.error
        return self.payload


@pytest.mark.asyncio
async def test_health_check_endpoint_healthy(monkeypatch):
    monkeypatch.setattr(health, "is_cognito_configured", lambda: True)
    monkeypatch.setattr(
        health,
        "get_connection",
        lambda: _DummyConnection({"status": "healthy", "version": "1.0", "nodes": 2, "collections": 3}),
    )

    result = await health.health_check_endpoint()
    assert result["status"] == "healthy"
    assert result["checks"]["weaviate"] == "healthy"
    assert result["details"]["weaviate"]["version"] == "1.0"
    assert result["cognito_configured"] is True


@pytest.mark.asyncio
async def test_health_check_endpoint_unhealthy_weaviate_raises_503(monkeypatch):
    monkeypatch.setattr(health, "is_cognito_configured", lambda: False)
    monkeypatch.setattr(
        health,
        "get_connection",
        lambda: _DummyConnection({"status": "unhealthy", "message": "down"}),
    )

    with pytest.raises(HTTPException) as exc:
        await health.health_check_endpoint()

    assert exc.value.status_code == 503
    detail = exc.value.detail
    assert detail["status"] == "unhealthy"
    assert detail["checks"]["weaviate"] == "unhealthy"
    assert detail["cognito_configured"] is False


@pytest.mark.asyncio
async def test_health_check_endpoint_exception_raises_503(monkeypatch, caplog):
    caplog.set_level(logging.ERROR, logger=health.logger.name)
    monkeypatch.setattr(health, "is_cognito_configured", lambda: False)
    monkeypatch.setattr(
        health,
        "get_connection",
        lambda: _DummyConnection(error=RuntimeError("connection failed")),
    )

    with pytest.raises(HTTPException) as exc:
        await health.health_check_endpoint()

    assert exc.value.status_code == 503
    detail = exc.value.detail
    assert detail["checks"]["weaviate"] == "unhealthy"
    assert detail["details"]["weaviate"]["error"] == "Weaviate health check failed"
    assert detail["details"]["weaviate"]["message"] == "Weaviate connection not ready"
    assert "connection failed" not in str(detail).lower()
    assert "connection failed" in caplog.text.lower()


@pytest.mark.asyncio
async def test_readiness_check_endpoint_ready(monkeypatch):
    monkeypatch.setattr(
        health,
        "get_connection",
        lambda: _DummyConnection({"status": "healthy"}),
    )

    result = await health.readiness_check_endpoint()
    assert result["ready"] is True
    assert "timestamp" in result


@pytest.mark.asyncio
async def test_readiness_check_endpoint_not_ready_raises_503(monkeypatch):
    monkeypatch.setattr(
        health,
        "get_connection",
        lambda: _DummyConnection({"status": "degraded"}),
    )

    with pytest.raises(HTTPException) as exc:
        await health.readiness_check_endpoint()

    assert exc.value.status_code == 503
    assert exc.value.detail["ready"] is False
    assert "not ready" in exc.value.detail["reason"].lower()


@pytest.mark.asyncio
async def test_readiness_check_endpoint_handles_exceptions(monkeypatch, caplog):
    caplog.set_level(logging.ERROR, logger=health.logger.name)
    monkeypatch.setattr(
        health,
        "get_connection",
        lambda: _DummyConnection(error=RuntimeError("timeout")),
    )

    with pytest.raises(HTTPException) as exc:
        await health.readiness_check_endpoint()

    assert exc.value.status_code == 503
    assert exc.value.detail["ready"] is False
    assert exc.value.detail["reason"] == "Weaviate connection not ready"
    assert "timeout" not in str(exc.value.detail).lower()
    assert "timeout" in caplog.text.lower()
