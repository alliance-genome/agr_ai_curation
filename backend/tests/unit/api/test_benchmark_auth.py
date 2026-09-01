"""Focused tests for scoped benchmark cookie and bearer authorization."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from jwt.exceptions import (
    ExpiredSignatureError,
    ImmatureSignatureError,
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
def _reset_provider(monkeypatch):
    monkeypatch.delenv("BENCHMARK_OIDC_COGNITO_M2M_ENABLED", raising=False)
    monkeypatch.delenv("BENCHMARK_OIDC_COGNITO_M2M_CLIENT_ID", raising=False)
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
    assert (
        principal["client_id"]
        == benchmark_auth.BENCHMARK_BROWSER_SESSION_CLIENT_ID
    )
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
async def test_conflicting_allowlisted_client_identities_are_401(monkeypatch):
    class Provider:
        async def validate_token(self, _token):
            return {
                "sub": "portal-service",
                "client_id": "portal-client",
                "azp": "operator-client",
                "scope": "portal.read",
            }

    monkeypatch.setattr(benchmark_auth, "_get_benchmark_provider", Provider)
    monkeypatch.setattr(
        benchmark_auth,
        "get_benchmark_oidc_allowed_client_ids",
        lambda: ("portal-client", "operator-client"),
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


def _configure_cognito_m2m(monkeypatch, claims):
    class Provider:
        async def validate_token(self, token):
            assert token == "signed-cognito-token"
            return claims

    monkeypatch.setattr(
        benchmark_auth, "get_benchmark_oidc_cognito_m2m_enabled", lambda: True
    )
    monkeypatch.setattr(
        benchmark_auth,
        "get_benchmark_oidc_cognito_m2m_client_id",
        lambda: "machine-client",
    )
    monkeypatch.setattr(
        benchmark_auth, "get_benchmark_oidc_audience", lambda: "benchmark-resource"
    )
    monkeypatch.setattr(benchmark_auth, "_get_cognito_m2m_provider", Provider)
    monkeypatch.setattr(
        benchmark_auth,
        "get_benchmark_oidc_capability_scopes",
        lambda capability: ("benchmark-resource/read",)
        if capability == benchmark_auth.BENCHMARK_READ
        else (),
    )


@pytest.mark.parametrize("audience", [pytest.param(None, id="no-aud"), "benchmark-resource"])
@pytest.mark.parametrize(
    "identity_claims",
    [
        pytest.param({}, id="no-azp"),
        pytest.param({"azp": "machine-client"}, id="matching-azp"),
    ],
)
@pytest.mark.asyncio
async def test_cognito_m2m_without_subject_uses_machine_principal(
    monkeypatch, audience, identity_claims
):
    claims = {
        "iss": "https://cognito-idp.us-east-1.amazonaws.com/example-pool",
        "client_id": "machine-client",
        "token_use": "access",
        "scope": "benchmark-resource/read benchmark-resource/run",
        "iat": 1_700_000_000,
        "exp": 1_700_003_600,
        **identity_claims,
    }
    if audience is not None:
        claims["aud"] = audience
    _configure_cognito_m2m(monkeypatch, claims)

    principal = await benchmark_auth.require_benchmark_read(
        _request(authorization="Bearer signed-cognito-token")
    )

    assert principal == {
        "sub": "service:machine-client",
        "client_id": "machine-client",
        "token_use": "access",
        "benchmark_capabilities": [benchmark_auth.BENCHMARK_READ],
    }


@pytest.mark.parametrize(
    "claim_updates",
    [
        pytest.param({"token_use": "id"}, id="id-token"),
        pytest.param({"token_use": "refresh"}, id="wrong-token-use"),
        pytest.param({"client_id": "browser-client"}, id="browser-client"),
        pytest.param({"client_id": "unknown-client"}, id="unknown-client"),
        pytest.param({"client_id": None}, id="missing-client"),
        pytest.param({"azp": "conflicting-client"}, id="conflicting-client"),
        pytest.param({"azp": ""}, id="empty-authorized-party"),
        pytest.param({"azp": None}, id="null-authorized-party"),
        pytest.param({"azp": 7}, id="numeric-authorized-party"),
        pytest.param({"azp": []}, id="list-authorized-party"),
        pytest.param({"azp": {}}, id="object-authorized-party"),
        pytest.param({"aud": "wrong-resource"}, id="wrong-optional-audience"),
        pytest.param({"aud": ["benchmark-resource"]}, id="malformed-audience"),
        pytest.param({"scope": ["benchmark-resource/read"]}, id="malformed-scope"),
    ],
)
@pytest.mark.asyncio
async def test_cognito_m2m_invalid_claim_matrix_fails_401(monkeypatch, claim_updates):
    claims = {
        "iss": "https://cognito-idp.us-east-1.amazonaws.com/example-pool",
        "client_id": "machine-client",
        "token_use": "access",
        "scope": "benchmark-resource/read",
        "iat": 1_700_000_000,
        "exp": 1_700_003_600,
        **claim_updates,
    }
    _configure_cognito_m2m(monkeypatch, claims)

    with pytest.raises(HTTPException) as exc_info:
        await benchmark_auth.require_benchmark_read(
            _request(authorization="Bearer signed-cognito-token")
        )

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Invalid benchmark access token"


@pytest.mark.parametrize(
    "scope",
    [pytest.param(None, id="missing"), pytest.param("benchmark-resource/run", id="wrong")],
)
@pytest.mark.asyncio
async def test_cognito_m2m_missing_or_wrong_scope_fails_403(monkeypatch, scope):
    claims = {
        "iss": "https://cognito-idp.us-east-1.amazonaws.com/example-pool",
        "client_id": "machine-client",
        "token_use": "access",
        "iat": 1_700_000_000,
        "exp": 1_700_003_600,
    }
    if scope is not None:
        claims["scope"] = scope
    _configure_cognito_m2m(monkeypatch, claims)

    with pytest.raises(HTTPException) as exc_info:
        await benchmark_auth.require_benchmark_read(
            _request(authorization="Bearer signed-cognito-token")
        )

    assert exc_info.value.status_code == 403


@pytest.mark.parametrize(
    "token_error",
    [
        InvalidIssuerError("private wrong issuer"),
        InvalidSignatureError("private bad signature"),
        ExpiredSignatureError("private expired token"),
        ImmatureSignatureError("private future iat"),
    ],
)
@pytest.mark.asyncio
async def test_cognito_m2m_signature_issuer_and_time_fail_sanitized(
    monkeypatch, caplog, token_error
):
    class Provider:
        async def validate_token(self, _token):
            raise token_error

    monkeypatch.setattr(
        benchmark_auth, "get_benchmark_oidc_cognito_m2m_enabled", lambda: True
    )
    monkeypatch.setattr(benchmark_auth, "_get_cognito_m2m_provider", Provider)

    with pytest.raises(HTTPException) as exc_info:
        await benchmark_auth.require_benchmark_read(
            _request(authorization="Bearer private-token-value")
        )

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Invalid benchmark access token"
    assert "private" not in caplog.text


def test_cognito_m2m_provider_requires_explicit_cognito_configuration(monkeypatch):
    monkeypatch.setattr(
        benchmark_auth, "get_benchmark_oidc_issuer_url", lambda: "https://issuer.example.org"
    )
    monkeypatch.setattr(
        benchmark_auth,
        "get_benchmark_oidc_cognito_m2m_client_id",
        lambda: "machine-client",
    )

    with pytest.raises(HTTPException) as exc_info:
        benchmark_auth._get_cognito_m2m_provider()

    assert exc_info.value.status_code == 503


def test_cognito_m2m_provider_uses_exact_issuer_and_time_contract(monkeypatch):
    issuer = "https://cognito-idp.us-east-1.amazonaws.com/example-pool"
    monkeypatch.setattr(benchmark_auth, "get_benchmark_oidc_issuer_url", lambda: issuer)
    monkeypatch.setattr(
        benchmark_auth, "get_benchmark_oidc_audience", lambda: "benchmark-resource"
    )
    monkeypatch.setattr(
        benchmark_auth,
        "get_benchmark_oidc_cognito_m2m_client_id",
        lambda: "machine-client",
    )
    monkeypatch.setattr(
        benchmark_auth, "get_benchmark_oidc_jwks_timeout_seconds", lambda: 4
    )
    monkeypatch.setattr(
        benchmark_auth, "get_benchmark_oidc_jwks_cache_ttl_seconds", lambda: 120
    )
    monkeypatch.setattr(
        benchmark_auth, "get_benchmark_oidc_clock_skew_seconds", lambda: 30
    )

    provider = benchmark_auth._get_cognito_m2m_provider()

    assert provider.validation_issuer == issuer
    assert provider.audience == "benchmark-resource"
    assert provider.required_claims == ["exp", "iat", "token_use", "client_id"]
    assert provider.verify_audience is False
    assert provider.jwks_timeout_seconds == 4
    assert provider.jwks_cache_ttl_seconds == 120
    assert provider.clock_skew_seconds == 30
