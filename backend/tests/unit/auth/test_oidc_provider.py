"""Unit tests for OIDC auth provider internals."""

import asyncio
from types import SimpleNamespace

import pytest

from src.auth.providers import oidc as oidc_module
from src.auth.providers.oidc import OIDCAuthProvider


def test_validate_token_uses_pyjwt_decode(monkeypatch):
    """validate_token should decode JWTs through PyJWT."""
    provider = OIDCAuthProvider(
        {
            "issuer_url": "https://issuer.example.org",
            "client_id": "oidc-client",
            "redirect_uri": "https://app.example.org/auth/callback",
        }
    )

    async def _discover_async():
        return {"issuer": "https://issuer.example.org"}

    class _FakeJwksClient:
        def get_signing_key_from_jwt(self, token):
            assert token == "id-token"
            return SimpleNamespace(key="fake-signing-key")

    async def _direct_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    captured = {}

    def _fake_decode(token, key, algorithms, audience, issuer):
        captured["token"] = token
        captured["key"] = key
        captured["algorithms"] = algorithms
        captured["audience"] = audience
        captured["issuer"] = issuer
        return {"sub": "user-123", "email": "user@example.org"}

    monkeypatch.setattr(provider, "_discover_async", _discover_async)
    monkeypatch.setattr(provider, "_get_jwks_client", lambda: _FakeJwksClient())
    monkeypatch.setattr(oidc_module.asyncio, "to_thread", _direct_to_thread)
    monkeypatch.setattr(oidc_module.jwt, "decode", _fake_decode)

    claims = asyncio.run(provider.validate_token("id-token"))

    assert claims["sub"] == "user-123"
    assert captured["token"] == "id-token"
    assert captured["key"] == "fake-signing-key"
    assert captured["algorithms"] == ["RS256", "RS384", "ES256", "ES384"]
    assert captured["audience"] == "oidc-client"
    assert captured["issuer"] == "https://issuer.example.org"


def test_validate_token_reraises_pyjwt_errors(monkeypatch):
    """validate_token should surface PyJWT decode errors."""
    provider = OIDCAuthProvider(
        {
            "issuer_url": "https://issuer.example.org",
            "client_id": "oidc-client",
            "redirect_uri": "https://app.example.org/auth/callback",
        }
    )

    async def _discover_async():
        return {"issuer": "https://issuer.example.org"}

    class _FakeJwksClient:
        def get_signing_key_from_jwt(self, _token):
            return SimpleNamespace(key="fake-signing-key")

    async def _direct_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    def _failing_decode(*_args, **_kwargs):
        raise oidc_module.PyJWTError("bad token")

    monkeypatch.setattr(provider, "_discover_async", _discover_async)
    monkeypatch.setattr(provider, "_get_jwks_client", lambda: _FakeJwksClient())
    monkeypatch.setattr(oidc_module.asyncio, "to_thread", _direct_to_thread)
    monkeypatch.setattr(oidc_module.jwt, "decode", _failing_decode)

    with pytest.raises(oidc_module.PyJWTError, match="bad token"):
        asyncio.run(provider.validate_token("id-token"))


def test_get_jwks_client_is_cached(monkeypatch):
    """JWKS client should be lazily created once and then reused."""
    provider = OIDCAuthProvider(
        {
            "issuer_url": "https://issuer.example.org",
            "client_id": "oidc-client",
            "redirect_uri": "https://app.example.org/auth/callback",
        }
    )

    created = {"count": 0}

    class _FakePyJWKClient:
        def __init__(self, jwks_uri):
            created["count"] += 1
            self.jwks_uri = jwks_uri

    monkeypatch.setattr(provider, "_discover", lambda: {"jwks_uri": "https://issuer.example.org/jwks"})
    monkeypatch.setattr(oidc_module, "PyJWKClient", _FakePyJWKClient)

    first = provider._get_jwks_client()
    second = provider._get_jwks_client()

    assert created["count"] == 1
    assert first is second
