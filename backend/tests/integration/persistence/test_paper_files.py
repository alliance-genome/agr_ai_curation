"""Persistence coverage for papers and their files (KANBAN-1976 / ALL-1460, P5)."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import IntegrityError

from src.lib.document_files.service import (
    ChunkRangeUnavailableError,
    DocumentFileError,
    element_text_bytes,
    mark_file_ready,
    ready_piece_total,
    reserve_file_chunk_range,
)
from src.lib.pdf_jobs.upload_intake_service import _PDF_DOCUMENT_DUPLICATE_CONSTRAINTS
from src.models.sql.database import SessionLocal
from src.models.sql.pdf_document import MissingMainFileError, PDFDocument, PDFDocumentFile
from tests.pdf_document_test_support import ensure_test_pdf_owner


BACKEND_ROOT = Path(__file__).resolve().parents[3]
PREFIX = "paper_files_test_"


@pytest.fixture(scope="module", autouse=True)
def migrated_database():
    command.upgrade(Config(str(BACKEND_ROOT / "alembic.ini")), "head")


@pytest.fixture
def owner_id():
    with SessionLocal.begin() as db:
        owner = ensure_test_pdf_owner(db, auth_sub="paper-files-owner")
    yield owner
    with SessionLocal.begin() as db:
        db.execute(delete(PDFDocument).where(PDFDocument.filename.like(f"{PREFIX}%")))


def _main_file(document_id: UUID, **overrides) -> PDFDocumentFile:
    values = dict(
        document_id=document_id, role="main", display_name="made-up.pdf", status="pending",
        tab_order=0, display_mode="local_pdf", file_path=f"owner/{document_id}/made-up.pdf",
        file_hash=document_id.hex * 2, file_size=100, page_count=3,
    )
    values.update(overrides)
    return PDFDocumentFile(**values)


def _paper(owner_id: int, *, next_chunk_index: int | None = 0, **file_overrides) -> tuple[UUID, UUID]:
    document_id = uuid4()
    with SessionLocal.begin() as db:
        db.add(PDFDocument(
            id=document_id, user_id=owner_id, filename=f"{PREFIX}{document_id}.pdf",
            next_chunk_index=next_chunk_index,
        ))
        db.flush()
        main_file = _main_file(document_id, **file_overrides)
        db.add(main_file)
        db.flush()
        return document_id, main_file.id


def test_a_new_paper_main_file_takes_pieces_from_zero_and_numbers_are_never_reused(owner_id):
    document_id, file_id = _paper(owner_id)

    assert reserve_file_chunk_range(document_id, file_id, 5) == 0
    with pytest.raises(DocumentFileError, match="already has a piece range"):
        reserve_file_chunk_range(document_id, file_id, 2)

    with SessionLocal.begin() as db:
        second = _main_file(document_id, role="supplement", tab_order=1,
                            file_path=f"owner/{document_id}/second.md", display_mode="text",
                            page_count=None)
        db.add(second)
        db.flush()
        second_id = second.id
    assert reserve_file_chunk_range(document_id, second_id, 2) == 5

    with SessionLocal() as db:
        paper = db.get(PDFDocument, document_id)
        files = {row.id: row for row in paper.files}
        assert paper.next_chunk_index == 7
        assert (files[file_id].chunk_index_start, files[file_id].chunk_index_end) == (0, 4)
        assert (files[second_id].chunk_index_start, files[second_id].chunk_index_end) == (5, 6)
        assert ready_piece_total(db, document_id) == 0

    mark_file_ready(file_id, text_bytes=42)
    mark_file_ready(second_id, text_bytes=7)
    with SessionLocal() as db:
        assert ready_piece_total(db, document_id) == 7
        # A file without a provider import keeps no import status.
        assert db.get(PDFDocumentFile, file_id).source_import_status is None


def test_a_provider_import_status_follows_the_file(owner_id):
    document_id, file_id = _paper(owner_id, source_import_status="pending")
    reserve_file_chunk_range(document_id, file_id, 1)
    mark_file_ready(file_id, text_bytes=1)
    with SessionLocal() as db:
        assert db.get(PDFDocumentFile, file_id).source_import_status == "completed"


def test_a_range_cannot_be_reserved_for_another_papers_file(owner_id):
    document_id, _ = _paper(owner_id)
    other_document_id, other_file_id = _paper(owner_id)
    with pytest.raises(DocumentFileError, match="is not a file of document"):
        reserve_file_chunk_range(document_id, other_file_id, 2)
    with SessionLocal() as db:
        assert db.get(PDFDocument, document_id).next_chunk_index == 0
        assert db.get(PDFDocumentFile, other_file_id).chunk_index_start is None
    assert other_document_id != document_id


def test_an_unmeasured_paper_cannot_reserve_a_range(owner_id):
    document_id, file_id = _paper(owner_id, next_chunk_index=None)
    with pytest.raises(ChunkRangeUnavailableError, match="fill step"):
        reserve_file_chunk_range(document_id, file_id, 3)
    with SessionLocal() as db:
        assert db.get(PDFDocumentFile, file_id).chunk_index_start is None


def test_a_ready_file_needs_its_range(owner_id):
    _, file_id = _paper(owner_id)
    with pytest.raises(DocumentFileError, match="no recorded piece range"):
        mark_file_ready(file_id, text_bytes=1)


def test_a_paper_has_one_main_file_and_main_file_hashes_stay_unique(owner_id):
    document_id, _ = _paper(owner_id)
    with pytest.raises(IntegrityError) as second_main:
        with SessionLocal.begin() as db:
            db.add(_main_file(document_id, tab_order=1, file_path=f"owner/{document_id}/other.pdf",
                              file_hash="f" * 64))
    assert second_main.value.orig.diag.constraint_name == "uq_pdf_document_files_one_main"

    other_id = uuid4()
    with pytest.raises(IntegrityError) as duplicate:
        with SessionLocal.begin() as db:
            db.add(PDFDocument(id=other_id, user_id=owner_id, filename=f"{PREFIX}{other_id}.pdf"))
            db.flush()
            db.add(_main_file(other_id, file_hash=document_id.hex * 2))
    constraint = duplicate.value.orig.diag.constraint_name
    assert constraint == "uq_pdf_document_files_main_file_hash"
    assert constraint in _PDF_DOCUMENT_DUPLICATE_CONSTRAINTS


@pytest.mark.parametrize(
    ("display_mode", "page_count"),
    [("text", 1), ("local_pdf", None), ("local_pdf", 0), ("provider_pdf_proxy", None)],
)
def test_page_counts_exist_only_for_pdf_files(owner_id, display_mode, page_count):
    document_id = uuid4()
    with pytest.raises(IntegrityError) as raised:
        with SessionLocal.begin() as db:
            db.add(PDFDocument(id=document_id, user_id=owner_id, filename=f"{PREFIX}{document_id}.pdf"))
            db.flush()
            db.add(_main_file(document_id, display_mode=display_mode, page_count=page_count))
    assert raised.value.orig.diag.constraint_name == "ck_pdf_document_files_page_count"


def test_deleting_a_paper_deletes_its_files_and_its_reference_link(owner_id):
    document_id, file_id = _paper(owner_id)
    with SessionLocal.begin() as db:
        db.execute(text(
            "INSERT INTO pdf_document_reference_links (document_id, provider, reference_curie, linked_at) "
            "VALUES (:id, 'made_up_provider', 'MADEUP:1', now())"
        ), {"id": document_id})
    with SessionLocal.begin() as db:
        db.delete(db.get(PDFDocument, document_id))
    with SessionLocal() as db:
        assert db.get(PDFDocumentFile, file_id) is None
        assert db.scalar(select(func.count()).select_from(PDFDocumentFile).where(
            PDFDocumentFile.document_id == document_id
        )) == 0
        assert db.scalar(text(
            "SELECT count(*) FROM pdf_document_reference_links WHERE document_id = :id"
        ), {"id": document_id}) == 0


def test_a_paper_without_a_main_file_is_a_named_error(owner_id):
    document_id = uuid4()
    with SessionLocal.begin() as db:
        db.add(PDFDocument(id=document_id, user_id=owner_id, filename=f"{PREFIX}{document_id}.pdf"))
    with SessionLocal() as db:
        with pytest.raises(MissingMainFileError):
            db.get(PDFDocument, document_id).main_file


def test_text_size_counts_the_text_that_is_cut_into_pieces():
    elements = [
        {"text": "αβ", "metadata": {}},
        {"text": "footer", "metadata": {"doc_item_label": "page_footer"}},
        {"text": "", "metadata": {"doc_item_label": "text"}},
    ]
    assert element_text_bytes(elements) == len("αβ".encode())
    with pytest.raises(DocumentFileError):
        element_text_bytes([{"metadata": {}}])
