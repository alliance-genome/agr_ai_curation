"""Regression tests for the document access-group data migration."""

from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import types
from typing import Any, cast

import pytest


MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "c7d8e9f0a1b2_migrate_document_access_mods_to_group_ids.py"
)


class _MappingRows:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def __iter__(self):
        return iter(self._rows)


class _Connection:
    def __init__(self, rows):
        self.rows = rows

    def execute(self, statement):
        assert "SELECT id, source_access_scope, source_access_mods" in str(statement)
        return _MappingRows(self.rows)


class _RecordingOp:
    def __init__(self, rows):
        self.connection = _Connection(rows)
        self.alterations: list[tuple[str, str, str]] = []
        self.statements: list[str] = []

    def get_bind(self):
        return self.connection

    def alter_column(self, table_name, column_name, *, new_column_name):
        self.alterations.append((table_name, column_name, new_column_name))

    def execute(self, statement):
        self.statements.append(str(statement))


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


def test_upgrade_flattens_valid_legacy_group_ids(monkeypatch) -> None:
    module = _load_migration(monkeypatch)
    recorder = _RecordingOp(
        [
            {
                "id": "doc-restricted",
                "source_access_scope": "restricted",
                "source_access_mods": {"mods": ["team-alpha", "lab-2"]},
            },
            {
                "id": "doc-global-with-metadata",
                "source_access_scope": "global",
                "source_access_mods": {"mods": []},
            },
        ]
    )
    module.op = recorder

    module.upgrade()

    assert recorder.alterations == [
        ("pdf_documents", "source_access_mods", "source_access_group_ids")
    ]
    assert len(recorder.statements) == 2
    assert "source_access_group_ids -> 'mods'" in recorder.statements[0]
    assert "- 'per_mod_status'" in recorder.statements[1]


@pytest.mark.parametrize(
    "row",
    [
        {
            "id": "restricted-without-groups",
            "source_access_scope": "restricted",
            "source_access_mods": None,
        },
        {
            "id": "restricted-with-malformed-groups",
            "source_access_scope": "restricted",
            "source_access_mods": {"mods": ["team-alpha", {"unsafe": True}]},
        },
        {
            "id": "populated-legacy-scalar",
            "source_access_scope": "global",
            "source_access_mods": "team-alpha",
        },
    ],
)
def test_upgrade_rejects_invalid_rows_before_schema_or_data_changes(
    monkeypatch,
    row,
) -> None:
    module = _load_migration(monkeypatch)
    recorder = _RecordingOp([row])
    module.op = recorder

    with pytest.raises(RuntimeError, match=str(row["id"])):
        module.upgrade()

    assert recorder.alterations == []
    assert recorder.statements == []


def test_downgrade_restores_nested_legacy_shape(monkeypatch) -> None:
    module = _load_migration(monkeypatch)
    recorder = _RecordingOp([])
    module.op = recorder

    module.downgrade()

    assert "jsonb_build_object" in recorder.statements[0]
    assert recorder.alterations == [
        ("pdf_documents", "source_access_group_ids", "source_access_mods")
    ]
