import threading
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from src.auth import current_principal as resolvers
from src.auth.base import AuthPrincipal, CurrentPrincipalDenied, PrincipalLookupIdentity
from src.auth.providers import cognito_current_principal as cognito
from src.lib.benchmarks import curator_authorization as authorization
from src.lib.benchmarks.execution_context import BenchmarkCuratorContext
from src.lib.benchmarks.observability import BenchmarkOperationError
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
    return cognito._lookup_cognito_principal(
        PrincipalLookupIdentity(
            frozen.subject, frozen.auth_provider, frozen.auth_issuer, frozen.provider_username,
        ), client=sdk, pool_id="us-east-1_synthetic", issuer=ISSUER,
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
    {"UserAttributes": [{"Name": "sub", "Value": "different-sub"}]},
])
def test_disabled_or_mismatched_account_never_reads_groups(changes):
    sdk = client()
    sdk.admin_get_user.return_value.update(changes)
    with pytest.raises(PermissionError):
        lookup(context(), sdk)
    sdk.admin_list_groups_for_user.assert_not_called()


@pytest.mark.parametrize("changes", [
    {"Enabled": None}, {"Username": None}, {"UserAttributes": []},
    {"UserAttributes": [{"Name": "sub", "Value": None}]},
])
def test_malformed_account_is_an_operational_failure(changes):
    sdk = client()
    sdk.admin_get_user.return_value.update(changes)
    with pytest.raises(ValueError):
        lookup(context(), sdk)
    sdk.admin_list_groups_for_user.assert_not_called()


def test_repeated_pagination_token_fails_instead_of_accepting_partial_groups():
    sdk = client()
    sdk.admin_list_groups_for_user.side_effect = [
        {"Groups": [{"GroupName": "FB"}], "NextToken": "same"},
        {"Groups": [{"GroupName": "WB"}], "NextToken": "same"},
    ]
    with pytest.raises(ValueError, match="pagination"):
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
@pytest.mark.parametrize("error_type", [RuntimeError, TimeoutError, PermissionError])
async def test_lookup_failure_is_sanitized_and_cannot_reuse_frozen_claims(monkeypatch, error_type):
    def fail(_):
        raise error_type("sensitive-provider-error")
    monkeypatch.setattr(authorization, "_configured_current_principal", fail)
    factory = MagicMock()
    with pytest.raises(BenchmarkOperationError) as error:
        await authorization.authorize_benchmark_curator(context(), session_factory=factory)
    assert "sensitive" not in str(error.value)
    assert error.value.__suppress_context__ is True
    assert error.value.__context__ is None
    assert error.value.__cause__ is None
    factory.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("error_type", [RuntimeError, PermissionError])
async def test_database_failure_is_operational_and_has_no_raw_chain(monkeypatch, error_type):
    principal = lookup(context(), client())
    monkeypatch.setattr(authorization, "_configured_current_principal", lambda _: principal)
    factory = MagicMock()
    factory.return_value.__enter__.return_value.get.side_effect = error_type("sensitive-sql-parameters")
    with pytest.raises(BenchmarkOperationError) as error:
        await authorization.authorize_benchmark_curator(context(), session_factory=factory)
    assert "sensitive" not in str(error.value)
    assert error.value.__traceback__ is not None
    assert error.value.__context__ is None and error.value.__cause__ is None
    factory.return_value.__exit__.assert_called_once()


@pytest.mark.asyncio
async def test_authorization_session_stays_in_one_worker_thread(monkeypatch):
    event_loop_thread = threading.get_ident()
    worker_threads = []
    principal = lookup(context(), client())

    def record_thread():
        current = threading.get_ident()
        assert current != event_loop_thread
        worker_threads.append(current)

    def current_principal(_):
        record_thread()
        return principal

    def factory():
        record_thread()
        session = MagicMock()
        session.__enter__.side_effect = lambda: (record_thread(), session)[1]
        session.__exit__.side_effect = lambda *args: record_thread()
        session.get.side_effect = lambda *args: (
            record_thread(), User(id=42, auth_sub="synthetic-sub", is_active=True),
        )[1]
        return session

    monkeypatch.setattr(authorization, "_configured_current_principal", current_principal)
    frozen = context()
    assert await authorization.authorize_benchmark_curator(frozen, session_factory=factory) is frozen
    assert len(worker_threads) == 5
    assert len(set(worker_threads)) == 1


def test_missing_resolver_does_not_construct_aws_client(monkeypatch):
    monkeypatch.setattr(resolvers, "entry_points", lambda **_: ())
    monkeypatch.setattr(resolvers, "is_dev_mode", lambda: False)
    monkeypatch.setattr(resolvers, "get_auth_provider", lambda: "oidc")
    sdk_factory = MagicMock()
    monkeypatch.setattr(cognito.boto3, "client", sdk_factory)
    with pytest.raises(ValueError):
        authorization._configured_current_principal(context())
    sdk_factory.assert_not_called()


def test_configured_lookup_uses_bounded_sdk_requests_and_closes_client(monkeypatch):
    monkeypatch.setattr(resolvers, "entry_points", lambda **_: ())
    monkeypatch.setattr(resolvers, "is_dev_mode", lambda: False)
    monkeypatch.setattr(resolvers, "get_auth_provider", lambda: "cognito")
    monkeypatch.setattr(cognito, "get_cognito_region", lambda: "us-east-1")
    monkeypatch.setattr(cognito, "get_cognito_user_pool_id", lambda: "us-east-1_synthetic")
    monkeypatch.setenv("BENCHMARK_CURATOR_AUTH_TIMEOUT_SECONDS", "2.5")
    monkeypatch.setenv("BENCHMARK_CURATOR_AUTH_MAX_ATTEMPTS", "3")
    sdk = client()
    sdk_factory = MagicMock(return_value=sdk)
    monkeypatch.setattr(cognito.boto3, "client", sdk_factory)
    assert authorization._configured_current_principal(context()).subject == "synthetic-sub"
    config = sdk_factory.call_args.kwargs["config"]
    assert config.connect_timeout == config.read_timeout == 2.5
    assert config.retries == {"mode": "standard", "total_max_attempts": 3}
    sdk.close.assert_called_once()


