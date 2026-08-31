"""Focused tests for scoped benchmark cookie and bearer authorization."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from jwt.exceptions import (
    ExpiredSignatureError,
    InvalidAudienceError,
    InvalidIssuerError,
    InvalidSignatureError,
)

from src.api import benchmark_auth


def _request(*, authorization: str = "", api_key: str = ""):
    headers = {"authorization": authorization} if authorization else {}
    if api_key:
        headers["X-API-Key"] = api_key
    return SimpleNamespace(headers=headers, cookies={})


@pytest.fixture(autouse=True)
def _reset_provider():
    benchmark_auth.reset_benchmark_auth_cache()
    yield
    benchmark_auth.reset_benchmark_auth_cache()


@pytest.mark.parametrize(
    ("capability", "dependency", "scope"),
    [
        (benchmark_auth.BENCHMARK_READ, benchmark_auth.require_benchmark_read, "portal.read"),
        (benchmark_auth.BENCHMARK_RUN, benchmark_auth.require_benchmark_run, "portal.run"),
        (benchmark_auth.BENCHMARK_CANCEL, benchmark_auth.require_benchmark_cancel, "portal.cancel"),
        (benchmark_auth.BENCHMARK_DELETE, benchmark_auth.require_benchmark_delete, "portal.delete"),
        (
            benchmark_auth.BENCHMARK_SOURCE_READ,
            benchmark_auth.require_benchmark_source_read,
            "portal.source.read",
        ),
    ],
)
@pytest.mark.asyncio
async def test_bearer_service_principal_receives_only_required_capability(
    monkeypatch, capability, dependency, scope
):
    class Provider:
        async def validate_token(self, token):
            assert token == "signed-token"
            return {
                "sub": "portal-service",
                "client_id": "portal-client",
                "scope": f"unrelated {scope}",
            }

    monkeypatch.setattr(benchmark_auth, "_get_benchmark_provider", Provider)
    monkeypatch.setattr(
        benchmark_auth,
        "get_benchmark_oidc_allowed_client_ids",
        lambda: ("portal-client",),
    )
    monkeypatch.setattr(
        benchmark_auth,
        "get_benchmark_oidc_capability_scopes",
        lambda requested: (scope,) if requested == capability else (),
    )

    principal = await dependency(_request(authorization="Bearer signed-token"))
    assert principal == {
        "sub": "portal-service",
        "client_id": "portal-client",
        "token_use": "bearer",
        "benchmark_capabilities": [capability],
    }


@pytest.mark.asyncio
async def test_cookie_operator_group_grants_only_explicit_capability(monkeypatch):
    async def _browser_user(_request, _scopes):
        return {"sub": "human-operator", "groups": ["benchmark-readers"]}

    monkeypatch.setattr(benchmark_auth.browser_auth, "_get_user_from_cookie_impl", _browser_user)
    monkeypatch.setattr(
        benchmark_auth,
        "get_benchmark_operator_capability_groups",
        lambda capability: ("benchmark-readers",)
        if capability == benchmark_auth.BENCHMARK_READ
        else ("benchmark-runners",),
    )

    principal = await benchmark_auth.require_benchmark_read(_request())
    assert principal["benchmark_capabilities"] == [benchmark_auth.BENCHMARK_READ]

    with pytest.raises(HTTPException) as exc_info:
        await benchmark_auth.require_benchmark_run(_request())
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_ordinary_curator_cookie_cannot_execute_or_read_sources(monkeypatch):
    async def _curator(_request, _scopes):
        return {"sub": "curator", "groups": ["WB_curators"]}

    monkeypatch.setattr(benchmark_auth.browser_auth, "_get_user_from_cookie_impl", _curator)
    monkeypatch.setattr(
        benchmark_auth,
        "get_benchmark_operator_capability_groups",
        lambda _capability: ("benchmark-operators",),
    )

    for dependency in (
        benchmark_auth.require_benchmark_run,
        benchmark_auth.require_benchmark_source_read,
    ):
        with pytest.raises(HTTPException) as exc_info:
            await dependency(_request())
        assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_missing_authentication_remains_401(monkeypatch):
    async def _unauthenticated(_request, _scopes):
        raise HTTPException(status_code=401, detail="Not authenticated")

    monkeypatch.setattr(
        benchmark_auth.browser_auth,
        "_get_user_from_cookie_impl",
        _unauthenticated,
    )
    with pytest.raises(HTTPException) as exc_info:
        await benchmark_auth.require_benchmark_read(_request())
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_testing_api_key_cannot_grant_benchmark_capability(monkeypatch):
    monkeypatch.setenv("TESTING_API_KEY", "testing-secret")
    monkeypatch.setenv("TESTING_API_KEY_GROUPS", "benchmark-readers")
    monkeypatch.setattr(
        benchmark_auth,
        "get_benchmark_operator_capability_groups",
        lambda _capability: ("benchmark-readers",),
    )

    with pytest.raises(HTTPException) as exc_info:
        await benchmark_auth.require_benchmark_read(
            _request(api_key="testing-secret")
        )

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Benchmark OIDC bearer token or browser session required"


@pytest.mark.parametrize(
    "token_error",
    [
        ExpiredSignatureError("secret-expired-token"),
        InvalidAudienceError("secret-wrong-audience-token"),
        InvalidIssuerError("secret-wrong-issuer-token"),
        InvalidSignatureError("secret-wrong-signature-token"),
    ],
)
@pytest.mark.asyncio
async def test_invalid_bearer_claims_fail_401_without_token_leak(
    monkeypatch, caplog, token_error
):
    class Provider:
        async def validate_token(self, _token):
            raise token_error

    monkeypatch.setattr(benchmark_auth, "_get_benchmark_provider", Provider)
    with pytest.raises(HTTPException) as exc_info:
        await benchmark_auth.require_benchmark_read(
            _request(authorization="Bearer header-secret-token")
        )
    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Invalid benchmark access token"
    assert "secret" not in caplog.text


@pytest.mark.asyncio
async def test_wrong_client_identity_is_401(monkeypatch):
    class Provider:
        async def validate_token(self, _token):
            return {
                "sub": "unapproved-service",
                "client_id": "unapproved-client",
                "scope": "portal.read",
            }

    monkeypatch.setattr(benchmark_auth, "_get_benchmark_provider", Provider)
    monkeypatch.setattr(
        benchmark_auth,
        "get_benchmark_oidc_allowed_client_ids",
        lambda: ("portal-client",),
    )
    with pytest.raises(HTTPException) as exc_info:
        await benchmark_auth.require_benchmark_read(
            _request(authorization="Bearer signed-token")
        )
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_valid_bearer_without_required_scope_is_403(monkeypatch):
    class Provider:
        async def validate_token(self, _token):
            return {
                "sub": "portal-service",
                "azp": "portal-client",
                "scope": "portal.read",
            }

    monkeypatch.setattr(benchmark_auth, "_get_benchmark_provider", Provider)
    monkeypatch.setattr(
        benchmark_auth,
        "get_benchmark_oidc_allowed_client_ids",
        lambda: ("portal-client",),
    )
    monkeypatch.setattr(
        benchmark_auth,
        "get_benchmark_oidc_capability_scopes",
        lambda _capability: ("portal.run",),
    )
    with pytest.raises(HTTPException) as exc_info:
        await benchmark_auth.require_benchmark_run(
            _request(authorization="Bearer signed-token")
        )
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_bearer_header_never_falls_back_to_cookie(monkeypatch):
    async def _browser_user(_request, _scopes):
        pytest.fail("bearer requests must not fall back to browser authentication")

    monkeypatch.setattr(benchmark_auth.browser_auth, "_get_user_from_cookie_impl", _browser_user)
    with pytest.raises(HTTPException) as exc_info:
        await benchmark_auth.require_benchmark_read(
            _request(authorization="Basic opaque-secret")
        )
    assert exc_info.value.status_code == 401


def test_provider_requires_complete_bearer_configuration(monkeypatch):
    monkeypatch.setattr(benchmark_auth, "get_benchmark_oidc_issuer_url", lambda: "")
    monkeypatch.setattr(benchmark_auth, "get_benchmark_oidc_audience", lambda: "")
    monkeypatch.setattr(benchmark_auth, "get_benchmark_oidc_allowed_client_ids", tuple)
    with pytest.raises(HTTPException) as exc_info:
        benchmark_auth._get_benchmark_provider()
    assert exc_info.value.status_code == 503
