"""Unit tests for PDFX wake endpoint behavior."""

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
async def test_wake_pdfx_requires_service_url(monkeypatch):
    monkeypatch.delenv("PDF_EXTRACTION_SERVICE_URL", raising=False)

    with pytest.raises(HTTPException) as exc:
        await documents.wake_pdf_extraction_worker({"sub": "dev-user-123"})
    assert exc.value.status_code == 500


@pytest.mark.asyncio
async def test_wake_pdfx_raises_on_transport_error(monkeypatch):
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

        async def post(self, url, headers=None):
            del headers
            req = documents.httpx.Request("POST", url)
            raise documents.httpx.RequestError("network down", request=req)

    monkeypatch.setattr(documents, "_build_pdf_extraction_service_headers", _service_headers)
    monkeypatch.setattr(documents.httpx, "AsyncClient", _DummyClient)

    with pytest.raises(HTTPException) as exc:
        await documents.wake_pdf_extraction_worker({"sub": "dev-user-123"})
    assert exc.value.status_code == 502
    assert "Failed to wake PDF extraction worker" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_wake_pdfx_raises_on_upstream_error_status(monkeypatch):
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

        async def post(self, _url, headers=None):
            del headers
            return _DummyResponse(503, {"error": "unavailable"})

        async def get(self, _url, headers=None):
            del headers
            return _DummyResponse(200, {"state": "sleeping"})

    monkeypatch.setattr(documents, "_build_pdf_extraction_service_headers", _service_headers)
    monkeypatch.setattr(documents.httpx, "AsyncClient", _DummyClient)

    with pytest.raises(HTTPException) as exc:
        await documents.wake_pdf_extraction_worker({"sub": "dev-user-123"})
    assert exc.value.status_code == 502
    assert "Wake request failed (503)" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_wake_pdfx_rejects_non_json_wake_payload(monkeypatch):
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

        async def post(self, _url, headers=None):
            del headers
            return _DummyBadJsonResponse(200, None, content=b"invalid")

        async def get(self, _url, headers=None):
            del headers
            return _DummyResponse(200, {"state": "ready"})

    monkeypatch.setattr(documents, "_build_pdf_extraction_service_headers", _service_headers)
    monkeypatch.setattr(documents.httpx, "AsyncClient", _DummyClient)

    with pytest.raises(HTTPException) as exc:
        await documents.wake_pdf_extraction_worker({"sub": "dev-user-123"})
    assert exc.value.status_code == 502
    assert "non-JSON" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_wake_pdfx_non_json_status_payload_defaults_unknown(monkeypatch):
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

        async def post(self, _url, headers=None):
            del headers
            return _DummyResponse(200, {"state": "starting"})

        async def get(self, _url, headers=None):
            del headers
            return _DummyBadJsonResponse(200, None, content=b"invalid")

    monkeypatch.setattr(documents, "_build_pdf_extraction_service_headers", _service_headers)
    monkeypatch.setattr(documents.httpx, "AsyncClient", _DummyClient)

    result = await documents.wake_pdf_extraction_worker({"sub": "dev-user-123"})
    assert result["worker_state"] == "unknown"
    assert result["worker_available"] is False
    assert result["wake_required"] is True


@pytest.mark.asyncio
async def test_wake_pdfx_returns_ready_worker(monkeypatch):
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

        async def post(self, _url, headers=None):
            del headers
            return _DummyResponse(200, {"state": "waking"})

        async def get(self, _url, headers=None):
            del headers
            return _DummyResponse(200, {"state": "ready"})

    monkeypatch.setattr(documents, "_build_pdf_extraction_service_headers", _service_headers)
    monkeypatch.setattr(documents.httpx, "AsyncClient", _DummyClient)

    result = await documents.wake_pdf_extraction_worker({"sub": "dev-user-123"})
    assert result["worker_state"] == "ready"
    assert result["worker_available"] is True
    assert result["wake_required"] is False


@pytest.mark.asyncio
async def test_wake_pdfx_handles_empty_wake_and_status_bodies(monkeypatch):
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

        async def post(self, _url, headers=None):
            del headers
            return _DummyResponse(202, payload=None, content=b"")

        async def get(self, _url, headers=None):
            del headers
            return _DummyResponse(200, payload=None, content=b"")

    monkeypatch.setattr(documents, "_build_pdf_extraction_service_headers", _service_headers)
    monkeypatch.setattr(documents.httpx, "AsyncClient", _DummyClient)

    result = await documents.wake_pdf_extraction_worker({"sub": "dev-user-123"})
    assert result["wake_response_code"] == 202
    assert result["wake_details"] == {}
    assert result["status_details"] == {}
    assert result["worker_state"] == "unknown"
    assert result["worker_available"] is False
    assert result["wake_required"] is True
