"""Unit tests for PDFX service auth header construction."""

import base64

import pytest
from fastapi import HTTPException

from src.api import documents


class _DummyResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _DummyBadJsonResponse(_DummyResponse):
    def json(self):
        raise ValueError("invalid json")


@pytest.fixture(autouse=True)
def _reset_pdfx_token_cache():
    documents._pdf_extraction_service_token = None
    documents._pdf_extraction_service_token_expires_at = 0.0
    yield
    documents._pdf_extraction_service_token = None
    documents._pdf_extraction_service_token_expires_at = 0.0


@pytest.mark.asyncio
async def test_pdfx_auth_headers_static_bearer(monkeypatch):
    monkeypatch.setenv("PDF_EXTRACTION_AUTH_MODE", "static_bearer")
    monkeypatch.setenv("PDF_EXTRACTION_BEARER_TOKEN", "pdfx-token-123")

    headers = await documents._build_pdf_extraction_service_headers()
    assert headers == {"Authorization": "Bearer pdfx-token-123"}


@pytest.mark.asyncio
async def test_pdfx_auth_headers_none_mode_returns_empty(monkeypatch):
    monkeypatch.setenv("PDF_EXTRACTION_AUTH_MODE", "none")

    headers = await documents._build_pdf_extraction_service_headers()
    assert headers == {}


@pytest.mark.asyncio
async def test_pdfx_auth_headers_static_bearer_requires_token(monkeypatch):
    monkeypatch.setenv("PDF_EXTRACTION_AUTH_MODE", "static_bearer")
    monkeypatch.delenv("PDF_EXTRACTION_BEARER_TOKEN", raising=False)

    with pytest.raises(HTTPException) as exc:
        await documents._build_pdf_extraction_service_headers()
    assert exc.value.status_code == 500
    assert "PDF_EXTRACTION_BEARER_TOKEN" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_pdfx_auth_headers_cognito_token_cached(monkeypatch):
    monkeypatch.setenv("PDF_EXTRACTION_AUTH_MODE", "cognito_client_credentials")
    monkeypatch.setenv("PDF_EXTRACTION_COGNITO_TOKEN_URL", "https://cognito.local/oauth2/token")
    monkeypatch.setenv("PDF_EXTRACTION_COGNITO_CLIENT_ID", "client-id")
    monkeypatch.setenv("PDF_EXTRACTION_COGNITO_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("PDF_EXTRACTION_COGNITO_SCOPE", "pdfx/read")

    post_calls = []

    class _DummyClient:
        def __init__(self, **_kwargs):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, data=None, headers=None):
            post_calls.append({"url": url, "data": data, "headers": headers})
            return _DummyResponse(200, {"access_token": "token-a", "expires_in": 3600})

    monkeypatch.setattr(documents.httpx, "AsyncClient", _DummyClient)

    first_headers = await documents._build_pdf_extraction_service_headers()
    second_headers = await documents._build_pdf_extraction_service_headers()

    expected_basic = base64.b64encode(b"client-id:client-secret").decode("ascii")
    assert first_headers == {"Authorization": "Bearer token-a"}
    assert second_headers == {"Authorization": "Bearer token-a"}
    assert len(post_calls) == 1
    assert post_calls[0]["url"] == "https://cognito.local/oauth2/token"
    assert post_calls[0]["data"] == {"grant_type": "client_credentials", "scope": "pdfx/read"}
    assert post_calls[0]["headers"]["Authorization"] == f"Basic {expected_basic}"


@pytest.mark.asyncio
async def test_pdfx_auth_headers_cognito_refreshes_expired_token(monkeypatch):
    monkeypatch.setenv("PDF_EXTRACTION_AUTH_MODE", "cognito_client_credentials")
    monkeypatch.setenv("PDF_EXTRACTION_COGNITO_TOKEN_URL", "https://cognito.local/oauth2/token")
    monkeypatch.setenv("PDF_EXTRACTION_COGNITO_CLIENT_ID", "client-id")
    monkeypatch.setenv("PDF_EXTRACTION_COGNITO_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("PDF_EXTRACTION_COGNITO_SCOPE", "pdfx/read")

    responses = [
        _DummyResponse(200, {"access_token": "token-a", "expires_in": 3600}),
        _DummyResponse(200, {"access_token": "token-b", "expires_in": 3600}),
    ]

    class _DummyClient:
        def __init__(self, **_kwargs):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, _url, data=None, headers=None):
            del data, headers
            return responses.pop(0)

    monkeypatch.setattr(documents.httpx, "AsyncClient", _DummyClient)

    first_headers = await documents._build_pdf_extraction_service_headers()
    documents._pdf_extraction_service_token_expires_at = 0.0
    second_headers = await documents._build_pdf_extraction_service_headers()

    assert first_headers == {"Authorization": "Bearer token-a"}
    assert second_headers == {"Authorization": "Bearer token-b"}


