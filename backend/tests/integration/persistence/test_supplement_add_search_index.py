"""Adding supplements against the real test Weaviate (P7c1): range deletes, overlap, and a usable paper.

The paper's pieces live in the real test Weaviate, so every range filter is the
search index's own. The paper's file listing is built from core
``SourceArtifact`` records (the persistence image has no Alliance package) and the
provider only serves downloads. All values are made up.
"""

from __future__ import annotations

import asyncio
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi import HTTPException
from sqlalchemy import delete, select
from weaviate.classes.query import Filter

from src.api import documents as documents_api
from src.api import pdf_viewer
from src.lib.document_files.readable import resolve_readable_files
from src.lib.document_sources import file_listing_service, supplement_add
from src.lib.document_sources.access import DocumentSourceRequestContext
from src.lib.document_sources.file_assessment import FileCheckLimits
from src.lib.document_sources.file_listing import (
    FileListing,
    ListedFile,
    ListedFileCheck,
    ListedFileCheckStatus,
    ListedFileStatus,
)
from src.lib.document_sources.models import (
    ProviderBearerKind,
    SourceAccessPolicy,
    SourceAccessScope,
    SourceArtifact,
    SourceArtifactFormat,
    SourceArtifactRole,
    SourceArtifactStatus,
    SupplementFileCategory,
)
from src.lib.document_sources.supplement_add import ChosenFile
from src.lib.pdf_jobs import service as pdf_job_service
from src.lib.pdf_jobs.service import ActiveJobExistsError
from src.lib.pipeline.store import store_chunks_to_weaviate
from src.lib.weaviate_client import chunks as chunk_reads
from src.models.sql.database import SessionLocal
from src.models.sql.pdf_document import PDFDocument, PDFDocumentFile
from src.models.sql.pdf_processing_job import PdfJobStatus, PdfProcessingJob
from tests.pdf_document_test_support import ensure_test_pdf_owner, new_paper

from .conftest import TEST_TENANT_NAME, TEST_USER_ID

PREFIX = "p7c1_index_test_"
COMPLETED_AT = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
CONTEXT = DocumentSourceRequestContext(
    provider_groups=("group-a",), authorized_group_ids=("group-a",), bearer_kind=ProviderBearerKind.CURATOR,
    curator_token="curator-token",
)
WORKBOOK = b"# Made_up_S20\n\n## Sheet1\n\nMade-up rows.\n\n| strain | score |\n| --- | --- |\n| s1 | 1 |\n"
WORKBOOK_2 = b"# Made_up_S40\n\n## Sheet1\n\n| gene | count |\n| --- | --- |\n| g1 | 3 |\n"
ACTIVE = {PdfJobStatus.PENDING.value, PdfJobStatus.RUNNING.value, PdfJobStatus.CANCEL_REQUESTED.value}


@pytest.fixture(scope="module", autouse=True)
def migrated_database():
    command.upgrade(Config(str(Path(__file__).resolve().parents[3] / "alembic.ini")), "head")


@pytest.fixture
def owner_id():
    with SessionLocal.begin() as db:
        owner = ensure_test_pdf_owner(db, auth_sub=TEST_USER_ID)
    yield owner
    with SessionLocal.begin() as db:
        db.execute(delete(PDFDocument).where(PDFDocument.filename.like(f"{PREFIX}%")))


@pytest.fixture
def index(weaviate_connection, setup_collections, clean_chunks, monkeypatch, tmp_path):
    """The real test Weaviate for every read, store and delete of this test."""

    monkeypatch.setattr(chunk_reads, "get_connection", lambda: weaviate_connection)
    monkeypatch.setattr("src.lib.weaviate_client.documents.get_connection", lambda: weaviate_connection)
    monkeypatch.setattr("src.lib.pipeline.store.update_document_status_detailed", AsyncMock())
    monkeypatch.setattr("src.config.get_pdf_storage_path", lambda: tmp_path)
    monkeypatch.setattr(pdf_viewer, "get_pdf_storage_path", lambda: tmp_path)
    monkeypatch.setattr(documents_api, "get_pdf_storage_path", lambda: tmp_path)
    limits = FileCheckLimits(
        max_bytes=10_000, few_characters_below=10, row_token_limit=200, count_tokens=len,
        token_encoding="made-up-encoding",
    )
    monkeypatch.setattr(supplement_add, "current_file_check_limits", lambda: limits)
    stored: dict = {}
    monkeypatch.setattr(
        file_listing_service, "_stores",
        lambda _provider: (lambda *key: stored.get(key), lambda *args: stored.__setitem__(args[:3], args[3])),
    )
    monkeypatch.setattr(file_listing_service, "list_files_for_paper", _listing)
    return weaviate_connection


