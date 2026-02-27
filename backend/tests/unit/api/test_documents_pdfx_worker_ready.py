"""Unit tests for PDFX worker readiness guard."""

import pytest
from fastapi import HTTPException

from src.api import documents


class _DummyResponse:
    def __init__(self, status_code: int, payload: dict | None = None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.content = b"{}"

    def json(self):
        return self._payload


class _DummyBadJsonResponse(_DummyResponse):
    def json(self):
        raise ValueError("invalid json")


class _DummyListJsonResponse(_DummyResponse):
    def json(self):
        return ["not-a-dict"]


class _DummyEmptyContentResponse(_DummyResponse):
    def __init__(self, status_code: int, payload: dict | None = None):
        super().__init__(status_code, payload)
        self.content = b""


@pytest.mark.asyncio
async def test_require_pdfx_worker_ready_no_service_url(monkeypatch):
    monkeypatch.delenv("PDF_EXTRACTION_SERVICE_URL", raising=False)

    await documents._require_pdf_extraction_worker_ready()


@pytest.mark.asyncio
async def test_require_pdfx_worker_ready_accepts_ready_state(monkeypatch):
    monkeypatch.setenv("PDF_EXTRACTION_SERVICE_URL", "https://pdfx.example.org")

    class _DummyClient:
        def __init__(self, **_kwargs):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, headers=None):
            del headers
            if url.endswith("/api/v1/status"):
                return _DummyResponse(200, {"state": "ready"})
            raise AssertionError(f"Unexpected URL: {url}")

    async def _service_headers():
        return {"Authorization": "Bearer service-token"}

    monkeypatch.setattr(documents, "_build_pdf_extraction_service_headers", _service_headers)
    monkeypatch.setattr(documents.httpx, "AsyncClient", _DummyClient)

    await documents._require_pdf_extraction_worker_ready()


@pytest.mark.asyncio
async def test_require_pdfx_worker_ready_no_auth_uses_health_endpoint(monkeypatch):
    monkeypatch.setenv("PDF_EXTRACTION_SERVICE_URL", "https://pdfx.example.org")

    called_urls = []

    class _DummyClient:
        def __init__(self, **_kwargs):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, headers=None):
            called_urls.append((url, headers))
            if url.endswith("/api/v1/health"):
                return _DummyResponse(200, {"status": "healthy", "ec2": "ready"})
            raise AssertionError(f"Unexpected URL: {url}")

    async def _service_headers():
        return {}

    monkeypatch.setattr(documents, "_build_pdf_extraction_service_headers", _service_headers)
    monkeypatch.setattr(documents.httpx, "AsyncClient", _DummyClient)

    await documents._require_pdf_extraction_worker_ready()
    assert len(called_urls) == 1
    assert called_urls[0][0] == "https://pdfx.example.org/api/v1/health"
    assert called_urls[0][1] in (None, {})


@pytest.mark.asyncio
async def test_require_pdfx_worker_ready_raises_on_unavailable_state(monkeypatch):
    monkeypatch.setenv("PDF_EXTRACTION_SERVICE_URL", "https://pdfx.example.org")

    class _DummyClient:
        def __init__(self, **_kwargs):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, headers=None):
            del headers
            if url.endswith("/api/v1/status"):
                return _DummyResponse(200, {"state": "sleeping"})
            raise AssertionError(f"Unexpected URL: {url}")

    async def _service_headers():
        return {"Authorization": "Bearer service-token"}

    monkeypatch.setattr(documents, "_build_pdf_extraction_service_headers", _service_headers)
    monkeypatch.setattr(documents.httpx, "AsyncClient", _DummyClient)

    with pytest.raises(HTTPException) as exc:
        await documents._require_pdf_extraction_worker_ready()
    assert exc.value.status_code == 503
    assert exc.value.detail["error"] == "pdf_extraction_worker_not_ready"
    assert exc.value.detail["worker_state"] == "sleeping"


@pytest.mark.asyncio
async def test_require_pdfx_worker_ready_raises_on_status_http_error(monkeypatch):
    monkeypatch.setenv("PDF_EXTRACTION_SERVICE_URL", "https://pdfx.example.org")

    class _DummyClient:
        def __init__(self, **_kwargs):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, headers=None):
            del headers
            if url.endswith("/api/v1/status"):
                return _DummyResponse(503, {"state": "sleeping"})
            raise AssertionError(f"Unexpected URL: {url}")

    async def _service_headers():
        return {"Authorization": "Bearer service-token"}

    monkeypatch.setattr(documents, "_build_pdf_extraction_service_headers", _service_headers)
    monkeypatch.setattr(documents.httpx, "AsyncClient", _DummyClient)

    with pytest.raises(HTTPException) as exc:
        await documents._require_pdf_extraction_worker_ready()
    assert exc.value.status_code == 503
    assert "worker status check failed" in str(exc.value.detail).lower()


