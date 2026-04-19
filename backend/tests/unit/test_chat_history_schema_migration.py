"""Unit tests for the durable chat history schema migration."""

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
    / "s1t2u3v4w5x6_add_chat_history_tables.py"
)


class RecordingOp:
    """Capture Alembic operations for structural assertions."""

    def __init__(self) -> None:
        self.created_tables: list[dict[str, object]] = []
        self.created_indexes: list[dict[str, object]] = []
        self.executed: list[str] = []
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

    def execute(self, statement):
        self.executed.append(str(statement))

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
        if isinstance(element, (sa.CheckConstraint, sa.PrimaryKeyConstraint)) and element.name:
            names.add(element.name)
    return names


def _single_foreign_key(column: sa.Column) -> sa.ForeignKey:
    foreign_keys = list(column.foreign_keys)
    assert len(foreign_keys) == 1
    return foreign_keys[0]


def test_upgrade_creates_chat_history_tables_indexes_and_triggers(monkeypatch):
    module = _load_migration_module(
        monkeypatch,
        module_name="chat_history_schema_migration_upgrade_test",
    )
    op_recorder = RecordingOp()
    module.op = op_recorder

    module.upgrade()

    assert [table["name"] for table in op_recorder.created_tables] == [
        "chat_sessions",
        "chat_messages",
    ]

    session_table = _table_by_name(op_recorder, "chat_sessions")
    session_columns = _columns(session_table)
    assert set(session_columns) == {
        "session_id",
        "user_auth_sub",
        "title",
        "active_document_id",
        "created_at",
        "updated_at",
        "last_message_at",
        "deleted_at",
        "search_vector",
    }
    assert "ck_chat_sessions_session_id_not_empty" in _constraint_names(session_table)
    assert "ck_chat_sessions_user_auth_sub_not_empty" in _constraint_names(session_table)
    assert "ck_chat_sessions_title_not_empty" in _constraint_names(session_table)
    assert _single_foreign_key(session_columns["active_document_id"]).target_fullname == (
        "pdf_documents.id"
    )

    message_table = _table_by_name(op_recorder, "chat_messages")
    message_columns = _columns(message_table)
    assert set(message_columns) == {
        "message_id",
        "session_id",
        "turn_id",
        "role",
        "message_type",
        "content",
        "payload_json",
        "trace_id",
        "created_at",
        "search_vector",
    }
    assert str(message_columns["message_type"].server_default.arg) == "text"
    assert "ck_chat_messages_role" in _constraint_names(message_table)
    assert "ck_chat_messages_turn_id_not_empty" in _constraint_names(message_table)
    assert "ck_chat_messages_content_not_empty" in _constraint_names(message_table)
    assert _single_foreign_key(message_columns["session_id"]).target_fullname == (
        "chat_sessions.session_id"
    )

    index_map = {index["name"]: index for index in op_recorder.created_indexes}
    assert set(index_map) == {
        "ix_chat_sessions_user_auth_sub",
        "ix_chat_sessions_active_document_id",
        "ix_chat_sessions_search_vector",
        "ix_chat_messages_session_timeline",
        "ix_chat_messages_turn_lookup",
        "uq_chat_messages_user_turn",
        "uq_chat_messages_assistant_turn",
        "ix_chat_messages_search_vector",
    }
    assert index_map["ix_chat_sessions_user_auth_sub"]["kwargs"]["postgresql_where"].text == (
        "deleted_at IS NULL"
    )
    assert index_map["ix_chat_sessions_active_document_id"]["kwargs"]["postgresql_where"].text == (
        "active_document_id IS NOT NULL"
    )
    assert index_map["ix_chat_messages_turn_lookup"]["kwargs"]["postgresql_where"].text == (
        "turn_id IS NOT NULL"
    )
    assert index_map["uq_chat_messages_user_turn"]["kwargs"]["postgresql_where"].text == (
        "turn_id IS NOT NULL AND role = 'user'"
    )
    assert index_map["uq_chat_messages_assistant_turn"]["kwargs"]["postgresql_where"].text == (
        "turn_id IS NOT NULL AND role = 'assistant'"
    )

    assert any(
        "CREATE INDEX ix_chat_sessions_recent_activity" in statement
        for statement in op_recorder.executed
    )
    assert any("CREATE OR REPLACE FUNCTION strip_chat_search_content" in statement for statement in op_recorder.executed)
    assert any("CREATE OR REPLACE FUNCTION set_chat_message_search_vector" in statement for statement in op_recorder.executed)
    assert any("CREATE OR REPLACE FUNCTION refresh_chat_session_rollup" in statement for statement in op_recorder.executed)
    assert any("CREATE OR REPLACE FUNCTION refresh_chat_session_search_trigger" in statement for statement in op_recorder.executed)
    assert any("CREATE OR REPLACE FUNCTION refresh_chat_session_from_message_trigger" in statement for statement in op_recorder.executed)
    assert any("CREATE TRIGGER trg_chat_messages_search_vector" in statement for statement in op_recorder.executed)
    assert any("CREATE TRIGGER trg_chat_sessions_refresh_rollup" in statement for statement in op_recorder.executed)
    assert any("CREATE TRIGGER trg_chat_messages_refresh_session" in statement for statement in op_recorder.executed)


def test_downgrade_drops_chat_history_tables_and_helpers(monkeypatch):
    module = _load_migration_module(
        monkeypatch,
        module_name="chat_history_schema_migration_downgrade_test",
    )
    op_recorder = RecordingOp()
    module.op = op_recorder

    module.downgrade()

    assert op_recorder.dropped_tables == ["chat_messages", "chat_sessions"]
    assert any(
        "DROP TRIGGER IF EXISTS trg_chat_messages_refresh_session ON chat_messages" in statement
        for statement in op_recorder.executed
    )
    assert any(
        "DROP FUNCTION IF EXISTS refresh_chat_session_rollup(text)" in statement
        for statement in op_recorder.executed
    )
    assert any(
        "DROP FUNCTION IF EXISTS strip_chat_search_content(text)" in statement
        for statement in op_recorder.executed
    )
