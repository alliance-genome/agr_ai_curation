"""Unit tests for auth API helper behavior."""

import importlib
import sys
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.security import SecurityScopes

sys.modules.setdefault(
    "rapidfuzz",
    SimpleNamespace(
        fuzz=SimpleNamespace(
            partial_ratio_alignment=lambda *_args, **_kwargs: SimpleNamespace(
                dest_start=0,
                dest_end=0,
                score=0.0,
            )
        )
    ),
)

auth_api = importlib.import_module("src.api.auth")


def _request(headers=None, cookies=None, base_url="https://app.example.org/"):
    return SimpleNamespace(headers=headers or {}, cookies=cookies or {}, base_url=base_url)


@pytest.mark.asyncio
async def test_get_user_from_cookie_api_key_bypass(monkeypatch):
    monkeypatch.setenv("TESTING_API_KEY", "key-123")
    monkeypatch.setenv("TESTING_API_KEY_USER", "bot")
    monkeypatch.setenv("TESTING_API_KEY_EMAIL", "bot@example.org")
    monkeypatch.setenv("TESTING_API_KEY_GROUPS", "developers,WB_curators")

    monkeypatch.setattr(auth_api, "is_dev_mode", lambda: False)
    monkeypatch.setattr(auth_api, "is_auth_configured", lambda: True)

    result = await auth_api._get_user_from_cookie_impl(
        _request(headers={"X-API-Key": "key-123"}),
        SecurityScopes(),
    )
    assert result["sub"] == "api-key-bot"
    assert result["email"] == "bot@example.org"
    assert "developers" in result["groups"]


@pytest.mark.asyncio
async def test_get_user_from_cookie_dev_mode(monkeypatch):
    monkeypatch.delenv("TESTING_API_KEY", raising=False)
    monkeypatch.setattr(auth_api, "is_dev_mode", lambda: True)

    result = await auth_api._get_user_from_cookie_impl(_request(), SecurityScopes())
    assert result["sub"] == "dev-user-123"
    assert "developers" in result["groups"]


@pytest.mark.asyncio
async def test_get_user_from_cookie_rejects_when_auth_not_configured(monkeypatch):
    monkeypatch.delenv("TESTING_API_KEY", raising=False)
    monkeypatch.setattr(auth_api, "is_dev_mode", lambda: False)
    monkeypatch.setattr(auth_api, "is_auth_configured", lambda: False)

    with pytest.raises(HTTPException) as exc:
        await auth_api._get_user_from_cookie_impl(_request(), SecurityScopes())

    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_get_user_from_cookie_requires_cookie_when_configured(monkeypatch):
    monkeypatch.delenv("TESTING_API_KEY", raising=False)
    monkeypatch.setattr(auth_api, "is_dev_mode", lambda: False)
    monkeypatch.setattr(auth_api, "is_auth_configured", lambda: True)

    with pytest.raises(HTTPException) as exc:
        await auth_api._get_user_from_cookie_impl(_request(cookies={}), SecurityScopes())

    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_get_user_from_cookie_provider_success(monkeypatch):
    monkeypatch.delenv("TESTING_API_KEY", raising=False)
    monkeypatch.setattr(auth_api, "is_dev_mode", lambda: False)
    monkeypatch.setattr(auth_api, "is_auth_configured", lambda: True)

    class _Provider:
        async def validate_token(self, _token):
            return {"ok": True}

        def extract_principal(self, _claims):
            return SimpleNamespace(
                subject="user-123",
                email="u@example.org",
                display_name="User 123",
                provider="cognito",
                groups=["devs"],
            )

    monkeypatch.setattr(auth_api, "_get_provider_or_503", lambda: _Provider())

    result = await auth_api._get_user_from_cookie_impl(
        _request(cookies={"auth_token": "jwt-123"}),
        SecurityScopes(),
    )
    assert result["sub"] == "user-123"
    assert result["provider"] == "cognito"
    assert "devs" in result["groups"]


@pytest.mark.asyncio
async def test_get_user_from_cookie_provider_failure(monkeypatch):
    monkeypatch.delenv("TESTING_API_KEY", raising=False)
    monkeypatch.setattr(auth_api, "is_dev_mode", lambda: False)
    monkeypatch.setattr(auth_api, "is_auth_configured", lambda: True)

    class _Provider:
        async def validate_token(self, _token):
            raise RuntimeError("bad token")

        def extract_principal(self, _claims):
            raise AssertionError("should not be called")

    monkeypatch.setattr(auth_api, "_get_provider_or_503", lambda: _Provider())

    with pytest.raises(HTTPException) as exc:
        await auth_api._get_user_from_cookie_impl(
            _request(cookies={"auth_token": "bad"}),
            SecurityScopes(),
        )
    assert exc.value.status_code == 401


def test_build_logout_redirect_uri_prefers_provider_redirect_uri():
    provider = SimpleNamespace(redirect_uri="https://login.example.org/callback")
    req = _request(base_url="https://app.example.org/")
    assert auth_api._build_logout_redirect_uri(req, provider) == "https://login.example.org/"


def test_build_logout_redirect_uri_falls_back_to_base_url():
    provider = SimpleNamespace(redirect_uri=None)
    req = _request(base_url="https://app.example.org/")
    assert auth_api._build_logout_redirect_uri(req, provider) == "https://app.example.org/"
