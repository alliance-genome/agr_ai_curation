"""PostgreSQL coverage for canonical batch-result reconciliation."""

from __future__ import annotations

import os
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from uuid import UUID, uuid4

from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import bindparam


BACKEND_ROOT = Path(__file__).resolve().parents[3]
MIGRATION_PATH = (
    BACKEND_ROOT
    / "alembic"
    / "versions"
    / "e8f9a0b1c2d3_reconcile_batch_result_files.py"
)


def _load_migration_module():
    spec = spec_from_file_location("batch_result_manifest_migration", MIGRATION_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def migration_connection():
    engine = create_engine(os.environ["DATABASE_URL"])
    schema_name = f"batch_result_manifest_{uuid4().hex}"

    try:
        with engine.connect() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema_name}"'))
            connection.commit()
            try:
                connection.execute(text(f'SET search_path TO "{schema_name}"'))
                connection.execute(
                    text(
                        """
                        CREATE TABLE file_outputs (
                            id uuid PRIMARY KEY,
                            filename varchar(512) NOT NULL
                        );
                        CREATE TABLE batch_documents (
                            id uuid PRIMARY KEY,
                            result_file_path varchar(500),
                            result_files jsonb
                        )
                        """
                    )
                )
                connection.commit()
                yield connection
            finally:
                connection.rollback()
                connection.execute(
                    text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
                )
                connection.commit()
    finally:
        engine.dispose()


def _insert_file(connection, *, file_id: UUID, filename: str) -> None:
    connection.execute(
        text("INSERT INTO file_outputs (id, filename) VALUES (:id, :filename)"),
        {"id": file_id, "filename": filename},
    )


def _insert_batch_document(
    connection,
    *,
    row_id: UUID,
    result_file_path: str | None,
    result_files: list[dict] | dict | None,
) -> None:
    statement = text(
        """
        INSERT INTO batch_documents (id, result_file_path, result_files)
        VALUES (:id, :result_file_path, :result_files)
        """
    ).bindparams(bindparam("result_files", type_=JSONB))
    connection.execute(
        statement,
        {
            "id": row_id,
            "result_file_path": result_file_path,
            "result_files": result_files,
        },
    )


def test_upgrade_reconciles_url_only_rows_preserves_canonical_rows_and_is_idempotent(
    migration_connection,
):
    module = _load_migration_module()
    current_file_id = uuid4()
    legacy_file_id = uuid4()
    canonical_file_id = uuid4()
    current_row_id = uuid4()
    legacy_row_id = uuid4()
    canonical_row_id = uuid4()
    empty_row_id = uuid4()
    _insert_file(
        migration_connection,
        file_id=current_file_id,
        filename="current.csv",
    )
    _insert_file(
        migration_connection,
        file_id=legacy_file_id,
        filename="legacy.json",
    )
    _insert_file(
        migration_connection,
        file_id=canonical_file_id,
        filename="canonical.tsv",
    )
    _insert_batch_document(
        migration_connection,
        row_id=current_row_id,
        result_file_path=None,
        result_files=[
            {"download_url": f"/api/files/{current_file_id}/download", "format": "csv"}
        ],
    )
    _insert_batch_document(
        migration_connection,
        row_id=legacy_row_id,
        result_file_path=f"/api/files/{legacy_file_id}/download",
        result_files=None,
    )
    canonical_manifest = [
        {
            "file_id": str(canonical_file_id),
            "filename": "curator-name.tsv",
            "download_url": f"/api/files/{canonical_file_id}/download",
            "format": "tsv",
            "source_keys": ["gene"],
        }
    ]
    _insert_batch_document(
        migration_connection,
        row_id=canonical_row_id,
        result_file_path="/ignored/compatibility/value",
        result_files=canonical_manifest,
    )
    _insert_batch_document(
        migration_connection,
        row_id=empty_row_id,
        result_file_path=None,
        result_files=None,
    )

    module.op = Operations(MigrationContext.configure(migration_connection))
    module.upgrade()

    rows = {
        row.id: row.result_files
        for row in migration_connection.execute(
            text("SELECT id, result_files FROM batch_documents")
        )
    }
    assert rows[current_row_id] == [
        {
            "file_id": str(current_file_id),
            "filename": "current.csv",
            "download_url": f"/api/files/{current_file_id}/download",
            "format": "csv",
        }
    ]
    assert rows[legacy_row_id] == [
        {
            "file_id": str(legacy_file_id),
            "filename": "legacy.json",
            "download_url": f"/api/files/{legacy_file_id}/download",
        }
    ]
    assert rows[canonical_row_id] == canonical_manifest
    assert rows[empty_row_id] is None
    assert "result_file_path" not in {
        column["name"] for column in inspect(migration_connection).get_columns("batch_documents")
    }

    module.upgrade()
    rerun_rows = {
        row.id: row.result_files
        for row in migration_connection.execute(
            text("SELECT id, result_files FROM batch_documents")
        )
    }
    assert rerun_rows == rows


def test_upgrade_reports_every_irreconcilable_batch_document_id(
    migration_connection,
):
    module = _load_migration_module()
    missing_file_row_id = uuid4()
    malformed_row_id = uuid4()
    _insert_batch_document(
        migration_connection,
        row_id=missing_file_row_id,
        result_file_path=f"/api/files/{uuid4()}/download",
        result_files=None,
    )
    _insert_batch_document(
        migration_connection,
        row_id=malformed_row_id,
        result_file_path=None,
        result_files={"download_url": "not-an-array"},
    )

    module.op = Operations(MigrationContext.configure(migration_connection))
    with pytest.raises(RuntimeError) as exc_info:
        module.upgrade()

    message = str(exc_info.value)
    assert str(missing_file_row_id) in message
    assert str(malformed_row_id) in message
    assert "result_file_path" in {
        column["name"] for column in inspect(migration_connection).get_columns("batch_documents")
    }
