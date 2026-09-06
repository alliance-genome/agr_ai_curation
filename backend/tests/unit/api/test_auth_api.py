"""Unit tests for auth API helper behavior."""

import importlib
import logging
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.security import SecurityScopes
from jwt.exceptions import (
    DecodeError,
    ExpiredSignatureError,
    InvalidAudienceError,
    InvalidSignatureError,
    PyJWKClientConnectionError,
    PyJWKClientError,
)

auth_api = importlib.import_module("src.api.auth")
http_errors = importlib.import_module("src.lib.http_errors")


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
    assert "iss" not in result


@pytest.mark.asyncio
async def test_get_user_from_cookie_dev_mode(monkeypatch):
    monkeypatch.delenv("TESTING_API_KEY", raising=False)
    monkeypatch.setattr(auth_api, "is_dev_mode", lambda: True)

    result = await auth_api._get_user_from_cookie_impl(_request(), SecurityScopes())
    assert result["sub"] == "dev-user-123"
    assert "developers" in result["groups"]
    assert "iss" not in result


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
@pytest.mark.parametrize("issuer", ["https://identity.example.org/pool", None])
async def test_get_user_from_cookie_provider_success(monkeypatch, issuer):
    monkeypatch.delenv("TESTING_API_KEY", raising=False)
    monkeypatch.setattr(auth_api, "is_dev_mode", lambda: False)
    monkeypatch.setattr(auth_api, "is_auth_configured", lambda: True)

    class _Provider:
        async def validate_token(self, _token):
            return {"iss": issuer, "private_claim": "not-for-user-context"}

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
        _request(
            cookies={"auth_token": "jwt-123"},
            headers={"X-Curation-Benchmark-Sender-Issuer": "https://spoof.invalid"},
        ),
        SecurityScopes(),
    )
    assert result["sub"] == "user-123"
    assert result["provider"] == "cognito"
    assert "devs" in result["groups"]
    assert result["iss"] == issuer
    assert "private_claim" not in result


@pytest.mark.parametrize(
    ("token_error", "expected_reason"),
    [
        (ExpiredSignatureError("expired-secret-token"), "expired"),
        (DecodeError("malformed-secret-token"), "malformed"),
        (InvalidSignatureError("signature-secret-token"), "invalid_signature"),
        (InvalidAudienceError("claims-secret-token"), "invalid_claims"),
        (
            PyJWKClientError(
                'Unable to find a signing key that matches: "unknown-key-secret-token"'
            ),
            "unknown_signing_key",
        ),
    ],
)
@pytest.mark.asyncio
async def test_get_user_from_cookie_rejects_expected_token_failures_once(
    monkeypatch,
    caplog,
    token_error,
    expected_reason,
):
    monkeypatch.delenv("TESTING_API_KEY", raising=False)
    monkeypatch.setattr(auth_api, "is_dev_mode", lambda: False)
    monkeypatch.setattr(auth_api, "is_auth_configured", lambda: True)

    class _Provider:
        async def validate_token(self, _token):
            raise token_error

        def extract_principal(self, _claims):
            raise AssertionError("should not be called")

    monkeypatch.setattr(auth_api, "_get_provider_or_503", lambda: _Provider())
    reported = []
    monkeypatch.setattr(
        http_errors,
        "report_runtime_exception",
        lambda exc, **_kwargs: reported.append(exc) or True,
    )
    caplog.set_level(logging.INFO, logger=auth_api.logger.name)

    with pytest.raises(HTTPException) as exc:
        await auth_api._get_user_from_cookie_impl(
            _request(cookies={"auth_token": "cookie-secret-token"}),
            SecurityScopes(),
        )
    assert exc.value.status_code == 401
    assert exc.value.detail == "Invalid authentication token"

    rejection_records = [
        record for record in caplog.records if record.message == "Authentication token rejected"
    ]
    assert len(rejection_records) == 1
    assert rejection_records[0].reason == expected_reason
    assert not [record for record in caplog.records if record.levelno >= logging.ERROR]
    assert reported == []
    assert "secret-token" not in caplog.text


@pytest.mark.parametrize(
    "provider_error",
    [
        PyJWKClientConnectionError("JWKS connection unavailable"),
        PyJWKClientError("The JWKS endpoint did not return a JSON object"),
        PyJWKClientError("The JWKS endpoint did not contain any signing keys"),
        RuntimeError("unexpected provider failure"),
    ],
)
@pytest.mark.asyncio
async def test_get_user_from_cookie_reports_unexpected_provider_failures(
    monkeypatch,
    caplog,
    provider_error,
):
    monkeypatch.delenv("TESTING_API_KEY", raising=False)
    monkeypatch.setattr(auth_api, "is_dev_mode", lambda: False)
    monkeypatch.setattr(auth_api, "is_auth_configured", lambda: True)

    class _Provider:
        async def validate_token(self, _token):
            raise provider_error

        def extract_principal(self, _claims):
            raise AssertionError("should not be called")

    reported = []
    monkeypatch.setattr(auth_api, "_get_provider_or_503", lambda: _Provider())
    monkeypatch.setattr(
        http_errors,
        "report_runtime_exception",
        lambda exc, **_kwargs: reported.append(exc) or True,
    )
    caplog.set_level(logging.ERROR, logger=auth_api.logger.name)

    with pytest.raises(HTTPException) as exc:
        await auth_api._get_user_from_cookie_impl(
            _request(cookies={"auth_token": "cookie-secret-token"}),
            SecurityScopes(),
        )

    assert exc.value.status_code == 503
    assert exc.value.detail == "Authentication provider unavailable"
    assert reported == [provider_error]
    assert len([record for record in caplog.records if record.levelno >= logging.ERROR]) == 1
    assert "cookie-secret-token" not in caplog.text


def test_build_logout_redirect_uri_prefers_provider_redirect_uri():
    provider = SimpleNamespace(redirect_uri="https://login.example.org/callback")
    req = _request(base_url="https://app.example.org/")
    assert auth_api._build_logout_redirect_uri(req, provider) == "https://login.example.org/"


def test_build_logout_redirect_uri_falls_back_to_base_url():
    provider = SimpleNamespace(redirect_uri=None)
    req = _request(base_url="https://app.example.org/")
    assert auth_api._build_logout_redirect_uri(req, provider) == "https://app.example.org/"
