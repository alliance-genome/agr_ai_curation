"""Unit tests for the prep candidate cleanup migration."""

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
    / "g4h5i6j7k8l9_drop_prep_confidence_and_ambiguities.py"
)


class RecordingOp:
    def __init__(self) -> None:
        self.dropped_constraints: list[tuple[str, str, str | None]] = []
        self.dropped_columns: list[tuple[str, str]] = []
        self.added_columns: list[tuple[str, sa.Column]] = []
        self.created_constraints: list[tuple[str, str, str]] = []

    def drop_constraint(self, name, table_name, type_=None):
        self.dropped_constraints.append((name, table_name, type_))

    def drop_column(self, table_name, column_name):
        self.dropped_columns.append((table_name, column_name))

    def add_column(self, table_name, column):
        self.added_columns.append((table_name, column))

    def create_check_constraint(self, name, table_name, condition):
        self.created_constraints.append((name, table_name, condition))


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


def test_upgrade_drops_prep_confidence_and_ambiguity_columns(monkeypatch):
    module = _load_migration_module(
        monkeypatch,
        module_name="curation_prep_candidate_cleanup_upgrade_test",
    )
    op_recorder = RecordingOp()
    module.op = op_recorder

    module.upgrade()

    assert op_recorder.dropped_constraints == [
        ("ck_curation_candidates_confidence", "curation_candidates", "check")
    ]
    assert op_recorder.dropped_columns == [
        ("curation_candidates", "confidence"),
        ("curation_candidates", "unresolved_ambiguities"),
    ]


def test_downgrade_restores_removed_columns(monkeypatch):
    module = _load_migration_module(
        monkeypatch,
        module_name="curation_prep_candidate_cleanup_downgrade_test",
    )
    op_recorder = RecordingOp()
    module.op = op_recorder

    module.downgrade()

    added_columns = {
        column.name: (table_name, column)
        for table_name, column in op_recorder.added_columns
    }
    assert set(added_columns) == {"confidence", "unresolved_ambiguities"}
    assert added_columns["confidence"][0] == "curation_candidates"
    assert added_columns["unresolved_ambiguities"][0] == "curation_candidates"
    assert op_recorder.created_constraints == [
        (
            "ck_curation_candidates_confidence",
            "curation_candidates",
            "confidence IS NULL OR (confidence >= 0.0 AND confidence <= 1.0)",
        )
    ]
