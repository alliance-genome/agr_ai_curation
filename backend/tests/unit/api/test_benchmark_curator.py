"""Human admission never treats orchestration/source authority as identity."""

from unittest.mock import AsyncMock, MagicMock, Mock

import pytest
from fastapi import HTTPException
from jwt.exceptions import InvalidAudienceError
from starlette.requests import Request

from src.api import benchmark_curator as admission
from src.auth.base import AuthPrincipal
from src.models.sql.user import User
from src.lib.security.redaction import REDACTED, redact_secrets


def request(cookie=None, source=None):
    headers = []
    if cookie:
        headers.append((b"cookie", f"auth_token={cookie}".encode()))
    if source:
        headers.append((b"x-benchmark-delegated-source-authorization", source.encode()))
    return Request({"type": "http", "headers": headers})


@pytest.fixture
def boundary(monkeypatch):
    provider = MagicMock()
    provider.validate_token = AsyncMock(return_value={"sensitive": "never-persist"})
    provider.extract_principal.return_value = AuthPrincipal(
        subject="curator", provider="oidc", groups=[],
        raw_claims={"iss": "https://synthetic.invalid", "cognito:username": "curator-name"},
    )
    monkeypatch.setattr(admission.browser_auth, "_get_provider_or_503", lambda: provider)
    factory = MagicMock()
    factory.return_value.__enter__.return_value.scalar.return_value = User(
        id=42, auth_sub="curator", is_active=True,
    )
    monkeypatch.setattr(admission, "SessionLocal", factory)
    current = AsyncMock(side_effect=lambda context: context)
    monkeypatch.setattr(admission, "authorize_benchmark_curator", current)
    return provider, factory, current


@pytest.mark.asyncio
@pytest.mark.parametrize("cookie,header,owner", [
    ("human-cookie", None, {"sub": "curator", "client_id": "benchmark_browser_session"}),
    (None, "Bearer human-token", {"sub": "service:portal", "client_id": "portal", "token_use": "access"}),
])
async def test_verified_cookie_or_separate_human_token_returns_only_receipt(boundary, cookie, header, owner):
    provider, factory, current = boundary
    result = await admission.require_benchmark_curator(request(cookie), owner, header)
    assert result.subject == "curator"
    assert result.auth_issuer == "https://synthetic.invalid"
    assert result.provider_username == "curator-name"
    assert result.db_user_id == 42
    assert "sensitive" not in result.model_dump_json()
    provider.validate_token.assert_awaited_once_with(cookie or "human-token")
    current.assert_awaited_once_with(result)
    factory.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("cookie,header,source", [
    (None, None, None), (None, None, "Bearer abc-only"),
    (None, "Bearer ", None), (None, "Bearer token extra", None),
    (None, "Basic token", None), ("cookie", "Bearer token", None),
])
async def test_missing_malformed_or_ambiguous_human_credentials_fail_before_provider(boundary, cookie, header, source):
    with pytest.raises(HTTPException) as error:
        await admission.require_benchmark_curator(
            request(cookie, source), {"sub": "service:portal", "client_id": "portal"}, header,
        )
    assert error.value.status_code == 401
    boundary[0].validate_token.assert_not_awaited()
    boundary[1].assert_not_called()


@pytest.mark.asyncio
async def test_wrong_audience_is_not_weakened_or_reflected(boundary):
    boundary[0].validate_token.side_effect = InvalidAudienceError("sensitive-token")
    with pytest.raises(HTTPException) as error:
        await admission.require_benchmark_curator(request("cookie"), {}, None)
    assert error.value.status_code == 401
    assert "sensitive" not in str(error.value.detail)
    boundary[1].assert_not_called()


@pytest.mark.asyncio
async def test_browser_owner_cannot_choose_a_different_human(boundary):
    with pytest.raises(HTTPException) as error:
        await admission.require_benchmark_curator(request("cookie"), {
            "sub": "another-curator", "client_id": "benchmark_browser_session",
        }, None)
    assert error.value.status_code == 403
    boundary[1].assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("owner", [
    {"sub": "another-curator", "client_id": "cli", "token_use": "bearer"},
    {"sub": "service:portal", "client_id": "portal", "token_use": "bearer"},
    {"sub": "service:other", "client_id": "portal", "token_use": "access"},
])
async def test_only_verified_m2m_shape_allows_distinct_human_subject(boundary, owner):
    with pytest.raises(HTTPException) as error:
        await admission.require_benchmark_curator(request(), owner, "Bearer human-token")
    assert error.value.status_code == 403
    boundary[1].assert_not_called()


