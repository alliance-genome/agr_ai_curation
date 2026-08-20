"""PostgreSQL coverage for canonical validator-agent identity migration."""

from __future__ import annotations

from copy import deepcopy
import importlib.util
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
    / "4d5e6f7a8b9c_canonicalize_entity_validator_agent_ids.py"
)
SPEC = importlib.util.spec_from_file_location(
    "validator_agent_identity_migration",
    MIGRATION_PATH,
)
assert SPEC is not None and SPEC.loader is not None
migration = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(migration)


@pytest.fixture(scope="module", autouse=True)
def migrated_database():
    command.upgrade(Config(str(BACKEND_ROOT / "alembic.ini")), "head")


def _flow_definition(agent_ids: list[str]) -> dict:
    return {
        "version": "1.1",
        "entry_node_id": "gene",
        "nodes": [
            {
                "id": alias,
                "type": "agent",
                "position": {"x": index * 100, "y": 0},
                "data": {
                    "agent_id": agent_id,
                    "agent_display_name": f"{alias.title()} validator",
                    "output_key": f"{alias}_output",
                    "domain_pack_id": alias,
                    "adapter_key": alias,
                    "entity_type": alias,
                    "description": f"Validate one {alias}",
                    "validation_attachments": [
                        {
                            "attachment_id": f"{alias}:identity",
                            "domain_pack_id": alias,
                            "validator_agent_id": alias,
                        },
                        {
                            "attachment_id": f"{alias}:canonical",
                            "domain_pack_id": alias,
                            "validator_agent_id": f"{alias}_validation",
                        },
                    ],
                    "unrelated": {"agent_id": alias},
                },
            }
            for index, (alias, agent_id) in enumerate(
                zip(
                    ("gene", "allele", "disease", "chemical"),
                    agent_ids,
                    strict=True,
                )
            )
        ],
        "edges": [],
        "prose": "gene allele disease chemical",
    }


