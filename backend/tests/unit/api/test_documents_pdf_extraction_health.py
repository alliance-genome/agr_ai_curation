"""Unit tests for PDF extraction proxy health aggregation."""

import pytest

from src.api import documents


class _DummyResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload
        self.content = b"{}"

    def json(self):
        return self._payload


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

    result = await documents.get_pdf_extraction_health()

    assert result["status"] == "healthy"
    assert result["worker_available"] is True

    deep_headers = next(headers for url, headers in calls if url.endswith("/api/v1/health/deep"))
    assert deep_headers == {"Authorization": "Bearer service-token"}

    health_headers = next(headers for url, headers in calls if url.endswith("/api/v1/health"))
    assert health_headers == {"Authorization": "Bearer service-token"}

    status_headers = next(headers for url, headers in calls if url.endswith("/api/v1/status"))
    assert status_headers == {"Authorization": "Bearer service-token"}
