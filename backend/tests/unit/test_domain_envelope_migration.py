"""Unit tests for the domain envelope persistence migration."""

from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import types

import sqlalchemy as sa


REPO_ROOT = Path(__file__).resolve().parents[3]
MIGRATION_PATH = (
    REPO_ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "j9k0l1m2n3o4_add_domain_envelope_persistence.py"
)


class RecordingOp:
    """Capture Alembic operations for structural assertions."""

    def __init__(self) -> None:
        self.created_tables: list[dict[str, object]] = []
        self.created_indexes: list[dict[str, object]] = []
        self.added_columns: list[tuple[str, sa.Column]] = []
        self.created_foreign_keys: list[dict[str, object]] = []
        self.created_check_constraints: list[dict[str, object]] = []
        self.executed: list[str] = []
        self.dropped_indexes: list[dict[str, object]] = []
        self.dropped_constraints: list[dict[str, object]] = []
        self.dropped_columns: list[tuple[str, str]] = []
        self.dropped_tables: list[str] = []

    def create_table(self, name, *elements, **kwargs):
        self.created_tables.append(
            {"name": name, "elements": elements, "kwargs": kwargs}
        )

    def create_index(self, name, table_name, columns, unique=False, **kwargs):
        self.created_indexes.append(
            {
                "name": name,
                "table_name": table_name,
                "columns": columns,
                "unique": unique,
                "kwargs": kwargs,
            }
        )

    def add_column(self, table_name, column):
        self.added_columns.append((table_name, column))

    def create_foreign_key(
        self,
        name,
        source_table,
        referent_table,
        local_cols,
        remote_cols,
        **kwargs,
    ):
        self.created_foreign_keys.append(
            {
                "name": name,
                "source_table": source_table,
                "referent_table": referent_table,
                "local_cols": local_cols,
                "remote_cols": remote_cols,
                "kwargs": kwargs,
            }
        )

    def create_check_constraint(self, name, table_name, condition):
        self.created_check_constraints.append(
            {"name": name, "table_name": table_name, "condition": condition}
        )

    def execute(self, statement):
        self.executed.append(str(statement))

    def drop_index(self, name, table_name=None, **kwargs):
        self.dropped_indexes.append(
            {"name": name, "table_name": table_name, "kwargs": kwargs}
        )

    def drop_constraint(self, name, table_name, type_=None):
        self.dropped_constraints.append(
            {"name": name, "table_name": table_name, "type": type_}
        )

    def drop_column(self, table_name, column_name):
        self.dropped_columns.append((table_name, column_name))

    def drop_table(self, name):
        self.dropped_tables.append(name)


def _load_migration_module(monkeypatch, *, module_name: str):
    dummy_alembic = types.ModuleType("alembic")
    dummy_alembic.op = object()
    monkeypatch.setitem(sys.modules, "alembic", dummy_alembic)

    spec = spec_from_file_location(module_name, MIGRATION_PATH)
    assert spec is not None
    assert spec.loader is not None

    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _table_by_name(op_recorder: RecordingOp, table_name: str) -> dict[str, object]:
    for table in op_recorder.created_tables:
        if table["name"] == table_name:
            return table
    raise AssertionError(f"Missing create_table call for {table_name}")


def _columns(table_call: dict[str, object]) -> dict[str, sa.Column]:
    return {
        element.name: element
        for element in table_call["elements"]
        if isinstance(element, sa.Column)
    }


def _constraint_names(table_call: dict[str, object]) -> set[str]:
    names: set[str] = set()
    for element in table_call["elements"]:
        if isinstance(
            element,
            (sa.CheckConstraint, sa.UniqueConstraint),
        ) and element.name:
            names.add(element.name)
    return names


