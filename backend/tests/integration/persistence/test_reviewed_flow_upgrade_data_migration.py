"""PostgreSQL coverage for the complete reviewed-flow migration."""

from __future__ import annotations

import importlib.util
from copy import deepcopy
from pathlib import Path
from uuid import UUID, uuid4

from alembic import command
from alembic.config import Config
import pytest
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from src.models.sql.database import engine


BACKEND_ROOT = Path(__file__).resolve().parents[3]
MIGRATION_PATH = (
    BACKEND_ROOT
    / "alembic"
    / "versions"
    / "d6e7f8a9b0c1_upgrade_semantically_reviewed_flows.py"
)
SPEC = importlib.util.spec_from_file_location("reviewed_flow_data_migration", MIGRATION_PATH)
assert SPEC is not None and SPEC.loader is not None
migration = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(migration)

SMOKE_ID = UUID("5f3fb920-5f19-4d96-8b61-1c3130ecc4dd")
ARCHIVE_ID = UUID("8846169a-a73a-449d-a025-84b8565d4480")


@pytest.fixture(scope="module", autouse=True)
def migrated_database():
    command.upgrade(Config(str(BACKEND_ROOT / "alembic.ini")), "head")


def _task_node(node_id: str = "task") -> dict:
    return {
        "id": node_id,
        "type": "task_input",
        "position": {"x": 0, "y": 0},
        "data": {
            "agent_id": "task_input",
            "agent_display_name": "Initial Instructions",
            "task_instructions": "Run the test flow",
            "output_key": "task_input",
        },
    }


def _formatter_free_definition() -> dict:
    task = _task_node()
    agent = {
        "id": "agent",
        "type": "agent",
        "position": {"x": 250, "y": 0},
        "data": {
            "agent_id": "gene",
            "agent_display_name": "Gene Validation Agent",
            "output_key": "gene_result",
        },
    }
    return {
        "version": "1.0",
        "entry_node_id": "task",
        "nodes": [task, agent],
        "edges": [{"id": "edge", "source": "task", "target": "agent"}],
    }


def _smoke_definition() -> dict:
    return {
        "edges": [
            {
                "condition": None,
                "id": "edge_1",
                "replaces_attachment_id": None,
                "role": "control_flow",
                "satisfies_binding_id": None,
                "source": "task_input_1",
                "target": "agent_1",
            }
        ],
        "entry_node_id": "task_input_1",
        "nodes": [
            {
                "data": {
                    "agent_description": None,
                    "agent_display_name": "Initial Instructions",
                    "agent_id": "task_input",
                    "custom_instructions": None,
                    "include_evidence": None,
                    "output_filename_template": None,
                    "output_key": "task_input_text",
                    "projection_plan": None,
                    "prompt_version": None,
                    "step_goal": None,
                    "task_instructions": "Smoke-test flow save serializer.",
                    "validation_attachments": [],
                    "validation_groups": [],
                },
                "id": "task_input_1",
                "position": {"x": 0.0, "y": 0.0},
                "type": "task_input",
            },
            {
                "data": {
                    "agent_description": None,
                    "agent_display_name": "Chat Output Agent",
                    "agent_id": "chat_output",
                    "custom_instructions": None,
                    "include_evidence": None,
                    "output_filename_template": None,
                    "output_key": "final_output_updated",
                    "projection_plan": None,
                    "prompt_version": None,
                    "step_goal": None,
                    "task_instructions": None,
                    "validation_attachments": [],
                    "validation_groups": [],
                },
                "id": "agent_1",
                "position": {"x": 260.0, "y": 0.0},
                "type": "agent",
            },
        ],
        "version": "1.0",
    }


def _archive_definition() -> dict:
    return {
        "edges": [
            {
                "condition": None,
                "id": "reactflow__edge-node_0-node_1",
                "source": "node_0",
                "target": "node_1",
            },
            {
                "condition": None,
                "id": "reactflow__edge-node_1-node_2",
                "source": "node_1",
                "target": "node_2",
            },
        ],
        "entry_node_id": "node_0",
        "nodes": [
            {
                "data": {
                    "agent_description": "Define the task for this flow",
                    "agent_display_name": "Initial Instructions",
                    "agent_id": "task_input",
                    "custom_instructions": "",
                    "output_key": "task_input",
                    "prompt_version": None,
                    "step_goal": None,
                    "task_instructions": "Test",
                },
                "id": "node_0",
                "position": {"x": 192.0, "y": 96.0},
                "type": "task_input",
            },
            {
                "data": {
                    "agent_description": "Custom agent from Gene Validation Agent",
                    "agent_display_name": "Gene Validation Agent (Custom)",
                    "agent_id": "ca_7fffddac-c7ad-4ee3-b641-97b6c652fc5b",
                    "custom_instructions": "",
                    "output_key": "ca_7fffddac_c7ad_4ee3_b641_97b6c652fc5b_output",
                    "prompt_version": None,
                    "step_goal": None,
                    "task_instructions": None,
                },
                "id": "node_1",
                "position": {"x": 176.0, "y": 226.0},
                "type": "agent",
            },
            {
                "data": {
                    "agent_description": "Formats and displays results directly in the chat window for review. Used as a flow terminal to show extraction results for immediate review.",
                    "agent_display_name": "Chat Output Agent",
                    "agent_id": "chat_output",
                    "custom_instructions": "",
                    "output_key": "chat_output_output",
                    "prompt_version": None,
                    "step_goal": None,
                    "task_instructions": None,
                },
                "id": "node_2",
                "position": {"x": 240.0, "y": 418.0},
                "type": "agent",
            },
        ],
        "version": "1.0",
    }


