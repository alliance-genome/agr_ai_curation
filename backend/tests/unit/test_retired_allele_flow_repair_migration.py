"""Unit tests for the retired allele saved-flow data repair."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_MIGRATION_PATH = (
    _BACKEND_ROOT
    / "alembic"
    / "versions"
    / "i6d7e8f9a0b1_remove_retired_allele_flow_selections.py"
)
_FIXTURE_PATH = (
    _BACKEND_ROOT / "tests" / "fixtures" / "flows" / "pre_catalog_change_allele_flow.json"
)
_SPEC = importlib.util.spec_from_file_location("retired_allele_flow_repair", _MIGRATION_PATH)
assert _SPEC is not None and _SPEC.loader is not None
migration = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(migration)


class _Result:
    def __init__(self, *, scalar=None, rows=None, rowcount=0):
        self._scalar = scalar
        self._rows = rows or []
        self.rowcount = rowcount

    def scalar_one(self):
        return self._scalar

    def mappings(self):
        return self

    def all(self):
        return self._rows


class _Connection:
    def __init__(self, definition: dict, *, audit_enabled: bool = True):
        self.definition = definition
        self.audit_enabled = audit_enabled
        self.update_parameters = []

    def execute(self, statement, parameters=None):
        sql = str(statement)
        if "SELECT EXISTS" in sql:
            return _Result(scalar=self.audit_enabled)
        if "SELECT id, flow_definition" in sql:
            return _Result(rows=[{"id": "flow-1", "flow_definition": self.definition}])
        if "SELECT flow_definition" in sql:
            return _Result(rows=[{"flow_definition": self.definition}])
        if sql.lstrip().startswith("UPDATE curation_flows"):
            assert isinstance(parameters, dict)
            self.update_parameters.append(parameters)
            self.definition = parameters["new_definition"]
            return _Result(rowcount=1)
        return _Result()


def test_data_repair_is_guarded_and_idempotent():
    original = json.loads(_FIXTURE_PATH.read_text())
    first_connection = _Connection(original)

    first_counts = migration._migrate(first_connection)

    assert first_counts["active_repaired"] == 1
    assert first_counts["active_matched_before"] == 1
    assert first_counts["active_matched_after"] == 0
    assert len(first_connection.update_parameters) == 1
    parameters = first_connection.update_parameters[0]
    assert parameters["old_definition"] == original
    assert "allele_pending_envelope_validator" not in json.dumps(
        parameters["new_definition"]
    )

    second_connection = _Connection(parameters["new_definition"])
    second_counts = migration._migrate(second_connection)

    assert second_counts["active_repaired"] == 0
    assert second_connection.update_parameters == []


def test_data_repair_requires_recoverable_audit_preimage():
    connection = _Connection(json.loads(_FIXTURE_PATH.read_text()), audit_enabled=False)

    with pytest.raises(RuntimeError, match="audit_curation_flows_update"):
        migration._migrate(connection)