def _indexes(connection, document_id: str) -> list[int]:
    with connection.session() as client:
        collection = client.collections.get("DocumentChunk").with_tenant(TEST_TENANT_NAME)
        result = collection.query.fetch_objects(
            filters=Filter.by_property("documentId").equal(document_id), limit=1000,
        )
    return sorted(obj.properties["chunkIndex"] for obj in result.objects)


async def _store(connection, document_id: str, start: int, end: int, section: str = "Results") -> None:
    await store_chunks_to_weaviate(
        [
            {
                "chunk_index": index, "content": f"Piece {index} text.", "element_type": "NarrativeText",
                "page_number": None, "section_title": section, "parent_section": section, "subsection": None,
                "metadata": {},
            }
            for index in range(start, end + 1)
        ],
        document_id, connection, TEST_USER_ID, chunk_range=(start, end),
    )


def _paper(owner_id: int, connection, *, main_end: int = 2) -> UUID:
    """A finished paper whose ready main PDF holds pieces 0..main_end in the real index."""

    document_id = uuid4()
    with SessionLocal.begin() as db:
        db.add(new_paper(
            id=document_id, user_id=owner_id, filename=f"{PREFIX}{document_id}.pdf", status="completed",
            processing_completed_at=COMPLETED_AT, file_path=f"{TEST_USER_ID}/{document_id}/made-up.pdf",
            file_hash=document_id.hex * 2, file_size=100, page_count=2, next_chunk_index=main_end + 1,
            chunk_index_start=0, chunk_index_end=main_end, chunk_count=main_end + 1, text_bytes=1_000,
            source_provider="made_up_provider", source_provider_reference_id="101",
            source_provider_reference_curie="MADE:101", source_provider_source_file_id="10",
        ))
    asyncio.run(_store(connection, str(document_id), 0, main_end))
    return document_id


class _Provider:
    provider_id = "made_up_provider"

    def __init__(self, payloads):
        self.payloads = payloads

    async def download_artifact(self, artifact_id, *, request_bearer_token, max_bytes):
        return self.payloads[artifact_id]


def _artifact(artifact_id, role, artifact_format, **metadata) -> SourceArtifact:
    return SourceArtifact(
        provider="made_up_provider", artifact_id=artifact_id, role=role, artifact_format=artifact_format,
        status=SourceArtifactStatus.AVAILABLE, display_name=f"Made_up_S{artifact_id}", md5sum=f"md5-{artifact_id}",
        access_policy=SourceAccessPolicy(scope=SourceAccessScope.RESTRICTED, group_ids=("group-a",)),
        metadata=metadata,
    )


def _entry(original_id: int, in_paper: frozenset[str]) -> ListedFile:
    original = _artifact(
        str(original_id), SourceArtifactRole.SOURCE_SUPPLEMENT, SourceArtifactFormat.UNKNOWN,
        file_class="supplement", file_extension="xlsx",
    )
    converted = _artifact(
        str(original_id + 1), SourceArtifactRole.CONVERTED_TEXT, SourceArtifactFormat.MARKDOWN,
        file_class="converted_merged_supplement", file_extension="md",
    )
    return ListedFile(
        provider_file_id=original.artifact_id, converted_text_id=converted.artifact_id,
        display_name=original.display_name, file_role="supplement", status=ListedFileStatus.READY,
        check=ListedFileCheck(ListedFileCheckStatus.NOT_CHECKED), in_this_paper=original.artifact_id in in_paper,
        md5=original.md5sum, converted_md5=converted.md5sum, artifact_format="unknown",
        category=SupplementFileCategory.CONVERTIBLE, single_title_required=True, tickable=True,
        not_tickable_reason=None, last_provider_observation=None, source_artifact=original,
        converted_artifact=converted,
    )


async def _listing(document_id, *, owner_user_id, provider, context):
    with SessionLocal() as db:
        in_paper = frozenset(
            row.source_provider_source_file_id
            for row in db.get(PDFDocument, document_id).files
            if row.source_provider_source_file_id and row.status != "failed"
        )
    return FileListing(
        provider="made_up_provider", reference_id="101", reference_curie="MADE:101", main_status="ready",
        files=tuple(_entry(item, in_paper) for item in (20, 40)), not_shown_count=0,
        duplicate_copies_count=0, check_version="made-up", existing_document_id=None,
    )


def _chosen(original_id: int) -> ChosenFile:
    return ChosenFile(str(original_id), md5=f"md5-{original_id}", converted_md5=f"md5-{original_id + 1}")


