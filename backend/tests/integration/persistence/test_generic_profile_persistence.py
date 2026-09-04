"""Real PostgreSQL profile CRUD, authorization and immutable revision constraints.

Each test runs the migration in a transactional private schema. No shared
application tables are changed or downgraded.
"""

from copy import deepcopy
import importlib.util
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from src.lib.agent_studio import generic_profile_service as service
from src.models.sql.database import engine


@pytest.fixture
def profile_db(monkeypatch):
    schema = "test_profiles_" + uuid4().hex
    with engine.connect() as connection:
        transaction = connection.begin()
        connection.execute(sa.text(f'CREATE SCHEMA "{schema}"'))
        connection.execute(sa.text(f'SET LOCAL search_path TO "{schema}"'))
        connection.execute(sa.text("CREATE TABLE users (user_id integer PRIMARY KEY)"))
        connection.execute(sa.text("CREATE TABLE projects (id uuid PRIMARY KEY)"))
        connection.execute(sa.text("INSERT INTO users VALUES (1), (2), (3)"))
        project_id = uuid4()
        connection.execute(
            sa.text("INSERT INTO projects VALUES (:id)"), {"id": project_id}
        )
        migration_path = (
            Path(__file__).resolve().parents[3]
            / "alembic/versions/f3a4b5c6d7e8_add_generic_profile_revisions.py"
        )
        spec = importlib.util.spec_from_file_location(
            "profile_migration", migration_path
        )
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
        monkeypatch.setattr(
            service,
            "_project_ids",
            lambda db, user: [project_id] if user in (1, 2) else [],
        )
        # API commit/rollback ends its savepoint, never the outer private-schema
        # transaction owned by this fixture.
        db = Session(bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint")
        try:
            yield db, project_id
            db.flush()
            connection.execute(sa.text("SET CONSTRAINTS ALL IMMEDIATE"))
        finally:
            db.close()
            transaction.rollback()


def profile_contract():
    return {
        "name": "Evidence record",
        "description": "Example",
        "semantic_class": "example_record",
        "fields": [
            {
                "key": "paper_name",
                "required": True,
                "nullable": True,
                "source_labels": ["Name in article"],
                "value_schema": {"kind": "string"},
            }
        ],
    }


def test_create_revise_clone_archive_preserves_history(profile_db):
    db, _ = profile_db
    row, first = service.create_profile(db, 1, profile_contract())
    original = deepcopy(first.contract)
    changed = deepcopy(original)
    changed["fields"][0]["nullable"] = False
    row, second, changes = service.revise_profile(
        db, row.id, 1, changed, expected_revision=1
    )
    assert second.revision == 2 and row.head_revision == 2
    assert changes[0]["code"] == "nullable_changed" and changes[0]["breaking"]
    assert first.contract == original and first.fingerprint != second.fingerprint
    history, cursor = service.list_profile_revisions(db, row.id, 1)
    assert [item.revision for item in history] == [2, 1]
    assert cursor is None
    cloned, cloned_revision = service.clone_profile(db, row.id, 1, 1, name="My copy")
    assert cloned.id != row.id and cloned_revision.revision == 1
    assert cloned_revision.contract["fields"] == first.contract["fields"]
    service.archive_profile(db, row.id, 1, expected_revision=2)
    with pytest.raises(service.ProfileNotFoundError):
        service.get_profile(db, row.id, 1)
    assert (
        service.get_profile_revision(db, row.id, 1, 1, include_archived=True).contract
        == original
    )
    assert [item.id for item in service.list_profiles(db, 1)[0]] == [cloned.id]


def test_private_project_authorization_and_clone(profile_db):
    db, project_id = profile_db
    private, _ = service.create_profile(db, 1, profile_contract())
    with pytest.raises(service.ProfileNotFoundError):
        service.get_profile(db, private.id, 2)
    shared, _ = service.create_profile(
        db, 1, profile_contract(), visibility="project", project_id=project_id
    )
    assert service.get_profile(db, shared.id, 2).id == shared.id
    with pytest.raises(service.ProfileNotFoundError):
        service.get_profile(db, shared.id, 3)
    with pytest.raises(service.ProfileNotFoundError):
        service.revise_profile(
            db, shared.id, 2, profile_contract(), expected_revision=1
        )
    clone, _ = service.clone_profile(db, shared.id, 1, 2, name="Curator copy")
    assert clone.owner_id == 2 and clone.visibility == "private"
    with pytest.raises(ValueError):
        service.create_profile(
            db, 3, profile_contract(), visibility="project", project_id=project_id
        )


def test_stale_expected_head_cannot_silently_overwrite(profile_db):
    db, _ = profile_db
    row, _ = service.create_profile(db, 1, profile_contract())
    changed = profile_contract()
    changed["description"] = "New revision"
    service.revise_profile(db, row.id, 1, changed, expected_revision=1)
    with pytest.raises(service.ProfileConflictError):
        service.revise_profile(db, row.id, 1, profile_contract(), expected_revision=1)
    assert row.head_revision == 2


def test_revision_immutability_and_head_foreign_key(profile_db):
    db, _ = profile_db
    row, first = service.create_profile(db, 1, profile_contract())
    with pytest.raises(DBAPIError, match="immutable"):
        with db.begin_nested():
            db.execute(
                sa.text(
                    "UPDATE generic_extraction_profile_revisions SET contract = '{}'::jsonb WHERE id = :id"
                ),
                {"id": first.id},
            )
    with pytest.raises(IntegrityError):
        with db.begin_nested():
            db.execute(sa.text("SET CONSTRAINTS ALL IMMEDIATE"))
            db.execute(
                sa.text(
                    "DELETE FROM generic_extraction_profile_revisions WHERE id = :id"
                ),
                {"id": first.id},
            )
    with pytest.raises(IntegrityError):
        with db.begin_nested():
            db.execute(sa.text("SET CONSTRAINTS ALL IMMEDIATE"))
            db.execute(
                sa.text(
                    "UPDATE generic_extraction_profiles SET head_revision = 99 WHERE id = :id"
                ),
                {"id": row.id},
            )


def test_keyset_pagination_is_bounded_and_authorized(profile_db, monkeypatch):
    db, _ = profile_db
    monkeypatch.setenv("GENERIC_PROFILE_LIST_PAGE_SIZE", "1")
    expected = {
        service.create_profile(db, 1, profile_contract())[0].id for _ in range(3)
    }
    service.create_profile(db, 3, profile_contract())
    seen, cursor = set(), None
    while True:
        rows, cursor = service.list_profiles(db, 1, after_id=cursor)
        assert len(rows) == 1
        seen.add(rows[0].id)
        if cursor is None:
            break
    assert seen == expected