@pytest.mark.asyncio
async def test_pdfx_auth_headers_cognito_uses_domain_fallback_for_token_url(monkeypatch):
    monkeypatch.setenv("PDF_EXTRACTION_AUTH_MODE", "cognito_client_credentials")
    monkeypatch.delenv("PDF_EXTRACTION_COGNITO_TOKEN_URL", raising=False)
    monkeypatch.setenv("COGNITO_DOMAIN", "https://auth.example.org/")
    monkeypatch.setenv("PDF_EXTRACTION_COGNITO_CLIENT_ID", "client-id")
    monkeypatch.setenv("PDF_EXTRACTION_COGNITO_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("PDF_EXTRACTION_COGNITO_SCOPE", "pdfx/read")

    called_urls = []

    class _DummyClient:
        def __init__(self, **_kwargs):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, data=None, headers=None):
            del data, headers
            called_urls.append(url)
            return _DummyResponse(200, {"access_token": "domain-token", "expires_in": 3600})

    monkeypatch.setattr(documents.httpx, "AsyncClient", _DummyClient)

    headers = await documents._build_pdf_extraction_service_headers()
    assert headers == {"Authorization": "Bearer domain-token"}
    assert called_urls == ["https://auth.example.org/oauth2/token"]


@pytest.mark.asyncio
async def test_pdfx_auth_headers_cognito_invalid_expires_in_defaults(monkeypatch):
    monkeypatch.setenv("PDF_EXTRACTION_AUTH_MODE", "cognito_client_credentials")
    monkeypatch.setenv("PDF_EXTRACTION_COGNITO_TOKEN_URL", "https://cognito.local/oauth2/token")
    monkeypatch.setenv("PDF_EXTRACTION_COGNITO_CLIENT_ID", "client-id")
    monkeypatch.setenv("PDF_EXTRACTION_COGNITO_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("PDF_EXTRACTION_COGNITO_SCOPE", "pdfx/read")

    class _DummyClient:
        def __init__(self, **_kwargs):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, _url, data=None, headers=None):
            del data, headers
            return _DummyResponse(200, {"access_token": "token-a", "expires_in": "not-an-int"})

    monkeypatch.setattr(documents.httpx, "AsyncClient", _DummyClient)

    headers = await documents._build_pdf_extraction_service_headers()
    assert headers == {"Authorization": "Bearer token-a"}
    assert documents._pdf_extraction_service_token_expires_at > 0


@pytest.mark.asyncio
async def test_pdfx_auth_headers_cognito_raises_on_token_error(monkeypatch):
    monkeypatch.setenv("PDF_EXTRACTION_AUTH_MODE", "cognito_client_credentials")
    monkeypatch.setenv("PDF_EXTRACTION_COGNITO_TOKEN_URL", "https://cognito.local/oauth2/token")
    monkeypatch.setenv("PDF_EXTRACTION_COGNITO_CLIENT_ID", "client-id")
    monkeypatch.setenv("PDF_EXTRACTION_COGNITO_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("PDF_EXTRACTION_COGNITO_SCOPE", "pdfx/read")

    class _DummyClient:
        def __init__(self, **_kwargs):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, _url, data=None, headers=None):
            del data, headers
            return _DummyResponse(502, {"error": "bad_gateway"})

    monkeypatch.setattr(documents.httpx, "AsyncClient", _DummyClient)

    with pytest.raises(HTTPException) as exc:
        await documents._build_pdf_extraction_service_headers()
    assert exc.value.status_code == 502


@pytest.mark.asyncio
async def test_pdfx_auth_headers_cognito_requires_access_token(monkeypatch):
    monkeypatch.setenv("PDF_EXTRACTION_AUTH_MODE", "cognito_client_credentials")
    monkeypatch.setenv("PDF_EXTRACTION_COGNITO_TOKEN_URL", "https://cognito.local/oauth2/token")
    monkeypatch.setenv("PDF_EXTRACTION_COGNITO_CLIENT_ID", "client-id")
    monkeypatch.setenv("PDF_EXTRACTION_COGNITO_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("PDF_EXTRACTION_COGNITO_SCOPE", "pdfx/read")

    class _DummyClient:
        def __init__(self, **_kwargs):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, _url, data=None, headers=None):
            del data, headers
            return _DummyResponse(200, {"expires_in": 3600})

    monkeypatch.setattr(documents.httpx, "AsyncClient", _DummyClient)

    with pytest.raises(HTTPException) as exc:
        await documents._build_pdf_extraction_service_headers()
    assert exc.value.status_code == 502


