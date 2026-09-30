"""Shared auth factory selection, configuration failures, and dev gate."""

import pytest

from src.auth import factory as auth_factory
from auth_runtime.dev import DevAuthProvider
from auth_runtime import oidc


def test_create_auth_provider_returns_dev_provider_when_dev_mode(monkeypatch):
    monkeypatch.setattr(auth_factory, "is_dev_mode", lambda: True)
    assert isinstance(auth_factory.create_auth_provider(), DevAuthProvider)


def test_create_auth_provider_raises_for_cognito_when_not_configured(monkeypatch):
    monkeypatch.setattr(auth_factory, "is_dev_mode", lambda: False)
    monkeypatch.setenv("AUTH_PROVIDER", "cognito")
    monkeypatch.delenv("COGNITO_USER_POOL_ID", raising=False)
    with pytest.raises(ValueError, match="not fully configured"):
        auth_factory.create_auth_provider()


def test_create_auth_provider_raises_for_unknown_provider(monkeypatch):
    monkeypatch.setattr(auth_factory, "is_dev_mode", lambda: False)
    monkeypatch.setenv("AUTH_PROVIDER", "mystery")
    with pytest.raises(ValueError, match="Invalid AUTH_PROVIDER"):
        auth_factory.create_auth_provider()


@pytest.mark.parametrize("kind", ["oidc", "cognito"])
@pytest.mark.parametrize("raw_limit", ["91", "invalid"])
def test_backend_getters_control_provider_network_limits(monkeypatch, kind, raw_limit):
    monkeypatch.setattr(auth_factory, "is_dev_mode", lambda: False)
    monkeypatch.setenv("AUTH_PROVIDER", kind)
    for key, value in {
        "OIDC_ISSUER_URL": "https://issuer.example.org",
        "OIDC_CLIENT_ID": "client",
        "OIDC_REDIRECT_URI": "https://app.example.org/callback",
        "COGNITO_REGION": "us-east-1",
        "COGNITO_USER_POOL_ID": "example-pool",
        "COGNITO_CLIENT_ID": "client",
        "COGNITO_DOMAIN": "https://issuer.example.org",
        "COGNITO_REDIRECT_URI": "https://app.example.org/callback",
    }.items():
        monkeypatch.setenv(key, value)
    for key in (
        "AUTH_PROVIDER_TIMEOUT_SECONDS",
        "AUTH_JWKS_TIMEOUT_SECONDS",
        "AUTH_JWKS_CACHE_TTL_SECONDS",
    ):
        monkeypatch.setenv(key, raw_limit)
    monkeypatch.setattr(auth_factory, "get_auth_provider_timeout_seconds", lambda: 7.5)
    monkeypatch.setattr(auth_factory, "get_auth_jwks_timeout_seconds", lambda: 8.5)
    monkeypatch.setattr(auth_factory, "get_auth_jwks_cache_ttl_seconds", lambda: 99)

    captured = {}

    class _DiscoveryResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"jwks_uri": "https://issuer.example.org/jwks"}

    def _get(url, *, timeout):
        captured["discovery_timeout"] = timeout
        return _DiscoveryResponse()

    def _jwks_client(url, **options):
        captured["jwks_options"] = options
        return object()

    monkeypatch.setattr(oidc.httpx, "get", _get)
    monkeypatch.setattr(oidc, "PyJWKClient", _jwks_client)
    provider = auth_factory.create_auth_provider()
    assert isinstance(provider, oidc.OIDCAuthProvider)
    provider._get_jwks_client()
    assert captured == {
        "discovery_timeout": 7.5,
        "jwks_options": {"timeout": 8.5, "lifespan": 99},
    }
