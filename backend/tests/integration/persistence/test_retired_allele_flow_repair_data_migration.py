"""PostgreSQL coverage for the v0.9.4 saved-flow repair."""

from __future__ import annotations

import importlib.util
import json
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

from alembic import command  # pyright: ignore[reportAttributeAccessIssue]
from alembic.config import Config  # pyright: ignore[reportMissingImports]
import pytest
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from src.models.sql.database import engine


BACKEND_ROOT = Path(__file__).resolve().parents[3]
MIGRATION_PATH = (
    BACKEND_ROOT
    / "alembic"
    / "versions"
    / "i6d7e8f9a0b1_remove_retired_allele_flow_selections.py"
)
FIXTURE_PATH = (
    BACKEND_ROOT
    / "tests"
    / "fixtures"
    / "flows"
    / "pre_catalog_change_allele_flow.json"
)
SPEC = importlib.util.spec_from_file_location("retired_allele_flow_data_repair", MIGRATION_PATH)
assert SPEC is not None and SPEC.loader is not None
migration = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(migration)


@pytest.fixture(scope="module", autouse=True)
def migrated_database():
    command.upgrade(Config(str(BACKEND_ROOT / "alembic.ini")), "head")


def test_repair_updates_only_matching_active_flow_and_preserves_audit_preimage():
    suffix = uuid4().hex
    user_id = 920_000_000 + int(suffix[:6], 16)
    affected_id = uuid4()
    unaffected_id = uuid4()
    inactive_id = uuid4()
    affected = json.loads(FIXTURE_PATH.read_text())
    unaffected = deepcopy(affected)
    unaffected_data = unaffected["nodes"][1]["data"]
    unaffected_data["validation_attachments"] = [
        unaffected_data["validation_attachments"][0]
    ]
    unaffected_data["validation_groups"] = [unaffected_data["validation_groups"][0]]

    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(
                sa.text(
                    "INSERT INTO users (user_id, auth_sub) VALUES (:user_id, :auth_sub)"
                ),
                {"user_id": user_id, "auth_sub": f"retired-flow-repair-{suffix}"},
            )
            insert = sa.text(
                """
                INSERT INTO curation_flows (
                    id, user_id, name, flow_definition, is_active
                ) VALUES (
                    :id, :user_id, :name, :definition, :is_active
                )
                """
            ).bindparams(sa.bindparam("definition", type_=JSONB))
            for flow_id, name, definition, is_active in (
                (affected_id, "affected", affected, True),
                (unaffected_id, "unaffected", unaffected, True),
                (inactive_id, "inactive", affected, False),
            ):
                connection.execute(
                    insert,
                    {
                        "id": flow_id,
                        "user_id": user_id,
                        "name": f"Retired flow repair {name} {suffix}",
                        "definition": definition,
                        "is_active": is_active,
                    },
                )

            counts = migration._migrate(connection)
            rows = {
                row.id: row.flow_definition
                for row in connection.execute(
                    sa.text(
                        "SELECT id, flow_definition FROM curation_flows "
                        "WHERE id = ANY(:ids)"
                    ),
                    {"ids": [affected_id, unaffected_id, inactive_id]},
                )
            }

            assert counts["active_matched_before"] == 1
            assert counts["active_matched_after"] == 0
            assert "allele_pending_envelope_validator" not in json.dumps(
                rows[affected_id]
            )
            assert rows[affected_id]["nodes"][1]["data"]["custom_instructions"] == (
                "Preserve this user customization."
            )
            assert rows[unaffected_id] == unaffected
            assert rows[inactive_id] == affected

            audit_rows = connection.execute(
                sa.text(
                    """
                    SELECT old_data, new_data, application_name
                    FROM audit_log
                    WHERE table_name = 'curation_flows'
                      AND operation = 'UPDATE'
                      AND row_id = :row_id
                    """
                ),
                {"row_id": str(affected_id)},
            ).mappings().all()
            assert len(audit_rows) == 1
            assert audit_rows[0]["old_data"]["flow_definition"] == affected
            assert audit_rows[0]["new_data"]["flow_definition"] == rows[affected_id]
            assert audit_rows[0]["application_name"] == (
                "alembic:i6d7e8f9a0b1:retired-allele-flow-selections"
            )

            migration._migrate(connection)
            audit_count = connection.execute(
                sa.text(
                    """
                    SELECT count(*)
                    FROM audit_log
                    WHERE table_name = 'curation_flows'
                      AND operation = 'UPDATE'
                      AND row_id = :row_id
                    """
                ),
                {"row_id": str(affected_id)},
            ).scalar_one()
            assert audit_count == 1
        finally:
            transaction.rollback()