def install_oidc_resolver(monkeypatch, resolver):
    monkeypatch.setattr(resolvers, "is_dev_mode", lambda: False)
    monkeypatch.setattr(resolvers, "get_auth_provider", lambda: "oidc")
    entry = MagicMock()
    entry.load.return_value = resolver

    def entries(**selection):
        assert selection == {
            "group": "agr_ai_curation.current_principal_resolvers", "name": "oidc",
        }
        return (entry,)

    monkeypatch.setattr(resolvers, "entry_points", entries)
    return entry


@pytest.mark.asyncio
@pytest.mark.parametrize("change,allowed", [
    ({}, True),
    ({"groups": ["flybase-curators"]}, False),
    ({"subject": "another-user"}, False),
    ({"provider": "another-provider"}, False),
    ({"raw_claims": {"iss": "https://wrong.invalid"}}, False),
])
async def test_registered_oidc_resolver_checks_live_membership(monkeypatch, change, allowed):
    issuer = "https://institution.invalid"
    frozen = context(auth_issuer=issuer, provider_username=None)
    principal = AuthPrincipal(**({
        "subject": frozen.subject, "provider": "oidc", "raw_claims": {"iss": issuer},
        "groups": ["flybase-curators", "wormbase-curators", "new-role"],
    } | change))
    resolver = MagicMock(return_value=principal)
    entry = install_oidc_resolver(monkeypatch, resolver)
    sdk_factory = MagicMock()
    monkeypatch.setattr(cognito.boto3, "client", sdk_factory)
    factory = MagicMock()
    factory.return_value.__enter__.return_value.get.return_value = User(
        id=42, auth_sub=frozen.subject, is_active=True,
    )
    if allowed:
        assert await authorization.authorize_benchmark_curator(frozen, session_factory=factory) is frozen
    else:
        with pytest.raises(PermissionError):
            await authorization.authorize_benchmark_curator(frozen, session_factory=factory)
    entry.load.assert_called_once_with()
    resolver.assert_called_once_with(PrincipalLookupIdentity(
        subject=frozen.subject, auth_provider="oidc", auth_issuer=issuer, provider_username=None,
    ))
    sdk_factory.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure,expected", [
    (CurrentPrincipalDenied("disabled account"), PermissionError),
    (PermissionError("sensitive directory credential"), BenchmarkOperationError),
    (TimeoutError("sensitive directory endpoint"), BenchmarkOperationError),
    (ValueError("incomplete memberships"), BenchmarkOperationError),
])
async def test_registered_resolver_preserves_failure_taxonomy(monkeypatch, failure, expected):
    install_oidc_resolver(monkeypatch, MagicMock(side_effect=failure))
    factory = MagicMock()
    with pytest.raises(expected) as error:
        await authorization.authorize_benchmark_curator(context(), session_factory=factory)
    assert error.value.__context__ is None and error.value.__cause__ is None
    assert "sensitive" not in str(error.value)
    factory.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("result", [None, {"subject": "synthetic-sub", "groups": ["FB", "WB"]}])
async def test_resolver_requires_canonical_principal(monkeypatch, result):
    install_oidc_resolver(monkeypatch, MagicMock(return_value=result))
    factory = MagicMock()
    with pytest.raises(BenchmarkOperationError):
        await authorization.authorize_benchmark_curator(context(), session_factory=factory)
    factory.assert_not_called()


@pytest.mark.parametrize("provider,count,dev", [
    ("oidc", 0, False), ("oidc", 2, False), ("cognito", 1, False),
    ("oidc", 1, True), ("dev", 1, False),
])
def test_unavailable_or_ambiguous_resolvers_fail_closed(monkeypatch, provider, count, dev):
    entry = install_oidc_resolver(monkeypatch, MagicMock())
    monkeypatch.setattr(resolvers, "get_auth_provider", lambda: provider)
    monkeypatch.setattr(resolvers, "is_dev_mode", lambda: dev)
    monkeypatch.setattr(resolvers, "entry_points", lambda **_: (entry,) * count)
    with pytest.raises(ValueError):
        resolvers.get_current_principal_resolver()
    entry.load.assert_not_called()


def test_non_callable_registration_is_rejected(monkeypatch):
    install_oidc_resolver(monkeypatch, object())
    with pytest.raises(TypeError):
        resolvers.get_current_principal_resolver()


@pytest.mark.parametrize("code,expected", [
    ("UserNotFoundException", CurrentPrincipalDenied),
    ("AccessDeniedException", ClientError),
])
def test_cognito_client_errors_preserve_taxonomy_and_close(monkeypatch, code, expected):

    monkeypatch.setattr(cognito, "get_cognito_region", lambda: "us-east-1")
    monkeypatch.setattr(cognito, "get_cognito_user_pool_id", lambda: "us-east-1_synthetic")
    sdk = client()
    sdk.admin_get_user.side_effect = ClientError({"Error": {"Code": code}}, "AdminGetUser")
    monkeypatch.setattr(cognito.boto3, "client", MagicMock(return_value=sdk))
    with pytest.raises(expected):
        cognito.resolve_current_principal(PrincipalLookupIdentity(
            "synthetic-sub", "oidc", ISSUER, "Federation_synthetic",
        ))
    sdk.close.assert_called_once()
