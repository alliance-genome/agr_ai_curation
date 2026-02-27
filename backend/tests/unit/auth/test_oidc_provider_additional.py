"""Additional unit tests for OIDC provider branches."""

import asyncio
import base64
from urllib.parse import parse_qs, urlparse

import pytest

from src.auth.providers import oidc as oidc_module
from src.auth.providers.oidc import OIDCAuthProvider


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def test_discover_fetches_and_caches(monkeypatch):
    provider = OIDCAuthProvider(
        {
            "issuer_url": "https://issuer.example.org",
            "client_id": "oidc-client",
            "redirect_uri": "https://app.example.org/auth/callback",
        }
    )
    calls = {"count": 0}

    def _fake_get(url, timeout):
        calls["count"] += 1
        assert ".well-known/openid-configuration" in url
        assert timeout == provider.timeout_seconds
        return _FakeResponse({"issuer": "https://issuer.example.org", "jwks_uri": "https://issuer.example.org/jwks"})

    monkeypatch.setattr(oidc_module.httpx, "get", _fake_get)
    first = provider._discover()
    second = provider._discover()
    assert first["issuer"] == "https://issuer.example.org"
    assert second is first
    assert calls["count"] == 1


@pytest.mark.asyncio
async def test_discover_async_uses_to_thread(monkeypatch):
    provider = OIDCAuthProvider(
        {
            "issuer_url": "https://issuer.example.org",
            "client_id": "oidc-client",
            "redirect_uri": "https://app.example.org/auth/callback",
        }
    )

    async def _fake_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(oidc_module.asyncio, "to_thread", _fake_to_thread)
    monkeypatch.setattr(provider, "_discover", lambda: {"issuer": "https://issuer.example.org"})
    result = await provider._discover_async()
    assert result["issuer"] == "https://issuer.example.org"


def test_get_jwks_client_requires_jwks_uri(monkeypatch):
    provider = OIDCAuthProvider(
        {
            "issuer_url": "https://issuer.example.org",
            "client_id": "oidc-client",
            "redirect_uri": "https://app.example.org/auth/callback",
        }
    )
    monkeypatch.setattr(provider, "_discover", lambda: {"issuer": "https://issuer.example.org"})
    with pytest.raises(ValueError, match="missing jwks_uri"):
        provider._get_jwks_client()


def test_extract_groups_supports_list_string_and_dot_path():
    provider = OIDCAuthProvider(
        {
            "issuer_url": "https://issuer.example.org",
            "client_id": "oidc-client",
            "redirect_uri": "https://app.example.org/auth/callback",
            "group_claim": "realm_access.roles",
        }
    )
    assert provider._extract_groups({"realm_access": {"roles": ["curator", "admin"]}}) == ["curator", "admin"]
    assert provider._extract_groups({"realm_access": {"roles": "curator"}}) == ["curator"]
    assert provider._extract_groups({"realm_access": {"roles": 123}}) == []


def test_get_login_url_requires_authorization_endpoint(monkeypatch):
    provider = OIDCAuthProvider(
        {
            "issuer_url": "https://issuer.example.org",
            "client_id": "oidc-client",
            "redirect_uri": "https://app.example.org/auth/callback",
        }
    )
    monkeypatch.setattr(provider, "_discover", lambda: {"issuer": "https://issuer.example.org"})
    with pytest.raises(ValueError, match="authorization_endpoint"):
        provider.get_login_url("state", "challenge")


def test_get_login_url_builds_expected_query(monkeypatch):
    provider = OIDCAuthProvider(
        {
            "issuer_url": "https://issuer.example.org",
            "client_id": "oidc-client",
            "redirect_uri": "https://app.example.org/auth/callback",
            "scopes": "openid profile email",
        }
    )
    monkeypatch.setattr(
        provider,
        "_discover",
        lambda: {"authorization_endpoint": "https://issuer.example.org/authorize"},
    )
    url = provider.get_login_url("state-1", "challenge-1", "S256")
    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    assert parsed.scheme == "https"
    assert params["client_id"] == ["oidc-client"]
    assert params["state"] == ["state-1"]
    assert params["code_challenge"] == ["challenge-1"]


@pytest.mark.asyncio
async def test_handle_callback_requires_token_endpoint(monkeypatch):
    provider = OIDCAuthProvider(
        {
            "issuer_url": "https://issuer.example.org",
            "client_id": "oidc-client",
            "redirect_uri": "https://app.example.org/auth/callback",
        }
    )
    monkeypatch.setattr(provider, "_discover_async", lambda: asyncio.sleep(0, result={"issuer": "https://issuer.example.org"}))
    with pytest.raises(ValueError, match="token_endpoint"):
        await provider.handle_callback("code", "verifier")


