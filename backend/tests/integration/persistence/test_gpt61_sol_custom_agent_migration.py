"""PostgreSQL coverage for moving custom agents from GPT-6 Sol to GPT-6.1 Sol."""

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
VERSIONS = BACKEND_ROOT / "alembic" / "versions"
MIGRATION_PATH = VERSIONS / "s6b7c8d9e0f1_migrate_custom_agents_to_gpt61_sol.py"
GPT6_SOL_MIGRATION_PATH = VERSIONS / "r5a6b7c8d9e0_migrate_custom_agents_to_gpt6_sol.py"


def _load_migration_module(path: Path, name: str):
    spec = spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_upgrade(module, connection) -> None:
    module.op = Operations(MigrationContext.configure(connection))
    module.upgrade()


@pytest.fixture
def migration_connection():
    engine = create_engine(os.environ["DATABASE_URL"])
    schema_name = f"gpt61_sol_custom_agents_{uuid4().hex}"

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


def _insert_agents(connection, cases, updated_at: datetime):
    ids = {}
    for name, (visibility, model_id, model_reasoning) in cases.items():
        ids[name] = uuid4()
        connection.execute(
            text(
                """
                INSERT INTO agents (id, visibility, model_id, model_reasoning, updated_at)
                VALUES (:id, :visibility, :model_id, :model_reasoning, :updated_at)
                """
            ),
            {
                "id": ids[name],
                "visibility": visibility,
                "model_id": model_id,
                "model_reasoning": model_reasoning,
                "updated_at": updated_at,
            },
        )
    connection.commit()
    return ids


def _rows(connection):
    return {
        row.id: row
        for row in connection.execute(
            text("SELECT id, model_id, model_reasoning, updated_at FROM agents")
        )
    }


def test_upgrade_moves_every_custom_gpt6_sol_agent_to_gpt61_sol(migration_connection):
    migration = _load_migration_module(MIGRATION_PATH, "gpt61_sol_custom_agent_migration")
    original = datetime(2026, 9, 25, tzinfo=timezone.utc)
    cases = {
        "sol_low": ("private", "gpt-6-sol", "low"),
        "sol_medium": ("project", "gpt-6-sol", "medium"),
        "sol_high": ("private", "gpt-6-sol", "high"),
        "sol_xhigh": ("private", "gpt-6-sol", "xhigh"),
        "sol_none": ("private", "gpt-6-sol", None),
        "system_sol": ("system", "gpt-6-sol", "medium"),
        "astra_xhigh": ("private", "gpt-6-astra", "xhigh"),
        "external": ("private", "deepseek/deepseek-v4-pro-0813", None),
    }
    ids = _insert_agents(migration_connection, cases, original)

    _run_upgrade(migration, migration_connection)

    rows = _rows(migration_connection)
    expected = {
        "sol_low": ("gpt-6.1-sol", "low"),
        "sol_medium": ("gpt-6.1-sol", "medium"),
        "sol_high": ("gpt-6.1-sol", "high"),
        # Curators get low, medium and high on GPT-6.1 Sol; xhigh becomes high.
        "sol_xhigh": ("gpt-6.1-sol", "high"),
        "sol_none": ("gpt-6.1-sol", None),
    }
    for name, (model_id, reasoning) in expected.items():
        row = rows[ids[name]]
        assert (row.model_id, row.model_reasoning) == (model_id, reasoning), name
        assert row.updated_at > original, name
    # System rows follow package config and deployment env at startup sync;
    # GPT-6 Astra keeps its own levels, including xhigh.
    for name in ("system_sol", "astra_xhigh", "external"):
        row = rows[ids[name]]
        assert (row.model_id, row.model_reasoning) == cases[name][1:], name
        assert row.updated_at == original, name

    moved_at = {ids[name]: rows[ids[name]].updated_at for name in expected}
    _run_upgrade(migration, migration_connection)
    rerun = _rows(migration_connection)
    assert all(rerun[agent_id].updated_at == stamp for agent_id, stamp in moved_at.items())


def test_gpt56_rows_reach_gpt61_sol_when_both_migrations_run(migration_connection):
    """A database still on GPT-5.6 runs the GPT-6 Sol step and then this one."""
    gpt6_sol = _load_migration_module(GPT6_SOL_MIGRATION_PATH, "gpt6_sol_custom_agent_migration")
    gpt61_sol = _load_migration_module(MIGRATION_PATH, "gpt61_sol_custom_agent_migration")
    cases = {
        "sol_disabled": ("private", "gpt-5.6-sol", "disabled"),
        "sol_xhigh": ("private", "gpt-5.6-sol", "xhigh"),
        "terra_minimal": ("private", "gpt-5.6-terra", "minimal"),
        "system_terra": ("system", "gpt-5.6-terra", "medium"),
    }
    ids = _insert_agents(migration_connection, cases, datetime(2026, 9, 1, tzinfo=timezone.utc))

    _run_upgrade(gpt6_sol, migration_connection)
    _run_upgrade(gpt61_sol, migration_connection)

    rows = _rows(migration_connection)
    assert (rows[ids["sol_disabled"]].model_id, rows[ids["sol_disabled"]].model_reasoning) == (
        "gpt-6.1-sol", "medium",
    )
    assert (rows[ids["sol_xhigh"]].model_id, rows[ids["sol_xhigh"]].model_reasoning) == (
        "gpt-6.1-sol", "high",
    )
    assert (rows[ids["terra_minimal"]].model_id, rows[ids["terra_minimal"]].model_reasoning) == (
        "gpt-6.1-sol", "low",
    )
    assert rows[ids["system_terra"]].model_id == "gpt-5.6-terra"


def test_downgrade_does_not_restore_the_retired_model_id(migration_connection):
    migration = _load_migration_module(MIGRATION_PATH, "gpt61_sol_custom_agent_migration")
    ids = _insert_agents(
        migration_connection,
        {"moved": ("private", "gpt-6.1-sol", "medium")},
        datetime(2026, 9, 29, tzinfo=timezone.utc),
    )

    migration.op = Operations(MigrationContext.configure(migration_connection))
    migration.downgrade()

    assert migration_connection.execute(
        text("SELECT model_id FROM agents WHERE id = :id"),
        {"id": ids["moved"]},
    ).scalar_one() == "gpt-6.1-sol"
