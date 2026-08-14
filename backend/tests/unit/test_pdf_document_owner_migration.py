"""Regression tests for the required PDF-document owner migration."""

from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import types
from typing import Any, cast
from uuid import UUID

import pytest


MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "2b3c4d5e6f7a_require_pdf_document_owner.py"
)


class _MappingsResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def __iter__(self):
        return iter(self._rows)

    def all(self):
        return list(self._rows)


class RecordingConnection:
    def __init__(self, candidates, users_by_auth_sub):
        self.candidates = candidates
        self.users_by_auth_sub = users_by_auth_sub
        self.updates: list[dict[str, object]] = []

    def execute(
        self,
        statement: object,
        parameters: dict[str, object] | None = None,
    ):
        sql = str(statement)
        if "FROM pdf_documents" in sql:
            return _MappingsResult(self.candidates)
        if "FROM users" in sql:
            assert parameters is not None
            auth_sub = parameters["auth_sub"]
            assert isinstance(auth_sub, str)
            return _MappingsResult(self.users_by_auth_sub.get(auth_sub, []))
        if sql.startswith("UPDATE pdf_documents"):
            assert parameters is not None
            self.updates.append(parameters.copy())
            return _MappingsResult([])
        raise AssertionError(f"Unexpected SQL: {sql}")


class RecordingOp:
    def __init__(self, connection):
        self.connection = connection
        self.alterations: list[tuple[str, str, bool]] = []

    def get_bind(self):
        return self.connection

    def alter_column(self, table_name, column_name, *, existing_type, nullable):
        del existing_type
        self.alterations.append((table_name, column_name, nullable))


def _load_migration(monkeypatch) -> Any:
    dummy_alembic = types.ModuleType("alembic")
    setattr(dummy_alembic, "op", object())
    monkeypatch.setitem(sys.modules, "alembic", dummy_alembic)
    spec = spec_from_file_location("pdf_document_owner_migration", MIGRATION_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return cast(Any, module)


def test_upgrade_reconciles_only_canonical_exact_owner_matches(monkeypatch):
    module = _load_migration(monkeypatch)
    document_id = UUID("11111111-1111-1111-1111-111111111111")
    connection = RecordingConnection(
        [{"id": document_id, "file_path": f"owner-sub/{document_id}/paper.pdf"}],
        {"owner-sub": [{"user_id": 42}]},
    )
    recorder = RecordingOp(connection)
    module.op = recorder

    module.upgrade()

    assert module.down_revision == "1a2b3c4d5e6f"
    assert connection.updates == [{"document_id": document_id, "user_id": 42}]
    assert recorder.alterations == [("pdf_documents", "user_id", False)]


def test_upgrade_reports_every_irreconcilable_id_before_changes(monkeypatch):
    module = _load_migration(monkeypatch)
    malformed_id = UUID("22222222-2222-2222-2222-222222222222")
    missing_user_id = UUID("11111111-1111-1111-1111-111111111111")
    ambiguous_user_id = UUID("33333333-3333-3333-3333-333333333333")
    connection = RecordingConnection(
        [
            {"id": malformed_id, "file_path": "not-a-canonical-path"},
            {
                "id": missing_user_id,
                "file_path": f"missing-sub/{missing_user_id}/paper.pdf",
            },
            {
                "id": ambiguous_user_id,
                "file_path": f"duplicate-sub/{ambiguous_user_id}/paper.pdf",
            },
        ],
        {"duplicate-sub": [{"user_id": 7}, {"user_id": 8}]},
    )
    recorder = RecordingOp(connection)
    module.op = recorder

    with pytest.raises(RuntimeError) as exc_info:
        module.upgrade()

    assert str(exc_info.value).endswith(
        "11111111-1111-1111-1111-111111111111, "
        "22222222-2222-2222-2222-222222222222, "
        "33333333-3333-3333-3333-333333333333"
    )
    assert connection.updates == []
    assert recorder.alterations == []


def test_upgrade_rejects_path_for_a_different_document(monkeypatch):
    module = _load_migration(monkeypatch)
    document_id = UUID("11111111-1111-1111-1111-111111111111")
    connection = RecordingConnection(
        [
            {
                "id": document_id,
                "file_path": "owner-sub/22222222-2222-2222-2222-222222222222/paper.pdf",
            }
        ],
        {"owner-sub": [{"user_id": 42}]},
    )
    recorder = RecordingOp(connection)
    module.op = recorder

    with pytest.raises(RuntimeError, match=str(document_id)):
        module.upgrade()

    assert connection.updates == []
    assert recorder.alterations == []


def test_downgrade_restores_nullable_column(monkeypatch):
    module = _load_migration(monkeypatch)
    recorder = RecordingOp(RecordingConnection([], {}))
    module.op = recorder

    module.downgrade()

    assert recorder.alterations == [("pdf_documents", "user_id", True)]
