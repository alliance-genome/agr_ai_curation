"""Unit tests for PDF extraction proxy health aggregation."""

import pytest
from fastapi import HTTPException

from src.api import documents


class _DummyResponse:
    def __init__(self, status_code: int, payload=None, content: bytes = b"{}"):
        self.status_code = status_code
        self._payload = payload
        self.content = content

    def json(self):
        return self._payload


class _DummyBadJsonResponse(_DummyResponse):
    def json(self):
        raise ValueError("invalid json")


@pytest.mark.asyncio
async def test_pdf_extraction_health_forwards_auth_headers_to_deep_health(monkeypatch):
    service_url = "https://pdfx.example.org"
    monkeypatch.setenv("PDF_EXTRACTION_SERVICE_URL", service_url)
    monkeypatch.setenv("PDF_EXTRACTION_HEALTH_TIMEOUT", "5")

    async def _service_headers():
        return {"Authorization": "Bearer service-token"}

    monkeypatch.setattr(documents, "_build_pdf_extraction_service_headers", _service_headers)

    calls: list[tuple[str, dict | None]] = []

    class _DummyClient:
        def __init__(self, **_kwargs):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, headers=None):
            calls.append((url, headers))
            if url.endswith("/api/v1/health"):
                return _DummyResponse(200, {"status": "healthy", "ec2": "ready"})
            if url.endswith("/api/v1/health/deep"):
                return _DummyResponse(200, {"status": "healthy"})
            if url.endswith("/api/v1/status"):
                return _DummyResponse(200, {"state": "ready"})
            raise AssertionError(f"Unexpected URL called: {url}")

    monkeypatch.setattr(documents.httpx, "AsyncClient", _DummyClient)

    result = await documents.get_pdf_extraction_health({"sub": "dev-user-123"})

    assert result["status"] == "healthy"
    assert result["worker_available"] is True

    deep_headers = next(headers for url, headers in calls if url.endswith("/api/v1/health/deep"))
    assert deep_headers == {"Authorization": "Bearer service-token"}

    health_headers = next(headers for url, headers in calls if url.endswith("/api/v1/health"))
    assert health_headers == {"Authorization": "Bearer service-token"}

    status_headers = next(headers for url, headers in calls if url.endswith("/api/v1/status"))
    assert status_headers == {"Authorization": "Bearer service-token"}


@pytest.mark.asyncio
async def test_pdf_extraction_health_worker_state_precedence(monkeypatch):
    monkeypatch.setenv("PDF_EXTRACTION_SERVICE_URL", "https://pdfx.example.org")

    async def _service_headers():
        return {"Authorization": "Bearer service-token"}

    class _DummyClient:
        def __init__(self, **_kwargs):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, headers=None):
            del headers
            if url.endswith("/api/v1/health"):
                return _DummyResponse(200, {"status": "healthy", "ec2": "ready"})
            if url.endswith("/api/v1/health/deep"):
                return _DummyResponse(200, {"status": "healthy"})
            if url.endswith("/api/v1/status"):
                return _DummyResponse(200, {"state": "sleeping"})
            raise AssertionError(f"Unexpected URL called: {url}")

    monkeypatch.setattr(documents, "_build_pdf_extraction_service_headers", _service_headers)
    monkeypatch.setattr(documents.httpx, "AsyncClient", _DummyClient)

    result = await documents.get_pdf_extraction_health({"sub": "dev-user-123"})
    assert result["status"] == "degraded"
    assert result["worker_state"] == "sleeping"
    assert result["worker_available"] is False
    assert result["error"] == "Worker sleeping"


