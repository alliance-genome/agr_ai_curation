"""PostgreSQL coverage for moving custom agents from GPT-5.6 Sol/Terra to GPT-6 Sol."""

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
    / "r5a6b7c8d9e0_migrate_custom_agents_to_gpt6_sol.py"
)


def _load_migration_module():
    spec = spec_from_file_location("gpt6_sol_custom_agent_migration", MIGRATION_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def migration_connection():
    engine = create_engine(os.environ["DATABASE_URL"])
    schema_name = f"gpt6_sol_custom_agents_{uuid4().hex}"

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


def test_upgrade_moves_every_custom_gpt56_agent_to_gpt6_sol(migration_connection):
    migration = _load_migration_module()
    original = datetime(2026, 9, 1, tzinfo=timezone.utc)
    cases = {
        "sol_high": ("private", "gpt-5.6-sol", "high"),
        "sol_disabled": ("private", "gpt-5.6-sol", "disabled"),
        "terra_low": ("project", "gpt-5.6-terra", "low"),
        "terra_minimal": ("private", "gpt-5.6-terra", "minimal"),
        "terra_none": ("private", "gpt-5.6-terra", None),
        "system_sol": ("system", "gpt-5.6-sol", "medium"),
        "system_terra": ("system", "gpt-5.6-terra", "medium"),
        "astra": ("private", "gpt-6-astra", "low"),
        "external": ("private", "deepseek/deepseek-v4-pro-0813", None),
    }
    ids = {
        name: _insert_agent(
            migration_connection,
            visibility=visibility,
            model_id=model_id,
            model_reasoning=reasoning,
            updated_at=original,
        )
        for name, (visibility, model_id, reasoning) in cases.items()
    }
    migration_connection.commit()

    migration.op = Operations(MigrationContext.configure(migration_connection))
    migration.upgrade()

    rows = {
        row.id: row
        for row in migration_connection.execute(
            text("SELECT id, model_id, model_reasoning, updated_at FROM agents")
        )
    }
    expected = {
        "sol_high": ("gpt-6-sol", "high"),
        "sol_disabled": ("gpt-6-sol", "disabled"),
        "terra_low": ("gpt-6-sol", "low"),
        # GPT-6 Sol rejects "minimal" (live-verified 2026-09-24).
        "terra_minimal": ("gpt-6-sol", "low"),
        "terra_none": ("gpt-6-sol", None),
    }
    for name, (model_id, reasoning) in expected.items():
        row = rows[ids[name]]
        assert (row.model_id, row.model_reasoning) == (model_id, reasoning), name
        assert row.updated_at > original, name
    # System rows follow package config and deployment env at startup sync.
    for name in ("system_sol", "system_terra", "astra", "external"):
        row = rows[ids[name]]
        assert (row.model_id, row.model_reasoning) == cases[name][1:], name
        assert row.updated_at == original, name

    moved_at = {ids[name]: rows[ids[name]].updated_at for name in expected}
    migration.upgrade()
    rerun = {
        row.id: row.updated_at
        for row in migration_connection.execute(text("SELECT id, updated_at FROM agents"))
    }
    assert all(rerun[agent_id] == stamp for agent_id, stamp in moved_at.items())


def test_downgrade_does_not_restore_retired_model_ids(migration_connection):
    migration = _load_migration_module()
    agent_id = _insert_agent(
        migration_connection,
        visibility="private",
        model_id="gpt-6-sol",
        model_reasoning="medium",
        updated_at=datetime(2026, 9, 24, tzinfo=timezone.utc),
    )
    migration_connection.commit()

    migration.op = Operations(MigrationContext.configure(migration_connection))
    migration.downgrade()

    assert migration_connection.execute(
        text("SELECT model_id FROM agents WHERE id = :id"),
        {"id": agent_id},
    ).scalar_one() == "gpt-6-sol"