def test_upgrade_creates_domain_envelope_tables_indexes_and_projection_refs(monkeypatch):
    module = _load_migration_module(
        monkeypatch,
        module_name="domain_envelope_migration_upgrade_test",
    )
    op_recorder = RecordingOp()
    module.op = op_recorder

    module.upgrade()

    assert [table["name"] for table in op_recorder.created_tables] == [
        "domain_envelopes",
        "domain_envelope_objects",
        "domain_validation_findings",
        "domain_envelope_history",
        "domain_envelope_projection_index",
    ]

    envelope_columns = _columns(_table_by_name(op_recorder, "domain_envelopes"))
    assert {
        "envelope_id",
        "revision",
        "project_key",
        "domain_pack_key",
        "domain_pack_version",
        "status",
        "document_id",
        "session_id",
        "flow_run_id",
        "schema_provider",
        "schema_ref_json",
        "object_model_ref_json",
        "model_field_ref_json",
        "envelope_json",
        "created_at",
        "updated_at",
        "checkpointed_at",
    } == set(envelope_columns)
    assert "ck_domain_envelopes_revision" in _constraint_names(
        _table_by_name(op_recorder, "domain_envelopes")
    )

    object_columns = _columns(_table_by_name(op_recorder, "domain_envelope_objects"))
    assert {
        "envelope_id",
        "object_id",
        "pending_ref_id",
        "envelope_revision",
        "object_type",
        "status",
        "validation_state",
        "payload_json",
        "object_json",
    }.issubset(object_columns)
    assert "uq_domain_envelope_objects_current" in _constraint_names(
        _table_by_name(op_recorder, "domain_envelope_objects")
    )

    finding_columns = _columns(_table_by_name(op_recorder, "domain_validation_findings"))
    assert {
        "envelope_id",
        "finding_id",
        "envelope_revision",
        "object_id",
        "field_path",
        "severity",
        "status",
        "finding_json",
    }.issubset(finding_columns)

    projection_columns = _columns(
        _table_by_name(op_recorder, "domain_envelope_projection_index")
    )
    assert {
        "envelope_id",
        "object_id",
        "envelope_revision",
        "projection_type",
        "projection_key",
        "projection_json",
    }.issubset(projection_columns)
    assert "uq_domain_projection_index_key" in _constraint_names(
        _table_by_name(op_recorder, "domain_envelope_projection_index")
    )

    index_map = {index["name"]: index for index in op_recorder.created_indexes}
    assert {
        "ix_domain_envelopes_document",
        "ix_domain_envelopes_session",
        "ix_domain_envelopes_flow_run",
        "ix_domain_envelopes_domain_pack_status",
        "ix_domain_envelope_objects_lookup",
        "ix_domain_validation_findings_lookup",
        "ix_domain_projection_index_lookup",
        "ix_curation_candidates_domain_projection",
    }.issubset(index_map)
    assert index_map["ix_domain_envelopes_session"]["kwargs"]["postgresql_where"].text == (
        "session_id IS NOT NULL"
    )
    assert index_map["ix_curation_candidates_domain_projection"]["kwargs"][
        "postgresql_where"
    ].text == "envelope_id IS NOT NULL"

    assert op_recorder.executed == [
        "CREATE INDEX ix_domain_envelope_history_time "
        "ON domain_envelope_history (envelope_id, occurred_at DESC)"
    ]

    added_columns = {
        column.name: table_name for table_name, column in op_recorder.added_columns
    }
    assert added_columns == {
        "envelope_id": "curation_candidates",
        "object_id": "curation_candidates",
        "envelope_revision": "curation_candidates",
    }
    assert op_recorder.created_foreign_keys[0]["name"] == (
        "fk_curation_candidates_envelope_id_domain_envelopes"
    )
    assert op_recorder.created_check_constraints[0]["name"] == (
        "ck_curation_candidates_domain_projection_ref"
    )


def test_downgrade_drops_domain_envelope_tables_in_reverse_order(monkeypatch):
    module = _load_migration_module(
        monkeypatch,
        module_name="domain_envelope_migration_downgrade_test",
    )
    op_recorder = RecordingOp()
    module.op = op_recorder

    module.downgrade()

    assert [item["name"] for item in op_recorder.dropped_indexes] == [
        "ix_curation_candidates_domain_projection",
        "ix_domain_projection_index_lookup",
        "ix_domain_envelope_history_time",
        "ix_domain_validation_findings_lookup",
        "ix_domain_envelope_objects_lookup",
        "ix_domain_envelopes_domain_pack_status",
        "ix_domain_envelopes_flow_run",
        "ix_domain_envelopes_session",
        "ix_domain_envelopes_document",
    ]
    assert op_recorder.dropped_columns == [
        ("curation_candidates", "envelope_revision"),
        ("curation_candidates", "object_id"),
        ("curation_candidates", "envelope_id"),
    ]
    assert op_recorder.dropped_tables == [
        "domain_envelope_projection_index",
        "domain_envelope_history",
        "domain_validation_findings",
        "domain_envelope_objects",
        "domain_envelopes",
    ]