@pytest.mark.asyncio
async def test_pdf_extraction_health_deep_failure_sets_degraded(monkeypatch):
    monkeypatch.setenv("PDF_EXTRACTION_SERVICE_URL", "https://pdfx.example.org")

    async def _service_headers():
        return {"Authorization": "Bearer service-token"}

    class _DummyClient:
        def __init__(self, **_kwargs):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, headers=None):
            del headers
            if url.endswith("/api/v1/health"):
                return _DummyResponse(200, {"status": "healthy", "ec2": "ready"})
            if url.endswith("/api/v1/health/deep"):
                return _DummyResponse(503, {"status": "degraded"})
            if url.endswith("/api/v1/status"):
                return _DummyResponse(200, {"state": "ready"})
            raise AssertionError(f"Unexpected URL called: {url}")

    monkeypatch.setattr(documents, "_build_pdf_extraction_service_headers", _service_headers)
    monkeypatch.setattr(documents.httpx, "AsyncClient", _DummyClient)

    result = await documents.get_pdf_extraction_health({"sub": "dev-user-123"})
    assert result["status"] == "degraded"
    assert result["worker_available"] is True
    assert result["error"] == "Deep health check failed"


@pytest.mark.asyncio
async def test_pdf_extraction_health_keeps_healthy_when_deep_ok_even_if_proxy_flaps(monkeypatch):
    monkeypatch.setenv("PDF_EXTRACTION_SERVICE_URL", "https://pdfx.example.org")

    async def _service_headers():
        return {"Authorization": "Bearer service-token"}

    class _DummyClient:
        def __init__(self, **_kwargs):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, headers=None):
            del headers
            if url.endswith("/api/v1/health"):
                return _DummyResponse(503, {"status": "down"})
            if url.endswith("/api/v1/health/deep"):
                return _DummyResponse(200, {"status": "healthy"})
            if url.endswith("/api/v1/status"):
                return _DummyResponse(200, {"state": "ready"})
            raise AssertionError(f"Unexpected URL called: {url}")

    monkeypatch.setattr(documents, "_build_pdf_extraction_service_headers", _service_headers)
    monkeypatch.setattr(documents.httpx, "AsyncClient", _DummyClient)

    result = await documents.get_pdf_extraction_health({"sub": "dev-user-123"})
    assert result["status"] == "healthy"
    assert result["worker_available"] is True
    assert result["error"] == "Proxy status endpoint unavailable"


@pytest.mark.asyncio
async def test_pdf_extraction_health_captures_auth_header_builder_error(monkeypatch):
    monkeypatch.setenv("PDF_EXTRACTION_SERVICE_URL", "https://pdfx.example.org")

    async def _service_headers():
        raise HTTPException(status_code=500, detail="invalid auth mode")

    class _DummyClient:
        def __init__(self, **_kwargs):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, headers=None):
            assert headers in (None, {})
            if url.endswith("/api/v1/health"):
                return _DummyResponse(200, {"status": "healthy", "ec2": "ready"})
            if url.endswith("/api/v1/health/deep"):
                return _DummyResponse(200, {"status": "healthy"})
            if url.endswith("/api/v1/status"):
                return _DummyResponse(200, {"state": "ready"})
            raise AssertionError(f"Unexpected URL called: {url}")

    monkeypatch.setattr(documents, "_build_pdf_extraction_service_headers", _service_headers)
    monkeypatch.setattr(documents.httpx, "AsyncClient", _DummyClient)

    result = await documents.get_pdf_extraction_health({"sub": "dev-user-123"})
    assert result["status"] == "degraded"
    assert result["worker_available"] is True
    assert result["status_error"] == "invalid auth mode"
    assert result["error"] == "invalid auth mode"


@pytest.mark.asyncio
async def test_pdf_extraction_health_handles_non_json_payloads(monkeypatch):
    monkeypatch.setenv("PDF_EXTRACTION_SERVICE_URL", "https://pdfx.example.org")

    async def _service_headers():
        return {"Authorization": "Bearer service-token"}

    class _DummyClient:
        def __init__(self, **_kwargs):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, headers=None):
            del headers
            if url.endswith("/api/v1/health"):
                return _DummyBadJsonResponse(200, None, content=b"not-json")
            if url.endswith("/api/v1/health/deep"):
                return _DummyBadJsonResponse(200, None, content=b"not-json")
            if url.endswith("/api/v1/status"):
                return _DummyBadJsonResponse(200, None, content=b"not-json")
            raise AssertionError(f"Unexpected URL called: {url}")

    monkeypatch.setattr(documents, "_build_pdf_extraction_service_headers", _service_headers)
    monkeypatch.setattr(documents.httpx, "AsyncClient", _DummyClient)

    result = await documents.get_pdf_extraction_health({"sub": "dev-user-123"})
    assert result["status"] == "degraded"
    assert result["worker_state"] == "unknown"
    assert result["worker_available"] is False
    assert "invalid json" in result["status_error"].lower()


