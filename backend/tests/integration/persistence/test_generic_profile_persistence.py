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
        mapping_spec = importlib.util.spec_from_file_location("profile_mapping_migration",
            migration_path.with_name("h5c6d7e8f9a0_add_profile_validator_references.py"))
        mapping_migration = importlib.util.module_from_spec(mapping_spec)
        mapping_spec.loader.exec_module(mapping_migration)
        with Operations.context(MigrationContext.configure(connection)):
            mapping_migration.upgrade()
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


def mapped_contract(monkeypatch):
    from src.lib.agent_studio import profile_mapping_service as mapping_service
    from src.lib.domain_packs.validation_registry import ValidatorBinding, ValidationBindingState
    from src.schemas.domain_pack_metadata import CustomProfileValidatorReuse
    from src.schemas.profile_validator_mapping import ValidatorCapabilityRef

    reuse = CustomProfileValidatorReuse.model_validate({"enabled": True, "inputs": {}, "outputs": {},
        "policy": {"unresolved_default": "requires_curator_review", "unresolved_allowed": ["requires_curator_review"],
                   "readiness_default": False, "readiness_allowed": [False]}})
    cap = mapping_service.ReusableCapability(ValidatorCapabilityRef(package_id="test", package_version="1.0.0",
        domain_pack_id="test", domain_pack_version="1.0.0", binding_id="lookup"),
        ValidatorBinding(binding_id="lookup", source_scope="object", state=ValidationBindingState.ACTIVE,
            custom_profile_reuse=reuse, raw={"custom_profile_reuse": reuse.model_dump(mode="json")}))
    monkeypatch.setattr(mapping_service, "capability_catalog", lambda **kwargs: [cap])
    raw = profile_contract()
    raw["validator_mappings"] = [{"mapping_id": "lookup", "capability_ref": cap.ref.model_dump(),
        "capability_fingerprint": cap.fingerprint(), "inputs": {}, "outputs": {},
        "policy": {"unresolved": "requires_curator_review", "blocks_readiness": False}}]
    return raw, cap


def test_mapping_capability_history_is_immutable_and_referenced(profile_db, monkeypatch):
    from src.models.sql.profile_validator_capability import ProfileValidatorCapability, ProfileValidatorCapabilityReference
    db, _ = profile_db
    raw, cap = mapped_contract(monkeypatch)
    row, revision = service.create_profile(db, 1, raw)
    db.execute(sa.text("SET CONSTRAINTS ALL IMMEDIATE"))
    assert db.get(ProfileValidatorCapability, cap.fingerprint()).snapshot == cap.snapshot()
    assert db.get(ProfileValidatorCapabilityReference, (revision.id, "lookup")) is not None
    db.execute(sa.text("SET CONSTRAINTS ALL DEFERRED"))
    clone, _ = service.clone_profile(db, row.id, 1, 1, name="clone")
    assert clone.id != row.id
    assert db.query(ProfileValidatorCapability).count() == 1
    for table in ("profile_validator_capabilities", "profile_validator_capability_references"):
        with pytest.raises(DBAPIError), db.begin_nested():
            db.execute(sa.text(f"DELETE FROM {table}"))
    service.archive_profile(db, row.id, 1, expected_revision=1)
    assert service.get_profile_revision(db, row.id, 1, 1, include_archived=True).contract == revision.contract


def test_direct_revision_insert_cannot_omit_capability_reference(profile_db, monkeypatch):
    from src.models.sql.generic_extraction_profile import GenericExtractionProfileRevision
    from src.schemas.generic_extraction_profile import normalize_profile_contract
    db, _ = profile_db
    row, _ = service.create_profile(db, 1, profile_contract())
    raw, _ = mapped_contract(monkeypatch)
    parsed = normalize_profile_contract(raw)
    with pytest.raises(DBAPIError, match="immutable capability reference"), db.begin_nested():
        db.add(GenericExtractionProfileRevision(profile_id=row.id, revision=2, fingerprint=parsed.fingerprint(),
            contract=parsed.model_dump(mode="json"), creator_id=1))
        db.flush()
        db.execute(sa.text("SET CONSTRAINTS ALL IMMEDIATE"))


def test_capability_version_cannot_change_and_old_profile_stays_readable(profile_db, monkeypatch):
    from dataclasses import replace
    from src.lib.agent_studio import profile_mapping_service as mapping_service
    db, _ = profile_db
    raw, cap = mapped_contract(monkeypatch)
    row, first = service.create_profile(db, 1, raw)
    changed_cap = replace(cap, binding=replace(cap.binding, raw={**cap.binding.raw, "description": "Changed semantics"}))
    monkeypatch.setattr(mapping_service, "capability_catalog", lambda **kwargs: [changed_cap])
    changed = deepcopy(raw)
    changed["validator_mappings"][0]["capability_fingerprint"] = changed_cap.fingerprint()
    with pytest.raises(mapping_service.ProfileMappingError, match="incompatible"), db.begin_nested():
        service.revise_profile(db, row.id, 1, changed, expected_revision=1)
    monkeypatch.setattr(mapping_service, "capability_catalog", lambda **kwargs: [])
    assert service.get_profile_revision(db, row.id, 1, 1).fingerprint == first.fingerprint
    with pytest.raises(mapping_service.ProfileMappingError), db.begin_nested():
        service.clone_profile(db, row.id, 1, 1, name="Cannot execute removed capability")


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
