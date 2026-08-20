"""Regression tests for the provider-neutral document access migration."""

from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import types
from typing import Any, cast


MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "3c4d5e6f7a8b_neutralize_document_access_groups.py"
)


class RecordingConnection:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, statement: object) -> None:
        self.statements.append(str(statement))


class RecordingOp:
    def __init__(self) -> None:
        self.connection = RecordingConnection()
        self.renames: list[tuple[str, str, str]] = []

    def get_bind(self) -> RecordingConnection:
        return self.connection

    def alter_column(
        self,
        table_name: str,
        column_name: str,
        *,
        new_column_name: str,
    ) -> None:
        self.renames.append((table_name, column_name, new_column_name))


def _load_migration(monkeypatch) -> Any:
    dummy_alembic = types.ModuleType("alembic")
    setattr(dummy_alembic, "op", object())
    monkeypatch.setitem(sys.modules, "alembic", dummy_alembic)
    spec = spec_from_file_location("document_access_group_migration", MIGRATION_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return cast(Any, module)


def test_upgrade_flattens_known_legacy_group_ids_and_renames_column(monkeypatch):
    module = _load_migration(monkeypatch)
    recorder = RecordingOp()
    module.op = recorder

    module.upgrade()

    assert module.down_revision == "2b3c4d5e6f7a"
    assert recorder.renames == [
        ("pdf_documents", "source_access_mods", "source_access_group_ids")
    ]
    sql = recorder.connection.statements[0]
    assert "source_access_group_ids -> 'mods'" in sql
    assert "jsonb_typeof(item) <> 'string'" in sql
    assert "ELSE NULL" in sql


def test_downgrade_restores_legacy_object_and_column_name(monkeypatch):
    module = _load_migration(monkeypatch)
    recorder = RecordingOp()
    module.op = recorder

    module.downgrade()

    assert "jsonb_build_object('mods', source_access_group_ids)" in (
        recorder.connection.statements[0]
    )
    assert recorder.renames == [
        ("pdf_documents", "source_access_group_ids", "source_access_mods")
    ]
