"""TraceReview provider parity and internal-service trust boundary tests."""

import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse
from unittest.mock import AsyncMock, Mock

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException, Response
from jwt.exceptions import PyJWKClientConnectionError

from auth_runtime.oidc import OIDCAuthProvider

from src.api import auth
from src.services import feedback_artifacts


def request(*, headers=None, cookies=None):
    return SimpleNamespace(headers=headers or {}, cookies=cookies or {})


@pytest.fixture(autouse=True)
def clean_auth(monkeypatch):
    auth._configured_provider.cache_clear()
    monkeypatch.setenv("DEV_MODE", "false")
    monkeypatch.setenv("SECURE_COOKIES", "true")
    monkeypatch.setenv("TRACE_REVIEW_INTERNAL_API_TOKEN", "service-token")
    yield
    auth._configured_provider.cache_clear()


@pytest.fixture(params=["oidc", "cognito"])
def provider(request, monkeypatch):
    kind = request.param
    monkeypatch.setenv("AUTH_PROVIDER", kind)
    if kind == "oidc":
        issuer = "https://issuer.example.org"
        monkeypatch.setenv("OIDC_ISSUER_URL", issuer)
        monkeypatch.setenv("OIDC_CLIENT_ID", "client")
        monkeypatch.setenv(
            "OIDC_REDIRECT_URI", "https://review.example.org/api/auth/callback"
        )
        monkeypatch.setenv("OIDC_GROUP_CLAIM", "realm_access.roles")
        monkeypatch.delenv("OIDC_LOGOUT_URL", raising=False)
        monkeypatch.delenv("OIDC_LOGOUT_REDIRECT_PARAM", raising=False)
    else:
        issuer = "https://cognito-idp.us-east-1.amazonaws.com/pool"
        for key, value in {
            "REGION": "us-east-1",
            "USER_POOL_ID": "pool",
            "CLIENT_ID": "client",
            "CLIENT_SECRET": "secret",
            "DOMAIN": "https://cognito.example.org",
            "REDIRECT_URI": "https://review.example.org/api/auth/callback",
        }.items():
            monkeypatch.setenv(f"COGNITO_{key}", value)
    p = auth._configured_provider()
    assert isinstance(p, OIDCAuthProvider)
    p._discovery = {
        "issuer": issuer,
        "authorization_endpoint": f"{issuer}/authorize",
        "token_endpoint": f"{issuer}/token",
        "jwks_uri": f"{issuer}/jwks",
        "end_session_endpoint": f"{issuer}/logout",
    }
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    claims = {
        "sub": "curator",
        "email": "curator@example.org",
        "name": "Curator",
        "iss": issuer,
        "aud": "client",
        "exp": int(time.time()) + 300,
    }
    claims.update(
        {"realm_access": {"roles": ["curators"]}}
        if kind == "oidc"
        else {"cognito:groups": ["curators"]}
    )
    token = jwt.encode(claims, key, algorithm="RS256", headers={"kid": "key-1"})
    monkeypatch.setattr(
        p,
        "_get_jwks_client",
        lambda: SimpleNamespace(
            get_signing_key_from_jwt=lambda token: SimpleNamespace(key=key.public_key())
        ),
    )
    return p, token, kind


def test_standalone_provider_reads_network_limits_from_environment(provider, monkeypatch):
    monkeypatch.setenv("AUTH_PROVIDER_TIMEOUT_SECONDS", "7.5")
    monkeypatch.setenv("AUTH_JWKS_TIMEOUT_SECONDS", "8.5")
    monkeypatch.setenv("AUTH_JWKS_CACHE_TTL_SECONDS", "99")
    auth._configured_provider.cache_clear()
    configured = auth._configured_provider()
    assert isinstance(configured, OIDCAuthProvider)
    assert configured.timeout_seconds == 7.5
    assert configured.jwks_timeout_seconds == 8.5
    assert configured.jwks_cache_ttl_seconds == 99


@pytest.mark.asyncio
async def test_browser_provider_login_callback_cookie_and_logout(provider, monkeypatch):
    p, token, kind = provider
    monkeypatch.setenv("AUTH_OAUTH_COOKIE_MAX_AGE_SECONDS", "123")
    monkeypatch.setenv("AUTH_SESSION_COOKIE_MAX_AGE_SECONDS", "456")
    redirect = await auth.login(request())
    params = parse_qs(urlparse(redirect.headers["location"]).query)
    assert params["redirect_uri"] == [p.redirect_uri]
    assert params["code_challenge_method"] == ["S256"]
    cookies = [
        value.decode() for key, value in redirect.raw_headers if key == b"set-cookie"
    ]
    assert all(
        "HttpOnly" in value and "Secure" in value and "Max-Age=123" in value
        for value in cookies
    )
    state = params["state"][0]
    verifier = cookies[1].split(";", 1)[0].split("=", 1)[1]
    exchange = AsyncMock(return_value=SimpleNamespace(id_token=token))
    monkeypatch.setattr(p, "handle_callback", exchange)
    result = await auth.callback(
        request(cookies={"oauth_state": state, "oauth_code_verifier": verifier}),
        Response(),
        "auth-code",
        state,
    )
    exchange.assert_awaited_once_with("auth-code", verifier)
    session = [
        value.decode() for key, value in result.raw_headers if key == b"set-cookie"
    ]
    assert any(
        value.startswith("auth_token=")
        and "HttpOnly" in value
        and "Secure" in value
        and "Max-Age=456" in value
        for value in session
    )
    user = await auth._get_user_from_cookie_impl(request(cookies={"auth_token": token}))
    assert user["sub"] == "curator"
    assert user["groups"] == ["curators"]
    assert (await auth.get_current_user(user))["user"]["groups"] == ["curators"]
    logout = await auth.logout(request())
    assert (
        "logout_uri" if kind == "cognito" else "post_logout_redirect_uri"
    ) in parse_qs(urlparse(logout.headers["location"]).query)
    assert "Max-Age=0" in logout.headers["set-cookie"]


