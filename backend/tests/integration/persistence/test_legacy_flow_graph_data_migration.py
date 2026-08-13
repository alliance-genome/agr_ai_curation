"""PostgreSQL coverage for the v0.8.12 saved-flow data migration."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
import pytest
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from src.models.sql.database import engine


BACKEND_ROOT = Path(__file__).resolve().parents[3]
MIGRATION_PATH = (
    BACKEND_ROOT / "alembic" / "versions" / "c5d6e7f8a9b0_upgrade_legacy_flow_graphs.py"
)
SPEC = importlib.util.spec_from_file_location("legacy_flow_graph_data_migration", MIGRATION_PATH)
assert SPEC is not None and SPEC.loader is not None
migration = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(migration)


@pytest.fixture(scope="module", autouse=True)
def migrated_database():
    command.upgrade(Config(str(BACKEND_ROOT / "alembic.ini")), "head")


def _legacy_definition(extractor_key: str) -> dict:
    return {
        "version": "1.0",
        "entry_node_id": "task",
        "nodes": [
            {
                "id": "task",
                "type": "task_input",
                "position": {"x": 0, "y": 0},
                "data": {
                    "agent_id": "task_input",
                    "agent_display_name": "Initial Instructions",
                    "task_instructions": "Run the test flow",
                    "output_key": "task_input",
                },
            },
            {
                "id": "extract",
                "type": "agent",
                "position": {"x": 250, "y": 0},
                "data": {
                    "agent_id": extractor_key,
                    "agent_display_name": "Extractor",
                    "output_key": "extracted",
                },
            },
            {
                "id": "output",
                "type": "agent",
                "position": {"x": 500, "y": 0},
                "data": {
                    "agent_id": "chat_output",
                    "agent_display_name": "Chat Output",
                    "output_key": "rendered",
                },
            },
        ],
        "edges": [
            {"id": "e1", "source": "task", "target": "extract"},
            {"id": "e2", "source": "extract", "target": "output"},
        ],
    }


def test_migration_honors_visibility_audits_jsonb_and_is_idempotent():
    suffix = uuid4().hex
    user_ids = [910_000_000 + index for index in range(3)]
    owner_id, other_id, member_id = user_ids
    project_id = uuid4()
    keys = {
        "system": f"test_system_extract_{suffix}",
        "private": f"test_private_extract_{suffix}",
        "project": f"test_project_extract_{suffix}",
        "inactive": f"test_inactive_extract_{suffix}",
    }
    flow_ids = {name: uuid4() for name in ("system", "private", "private_hidden", "project", "inactive")}

    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            for user_id in user_ids:
                connection.execute(
                    sa.text(
                        "INSERT INTO users (user_id, auth_sub) VALUES (:user_id, :auth_sub)"
                    ),
                    {"user_id": user_id, "auth_sub": f"migration-test-{user_id}-{suffix}"},
                )
            connection.execute(
                sa.text("INSERT INTO projects (id, name) VALUES (:id, :name)"),
                {"id": project_id, "name": f"Migration test {suffix}"},
            )
            connection.execute(
                sa.text(
                    """
                    INSERT INTO project_members (project_id, user_id, role)
                    VALUES (:project_id, :user_id, 'member')
                    """
                ),
                {"project_id": project_id, "user_id": member_id},
            )

            agent_insert = sa.text(
                """
                INSERT INTO agents (
                    agent_key, user_id, name, instructions, model_id,
                    category, visibility, project_id, is_active
                ) VALUES (
                    :agent_key, :user_id, :name, 'test', 'test-model',
                    'Extraction', :visibility, :project_id, :is_active
                )
                """
            )
            connection.execute(
                agent_insert,
                [
                    {
                        "agent_key": keys["system"], "user_id": None,
                        "name": "System extractor", "visibility": "system",
                        "project_id": None, "is_active": True,
                    },
                    {
                        "agent_key": keys["private"], "user_id": owner_id,
                        "name": "Private extractor", "visibility": "private",
                        "project_id": None, "is_active": True,
                    },
                    {
                        "agent_key": keys["project"], "user_id": owner_id,
                        "name": "Project extractor", "visibility": "project",
                        "project_id": project_id, "is_active": True,
                    },
                    {
                        "agent_key": keys["inactive"], "user_id": owner_id,
                        "name": "Inactive extractor", "visibility": "private",
                        "project_id": None, "is_active": False,
                    },
                ],
            )

            flow_insert = sa.text(
                """
                INSERT INTO curation_flows (
                    id, user_id, name, flow_definition, is_active
                ) VALUES (
                    :id, :user_id, :name, :definition, true
                )
                """
            ).bindparams(sa.bindparam("definition", type_=JSONB))
            flow_specs = [
                ("system", owner_id, keys["system"]),
                ("private", owner_id, keys["private"]),
                ("private_hidden", other_id, keys["private"]),
                ("project", member_id, keys["project"]),
                ("inactive", owner_id, keys["inactive"]),
            ]
            for name, user_id, extractor_key in flow_specs:
                connection.execute(
                    flow_insert,
                    {
                        "id": flow_ids[name],
                        "user_id": user_id,
                        "name": f"Migration {name} {suffix}",
                        "definition": _legacy_definition(extractor_key),
                    },
                )

            original_updated_at = dict(
                connection.execute(
                    sa.text(
                        "SELECT id, updated_at FROM curation_flows WHERE id = ANY(:ids)"
                    ),
                    {"ids": list(flow_ids.values())},
                ).all()
            )
            migration._migrate(connection)

            migrated_rows = {
                row.id: row.flow_definition
                for row in connection.execute(
                    sa.text(
                        "SELECT id, flow_definition FROM curation_flows WHERE id = ANY(:ids)"
                    ),
                    {"ids": list(flow_ids.values())},
                )
            }
            for name in ("system", "private", "project"):
                assert migrated_rows[flow_ids[name]]["version"] == "1.1"
            for name in ("private_hidden", "inactive"):
                assert migrated_rows[flow_ids[name]]["version"] == "1.0"

            current_updated_at = dict(
                connection.execute(
                    sa.text(
                        "SELECT id, updated_at FROM curation_flows WHERE id = ANY(:ids)"
                    ),
                    {"ids": list(flow_ids.values())},
                ).all()
            )
            assert current_updated_at == original_updated_at

            updated_ids = [flow_ids[name] for name in ("system", "private", "project")]
            audit_rows = connection.execute(
                sa.text(
                    """
                    SELECT row_id, old_data, new_data, application_name
                    FROM audit_log
                    WHERE table_name = 'curation_flows'
                      AND operation = 'UPDATE'
                      AND row_id = ANY(:row_ids)
                    """
                ),
                {"row_ids": [str(value) for value in updated_ids]},
            ).mappings().all()
            assert len(audit_rows) == 3
            assert all(row["old_data"]["flow_definition"]["version"] == "1.0" for row in audit_rows)
            assert all(row["new_data"]["flow_definition"]["version"] == "1.1" for row in audit_rows)
            assert {row["application_name"] for row in audit_rows} == {
                "alembic:c5d6e7f8a9b0:legacy-flow-graphs"
            }

            migration._migrate(connection)
            audit_count = connection.execute(
                sa.text(
                    """
                    SELECT count(*) FROM audit_log
                    WHERE table_name = 'curation_flows'
                      AND operation = 'UPDATE'
                      AND row_id = ANY(:row_ids)
                    """
                ),
                {"row_ids": [str(value) for value in updated_ids]},
            ).scalar_one()
            assert audit_count == 3
        finally:
            transaction.rollback()
