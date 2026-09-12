"""Explicit onboarding; no credentials, SQL or providers outside test doubles."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from jwt.exceptions import InvalidAudienceError
from sqlalchemy.exc import IntegrityError
from src.api import benchmark_curator, benchmark_gate, benchmark_onboarding
from src.auth.base import AuthPrincipal, CurrentPrincipalDenied
from src.lib.benchmarks import curator_onboarding as service
from src.models.sql.user import User


@pytest.fixture
def boundary(monkeypatch):
    principal = AuthPrincipal(
        subject="onboarding-curator",
        provider="oidc",
        email="curator@example.invalid",
        display_name="Curator",
        groups=["curators"],
        raw_claims={
            "iss": "https://identity.example.invalid",
            "cognito:username": "curator",
        },
    )
    provider = Mock()
    provider.validate_token = AsyncMock(return_value={})
    provider.extract_principal.return_value = principal
    monkeypatch.setattr(
        benchmark_curator.browser_auth, "_get_provider_or_503", lambda: provider
    )
    monkeypatch.setattr(
        service, "get_groups_from_provider_groups", lambda groups: groups
    )
    current = Mock(return_value=replace(principal, email=None, display_name=None))
    monkeypatch.setattr(service, "get_current_principal_resolver", lambda: current)
    monkeypatch.setattr(
        service, "get_benchmark_environment_id", lambda: "test-executor"
    )
    session = MagicMock()
    session.scalar.return_value = None
    factory = MagicMock()
    factory.return_value.__enter__.return_value = session
    provision = Mock(
        return_value=User(id=1, auth_sub=principal.subject, is_active=True)
    )
    monkeypatch.setattr(service, "provision_user", provision)
    monkeypatch.setattr(
        benchmark_onboarding,
        "onboard_benchmark_curator",
        lambda human: service.onboard_benchmark_curator(human, session_factory=factory),
    )
    monkeypatch.setattr(benchmark_gate, "get_benchmark_api_enabled", lambda: True)
    app = FastAPI()
    app.include_router(benchmark_onboarding.router)
    app.dependency_overrides[benchmark_onboarding.require_benchmark_read] = lambda: {
        "sub": "service:portal",
        "client_id": "portal",
        "token_use": "access",
    }
    return SimpleNamespace(
        principal=principal,
        current=current,
        provider=provider,
        session=session,
        factory=factory,
        provision=provision,
        app=app,
        client=TestClient(app),
    )


def post(boundary):
    return boundary.client.post(
        "/api/v1/benchmarks/curator/onboard",
        headers={
            "X-Benchmark-Curator-Authorization": "Bearer synthetic-human",
        },
    )


def test_onboarding_acknowledges_only_verified_identity_after_current_check(boundary):
    def provision(session, principal):
        boundary.current.assert_called_once()
        assert principal.email == "curator@example.invalid"
        assert principal.display_name == "Curator"
        return User(id=1, auth_sub=principal.subject, is_active=True)

    boundary.provision.side_effect = provision
    response = post(boundary)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "schema_version": 1,
        "subject": boundary.principal.subject,
        "issuer": "https://identity.example.invalid",
        "environment_id": "test-executor",
    }


@pytest.mark.parametrize(
    "failure,status",
    [(CurrentPrincipalDenied("private"), 403), (RuntimeError("private"), 503)],
)
def test_current_provider_failure_never_provisions_or_reflects_details(
    boundary, failure, status
):
    boundary.current.side_effect = failure
    response = post(boundary)
    assert response.status_code == status
    assert "private" not in response.text and "synthetic-human" not in response.text
    boundary.factory.assert_not_called()
    boundary.provision.assert_not_called()


@pytest.mark.parametrize(
    "change", ["subject", "issuer", "username", "provider", "groups"]
)
def test_current_identity_or_revoked_group_denied_before_sql(boundary, change):
    current = replace(
        boundary.principal, raw_claims=dict(boundary.principal.raw_claims)
    )
    if change == "subject":
        current.subject = "other"
    elif change == "issuer":
        current.raw_claims["iss"] = "https://other.invalid"
    elif change == "username":
        current.raw_claims["cognito:username"] = "other"
    elif change == "provider":
        current.provider = "other"
    else:
        current.groups = []
    boundary.current.return_value = current
    assert post(boundary).status_code == 403
    boundary.factory.assert_not_called()


def test_inactive_user_is_not_updated_or_provisioned(boundary):
    boundary.session.scalar.return_value = User(
        id=1, auth_sub=boundary.principal.subject, is_active=False
    )
    assert post(boundary).status_code == 403
    boundary.provision.assert_not_called()


def test_wrong_audience_never_reaches_current_lookup(boundary):
    boundary.provider.validate_token.side_effect = InvalidAudienceError(
        "synthetic-human"
    )
    assert post(boundary).status_code == 401
    boundary.current.assert_not_called()


def test_api_gate_and_read_capability_precede_onboarding(boundary, monkeypatch):
    monkeypatch.setattr(benchmark_gate, "get_benchmark_api_enabled", lambda: False)
    assert post(boundary).status_code == 404
    monkeypatch.setattr(benchmark_gate, "get_benchmark_api_enabled", lambda: True)

    def denied():
        raise HTTPException(403, "Read capability required")

    boundary.app.dependency_overrides[benchmark_onboarding.require_benchmark_read] = (
        denied
    )
    assert post(boundary).status_code == 403
    boundary.provider.validate_token.assert_not_called()


@pytest.mark.parametrize(
    "constraint,code,active,status",
    [
        ("uq_users_auth_sub", "23505", True, 200),
        ("uq_users_auth_sub", "23505", False, 403),
        ("unrelated_constraint", "23505", True, 503),
        ("uq_users_auth_sub", "23514", True, 503),
    ],
)
def test_only_expected_subject_race_can_reconcile(
    boundary, constraint, code, active, status
):
    original = Exception("private-db-detail")
    original.pgcode = code
    original.diag = SimpleNamespace(constraint_name=constraint, table_name="users")
    error = IntegrityError("private-sql", {}, original)
    user = User(id=1, auth_sub=boundary.principal.subject, is_active=active)
    boundary.session.scalar.side_effect = [None, user]
    boundary.provision.side_effect = [error, user]
    response = post(boundary)
    assert response.status_code == status
    assert "private" not in response.text
    assert boundary.provision.call_count == (2 if status == 200 else 1)