async def _add(document_id, owner_id, connection, files, *, job_id, checkpoint=None, payloads=None):
    return await supplement_add.add_files_to_paper(
        document_id, owner_id, CONTEXT, files, job_id=job_id,
        provider=_Provider(payloads or {"21": WORKBOOK, "41": WORKBOOK_2}),
        weaviate_client=connection, stage_checkpoint=checkpoint or AsyncMock(),
    )


def _rows(document_id: UUID) -> list[PDFDocumentFile]:
    with SessionLocal() as db:
        return db.execute(
            select(PDFDocumentFile).where(PDFDocumentFile.document_id == document_id).order_by(PDFDocumentFile.tab_order)
        ).scalars().all()


async def _usable(document_id: UUID, connection, *, readable_ranges: list[tuple[int, int]]) -> None:
    """Readable and chattable (reads keep to ready files), viewable, and the paper unchanged."""

    readable = resolve_readable_files(str(document_id), TEST_USER_ID)
    assert [(item.chunk_index_start, item.chunk_index_end) for item in readable.files] == readable_ranges
    listed = await chunk_reads.get_chunks(
        str(document_id), {"page": 1, "page_size": 100}, TEST_USER_ID, readable=readable,
    )
    expected = [index for start, end in readable_ranges for index in range(start, end + 1)]
    assert [chunk["chunk_index"] for chunk in listed["chunks"]] == expected
    with SessionLocal() as db:
        files = pdf_viewer.list_document_files(document_id=document_id, db=db, user={"sub": TEST_USER_ID})
        paper = db.get(PDFDocument, document_id)
        assert paper.status == "completed" and paper.processing_completed_at == COMPLETED_AT
    assert files.files[0].role == "main"


def _deletable(document_id: UUID, owner_id: int) -> bool:
    """The delete endpoint's own rule: no active latest job, after reconciling a stale one."""

    latest = pdf_job_service.get_latest_job_for_document(
        document_id=str(document_id), user_id=owner_id, reconcile_stale=True,
    )
    return latest is None or latest.status not in ACTIVE


# --- delete_chunk_range against the real index ---------------------------------------------


def test_a_range_delete_removes_exactly_its_range_and_no_other_files_or_papers_pieces(owner_id, index):
    paper = _paper(owner_id, index, main_end=2)
    asyncio.run(_store(index, str(paper), 3, 4))
    asyncio.run(_store(index, str(paper), 5, 6))
    other = _paper(owner_id, index, main_end=6)

    deleted = chunk_reads.delete_chunk_range(str(paper), TEST_USER_ID, start=3, end=4)

    assert deleted == 2
    assert _indexes(index, str(paper)) == [0, 1, 2, 5, 6]  # 2 and 5, the pieces either side, stay
    assert _indexes(index, str(other)) == [0, 1, 2, 3, 4, 5, 6]


def test_a_range_with_no_pieces_deletes_nothing_and_an_inverted_range_is_refused(owner_id, index):
    paper = _paper(owner_id, index, main_end=2)
    assert chunk_reads.delete_chunk_range(str(paper), TEST_USER_ID, start=3, end=9) == 0
    with pytest.raises(ValueError):
        chunk_reads.delete_chunk_range(str(paper), TEST_USER_ID, start=4, end=3)
    with pytest.raises(ValueError):
        chunk_reads.delete_chunk_range(str(paper), TEST_USER_ID, start=-1, end=1)
    assert _indexes(index, str(paper)) == [0, 1, 2]


@pytest.mark.asyncio
async def test_a_range_delete_while_another_file_is_being_stored_keeps_that_files_pieces(owner_id, index):
    paper = await asyncio.to_thread(_paper, owner_id, index, main_end=2)
    await _store(index, str(paper), 3, 4)

    await asyncio.gather(
        _store(index, str(paper), 5, 8),
        asyncio.to_thread(chunk_reads.delete_chunk_range, str(paper), TEST_USER_ID, start=3, end=4),
    )
    assert _indexes(index, str(paper)) == [0, 1, 2, 5, 6, 7, 8]


# --- a real add: pieces, sections, the viewer --------------------------------------------------


