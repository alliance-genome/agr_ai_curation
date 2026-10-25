"""Real PostgreSQL, separate-Session concurrency without timing-based sleeps."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier, Event
from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session, Query

from src.models.sql.database import engine as app_engine
from src.models.sql.user import User
from src.models.sql.agent import Project, ProjectMember
from src.services import user_service


@pytest.fixture
def engine(monkeypatch):
    schema = 'provision_' + uuid4().hex
    with app_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    isolated = create_engine(app_engine.url, connect_args={'options': f'-csearch_path={schema}'})
    for table in (User.__table__, Project.__table__, ProjectMember.__table__):
        table.create(isolated)
    monkeypatch.delenv('DEFAULT_PROJECT_ID', raising=False)
    try:
        # Unwind inside the integration auth patch's lifetime. The shared
        # monkeypatch fixture tears down later and would restore its MagicMock.
        with patch.object(user_service, 'provision_weaviate_tenants', lambda _: False):
            yield isolated
    finally:
        isolated.dispose()
        with app_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))


def principal(email: str | None = 'user@example.org', display_name: str | None = 'User'):
    return SimpleNamespace(subject='race-user', email=email, display_name=display_name)


def test_concurrent_first_login_has_one_identity_and_membership(engine, monkeypatch):
    project_id = uuid4()
    with Session(engine) as db:
        db.add(Project(id=project_id, name='Test project', is_active=True))
        db.commit()
    monkeypatch.setenv('DEFAULT_PROJECT_ID', str(project_id))
    barrier = Barrier(2)
    class FirstRead(Query):
        def one_or_none(self):
            result = super().one_or_none()
            if self.column_descriptions[0]['entity'] is User:
                assert result is None
                barrier.wait(5)
            return result
    def provision():
        with Session(engine, query_cls=FirstRead) as db:
            return user_service.provision_user(db, principal()).id
    with ThreadPoolExecutor(2) as pool:
        first, second = pool.submit(provision), pool.submit(provision)
        assert first.result(10) == second.result(10)
    with Session(engine) as db:
        assert len(db.scalars(select(User)).all()) == 1
        assert len(db.scalars(select(ProjectMember)).all()) == 1


def test_stale_activity_cannot_overwrite_newer_profile_or_timestamp(engine, monkeypatch):
    initial = datetime(2026, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(user_service, '_utc_now', lambda: initial)
    with Session(engine) as db:
        user_service.provision_user(db, principal())
    old_read, newer_done = Event(), Event()
    class StaleRead(Query):
        def one_or_none(self):
            result = super().one_or_none()
            old_read.set()
            assert newer_done.wait(5)
            return result
    def older():
        with Session(engine, query_cls=StaleRead) as db:
            return user_service.provision_user(db, principal()).id
    monkeypatch.setattr(user_service, '_utc_now', lambda: initial + timedelta(minutes=10))
    with ThreadPoolExecutor(1) as pool:
        pending = pool.submit(older)
        assert old_read.wait(5)
        monkeypatch.setattr(user_service, '_utc_now', lambda: initial + timedelta(minutes=11))
        try:
            with Session(engine) as db:
                user_service.provision_user(db, principal(email=None, display_name=None))
                user = db.scalars(select(User)).one()
                user.is_active = False
                db.commit()
        finally:
            newer_done.set()
        pending.result(10)
    with Session(engine) as db:
        user = db.scalars(select(User)).one()
        assert user.email is None and user.display_name is None
        assert user.last_login == initial + timedelta(minutes=11)
        assert user.is_active is False and user.created_at == initial


def test_profile_update_after_stale_read_preserves_newer_activity(engine, monkeypatch):
    initial = datetime(2026, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(user_service, '_utc_now', lambda: initial)
    with Session(engine) as db:
        user_service.provision_user(db, principal())
    with Session(engine, expire_on_commit=False) as stale, Session(engine) as newer:
        stale_user = stale.scalars(select(User)).one()
        monkeypatch.setattr(user_service, '_utc_now', lambda: initial + timedelta(minutes=20))
        user_service.provision_user(newer, principal(email='new@example.org'))
        monkeypatch.setattr(user_service, '_utc_now', lambda: initial + timedelta(minutes=10))
        user_service.provision_user(stale, principal(display_name='Changed'))
        assert stale_user.last_login == initial + timedelta(minutes=20)
        assert stale_user.display_name == 'Changed'
        # Profile claims are synchronized in write order, without invented provider revisions.
        assert stale_user.email == 'user@example.org'


def test_real_weaviate_duplicate_creation_converges(weaviate_connection, setup_collections, monkeypatch):
    from src.services.tenant_provisioning_cache import TenantProvisioningCache
    monkeypatch.setattr(user_service, 'get_connection', lambda: weaviate_connection)
    cache = TenantProvisioningCache()
    monkeypatch.setattr(user_service, 'tenant_provisioning_cache', cache)
    subject = 'provision-test-' + uuid4().hex
    try:
        assert user_service.provision_weaviate_tenants(subject)
        cache.clear()
        assert user_service.provision_weaviate_tenants(subject)
    finally:
        with weaviate_connection.session() as client:
            for name in ('DocumentChunk', 'PDFDocument'):
                client.collections.get(name).tenants.remove(user_service.get_tenant_name(subject))
