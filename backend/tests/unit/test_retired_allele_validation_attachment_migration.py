"""Regression test for the retired allele attachment Alembic repair."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from src.lib.packages.persisted_flow_migration_loader import (
    load_persisted_flow_migration_catalog,
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
_REAPPLY_MIGRATION_PATH = (
    Path(__file__).parents[2]
    / "alembic"
    / "versions"
    / "i6j7k8l9m0n1_reapply_retired_flow_attachment_repair.py"
)
_REAPPLY_SPEC = importlib.util.spec_from_file_location(
    "reapply_retired_flow_attachment_repair",
    _REAPPLY_MIGRATION_PATH,
)
assert _REAPPLY_SPEC and _REAPPLY_SPEC.loader
reapply_migration = importlib.util.module_from_spec(_REAPPLY_SPEC)
_REAPPLY_SPEC.loader.exec_module(reapply_migration)
SHIPPED_MIGRATION = next(
    item
    for item in load_persisted_flow_migration_catalog().migrations
    if item.migration_id == migration.FLOW_MIGRATION_ID
)
RETIRED_ATTACHMENT_IDS = {
    attachment.attachment_id for attachment in SHIPPED_MIGRATION.retired_attachments
}


def _definition(*, agent_id: str = "allele_extractor") -> dict:
    attachment_id = sorted(RETIRED_ATTACHMENT_IDS)[0]
    return {
        "nodes": [
            {
                "id": "extract",
                "data": {
                    "agent_id": agent_id,
                    "validation_attachments": [
                        {
                            "attachment_id": attachment_id,
                            "validator_binding_id": SHIPPED_MIGRATION.retired_binding_id,
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
        self.calls = []

    def execute(self, statement, parameters=None):
        sql = str(statement)
        self.calls.append((sql, parameters))
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
    definition = _definition(agent_id="ca_alliance_allele")
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
    verification_sql, verification_parameters = next(
        call for call in connection.calls if "SELECT count(*)" in call[0]
    )
    assert "data,agent_id" not in verification_sql
    assert set(verification_parameters) == {"retired_attachment_ids"}


def test_upgrade_is_noop_when_active_packages_do_not_declare_repair(monkeypatch):
    monkeypatch.setattr(
        migration,
        "load_persisted_flow_migration_catalog",
        lambda: SimpleNamespace(migrations=()),
    )
    monkeypatch.setattr(
        migration.op,
        "get_bind",
        lambda: (_ for _ in ()).throw(AssertionError("database should not be touched")),
    )

    migration.upgrade()


def test_post_head_reconciliation_reapplies_the_same_idempotent_repair(monkeypatch):
    connection = object()
    calls = []
    monkeypatch.setattr(reapply_migration.op, "get_bind", lambda: connection)
    monkeypatch.setattr(
        reapply_migration,
        "apply_persisted_flow_migration",
        lambda bind, flow_migration: calls.append((bind, flow_migration)),
    )

    reapply_migration.upgrade()

    assert reapply_migration.down_revision == "h5c6d7e8f9a0"
    assert calls == [(connection, SHIPPED_MIGRATION)]
