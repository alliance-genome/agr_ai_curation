"""PostgreSQL coverage for the final GPT-5.6 custom-agent reconciliation."""

from __future__ import annotations

from datetime import datetime, timezone
from importlib.util import module_from_spec, spec_from_file_location
import os
from pathlib import Path
from uuid import uuid4

from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
from sqlalchemy import create_engine, text


BACKEND_ROOT = Path(__file__).resolve().parents[3]
MIGRATION_PATH = (
    BACKEND_ROOT
    / "alembic"
    / "versions"
    / "0f1e2d3c4b5a_remigrate_custom_agents_to_gpt56.py"
)


def _load_migration_module():
    spec = spec_from_file_location("gpt56_custom_agent_remigration", MIGRATION_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def migration_connection():
    engine = create_engine(os.environ["DATABASE_URL"])
    schema_name = f"gpt56_custom_agents_{uuid4().hex}"

    try:
        with engine.connect() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema_name}"'))
            connection.commit()
            try:
                connection.execute(text(f'SET search_path TO "{schema_name}"'))
                connection.execute(
                    text(
                        """
                        CREATE TABLE agents (
                            id uuid PRIMARY KEY,
                            visibility varchar(20) NOT NULL,
                            model_id varchar(100) NOT NULL,
                            model_reasoning varchar(20),
                            updated_at timestamptz NOT NULL
                        )
                        """
                    )
                )
                connection.commit()
                yield connection
            finally:
                connection.rollback()
                connection.execute(
                    text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
                )
                connection.commit()
    finally:
        engine.dispose()


def _insert_agent(
    connection,
    *,
    visibility: str,
    model_id: str,
    model_reasoning: str | None,
    updated_at: datetime,
):
    agent_id = uuid4()
    connection.execute(
        text(
            """
            INSERT INTO agents (id, visibility, model_id, model_reasoning, updated_at)
            VALUES (:id, :visibility, :model_id, :model_reasoning, :updated_at)
            """
        ),
        {
            "id": agent_id,
            "visibility": visibility,
            "model_id": model_id,
            "model_reasoning": model_reasoning,
            "updated_at": updated_at,
        },
    )
    return agent_id


def test_upgrade_reconciles_remaining_custom_agents_and_is_idempotent(
    migration_connection,
):
    migration = _load_migration_module()
    original_timestamp = datetime(2026, 7, 9, tzinfo=timezone.utc)
    legacy_sol_id = _insert_agent(
        migration_connection,
        visibility="private",
        model_id="gpt-5.5",
        model_reasoning="high",
        updated_at=original_timestamp,
    )
    legacy_terra_id = _insert_agent(
        migration_connection,
        visibility="project",
        model_id="gpt-5.4-mini",
        model_reasoning="low",
        updated_at=original_timestamp,
    )
    system_id = _insert_agent(
        migration_connection,
        visibility="system",
        model_id="gpt-5.5",
        model_reasoning="medium",
        updated_at=original_timestamp,
    )
    unrelated_id = _insert_agent(
        migration_connection,
        visibility="private",
        model_id="org.example/model-x",
        model_reasoning=None,
        updated_at=original_timestamp,
    )
    migration_connection.commit()

    migration.op = Operations(MigrationContext.configure(migration_connection))
    migration.upgrade()

    rows = {
        row.id: row
        for row in migration_connection.execute(
            text(
                """
                SELECT id, visibility, model_id, model_reasoning, updated_at
                FROM agents
                ORDER BY id
                """
            )
        )
    }
    assert rows[legacy_sol_id].model_id == "gpt-5.6-sol"
    assert rows[legacy_sol_id].model_reasoning == "high"
    assert rows[legacy_sol_id].updated_at > original_timestamp
    assert rows[legacy_terra_id].model_id == "gpt-5.6-terra"
    assert rows[legacy_terra_id].model_reasoning == "low"
    assert rows[legacy_terra_id].updated_at > original_timestamp
    assert rows[system_id].model_id == "gpt-5.5"
    assert rows[system_id].updated_at == original_timestamp
    assert rows[unrelated_id].model_id == "org.example/model-x"
    assert rows[unrelated_id].updated_at == original_timestamp

    first_updated_at = {
        legacy_sol_id: rows[legacy_sol_id].updated_at,
        legacy_terra_id: rows[legacy_terra_id].updated_at,
    }
    migration.upgrade()
    rerun = {
        row.id: row.updated_at
        for row in migration_connection.execute(
            text("SELECT id, updated_at FROM agents")
        )
    }
    assert rerun[legacy_sol_id] == first_updated_at[legacy_sol_id]
    assert rerun[legacy_terra_id] == first_updated_at[legacy_terra_id]


def test_downgrade_does_not_restore_retired_model_ids(migration_connection):
    migration = _load_migration_module()
    canonical_id = _insert_agent(
        migration_connection,
        visibility="private",
        model_id="gpt-5.6-sol",
        model_reasoning="medium",
        updated_at=datetime(2026, 8, 14, tzinfo=timezone.utc),
    )
    migration_connection.commit()

    migration.op = Operations(MigrationContext.configure(migration_connection))
    migration.downgrade()

    assert migration_connection.execute(
        text("SELECT model_id FROM agents WHERE id = :id"),
        {"id": canonical_id},
    ).scalar_one() == "gpt-5.6-sol"
