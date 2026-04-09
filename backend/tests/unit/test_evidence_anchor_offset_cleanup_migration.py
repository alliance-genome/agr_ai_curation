"""Unit tests for removing PDFX markdown offsets from evidence anchors."""

from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import types


REPO_ROOT = Path(__file__).resolve().parents[3]
MIGRATION_PATH = (
    REPO_ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "r8s9t0u1v2w3_remove_evidence_anchor_pdfx_offsets.py"
)


class RecordingOp:
    def __init__(self) -> None:
        self.executed: list[object] = []

    def execute(self, statement) -> None:
        self.executed.append(statement)


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


def test_upgrade_strips_pdfx_markdown_offsets_from_evidence_anchor_payloads(monkeypatch):
    module = _load_migration_module(
        monkeypatch,
        module_name="evidence_anchor_offset_cleanup_upgrade_test",
    )
    op_recorder = RecordingOp()
    module.op = op_recorder

    module.upgrade()

    assert len(op_recorder.executed) == 1
    statement = str(op_recorder.executed[0])
    assert "UPDATE evidence_anchors" in statement
    assert "pdfx_markdown_offset_start" in statement
    assert "pdfx_markdown_offset_end" in statement
    assert "COALESCE(anchor" not in statement


def test_downgrade_is_a_noop(monkeypatch):
    module = _load_migration_module(
        monkeypatch,
        module_name="evidence_anchor_offset_cleanup_downgrade_test",
    )
    op_recorder = RecordingOp()
    module.op = op_recorder

    module.downgrade()

    assert op_recorder.executed == []
