"""Unit tests for API health and readiness endpoints."""

import logging
from typing import Any, cast

import pytest
from fastapi import HTTPException

from src.api import health
from src.lib.document_sources.models import DocumentSourceHealth


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
        "check_configured_document_source_health",
        lambda: _async_health(
            DocumentSourceHealth(
                provider="local_pdf",
                ok=True,
                message="Using local PDF upload flow",
                metadata={"enabled": False},
            )
        ),
    )
    monkeypatch.setattr(
        health,
        "get_connection",
        lambda: _DummyConnection(
            {
                "status": "healthy",
                "version": "1.0",
                "nodes": 2,
                "collections": 3,
            }
        ),
    )

    result = await health.health_check_endpoint()
    assert result["status"] == "healthy"
    assert result["checks"]["weaviate"] == "healthy"
    assert result["checks"]["document_source"] == "healthy"
    assert result["details"]["document_source"] == {
        "provider": "local_pdf",
        "enabled": False,
        "message": "Using local PDF upload flow",
    }
    assert result["details"]["weaviate"]["version"] == "1.0"
    assert result["cognito_configured"] is True


@pytest.mark.asyncio
async def test_health_check_endpoint_unhealthy_weaviate_raises_503(monkeypatch):
    monkeypatch.setattr(health, "is_cognito_configured", lambda: False)
    _patch_document_source_ready(monkeypatch)
    monkeypatch.setattr(
        health,
        "get_connection",
        lambda: _DummyConnection({"status": "unhealthy", "message": "down"}),
    )

    with pytest.raises(HTTPException) as exc:
        await health.health_check_endpoint()

    assert exc.value.status_code == 503
    detail = _detail_dict(exc.value)
    assert detail["status"] == "unhealthy"
    assert detail["checks"]["weaviate"] == "unhealthy"
    assert detail["cognito_configured"] is False


@pytest.mark.asyncio
async def test_health_check_endpoint_exception_raises_503(monkeypatch, caplog):
    caplog.set_level(logging.ERROR, logger=health.logger.name)
    monkeypatch.setattr(health, "is_cognito_configured", lambda: False)
    _patch_document_source_ready(monkeypatch)
    monkeypatch.setattr(
        health,
        "get_connection",
        lambda: _DummyConnection(error=RuntimeError("connection failed")),
    )

    with pytest.raises(HTTPException) as exc:
        await health.health_check_endpoint()

    assert exc.value.status_code == 503
    detail = _detail_dict(exc.value)
    assert detail["checks"]["weaviate"] == "unhealthy"
    assert detail["details"]["weaviate"]["error"] == "Weaviate health check failed"
    assert detail["details"]["weaviate"]["message"] == "Weaviate connection not ready"
    assert "connection failed" not in str(detail).lower()
    assert "connection failed" in caplog.text.lower()


@pytest.mark.asyncio
async def test_health_check_endpoint_document_source_failure_raises_503(monkeypatch):
    monkeypatch.setattr(health, "is_cognito_configured", lambda: True)
    monkeypatch.setattr(
        health,
        "get_connection",
        lambda: _DummyConnection({"status": "healthy"}),
    )
    monkeypatch.setattr(
        health,
        "check_configured_document_source_health",
        lambda: _async_health(
            DocumentSourceHealth(
                provider="abc_literature",
                ok=False,
                message="ABC Literature unavailable",
                metadata={"enabled": True},
            )
        ),
    )

    with pytest.raises(HTTPException) as exc:
        await health.health_check_endpoint()

    assert exc.value.status_code == 503
    detail = _detail_dict(exc.value)
    assert detail["checks"]["weaviate"] == "healthy"
    assert detail["checks"]["document_source"] == "unhealthy"
    assert detail["details"]["document_source"] == {
        "provider": "abc_literature",
        "enabled": True,
        "message": "ABC Literature unavailable",
    }