@pytest.mark.asyncio
async def test_an_add_stores_its_pieces_after_the_main_files_and_the_viewer_shows_their_sections(owner_id, index):
    paper = await asyncio.to_thread(_paper, owner_id, index, main_end=2)
    job = supplement_add.start_supplement_add(paper, owner_id, [_chosen(20)])

    result = await _add(paper, owner_id, index, [_chosen(20)], job_id=UUID(job.job_id))

    main, added = _rows(paper)
    assert result.files[0].file_id == added.id and added.status == "ready"
    # A stored map would be applied a second time by the text route.
    assert added.hierarchy_metadata is None
    assert _indexes(index, str(paper)) == list(range(0, added.chunk_index_end + 1))
    await _usable(paper, index, readable_ranges=[(0, 2), (3, added.chunk_index_end)])
    with SessionLocal() as db:
        text = pdf_viewer.get_document_file_text(
            document_id=paper, file_id=added.id, db=db, user={"sub": TEST_USER_ID},
        )
    with index.session() as client:
        collection = client.collections.get("DocumentChunk").with_tenant(TEST_TENANT_NAME)
        stored = collection.query.fetch_objects(
            filters=Filter.by_property("documentId").equal(str(paper))
            & Filter.by_property("chunkIndex").greater_or_equal(added.chunk_index_start),
            limit=100,
        )
    piece_sections = {obj.properties["parentSection"] for obj in stored.objects}
    block_sections = {block.parent_section for block in text.blocks if block.parent_section}
    assert piece_sections and piece_sections <= block_sections and "Sheet1" in piece_sections
    readable = resolve_readable_files(str(paper), TEST_USER_ID)
    sheet = await chunk_reads.get_chunks_by_parent_section(str(paper), "Sheet1", TEST_USER_ID, readable=readable)
    assert sheet and all(chunk["chunk_index"] >= added.chunk_index_start for chunk in sheet)


# --- the paper stays usable in every intermediate state ---------------------------------------


@pytest.mark.asyncio
async def test_during_an_add_the_paper_is_readable_viewable_and_only_its_ready_files_are_read(owner_id, index):
    paper = await asyncio.to_thread(_paper, owner_id, index, main_end=2)
    job = supplement_add.start_supplement_add(paper, owner_id, [_chosen(20)])
    seen = {}

    async def checkpoint(stage):
        if stage == "finishing":
            # Rows pending, pieces stored in their range, nothing marked ready yet.
            (_, pending) = _rows(paper)
            seen["pending"] = (pending.status, pending.chunk_index_start, pending.chunk_index_end)
            await _usable(paper, index, readable_ranges=[(0, 2)])
            seen["deletable"] = _deletable(paper, owner_id)

    await _add(paper, owner_id, index, [_chosen(20)], job_id=UUID(job.job_id), checkpoint=checkpoint)

    assert seen["pending"][0] == "pending" and seen["pending"][1] == 3
    # A live add holds the paper's one job: delete waits for it (409), as for an import.
    assert seen["deletable"] is False


@pytest.mark.asyncio
async def test_after_a_failed_write_the_paper_is_as_before_and_deletable(owner_id, index, monkeypatch):
    paper = await asyncio.to_thread(_paper, owner_id, index, main_end=2)
    job = supplement_add.start_supplement_add(paper, owner_id, [_chosen(20), _chosen(40)])

    async def fail_at_finish(stage):
        if stage == "finishing":
            raise RuntimeError("made-up failure while writing")

    with pytest.raises(RuntimeError):
        await _add(paper, owner_id, index, [_chosen(20), _chosen(40)], job_id=UUID(job.job_id),
                   checkpoint=fail_at_finish)
    pdf_job_service.mark_failed(job_id=job.job_id, message="made-up", stage="failed")

    assert [row.role for row in _rows(paper)] == ["main"]
    assert _indexes(index, str(paper)) == [0, 1, 2]
    await _usable(paper, index, readable_ranges=[(0, 2)])
    assert _deletable(paper, owner_id) is True


