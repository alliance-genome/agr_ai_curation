"""Unit tests for extraction-result idempotency migration."""

from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import types


REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_PATH = (
    REPO_ROOT
    / "alembic"
    / "versions"
    / "w8x9y0z1a2b3_add_extraction_result_idempotency.py"
)


class RecordingOp:
    def __init__(self) -> None:
        self.added_columns: list[tuple[str, str]] = []
        self.created_indexes: list[dict[str, object]] = []
        self.dropped_indexes: list[dict[str, object]] = []
        self.dropped_columns: list[tuple[str, str]] = []

    def add_column(self, table_name, column):
        self.added_columns.append((table_name, column.name))

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

    def drop_index(self, name, table_name=None, **kwargs):
        self.dropped_indexes.append(
            {"name": name, "table_name": table_name, "kwargs": kwargs}
        )

    def drop_column(self, table_name, column_name):
        self.dropped_columns.append((table_name, column_name))


def _load_migration_module(monkeypatch):
    dummy_alembic = types.ModuleType("alembic")
    dummy_alembic.op = object()
    monkeypatch.setitem(sys.modules, "alembic", dummy_alembic)

    spec = spec_from_file_location("extraction_result_idempotency_migration", MIGRATION_PATH)
    assert spec is not None
    assert spec.loader is not None

    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_upgrade_adds_idempotency_columns_and_partial_unique_index(monkeypatch):
    module = _load_migration_module(monkeypatch)
    op_recorder = RecordingOp()
    module.op = op_recorder

    module.upgrade()

    assert op_recorder.added_columns == [
        ("extraction_results", "idempotency_key"),
        ("extraction_results", "payload_hash"),
    ]
    assert len(op_recorder.created_indexes) == 1
    created_index = op_recorder.created_indexes[0]
    assert created_index["name"] == "uq_extraction_results_idempotency_key"
    assert created_index["table_name"] == "extraction_results"
    assert created_index["columns"] == ["idempotency_key"]
    assert created_index["unique"] is True
    where_clause = created_index["kwargs"]["postgresql_where"]
    assert where_clause.text == "idempotency_key IS NOT NULL"


def test_downgrade_drops_unique_index_and_columns(monkeypatch):
    module = _load_migration_module(monkeypatch)
    op_recorder = RecordingOp()
    module.op = op_recorder

    module.downgrade()

    assert op_recorder.dropped_indexes == [
        {
            "name": "uq_extraction_results_idempotency_key",
            "table_name": "extraction_results",
            "kwargs": {},
        }
    ]
    assert op_recorder.dropped_columns == [
        ("extraction_results", "payload_hash"),
        ("extraction_results", "idempotency_key"),
    ]
