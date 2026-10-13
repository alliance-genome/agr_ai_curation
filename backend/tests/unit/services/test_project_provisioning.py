"""Regression for users arriving after the one-time project membership seed."""
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from src.models.sql.agent import Project, ProjectMember
from src.models.sql.user import User
from src.services.user_service import provision_user


@pytest.fixture
def db(monkeypatch):
    engine = create_engine('sqlite://')
    for table in (User.__table__, Project.__table__, ProjectMember.__table__):
        table.create(engine)
    monkeypatch.setattr('src.services.user_service.provision_weaviate_tenants', lambda _: True)
    monkeypatch.delenv('DEFAULT_PROJECT_ID', raising=False)
    with Session(engine) as session:
        yield session
    engine.dispose()


def principal():
    return SimpleNamespace(subject='new-curator', email='curator@example.org', display_name='Curator')


def project(db, monkeypatch, active=True):
    row = Project(id=uuid.uuid4(), name='Configured collaboration', is_active=active)
    db.add(row)
    db.commit()
    monkeypatch.setenv('DEFAULT_PROJECT_ID', str(row.id))
    return row


def test_new_user_can_resolve_project_for_sharing(db, monkeypatch):
    from src.lib.agent_studio.custom_agent_service import _get_primary_project_id_for_user
    target = project(db, monkeypatch)
    user = provision_user(db, principal())
    assert _get_primary_project_id_for_user(db, user.id) == target.id
    member = db.scalars(select(ProjectMember)).one()
    assert member.role == 'member'
    provision_user(db, principal())
    assert len(db.scalars(select(ProjectMember)).all()) == 1


def test_unconfigured_does_not_pick_arbitrary_project(db):
    db.add(Project(id=uuid.uuid4(), name='Private project'))
    db.commit()
    provision_user(db, principal())
    assert db.scalars(select(ProjectMember)).all() == []


@pytest.mark.parametrize('setting', ['invalid', str(uuid.uuid4()), 'inactive'])
def test_invalid_default_does_not_persist_partial_user(db, monkeypatch, setting):
    if setting == 'inactive':
        project(db, monkeypatch, active=False)
    else:
        monkeypatch.setenv('DEFAULT_PROJECT_ID', setting)
    with pytest.raises(ValueError, match='DEFAULT_PROJECT_ID'):
        provision_user(db, principal())
    db.rollback()
    assert db.scalars(select(User)).all() == []
    assert db.scalars(select(ProjectMember)).all() == []


def test_existing_user_is_not_automatically_reenrolled(db, monkeypatch):
    user = provision_user(db, principal())
    project(db, monkeypatch)
    assert provision_user(db, principal()).id == user.id
    assert db.scalars(select(ProjectMember)).all() == []


def test_existing_membership_is_preserved_when_default_changes(db, monkeypatch):
    original = project(db, monkeypatch)
    user = provision_user(db, principal())
    project(db, monkeypatch)
    provision_user(db, principal())
    member = db.scalars(select(ProjectMember)).one()
    assert (member.user_id, member.project_id) == (user.id, original.id)


def test_membership_failure_does_not_commit_user(db, monkeypatch):
    project(db, monkeypatch)
    add = db.add

    def reject_membership(row):
        if isinstance(row, ProjectMember):
            raise RuntimeError('membership write rejected')
        add(row)

    monkeypatch.setattr(db, 'add', reject_membership)
    with pytest.raises(RuntimeError, match='membership write rejected'):
        provision_user(db, principal())
    db.rollback()
    assert db.scalars(select(User)).all() == []
