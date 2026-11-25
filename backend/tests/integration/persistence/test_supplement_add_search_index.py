"""Adding supplements against the real test Weaviate (P7c1): range deletes, overlap, and a usable paper.

The paper's pieces live in the real test Weaviate, so every range filter is the
search index's own. The paper's file listing is built from core
``SourceArtifact`` records (the persistence image has no Alliance package) and the
provider only serves downloads. All values are made up.
"""

from __future__ import annotations

import asyncio
import hashlib
import threading
from dataclasses import replace
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
from src.lib.document_files import service as file_service
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
WORKBOOKS = {"21": WORKBOOK, "41": WORKBOOK_2}
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
    """A finished paper whose ready main PDF holds pieces 0..main_end in the real index.

    Its own search-index object is real too (completed), so what an add writes to it is seen.
    """

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
    with connection.session() as client:
        client.collections.get("PDFDocument").with_tenant(TEST_TENANT_NAME).data.insert(
            uuid=document_id,
            properties={
                "filename": f"{PREFIX}{document_id}.pdf", "processingStatus": "completed",
                "embeddingStatus": "completed", "chunkCount": main_end + 1, "vectorCount": main_end + 1,
            },
        )
    return document_id


def _paper_object(connection, document_id: UUID) -> dict:
    with connection.session() as client:
        found = client.collections.get("PDFDocument").with_tenant(TEST_TENANT_NAME).query.fetch_object_by_id(
            document_id,
        )
    return dict(found.properties)


class _Provider:
    provider_id = "made_up_provider"

    def __init__(self, payloads):
        self.payloads = payloads

    async def download_artifact(self, artifact_id, *, request_bearer_token, max_bytes):
        return self.payloads[artifact_id]


def _artifact(artifact_id, role, artifact_format, *, md5sum, **metadata) -> SourceArtifact:
    return SourceArtifact(
        provider="made_up_provider", artifact_id=artifact_id, role=role, artifact_format=artifact_format,
        status=SourceArtifactStatus.AVAILABLE, display_name=f"Made_up_S{artifact_id}", md5sum=md5sum,
        access_policy=SourceAccessPolicy(scope=SourceAccessScope.RESTRICTED, group_ids=("group-a",)),
        metadata=metadata,
    )


