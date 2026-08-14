"""Regression tests for the PDF file-size constraint migration."""

from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import types


MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "1a2b3c4d5e6f_relax_pdf_file_size_constraint.py"
)


class RecordingOp:
    def __init__(self) -> None:
        self.created_constraints: list[tuple[str, str, str]] = []
        self.dropped_constraints: list[tuple[str, str, str | None]] = []

    def create_check_constraint(self, name, table_name, condition):
        self.created_constraints.append((name, table_name, condition))

    def drop_constraint(self, name, table_name, type_=None):
        self.dropped_constraints.append((name, table_name, type_))


def _load_migration(monkeypatch):
    dummy_alembic = types.ModuleType("alembic")
    dummy_alembic.op = object()
    monkeypatch.setitem(sys.modules, "alembic", dummy_alembic)
    spec = spec_from_file_location("pdf_file_size_constraint_migration", MIGRATION_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_upgrade_replaces_legacy_ceiling_with_positive_invariant(monkeypatch):
    module = _load_migration(monkeypatch)
    recorder = RecordingOp()
    module.op = recorder

    module.upgrade()

    assert module.down_revision == "0f1e2d3c4b5a"
    assert recorder.dropped_constraints == [
        ("ck_pdf_documents_file_size", "pdf_documents", "check")
    ]
    assert recorder.created_constraints == [
        ("ck_pdf_documents_file_size", "pdf_documents", "file_size > 0")
    ]


def test_downgrade_restores_legacy_constraint(monkeypatch):
    module = _load_migration(monkeypatch)
    recorder = RecordingOp()
    module.op = recorder

    module.downgrade()

    assert recorder.created_constraints == [
        (
            "ck_pdf_documents_file_size",
            "pdf_documents",
            "file_size > 0 AND file_size <= 524288000",
        )
    ]