@pytest.mark.asyncio
async def test_readiness_check_endpoint_ready(monkeypatch):
    monkeypatch.setattr(
        health,
        "get_connection",
        lambda: _DummyConnection({"status": "healthy"}),
    )
    monkeypatch.setattr(
        health,
        "check_configured_document_source_health",
        lambda: _async_health(
            DocumentSourceHealth(
                provider="local_pdf",
                ok=True,
                message="Using local PDF upload flow",
                metadata={"enabled": False},
            )
        ),
    )

    result = await health.readiness_check_endpoint()
    assert result["ready"] is True
    assert "timestamp" in result


@pytest.mark.asyncio
async def test_readiness_check_endpoint_document_source_not_ready(monkeypatch):
    monkeypatch.setattr(
        health,
        "get_connection",
        lambda: _DummyConnection({"status": "healthy"}),
    )
    monkeypatch.setattr(
        health,
        "check_configured_document_source_health",
        lambda: _async_health(
            DocumentSourceHealth(
                provider="abc_literature",
                ok=False,
                message="ABC Literature unavailable",
                metadata={"enabled": True},
            )
        ),
    )

    with pytest.raises(HTTPException) as exc:
        await health.readiness_check_endpoint()

    assert exc.value.status_code == 503
    assert exc.value.detail == {
        "ready": False,
        "reason": "Document-source provider not ready",
        "document_source": {
            "provider": "abc_literature",
            "enabled": True,
        },
    }


@pytest.mark.asyncio
async def test_readiness_check_endpoint_not_ready_raises_503(monkeypatch):
    _patch_document_source_ready(monkeypatch)
    monkeypatch.setattr(
        health,
        "get_connection",
        lambda: _DummyConnection({"status": "degraded"}),
    )

    with pytest.raises(HTTPException) as exc:
        await health.readiness_check_endpoint()

    assert exc.value.status_code == 503
    detail = _detail_dict(exc.value)
    assert detail["ready"] is False
    assert "not ready" in detail["reason"].lower()


@pytest.mark.asyncio
async def test_readiness_check_endpoint_handles_exceptions(monkeypatch, caplog):
    caplog.set_level(logging.ERROR, logger=health.logger.name)
    _patch_document_source_ready(monkeypatch)
    monkeypatch.setattr(
        health,
        "get_connection",
        lambda: _DummyConnection(error=RuntimeError("timeout")),
    )

    with pytest.raises(HTTPException) as exc:
        await health.readiness_check_endpoint()

    assert exc.value.status_code == 503
    detail = _detail_dict(exc.value)
    assert detail["ready"] is False
    assert detail["reason"] == "Weaviate connection not ready"
    assert "timeout" not in str(exc.value.detail).lower()
    assert "timeout" in caplog.text.lower()


@pytest.mark.asyncio
async def test_readiness_check_endpoint_handles_document_source_exception(
    monkeypatch,
    caplog,
):
    caplog.set_level(logging.ERROR, logger=health.logger.name)
    monkeypatch.setattr(
        health,
        "get_connection",
        lambda: _DummyConnection({"status": "healthy"}),
    )

    async def fail_document_source_health():
        raise RuntimeError("provider secret-ish failure")

    monkeypatch.setattr(
        health,
        "check_configured_document_source_health",
        fail_document_source_health,
    )

    with pytest.raises(HTTPException) as exc:
        await health.readiness_check_endpoint()

    assert exc.value.status_code == 503
    assert exc.value.detail == {
        "ready": False,
        "reason": "Document-source provider not ready",
        "document_source": {
            "provider": "unknown",
            "enabled": False,
        },
    }
    assert "provider secret-ish failure" not in str(exc.value.detail).lower()
    assert "provider secret-ish failure" in caplog.text.lower()


def _patch_document_source_ready(monkeypatch) -> None:
    monkeypatch.setattr(
        health,
        "check_configured_document_source_health",
        lambda: _async_health(
            DocumentSourceHealth(
                provider="local_pdf",
                ok=True,
                message="Using local PDF upload flow",
                metadata={"enabled": False},
            )
        ),
    )


def _detail_dict(exc: HTTPException) -> dict[str, Any]:
    return cast(dict[str, Any], exc.detail)


async def _async_health(payload: DocumentSourceHealth) -> DocumentSourceHealth:
    return payload