@pytest.mark.asyncio
async def test_handle_callback_with_client_secret_sends_basic_auth(monkeypatch):
    provider = OIDCAuthProvider(
        {
            "issuer_url": "https://issuer.example.org",
            "client_id": "oidc-client",
            "client_secret": "secret-123",
            "redirect_uri": "https://app.example.org/auth/callback",
        }
    )
    sent = {}

    class _FakeAsyncClient:
        def __init__(self, timeout):
            assert timeout == provider.timeout_seconds

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, data, headers):
            sent["url"] = url
            sent["data"] = data
            sent["headers"] = headers
            return _FakeResponse({"id_token": "id-token", "access_token": "access-token", "expires_in": 60})

    monkeypatch.setattr(
        provider,
        "_discover_async",
        lambda: asyncio.sleep(0, result={"token_endpoint": "https://issuer.example.org/token"}),
    )
    monkeypatch.setattr(oidc_module.httpx, "AsyncClient", _FakeAsyncClient)

    token_set = await provider.handle_callback("code-1", "verifier-1")
    assert token_set.id_token == "id-token"
    assert "client_id" not in sent["data"]
    expected_creds = base64.b64encode(b"oidc-client:secret-123").decode("utf-8")
    assert sent["headers"]["Authorization"] == f"Basic {expected_creds}"


@pytest.mark.asyncio
async def test_handle_callback_without_client_secret_includes_client_id(monkeypatch):
    provider = OIDCAuthProvider(
        {
            "issuer_url": "https://issuer.example.org",
            "client_id": "oidc-client",
            "redirect_uri": "https://app.example.org/auth/callback",
        }
    )
    sent = {}

    class _FakeAsyncClient:
        def __init__(self, timeout):
            assert timeout == provider.timeout_seconds

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, data, headers):
            sent["url"] = url
            sent["data"] = data
            sent["headers"] = headers
            return _FakeResponse({"id_token": "id-token"})

    monkeypatch.setattr(
        provider,
        "_discover_async",
        lambda: asyncio.sleep(0, result={"token_endpoint": "https://issuer.example.org/token"}),
    )
    monkeypatch.setattr(oidc_module.httpx, "AsyncClient", _FakeAsyncClient)

    token_set = await provider.handle_callback("code-1", "verifier-1")
    assert token_set.id_token == "id-token"
    assert sent["data"]["client_id"] == "oidc-client"


@pytest.mark.asyncio
async def test_handle_callback_requires_id_token(monkeypatch):
    provider = OIDCAuthProvider(
        {
            "issuer_url": "https://issuer.example.org",
            "client_id": "oidc-client",
            "redirect_uri": "https://app.example.org/auth/callback",
        }
    )

    class _FakeAsyncClient:
        def __init__(self, timeout):
            assert timeout == provider.timeout_seconds

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, data, headers):
            return _FakeResponse({"access_token": "access-token"})

    monkeypatch.setattr(
        provider,
        "_discover_async",
        lambda: asyncio.sleep(0, result={"token_endpoint": "https://issuer.example.org/token"}),
    )
    monkeypatch.setattr(oidc_module.httpx, "AsyncClient", _FakeAsyncClient)

    with pytest.raises(ValueError, match="missing id_token"):
        await provider.handle_callback("code-1", "verifier-1")


def test_get_logout_url_returns_none_when_discovery_has_no_end_session(monkeypatch):
    provider = OIDCAuthProvider(
        {
            "issuer_url": "https://issuer.example.org",
            "client_id": "oidc-client",
            "redirect_uri": "https://app.example.org/auth/callback",
        }
    )
    monkeypatch.setattr(provider, "_discover", lambda: {"issuer": "https://issuer.example.org"})
    assert provider.get_logout_url("https://app.example.org/") is None


def test_get_logout_url_returns_endpoint_without_params_when_none_available(monkeypatch):
    provider = OIDCAuthProvider(
        {
            "issuer_url": "https://issuer.example.org",
            "client_id": "",
            "redirect_uri": "",
        }
    )
    monkeypatch.setattr(
        provider,
        "_discover",
        lambda: {"end_session_endpoint": "https://issuer.example.org/end-session"},
    )
    assert provider.get_logout_url(None) == "https://issuer.example.org/end-session"


def test_extract_principal_and_provider_name():
    provider = OIDCAuthProvider(
        {
            "issuer_url": "https://issuer.example.org",
            "client_id": "oidc-client",
            "redirect_uri": "https://app.example.org/auth/callback",
            "group_claim": "groups",
        }
    )
    principal = provider.extract_principal(
        {
            "sub": "user-123",
            "email": "user@example.org",
            "preferred_username": "test-user",
            "groups": ["developers"],
        }
    )
    assert provider.provider_name == "oidc"
    assert principal.subject == "user-123"
    assert principal.groups == ["developers"]
