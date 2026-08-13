"""Regression tests for the PDF page-count constraint migration."""

from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import types


MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "b2c3d4e5f6a7_relax_pdf_page_count_constraint.py"
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
    spec = spec_from_file_location("pdf_page_count_constraint_migration", MIGRATION_PATH)
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

    assert module.down_revision == "a1b2c3d4e5f6"
    assert recorder.dropped_constraints == [
        ("ck_pdf_documents_page_count", "pdf_documents", "check")
    ]
    assert recorder.created_constraints == [
        ("ck_pdf_documents_page_count", "pdf_documents", "page_count > 0")
    ]


def test_downgrade_restores_legacy_constraint(monkeypatch):
    module = _load_migration(monkeypatch)
    recorder = RecordingOp()
    module.op = recorder

    module.downgrade()

    assert recorder.created_constraints == [
        (
            "ck_pdf_documents_page_count",
            "pdf_documents",
            "page_count > 0 AND page_count <= 50",
        )
    ]
