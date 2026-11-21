"""Postgres coverage for the paper-files migration (KANBAN-1976 / ALL-1460, P5).

Fixture rows cover the awkward cases a production library holds: pending,
processing and failed papers, papers that never got pieces, text-only and
frozen benchmark papers with placeholder page counts, provider imports with
full provenance, an upload linked to its reference, and duplicate provider
provenance across two papers. All values are made up.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from src.models.sql.database import SessionLocal


BACKEND_ROOT = Path(__file__).resolve().parents[3]
PREVIOUS_HEAD = "a9b8c7d6e5f4"
REVISION = "c6d7e8f9a0b1"

_MOVED = (
    "file_path", "file_hash", "file_size", "page_count", "pdfx_json_path",
    "processed_json_path", "hierarchy_metadata", "source_provider_source_file_id",
    "source_provider_converted_artifact_id", "source_provider_pdf_artifact_id", "source_md5",
    "source_converted_artifact_sha256", "source_file_class", "source_file_extension",
    "source_artifact_status", "source_import_status", "source_imported_at",
    "source_payload_path", "source_markdown_path", "source_access_scope",
    "source_access_group_ids", "viewer_mode",
)
_INSERT_COLUMNS = (
    "id", "user_id", "filename", "title", "status", "error_message", "upload_timestamp",
    "processing_started_at", "processing_completed_at", "source_provider",
    "source_provider_reference_id", "source_provider_reference_curie", "source_external_ids",
    *_MOVED,
)
_JSON_COLUMNS = {"hierarchy_metadata", "source_access_group_ids", "source_external_ids"}


def _config() -> Config:
    return Config(str(BACKEND_ROOT / "alembic.ini"))


def _owner(db, auth_sub: str) -> int:
    return db.execute(
        text("INSERT INTO users (auth_sub, is_active) VALUES (:sub, true) RETURNING user_id"),
        {"sub": auth_sub},
    ).scalar_one()


def _paper(owner_id: int, index: int, **overrides) -> dict:
    uploaded = datetime(2026, 10, 1, 9, index, tzinfo=timezone.utc)
    row = {column: None for column in _INSERT_COLUMNS}
    row.update(
        id=uuid4(),
        user_id=owner_id,
        filename=f"made-up-paper-{index}.pdf",
        status="completed",
        upload_timestamp=uploaded,
        processing_started_at=uploaded,
        processing_completed_at=uploaded,
        file_path=f"owner/{index}/made-up-paper-{index}.pdf",
        file_hash=f"{index:064x}",
        file_size=1000 + index,
        page_count=4 + index,
        processed_json_path=f"owner/processed_json/{index}.json",
    )
    row.update(overrides)
    return row


def _insert(db, row: dict) -> None:
    columns = ", ".join(_INSERT_COLUMNS)
    values = ", ".join(
        f"CAST(:{column} AS jsonb)" if column in _JSON_COLUMNS else f":{column}"
        for column in _INSERT_COLUMNS
    )
    params = {
        column: json.dumps(row[column]) if column in _JSON_COLUMNS and row[column] is not None else row[column]
        for column in _INSERT_COLUMNS
    }
    db.execute(text(f"INSERT INTO pdf_documents ({columns}) VALUES ({values})"), params)


def _columns(db, table: str) -> set[str]:
    return set(db.scalars(text(
        "SELECT column_name FROM information_schema.columns WHERE table_name = :table"
    ), {"table": table}))


def _fixture_papers(owner_id: int) -> dict[str, dict]:
    provider = {
        "source_provider": "made_up_provider",
        "source_provider_reference_id": "ref-100",
        "source_provider_reference_curie": "MADEUP:100",
        "source_external_ids": {"doi": "10.0000/made-up.100"},
        "source_provider_source_file_id": "file-100",
        "source_provider_converted_artifact_id": "converted-100",
        "source_provider_pdf_artifact_id": "pdf-100",
        "source_md5": "a" * 32,
        "source_converted_artifact_sha256": "b" * 64,
        "source_file_class": "main",
        "source_file_extension": "md",
        "source_artifact_status": "final",
        "source_import_status": "completed",
        "source_imported_at": datetime(2026, 10, 2, tzinfo=timezone.utc),
        "source_markdown_path": "owner/source_markdown/2.md",
        "source_access_scope": "restricted",
        "source_access_group_ids": ["group-a", "group-b"],
        "viewer_mode": "local_pdf",
    }
    return {
        "upload": _paper(owner_id, 1, hierarchy_metadata={"top_level_sections": ["Intro"]},
                         pdfx_json_path="owner/pdfx_json/1.json"),
        "provider": _paper(owner_id, 2, **provider),
        "duplicate_provider": _paper(owner_id, 3, **{
            **provider, "source_markdown_path": "owner/source_markdown/3.md",
        }),
        "pending": _paper(owner_id, 4, status="pending", processing_started_at=None,
                          processing_completed_at=None, processed_json_path=None),
        "processing": _paper(owner_id, 5, status="processing", processing_completed_at=None),
        "failed_without_pieces": _paper(owner_id, 6, status="failed",
                                        error_message="Made-up parsing failure",
                                        processed_json_path=None),
        "text_only": _paper(owner_id, 7, viewer_mode="text_only", page_count=1),
        "frozen": _paper(owner_id, 8, viewer_mode="benchmark_frozen", page_count=3,
                         filename="benchmark-frozen", source_payload_path="benchmark_documents/x/source"),
        "proxy": _paper(owner_id, 9, viewer_mode="provider_pdf_proxy"),
        "linked_upload": _paper(owner_id, 10),
    }


def test_each_paper_becomes_one_main_file_with_its_old_values(historical_migration_database):
    command.upgrade(_config(), PREVIOUS_HEAD)
    with SessionLocal.begin() as db:
        owner_id = _owner(db, "migration-files-owner")
        papers = _fixture_papers(owner_id)
        for row in papers.values():
            _insert(db, row)
        db.execute(text(
            "INSERT INTO pdf_document_reference_links (document_id, provider, reference_curie, linked_at) "
            "VALUES (:id, 'made_up_provider', 'MADEUP:900', now())"
        ), {"id": papers["linked_upload"]["id"]})

    command.upgrade(_config(), REVISION)

    with SessionLocal() as db:
        assert not (_columns(db, "pdf_documents") & set(_MOVED))
        assert {"document_kind", "next_chunk_index"} <= _columns(db, "pdf_documents")
        assert db.scalar(text("SELECT count(*) FROM pdf_documents")) == len(papers)
        assert db.scalar(text("SELECT count(*) FROM pdf_document_files")) == len(papers)
        files = {
            row.document_id: row
            for row in db.execute(text("SELECT * FROM pdf_document_files")).mappings()
        }
        kinds = dict(db.execute(text("SELECT id, document_kind FROM pdf_documents")).all())
        counters = set(db.scalars(text("SELECT next_chunk_index FROM pdf_documents")))
        link = db.scalar(text("SELECT reference_curie FROM pdf_document_reference_links"))

    assert counters == {None}
    assert link == "MADEUP:900"
    expected_modes = {
        "upload": "local_pdf", "provider": "local_pdf", "duplicate_provider": "local_pdf",
        "pending": "local_pdf", "processing": "local_pdf", "failed_without_pieces": "local_pdf",
        "text_only": "text", "frozen": "text", "proxy": "provider_pdf_proxy",
        "linked_upload": "local_pdf",
    }
    for name, paper in papers.items():
        stored = files[paper["id"]]
        assert stored["role"] == "main"
        assert stored["tab_order"] == 0
        assert stored["display_name"] == paper["filename"]
        assert stored["added_at"] == paper["upload_timestamp"]
        assert stored["display_mode"] == expected_modes[name], name
        assert stored["status"] == ("ready" if paper["status"] == "completed" else "failed"), name
        assert stored["error_message"] == paper["error_message"]
        assert stored["chunk_index_start"] is None and stored["chunk_count"] is None
        assert stored["text_bytes"] is None
        for column in _MOVED:
            if column in {"viewer_mode", "page_count"}:
                continue
            assert stored[column] == paper[column], (name, column)
        if expected_modes[name] == "text":
            assert stored["page_count"] is None
        else:
            assert stored["page_count"] == paper["page_count"]
        assert kinds[paper["id"]] == ("benchmark_frozen" if name == "frozen" else "library")

    command.upgrade(_config(), "head")
    from src.models.sql.pdf_document import PDFDocument

    with SessionLocal() as db:
        provider_paper = db.get(PDFDocument, papers["provider"]["id"])
        assert provider_paper.source_provider_reference_curie == "MADEUP:100"
        assert provider_paper.main_file.source_md5 == "a" * 32
        assert provider_paper.reference_link is None
        linked = db.get(PDFDocument, papers["linked_upload"]["id"])
        assert linked.reference_link.reference_curie == "MADEUP:900"


def test_unknown_viewer_mode_stops_the_migration_and_changes_nothing(historical_migration_database):
    command.upgrade(_config(), PREVIOUS_HEAD)
    with SessionLocal.begin() as db:
        owner_id = _owner(db, "migration-files-unknown")
        unknown = _paper(owner_id, 1, viewer_mode="made_up_mode")
        _insert(db, unknown)
        _insert(db, _paper(owner_id, 2))

    with pytest.raises(Exception) as raised:
        command.upgrade(_config(), REVISION)
    assert type(raised.value).__name__ == "PaperFilesMigrationError"
    assert str(unknown["id"]) in str(raised.value)
    assert "1 papers have an unknown viewer_mode" in str(raised.value)

    with SessionLocal() as db:
        assert db.scalar(text("SELECT version_num FROM alembic_version")) == PREVIOUS_HEAD
        assert db.scalar(text("SELECT to_regclass('pdf_document_files')")) is None
        assert "viewer_mode" in _columns(db, "pdf_documents")
        assert db.scalar(text("SELECT count(*) FROM pdf_documents")) == 2


def test_downgrade_is_refused(historical_migration_database):
    command.upgrade(_config(), REVISION)
    with pytest.raises(RuntimeError, match="forward-only"):
        command.downgrade(_config(), PREVIOUS_HEAD)
    with SessionLocal() as db:
        assert db.scalar(text("SELECT version_num FROM alembic_version")) == REVISION


def test_migrated_papers_keep_their_constraints(historical_migration_database):
    command.upgrade(_config(), REVISION)
    with SessionLocal() as db:
        indexes = set(db.scalars(text(
            "SELECT indexname FROM pg_indexes WHERE tablename = 'pdf_document_files'"
        )))
        constraints = set(db.scalars(text(
            "SELECT conname FROM pg_constraint WHERE conrelid = 'pdf_document_files'::regclass"
        )))
        cascade = db.scalar(text(
            "SELECT confdeltype FROM pg_constraint "
            "WHERE conrelid = 'pdf_document_files'::regclass AND contype = 'f'"
        ))
    assert {
        "uq_pdf_document_files_one_main", "uq_pdf_document_files_main_file_hash",
        "ix_pdf_document_files_source_md5", "ix_pdf_document_files_converted_artifact",
    } <= indexes
    assert {
        "uq_pdf_document_files_file_path", "uq_pdf_document_files_source_file",
        "uq_pdf_document_files_tab_order", "ck_pdf_document_files_page_count",
        "ck_pdf_document_files_chunk_range", "ck_pdf_document_files_display_mode",
    } <= constraints
    assert cascade == "c"



def _expect_stop(match: str) -> str:
    with pytest.raises(Exception) as raised:
        command.upgrade(_config(), REVISION)
    assert type(raised.value).__name__ == "PaperFilesMigrationError"
    assert match in str(raised.value)
    with SessionLocal() as db:
        assert db.scalar(text("SELECT version_num FROM alembic_version")) == PREVIOUS_HEAD
        assert db.scalar(text("SELECT to_regclass('pdf_document_files')")) is None
    return str(raised.value)


@pytest.mark.parametrize(
    ("relax", "overrides"),
    [
        ("ALTER TABLE pdf_documents ALTER COLUMN file_path DROP NOT NULL", {"file_path": None}),
        ("ALTER TABLE pdf_documents ALTER COLUMN file_hash DROP NOT NULL", {"file_hash": None}),
        ("ALTER TABLE pdf_documents DROP CONSTRAINT ck_pdf_documents_file_size", {"file_size": 0}),
        ("ALTER TABLE pdf_documents DROP CONSTRAINT ck_pdf_documents_page_count", {"page_count": 0}),
        ("ALTER TABLE pdf_documents ALTER COLUMN page_count DROP NOT NULL",
         {"page_count": None, "viewer_mode": "local_pdf"}),
    ],
)
def test_a_missing_required_file_value_stops_the_migration(historical_migration_database, relax, overrides):
    command.upgrade(_config(), PREVIOUS_HEAD)
    with SessionLocal.begin() as db:
        # Simulate a database whose old constraints were relaxed by hand.
        db.execute(text(relax))
        owner_id = _owner(db, "migration-files-missing")
        broken = _paper(owner_id, 1, **overrides)
        _insert(db, broken)
        _insert(db, _paper(owner_id, 2))

    message = _expect_stop("lack a required file value")
    assert message.startswith("1 papers") and str(broken["id"]) in message


@pytest.mark.parametrize(
    ("column", "constraint"),
    [("file_hash", "uq_pdf_documents_file_hash"), ("file_path", "uq_pdf_documents_file_path")],
)
def test_a_shared_hash_or_path_stops_the_migration_naming_both_rows(
    historical_migration_database, column, constraint,
):
    command.upgrade(_config(), PREVIOUS_HEAD)
    with SessionLocal.begin() as db:
        # A hand-run 2025 SQL script dropped these constraints; some databases may lack them.
        db.execute(text(f"ALTER TABLE pdf_documents DROP CONSTRAINT {constraint}"))
        owner_id = _owner(db, "migration-files-shared")
        first = _paper(owner_id, 1)
        second = _paper(owner_id, 2, **{column: first[column]})
        for row in (first, second, _paper(owner_id, 3)):
            _insert(db, row)

    message = _expect_stop(f"2 papers share a {column}")
    assert str(first["id"]) in message and str(second["id"]) in message
    assert first[column] not in message