def _insert_user(connection: sa.Connection, user_id: int, suffix: str) -> None:
    connection.execute(
        sa.text("INSERT INTO users (user_id, auth_sub) VALUES (:id, :sub)"),
        {"id": user_id, "sub": f"reviewed-flow-migration-{suffix}"},
    )


def _insert_flow(
    connection: sa.Connection,
    *,
    flow_id: UUID,
    user_id: int,
    name: str,
    definition: dict,
    active: bool,
) -> None:
    connection.execute(
        sa.text(
            """
            INSERT INTO curation_flows (id, user_id, name, flow_definition, is_active)
            VALUES (:id, :user_id, :name, :definition, :active)
            """
        ).bindparams(sa.bindparam("definition", type_=JSONB)),
        {
            "id": flow_id,
            "user_id": user_id,
            "name": name,
            "definition": definition,
            "active": active,
        },
    )


def test_updates_archives_deletes_audits_and_is_idempotent():
    suffix = uuid4().hex
    user_id = 920_000_000
    ordinary_id = uuid4()
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            _insert_user(connection, user_id, suffix)
            _insert_flow(
                connection,
                flow_id=ordinary_id,
                user_id=user_id,
                name="Formatter-free",
                definition=_formatter_free_definition(),
                active=True,
            )
            _insert_flow(
                connection,
                flow_id=SMOKE_ID,
                user_id=user_id,
                name="codex-flow-save-public-smoke-1769a3e329",
                definition=_smoke_definition(),
                active=False,
            )
            _insert_flow(
                connection,
                flow_id=ARCHIVE_ID,
                user_id=user_id,
                name="Test",
                definition=_archive_definition(),
                active=True,
            )

            migration._migrate(connection)

            ordinary = connection.execute(
                sa.text(
                    "SELECT flow_definition FROM curation_flows WHERE id = :id"
                ),
                {"id": ordinary_id},
            ).scalar_one()
            assert ordinary["version"] == "1.1"
            archived = connection.execute(
                sa.text(
                    "SELECT is_active, flow_definition FROM curation_flows WHERE id = :id"
                ),
                {"id": ARCHIVE_ID},
            ).mappings().one()
            assert archived["is_active"] is False
            assert archived["flow_definition"]["version"] == "1.1"
            assert archived["flow_definition"]["archived_reason"] == (
                "private_source_agent_inactive"
            )
            assert connection.execute(
                sa.text("SELECT count(*) FROM curation_flows WHERE id = :id"),
                {"id": SMOKE_ID},
            ).scalar_one() == 0
            assert connection.execute(
                sa.text(
                    """
                    SELECT count(*) FROM curation_flows
                    WHERE coalesce(flow_definition->>'version', '1.0') <> '1.1'
                    """
                )
            ).scalar_one() == 0

            audits = connection.execute(
                sa.text(
                    """
                    SELECT row_id, operation, old_data, new_data, application_name
                    FROM audit_log
                    WHERE table_name = 'curation_flows'
                      AND row_id = ANY(:ids)
                      AND operation IN ('UPDATE', 'DELETE')
                    ORDER BY operation, row_id
                    """
                ),
                {"ids": [str(ordinary_id), str(ARCHIVE_ID), str(SMOKE_ID)]},
            ).mappings().all()
            assert len(audits) == 3
            assert {row["application_name"] for row in audits} == {
                "alembic:d6e7f8a9b0c1:reviewed-flow-upgrades"
            }
            deleted = next(row for row in audits if row["operation"] == "DELETE")
            assert deleted["old_data"]["flow_definition"] == _smoke_definition()
            assert deleted["new_data"] is None

            migration._migrate(connection)
            assert connection.execute(
                sa.text(
                    """
                    SELECT count(*) FROM audit_log
                    WHERE table_name = 'curation_flows'
                      AND row_id = ANY(:ids)
                      AND operation IN ('UPDATE', 'DELETE')
                    """
                ),
                {"ids": [str(ordinary_id), str(ARCHIVE_ID), str(SMOKE_ID)]},
            ).scalar_one() == 3
        finally:
            transaction.rollback()


def test_destructive_preimage_drift_rolls_back_everything():
    suffix = uuid4().hex
    user_id = 920_000_001
    ordinary_id = uuid4()
    drifted_smoke = deepcopy(_smoke_definition())
    drifted_smoke["nodes"][0]["data"]["task_instructions"] = "Curator edited"
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            _insert_user(connection, user_id, suffix)
            _insert_flow(
                connection,
                flow_id=ordinary_id,
                user_id=user_id,
                name="Formatter-free",
                definition=_formatter_free_definition(),
                active=True,
            )
            _insert_flow(
                connection,
                flow_id=SMOKE_ID,
                user_id=user_id,
                name="codex-flow-save-public-smoke-1769a3e329",
                definition=drifted_smoke,
                active=False,
            )
            nested = connection.begin_nested()
            with pytest.raises(RuntimeError, match="preimage drifted"):
                migration._migrate(connection)
            nested.rollback()
            assert connection.execute(
                sa.text(
                    "SELECT flow_definition->>'version' FROM curation_flows WHERE id = :id"
                ),
                {"id": ordinary_id},
            ).scalar_one() == "1.0"
            assert connection.execute(
                sa.text("SELECT count(*) FROM curation_flows WHERE id = :id"),
                {"id": SMOKE_ID},
            ).scalar_one() == 1
        finally:
            transaction.rollback()


def test_missing_delete_audit_trigger_blocks_before_mutation():
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(
                sa.text(
                    "ALTER TABLE curation_flows DISABLE TRIGGER audit_curation_flows_delete"
                )
            )
            with pytest.raises(RuntimeError, match="audit_curation_flows_delete"):
                migration._migrate(connection)
        finally:
            transaction.rollback()