def _insert_user(connection: sa.Connection, user_id: int, suffix: str) -> None:
    connection.execute(
        sa.text("INSERT INTO users (user_id, auth_sub) VALUES (:id, :sub)"),
        {"id": user_id, "sub": f"validator-identity-migration-{suffix}"},
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
            INSERT INTO curation_flows (
                id, user_id, name, flow_definition, is_active
            ) VALUES (
                :id, :user_id, :name, :definition, :active
            )
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


def test_upgrade_rewrites_exact_agent_references_and_is_idempotent():
    suffix = uuid4().hex
    user_id = 930_000_000
    project_id = uuid4()
    active_flow_id = uuid4()
    inactive_flow_id = uuid4()
    active_definition = _flow_definition(
        ["gene", "allele", "disease", "chemical"]
    )
    inactive_definition = _flow_definition(
        ["gene_validation", "allele", "disease_validation", "chemical"]
    )
    expected_active = deepcopy(active_definition)
    expected_inactive = deepcopy(inactive_definition)
    for definition in (expected_active, expected_inactive):
        for node in definition["nodes"]:
            alias = node["id"]
            node["data"]["agent_id"] = f"{alias}_validation"
            node["data"]["validation_attachments"][0][
                "validator_agent_id"
            ] = f"{alias}_validation"

    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            _insert_user(connection, user_id, suffix)
            connection.execute(
                sa.text("INSERT INTO projects (id, name) VALUES (:id, :name)"),
                {"id": project_id, "name": f"Validator migration {suffix}"},
            )
            _insert_flow(
                connection,
                flow_id=active_flow_id,
                user_id=user_id,
                name=f"Active validator migration {suffix}",
                definition=active_definition,
                active=True,
            )
            _insert_flow(
                connection,
                flow_id=inactive_flow_id,
                user_id=user_id,
                name=f"Inactive validator migration {suffix}",
                definition=inactive_definition,
                active=False,
            )

            aliases = ("gene", "allele", "disease", "chemical")
            agent_insert = sa.text(
                """
                INSERT INTO agents (
                    agent_key, user_id, name, instructions, model_id,
                    visibility, project_id, template_source,
                    group_rules_component, is_active
                ) VALUES (
                    :agent_key, :user_id, :name, 'test', 'test-model',
                    :visibility, :project_id, :template_source,
                    :group_rules_component, :is_active
                )
                """
            )
            custom_agent_keys: list[str] = []
            for index, alias in enumerate(aliases):
                agent_key = f"ca_{uuid4()}"
                custom_agent_keys.append(agent_key)
                visibility = "private" if index % 2 == 0 else "project"
                connection.execute(
                    agent_insert,
                    {
                        "agent_key": agent_key,
                        "user_id": user_id,
                        "name": f"{alias.title()} custom {suffix}",
                        "visibility": visibility,
                        "project_id": project_id if visibility == "project" else None,
                        "template_source": alias,
                        "group_rules_component": alias,
                        "is_active": index % 2 == 0,
                    },
                )
            system_control_key = f"system-alias-control-{suffix}"
            connection.execute(
                agent_insert,
                {
                    "agent_key": system_control_key,
                    "user_id": None,
                    "name": f"System control {suffix}",
                    "visibility": "system",
                    "project_id": None,
                    "template_source": "gene",
                    "group_rules_component": "gene",
                    "is_active": True,
                },
            )

            prompt_ids: dict[str, UUID] = {}
            file_output_ids: dict[str, UUID] = {}
            for index, alias in enumerate(aliases):
                prompt_id = uuid4()
                prompt_ids[alias] = prompt_id
                connection.execute(
                    sa.text(
                        """
                        INSERT INTO prompt_templates (
                            id, agent_name, prompt_type, content, version, is_active
                        ) VALUES (
                            :id, :agent_name, 'system', :content, :version, true
                        )
                        """
                    ),
                    {
                        "id": prompt_id,
                        "agent_name": alias,
                        "content": f"Legacy {alias} prompt {suffix}",
                        "version": 900 + index,
                    },
                )
                connection.execute(
                    sa.text(
                        """
                        INSERT INTO prompt_execution_log (
                            id, prompt_template_id, agent_name,
                            prompt_type, prompt_version
                        ) VALUES (
                            :id, :prompt_template_id, :agent_name,
                            'system', :prompt_version
                        )
                        """
                    ),
                    {
                        "id": uuid4(),
                        "prompt_template_id": prompt_id,
                        "agent_name": alias,
                        "prompt_version": 900 + index,
                    },
                )
                file_output_id = uuid4()
                file_output_ids[alias] = file_output_id
                connection.execute(
                    sa.text(
                        """
                        INSERT INTO file_outputs (
                            id, filename, file_path, file_type, file_size,
                            curator_id, session_id, trace_id, agent_name
                        ) VALUES (
                            :id, :filename, :file_path, 'json', 1,
                            :curator_id, :session_id, :trace_id, :agent_name
                        )
                        """
                    ),
                    {
                        "id": file_output_id,
                        "filename": f"{alias}-{suffix}.json",
                        "file_path": f"/tmp/{alias}-{suffix}.json",
                        "curator_id": f"curator-{suffix}",
                        "session_id": f"session-{suffix}",
                        "trace_id": uuid4().hex,
                        "agent_name": alias,
                    },
                )

            migration._migrate(connection)

            flows = {
                row.id: row.flow_definition
                for row in connection.execute(
                    sa.text(
                        """
                        SELECT id, flow_definition
                        FROM curation_flows
                        WHERE id = ANY(:ids)
                        """
                    ),
                    {"ids": [active_flow_id, inactive_flow_id]},
                )
            }
            assert flows[active_flow_id] == expected_active
            assert flows[inactive_flow_id] == expected_inactive

            custom_rows = connection.execute(
                sa.text(
                    """
                    SELECT agent_key, template_source, group_rules_component, is_active
                    FROM agents
                    WHERE agent_key = ANY(:agent_keys)
                    ORDER BY agent_key
                    """
                ),
                {"agent_keys": custom_agent_keys},
            ).mappings().all()
            assert {
                row["template_source"] for row in custom_rows
            } == {f"{alias}_validation" for alias in aliases}
            assert {
                row["group_rules_component"] for row in custom_rows
            } == {f"{alias}_validation" for alias in aliases}
            assert {row["is_active"] for row in custom_rows} == {True, False}

            system_control = connection.execute(
                sa.text(
                    """
                    SELECT template_source, group_rules_component
                    FROM agents
                    WHERE agent_key = :agent_key
                    """
                ),
                {"agent_key": system_control_key},
            ).mappings().one()
            assert system_control == {
                "template_source": "gene",
                "group_rules_component": "gene",
            }

            prompt_rows = connection.execute(
                sa.text(
                    """
                    SELECT id, agent_name, is_active
                    FROM prompt_templates
                    WHERE id = ANY(:ids)
                    """
                ),
                {"ids": list(prompt_ids.values())},
            ).mappings().all()
            assert {row["agent_name"] for row in prompt_rows} == {
                f"{alias}_validation" for alias in aliases
            }
            assert all(row["is_active"] is False for row in prompt_rows)
            assert connection.execute(
                sa.text(
                    """
                    SELECT count(*)
                    FROM prompt_templates
                    WHERE agent_name IN ('gene', 'allele', 'disease', 'chemical')
                    """
                )
            ).scalar_one() == 0

            prompt_log_names = connection.execute(
                sa.text(
                    """
                    SELECT agent_name
                    FROM prompt_execution_log
                    WHERE prompt_template_id = ANY(:ids)
                    """
                ),
                {"ids": list(prompt_ids.values())},
            ).scalars().all()
            assert set(prompt_log_names) == {
                f"{alias}_validation" for alias in aliases
            }
            file_output_names = connection.execute(
                sa.text(
                    """
                    SELECT agent_name
                    FROM file_outputs
                    WHERE id = ANY(:ids)
                    """
                ),
                {"ids": list(file_output_ids.values())},
            ).scalars().all()
            assert set(file_output_names) == {
                f"{alias}_validation" for alias in aliases
            }

            first_audit_count = connection.execute(
                sa.text(
                    """
                    SELECT count(*)
                    FROM audit_log
                    WHERE table_name = 'curation_flows'
                      AND operation = 'UPDATE'
                      AND row_id = ANY(:row_ids)
                    """
                ),
                {"row_ids": [str(active_flow_id), str(inactive_flow_id)]},
            ).scalar_one()
            assert first_audit_count == 2

            migration._migrate(connection)

            second_audit_count = connection.execute(
                sa.text(
                    """
                    SELECT count(*)
                    FROM audit_log
                    WHERE table_name = 'curation_flows'
                      AND operation = 'UPDATE'
                      AND row_id = ANY(:row_ids)
                    """
                ),
                {"row_ids": [str(active_flow_id), str(inactive_flow_id)]},
            ).scalar_one()
            assert second_audit_count == first_audit_count
        finally:
            transaction.rollback()