@pytest.mark.asyncio
async def test_pdfx_auth_headers_cognito_handles_transport_error(monkeypatch):
    monkeypatch.setenv("PDF_EXTRACTION_AUTH_MODE", "cognito_client_credentials")
    monkeypatch.setenv("PDF_EXTRACTION_COGNITO_TOKEN_URL", "https://cognito.local/oauth2/token")
    monkeypatch.setenv("PDF_EXTRACTION_COGNITO_CLIENT_ID", "client-id")
    monkeypatch.setenv("PDF_EXTRACTION_COGNITO_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("PDF_EXTRACTION_COGNITO_SCOPE", "pdfx/read")

    class _DummyClient:
        def __init__(self, **_kwargs):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, _url, data=None, headers=None):
            del data, headers
            req = documents.httpx.Request("POST", _url)
            raise documents.httpx.RequestError("network down", request=req)

    monkeypatch.setattr(documents.httpx, "AsyncClient", _DummyClient)

    with pytest.raises(HTTPException) as exc:
        await documents._build_pdf_extraction_service_headers()
    assert exc.value.status_code == 502
    assert "Failed to fetch PDF extraction service token" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_pdfx_auth_headers_cognito_handles_invalid_json(monkeypatch):
    monkeypatch.setenv("PDF_EXTRACTION_AUTH_MODE", "cognito_client_credentials")
    monkeypatch.setenv("PDF_EXTRACTION_COGNITO_TOKEN_URL", "https://cognito.local/oauth2/token")
    monkeypatch.setenv("PDF_EXTRACTION_COGNITO_CLIENT_ID", "client-id")
    monkeypatch.setenv("PDF_EXTRACTION_COGNITO_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("PDF_EXTRACTION_COGNITO_SCOPE", "pdfx/read")

    class _DummyClient:
        def __init__(self, **_kwargs):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, _url, data=None, headers=None):
            del data, headers
            return _DummyBadJsonResponse(200, {})

    monkeypatch.setattr(documents.httpx, "AsyncClient", _DummyClient)

    with pytest.raises(HTTPException) as exc:
        await documents._build_pdf_extraction_service_headers()
    assert exc.value.status_code == 502
    assert "not valid JSON" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_pdfx_auth_headers_unsupported_mode(monkeypatch):
    monkeypatch.setenv("PDF_EXTRACTION_AUTH_MODE", "jwt_passthrough")

    with pytest.raises(HTTPException) as exc:
        await documents._build_pdf_extraction_service_headers()
    assert exc.value.status_code == 500
    assert "Unsupported PDF_EXTRACTION_AUTH_MODE" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_pdfx_auth_headers_cognito_requires_token_url_or_domain(monkeypatch):
    monkeypatch.setenv("PDF_EXTRACTION_AUTH_MODE", "cognito_client_credentials")
    monkeypatch.delenv("PDF_EXTRACTION_COGNITO_TOKEN_URL", raising=False)
    monkeypatch.delenv("COGNITO_DOMAIN", raising=False)
    monkeypatch.setenv("PDF_EXTRACTION_COGNITO_CLIENT_ID", "client-id")
    monkeypatch.setenv("PDF_EXTRACTION_COGNITO_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("PDF_EXTRACTION_COGNITO_SCOPE", "pdfx/read")

    with pytest.raises(HTTPException) as exc:
        await documents._build_pdf_extraction_service_headers()
    assert exc.value.status_code == 500
    assert "PDF_EXTRACTION_COGNITO_TOKEN_URL or COGNITO_DOMAIN" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_pdfx_auth_headers_cognito_requires_client_credentials(monkeypatch):
    monkeypatch.setenv("PDF_EXTRACTION_AUTH_MODE", "cognito_client_credentials")
    monkeypatch.setenv("PDF_EXTRACTION_COGNITO_TOKEN_URL", "https://cognito.local/oauth2/token")
    monkeypatch.delenv("PDF_EXTRACTION_COGNITO_CLIENT_ID", raising=False)
    monkeypatch.setenv("PDF_EXTRACTION_COGNITO_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("PDF_EXTRACTION_COGNITO_SCOPE", "pdfx/read")

    with pytest.raises(HTTPException) as exc:
        await documents._build_pdf_extraction_service_headers()
    assert exc.value.status_code == 500
    assert "PDF_EXTRACTION_COGNITO_CLIENT_ID" in str(exc.value.detail)
