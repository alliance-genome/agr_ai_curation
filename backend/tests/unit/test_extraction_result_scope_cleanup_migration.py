"""Unit tests for dropping extraction-result scope columns."""

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
    / "q2r3s4t5u6v7_drop_extraction_result_scope_columns.py"
)


class RecordingOp:
    def __init__(self) -> None:
        self.dropped_columns: list[tuple[str, str]] = []
        self.added_columns: list[tuple[str, sa.Column]] = []

    def drop_column(self, table_name, column_name):
        self.dropped_columns.append((table_name, column_name))

    def add_column(self, table_name, column):
        self.added_columns.append((table_name, column))


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


def test_upgrade_drops_extraction_result_scope_columns(monkeypatch):
    module = _load_migration_module(
        monkeypatch,
        module_name="extraction_result_scope_cleanup_upgrade_test",
    )
    op_recorder = RecordingOp()
    module.op = op_recorder

    module.upgrade()

    assert op_recorder.dropped_columns == [
        ("extraction_results", "profile_key"),
        ("extraction_results", "domain_key"),
    ]


def test_downgrade_restores_extraction_result_scope_columns(monkeypatch):
    module = _load_migration_module(
        monkeypatch,
        module_name="extraction_result_scope_cleanup_downgrade_test",
    )
    op_recorder = RecordingOp()
    module.op = op_recorder

    module.downgrade()

    added_columns = {
        column.name: (table_name, column)
        for table_name, column in op_recorder.added_columns
    }
    assert set(added_columns) == {"profile_key", "domain_key"}
    assert added_columns["profile_key"][0] == "extraction_results"
    assert added_columns["profile_key"][1].nullable is True
    assert added_columns["domain_key"][0] == "extraction_results"
    assert added_columns["domain_key"][1].nullable is True