@pytest.mark.asyncio
async def test_pdf_extraction_health_status_endpoint_http_error_keeps_worker_unknown(monkeypatch):
    monkeypatch.setenv("PDF_EXTRACTION_SERVICE_URL", "https://pdfx.example.org")

    async def _service_headers():
        return {"Authorization": "Bearer service-token"}

    class _DummyClient:
        def __init__(self, **_kwargs):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, headers=None):
            del headers
            if url.endswith("/api/v1/health"):
                return _DummyResponse(200, {"status": "healthy", "ec2": "ready"})
            if url.endswith("/api/v1/health/deep"):
                return _DummyResponse(200, {"status": "healthy"})
            if url.endswith("/api/v1/status"):
                return _DummyResponse(503, {"state": "ready"})
            raise AssertionError(f"Unexpected URL called: {url}")

    monkeypatch.setattr(documents, "_build_pdf_extraction_service_headers", _service_headers)
    monkeypatch.setattr(documents.httpx, "AsyncClient", _DummyClient)

    result = await documents.get_pdf_extraction_health({"sub": "dev-user-123"})
    assert result["status"] == "degraded"
    assert result["worker_state"] == "unknown"
    assert result["worker_available"] is False
    assert result["status_error"] == "Status endpoint returned 503"


@pytest.mark.asyncio
async def test_pdf_extraction_health_returns_misconfigured_without_service_url(monkeypatch):
    monkeypatch.delenv("PDF_EXTRACTION_SERVICE_URL", raising=False)

    result = await documents.get_pdf_extraction_health({"sub": "dev-user-123"})
    assert result["status"] == "misconfigured"
    assert result["service_url"] == ""
    assert "not configured" in result["error"]


@pytest.mark.asyncio
async def test_pdf_extraction_health_surfaces_status_error_when_other_checks_healthy(monkeypatch):
    monkeypatch.setenv("PDF_EXTRACTION_SERVICE_URL", "https://pdfx.example.org")

    async def _service_headers():
        return {}

    class _DummyClient:
        def __init__(self, **_kwargs):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, headers=None):
            del headers
            if url.endswith("/api/v1/health"):
                return _DummyResponse(200, {"status": "healthy", "ec2": "ready"})
            if url.endswith("/api/v1/health/deep"):
                return _DummyResponse(200, {"status": "healthy"})
            if url.endswith("/api/v1/status"):
                req = documents.httpx.Request("GET", url)
                raise documents.httpx.RequestError("status network error", request=req)
            raise AssertionError(f"Unexpected URL called: {url}")

    monkeypatch.setattr(documents, "_build_pdf_extraction_service_headers", _service_headers)
    monkeypatch.setattr(documents.httpx, "AsyncClient", _DummyClient)

    result = await documents.get_pdf_extraction_health({"sub": "dev-user-123"})
    assert result["status"] == "healthy"
    assert result["worker_state"] == "ready"
    assert result["worker_available"] is True
    assert "status network error" in result["status_error"]
    assert "status network error" in result["error"]


@pytest.mark.asyncio
async def test_pdf_extraction_health_handles_request_error(monkeypatch):
    monkeypatch.setenv("PDF_EXTRACTION_SERVICE_URL", "https://pdfx.example.org")

    async def _service_headers():
        return {"Authorization": "Bearer service-token"}

    class _DummyClient:
        def __init__(self, **_kwargs):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, headers=None):
            del headers
            req = documents.httpx.Request("GET", url)
            raise documents.httpx.RequestError("proxy timeout", request=req)

    monkeypatch.setattr(documents, "_build_pdf_extraction_service_headers", _service_headers)
    monkeypatch.setattr(documents.httpx, "AsyncClient", _DummyClient)

    result = await documents.get_pdf_extraction_health({"sub": "dev-user-123"})
    assert result["status"] == "unreachable"
    assert result["worker_state"] == "unknown"
    assert result["worker_available"] is False
    assert "proxy timeout" in result["error"]