@pytest.mark.asyncio
async def test_current_authorization_revocation_fails_closed(boundary):
    boundary[2].side_effect = PermissionError("private-provider-details")
    with pytest.raises(HTTPException) as error:
        await admission.require_benchmark_curator(request("cookie"), {"sub": "curator"}, None)
    assert error.value.status_code == 403
    assert "private" not in str(error.value.detail)


@pytest.mark.asyncio
async def test_human_credential_limit_is_configurable(boundary, monkeypatch):
    monkeypatch.setenv("BENCHMARK_CURATOR_AUTH_MAX_BYTES", "3")
    with pytest.raises(HTTPException) as error:
        await admission.require_benchmark_curator(request("long-cookie"), {}, None)
    assert error.value.status_code == 401
    boundary[0].validate_token.assert_not_awaited()


@pytest.mark.asyncio
async def test_provider_boundary_scrubs_bare_credentials(boundary):
    async def validate(token):
        assert redact_secrets({"message": f"failed credential {token}"}) == {
            "message": f"failed credential {REDACTED}",
        }
        return {}
    boundary[0].validate_token.side_effect = validate
    await admission.require_benchmark_curator(request("opaque-human-value"), {"sub": "curator"}, None)
    assert redact_secrets("opaque-human-value") == "opaque-human-value"


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["token", "account"])
async def test_unavailable_authority_reports_only_sanitized_failure(boundary, monkeypatch, caplog, stage):
    reporter = Mock(return_value=True)
    monkeypatch.setattr("src.lib.http_errors.report_runtime_exception", reporter)
    raw = RuntimeError("private-paper-content opaque-human-value sql-parameters")
    if stage == "token":
        boundary[0].validate_token.side_effect = raw
    else:
        boundary[2].side_effect = raw
    with pytest.raises(HTTPException) as error:
        await admission.require_benchmark_curator(request("opaque-human-value"), {"sub": "curator"}, None)
    assert error.value.status_code == 503
    assert error.value.detail == {
        "code": "curator_authorization_unavailable",
        "message": "Verified current AI Curation curator authorization required",
    }
    reporter.assert_called_once()
    captured = reporter.call_args.args[0]
    assert captured.__traceback__ is not None
    assert captured.__context__ is None and captured.__cause__ is None
    assert "RuntimeError" in str(captured)
    for sensitive in ("private-paper-content", "opaque-human-value", "sql-parameters"):
        assert sensitive not in str(captured) + str(error.value.detail) + caplog.text


@pytest.mark.asyncio
async def test_authorization_denial_does_not_report_server_failure(boundary, monkeypatch):
    reporter = Mock()
    monkeypatch.setattr("src.lib.http_errors.report_runtime_exception", reporter)
    boundary[2].side_effect = PermissionError("revoked")
    with pytest.raises(HTTPException) as error:
        await admission.require_benchmark_curator(request("cookie"), {"sub": "curator"}, None)
    assert error.value.status_code == 403
    reporter.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure,expected", [(None, 200), ("audience", 401), ("subject", 403), ("revoked", 403)])
async def test_read_wrapper_preserves_current_human_verification(boundary, failure, expected):
    owner = {"sub": "service:portal", "client_id": "portal", "token_use": "access"}
    if failure == "audience":
        boundary[0].validate_token.side_effect = InvalidAudienceError("private-token")
    elif failure == "subject":
        owner = {"sub": "another-human", "client_id": "cli", "token_use": "bearer"}
    elif failure == "revoked":
        boundary[2].side_effect = PermissionError("private-provider-detail")
    if expected == 200:
        receipt = await admission.require_benchmark_read_curator(request(), owner, "Bearer human-token")
        assert receipt.subject == "curator" and receipt.db_user_id == 42
    else:
        with pytest.raises(HTTPException) as error:
            await admission.require_benchmark_read_curator(request(), owner, "Bearer human-token")
        assert error.value.status_code == expected
        assert "private" not in str(error.value.detail)