@pytest.mark.asyncio
async def test_require_pdfx_worker_ready_no_auth_uses_health_state_for_sleeping_worker(monkeypatch):
    monkeypatch.setenv("PDF_EXTRACTION_SERVICE_URL", "https://pdfx.example.org")

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
                return _DummyResponse(200, {"status": "healthy", "ec2": "sleeping"})
            raise AssertionError(f"Unexpected URL: {url}")

    async def _service_headers():
        return {}

    monkeypatch.setattr(documents, "_build_pdf_extraction_service_headers", _service_headers)
    monkeypatch.setattr(documents.httpx, "AsyncClient", _DummyClient)

    with pytest.raises(HTTPException) as exc:
        await documents._require_pdf_extraction_worker_ready()
    assert exc.value.status_code == 503
    assert exc.value.detail["worker_state"] == "sleeping"


@pytest.mark.asyncio
async def test_require_pdfx_worker_ready_handles_non_json_status_payload(monkeypatch):
    monkeypatch.setenv("PDF_EXTRACTION_SERVICE_URL", "https://pdfx.example.org")

    class _DummyClient:
        def __init__(self, **_kwargs):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, headers=None):
            del headers
            if url.endswith("/api/v1/status"):
                return _DummyBadJsonResponse(200, {})
            raise AssertionError(f"Unexpected URL: {url}")

    async def _service_headers():
        return {"Authorization": "Bearer service-token"}

    monkeypatch.setattr(documents, "_build_pdf_extraction_service_headers", _service_headers)
    monkeypatch.setattr(documents.httpx, "AsyncClient", _DummyClient)

    with pytest.raises(HTTPException) as exc:
        await documents._require_pdf_extraction_worker_ready()
    assert exc.value.status_code == 503
    assert exc.value.detail["worker_state"] == "unknown"


@pytest.mark.asyncio
async def test_require_pdfx_worker_ready_handles_transport_error(monkeypatch):
    monkeypatch.setenv("PDF_EXTRACTION_SERVICE_URL", "https://pdfx.example.org")

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
            raise documents.httpx.RequestError("connection reset", request=req)

    async def _service_headers():
        return {"Authorization": "Bearer service-token"}

    monkeypatch.setattr(documents, "_build_pdf_extraction_service_headers", _service_headers)
    monkeypatch.setattr(documents.httpx, "AsyncClient", _DummyClient)

    with pytest.raises(HTTPException) as exc:
        await documents._require_pdf_extraction_worker_ready()
    assert exc.value.status_code == 503
    assert "Unable to reach PDF extraction worker status endpoint" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_require_pdfx_worker_ready_handles_non_dict_json_payload(monkeypatch):
    monkeypatch.setenv("PDF_EXTRACTION_SERVICE_URL", "https://pdfx.example.org")

    class _DummyClient:
        def __init__(self, **_kwargs):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, headers=None):
            del headers
            if url.endswith("/api/v1/status"):
                return _DummyListJsonResponse(200, {})
            raise AssertionError(f"Unexpected URL: {url}")

    async def _service_headers():
        return {"Authorization": "Bearer service-token"}

    monkeypatch.setattr(documents, "_build_pdf_extraction_service_headers", _service_headers)
    monkeypatch.setattr(documents.httpx, "AsyncClient", _DummyClient)

    with pytest.raises(HTTPException) as exc:
        await documents._require_pdf_extraction_worker_ready()
    assert exc.value.status_code == 503
    assert exc.value.detail["worker_state"] == "unknown"


@pytest.mark.asyncio
async def test_require_pdfx_worker_ready_handles_empty_status_body(monkeypatch):
    monkeypatch.setenv("PDF_EXTRACTION_SERVICE_URL", "https://pdfx.example.org")

    class _DummyClient:
        def __init__(self, **_kwargs):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, headers=None):
            del headers
            if url.endswith("/api/v1/status"):
                return _DummyEmptyContentResponse(200, {"state": "ready"})
            raise AssertionError(f"Unexpected URL: {url}")

    async def _service_headers():
        return {"Authorization": "Bearer service-token"}

    monkeypatch.setattr(documents, "_build_pdf_extraction_service_headers", _service_headers)
    monkeypatch.setattr(documents.httpx, "AsyncClient", _DummyClient)

    with pytest.raises(HTTPException) as exc:
        await documents._require_pdf_extraction_worker_ready()
    assert exc.value.status_code == 503
    assert exc.value.detail["worker_state"] == "unknown"
