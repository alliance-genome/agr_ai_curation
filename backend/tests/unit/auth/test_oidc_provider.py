"""Unit tests for OIDC auth provider internals."""

import asyncio
import logging
from types import SimpleNamespace

import pytest
from jwt.exceptions import PyJWTError

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

    def _fake_decode(token, key, algorithms, audience, issuer, leeway, options):
        captured["token"] = token
        captured["key"] = key
        captured["algorithms"] = algorithms
        captured["audience"] = audience
        captured["issuer"] = issuer
        captured["leeway"] = leeway
        captured["options"] = options
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
    assert captured["leeway"] == 0
    assert captured["options"] is None


def test_validate_token_reraises_pyjwt_errors_without_error_log(monkeypatch, caplog):
    """The API boundary should classify surfaced PyJWT decode errors."""
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
        raise PyJWTError("bad token")

    monkeypatch.setattr(provider, "_discover_async", _discover_async)
    monkeypatch.setattr(provider, "_get_jwks_client", lambda: _FakeJwksClient())
    monkeypatch.setattr(oidc_module.asyncio, "to_thread", _direct_to_thread)
    monkeypatch.setattr(oidc_module.jwt, "decode", _failing_decode)
    caplog.set_level(logging.ERROR)

    with pytest.raises(PyJWTError, match="bad token"):
        asyncio.run(provider.validate_token("id-token"))

    assert not caplog.records


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
        def __init__(self, jwks_uri, **options):
            created["count"] += 1
            self.jwks_uri = jwks_uri
            self.options = options

    monkeypatch.setattr(provider, "_discover", lambda: {"jwks_uri": "https://issuer.example.org/jwks"})
    monkeypatch.setattr(oidc_module, "PyJWKClient", _FakePyJWKClient)

    first = provider._get_jwks_client()
    second = provider._get_jwks_client()

    assert created["count"] == 1
    assert first is second


def test_discovery_normalizes_trailing_slash_without_altering_validation_issuer(
    monkeypatch,
):
    provider = OIDCAuthProvider(
        {
            "issuer_url": "https://issuer.example.org/",
            "validation_issuer": "https://issuer.example.org/",
            "client_id": "oidc-client",
        }
    )
    captured = {}

    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"issuer": "https://issuer.example.org/"}

    def _get(url, *, timeout):
        captured["url"] = url
        captured["timeout"] = timeout
        return _Response()

    monkeypatch.setattr(oidc_module.httpx, "get", _get)

    assert provider._discover() == {"issuer": "https://issuer.example.org/"}
    assert captured["url"] == (
        "https://issuer.example.org/.well-known/openid-configuration"
    )
    assert provider.validation_issuer == "https://issuer.example.org/"


def test_benchmark_validation_options_configure_jwks_and_time_claims(monkeypatch):
    provider = OIDCAuthProvider(
        {
            "issuer_url": "https://issuer.example.org/",
            "validation_issuer": "https://issuer.example.org/",
            "client_id": "benchmark-api",
            "audience": "benchmark-api",
            "jwks_timeout_seconds": 5,
            "jwks_cache_ttl_seconds": 300,
            "clock_skew_seconds": 60,
            "required_claims": ("exp", "iat", "sub"),
        }
    )
    captured = {}

    class _FakePyJWKClient:
        def __init__(self, jwks_uri, **options):
            captured["jwks_uri"] = jwks_uri
            captured["jwks_options"] = options

        def get_signing_key_from_jwt(self, _token):
            return SimpleNamespace(key="key")

    async def _discover_async():
        return {
            "issuer": "https://untrusted-discovery-value.example.org",
            "jwks_uri": "https://issuer.example.org/jwks",
        }

    async def _direct_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    def _decode(*_args, **kwargs):
        captured["decode"] = kwargs
        return {"sub": "service"}

    monkeypatch.setattr(provider, "_discover", lambda: {"jwks_uri": "https://issuer.example.org/jwks"})
    monkeypatch.setattr(provider, "_discover_async", _discover_async)
    monkeypatch.setattr(oidc_module, "PyJWKClient", _FakePyJWKClient)
    monkeypatch.setattr(oidc_module.asyncio, "to_thread", _direct_to_thread)
    monkeypatch.setattr(oidc_module.jwt, "decode", _decode)

    assert asyncio.run(provider.validate_token("access-token")) == {"sub": "service"}
    assert captured["jwks_options"] == {"timeout": 5, "lifespan": 300}
    assert captured["decode"]["audience"] == "benchmark-api"
    assert captured["decode"]["issuer"] == "https://issuer.example.org/"
    assert captured["decode"]["leeway"] == 60
    assert captured["decode"]["options"] == {"require": ["exp", "iat", "sub"]}
