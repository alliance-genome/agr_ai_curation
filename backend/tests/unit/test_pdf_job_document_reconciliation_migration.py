"""Regression coverage for terminal PDF job document reconciliation."""

from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import types

import sqlalchemy as sa


MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "5e6f7a8b9c0d_reconcile_terminal_pdf_job_documents.py"
)


class ConnectionOp:
    def __init__(self, connection) -> None:
        self.connection = connection

    def execute(self, statement):
        return self.connection.execute(statement)


def _load_migration(monkeypatch):
    dummy_alembic = types.ModuleType("alembic")
    setattr(dummy_alembic, "op", object())
    monkeypatch.setitem(sys.modules, "alembic", dummy_alembic)
    spec = spec_from_file_location("pdf_job_document_reconciliation", MIGRATION_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _create_schema(connection) -> None:
    connection.execute(
        sa.text(
            """
            CREATE TABLE pdf_documents (
                id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                processing_started_at TEXT,
                processing_completed_at TEXT,
                error_message TEXT
            )
            """
        )
    )
    connection.execute(
        sa.text(
            """
            CREATE TABLE pdf_processing_jobs (
                id TEXT PRIMARY KEY,
                document_id TEXT NOT NULL,
                status TEXT NOT NULL,
                message TEXT,
                error_message TEXT,
                created_at TEXT NOT NULL,
                started_at TEXT,
                updated_at TEXT NOT NULL,
                completed_at TEXT
            )
            """
        )
    )


def _insert_document(connection, document_id: str, status: str, error_message=None) -> None:
    connection.execute(
        sa.text(
            """
            INSERT INTO pdf_documents (id, status, error_message)
            VALUES (:id, :status, :error_message)
            """
        ),
        {"id": document_id, "status": status, "error_message": error_message},
    )


def _insert_job(
    connection,
    *,
    job_id: str,
    document_id: str,
    status: str,
    created_at: str,
    message: str | None = None,
) -> None:
    connection.execute(
        sa.text(
            """
            INSERT INTO pdf_processing_jobs (
                id, document_id, status, message, created_at, started_at,
                updated_at, completed_at
            ) VALUES (
                :id, :document_id, :status, :message, :created_at, :created_at,
                :created_at, :completed_at
            )
            """
        ),
        {
            "id": job_id,
            "document_id": document_id,
            "status": status,
            "message": message,
            "created_at": created_at,
            "completed_at": created_at if status in {"failed", "cancelled", "completed"} else None,
        },
    )


def test_upgrade_reconciles_only_active_documents_with_latest_failed_or_cancelled_job(
    monkeypatch,
):
    module = _load_migration(monkeypatch)
    engine = sa.create_engine("sqlite://")

    with engine.begin() as connection:
        _create_schema(connection)
        cases = {
            "latest-failed": "processing",
            "latest-cancelled": "embedding",
            "newer-active": "processing",
            "latest-completed": "chunking",
            "already-terminal": "failed",
            "completed-document": "completed",
            "tie-newer-active": "storing",
        }
        for document_id, status in cases.items():
            _insert_document(
                connection,
                document_id,
                status,
                error_message="preserve me" if document_id == "already-terminal" else None,
            )

        _insert_job(
            connection,
            job_id="1",
            document_id="latest-failed",
            status="failed",
            created_at="2026-08-20T10:00:00Z",
            message="Extraction failed",
        )
        _insert_job(
            connection,
            job_id="2",
            document_id="latest-cancelled",
            status="cancelled",
            created_at="2026-08-20T10:00:00Z",
            message="Curator cancelled",
        )
        _insert_job(
            connection,
            job_id="3",
            document_id="newer-active",
            status="failed",
            created_at="2026-08-20T09:00:00Z",
        )
        _insert_job(
            connection,
            job_id="4",
            document_id="newer-active",
            status="running",
            created_at="2026-08-20T10:00:00Z",
        )
        _insert_job(
            connection,
            job_id="5",
            document_id="latest-completed",
            status="completed",
            created_at="2026-08-20T10:00:00Z",
        )
        _insert_job(
            connection,
            job_id="6",
            document_id="already-terminal",
            status="failed",
            created_at="2026-08-20T10:00:00Z",
        )
        _insert_job(
            connection,
            job_id="7",
            document_id="completed-document",
            status="cancelled",
            created_at="2026-08-20T10:00:00Z",
        )
        _insert_job(
            connection,
            job_id="8",
            document_id="tie-newer-active",
            status="failed",
            created_at="2026-08-20T10:00:00Z",
        )
        _insert_job(
            connection,
            job_id="9",
            document_id="tie-newer-active",
            status="running",
            created_at="2026-08-20T10:00:00Z",
        )

        setattr(module, "op", ConnectionOp(connection))
        module.upgrade()
        module.upgrade()

        rows = {
            row.id: row
            for row in connection.execute(
                sa.text(
                    """
                    SELECT id, status, processing_started_at,
                           processing_completed_at, error_message
                    FROM pdf_documents
                    """
                )
            )
        }

    assert rows["latest-failed"].status == "failed"
    assert rows["latest-failed"].error_message == "Extraction failed"
    assert rows["latest-failed"].processing_completed_at is not None
    assert rows["latest-cancelled"].status == "failed"
    assert rows["latest-cancelled"].error_message == "Curator cancelled"
    assert rows["newer-active"].status == "processing"
    assert rows["latest-completed"].status == "chunking"
    assert rows["already-terminal"].status == "failed"
    assert rows["already-terminal"].error_message == "preserve me"
    assert rows["completed-document"].status == "completed"
    assert rows["tie-newer-active"].status == "storing"


def test_downgrade_is_noop(monkeypatch):
    module = _load_migration(monkeypatch)
    module.downgrade()
