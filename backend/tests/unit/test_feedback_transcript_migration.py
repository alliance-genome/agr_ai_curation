"""Unit tests for the feedback transcript migration."""

from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import types

import sqlalchemy as sa


REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_PATH = (
    REPO_ROOT
    / "alembic"
    / "versions"
    / "e4f5a6b7c8d9_add_feedback_transcript_column.py"
)


class RecordingOp:
    """Capture Alembic operations for structural assertions."""

    def __init__(self) -> None:
        self.bind = object()
        self.created_tables: list[dict[str, object]] = []
        self.created_indexes: list[dict[str, object]] = []
        self.added_columns: list[tuple[str, sa.Column]] = []
        self.dropped_columns: list[tuple[str, str]] = []
        self.executed: list[str] = []

    def get_bind(self):
        return self.bind

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

    def drop_column(self, table_name, column_name):
        self.dropped_columns.append((table_name, column_name))

    def execute(self, statement):
        self.executed.append(str(statement))


class InspectorStub:
    def __init__(
        self,
        *,
        table_names: list[str],
        columns_by_table: dict[str, list[dict[str, object]]] | None = None,
    ) -> None:
        self._table_names = table_names
        self._columns_by_table = columns_by_table or {}

    def get_table_names(self):
        return list(self._table_names)

    def get_columns(self, table_name):
        return list(self._columns_by_table.get(table_name, []))


class FakeEnumCreator:
    def __init__(self) -> None:
        self.calls: list[tuple[object, bool]] = []

    def create(self, bind, checkfirst=False):
        self.calls.append((bind, checkfirst))


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


def _columns(table_call: dict[str, object]) -> dict[str, sa.Column]:
    return {
        element.name: element
        for element in table_call["elements"]
        if isinstance(element, sa.Column)
    }


def test_upgrade_recreates_feedback_reports_with_transcript_column_and_audit_triggers(monkeypatch):
    module = _load_migration_module(
        monkeypatch,
        module_name="feedback_transcript_migration_upgrade_create_table_test",
    )
    op_recorder = RecordingOp()
    enum_creator = FakeEnumCreator()
    module.op = op_recorder
    monkeypatch.setattr(module.sa, "inspect", lambda _bind: InspectorStub(table_names=[]))
    monkeypatch.setattr(
        module,
        "_processing_status_enum",
        lambda *, create_type: (
            enum_creator
            if create_type
            else sa.Enum(
                "pending",
                "processing",
                "completed",
                "failed",
                name="processingstatus",
            )
        ),
    )

    module.upgrade()

    assert [table["name"] for table in op_recorder.created_tables] == ["feedback_reports"]
    feedback_table = op_recorder.created_tables[0]
    feedback_columns = _columns(feedback_table)
    assert "conversation_transcript" in feedback_columns
    assert feedback_columns["conversation_transcript"].nullable is True
    assert {index["name"] for index in op_recorder.created_indexes} == {
        "ix_feedback_reports_session_id",
        "ix_feedback_reports_created_at",
        "ix_feedback_reports_processing_status",
    }
    assert enum_creator.calls == [(op_recorder.bind, True)]
    assert any(
        "CREATE TRIGGER audit_feedback_reports_insert" in statement
        for statement in op_recorder.executed
    )
    assert any(
        "CREATE TRIGGER audit_feedback_reports_update" in statement
        for statement in op_recorder.executed
    )
    assert any(
        "CREATE TRIGGER audit_feedback_reports_delete" in statement
        for statement in op_recorder.executed
    )
    assert op_recorder.added_columns == []


def test_upgrade_adds_transcript_column_and_refreshes_feedback_audit_triggers(monkeypatch):
    module = _load_migration_module(
        monkeypatch,
        module_name="feedback_transcript_migration_upgrade_add_column_test",
    )
    op_recorder = RecordingOp()
    module.op = op_recorder
    monkeypatch.setattr(
        module.sa,
        "inspect",
        lambda _bind: InspectorStub(
            table_names=["feedback_reports"],
            columns_by_table={"feedback_reports": [{"name": "id"}, {"name": "session_id"}]},
        ),
    )

    module.upgrade()

    assert op_recorder.created_tables == []
    assert op_recorder.added_columns[0][0] == "feedback_reports"
    assert op_recorder.added_columns[0][1].name == "conversation_transcript"
    assert any(
        "DROP TRIGGER IF EXISTS audit_feedback_reports_insert ON feedback_reports" in statement
        for statement in op_recorder.executed
    )
    assert any(
        "CREATE TRIGGER audit_feedback_reports_insert" in statement
        for statement in op_recorder.executed
    )
