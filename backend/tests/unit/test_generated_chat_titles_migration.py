"""Unit tests for the generated chat-title migration."""

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
    / "y8z9a0b1c2d3_add_generated_chat_titles.py"
)


class RecordingOp:
    def __init__(self) -> None:
        self.added_columns: list[tuple[str, sa.Column]] = []
        self.dropped_columns: list[tuple[str, str]] = []
        self.created_constraints: list[tuple[str, str, str]] = []
        self.dropped_constraints: list[tuple[str, str, str | None]] = []
        self.executed: list[str] = []

    def add_column(self, table_name, column):
        self.added_columns.append((table_name, column))

    def drop_column(self, table_name, column_name):
        self.dropped_columns.append((table_name, column_name))

    def create_check_constraint(self, name, table_name, condition):
        self.created_constraints.append((name, table_name, condition))

    def drop_constraint(self, name, table_name, type_=None):
        self.dropped_constraints.append((name, table_name, type_))

    def execute(self, statement):
        self.executed.append(str(statement))


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


def test_upgrade_adds_generated_title_metadata_and_refresh_logic(monkeypatch):
    module = _load_migration_module(
        monkeypatch,
        module_name="generated_chat_titles_migration_upgrade_test",
    )
    op_recorder = RecordingOp()
    module.op = op_recorder

    module.upgrade()

    assert op_recorder.added_columns[0][0] == "chat_sessions"
    assert op_recorder.added_columns[0][1].name == "generated_title"
    assert op_recorder.added_columns[0][1].nullable is True
    assert op_recorder.created_constraints == [
        (
            "ck_chat_sessions_generated_title_not_empty",
            "chat_sessions",
            "generated_title IS NULL OR btrim(generated_title) <> ''",
        )
    ]
    assert any(
        "coalesce(title, generated_title, '')" in statement
        for statement in op_recorder.executed
    )
    assert any(
        "DROP TRIGGER IF EXISTS trg_chat_sessions_refresh_rollup ON chat_sessions" in statement
        for statement in op_recorder.executed
    )
    assert any(
        "AFTER INSERT OR UPDATE OF title, generated_title" in statement
        for statement in op_recorder.executed
    )


def test_downgrade_removes_generated_title_metadata_and_restores_title_only_trigger(monkeypatch):
    module = _load_migration_module(
        monkeypatch,
        module_name="generated_chat_titles_migration_downgrade_test",
    )
    op_recorder = RecordingOp()
    module.op = op_recorder

    module.downgrade()

    assert op_recorder.dropped_constraints == [
        ("ck_chat_sessions_generated_title_not_empty", "chat_sessions", "check")
    ]
    assert op_recorder.dropped_columns == [("chat_sessions", "generated_title")]
    assert any(
        "coalesce(title, '')" in statement
        for statement in op_recorder.executed
    )
    assert any(
        "AFTER INSERT OR UPDATE OF title" in statement
        for statement in op_recorder.executed
    )
