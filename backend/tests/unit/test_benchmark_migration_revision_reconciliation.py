"""Tests for the released/main Alembic revision-ID reconciliation."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


_MIGRATION_PATH = (
    Path(__file__).parents[2]
    / "alembic"
    / "versions"
    / "e2f3a4b5c6e8_add_benchmark_persistence.py"
)
_SPEC = importlib.util.spec_from_file_location(
    "reidentified_benchmark_persistence_migration",
    _MIGRATION_PATH,
)
assert _SPEC and _SPEC.loader
migration = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(migration)


class _Inspector:
    def __init__(self, tables: set[str]):
        self.tables = tables

    def has_table(self, name: str) -> bool:
        return name in self.tables


def test_reidentified_benchmark_revision_follows_released_flow_repair():
    assert migration.revision == "e2f3a4b5c6e8"
    assert migration.down_revision == "e2f3a4b5c6d7"


@pytest.mark.parametrize(
    ("tables", "expected"),
    [
        (set(), False),
        (set(migration._BASE_TABLES), True),
    ],
)
def test_existing_benchmark_schema_recognizes_atomic_states(
    monkeypatch: pytest.MonkeyPatch,
    tables: set[str],
    expected: bool,
):
    bind = object()
    monkeypatch.setattr(migration.op, "get_bind", lambda: bind)
    monkeypatch.setattr(migration.sa, "inspect", lambda candidate: _Inspector(tables))

    assert migration._existing_benchmark_schema_is_complete() is expected


def test_existing_benchmark_schema_fails_closed_on_partial_state(
    monkeypatch: pytest.MonkeyPatch,
):
    bind = object()
    monkeypatch.setattr(migration.op, "get_bind", lambda: bind)
    monkeypatch.setattr(
        migration.sa,
        "inspect",
        lambda candidate: _Inspector({"benchmark_jobs"}),
    )

    with pytest.raises(RuntimeError, match="Partial benchmark persistence schema"):
        migration._existing_benchmark_schema_is_complete()
