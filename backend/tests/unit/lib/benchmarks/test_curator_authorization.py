from unittest.mock import MagicMock

import pytest

from src.lib.benchmarks import curator_authorization as authorization
from src.lib.benchmarks.execution_context import BenchmarkCuratorContext
from src.models.sql.user import User


ISSUER = "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_synthetic"


def context(**changes):
    return BenchmarkCuratorContext(**({
        "subject": "synthetic-sub", "auth_provider": "oidc", "db_user_id": 42,
        "auth_issuer": ISSUER, "provider_username": "Federation_synthetic",
        "active_groups": ("FB", "WB"),
    } | changes))


def client():
    result = MagicMock()
    result.admin_get_user.return_value = {
        "Enabled": True, "Username": "Federation_synthetic",
        "UserAttributes": [{"Name": "sub", "Value": "synthetic-sub"}],
    }
    result.admin_list_groups_for_user.side_effect = [
        {"Groups": [{"GroupName": "flybase-curators"}], "NextToken": "next"},
        {"Groups": [{"GroupName": "wormbase-curators"}, {"GroupName": "new-role"}]},
    ]
    return result


def lookup(frozen, sdk):
    return authorization._lookup_cognito_principal(
        frozen, client=sdk, pool_id="us-east-1_synthetic", issuer=ISSUER,
    )


def test_account_subject_and_all_group_pages_are_read_without_human_token():
    sdk = client()
    principal = lookup(context(), sdk)
    assert principal.subject == "synthetic-sub"
    assert principal.groups == ["flybase-curators", "wormbase-curators", "new-role"]
    sdk.admin_get_user.assert_called_once_with(
        UserPoolId="us-east-1_synthetic", Username="Federation_synthetic",
    )
    assert sdk.admin_list_groups_for_user.call_args.kwargs["NextToken"] == "next"
    assert set(principal.raw_claims) == {"iss", "cognito:username"}


@pytest.mark.parametrize("changes", [
    {"auth_provider": "dev"}, {"auth_issuer": "https://another-pool.invalid"},
    {"auth_issuer": None}, {"provider_username": None},
])
def test_provider_locator_mismatch_rejected_before_account_read(changes):
    sdk = client()
    with pytest.raises(PermissionError):
        lookup(context(**changes), sdk)
    sdk.admin_get_user.assert_not_called()


@pytest.mark.parametrize("changes", [
    {"Enabled": False}, {"Username": "different-user"},
    {"UserAttributes": []},
    {"UserAttributes": [{"Name": "sub", "Value": "different-sub"}]},
])
def test_disabled_or_mismatched_account_never_reads_groups(changes):
    sdk = client()
    sdk.admin_get_user.return_value.update(changes)
    with pytest.raises(PermissionError):
        lookup(context(), sdk)
    sdk.admin_list_groups_for_user.assert_not_called()


def test_repeated_pagination_token_fails_instead_of_accepting_partial_groups():
    sdk = client()
    sdk.admin_list_groups_for_user.side_effect = [
        {"Groups": [{"GroupName": "FB"}], "NextToken": "same"},
        {"Groups": [{"GroupName": "WB"}], "NextToken": "same"},
    ]
    with pytest.raises(PermissionError, match="pagination"):
        lookup(context(), sdk)


@pytest.mark.asyncio
@pytest.mark.parametrize("active,groups,allowed", [
    (True, ["flybase-curators", "wormbase-curators", "new-role"], True),
    (False, ["flybase-curators", "wormbase-curators"], False),
    (True, ["flybase-curators"], False),
])
async def test_live_provider_and_local_account_are_checked(monkeypatch, active, groups, allowed):
    frozen = context()
    principal = lookup(frozen, client())
    principal.groups = groups
    monkeypatch.setattr(authorization, "_configured_current_principal", lambda _: principal)
    factory = MagicMock()
    factory.return_value.__enter__.return_value.get.return_value = User(
        id=42, auth_sub=frozen.subject, is_active=active,
    )
    if allowed:
        assert await authorization.authorize_benchmark_curator(frozen, session_factory=factory) is frozen
    else:
        with pytest.raises(PermissionError):
            await authorization.authorize_benchmark_curator(frozen, session_factory=factory)


@pytest.mark.asyncio
async def test_lookup_failure_is_sanitized_and_cannot_reuse_frozen_claims(monkeypatch):
    def fail(_):
        raise RuntimeError("sensitive-provider-error")
    monkeypatch.setattr(authorization, "_configured_current_principal", fail)
    factory = MagicMock()
    with pytest.raises(PermissionError) as error:
        await authorization.authorize_benchmark_curator(context(), session_factory=factory)
    assert "sensitive" not in str(error.value)
    assert error.value.__suppress_context__ is True
    factory.assert_not_called()


def test_unsupported_provider_does_not_construct_aws_client(monkeypatch):
    monkeypatch.setattr(authorization, "is_dev_mode", lambda: False)
    monkeypatch.setattr(authorization, "get_auth_provider", lambda: "oidc")
    sdk_factory = MagicMock()
    monkeypatch.setattr(authorization.boto3, "client", sdk_factory)
    with pytest.raises(PermissionError):
        authorization._configured_current_principal(context())
    sdk_factory.assert_not_called()


def test_configured_lookup_uses_bounded_sdk_requests_and_closes_client(monkeypatch):
    monkeypatch.setattr(authorization, "is_dev_mode", lambda: False)
    monkeypatch.setattr(authorization, "get_auth_provider", lambda: "cognito")
    monkeypatch.setattr(authorization, "get_cognito_region", lambda: "us-east-1")
    monkeypatch.setattr(authorization, "get_cognito_user_pool_id", lambda: "us-east-1_synthetic")
    monkeypatch.setenv("BENCHMARK_CURATOR_AUTH_TIMEOUT_SECONDS", "2.5")
    monkeypatch.setenv("BENCHMARK_CURATOR_AUTH_MAX_ATTEMPTS", "3")
    sdk = client()
    sdk_factory = MagicMock(return_value=sdk)
    monkeypatch.setattr(authorization.boto3, "client", sdk_factory)
    assert authorization._configured_current_principal(context()).subject == "synthetic-sub"
    config = sdk_factory.call_args.kwargs["config"]
    assert config.connect_timeout == config.read_timeout == 2.5
    assert config.retries == {"mode": "standard", "total_max_attempts": 3}
    sdk.close.assert_called_once()
