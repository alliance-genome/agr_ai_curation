"""The one-time fill step and the read-only piece-number check, on Postgres and Weaviate.

Rows look like papers right after the paper-files migration: one main file,
no piece range, no text size, and no next piece number. All values are made up.
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config

from src.lib.document_files import fill_existing, piece_numbers
from src.models.sql.database import SessionLocal
from src.models.sql.pdf_document import PDFDocument, PDFDocumentFile
from src.models.sql.user import User

from .conftest import TEST_TENANT_NAME, TEST_USER_ID


BACKEND_ROOT = Path(__file__).resolve().parents[3]
ELEMENTS = [
    {"text": "Made-up title", "metadata": {"doc_item_label": "section_header"}},
    {"text": "Ca²⁺ rose 5×.", "metadata": {}},
    {"text": "Page 3 of 9", "metadata": {"doc_item_label": "page_footer"}},
]
EXPECTED_TEXT_BYTES = len("Made-up title".encode()) + len("Ca²⁺ rose 5×.".encode())


@pytest.fixture
def migrated_library(historical_migration_database, weaviate_connection, setup_collections, clean_chunks,
                     tmp_path, monkeypatch):
    command.upgrade(Config(str(BACKEND_ROOT / "alembic.ini")), "head")
    monkeypatch.setattr("src.config.get_pdf_storage_path", lambda: tmp_path)
    monkeypatch.setattr("src.lib.weaviate_client.connection.get_connection", lambda: weaviate_connection)
    with SessionLocal.begin() as db:
        owner = User(auth_sub=TEST_USER_ID, is_active=True)
        db.add(owner)
        db.flush()
        owner_id = owner.id

    def add_paper(*, status: str, file_status: str, pieces: list[int], processed: bool = True,
                  elements: list | None = None, write_json: bool = True) -> str:
        document_id = uuid4()
        processed_path = None
        if processed:
            processed_path = f"owner/processed_json/{document_id}.json"
            if write_json:
                path = tmp_path / processed_path
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(ELEMENTS if elements is None else elements), encoding="utf-8")
        with SessionLocal.begin() as db:
            paper = PDFDocument(
                id=document_id, user_id=owner_id, filename=f"made-up-{document_id}.pdf",
                status=status, next_chunk_index=None,
            )
            paper.files.append(PDFDocumentFile(
                role="main", display_name=paper.filename, status=file_status, tab_order=0,
                display_mode="local_pdf", file_path=f"owner/{document_id}/made-up.pdf",
                file_hash=uuid4().hex + uuid4().hex, file_size=10, page_count=2,
                processed_json_path=processed_path,
            ))
            db.add(paper)
        with weaviate_connection.session() as client:
            collection = client.collections.get("DocumentChunk").with_tenant(TEST_TENANT_NAME)
            for number in pieces:
                collection.data.insert({
                    "documentId": str(document_id), "chunkIndex": number,
                    "content": f"made-up piece {number}",
                })
        return str(document_id)

    return add_paper


def _file(document_id: str) -> tuple[PDFDocumentFile, int | None]:
    with SessionLocal() as db:
        paper = db.get(PDFDocument, document_id)
        main_file = paper.main_file
        db.expunge(main_file)
        return main_file, paper.next_chunk_index


def test_fill_step_records_ranges_sizes_and_counters_and_repeats_cleanly(migrated_library):
    ready = migrated_library(status="completed", file_status="ready", pieces=[0, 1, 2])
    pending_with_pieces = migrated_library(status="pending", file_status="failed", pieces=[0, 1], processed=False)
    failed_without_pieces = migrated_library(status="failed", file_status="failed", pieces=[], processed=False)

    report = fill_existing.run(verify_only=False)

    assert report["papers_written"] == 3
    assert report["main_files_measured"] == 3
    assert report["main_files_without_processed_json"] == 2
    assert fill_existing._verify_ok(report["verify"])
    ready_file, ready_next = _file(ready)
    assert (ready_file.chunk_index_start, ready_file.chunk_index_end, ready_file.chunk_count) == (0, 2, 3)
    assert ready_file.text_bytes == EXPECTED_TEXT_BYTES
    assert ready_next == 3
    pending_file, pending_next = _file(pending_with_pieces)
    assert (pending_file.chunk_index_start, pending_file.chunk_index_end) == (0, 1)
    assert pending_file.status == "failed" and pending_file.text_bytes is None
    assert pending_next == 2
    failed_file, failed_next = _file(failed_without_pieces)
    assert failed_file.chunk_index_start is None and failed_file.chunk_count == 0
    assert failed_next == 0

    again = fill_existing.run(verify_only=False)
    assert again["papers_written"] == 0
    assert again["papers_already_filled"] == 3
    assert fill_existing._verify_ok(fill_existing.run(verify_only=True)["verify"])


def test_a_gap_in_piece_numbers_stops_the_fill_step_before_any_write(migrated_library, capsys):
    unbroken = migrated_library(status="completed", file_status="ready", pieces=[0, 1])
    gapped = migrated_library(status="completed", file_status="ready", pieces=[0, 2, 3])

    with pytest.raises(fill_existing.FillStepError) as raised:
        fill_existing.run(verify_only=False)
    assert raised.value.document_ids == [gapped]
    unbroken_file, unbroken_next = _file(unbroken)
    assert unbroken_file.chunk_count is None and unbroken_next is None

    assert fill_existing.main([]) == 1
    printed = json.loads(capsys.readouterr().out)
    assert printed["document_ids"] == [gapped]


def test_a_ready_file_without_pieces_stops_the_fill_step(migrated_library):
    empty = migrated_library(status="completed", file_status="ready", pieces=[])
    with pytest.raises(fill_existing.FillStepError, match="ready files without stored pieces") as raised:
        fill_existing.run(verify_only=False)
    assert raised.value.document_ids == [empty]


def test_duplicate_piece_numbers_are_not_unbroken(migrated_library):
    duplicated = migrated_library(status="completed", file_status="ready", pieces=[0, 1, 1, 3])
    with pytest.raises(fill_existing.FillStepError, match="not unbroken") as raised:
        fill_existing.run(verify_only=False)
    assert raised.value.document_ids == [duplicated]


def test_read_only_check_reports_counts_and_internal_ids_only(migrated_library, capsys):
    migrated_library(status="completed", file_status="ready", pieces=[0, 1])
    gapped = migrated_library(status="completed", file_status="ready", pieces=[1, 2])

    summary = piece_numbers.run_read_only_check()
    assert summary["papers"] == 2
    assert summary["papers_with_pieces"] == 2
    assert summary["pieces"] == 4
    assert summary["papers_not_unbroken_ids"] == [gapped]
    assert summary["not_unbroken_detail"][gapped] == {"count": 2, "distinct": 2, "lowest": 1, "highest": 2}
    assert summary["by_status"]["completed"]["papers"] == 2

    assert piece_numbers.main(["--json"]) == 1
    printed = capsys.readouterr().out
    assert "made-up-" not in printed and "made-up piece" not in printed
    with SessionLocal() as db:
        assert db.get(PDFDocument, gapped).next_chunk_index is None


def test_a_ready_file_whose_processed_json_is_missing_on_disk_stops_the_fill_step(migrated_library):
    missing = migrated_library(status="completed", file_status="ready", pieces=[0], write_json=False)
    with pytest.raises(fill_existing.FillStepError, match="ready files without processed JSON") as raised:
        fill_existing.run(verify_only=False)
    assert raised.value.document_ids == [missing]


def test_processed_json_with_an_element_without_text_stops_the_fill_step(migrated_library):
    broken = migrated_library(status="failed", file_status="failed", pieces=[0],
                              elements=[{"metadata": {}}])
    with pytest.raises(fill_existing.FillStepError, match="cannot be read as elements") as raised:
        fill_existing.run(verify_only=False)
    assert raised.value.document_ids == [broken]
