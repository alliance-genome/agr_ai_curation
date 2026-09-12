"""Real PostgreSQL first-login and concurrent explicit onboarding proof."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import Barrier
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import delete, event, func, select
from src.api import (
    benchmark_catalog,
    benchmark_curator,
    benchmark_gate,
    benchmark_onboarding,
)
from src.auth.base import AuthPrincipal, CurrentPrincipalDenied
from src.lib.benchmarks import curator_authorization, curator_onboarding
from src.models.sql.benchmark import BenchmarkJob
from src.models.sql.database import SessionLocal, engine
from src.models.sql.user import User
from src.services import user_service


@pytest.fixture(scope="module", autouse=True)
def migrated_database():
    command.upgrade(
        Config(str(Path(__file__).resolve().parents[3] / "alembic.ini")), "head"
    )


@pytest.fixture
def boundary(monkeypatch):
    monkeypatch.setenv(
        "BENCHMARK_ROOT",
        str(Path(__file__).resolve().parents[4] / "packages/alliance/benchmarks"),
    )
    principal = AuthPrincipal(
        subject=f"all1150-{uuid4()}",
        provider="oidc",
        email="curator@example.invalid",
        display_name="Verified curator",
        groups=[],
        raw_claims={
            "iss": "https://identity.example.invalid",
            "cognito:username": "curator",
        },
    )
    current = Mock(return_value=replace(principal, email=None, display_name=None))
    monkeypatch.setattr(
        curator_onboarding, "get_current_principal_resolver", lambda: current
    )
    monkeypatch.setattr(
        curator_authorization, "get_current_principal_resolver", lambda: current
    )
    provider = Mock()
    provider.validate_token = AsyncMock(return_value={})
    provider.extract_principal.return_value = principal
    monkeypatch.setattr(
        benchmark_curator.browser_auth, "_get_provider_or_503", lambda: provider
    )
    monkeypatch.setattr(benchmark_gate, "get_benchmark_api_enabled", lambda: True)
    tenants = Mock(
        return_value=False
    )  # Normal best-effort tenant failure is not a new strict gate.
    monkeypatch.setattr(user_service, "provision_weaviate_tenants", tenants)
    app = FastAPI()
    app.include_router(benchmark_onboarding.router)
    app.include_router(benchmark_catalog.router)
    app.dependency_overrides[benchmark_onboarding.require_benchmark_read] = lambda: {
        "sub": "service:portal",
        "client_id": "portal",
        "token_use": "access",
    }
    with SessionLocal() as session:
        initial_jobs = session.scalar(select(func.count()).select_from(BenchmarkJob))
    yield app, principal, current, tenants
    with SessionLocal() as session:
        assert (
            session.scalar(select(func.count()).select_from(BenchmarkJob))
            == initial_jobs
        )
        session.execute(delete(User).where(User.auth_sub == principal.subject))
        session.commit()


def request(app, *, onboard=True):
    with TestClient(app) as client:
        method = client.post if onboard else client.get
        path = (
            "/api/v1/benchmarks/curator/onboard"
            if onboard
            else "/api/v1/benchmarks/catalog"
        )
        return method(
            path,
            headers={"X-Benchmark-Curator-Authorization": "Bearer synthetic-human"},
        )


def test_explicit_onboarding_then_readonly_catalog(boundary):
    app, principal, current, tenants = boundary
    assert request(app, onboard=False).status_code == 403
    with SessionLocal() as session:
        assert (
            session.scalar(select(User).where(User.auth_sub == principal.subject))
            is None
        )
    receipt = request(app)
    assert receipt.status_code == 200, receipt.text
    assert set(receipt.json()) == {
        "schema_version",
        "subject",
        "issuer",
        "environment_id",
    }
    assert request(app).json() == receipt.json()
    assert request(app, onboard=False).status_code == 200
    with SessionLocal() as session:
        user = session.scalar(select(User).where(User.auth_sub == principal.subject))
        assert (
            user.email == principal.email
            and user.display_name == principal.display_name
        )
        before = (user.last_login, user.email, user.display_name)
    assert request(app, onboard=False).status_code == 200
    with SessionLocal() as session:
        user = session.scalar(select(User).where(User.auth_sub == principal.subject))
        assert (user.last_login, user.email, user.display_name) == before
    assert tenants.call_count == 2


def test_concurrent_first_connections_converge_on_one_user(boundary):
    app, principal, _, tenants = boundary
    barrier = Barrier(2)

    def before_insert(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO users "):
            barrier.wait(timeout=10)

    event.listen(engine, "before_cursor_execute", before_insert)
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: request(app), range(2)))
    finally:
        event.remove(engine, "before_cursor_execute", before_insert)
    assert [result.status_code for result in results] == [200, 200]
    assert results[0].json() == results[1].json()
    with SessionLocal() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(User)
                .where(User.auth_sub == principal.subject)
            )
            == 1
        )
    assert tenants.call_count == 2


def test_inactive_local_user_is_unchanged(boundary):
    app, principal, _, tenants = boundary
    with SessionLocal() as session:
        user = User(
            auth_sub=principal.subject,
            email="original@example.invalid",
            is_active=False,
        )
        session.add(user)
        session.commit()
        baseline = (user.id, user.email, user.last_login)
    assert request(app).status_code == 403
    with SessionLocal() as session:
        user = session.scalar(select(User).where(User.auth_sub == principal.subject))
        assert not user.is_active
        assert (user.id, user.email, user.last_login) == baseline
    tenants.assert_not_called()


@pytest.mark.parametrize(
    "failure,status",
    [(CurrentPrincipalDenied("disabled"), 403), (RuntimeError("unavailable"), 503)],
)
def test_current_provider_denial_or_outage_never_creates_user(
    boundary, failure, status
):
    app, principal, current, tenants = boundary
    current.side_effect = failure
    assert request(app).status_code == status
    with SessionLocal() as session:
        assert (
            session.scalar(select(User).where(User.auth_sub == principal.subject))
            is None
        )
    tenants.assert_not_called()
