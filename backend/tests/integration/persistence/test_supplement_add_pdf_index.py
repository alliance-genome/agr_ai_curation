"""A converted PDF supplement added as its PDF, on real PostgreSQL and the test Weaviate (ALL-1460, P7c2).

The listing is built from core ``SourceArtifact`` records (no Alliance package in
this image); the provider only serves downloads: the original PDF (made here with
pypdf) and ABC-shaped converted text. All values are made up.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
from pathlib import Path
from uuid import uuid4

import pytest
from pypdf import PdfWriter

from src.api import pdf_viewer
from src.lib.document_files.readable import resolve_readable_files
from src.lib.document_sources import file_listing_service
from src.lib.document_sources.file_listing import (
    FileListing,
    ListedFile,
    ListedFileCheck,
    ListedFileCheckStatus,
    ListedFileStatus,
)
from src.lib.document_sources.models import SourceArtifactFormat, SourceArtifactRole, SupplementFileCategory
from src.lib.document_sources.supplement_add import ChosenFile
from src.lib.weaviate_client import chunks as chunk_reads
from src.models.sql.database import SessionLocal

from .conftest import TEST_USER_ID
from .test_supplement_add_search_index import (  # noqa: F401  (fixtures)
    _add,
    _artifact,
    _paper,
    _rows,
    index,
    migrated_database,
    owner_id,
)


def _pdf(pages: int) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=612, height=792)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


PDF = _pdf(4)
PDF_TEXT = b"## Supplementary methods\n\nMade-up methods of the supplement.\n\n## Table S7\n\n| strain | score |\n| --- | --- |\n| s1 | 1 |\n"


def _pdf_entry() -> ListedFile:
    original = _artifact("60", SourceArtifactRole.SOURCE_PDF, SourceArtifactFormat.PDF,
                         md5sum=hashlib.md5(PDF).hexdigest(), file_class="supplement", file_extension="pdf")
    converted = _artifact("61", SourceArtifactRole.CONVERTED_TEXT, SourceArtifactFormat.MARKDOWN,
                          md5sum=hashlib.md5(PDF_TEXT).hexdigest(), file_class="converted_merged_supplement", file_extension="md")
    return ListedFile(
        provider_file_id="60", converted_text_id="61", display_name=original.display_name, file_role="supplement",
        status=ListedFileStatus.READY, check=ListedFileCheck(ListedFileCheckStatus.NOT_CHECKED),
        in_this_paper=False, md5=original.md5sum, converted_md5=converted.md5sum, artifact_format="pdf",
        category=SupplementFileCategory.CONVERTIBLE, single_title_required=False, tickable=True,
        not_tickable_reason=None, last_provider_observation=None, source_artifact=original,
        converted_artifact=converted,
    )


@pytest.fixture
def pdf_listing(monkeypatch, index):  # noqa: F811
    async def listing(document_id, *, owner_user_id, provider, context):
        return FileListing(
            provider="made_up_provider", reference_id="101", reference_curie="MADE:101", main_status="ready",
            files=(_pdf_entry(),), not_shown_count=0, duplicate_copies_count=0, check_version="made-up",
            existing_document_id=None,
        )

    monkeypatch.setattr(file_listing_service, "list_files_for_paper", listing)
    return index


@pytest.mark.asyncio
async def test_a_pdf_supplement_is_stored_served_and_read_from_its_text(owner_id, pdf_listing, tmp_path):  # noqa: F811
    paper = await asyncio.to_thread(_paper, owner_id, pdf_listing)
    chosen = ChosenFile("60", md5=hashlib.md5(PDF).hexdigest(), converted_md5=hashlib.md5(PDF_TEXT).hexdigest())

    result = await _add(paper, owner_id, pdf_listing, [chosen], job_id=uuid4(),
                        payloads={"60": PDF, "61": PDF_TEXT})

    (added,) = result.added
    main, row = _rows(paper)
    assert (row.role, row.status, row.display_mode, row.page_count) == ("supplement", "ready", "local_pdf", 4)
    assert row.file_path == f"{TEST_USER_ID}/{paper}/supplement_pdf/{row.id}.pdf"
    assert (Path(tmp_path) / row.file_path).read_bytes() == PDF
    assert row.file_size == len(PDF) and row.source_provider_pdf_artifact_id == "60"
    # The viewer serves the stored PDF with its page count.
    with SessionLocal() as db:
        files = pdf_viewer.list_document_files(document_id=paper, db=db, user={"sub": TEST_USER_ID})
        served = pdf_viewer.get_document_file_content(document_id=paper, file_id=row.id, db=db,
                                                       user={"sub": TEST_USER_ID})
    pdf_ref = files.files[1]
    assert (pdf_ref.display_mode, pdf_ref.page_count, pdf_ref.text_url) == ("local_pdf", 4, None)
    assert pdf_ref.content_url and Path(served.path).read_bytes() == PDF
    # Its pieces come from the text, readable, and claim no page.
    readable = resolve_readable_files(str(paper), TEST_USER_ID)
    pieces = await chunk_reads.get_chunks(str(paper), {"page": 1, "page_size": 100}, TEST_USER_ID, readable=readable)
    supplement_pieces = [chunk for chunk in pieces["chunks"] if chunk["chunk_index"] >= row.chunk_index_start]
    assert supplement_pieces and all(not chunk.get("page_number") for chunk in supplement_pieces)
    assert any("Made-up methods" in chunk["content"] for chunk in supplement_pieces)
    assert main.display_mode == "local_pdf"


@pytest.mark.asyncio
async def test_a_failed_pdf_write_leaves_no_stored_pdf_and_the_paper_unchanged(  # noqa: F811
    owner_id, pdf_listing, tmp_path, monkeypatch,  # noqa: F811
):
    paper = await asyncio.to_thread(_paper, owner_id, pdf_listing)
    chosen = ChosenFile("60", md5=hashlib.md5(PDF).hexdigest(), converted_md5=hashlib.md5(PDF_TEXT).hexdigest())

    async def fail(*_args, **_kwargs):
        raise RuntimeError("made-up storage failure")

    monkeypatch.setattr("src.lib.document_sources.ingestion.store_file_pieces", fail)
    with pytest.raises(RuntimeError, match="made-up storage failure"):
        await _add(paper, owner_id, pdf_listing, [chosen], job_id=uuid4(), payloads={"60": PDF, "61": PDF_TEXT})

    assert [row.role for row in _rows(paper)] == ["main"]
    assert not list((Path(tmp_path) / TEST_USER_ID / str(paper) / "supplement_pdf").glob("*"))
    assert not list((Path(tmp_path) / ".add_spool").glob("*"))
