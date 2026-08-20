"""PostgreSQL coverage for canonical validator-agent identities."""

from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
import json
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
    / "4d5e6f7a8b9c_canonicalize_validator_agent_ids.py"
)
ALIASES = ("gene", "allele", "disease", "chemical")
CANONICAL = tuple(f"{alias}_validation" for alias in ALIASES)


def _load_migration_module():
    spec = spec_from_file_location("validator_agent_identity_migration", MIGRATION_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def migration_connection():
    engine = create_engine(os.environ["DATABASE_URL"])
    schema_name = f"validator_identity_{uuid4().hex}"

    try:
        with engine.connect() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema_name}"'))
            connection.execute(text(f'SET search_path TO "{schema_name}"'))
            connection.execute(
                text(
                    """
                    CREATE TABLE agents (
                        id uuid PRIMARY KEY,
                        agent_key varchar(100) NOT NULL UNIQUE,
                        visibility varchar(20) NOT NULL,
                        template_source varchar(100),
                        group_rules_component varchar(100),
                        is_active boolean NOT NULL,
                        supervisor_enabled boolean NOT NULL
                    );
                    CREATE TABLE curation_flows (
                        id uuid PRIMARY KEY,
                        flow_definition jsonb NOT NULL
                    );
                    CREATE TABLE prompt_templates (
                        id uuid PRIMARY KEY,
                        agent_name varchar(100) NOT NULL,
                        prompt_type varchar(50) NOT NULL,
                        group_id varchar(20),
                        version integer NOT NULL,
                        is_active boolean NOT NULL
                    );
                    CREATE TABLE prompt_execution_log (
                        id uuid PRIMARY KEY,
                        agent_name varchar(100) NOT NULL
                    )
                    """
                )
            )
            connection.commit()
            try:
                yield connection
            finally:
                connection.rollback()
                connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
                connection.commit()
    finally:
        engine.dispose()


def _agent_row(
    connection,
    agent_key: str,
    *,
    visibility: str = "system",
    template_source: str | None = None,
    group_rules_component: str | None = None,
    active: bool = True,
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO agents (
                id, agent_key, visibility, template_source,
                group_rules_component, is_active, supervisor_enabled
            ) VALUES (
                :id, :agent_key, :visibility, :template_source,
                :group_rules_component, :active, true
            )
            """
        ),
        {
            "id": uuid4(),
            "agent_key": agent_key,
            "visibility": visibility,
            "template_source": template_source,
            "group_rules_component": group_rules_component,
            "active": active,
        },
    )


def test_upgrade_migrates_all_validator_references_and_is_idempotent(
    migration_connection,
):
    _agent_row(migration_connection, "gene")
    _agent_row(migration_connection, "gene_validation")
    _agent_row(migration_connection, "allele")
    _agent_row(migration_connection, "disease", active=False)
    _agent_row(migration_connection, "chemical")
    _agent_row(
        migration_connection,
        "private_gene_copy",
        visibility="private",
        template_source="gene",
        group_rules_component="allele",
    )
    _agent_row(
        migration_connection,
        "project_disease_copy",
        visibility="project",
        template_source="disease",
        group_rules_component="chemical",
    )

    flow_id = uuid4()
    flow_definition = {
        "version": "1.1",
        "domain_pack_id": "gene",
        "nodes": [
            {"id": alias, "data": {"agent_id": alias, "entity_type": alias}}
            for alias in ALIASES
        ]
        + [{"id": "canonical", "data": {"agent_id": "gene_validation"}}],
    }
    migration_connection.execute(
        text(
            "INSERT INTO curation_flows (id, flow_definition) "
            "VALUES (:id, CAST(:definition AS jsonb))"
        ),
        {"id": flow_id, "definition": json.dumps(flow_definition)},
    )

    legacy_gene_prompt_id = uuid4()
    for prompt_id, agent_name, version, active in (
        (legacy_gene_prompt_id, "gene", 1, True),
        (uuid4(), "gene_validation", 1, True),
        (uuid4(), "allele", 2, True),
    ):
        migration_connection.execute(
            text(
                """
                INSERT INTO prompt_templates (
                    id, agent_name, prompt_type, group_id, version, is_active
                ) VALUES (:id, :agent_name, 'system', NULL, :version, :active)
                """
            ),
            {
                "id": prompt_id,
                "agent_name": agent_name,
                "version": version,
                "active": active,
            },
        )
    execution_log_id = uuid4()
    migration_connection.execute(
        text(
            "INSERT INTO prompt_execution_log (id, agent_name) "
            "VALUES (:id, 'chemical')"
        ),
        {"id": execution_log_id},
    )
    migration_connection.commit()

    migration = _load_migration_module()
    migration.op = Operations(MigrationContext.configure(migration_connection))
    migration.upgrade()
    migration.upgrade()

    migrated_flow = migration_connection.execute(
        text("SELECT flow_definition FROM curation_flows WHERE id = :id"),
        {"id": flow_id},
    ).scalar_one()
    assert [node["data"]["agent_id"] for node in migrated_flow["nodes"]] == [
        *CANONICAL,
        "gene_validation",
    ]
    assert migrated_flow["domain_pack_id"] == "gene"
    assert migrated_flow["nodes"][0]["data"]["entity_type"] == "gene"

    custom_rows = {
        row.agent_key: row
        for row in migration_connection.execute(
            text(
                """
                SELECT agent_key, template_source, group_rules_component
                FROM agents
                WHERE visibility IN ('private', 'project')
                """
            )
        )
    }
    assert custom_rows["private_gene_copy"].template_source == "gene_validation"
    assert custom_rows["private_gene_copy"].group_rules_component == "allele_validation"
    assert custom_rows["project_disease_copy"].template_source == "disease_validation"
    assert custom_rows["project_disease_copy"].group_rules_component == "chemical_validation"

    system_rows = {
        row.agent_key: row
        for row in migration_connection.execute(
            text(
                """
                SELECT agent_key, is_active, supervisor_enabled
                FROM agents WHERE visibility = 'system'
                """
            )
        )
    }
    assert all(agent_id in system_rows for agent_id in CANONICAL)
    assert system_rows["gene"].is_active is False
    assert system_rows["gene"].supervisor_enabled is False
    assert {key for key in system_rows if key in ALIASES} == {"gene"}

    legacy_gene_prompt = migration_connection.execute(
        text("SELECT agent_name, is_active FROM prompt_templates WHERE id = :id"),
        {"id": legacy_gene_prompt_id},
    ).one()
    assert legacy_gene_prompt.agent_name == "gene"
    assert legacy_gene_prompt.is_active is False
    assert migration_connection.execute(
        text("SELECT agent_name FROM prompt_templates WHERE version = 2")
    ).scalar_one() == "allele_validation"
    assert migration_connection.execute(
        text("SELECT agent_name FROM prompt_execution_log WHERE id = :id"),
        {"id": execution_log_id},
    ).scalar_one() == "chemical_validation"


def test_downgrade_does_not_restore_retired_aliases(migration_connection):
    migration = _load_migration_module()
    migration.op = Operations(MigrationContext.configure(migration_connection))
    migration.downgrade()
