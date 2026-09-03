"""Regression test for the retired allele attachment Alembic repair."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from uuid import uuid4

from src.lib.flows.persisted_flow_migrations import (
    RETIRED_ALLELE_PENDING_VALIDATOR_ATTACHMENT_IDS,
    RETIRED_ALLELE_PENDING_VALIDATOR_BINDING_ID,
)


_MIGRATION_PATH = (
    Path(__file__).parents[2]
    / "alembic"
    / "versions"
    / "e2f3a4b5c6d7_remove_retired_allele_validation_attachments.py"
)
_SPEC = importlib.util.spec_from_file_location(
    "retired_allele_validation_attachment_migration",
    _MIGRATION_PATH,
)
assert _SPEC and _SPEC.loader
migration = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(migration)


def _definition() -> dict:
    attachment_id = sorted(RETIRED_ALLELE_PENDING_VALIDATOR_ATTACHMENT_IDS)[0]
    return {
        "nodes": [
            {
                "id": "extract",
                "data": {
                    "agent_id": "allele_extractor",
                    "validation_attachments": [
                        {
                            "attachment_id": attachment_id,
                            "validator_binding_id": RETIRED_ALLELE_PENDING_VALIDATOR_BINDING_ID,
                        },
                        {"attachment_id": "current"},
                    ],
                },
            }
        ],
        "edges": [],
    }


class _Result:
    def __init__(self, *, rows=(), rowcount=-1, scalar=None):
        self._rows = list(rows)
        self.rowcount = rowcount
        self._scalar = scalar

    def mappings(self):
        return self

    def __iter__(self):
        return iter(self._rows)

    def scalar_one(self):
        return self._scalar


class _Connection:
    def __init__(self, flow_id, definition):
        self.flow_id = flow_id
        self.definition = definition
        self.updates = []

    def execute(self, statement, parameters=None):
        sql = str(statement)
        if "SELECT id, flow_definition" in sql:
            return _Result(rows=[{"id": self.flow_id, "flow_definition": self.definition}])
        if "UPDATE curation_flows" in sql:
            self.updates.append(parameters)
            return _Result(rowcount=1)
        if "SELECT count(*)" in sql:
            return _Result(scalar=0)
        raise AssertionError(f"Unexpected migration SQL: {sql}")


def test_upgrade_uses_guarded_idempotent_definition_update(monkeypatch):
    flow_id = uuid4()
    definition = _definition()
    connection = _Connection(flow_id, definition)
    monkeypatch.setattr(migration.op, "get_bind", lambda: connection)

    migration.upgrade()

    assert migration.down_revision == "d1e2f3a4b5c6"
    assert len(connection.updates) == 1
    update = connection.updates[0]
    assert update["flow_id"] == flow_id
    assert update["expected_definition"] == definition
    assert update["updated_definition"]["nodes"][0]["data"][
        "validation_attachments"
    ] == [{"attachment_id": "current"}]
