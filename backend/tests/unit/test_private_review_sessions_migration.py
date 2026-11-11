"""Unit tests for the private-review-session migration (ALL-1547)."""

from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import types


BACKEND_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_PATH = BACKEND_ROOT / "alembic" / "versions" / "b5c6d7e8f9a0_private_review_sessions.py"


class RecordingOp:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def execute(self, statement):
        self.calls.append(("execute", str(statement)))

    def drop_constraint(self, name, table_name, type_=None):
        self.calls.append(("drop_constraint", name, table_name, type_))

    def create_check_constraint(self, name, table_name, condition):
        self.calls.append(("create_check_constraint", name, table_name, condition))

    def drop_index(self, name, table_name=None):
        self.calls.append(("drop_index", name, table_name))

    def create_index(self, name, table_name, columns):
        self.calls.append(("create_index", name, table_name, tuple(columns)))

    def drop_column(self, table_name, column_name):
        self.calls.append(("drop_column", table_name, column_name))

    def add_column(self, table_name, column):
        self.calls.append(("add_column", table_name, column.name, column.nullable))


def _module(monkeypatch):
    dummy_alembic = types.ModuleType("alembic")
    dummy_alembic.op = object()
    monkeypatch.setitem(sys.modules, "alembic", dummy_alembic)
    spec = spec_from_file_location("private_review_sessions_migration", MIGRATION_PATH)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    module.op = RecordingOp()
    return module


def test_revision_follows_the_reference_link_head(monkeypatch):
    module = _module(monkeypatch)
    assert module.revision == "b5c6d7e8f9a0"
    assert module.down_revision == "a2b3c4d5e6f7"
    assert "curator" not in module.CURRENT_SORT_FIELDS
    assert set(module.PREVIOUS_SORT_FIELDS) - set(module.CURRENT_SORT_FIELDS) == {"curator"}


def test_upgrade_rewrites_saved_views_before_dropping_the_column(monkeypatch):
    module = _module(monkeypatch)
    module.upgrade()
    calls = module.op.calls

    assert calls[0] == (
        "execute",
        "UPDATE curation_saved_views SET sort_by = 'prepared_at' WHERE sort_by = 'curator'",
    )
    assert calls[1] == ("drop_constraint", "ck_curation_saved_views_sort_by", "curation_saved_views", "check")
    assert calls[2][:3] == ("create_check_constraint", "ck_curation_saved_views_sort_by", "curation_saved_views")
    assert "'curator'" not in calls[2][3]
    assert calls[3][0] == "execute"
    assert "filters - ARRAY['inventory_scope', 'curator_ids']::text[]" in calls[3][1]
    assert calls[4:] == [
        ("drop_index", "ix_curation_sessions_assigned_curator", "curation_review_sessions"),
        ("drop_column", "curation_review_sessions", "assigned_curator_id"),
    ]


def test_downgrade_restores_the_schema(monkeypatch):
    module = _module(monkeypatch)
    module.downgrade()
    calls = module.op.calls

    assert calls[0] == ("add_column", "curation_review_sessions", "assigned_curator_id", True)
    assert calls[1] == (
        "create_index",
        "ix_curation_sessions_assigned_curator",
        "curation_review_sessions",
        ("assigned_curator_id",),
    )
    assert calls[2] == ("drop_constraint", "ck_curation_saved_views_sort_by", "curation_saved_views", "check")
    assert "'curator'" in calls[3][3]
