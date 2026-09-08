import pytest
from pydantic import ValidationError

from src.auth.base import AuthPrincipal
from src.lib.benchmarks.execution_context import (
    BenchmarkCuratorContext,
    capture_curator_context,
    require_current_curator_authorization,
)
from src.models.sql.user import User


def principal(**changes):
    return AuthPrincipal(**({
        "subject": "curator-1", "provider": "oidc",
        "groups": ["wormbase-curators", "flybase-curators", "flybase-curators"],
        "email": "synthetic@example.invalid",
        "raw_claims": {"private_test_token": "NEVER_PERSIST_THIS_TOKEN"},
    } | changes))


def user(**changes):
    return User(**({"id": 42, "auth_sub": "curator-1", "is_active": True} | changes))


def test_receipt_is_immutable_canonical_and_contains_only_execution_identity():
    context = capture_curator_context(principal(), user=user())
    assert context.model_dump(mode="json") == {
        "schema_version": 1, "subject": "curator-1", "auth_provider": "oidc",
        "db_user_id": 42, "active_groups": ["FB", "WB"],
        "auth_issuer": None, "provider_username": None,
    }
    serialized = context.model_dump_json()
    assert "NEVER_PERSIST_THIS_TOKEN" not in serialized
    assert "synthetic@example.invalid" not in serialized
    assert BenchmarkCuratorContext.model_validate_json(serialized) == context
    with pytest.raises(ValidationError):
        context.subject = "another-curator"


@pytest.mark.parametrize("identity,account", [
    (principal(subject="service:portal"), user(auth_sub="service:portal")),
    (principal(provider="unknown"), user()),
    (principal(), user(is_active=False)),
    (principal(), user(auth_sub="another-curator")),
])
def test_capture_rejects_service_unverified_disabled_or_mismatched_identity(identity, account):
    with pytest.raises(PermissionError):
        capture_curator_context(identity, user=account)


@pytest.mark.parametrize("identity,account", [
    (None, user()),
    (principal(), None),
    (principal(groups=["flybase-curators"]), user()),
    (principal(subject="curator-2"), user(auth_sub="curator-2")),
    (principal(provider="other-provider"), user()),
    (principal(), user(is_active=False)),
    (principal(), user(id=43)),
])
def test_pre_execution_check_rejects_unavailable_revoked_or_changed_authorization(identity, account):
    frozen = capture_curator_context(principal(), user=user())
    with pytest.raises(PermissionError):
        require_current_curator_authorization(
            frozen, current_principal=identity, current_user=account
        )


def test_new_groups_do_not_change_an_accepted_experiment():
    frozen = capture_curator_context(principal(), user=user())
    checked = require_current_curator_authorization(
        frozen, current_principal=principal(groups=["flybase-curators", "wormbase-curators", "new-role"]),
        current_user=user(),
    )
    assert checked is frozen
    assert checked.active_groups == ("FB", "WB")


def test_capture_retains_only_provider_locator_from_verified_claims():
    claims = {
        "iss": "https://synthetic-issuer.invalid", "cognito:username": "Federation_user",
        "private_token": "never-store-this",
    }
    frozen = capture_curator_context(principal(raw_claims=claims), user=user())
    assert frozen.auth_issuer == claims["iss"]
    assert frozen.provider_username == claims["cognito:username"]
    assert "never-store-this" not in frozen.model_dump_json()
    with pytest.raises(PermissionError):
        require_current_curator_authorization(
            frozen, current_principal=principal(raw_claims={**claims, "iss": "https://other.invalid"}),
            current_user=user(),
        )
