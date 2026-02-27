"""Unit tests for auth API endpoint handlers and edge paths."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Response
from fastapi.security import SecurityScopes

from src.api import auth as auth_api
from src.auth.base import TokenSet


def _request(headers=None, cookies=None, base_url="https://app.example.org/"):
    return SimpleNamespace(headers=headers or {}, cookies=cookies or {}, base_url=base_url)


@pytest.fixture(autouse=True)
def _reset_auth_provider_state():
    original_provider = auth_api._provider
    original_provider_error = auth_api._provider_error
    original_provider_failed = auth_api._provider_failed

    auth_api._provider = None
    auth_api._provider_error = None
    auth_api._provider_failed = False
    try:
        yield
    finally:
        auth_api._provider = original_provider
        auth_api._provider_error = original_provider_error
        auth_api._provider_failed = original_provider_failed


def test_get_provider_or_503_caches_success(monkeypatch):
    provider = SimpleNamespace(provider_name="oidc")
    calls = {"count": 0}

    def _factory():
        calls["count"] += 1
        return provider

    monkeypatch.setattr(auth_api, "create_auth_provider", _factory)
    first = auth_api._get_provider_or_503()
    second = auth_api._get_provider_or_503()
    assert first is provider
    assert second is provider
    assert calls["count"] == 1


@pytest.mark.asyncio
async def test_login_requires_configured_auth(monkeypatch):
    monkeypatch.setattr(auth_api, "is_auth_configured", lambda: False)
    with pytest.raises(HTTPException) as exc:
        await auth_api.login(_request())
    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_login_handles_provider_url_generation_failure(monkeypatch):
    class _Provider:
        def get_login_url(self, *_args, **_kwargs):
            raise RuntimeError("provider down")

    async def _direct_threadpool(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(auth_api, "is_auth_configured", lambda: True)
    monkeypatch.setattr(auth_api, "_get_provider_or_503", lambda: _Provider())
    monkeypatch.setattr(auth_api, "run_in_threadpool", _direct_threadpool)

    with pytest.raises(HTTPException) as exc:
        await auth_api.login(_request())
    assert exc.value.status_code == 503
    assert "provider unavailable" in exc.value.detail.lower()


@pytest.mark.asyncio
async def test_login_sets_pkce_and_state_cookies(monkeypatch):
    class _Provider:
        def get_login_url(self, state, code_challenge, method):
            assert state
            assert code_challenge
            assert method == "S256"
            return "https://issuer.example.org/authorize"

    async def _direct_threadpool(func, *args, **kwargs):
        return func(*args, **kwargs)

    generated = iter(["verifier-1", "state-1"])
    monkeypatch.setattr(auth_api, "is_auth_configured", lambda: True)
    monkeypatch.setattr(auth_api, "_get_provider_or_503", lambda: _Provider())
    monkeypatch.setattr(auth_api, "run_in_threadpool", _direct_threadpool)
    monkeypatch.setattr(auth_api.secrets, "token_urlsafe", lambda _n: next(generated))
    monkeypatch.setattr(auth_api, "get_secure_cookies", lambda: False)

    response = await auth_api.login(_request())
    assert response.status_code == 302
    assert str(response.headers["location"]) == "https://issuer.example.org/authorize"
    set_cookie_headers = response.headers.getlist("set-cookie")
    assert any(header.startswith("oauth_state=") for header in set_cookie_headers)
    assert any(header.startswith("oauth_code_verifier=") for header in set_cookie_headers)


@pytest.mark.asyncio
async def test_callback_redirects_to_login_when_oauth_state_missing():
    response = await auth_api.callback(
        request=_request(cookies={}),
        response=Response(),
        code="code",
        state="state",
        db=object(),
    )
    assert response.status_code == 302
    assert str(response.headers["location"]) == "/api/auth/login"


@pytest.mark.asyncio
async def test_callback_redirects_to_login_on_state_mismatch():
    response = await auth_api.callback(
        request=_request(cookies={"oauth_state": "wrong", "oauth_code_verifier": "verifier"}),
        response=Response(),
        code="code",
        state="state",
        db=object(),
    )
    assert response.status_code == 302
    assert str(response.headers["location"]) == "/api/auth/login"


@pytest.mark.asyncio
async def test_callback_redirects_when_code_verifier_missing():
    response = await auth_api.callback(
        request=_request(cookies={"oauth_state": "state"}),
        response=Response(),
        code="code",
        state="state",
        db=object(),
    )
    assert response.status_code == 302
    assert str(response.headers["location"]) == "/api/auth/login"


@pytest.mark.asyncio
async def test_callback_success_sets_auth_cookie_and_clears_pkce(monkeypatch):
    class _Provider:
        async def handle_callback(self, _code, _verifier):
            return TokenSet(id_token="jwt-token")

        async def validate_token(self, _token):
            return {"sub": "user-123"}

        def extract_principal(self, _claims):
            return SimpleNamespace(subject="user-123", email="u@example.org", display_name="User", groups=[])

    monkeypatch.setattr(auth_api, "_get_provider_or_503", lambda: _Provider())
    monkeypatch.setattr(auth_api, "provision_user", lambda _db, principal: SimpleNamespace(auth_sub=principal.subject))
    monkeypatch.setattr(auth_api, "get_secure_cookies", lambda: False)

    response = await auth_api.callback(
        request=_request(cookies={"oauth_state": "state", "oauth_code_verifier": "verifier"}),
        response=Response(),
        code="code",
        state="state",
        db=object(),
    )
    assert response.status_code == 302
    assert str(response.headers["location"]) == "/"
    set_cookie_headers = response.headers.getlist("set-cookie")
    assert any(header.startswith("auth_token=jwt-token") for header in set_cookie_headers)
    assert any(header.startswith("oauth_state=") for header in set_cookie_headers)
    assert any(header.startswith("oauth_code_verifier=") for header in set_cookie_headers)


@pytest.mark.asyncio
async def test_callback_raises_for_provider_failure(monkeypatch):
    class _Provider:
        async def handle_callback(self, _code, _verifier):
            raise RuntimeError("bad exchange")

        async def validate_token(self, _token):
            return {}

        def extract_principal(self, _claims):
            return SimpleNamespace(subject="unused")

    monkeypatch.setattr(auth_api, "_get_provider_or_503", lambda: _Provider())

    with pytest.raises(HTTPException) as exc:
        await auth_api.callback(
            request=_request(cookies={"oauth_state": "state", "oauth_code_verifier": "verifier"}),
            response=Response(),
            code="code",
            state="state",
            db=object(),
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_callback_raises_when_principal_subject_missing(monkeypatch):
    class _Provider:
        async def handle_callback(self, _code, _verifier):
            return TokenSet(id_token="jwt-token")

        async def validate_token(self, _token):
            return {"sub": "missing"}

        def extract_principal(self, _claims):
            return SimpleNamespace(subject="")

    monkeypatch.setattr(auth_api, "_get_provider_or_503", lambda: _Provider())

    with pytest.raises(HTTPException) as exc:
        await auth_api.callback(
            request=_request(cookies={"oauth_state": "state", "oauth_code_verifier": "verifier"}),
            response=Response(),
            code="code",
            state="state",
            db=object(),
        )
    assert exc.value.status_code == 400


def test_build_mock_user_attribute_error():
    user = auth_api._build_mock_user({"sub": "abc"})
    assert user.sub == "abc"
    with pytest.raises(AttributeError):
        _ = user.missing


def test_with_group_claim_aliases_includes_cognito_fallback(monkeypatch):
    monkeypatch.setattr(auth_api, "get_group_claim_key", lambda: "custom:groups")
    payload = auth_api._with_group_claim_aliases({"sub": "u"}, ["devs"])
    assert payload["custom:groups"] == ["devs"]
    assert payload["groups"] == ["devs"]
    assert payload["cognito:groups"] == ["devs"]


@pytest.mark.asyncio
async def test_get_user_from_cookie_impl_dev_mode_unknown_group_fallback(monkeypatch):
    monkeypatch.delenv("TESTING_API_KEY", raising=False)
    monkeypatch.setattr(auth_api, "is_dev_mode", lambda: True)
    monkeypatch.setenv("DEV_USER_GROUPS", "WB")
    monkeypatch.setattr(auth_api, "get_group", lambda _id: None)

    user = await auth_api._get_user_from_cookie_impl(_request(), SecurityScopes())
    assert "wb-curators" in user["groups"]


@pytest.mark.asyncio
async def test_get_user_from_cookie_impl_rejects_principal_missing_subject(monkeypatch):
    class _Provider:
        async def validate_token(self, _token):
            return {"ok": True}

        def extract_principal(self, _claims):
            return SimpleNamespace(subject="", email="u@example.org", display_name="User", provider="oidc", groups=[])

    monkeypatch.delenv("TESTING_API_KEY", raising=False)
    monkeypatch.setattr(auth_api, "is_dev_mode", lambda: False)
    monkeypatch.setattr(auth_api, "is_auth_configured", lambda: True)
    monkeypatch.setattr(auth_api, "_get_provider_or_503", lambda: _Provider())

    with pytest.raises(HTTPException) as exc:
        await auth_api._get_user_from_cookie_impl(_request(cookies={"auth_token": "jwt"}), SecurityScopes())
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_logout_requires_user():
    with pytest.raises(HTTPException) as exc:
        await auth_api.logout(request=_request(), response=Response(), user=None)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_logout_success_clears_cookies_and_returns_provider_logout_url(monkeypatch):
    class _Provider:
        redirect_uri = "https://issuer.example.org/callback"

        def get_logout_url(self, redirect_uri):
            return f"https://issuer.example.org/logout?redirect={redirect_uri}"

    async def _direct_threadpool(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(auth_api, "get_secure_cookies", lambda: False)
    monkeypatch.setattr(auth_api, "_get_provider_or_503", lambda: _Provider())
    monkeypatch.setattr(auth_api, "run_in_threadpool", _direct_threadpool)

    response = Response()
    result = await auth_api.logout(
        request=_request(base_url="https://app.example.org/"),
        response=response,
        user={"sub": "user-123"},
    )
    assert result["status"] == "logged_out"
    assert "issuer.example.org/logout" in result["logout_url"]
    set_cookie_headers = response.headers.getlist("set-cookie")
    assert any(header.startswith("auth_token=") for header in set_cookie_headers)
    assert any(header.startswith("cognito_token=") for header in set_cookie_headers)


def test_auth_compat_get_user_property_returns_impl():
    assert auth_api.auth.get_user is auth_api._get_user_from_cookie_impl