@pytest.mark.asyncio
async def test_a_dead_add_before_and_after_the_stale_sweep_and_the_next_add(owner_id, index):
    paper = await asyncio.to_thread(_paper, owner_id, index, main_end=2)
    job = supplement_add.start_supplement_add(paper, owner_id, [_chosen(20)])

    class Died(BaseException):
        """The backend process dying: nothing after it runs, not even the add's own clean-up."""

    async def die(stage):
        if stage == "finishing":
            raise Died()

    original_undo = supplement_add._undo
    supplement_add._undo = AsyncMock()  # a dead process cleans nothing up
    try:
        with pytest.raises(Died):
            await _add(paper, owner_id, index, [_chosen(20)], job_id=UUID(job.job_id), checkpoint=die)
    finally:
        supplement_add._undo = original_undo
    with SessionLocal.begin() as db:
        stored_job = db.get(PdfProcessingJob, UUID(job.job_id))
        stored_job.status = PdfJobStatus.RUNNING.value
        stored_job.updated_at = datetime.now(timezone.utc)
    dead_row = _rows(paper)[1]
    assert (dead_row.status, dead_row.chunk_index_start) == ("pending", 3)

    # Before the sweep: readable (its pieces are never read), viewable, not yet deletable.
    await _usable(paper, index, readable_ranges=[(0, 2)])
    assert _deletable(paper, owner_id) is False

    # The sweep (run by the delete check itself once the job is stale): the row fails, the paper does not.
    with SessionLocal.begin() as db:
        db.get(PdfProcessingJob, UUID(job.job_id)).updated_at = datetime.now(timezone.utc) - timedelta(hours=3)
    assert _deletable(paper, owner_id) is True
    assert _rows(paper)[1].status == "failed"
    await _usable(paper, index, readable_ranges=[(0, 2)])

    # The next add clears the dead rows' pieces from the real index, and piece numbers move on.
    dead_end = dead_row.chunk_index_end
    next_job = supplement_add.start_supplement_add(paper, owner_id, [_chosen(20)])
    await _add(paper, owner_id, index, [_chosen(20)], job_id=UUID(next_job.job_id))
    main, added = _rows(paper)
    assert added.id != dead_row.id and added.chunk_index_start == dead_end + 1
    assert _indexes(index, str(paper)) == [0, 1, 2, *range(added.chunk_index_start, added.chunk_index_end + 1)]
    await _usable(paper, index, readable_ranges=[(0, 2), (added.chunk_index_start, added.chunk_index_end)])


# --- adds that overlap in time ----------------------------------------------------------------


def test_two_add_requests_at_the_same_moment_give_one_job_and_no_rows(owner_id, index):
    paper = _paper(owner_id, index, main_end=2)
    barrier = threading.Barrier(2)
    outcomes: list[object] = []

    def request(original_id: int) -> None:
        barrier.wait()
        try:
            outcomes.append(supplement_add.start_supplement_add(paper, owner_id, [_chosen(original_id)]))
        except ActiveJobExistsError as refused:
            outcomes.append(refused)

    threads = [threading.Thread(target=request, args=(item,)) for item in (20, 40)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(type(item).__name__ for item in outcomes) == ["ActiveJobExistsError", "PdfJobResponse"]
    assert [row.role for row in _rows(paper)] == ["main"]
    with SessionLocal() as db:
        jobs = db.execute(select(PdfProcessingJob).where(PdfProcessingJob.document_id == paper)).scalars().all()
    assert len(jobs) == 1


@pytest.mark.asyncio
async def test_a_delete_during_an_add_is_refused_by_the_delete_endpoint(owner_id, index, monkeypatch):
    paper = await asyncio.to_thread(_paper, owner_id, index, main_end=2)
    supplement_add.start_supplement_add(paper, owner_id, [_chosen(20)])

    async def provision(_principal):
        return type("User", (), {"id": owner_id})()

    monkeypatch.setattr(documents_api, "provision_user_async", provision)
    with pytest.raises(HTTPException) as raised:
        await documents_api.delete_document_endpoint(document_id=str(paper), user={"sub": TEST_USER_ID})
    assert raised.value.status_code == 409
    assert _indexes(index, str(paper)) == [0, 1, 2]


@pytest.mark.asyncio
async def test_an_add_after_the_paper_is_deleted_writes_nothing(owner_id, index):
    paper = await asyncio.to_thread(_paper, owner_id, index, main_end=2)
    with SessionLocal.begin() as db:
        db.delete(db.get(PDFDocument, paper))
    with pytest.raises(HTTPException) as raised:
        supplement_add.start_supplement_add(paper, owner_id, [_chosen(20)])
    assert raised.value.status_code == 404


@pytest.mark.asyncio
async def test_a_paper_deleted_while_its_add_writes_keeps_no_piece_in_the_index(owner_id, index):
    paper = await asyncio.to_thread(_paper, owner_id, index, main_end=2)
    job = supplement_add.start_supplement_add(paper, owner_id, [_chosen(20)])

    async def delete_paper_mid_write(stage):
        # The worst order for an add: the delete wipes the index before the add stores,
        # and removes the paper's rows after the add stored but before it finished.
        if stage == "vector_storage":
            chunk_reads.delete_pieces_of_deleted_paper(str(paper), TEST_USER_ID)
        if stage == "finishing":
            assert _indexes(index, str(paper))  # the add's pieces are in the index now
            with SessionLocal.begin() as db:
                db.delete(db.get(PDFDocument, paper))

    with pytest.raises(Exception):
        await _add(paper, owner_id, index, [_chosen(20)], job_id=UUID(job.job_id), checkpoint=delete_paper_mid_write)
    assert _indexes(index, str(paper)) == []