@pytest.mark.asyncio
async def test_provider_rejects_bad_signature_and_legacy_cookie(provider):
    _, token, _ = provider
    for cookies in ({"auth_token": token[:-10] + "bad"}, {"cognito_token": token}):
        with pytest.raises(HTTPException) as exc:
            await auth._get_user_from_cookie_impl(request(cookies=cookies))
        assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_provider_outage_is_unavailable(provider, monkeypatch):
    p, token, _ = provider
    monkeypatch.setattr(
        p,
        "validate_token",
        AsyncMock(side_effect=PyJWKClientConnectionError("private provider response")),
    )
    with pytest.raises(HTTPException) as exc:
        await auth._get_user_from_cookie_impl(request(cookies={"auth_token": token}))
    assert exc.value.status_code == 503
    assert "private" not in exc.value.detail


@pytest.mark.asyncio
async def test_invalid_callback_state_does_not_exchange(provider, monkeypatch):
    p, _, _ = provider
    exchange = AsyncMock()
    monkeypatch.setattr(p, "handle_callback", exchange)
    with pytest.raises(HTTPException) as exc:
        await auth.callback(
            request(cookies={"oauth_state": "stored"}), Response(), "code", "wrong"
        )
    assert exc.value.status_code == 403
    exchange.assert_not_called()


@pytest.mark.asyncio
async def test_dev_requires_explicit_gate(monkeypatch):
    monkeypatch.setenv("AUTH_PROVIDER", "dev")
    with pytest.raises(HTTPException) as exc:
        await auth._get_user_from_cookie_impl(request())
    assert exc.value.status_code == 503
    monkeypatch.setenv("DEV_MODE", "true")
    user = await auth._get_user_from_cookie_impl(request())
    assert user["provider"] == "dev"
    assert user["groups"] == ["developers"]


@pytest.mark.asyncio
async def test_incomplete_oidc_does_not_enable_dev(monkeypatch):
    monkeypatch.setenv("AUTH_PROVIDER", "oidc")
    monkeypatch.delenv("OIDC_ISSUER_URL", raising=False)
    with pytest.raises(HTTPException) as exc:
        await auth.login(request())
    assert exc.value.status_code == 503


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["oidc", "cognito", "dev"])
async def test_internal_service_auth_precedes_provider_initialization(
    kind, monkeypatch
):
    monkeypatch.setenv("AUTH_PROVIDER", kind)
    monkeypatch.setattr(
        auth,
        "_configured_provider",
        Mock(
            side_effect=AssertionError(
                "service caller must not initialize browser auth"
            )
        ),
    )
    user = await auth._get_user_from_cookie_impl(
        request(
            headers={
                "authorization": "Bearer service-token",
                "x-trusted-caller-sub": "curator",
                "x-trusted-caller-email": "curator@example.org",
            }
        )
    )
    assert user["token_use"] == "internal_service"
    assert user["trusted_caller_sub"] == "curator"
    assert user["trusted_caller_email"] == "curator@example.org"


@pytest.mark.asyncio
async def test_wrong_service_token_cannot_use_dev_or_cookie(monkeypatch):
    monkeypatch.setenv("DEV_MODE", "true")
    with pytest.raises(HTTPException) as exc:
        await auth._get_user_from_cookie_impl(
            request(headers={"authorization": "Bearer wrong-token"})
        )
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_old_caller_headers_are_ignored():
    user = await auth._get_user_from_cookie_impl(
        request(
            headers={
                "authorization": "Bearer service-token",
                "x-agr-trusted-caller-sub": "forged",
                "x-agr-trusted-caller-email": "forged@example.org",
            }
        )
    )
    assert "trusted_caller_sub" not in user
    assert "trusted_caller_email" not in user


def test_feedback_artifacts_forward_only_canonical_identity(monkeypatch):
    monkeypatch.setenv("AI_CURATION_BACKEND_URL", "http://backend:8000")
    get = Mock(return_value=SimpleNamespace(status_code=404))
    monkeypatch.setattr(feedback_artifacts.requests, "get", get)
    feedback_artifacts.fetch_feedback_trace_artifacts(
        "feedback", caller_sub="curator", caller_email="curator@example.org"
    )
    assert get.call_args.kwargs["headers"] == {
        "Authorization": "Bearer service-token",
        "X-Trusted-Caller-Sub": "curator",
        "X-Trusted-Caller-Email": "curator@example.org",
    }


@pytest.mark.asyncio
async def test_missing_subject_never_creates_browser_session(provider, monkeypatch):
    p, token, _ = provider
    monkeypatch.setattr(
        p, "handle_callback", AsyncMock(return_value=SimpleNamespace(id_token=token))
    )
    monkeypatch.setattr(
        p, "validate_token", AsyncMock(return_value={"email": "curator@example.org"})
    )
    with pytest.raises(HTTPException) as exc:
        await auth.callback(
            request(
                cookies={"oauth_state": "state", "oauth_code_verifier": "verifier"}
            ),
            Response(),
            "code",
            "state",
        )
    assert exc.value.status_code == 401
