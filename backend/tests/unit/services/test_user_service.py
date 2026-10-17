"""Unit tests for user_service provisioning and claim mapping."""

from types import SimpleNamespace
from datetime import datetime, timedelta, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from src.models.sql.user import User


import pytest

from src.services import user_service


def _principal(subject="sub-1", email="user@example.org", display_name: str | None="User"):
    return SimpleNamespace(subject=subject, email=email, display_name=display_name)


def test_principal_from_claims_prefers_groups_list():
    claims = {"sub": "u1", "email": "u1@example.org", "name": "U1", "groups": ["a", "b"]}
    principal = user_service.principal_from_claims(claims, provider="cognito")
    assert principal.subject == "u1"
    assert principal.groups == ["a", "b"]
    assert principal.provider == "cognito"


def test_principal_from_claims_uses_cognito_groups_and_scalar_fallback():
    claims = {"sub": "u2", "email": "u2@example.org", "cognito:groups": "WB_curators"}
    principal = user_service.principal_from_claims(claims, provider="cognito")
    assert principal.groups == ["WB_curators"]


def test_principal_from_claims_defaults_missing_subject_to_empty():
    principal = user_service.principal_from_claims({"email": "u3@example.org"})
    assert principal.subject == ""
    assert principal.display_name == "u3@example.org"


def test_provision_weaviate_tenants_success(monkeypatch):
    created = []

    class _Collection:
        def __init__(self, name):
            self.name = name
            self.tenants = self

        def create(self, tenant):
            created.append((self.name, tenant.name))

    class _Client:
        class collections:
            @staticmethod
            def get(name):
                return _Collection(name)

    class _Conn:
        def session(self):
            class _Ctx:
                def __enter__(self_inner):
                    return _Client()

                def __exit__(self_inner, exc_type, exc, tb):
                    return False

            return _Ctx()

    monkeypatch.setattr(user_service, "get_connection", lambda: _Conn())
    monkeypatch.setattr(user_service, "get_tenant_name", lambda sub: f"tenant_{sub}")

    assert user_service.provision_weaviate_tenants("abc") is True
    assert ("DocumentChunk", "tenant_abc") in created
    assert ("PDFDocument", "tenant_abc") in created


def test_provision_weaviate_tenants_failure(monkeypatch):
    monkeypatch.setattr(user_service, "get_connection", lambda: (_ for _ in ()).throw(RuntimeError("down")))
    assert user_service.provision_weaviate_tenants("abc") is False




@pytest.fixture
def db(monkeypatch):
    engine = create_engine('sqlite://')
    User.__table__.create(engine)
    monkeypatch.delenv('DEFAULT_PROJECT_ID', raising=False)
    monkeypatch.setattr(user_service, 'provision_weaviate_tenants', lambda _: True)
    with Session(engine, expire_on_commit=False) as session:
        yield session
    engine.dispose()


def test_first_login_and_warm_operation_counts(db, monkeypatch):
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(user_service, '_utc_now', lambda: now)
    user = user_service.provision_user(db, _principal())
    assert user.id and user.created_at.replace(tzinfo=timezone.utc) == now
    calls = []
    monkeypatch.setattr(db, 'commit', lambda: calls.append('commit'))
    monkeypatch.setattr(db, 'refresh', lambda _: calls.append('refresh'))
    assert user_service.provision_user(db, _principal()).id == user.id
    assert calls == []


@pytest.mark.parametrize('age,expected', [(None, True), (299, False), (300, True), (301, True), (-60, False)])
def test_activity_boundaries(db, monkeypatch, age, expected):
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(user_service, '_utc_now', lambda: now)
    monkeypatch.setenv('USER_ACTIVITY_WRITE_INTERVAL_SECONDS', '300')
    user = user_service.provision_user(db, _principal())
    user.last_login = None if age is None else now - timedelta(seconds=age)
    db.commit()
    original = user.last_login
    user_service.provision_user(db, _principal())
    assert user.last_login == (now.replace(tzinfo=None) if expected else original)


def test_profile_clearing_is_immediate_and_preserves_account_fields(db, monkeypatch):
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(user_service, '_utc_now', lambda: now)
    user = user_service.provision_user(db, _principal())
    created = user.created_at
    user.is_active = False
    db.commit()
    monkeypatch.setattr(user_service, '_utc_now', lambda: now + timedelta(seconds=1))
    user_service.provision_user(db, _principal(email='invalid', display_name=None))
    assert user.email is None and user.display_name is None
    assert user.is_active is False and user.created_at == created
    assert user.last_login == (now + timedelta(seconds=1)).replace(tzinfo=None)


def test_profile_write_preserves_future_timestamp(db, monkeypatch):
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(user_service, '_utc_now', lambda: now)
    user = user_service.provision_user(db, _principal())
    user.last_login = now + timedelta(hours=1)
    db.commit()
    user_service.provision_user(db, _principal(email='new@example.org', display_name=None))
    assert user.display_name == 'new@example.org'
    assert user.last_login == (now + timedelta(hours=1)).replace(tzinfo=None)


def test_provision_user_rejects_missing_subject():
    with pytest.raises(ValueError, match='missing subject'):
        user_service.provision_user(object(), _principal(subject=''))


def test_naive_and_aware_utc_interpretation():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for last in [now, now.replace(tzinfo=None), now.astimezone(timezone(timedelta(hours=3)))]:
        assert not user_service._activity_due(last, now, 300)
        assert user_service._activity_due(last, now + timedelta(seconds=300), 300)


@pytest.mark.parametrize('raw,interval,ttl,capacity', [('bad', 300, 300, 1024), ('-1', 0, 0, 1), ('0', 0, 0, 1), ('12', 12, 12, 12)])
def test_provisioning_settings(monkeypatch, raw, interval, ttl, capacity):
    from src.lib.openai_agents import config
    for name in ('USER_ACTIVITY_WRITE_INTERVAL_SECONDS', 'USER_TENANT_CACHE_TTL_SECONDS', 'USER_TENANT_CACHE_MAX_ENTRIES'):
        monkeypatch.setenv(name, raw)
    assert config.get_user_activity_write_interval_seconds() == interval
    assert config.get_user_tenant_cache_ttl_seconds() == ttl
    assert config.get_user_tenant_cache_max_entries() == capacity


def test_warm_lookup_does_not_commit_unrelated_pending_work(db):
    user_service.provision_user(db, _principal())
    unrelated = User(auth_sub='pending', email=None, display_name=None)
    db.add(unrelated)
    user_service.provision_user(db, _principal())
    assert unrelated in db.new and unrelated.id is None
    db.rollback()