def _entry(original_id: int, in_paper: frozenset[str]) -> ListedFile:
    original = _artifact(
        str(original_id), SourceArtifactRole.SOURCE_SUPPLEMENT, SourceArtifactFormat.UNKNOWN,
        md5sum=f"md5-{original_id}", file_class="supplement", file_extension="xlsx",
    )
    converted = _artifact(
        str(original_id + 1), SourceArtifactRole.CONVERTED_TEXT, SourceArtifactFormat.MARKDOWN,
        md5sum=hashlib.md5(WORKBOOKS[str(original_id + 1)]).hexdigest(),
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
    return ChosenFile(str(original_id), md5=f"md5-{original_id}",
                      converted_md5=hashlib.md5(WORKBOOKS[str(original_id + 1)]).hexdigest())


async def _add(document_id, owner_id, connection, files, *, job_id, checkpoint=None, payloads=None, commit_state=None):
    return await supplement_add.add_files_to_paper(
        document_id, owner_id, CONTEXT, files, job_id=job_id,
        provider=_Provider(payloads or WORKBOOKS),
        weaviate_client=connection, stage_checkpoint=checkpoint or AsyncMock(), commit_state=commit_state,
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


@pytest.fixture
def pdf_supplement(monkeypatch):
    pdf = (Path(__file__).resolve().parents[2] / "fixtures" / "live_tiny_chat.pdf").read_bytes()
    md5 = hashlib.md5(pdf).hexdigest()

    async def list_pdf(*args, **kwargs):
        listing = await _listing(*args, **kwargs)
        entry = listing.files[0]
        original = replace(entry.source_artifact, artifact_format=SourceArtifactFormat.PDF, md5sum=md5,
                           metadata={"file_class": "supplement", "file_extension": "pdf"})
        return replace(listing, files=(replace(entry, md5=md5, source_artifact=original, artifact_format="pdf"),))

    monkeypatch.setattr(file_listing_service, "list_files_for_paper", list_pdf)
    return replace(_chosen(20), md5=md5), {"20": pdf, "21": WORKBOOK}


@pytest.mark.asyncio
@pytest.mark.parametrize("during", ["insert", "markdown", "json", "pdf", "reserve", "ready"])
async def test_cancellation_drains_the_write_before_rollback(
    owner_id, index, pdf_supplement, monkeypatch, tmp_path, during,
):
    paper = await asyncio.to_thread(_paper, owner_id, index)
    started = asyncio.Event()
    release = threading.Event()
    finished = threading.Event()
    loop = asyncio.get_running_loop()
    owner, name = {
        "insert": (supplement_add, "_insert_rows"),
        "markdown": (Path, "write_text"),
        "json": (Path, "write_text"),
        "pdf": (supplement_add, "_save_supplement_pdf"),
        "reserve": (file_service, "reserve_file_chunk_range"),
        "ready": (file_service, "mark_files_ready"),
    }[during]
    write = getattr(owner, name)

    def paused_write(*args, **kwargs):
        if owner is Path and args[0].suffix != {"markdown": ".md", "json": ".json"}[during]:
            return write(*args, **kwargs)
        loop.call_soon_threadsafe(started.set)
        assert release.wait(10), "test did not release the writer"
        result = write(*args, **kwargs)
        finished.set()
        return result

    monkeypatch.setattr(owner, name, paused_write)
    undo = AsyncMock(wraps=supplement_add._undo)
    monkeypatch.setattr(supplement_add, "_undo", undo)
    commit = supplement_add.SupplementAddCommit()
    chosen, payloads = pdf_supplement
    add = asyncio.create_task(_add(
        paper, owner_id, index, [chosen], job_id=uuid4(), payloads=payloads, commit_state=commit,
    ))
    try:
        await asyncio.wait_for(started.wait(), timeout=10)
        add.cancel("deadline")
        await asyncio.sleep(0)  # Deliver cancellation while the worker is still blocked.
        add.cancel("second cancellation")
        await asyncio.sleep(0)
        premature_undo = undo.await_count
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError, match="deadline"):
        await add
    assert premature_undo == 0 and finished.is_set()
    rows = _rows(paper)
    assert commit.committed is (during == "ready")
    paths = {path for path in tmp_path.rglob("*") if path.is_file()}
    indexes = _indexes(index, str(paper))
    paper_object = _paper_object(index, paper)
    assert paper_object["chunkCount"] == paper_object["vectorCount"] == len(indexes)
    if during == "ready":
        undo.assert_not_awaited()
        main, added = rows
        assert main.role == "main" and added.status == "ready"
        assert paths == {
            tmp_path / added.source_markdown_path, tmp_path / added.processed_json_path, tmp_path / added.file_path,
        }
        assert (tmp_path / added.source_markdown_path).read_bytes() == WORKBOOK
        assert (tmp_path / added.file_path).read_bytes() == payloads["20"]
        assert added.chunk_index_start == 3
        assert indexes == list(range(added.chunk_index_end + 1))
        await _usable(paper, index, readable_ranges=[(0, 2), (3, added.chunk_index_end)])
    else:
        undo.assert_awaited_once()
        assert [row.role for row in rows] == ["main"]
        assert paths == set()
        assert indexes == [0, 1, 2]


@pytest.mark.asyncio
async def test_rollback_skips_ready_rows_and_their_artifacts_and_pieces(owner_id, index, tmp_path):
    paper = await asyncio.to_thread(_paper, owner_id, index)
    await _add(paper, owner_id, index, [_chosen(20), _chosen(40)], job_id=uuid4())
    main, ready, failed = _rows(paper)
    file_service.mark_file_failed(failed.id, error_message="test failure")
    paths = [path for row in (ready, failed) for path in (row.source_markdown_path, row.processed_json_path)]

    await supplement_add._undo(paper, TEST_USER_ID, [main.id, ready.id, failed.id], paths)

    assert [(row.id, row.status) for row in _rows(paper)] == [(main.id, "ready"), (ready.id, "ready")]
    assert {path for path in paths if (tmp_path / path).is_file()} == {
        ready.source_markdown_path, ready.processed_json_path,
    }
    assert _indexes(index, str(paper)) == list(range(ready.chunk_index_end + 1))


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


@pytest.mark.asyncio
async def test_paper_deleted_after_first_rollback_read_leaves_no_files_or_pieces(owner_id, index, tmp_path):
    from sqlalchemy import event

    from src.models.sql.database import engine

    paper = await asyncio.to_thread(_paper, owner_id, index)

    def delete_paper(*_args):
        # Commit on another connection after Postgres has taken the rollback read's snapshot.
        with SessionLocal.begin() as db:
            db.execute(delete(PDFDocument).where(PDFDocument.id == paper))

    async def checkpoint(stage):
        if stage == "vector_storage":
            chunk_reads.delete_pieces_of_deleted_paper(str(paper), TEST_USER_ID)
        if stage == "finishing":
            assert _indexes(index, str(paper))
            assert any(path.is_file() for path in tmp_path.rglob("*"))
            event.listen(engine, "after_cursor_execute", delete_paper, once=True)
            raise RuntimeError("cancel add before ready")

    try:
        with pytest.raises(RuntimeError, match="cancel add before ready"):
            await _add(paper, owner_id, index, [_chosen(20)], job_id=uuid4(), checkpoint=checkpoint)
    finally:
        if event.contains(engine, "after_cursor_execute", delete_paper):
            event.remove(engine, "after_cursor_execute", delete_paper)

    with SessionLocal() as db:
        assert db.get(PDFDocument, paper) is None
    assert _rows(paper) == []
    assert _indexes(index, str(paper)) == []
    assert not any(path.is_file() for path in tmp_path.rglob("*"))


@pytest.mark.asyncio
async def test_the_real_stale_sweep_fails_a_dead_adds_rows_on_a_completed_paper(owner_id, index):
    """A deploy-style stop: the process is gone mid-add and only the background sweep runs.

    Afterwards the paper has no pending file rows, the structural fact the model's
    "files are still being added" line keys on, and a new run reads its ready files.
    """

    from src.lib.pdf_jobs.recovery import reconcile_stale_jobs_once

    paper = await asyncio.to_thread(_paper, owner_id, index, main_end=2)
    job = supplement_add.start_supplement_add(paper, owner_id, [_chosen(20)])

    class ProcessGone(BaseException):
        pass

    async def stop(stage):
        if stage == "finishing":
            raise ProcessGone()

    original_undo = supplement_add._undo
    supplement_add._undo = AsyncMock()  # nothing runs after the process is gone
    try:
        with pytest.raises(ProcessGone):
            await _add(paper, owner_id, index, [_chosen(20)], job_id=UUID(job.job_id), checkpoint=stop)
    finally:
        supplement_add._undo = original_undo
    with SessionLocal.begin() as db:
        stored_job = db.get(PdfProcessingJob, UUID(job.job_id))
        stored_job.status = PdfJobStatus.RUNNING.value
    assert [row.status for row in _rows(paper)] == ["ready", "pending"]

    later = datetime.now(timezone.utc) + timedelta(hours=3)
    cursor = reconcile_stale_jobs_once(now=later)
    while cursor is not None:
        cursor = reconcile_stale_jobs_once(after=cursor, now=later)

    with SessionLocal() as db:
        assert db.get(PdfProcessingJob, UUID(job.job_id)).status == PdfJobStatus.FAILED.value
        pending = db.scalar(
            select(PDFDocumentFile.id).where(
                PDFDocumentFile.document_id == paper, PDFDocumentFile.status == "pending",
            )
        )
    assert pending is None
    assert [row.status for row in _rows(paper)] == ["ready", "failed"]
    await _usable(paper, index, readable_ranges=[(0, 2)])
    assert _deletable(paper, owner_id) is True


@pytest.mark.asyncio
async def test_after_the_stale_sweep_a_run_is_no_longer_told_files_are_being_added(owner_id, index):
    """The model's "Files are still being added" line keys on pending file rows. A dead
    add leaves one; once the real background sweep fails it, a new run's outline no
    longer carries the line (the count is taken per run, never cached)."""

    from src.lib.document_context import DocumentContext
    from src.lib.openai_agents.prompt_utils import FILES_BEING_ADDED_LINE, format_hierarchy_for_prompt
    from src.lib.pdf_jobs.recovery import reconcile_stale_jobs_once

    paper = await asyncio.to_thread(_paper, owner_id, index, main_end=2)
    job = supplement_add.start_supplement_add(paper, owner_id, [_chosen(20)])

    class ProcessGone(BaseException):
        pass

    async def stop(stage):
        if stage == "finishing":
            raise ProcessGone()

    original_undo = supplement_add._undo
    supplement_add._undo = AsyncMock()  # nothing runs after the process is gone
    try:
        with pytest.raises(ProcessGone):
            await _add(paper, owner_id, index, [_chosen(20)], job_id=UUID(job.job_id), checkpoint=stop)
    finally:
        supplement_add._undo = original_undo
    with SessionLocal.begin() as db:
        db.get(PdfProcessingJob, UUID(job.job_id)).status = PdfJobStatus.RUNNING.value

    during = await DocumentContext.fetch(str(paper), TEST_USER_ID)
    assert during.hierarchy is not None and during.hierarchy["files_being_added"] == 1
    assert FILES_BEING_ADDED_LINE in format_hierarchy_for_prompt(during.hierarchy)

    later = datetime.now(timezone.utc) + timedelta(hours=3)
    cursor = reconcile_stale_jobs_once(now=later)
    while cursor is not None:
        cursor = reconcile_stale_jobs_once(after=cursor, now=later)

    after = await DocumentContext.fetch(str(paper), TEST_USER_ID)
    assert after.hierarchy is not None and after.hierarchy["files_being_added"] == 0
    assert FILES_BEING_ADDED_LINE not in format_hierarchy_for_prompt(after.hierarchy)


# --- a delete and an add cannot interleave (paper_lock.py) ------------------------------------


def _wire_delete(monkeypatch, owner_id, *, in_wipe=None, wipe_seconds: float = 0.0, wipe_error=None):
    """The real delete endpoint, with an index wipe that yields the loop like the real one.

    The real helper's wipe runs in an executor; this one awaits for ``wipe_seconds``
    (handing the loop to other work), then deletes the pieces in a worker thread.
    ``in_wipe`` is set when the delete is between its active-job check and its row delete.
    """

    async def provision(_principal):
        return type("User", (), {"id": owner_id})()

    async def present(_sub, _document_id):
        return {"document": {"processingStatus": "completed"}}

    async def wipe(user_sub, document_id):
        if in_wipe is not None:
            in_wipe.set()
        await asyncio.sleep(wipe_seconds)
        if wipe_error is not None:
            raise wipe_error
        deleted = await asyncio.to_thread(chunk_reads.delete_pieces_of_deleted_paper, document_id, user_sub)
        return {"success": True, "chunks_deleted": deleted}

    async def delete_parent(_user_sub, _document_id, *, allow_missing=False):
        return {"success": True, "deleted": True}

    monkeypatch.setattr(documents_api, "provision_user_async", provision)
    monkeypatch.setattr(documents_api, "get_document", present)
    monkeypatch.setattr(documents_api, "delete_paper_chunks", wipe)
    monkeypatch.setattr(documents_api, "delete_paper_object", delete_parent)


async def _delete(paper: UUID):
    return await documents_api.delete_document_endpoint(document_id=str(paper), user={"sub": TEST_USER_ID})


@pytest.mark.asyncio
async def test_a_delete_with_no_add_running_deletes_the_paper_as_before(owner_id, index, monkeypatch):
    paper = await asyncio.to_thread(_paper, owner_id, index, main_end=2)
    _wire_delete(monkeypatch, owner_id)

    result = await _delete(paper)

    assert result.success is True
    assert _rows(paper) == [] and _indexes(index, str(paper)) == []


@pytest.mark.asyncio
async def test_two_deletes_of_one_paper_on_one_loop_never_block_it(owner_id, index, monkeypatch):
    """A double-click on delete: one deletes, the other is refused at once, and the loop keeps serving."""

    paper = await asyncio.to_thread(_paper, owner_id, index, main_end=2)
    other = await asyncio.to_thread(_paper, owner_id, index, main_end=1)
    _wire_delete(monkeypatch, owner_id, wipe_seconds=1.0)
    ticks = 0

    async def unrelated_work():
        nonlocal ticks
        for _ in range(40):
            await asyncio.sleep(0.025)
            ticks += 1

    outcomes = await asyncio.wait_for(
        asyncio.gather(_delete(paper), _delete(paper), _delete(other), unrelated_work(), return_exceptions=True),
        timeout=30,
    )

    first, second, other_result, _ = outcomes
    results = [first, second]
    refused = [item for item in results if isinstance(item, HTTPException)]
    deleted = [item for item in results if not isinstance(item, BaseException)]
    assert len(deleted) == 1 and len(refused) == 1
    assert refused[0].status_code in (409, 404)
    assert other_result.success is True  # another paper's delete went ahead meanwhile
    assert ticks == 40  # an unrelated request on the same loop finished during the wipe
    assert _rows(paper) == [] and _indexes(index, str(paper)) == []


@pytest.mark.asyncio
async def test_an_add_requested_while_the_paper_is_being_deleted_is_refused_at_once(owner_id, index, monkeypatch):
    import time

    paper = await asyncio.to_thread(_paper, owner_id, index, main_end=2)
    in_wipe = asyncio.Event()
    _wire_delete(monkeypatch, owner_id, in_wipe=in_wipe, wipe_seconds=1.0)

    async def add_during_delete():
        await in_wipe.wait()
        started = time.monotonic()
        with pytest.raises(ActiveJobExistsError):
            await asyncio.to_thread(supplement_add.start_supplement_add, paper, owner_id, [_chosen(20)])
        return time.monotonic() - started

    elapsed, deleted = await asyncio.gather(add_during_delete(), _delete(paper))

    assert elapsed < 0.5 and deleted.success is True  # refused without waiting (409 import_in_progress)
    with SessionLocal() as db:
        assert db.scalar(select(PdfProcessingJob.id).where(PdfProcessingJob.document_id == paper)) is None
    assert _indexes(index, str(paper)) == []


@pytest.mark.asyncio
async def test_a_delete_requested_while_an_add_creates_its_job_is_refused_at_once(owner_id, index, monkeypatch):
    import time

    from src.lib.document_files.paper_lock import try_lock_paper_for_add
    from src.lib.pdf_jobs.service import create_job_in_session

    paper = await asyncio.to_thread(_paper, owner_id, index, main_end=2)
    _wire_delete(monkeypatch, owner_id)
    locked = threading.Event()
    release = threading.Event()

    def add_creating_its_job() -> None:
        with SessionLocal() as session:
            assert try_lock_paper_for_add(session, paper)
            locked.set()
            release.wait(timeout=30)  # the add's job-creating transaction is still open
            create_job_in_session(session, document_id=paper, user_id=owner_id)

    adder = threading.Thread(target=add_creating_its_job)
    adder.start()
    await asyncio.to_thread(locked.wait, 30)
    started = time.monotonic()
    with pytest.raises(HTTPException) as raised:
        await _delete(paper)
    elapsed = time.monotonic() - started
    release.set()
    adder.join(timeout=30)

    assert raised.value.status_code == 409 and elapsed < 0.5
    assert [row.role for row in _rows(paper)] == ["main"] and _indexes(index, str(paper)) == [0, 1, 2]


@pytest.mark.asyncio
async def test_the_lock_is_released_when_the_delete_fails(owner_id, index, monkeypatch):
    paper = await asyncio.to_thread(_paper, owner_id, index, main_end=2)
    _wire_delete(monkeypatch, owner_id, wipe_error=RuntimeError("made-up index failure"))
    with pytest.raises(HTTPException) as raised:
        await _delete(paper)
    assert raised.value.status_code == 500

    _wire_delete(monkeypatch, owner_id)
    assert (await _delete(paper)).success is True


@pytest.mark.asyncio
async def test_the_lock_is_released_when_the_delete_request_is_cancelled(owner_id, index, monkeypatch):
    paper = await asyncio.to_thread(_paper, owner_id, index, main_end=2)
    in_wipe = asyncio.Event()
    _wire_delete(monkeypatch, owner_id, in_wipe=in_wipe, wipe_seconds=5.0)
    request = asyncio.ensure_future(_delete(paper))
    await in_wipe.wait()
    request.cancel()
    with pytest.raises(asyncio.CancelledError):
        await request
    await asyncio.sleep(0.5)  # the release runs in its thread

    job = await asyncio.to_thread(supplement_add.start_supplement_add, paper, owner_id, [_chosen(20)])
    assert job.status == PdfJobStatus.PENDING.value


# --- a paper's status is never taken from an add -----------------------------------------------


async def _reported_status(paper: UUID, monkeypatch) -> tuple[str, str]:
    async def provision(_principal):
        with SessionLocal() as db:
            return type("User", (), {"id": db.get(PDFDocument, paper).user_id, "auth_sub": TEST_USER_ID})()

    monkeypatch.setattr(documents_api, "provision_user_async", provision)
    detail = await documents_api.get_document_endpoint(document_id=str(paper), user={"sub": TEST_USER_ID})
    status = await documents_api.get_document_processing_status(document_id=str(paper), user={"sub": TEST_USER_ID})
    detail_status = getattr(detail.status, "value", detail.status)
    endpoint_status = status["processing_status"]
    return str(detail_status).lower(), str(getattr(endpoint_status, "value", endpoint_status)).lower()


@pytest.mark.asyncio
async def test_the_papers_status_stays_completed_during_and_after_an_add_that_added_nothing(
    owner_id, index, monkeypatch,
):
    paper = await asyncio.to_thread(_paper, owner_id, index, main_end=2)
    job = await asyncio.to_thread(supplement_add.start_supplement_add, paper, owner_id, [_chosen(20)])
    assert job.filename == f"{PREFIX}{paper}.pdf"  # the paper's name, never its id
    assert job.metadata == {"job_kind": "supplement_add"}

    assert await _reported_status(paper, monkeypatch) == ("completed", "completed")  # while the add's job is active
    pdf_job_service.mark_failed(job_id=job.job_id, message="None of the chosen files could be added.", stage="failed")
    assert await _reported_status(paper, monkeypatch) == ("completed", "completed")


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["succeeds", "fails_while_storing", "cancelled", "killed"])
async def test_an_add_never_changes_the_papers_own_index_object(owner_id, index, monkeypatch, outcome):
    """Byte for byte: before, at every checkpoint of the add, and after it, whatever its end.

    Nothing is mocked on the index side: the real store, the real status writer.
    A successful add then writes only the object's two piece counts.
    """

    from src.lib.exceptions import PDFCancellationError

    paper = await asyncio.to_thread(_paper, owner_id, index, main_end=2)
    before = _paper_object(index, paper)
    job = await asyncio.to_thread(supplement_add.start_supplement_add, paper, owner_id, [_chosen(20), _chosen(40)])
    seen: list[tuple[str, dict]] = []

    class ProcessGone(BaseException):
        pass

    async def checkpoint(stage):
        seen.append((stage, _paper_object(index, paper)))
        if outcome == "cancelled" and stage == "finishing":
            raise PDFCancellationError("made-up cancel")
        if outcome == "killed" and stage == "finishing":
            raise ProcessGone()

    if outcome == "fails_while_storing":
        calls = {"n": 0}
        real_store = store_chunks_to_weaviate

        async def fail_second_file(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("made-up index failure")
            return await real_store(*args, **kwargs)

        monkeypatch.setattr("src.lib.pipeline.store.store_chunks_to_weaviate", fail_second_file)
    if outcome == "killed":
        monkeypatch.setattr(supplement_add, "_undo", AsyncMock())

    run = _add(paper, owner_id, index, [_chosen(20), _chosen(40)], job_id=UUID(job.job_id), checkpoint=checkpoint)
    if outcome == "succeeds":
        await run
    else:
        with pytest.raises(BaseException):
            await run

    stages = [stage for stage, _ in seen]
    assert "vector_storage" in stages and stages.count("vector_storage") == 2  # one checkpoint between the files
    assert all(state == before for _, state in seen), [stage for stage, state in seen if state != before]
    after = _paper_object(index, paper)
    if outcome == "succeeds":
        (_, first, second) = _rows(paper)
        assert {key: value for key, value in after.items() if key not in ("chunkCount", "vectorCount")} == {
            key: value for key, value in before.items() if key not in ("chunkCount", "vectorCount")
        }
        assert after["chunkCount"] == after["vectorCount"] == second.chunk_index_end + 1
    else:
        assert after == before


# --- a process that dies mid-add leaves nothing private behind ---------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("dies_at", ["before_any_file", "after_the_markdown"])
async def test_files_of_a_dead_add_are_found_and_removed_by_the_next_add(owner_id, index, monkeypatch, dies_at):
    from src.config import get_pdf_storage_path
    from src.lib.document_sources import ingestion
    from src.lib.pdf_jobs.recovery import reconcile_stale_jobs_once

    paper = await asyncio.to_thread(_paper, owner_id, index, main_end=2)
    job = await asyncio.to_thread(supplement_add.start_supplement_add, paper, owner_id, [_chosen(20)])

    class ProcessGone(BaseException):
        pass

    original_markdown, original_json = ingestion._save_source_markdown, ingestion._save_processed_json
    original_undo = supplement_add._undo

    async def die_on_markdown(**kwargs):
        raise ProcessGone()

    async def die_on_json(**kwargs):
        raise ProcessGone()

    if dies_at == "before_any_file":
        monkeypatch.setattr(ingestion, "_save_source_markdown", die_on_markdown)
    else:
        monkeypatch.setattr(ingestion, "_save_processed_json", die_on_json)
    monkeypatch.setattr(supplement_add, "_undo", AsyncMock())  # nothing runs after the process is gone
    with pytest.raises(ProcessGone):
        await _add(paper, owner_id, index, [_chosen(20)], job_id=UUID(job.job_id))
    monkeypatch.setattr(ingestion, "_save_source_markdown", original_markdown)
    monkeypatch.setattr(ingestion, "_save_processed_json", original_json)
    dead = _rows(paper)[1]
    assert dead.status == "pending" and dead.source_markdown_path and dead.processed_json_path
    written = get_pdf_storage_path() / dead.source_markdown_path
    assert written.is_file() is (dies_at == "after_the_markdown")

    with SessionLocal.begin() as db:
        db.get(PdfProcessingJob, UUID(job.job_id)).status = PdfJobStatus.RUNNING.value
    later = datetime.now(timezone.utc) + timedelta(hours=3)
    cursor = reconcile_stale_jobs_once(now=later)
    while cursor is not None:
        cursor = reconcile_stale_jobs_once(after=cursor, now=later)
    assert _rows(paper)[1].status == "failed"

    monkeypatch.setattr(supplement_add, "_undo", original_undo)
    next_job = await asyncio.to_thread(supplement_add.start_supplement_add, paper, owner_id, [_chosen(20)])
    await _add(paper, owner_id, index, [_chosen(20)], job_id=UUID(next_job.job_id))
    assert dead.id not in {row.id for row in _rows(paper)}
    assert not written.exists()


# --- awkward section names: repeated headings, text before any heading, symbol-only sheets ----


AWKWARD = (
    b"Made-up note before any heading.\n\n"
    b"# Made_up_S20\n\n"
    b"## a\n\n### Notes\n\n| strain | score |\n| --- | --- |\n| s1 | 1 |\n\n"
    b"## (\n\n### Notes\n\n| gene | count |\n| --- | --- |\n| g1 | 3 |\n"
)


@pytest.mark.asyncio
async def test_pieces_saved_json_and_viewer_blocks_carry_the_same_sections_for_awkward_names(owner_id, index, monkeypatch):
    import json

    from src.config import get_pdf_storage_path

    monkeypatch.setitem(WORKBOOKS, "21", AWKWARD)
    paper = await asyncio.to_thread(_paper, owner_id, index, main_end=2)
    job = supplement_add.start_supplement_add(paper, owner_id, [_chosen(20)])
    await _add(paper, owner_id, index, [_chosen(20)], job_id=UUID(job.job_id))
    (_, added) = _rows(paper)

    fields = ("section_title", "parent_section", "subsection", "section_path")
    saved = json.loads((get_pdf_storage_path() / added.processed_json_path).read_text(encoding="utf-8"))
    saved_sections = [tuple(
        tuple(element["metadata"][name]) if name == "section_path" else element["metadata"][name] for name in fields
    ) for element in saved]
    by_text = {element["text"]: element["metadata"] for element in saved}
    assert by_text["Made-up note before any heading."]["parent_section"] == "Made_up_S20.xlsx"
    tables = [element["metadata"] for element in saved if element["type"] == "Table"]
    assert [table["section_title"] for table in tables] == ["a > Notes", "( > Notes"]

    with SessionLocal() as db:
        text = pdf_viewer.get_document_file_text(
            document_id=paper, file_id=added.id, db=db, user={"sub": TEST_USER_ID},
        )
    block_sections = [
        (block.section_title, block.parent_section, block.subsection, tuple(block.section_path))
        for block in text.blocks
    ]
    assert set(block_sections) == set(saved_sections)

    with index.session() as client:
        collection = client.collections.get("DocumentChunk").with_tenant(TEST_TENANT_NAME)
        stored = collection.query.fetch_objects(
            filters=Filter.by_property("documentId").equal(str(paper))
            & Filter.by_property("chunkIndex").greater_or_equal(added.chunk_index_start),
            limit=100,
        )
    piece_sections = {
        (obj.properties["sectionTitle"], obj.properties["parentSection"], obj.properties["subsection"],
         tuple(obj.properties["sectionPath"]))
        for obj in stored.objects
    }
    assert piece_sections <= set(saved_sections)
    assert {("a > Notes", "a", "Notes", ("a", "Notes")), ("( > Notes", "(", "Notes", ("(", "Notes"))} <= piece_sections

    readable = resolve_readable_files(str(paper), TEST_USER_ID)
    for sheet in ("a", "("):
        notes = await chunk_reads.get_chunks_by_subsection(str(paper), sheet, "Notes", TEST_USER_ID, readable=readable)
        assert len(notes) == 1, sheet


# --- the delete with a small pool, and a lock lost after the delete finished --------------------


@pytest.mark.asyncio
async def test_with_a_pool_of_two_deletes_never_block_the_loop_and_a_pool_timeout_is_a_503(
    owner_id, index, monkeypatch,
):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from src.models.sql import database

    small = create_engine(database.engine.url, pool_size=2, max_overflow=0, pool_timeout=1)
    monkeypatch.setattr(database, "engine", small)
    monkeypatch.setattr(documents_api, "SessionLocal", sessionmaker(bind=small))
    papers = [await asyncio.to_thread(_paper, owner_id, index, main_end=1) for _ in range(3)]
    _wire_delete(monkeypatch, owner_id, wipe_seconds=2.0)
    ticks = 0

    async def unrelated_work():
        nonlocal ticks
        for _ in range(60):
            await asyncio.sleep(0.05)
            ticks += 1

    try:
        outcomes = await asyncio.wait_for(
            asyncio.gather(*(_delete(paper) for paper in papers), unrelated_work(), return_exceptions=True),
            timeout=60,
        )
    finally:
        small.dispose()

    results = outcomes[:3]
    busy = [item for item in results if isinstance(item, HTTPException)]
    assert all(isinstance(item, HTTPException) or item.success for item in results), results
    assert busy and all(item.status_code == 503 for item in busy)  # the pool ran out, answered clearly
    assert ticks == 60  # the loop kept serving while deletes waited for connections


@pytest.mark.asyncio
async def test_a_delete_that_finished_says_so_even_if_its_lock_connection_was_lost(owner_id, index, monkeypatch):
    """The lock's database session is killed after the whole delete has finished, just before the release."""

    from sqlalchemy import text as sql_text

    from src.lib.document_files import paper_lock
    from src.models.sql.database import engine

    paper = await asyncio.to_thread(_paper, owner_id, index, main_end=2)
    _wire_delete(monkeypatch, owner_id)
    reports = []
    monkeypatch.setattr(
        "src.lib.observability.runtime.report_runtime_exception", lambda exc, **kwargs: reports.append(kwargs),
    )
    real_release = paper_lock._release
    state = {}

    def release_after_losing_the_connection(connection, key):
        # Everything the delete does is done by now: the paper's rows are gone.
        state["rows_left_at_release"] = len(_rows(paper))
        pid = connection.execute(sql_text("SELECT pg_backend_pid()")).scalar_one()
        with engine.connect() as other:
            other.execute(sql_text("SELECT pg_terminate_backend(:pid)"), {"pid": pid})
            other.commit()
        real_release(connection, key)

    monkeypatch.setattr(paper_lock, "_release", release_after_losing_the_connection)

    result = await _delete(paper)

    assert state["rows_left_at_release"] == 0
    assert result.success is True and _rows(paper) == [] and _indexes(index, str(paper)) == []
    assert [report["operation"] for report in reports] == ["paper_lock_release_failed"]
    # The lock died with its session: the paper id is free again.
    with engine.connect() as connection:
        assert connection.execute(
            sql_text("SELECT pg_try_advisory_lock(:key)"), {"key": paper_lock.paper_lock_key(paper)},
        ).scalar_one() is True
        connection.execute(sql_text("SELECT pg_advisory_unlock(:key)"), {"key": paper_lock.paper_lock_key(paper)})
        connection.commit()


@pytest.mark.asyncio
async def test_a_pool_timeout_while_reading_the_papers_index_object_is_the_delete_503(owner_id, index, monkeypatch):
    from sqlalchemy.exc import TimeoutError as PoolTimeoutError

    paper = await asyncio.to_thread(_paper, owner_id, index, main_end=2)
    _wire_delete(monkeypatch, owner_id)

    async def no_connection(_sub, _document_id):
        raise PoolTimeoutError("made-up: QueuePool limit reached")

    monkeypatch.setattr(documents_api, "get_document", no_connection)
    with pytest.raises(HTTPException) as raised:
        await _delete(paper)
    assert raised.value.status_code == 503
    assert raised.value.detail == "The server is busy right now; try deleting the paper again in a moment."
    assert [row.role for row in _rows(paper)] == ["main"]

    _wire_delete(monkeypatch, owner_id)  # the lock was released: the next delete goes ahead
    assert (await _delete(paper)).success is True
