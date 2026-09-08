"""Disposable PostgreSQL migration and flow authorization/clone coverage."""
import asyncio
from importlib.util import module_from_spec, spec_from_file_location
import os
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.api import flows
from src.lib.flows.access import get_visible_flow
from src.models.sql import CurationFlow
from src.schemas.flows import CloneFlowRequest, ShareFlowRequest
from tests.unit.api.test_flows_api import _minimal_flow_definition_payload


def migration(connection, filename):
    path = Path(__file__).resolve().parents[3] / "alembic" / "versions" / filename
    spec = spec_from_file_location(filename.removesuffix(".py"), path)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    setattr(module, "op", Operations(MigrationContext.configure(connection)))
    return module


@pytest.fixture
def sharing_db():
    engine = create_engine(os.environ["DATABASE_URL"])
    schema = f"flow_sharing_{uuid4().hex}"
    with engine.connect() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        connection.execute(text(f'SET search_path TO "{schema}"'))
        connection.execute(text("""
            CREATE TABLE projects (id uuid PRIMARY KEY);
            CREATE TABLE project_members (
                project_id uuid NOT NULL REFERENCES projects(id),
                user_id integer NOT NULL, joined_at timestamptz DEFAULT now()
            );
        """))
        migration(connection, "k5l6m7n8o9p0_create_curation_flows_table.py").upgrade()
        connection.execute(text("""
            INSERT INTO curation_flows (id, user_id, name, flow_definition)
            VALUES (:id, 7, 'Existing', '{}')
        """), {"id": uuid4()})
        sharing_migration = migration(connection, "7c9e2a4b6d80_add_flow_project_sharing.py")
        sharing_migration.upgrade()
        connection.commit()
        try:
            with Session(connection) as db:
                yield db, sharing_migration
        finally:
            connection.rollback()
            connection.execute(text('SET search_path TO public'))
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            connection.commit()
    engine.dispose()


def test_alembic_upgrade_head_preserves_existing_private_flow():
    config = Config(str(Path(__file__).resolve().parents[3] / "alembic.ini"))
    engine = create_engine(os.environ["DATABASE_URL"])
    flow_id = uuid4()
    command.upgrade(config, "head")
    command.downgrade(config, "n0o1p2q3r4s5")
    try:
        with engine.begin() as connection:
            connection.execute(text("""
                INSERT INTO curation_flows (id, user_id, name, flow_definition)
                VALUES (:id, 7, :name, '{}')
            """), {"id": flow_id, "name": f"Migration {flow_id}"})
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert connection.execute(text(
                "SELECT version_num FROM alembic_version"
            )).scalar_one() == "7c9e2a4b6d80"
            assert connection.execute(text(
                "SELECT visibility, project_id, shared_at FROM curation_flows WHERE id = :id"
            ), {"id": flow_id}).one() == ("private", None, None)
    finally:
        command.upgrade(config, "head")
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM curation_flows WHERE id = :id"), {"id": flow_id})
        engine.dispose()


def test_existing_private_defaults_constraints_and_index(sharing_db):
    db, migration_module = sharing_db
    old = db.query(CurationFlow).one()
    assert (old.visibility, old.project_id, old.shared_at) == ("private", None, None)
    project = uuid4()
    db.execute(text("INSERT INTO projects VALUES (:id)"), {"id": project})
    db.commit()
    for update in ["visibility = 'public'", "visibility = 'project'",
                   f"project_id = '{project}'", "shared_at = now()",
                   f"visibility = 'project', project_id = '{uuid4()}', shared_at = now()"]:
        with pytest.raises(IntegrityError), db.begin_nested():
            db.execute(text(f"UPDATE curation_flows SET {update}"))
    index = db.execute(text("SELECT indexdef FROM pg_indexes WHERE schemaname = current_schema() "
                            "AND indexname = 'idx_curation_flows_project_active_updated'")).scalar_one()
    assert "project_id, updated_at" in index and "is_active IS TRUE" in index and "project" in index
    migration_module.downgrade()
    assert db.execute(text("SELECT name FROM curation_flows")).scalar_one() == "Existing"
    migration_module.upgrade()
    assert db.execute(text("SELECT visibility FROM curation_flows")).scalar_one() == "private"


def test_shared_list_clone_collision_and_revocation(sharing_db, monkeypatch):
    db, _ = sharing_db
    project = uuid4()
    db.execute(text("INSERT INTO projects VALUES (:id)"), {"id": project})
    db.execute(text("INSERT INTO project_members (project_id, user_id) VALUES (:id, 7), (:id, 99)"), {"id": project})
    source = db.query(CurationFlow).one()
    source.flow_definition = _minimal_flow_definition_payload()
    source.execution_count = 12
    source.last_executed_at = source.created_at
    db.commit()
    monkeypatch.setattr(flows, "set_global_user_from_cognito", lambda _db, user: SimpleNamespace(id=user["id"]))
    monkeypatch.setattr(flows, "_flow_agent_policy_entry", lambda *_, **__: {"category": "Extraction", "supervisor": {}})
    asyncio.run(flows.share_flow(source.id, ShareFlowRequest(visibility="project"), {"id": 7}, db))
    member_list = asyncio.run(flows.list_flows(1, 1, {"id": 99}, db))
    assert member_list.total == 1 and member_list.flows[0].id == source.id
    assert not member_list.flows[0].is_owner
    assert asyncio.run(flows.list_flows(1, 1, {"id": 100}, db)).total == 0
    clone = asyncio.run(flows.clone_flow(source.id, CloneFlowRequest(), {"id": 99}, db))
    assert clone.user_id == 99 and clone.is_owner and clone.visibility == "private"
    assert clone.project_id is None and clone.shared_at is None
    assert clone.execution_count == 0 and clone.last_executed_at is None
    assert clone.id != source.id and clone.name == "Existing (Copy)"
    second = asyncio.run(flows.clone_flow(source.id, CloneFlowRequest(), {"id": 99}, db))
    assert second.name == "Existing (Copy 2)"
    with pytest.raises(flows.HTTPException) as exc:
        asyncio.run(flows.clone_flow(source.id, CloneFlowRequest(name=clone.name), {"id": 99}, db))
    assert exc.value.status_code == 409
    db.refresh(source)
    assert source.execution_count == 12 and source.visibility == "project"
    assert asyncio.run(flows.list_flows(2, 1, {"id": 99}, db)).total == 3
    db.execute(text("DELETE FROM project_members WHERE user_id = 99"))
    db.commit()
    with pytest.raises(flows.HTTPException) as exc:
        get_visible_flow(db, source.id, 99)
    assert exc.value.status_code == 403
    assert asyncio.run(flows.list_flows(1, 10, {"id": 99}, db)).total == 2
    asyncio.run(flows.share_flow(source.id, ShareFlowRequest(visibility="private"), {"id": 7}, db))
    assert source.project_id is None and source.shared_at is None
    source.is_active = False
    db.commit()
    with pytest.raises(flows.HTTPException) as exc:
        get_visible_flow(db, source.id, 7)
    assert exc.value.status_code == 404
    # Archived clone names may be reused; active names remain unique.
    db.get(CurationFlow, clone.id).is_active = False
    source.is_active = True
    db.commit()
    monkeypatch.setattr(flows, "get_visible_flow", lambda *_: source)
    recycled = asyncio.run(flows.clone_flow(source.id, CloneFlowRequest(), {"id": 99}, db))
    assert recycled.name == clone.name
