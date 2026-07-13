"""Regression tests for the v0.8.11 production/main lineage reconciliation."""

from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import types


MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "b3c4d5e6f7a8_add_batch_result_files.py"
)


class RecordingOp:
    def __init__(self) -> None:
        self.added_columns: list[str] = []
        self.executed_sql: list[str] = []
        self.dropped_constraints: list[tuple[str, str, str | None]] = []
        self.created_constraints: list[tuple[str, str, str]] = []

    def get_bind(self):
        return object()

    def add_column(self, _table_name, column):
        self.added_columns.append(column.name)

    def execute(self, statement):
        self.executed_sql.append(str(statement))

    def drop_constraint(self, name, table_name, type_=None):
        self.dropped_constraints.append((name, table_name, type_))

    def create_check_constraint(self, name, table_name, condition):
        self.created_constraints.append((name, table_name, condition))


class ProductionHotfixInspector:
    def get_columns(self, table_name):
        assert table_name == "batch_documents"
        return [
            {"name": "result_files"},
            {"name": "output_status"},
            {"name": "output_branches"},
        ]

    def get_check_constraints(self, table_name):
        assert table_name == "pdf_documents"
        return [{"name": "ck_pdf_documents_page_count"}]


def _load_migration(monkeypatch):
    dummy_alembic = types.ModuleType("alembic")
    dummy_alembic.op = object()
    monkeypatch.setitem(sys.modules, "alembic", dummy_alembic)
    spec = spec_from_file_location("v0811_main_reconciliation_migration", MIGRATION_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_upgrade_preserves_existing_production_output_manifests(monkeypatch):
    module = _load_migration(monkeypatch)
    recorder = RecordingOp()
    module.op = recorder
    monkeypatch.setattr(module.sa, "inspect", lambda _bind: ProductionHotfixInspector())

    module.upgrade()

    assert recorder.added_columns == []
    assert len(recorder.executed_sql) == 2
    assert "AND result_files IS NULL" in recorder.executed_sql[0]
    assert "AND output_status IS NULL" in recorder.executed_sql[1]
    assert recorder.dropped_constraints == [
        ("ck_pdf_documents_page_count", "pdf_documents", "check")
    ]
    assert recorder.created_constraints == [
        ("ck_pdf_documents_page_count", "pdf_documents", "page_count > 0")
    ]
