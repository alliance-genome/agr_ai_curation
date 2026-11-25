# ruff: noqa: F811 - the shared fixtures (index, owner_id) are imported from the add tests and used by name
"""Removing one supplement from a paper, on real PostgreSQL and the real test Weaviate (P10b-api).

Papers get their supplements through the real add (``add_files_to_paper``), so
every removed file has the shape an add writes. Nothing on the index side is
mocked. All values are made up.
"""

from __future__ import annotations

import asyncio
import threading
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from src.api import documents as documents_api
from src import config as app_config
from src.lib.curation_workspace.models import (
    CurationCandidate,
    CurationEvidenceRecord,
    CurationReviewSession,
    DomainEnvelopeModel,
)
from src.lib.document_cleanup import cleanup_document_curation_dependencies
from src.lib.document_files.readable import resolve_readable_files
from src.lib.document_sources import supplement_add
from src.lib.weaviate_client import chunks as chunk_reads
from src.models.sql.database import SessionLocal
from src.models.sql.pdf_document import PDFDocument, PDFDocumentFile
from src.schemas.curation_workspace import (
    CurationCandidateSource,
    CurationCandidateStatus,
    CurationEvidenceSource,
    CurationSessionStatus,
)

from .conftest import TEST_USER_ID
from .test_supplement_add_search_index import (  # noqa: F401 - fixtures used by name
    PREFIX,
    _add,
    _chosen,
    _indexes,
    _paper,
    _paper_object,
    _rows,
    _wire_delete,
    index,
    migrated_database,
    owner_id,
)

NOW = datetime(2026, 11, 23, tzinfo=timezone.utc)


async def _paper_with_supplements(owner_id: int, connection, *files: int) -> UUID:
    paper = await asyncio.to_thread(_paper, owner_id, connection, main_end=2)
    job = await asyncio.to_thread(supplement_add.start_supplement_add, paper, owner_id, [_chosen(f) for f in files])
    await _add(paper, owner_id, connection, [_chosen(f) for f in files], job_id=UUID(job.job_id))
    from src.lib.pdf_jobs import service as pdf_job_service

    pdf_job_service.mark_completed(job_id=job.job_id, message="made-up")
    return paper


def _wire(monkeypatch, owner_id):
    async def provision(_principal):
        return type("User", (), {"id": owner_id})()

    monkeypatch.setattr(documents_api, "provision_user_async", provision)


async def _remove(paper: UUID, file_id: UUID):
    return await documents_api.remove_paper_file_endpoint(
        document_id=str(paper), file_id=str(file_id), user={"sub": TEST_USER_ID},
    )


@pytest.fixture
def cleanup_sessions():
    yield
    with SessionLocal.begin() as db:
        for stored in db.scalars(select(PDFDocument).where(PDFDocument.filename.like(f"{PREFIX}%"))):
            cleanup_document_curation_dependencies(db, stored.id)


def _session_quoting(paper: UUID, *, anchor_file: UUID | None = None, envelope_file: UUID | None = None) -> UUID:
    with SessionLocal.begin() as db:
        session_row = CurationReviewSession(
            id=uuid4(), status=CurationSessionStatus.IN_PROGRESS, adapter_key="fixture_adapter",
            document_id=paper, session_version=1, tags=[], total_candidates=1, reviewed_candidates=0,
            pending_candidates=1, accepted_candidates=0, rejected_candidates=0, manual_candidates=0,
            warnings=[], prepared_at=NOW, created_at=NOW, updated_at=NOW,
            created_by_id=TEST_USER_ID,  # a real session belongs to the curator who prepared it
        )
        db.add(session_row)
        db.flush()
        if anchor_file is not None:
            candidate = CurationCandidate(
                id=uuid4(), session_id=session_row.id, source=CurationCandidateSource.MANUAL,
                status=CurationCandidateStatus.PENDING, order=0, adapter_key="fixture_adapter",
                candidate_metadata={}, normalized_payload={}, created_at=NOW, updated_at=NOW,
            )
            db.add(candidate)
            db.flush()
            db.add(CurationEvidenceRecord(
                candidate_id=candidate.id, source=CurationEvidenceSource.MANUAL,
                anchor={"snippet_text": "Made-up quote."}, source_file_id=anchor_file,
            ))
        if envelope_file is not None:
            from src.schemas.domain_envelope import DomainEnvelopeStatus

            db.add(DomainEnvelopeModel(
                envelope_id=f"env-{uuid4()}", project_key="example", domain_pack_key="example",
                adapter_key="example", source_payload_hash="a" * 64, status=DomainEnvelopeStatus.EXTRACTED,
                document_id=paper, session_id=session_row.id,
                envelope_json={"objects": [{"evidence": [{"file_id": str(envelope_file), "quote": "Made-up."}]}]},
            ))
        return session_row.id


@pytest.mark.asyncio
async def test_a_supplement_is_removed_with_its_pieces_files_and_counts_and_nothing_else(owner_id, index, monkeypatch):
    paper = await _paper_with_supplements(owner_id, index, 20, 40)
    other_paper = await _paper_with_supplements(owner_id, index, 20, 40)
    (_, _, other_second) = _rows(other_paper)
    main, first, second = _rows(paper)
    first_paths = [app_config.get_pdf_storage_path() / path for path in (first.source_markdown_path, first.processed_json_path)]
    assert all(path.is_file() for path in first_paths)
    _wire(monkeypatch, owner_id)

    answer = await _remove(paper, first.id)

    assert answer == {
        "document_id": str(paper), "file_id": str(first.id), "removed": True, "cleanup_finished": True,
        "ready_file_count": 2,
    }
    remaining = _rows(paper)
    assert [row.id for row in remaining] == [main.id, second.id]
    expected = [*range(0, 3), *range(second.chunk_index_start, second.chunk_index_end + 1)]
    assert _indexes(index, str(paper)) == expected  # only the removed file's range is gone
    assert not any(path.exists() for path in first_paths)
    # Exactly that file's copies: the other supplement's stay, and so does every other paper's.
    assert all(path.is_file() for path in _stored_paths(second))
    assert all(path.is_file() for path in _stored_paths(other_second))
    stored = _paper_object(index, paper)
    assert stored["chunkCount"] == stored["vectorCount"] == 3 + second.chunk_count
    assert (stored["processingStatus"], stored["embeddingStatus"]) == ("completed", "completed")
    with SessionLocal() as db:
        paper_row = db.get(PDFDocument, paper)
        assert paper_row.status == "completed" and paper_row.processing_completed_at is not None
    readable = resolve_readable_files(str(paper), TEST_USER_ID)
    assert [(f.chunk_index_start, f.chunk_index_end) for f in readable.files] == [
        (0, 2), (second.chunk_index_start, second.chunk_index_end),
    ]


@pytest.mark.asyncio
async def test_the_main_file_a_file_of_another_paper_and_a_bad_id_are_refused(owner_id, index, monkeypatch):
    paper = await _paper_with_supplements(owner_id, index, 20)
    other = await _paper_with_supplements(owner_id, index, 40)
    main, _ = _rows(paper)
    (_, other_supplement) = _rows(other)
    _wire(monkeypatch, owner_id)

    for file_id, status, code in (
        (main.id, 409, "main_file_not_removable"),
        (other_supplement.id, 404, "file_not_in_paper"),
        (uuid4(), 404, "file_not_in_paper"),
    ):
        with pytest.raises(HTTPException) as raised:
            await _remove(paper, file_id)
        assert (raised.value.status_code, raised.value.detail["error"]) == (status, code)
    assert len(_rows(paper)) == 2


@pytest.mark.asyncio
async def test_a_paper_keeps_at_least_one_ready_file(owner_id, index, monkeypatch):
    paper = await _paper_with_supplements(owner_id, index, 20)
    main, supplement = _rows(paper)
    with SessionLocal.begin() as db:
        db.get(PDFDocumentFile, main.id).status = "failed"
    _wire(monkeypatch, owner_id)
    with pytest.raises(HTTPException) as raised:
        await _remove(paper, supplement.id)
    assert raised.value.detail["error"] == "last_file_not_removable"


@pytest.mark.asyncio
@pytest.mark.parametrize("by", ["anchor", "envelope"])
async def test_a_file_quoted_in_a_review_session_is_refused_with_the_session(
    owner_id, index, monkeypatch, cleanup_sessions, by,
):
    paper = await _paper_with_supplements(owner_id, index, 20)
    (_, supplement) = _rows(paper)
    session_id = await asyncio.to_thread(
        _session_quoting, paper,
        anchor_file=supplement.id if by == "anchor" else None,
        envelope_file=supplement.id if by == "envelope" else None,
    )
    _wire(monkeypatch, owner_id)

    with pytest.raises(HTTPException) as raised:
        await _remove(paper, supplement.id)

    assert raised.value.status_code == 409
    assert raised.value.detail == {"error": "file_quoted_in_review", "session_ids": [str(session_id)]}
    assert len(_rows(paper)) == 2
    assert _indexes(index, str(paper))[-1] == supplement.chunk_index_end


@pytest.mark.asyncio
async def test_an_envelope_quoting_another_file_does_not_block(owner_id, index, monkeypatch, cleanup_sessions):
    paper = await _paper_with_supplements(owner_id, index, 20)
    main, supplement = _rows(paper)
    await asyncio.to_thread(_session_quoting, paper, envelope_file=main.id)
    _wire(monkeypatch, owner_id)
    assert (await _remove(paper, supplement.id))["removed"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["add_running", "paper_not_completed"])
async def test_a_removal_waits_for_nothing_and_refuses_while_files_are_written(owner_id, index, monkeypatch, state):
    paper = await _paper_with_supplements(owner_id, index, 20)
    (_, supplement) = _rows(paper)
    if state == "add_running":
        await asyncio.to_thread(supplement_add.start_supplement_add, paper, owner_id, [_chosen(40)])
        expected = "import_in_progress"
    else:
        with SessionLocal.begin() as db:
            db.get(PDFDocument, paper).status = "processing"
        expected = "paper_not_completed"
    _wire(monkeypatch, owner_id)
    with pytest.raises(HTTPException) as raised:
        await _remove(paper, supplement.id)
    assert (raised.value.status_code, raised.value.detail["error"]) == (409, expected)


@pytest.mark.asyncio
async def test_a_removal_while_another_operation_holds_the_paper_is_refused_at_once(owner_id, index, monkeypatch):
    import time

    from src.lib.document_files.paper_lock import try_lock_paper_for_add

    paper = await _paper_with_supplements(owner_id, index, 20)
    (_, supplement) = _rows(paper)
    _wire(monkeypatch, owner_id)
    locked, release = threading.Event(), threading.Event()

    def add_creating_its_job():
        with SessionLocal() as session:
            assert try_lock_paper_for_add(session, paper)
            locked.set()
            release.wait(timeout=30)
            session.rollback()

    holder = threading.Thread(target=add_creating_its_job)
    holder.start()
    await asyncio.to_thread(locked.wait, 30)
    started = time.monotonic()
    with pytest.raises(HTTPException) as raised:
        await _remove(paper, supplement.id)
    elapsed = time.monotonic() - started
    release.set()
    holder.join(timeout=30)
    assert raised.value.status_code == 409 and elapsed < 0.5
    assert raised.value.detail == "Another operation on this paper is in progress; try again in a moment."


@pytest.mark.asyncio
async def test_an_add_during_a_removal_is_refused(owner_id, index, monkeypatch):
    from src.lib.pdf_jobs.service import ActiveJobExistsError

    paper = await _paper_with_supplements(owner_id, index, 20)
    (_, supplement) = _rows(paper)
    _wire(monkeypatch, owner_id)
    in_removal = asyncio.Event()
    real_delete = chunk_reads.delete_chunk_range

    def slow_delete(*args, **kwargs):
        loop.call_soon_threadsafe(in_removal.set)
        import time

        time.sleep(1.0)
        return real_delete(*args, **kwargs)

    loop = asyncio.get_running_loop()
    monkeypatch.setattr(chunk_reads, "delete_chunk_range", slow_delete)

    async def add_during():
        await in_removal.wait()
        with pytest.raises(ActiveJobExistsError):
            await asyncio.to_thread(supplement_add.start_supplement_add, paper, owner_id, [_chosen(40)])

    removed, _ = await asyncio.gather(_remove(paper, supplement.id), add_during())
    assert removed["removed"] is True


@pytest.mark.asyncio
async def test_two_removals_of_one_file_give_one_removal(owner_id, index, monkeypatch):
    paper = await _paper_with_supplements(owner_id, index, 20, 40)
    (_, first, _) = _rows(paper)
    _wire(monkeypatch, owner_id)
    outcomes = await asyncio.gather(_remove(paper, first.id), _remove(paper, first.id), return_exceptions=True)
    removed = [o for o in outcomes if isinstance(o, dict)]
    refused = [o for o in outcomes if isinstance(o, HTTPException)]
    assert len(removed) == 1 and len(refused) == 1 and refused[0].status_code in (404, 409)


_FAILING_STEPS = {
    # The clean-up step that fails, and what to patch to make it fail.
    "pieces": "src.lib.weaviate_client.chunks.delete_chunk_range",
    "stored_files": "src.lib.document_files.service.remove_stored_files",
    "piece_count": "src.lib.pipeline.store.update_document_piece_count",
    "file_row": "src.lib.document_files.file_removal._delete_removed_row",
}


def _stored_paths(row) -> list:
    root = app_config.get_pdf_storage_path()
    return [root / path for path in (row.source_markdown_path, row.processed_json_path) if path]


def _assert_gone_for_every_reader(paper: UUID, file_id: UUID, owner_id: int) -> None:
    """No reader sees a file being removed: reads, viewer, Library count, listing, evidence, Prepare."""

    from src.api import pdf_viewer
    from src.lib.curation_workspace.evidence_files import require_paper_file
    from src.lib.curation_workspace.removed_file_gate import ResultQuotesRemovedFile, require_quoted_files_ready
    from src.lib.document_files.service import ready_file_count
    from src.lib.document_sources import file_listing_service
    from src.lib.pdf_viewer.files import find_paper_file

    readable = resolve_readable_files(str(paper), TEST_USER_ID)
    assert str(file_id) not in {f.file_id for f in readable.files}
    with SessionLocal() as db:
        listed = pdf_viewer.list_document_files(document_id=paper, db=db, user={"sub": TEST_USER_ID})
        assert file_id not in {item.file_id for item in listed.files}
        paper_row = db.get(PDFDocument, paper)
        assert find_paper_file(paper_row, file_id) is None  # content routes answer 404
        assert ready_file_count(paper_row) == 1
    removed_row = next(row for row in _rows(paper) if row.id == file_id)
    in_paper = file_listing_service._paper_lookup(paper, owner_id).file_ids
    assert removed_row.source_provider_source_file_id not in in_paper
    with SessionLocal() as db:
        with pytest.raises(HTTPException) as refused:
            require_paper_file(db, document_id=paper, file_id=str(file_id))
        assert refused.value.status_code == 400 and refused.value.detail["code"] == "file_not_in_paper"
        with pytest.raises(ResultQuotesRemovedFile):
            require_quoted_files_ready(
                db, document_id=str(paper), envelope_jsons=[{"evidence": [{"file_id": str(file_id)}]}],
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("step", sorted(_FAILING_STEPS))
@pytest.mark.parametrize("finisher", ["retry", "paper_delete"])
async def test_a_failed_clean_up_step_leaves_a_removed_row_that_a_retry_or_the_paper_delete_finishes(
    owner_id, index, monkeypatch, step, finisher,
):
    paper = await _paper_with_supplements(owner_id, index, 20)
    (main, supplement) = _rows(paper)
    leftover = list(range(supplement.chunk_index_start, supplement.chunk_index_end + 1))
    paths = _stored_paths(supplement)
    assert all(path.is_file() for path in paths)
    _wire(monkeypatch, owner_id)
    reports = []
    monkeypatch.setattr(
        "src.lib.observability.runtime.report_runtime_exception", lambda exc, **kwargs: reports.append(kwargs),
    )
    target = _FAILING_STEPS[step]
    module_path, name = target.rsplit(".", 1)
    module = __import__(module_path, fromlist=[name])
    real = getattr(module, name)

    async def failing_async(*_args, **_kwargs):
        raise RuntimeError("made-up failure")

    def failing(*_args, **_kwargs):
        raise RuntimeError("made-up failure")

    monkeypatch.setattr(module, name, failing_async if asyncio.iscoroutinefunction(real) else failing)
    answer = await _remove(paper, supplement.id)
    monkeypatch.setattr(module, name, real)

    assert answer["removed"] is True and answer["cleanup_finished"] is False
    assert [
        (r["operation"], r["level"], r["include_request"], r["context"]["step"])
        for r in reports
    ] == [("file_removal_cleanup_failed", "error", False, step)]
    (_, lingering) = _rows(paper)
    assert lingering.id == supplement.id and lingering.status == "removed"
    assert (lingering.chunk_index_start, lingering.source_markdown_path) == (
        supplement.chunk_index_start, supplement.source_markdown_path,
    )
    _assert_gone_for_every_reader(paper, supplement.id, owner_id)

    if finisher == "retry":
        again = await _remove(paper, supplement.id)
        assert again["removed"] is True and again["cleanup_finished"] is True
        assert [row.id for row in _rows(paper)] == [main.id]
        assert _indexes(index, str(paper)) == [0, 1, 2]
        stored = _paper_object(index, paper)
        assert stored["chunkCount"] == stored["vectorCount"] == 3
        # A third removal finds nothing: the file is gone.
        with pytest.raises(HTTPException) as gone:
            await _remove(paper, supplement.id)
        assert gone.value.status_code == 404
    else:
        _wire_delete(monkeypatch, owner_id)
        result = await documents_api.delete_document_endpoint(document_id=str(paper), user={"sub": TEST_USER_ID})
        assert result.success is True
        assert _rows(paper) == [] and _indexes(index, str(paper)) == []
    assert not any(path.exists() for path in paths)
    assert not set(leftover) & set(_indexes(index, str(paper)))


@pytest.mark.asyncio
async def test_a_leftover_range_never_meets_a_later_add_and_a_re_add_waits_for_the_removal(owner_id, index, monkeypatch):
    from src.lib.document_sources.supplement_add import FileRemovalUnfinished

    paper = await _paper_with_supplements(owner_id, index, 20)
    (_, supplement) = _rows(paper)
    leftover = list(range(supplement.chunk_index_start, supplement.chunk_index_end + 1))
    _wire(monkeypatch, owner_id)
    monkeypatch.setattr("src.lib.observability.runtime.report_runtime_exception", lambda exc, **kwargs: None)
    real_delete = chunk_reads.delete_chunk_range

    def failing_delete(*_args, **_kwargs):
        raise RuntimeError("made-up index failure")

    monkeypatch.setattr(chunk_reads, "delete_chunk_range", failing_delete)
    await _remove(paper, supplement.id)
    monkeypatch.setattr(chunk_reads, "delete_chunk_range", real_delete)
    assert _indexes(index, str(paper)) == [0, 1, 2, *leftover]  # the range is still there
    from src.lib.weaviate_client.documents import re_embed_document

    assert (await re_embed_document(str(paper), TEST_USER_ID))["total_chunks"] == 3  # readable pieces only

    # The same provider file cannot come back while its removed row lingers.
    with pytest.raises(FileRemovalUnfinished):
        supplement_add.start_supplement_add(paper, owner_id, [_chosen(20)])

    # Another file gets a range above every number ever used, and the leftover stays unreadable.
    job = await asyncio.to_thread(supplement_add.start_supplement_add, paper, owner_id, [_chosen(40)])
    await _add(paper, owner_id, index, [_chosen(40)], job_id=UUID(job.job_id))
    from src.lib.pdf_jobs import service as pdf_job_service

    pdf_job_service.mark_completed(job_id=job.job_id, message="made-up")
    other = next(row for row in _rows(paper) if row.source_provider_source_file_id == "40")
    assert other.chunk_index_start > max(leftover)
    readable = resolve_readable_files(str(paper), TEST_USER_ID)
    assert [(f.chunk_index_start, f.chunk_index_end) for f in readable.files] == [
        (0, 2), (other.chunk_index_start, other.chunk_index_end),
    ]

    # Removing it again finishes the removal; then the file can be added again.
    assert (await _remove(paper, supplement.id))["cleanup_finished"] is True
    job = await asyncio.to_thread(supplement_add.start_supplement_add, paper, owner_id, [_chosen(20)])
    await _add(paper, owner_id, index, [_chosen(20)], job_id=UUID(job.job_id))
    readded = next(row for row in _rows(paper) if row.source_provider_source_file_id == "20")
    assert readded.status == "ready" and readded.chunk_index_start > other.chunk_index_end


@pytest.mark.asyncio
async def test_a_failing_cache_clear_is_reported_and_the_removal_stands(owner_id, index, monkeypatch):
    paper = await _paper_with_supplements(owner_id, index, 20)
    (main, supplement) = _rows(paper)
    _wire(monkeypatch, owner_id)
    reports = []
    monkeypatch.setattr(
        "src.lib.observability.runtime.report_runtime_exception", lambda exc, **kwargs: reports.append(kwargs),
    )

    def failing(*_args, **_kwargs):
        raise RuntimeError("made-up cache failure")

    monkeypatch.setattr("src.lib.document_cache.invalidate_cache", failing)
    answer = await _remove(paper, supplement.id)
    assert answer["removed"] is True and answer["cleanup_finished"] is True
    assert [row.id for row in _rows(paper)] == [main.id]
    assert [(r["level"], r["include_request"], r["context"]["step"]) for r in reports] == [
        ("warning", False, "cache")
    ]


@pytest.mark.asyncio
async def test_a_request_cancelled_after_the_file_is_marked_leaves_a_removed_row_a_retry_finishes(
    owner_id, index, monkeypatch,
):
    paper = await _paper_with_supplements(owner_id, index, 20)
    (main, supplement) = _rows(paper)
    paths = _stored_paths(supplement)
    _wire(monkeypatch, owner_id)
    real_delete = chunk_reads.delete_chunk_range

    def cancelled(*_args, **_kwargs):
        raise asyncio.CancelledError()

    monkeypatch.setattr(chunk_reads, "delete_chunk_range", cancelled)
    with pytest.raises(asyncio.CancelledError):
        await _remove(paper, supplement.id)
    monkeypatch.setattr(chunk_reads, "delete_chunk_range", real_delete)
    (_, lingering) = _rows(paper)
    assert lingering.status == "removed" and all(path.is_file() for path in paths)
    _assert_gone_for_every_reader(paper, supplement.id, owner_id)

    again = await _remove(paper, supplement.id)  # the paper lock was released: the retry gets it
    assert again["cleanup_finished"] is True
    assert [row.id for row in _rows(paper)] == [main.id] and not any(path.exists() for path in paths)
    assert _indexes(index, str(paper)) == [0, 1, 2]


@pytest.mark.asyncio
async def test_a_removed_row_never_counts_against_the_paper_file_limit(owner_id, index, monkeypatch):
    paper = await _paper_with_supplements(owner_id, index, 20)
    (_, supplement) = _rows(paper)
    _wire(monkeypatch, owner_id)
    monkeypatch.setattr("src.lib.observability.runtime.report_runtime_exception", lambda exc, **kwargs: None)

    def failing(*_args, **_kwargs):
        raise RuntimeError("made-up failure")

    monkeypatch.setattr("src.lib.document_files.file_removal._delete_removed_row", failing)
    await _remove(paper, supplement.id)
    # The main file plus one more: the lingering removed row does not take a place.
    monkeypatch.setattr("src.lib.openai_agents.config.get_document_max_files_per_paper", lambda: 2)
    job = await asyncio.to_thread(supplement_add.start_supplement_add, paper, owner_id, [_chosen(40)])
    assert job.job_id


@pytest.mark.asyncio
async def test_with_a_one_connection_pool_a_removal_never_blocks_the_loop_and_a_timeout_is_a_503(owner_id, index, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from src.models.sql import database

    paper = await _paper_with_supplements(owner_id, index, 20)
    (_, supplement) = _rows(paper)
    _wire(monkeypatch, owner_id)
    small = create_engine(database.engine.url, pool_size=1, max_overflow=0, pool_timeout=1)
    monkeypatch.setattr(database, "engine", small)
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=small))
    held = small.connect()  # the pool's only connection is in use
    ticks = 0

    async def unrelated():
        nonlocal ticks
        for _ in range(30):
            await asyncio.sleep(0.05)
            ticks += 1

    try:
        outcome, _ = await asyncio.gather(_remove(paper, supplement.id), unrelated(), return_exceptions=True)
    finally:
        held.close()
        small.dispose()
    assert isinstance(outcome, HTTPException) and outcome.status_code == 503
    assert outcome.detail == "The server is busy right now; try removing the file again in a moment."
    assert ticks == 30
    assert len(_rows(paper)) == 2


@pytest.mark.asyncio
async def test_a_failed_leftover_of_a_dead_add_can_be_removed(owner_id, index, monkeypatch):
    paper = await _paper_with_supplements(owner_id, index, 20)
    (_, supplement) = _rows(paper)
    with SessionLocal.begin() as db:
        db.get(PDFDocumentFile, supplement.id).status = "failed"
    _wire(monkeypatch, owner_id)
    assert (await _remove(paper, supplement.id))["ready_file_count"] == 1
    assert _indexes(index, str(paper)) == [0, 1, 2]


# --- after a removal: preparation refuses a result quoting the file; anchors and removal are ordered --


def _envelope_quoting(paper: UUID, file_id: UUID, *, session_id: UUID | None = None) -> str:
    from src.schemas.domain_envelope import DomainEnvelopeStatus

    envelope_id = f"env-{uuid4()}"
    with SessionLocal.begin() as db:
        db.add(DomainEnvelopeModel(
            envelope_id=envelope_id, project_key="example", domain_pack_key="example", adapter_key="example",
            source_payload_hash="a" * 64, status=DomainEnvelopeStatus.EXTRACTED, document_id=paper,
            session_id=session_id,
            # A result not yet prepared is owned by its extraction result.
            source_extraction_result_id=None if session_id is not None else str(uuid4()),
            envelope_json={"objects": [{"evidence": [{"file_id": str(file_id), "quote": "Made-up."}]}]},
        ))
    return envelope_id


@pytest.mark.asyncio
async def test_preparation_refuses_a_result_that_quotes_a_removed_file(owner_id, index, monkeypatch, cleanup_sessions):
    from src.lib.curation_workspace.removed_file_gate import ResultQuotesRemovedFile, require_quoted_files_ready

    paper = await _paper_with_supplements(owner_id, index, 20, 40)
    (main, first, second) = _rows(paper)
    quoting_first = await asyncio.to_thread(_envelope_quoting, paper, first.id)
    quoting_second = await asyncio.to_thread(_envelope_quoting, paper, second.id)
    _wire(monkeypatch, owner_id)
    # A result not in a review session does not block the removal.
    assert (await _remove(paper, first.id))["removed"] is True

    def prepare(envelope_id):
        with SessionLocal.begin() as db:
            envelope = db.get(DomainEnvelopeModel, envelope_id)
            require_quoted_files_ready(db, document_id=str(paper), envelope_jsons=[envelope.envelope_json])

    with pytest.raises(ResultQuotesRemovedFile) as raised:
        await asyncio.to_thread(prepare, quoting_first)
    assert raised.value.code == "result_quotes_removed_file"
    await asyncio.to_thread(prepare, quoting_second)  # a result quoting a remaining file prepares

    # A ready file of ANOTHER paper is not a file of this paper: refused too.
    other_paper = await asyncio.to_thread(_paper, owner_id, index, main_end=1)
    (other_main,) = _rows(other_paper)
    with pytest.raises(ResultQuotesRemovedFile):
        with SessionLocal.begin() as db:
            require_quoted_files_ready(
                db, document_id=str(paper), envelope_jsons=[{"evidence": [{"file_id": str(other_main.id)}]}],
            )


def test_a_result_saved_before_files_were_recorded_prepares():
    from src.lib.curation_workspace.removed_file_gate import quoted_file_ids, require_quoted_files_ready

    older = {"objects": [{"evidence": [{"quote": "Made-up.", "page": 2}]}]}
    assert quoted_file_ids(older) == set()
    with SessionLocal() as db:
        require_quoted_files_ready(db, document_id=str(uuid4()), envelope_jsons=[older])


@pytest.mark.asyncio
async def test_a_removal_during_an_anchor_write_answers_at_once_and_then_refuses(
    owner_id, index, monkeypatch, cleanup_sessions,
):
    """The anchor's transaction holds the file row FOR SHARE: the removal does not wait (NOWAIT, 409);
    once the anchor is written, a removal finds the file quoted and refuses."""

    from src.lib.curation_workspace.evidence_files import require_paper_file

    paper = await _paper_with_supplements(owner_id, index, 20)
    (_, supplement) = _rows(paper)
    session_id = await asyncio.to_thread(_session_quoting, paper)  # a session with no quote yet
    _wire(monkeypatch, owner_id)
    holding, release = threading.Event(), threading.Event()

    def write_anchor():
        with SessionLocal.begin() as db:
            file_row = require_paper_file(db, document_id=paper, file_id=str(supplement.id))
            holding.set()
            release.wait(timeout=30)
            candidate = CurationCandidate(
                id=uuid4(), session_id=session_id, source=CurationCandidateSource.MANUAL,
                status=CurationCandidateStatus.PENDING, order=1, adapter_key="fixture_adapter",
                candidate_metadata={}, normalized_payload={}, created_at=NOW, updated_at=NOW,
            )
            db.add(candidate)
            db.flush()
            db.add(CurationEvidenceRecord(
                candidate_id=candidate.id, source=CurationEvidenceSource.MANUAL,
                anchor={"snippet_text": "Made-up quote."}, source_file_id=file_row.id,
            ))

    import time

    writer = threading.Thread(target=write_anchor)
    writer.start()
    await asyncio.to_thread(holding.wait, 30)
    started = time.monotonic()
    with pytest.raises(HTTPException) as busy:
        await _remove(paper, supplement.id)
    assert busy.value.status_code == 409 and time.monotonic() - started < 1.0
    assert busy.value.detail == "Another operation on this paper is in progress; try again in a moment."
    release.set()
    writer.join(timeout=30)
    with pytest.raises(HTTPException) as raised:
        await _remove(paper, supplement.id)
    assert raised.value.detail == {"error": "file_quoted_in_review", "session_ids": [str(session_id)]}
    assert len(_rows(paper)) == 2


@pytest.mark.asyncio
async def test_an_anchor_written_after_a_removal_is_refused_cleanly(owner_id, index, monkeypatch, cleanup_sessions):
    from src.lib.curation_workspace.evidence_files import require_paper_file

    paper = await _paper_with_supplements(owner_id, index, 20)
    (_, supplement) = _rows(paper)
    _wire(monkeypatch, owner_id)
    await _remove(paper, supplement.id)

    def write_anchor():
        with SessionLocal.begin() as db:
            require_paper_file(db, document_id=paper, file_id=str(supplement.id))

    with pytest.raises(HTTPException) as raised:
        await asyncio.to_thread(write_anchor)
    assert (raised.value.status_code, raised.value.detail["code"]) == (400, "file_not_in_paper")


@pytest.mark.asyncio
async def test_a_preparation_and_a_removal_are_ordered(owner_id, index, monkeypatch, cleanup_sessions):
    """A preparation holding the quoted file makes a removal answer 409 at once; once it has attached its
    envelope to the session, a removal finds the file quoted and refuses. Two long preparations can delay a
    removal only by making it answer 409; they never make it wait."""

    from src.lib.curation_workspace.removed_file_gate import require_quoted_files_ready

    paper = await _paper_with_supplements(owner_id, index, 20)
    (_, supplement) = _rows(paper)
    envelope_id = await asyncio.to_thread(_envelope_quoting, paper, supplement.id)
    session_id = await asyncio.to_thread(_session_quoting, paper)
    _wire(monkeypatch, owner_id)
    checked, release = threading.Event(), threading.Event()

    def prepare_into_session():
        with SessionLocal.begin() as db:
            envelope = db.get(DomainEnvelopeModel, envelope_id)
            require_quoted_files_ready(db, document_id=str(paper), envelope_jsons=[envelope.envelope_json])
            checked.set()
            release.wait(timeout=30)
            envelope.session_id = session_id  # as _bind_envelopes_to_review_session does

    import time

    preparer = threading.Thread(target=prepare_into_session)
    preparer.start()
    await asyncio.to_thread(checked.wait, 30)
    started = time.monotonic()
    with pytest.raises(HTTPException) as busy:
        await _remove(paper, supplement.id)
    assert busy.value.status_code == 409 and time.monotonic() - started < 1.0  # never waits on the row
    release.set()
    preparer.join(timeout=30)
    with pytest.raises(HTTPException) as raised:
        await _remove(paper, supplement.id)
    assert raised.value.detail == {"error": "file_quoted_in_review", "session_ids": [str(session_id)]}


# --- the Library's ready_file_count (agreed with b-p9b) ----------------------------------------


async def _list_rows() -> dict[str, dict]:
    from src.models.api_schemas import SortBy, SortOrder

    listed = await documents_api.list_documents_endpoint(
        user={"sub": TEST_USER_ID}, page=1, page_size=100, search=None, embedding_status=None,
        sort_by=SortBy.CREATION_DATE, sort_order=SortOrder.DESC, date_from=None, date_to=None,
        min_vector_count=None, max_vector_count=None,
    )
    return {row["document_id"]: row for row in listed.documents}


@pytest.mark.asyncio
async def test_ready_file_count_on_the_list_and_the_details(owner_id, index, monkeypatch):
    monkeypatch.setattr("src.lib.weaviate_client.documents.get_connection", lambda: index)
    with_files = _dated(index, await _paper_with_supplements(owner_id, index, 20, 40), 0)
    main_only = _dated(index, await asyncio.to_thread(_paper, owner_id, index, main_end=1), 1)
    (_, first, second) = _rows(with_files)
    with SessionLocal.begin() as db:
        db.get(PDFDocumentFile, second.id).status = "failed"  # a failed leftover is not counted
    rows = await _list_rows()
    assert rows[str(with_files)]["ready_file_count"] == 2
    assert rows[str(main_only)]["ready_file_count"] == 1

    _wire(monkeypatch, owner_id)
    await _remove(with_files, first.id)
    assert (await _list_rows())[str(with_files)]["ready_file_count"] == 1
    detail = await documents_api.get_document_endpoint(document_id=str(with_files), user={"sub": TEST_USER_ID})
    assert detail.ready_file_count == 1


def _dated(connection, paper: UUID, minute: int) -> UUID:
    """Give the paper's search object the creationDate every real paper has (create_document sets it).

    The shared fixture leaves it out, and Weaviate's sorted fetch (the Library sorts by
    creationDate) then drops undated objects once they near the page size: review P2-2,
    an artefact of that fixture shape, never of a real paper.
    """

    from datetime import timedelta

    from .conftest import TEST_TENANT_NAME

    with connection.session() as client:
        client.collections.get("PDFDocument").with_tenant(TEST_TENANT_NAME).data.update(
            uuid=paper, properties={"creationDate": (NOW + timedelta(minutes=minute)).isoformat()},
        )
    return paper


@pytest.mark.asyncio
async def test_the_list_lists_every_paper_with_no_query_per_row(owner_id, index, monkeypatch):
    from sqlalchemy import event

    from src.models.sql.database import engine

    monkeypatch.setattr("src.lib.weaviate_client.documents.get_connection", lambda: index)
    statements: list[str] = []

    def count(_conn, _cursor, statement, *_args):
        statements.append(statement)

    first = _dated(index, await asyncio.to_thread(_paper, owner_id, index, main_end=1), 0)
    await _list_rows()  # warm: a first listing loads things once, not per row
    event.listen(engine, "before_cursor_execute", count)
    try:
        assert str(first) in await _list_rows()
        few = len(statements)
        more = [_dated(index, await _paper_with_supplements(owner_id, index, 20), minute) for minute in range(1, 5)]
        statements.clear()
        listed = await _list_rows()
        many = len(statements)
    finally:
        event.remove(engine, "before_cursor_execute", count)
    assert {str(paper) for paper in (first, *more)} <= set(listed)  # every created paper is listed
    # Exactly four statements for one paper or five (the files load in one query for all rows).
    assert few == many == 4, (few, many)


# --- the read-only pre-check for the removal confirmation ---------------------------------------


def _prepared_result_quoting(paper: UUID, envelope_id: str) -> None:
    """A prepared result of the owner for the paper, replaying one envelope (what Review & Curate replays)."""

    from src.lib.curation_workspace.curation_prep_constants import CURATION_PREP_AGENT_ID
    from src.lib.curation_workspace.models import CurationExtractionResultRecord
    from src.schemas.curation_prep import CurationPrepAgentOutput
    from src.schemas.curation_workspace import CurationExtractionSourceKind

    with SessionLocal.begin() as db:
        envelope = db.get(DomainEnvelopeModel, envelope_id)
        prep = CurationPrepAgentOutput.model_validate({"envelope_refs": [{
            "envelope_id": envelope.envelope_id, "envelope_revision": envelope.revision,
            "source_extraction_result_id": envelope.source_extraction_result_id,
            "domain_pack_id": "example", "review_row_count": 1, "retired_record_count": 0}],
            "review_row_count": 1, "run_metadata": {"model_name": "deterministic_programmatic_mapper_v1",
                "token_usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}}})
        db.add(CurationExtractionResultRecord(
            document_id=paper, adapter_key="example", agent_key=CURATION_PREP_AGENT_ID,
            source_kind=CurationExtractionSourceKind.CHAT, user_id=TEST_USER_ID, candidate_count=1,
            payload_json=prep.model_dump(mode="json"),
            extraction_metadata={"final_run_metadata": prep.run_metadata.model_dump(mode="json")},
        ))


async def _check(paper: UUID, file_id: UUID) -> dict:
    return await documents_api.removal_check_endpoint(
        document_id=str(paper), file_id=str(file_id), user={"sub": TEST_USER_ID},
    )


@pytest.mark.asyncio
async def test_the_pre_check_says_whether_review_and_curate_would_refuse_after_the_removal(
    owner_id, index, monkeypatch, cleanup_sessions,
):
    paper = await _paper_with_supplements(owner_id, index, 20, 40)
    (main, first, second) = _rows(paper)
    _wire(monkeypatch, owner_id)
    assert await _check(paper, first.id) == {
        "document_id": str(paper), "file_id": str(first.id), "removable": True, "error": None,
        "newest_result_quotes_file": False,  # nothing prepared yet
        "has_review_session": False, "review_and_curate_would_refuse": False,
    }
    envelope_id = await asyncio.to_thread(_envelope_quoting, paper, first.id)
    await asyncio.to_thread(_prepared_result_quoting, paper, envelope_id)
    checked = await _check(paper, first.id)
    # No session to open: Review & Curate replays the newest result, which quotes the file.
    assert (checked["newest_result_quotes_file"], checked["review_and_curate_would_refuse"]) == (True, True)
    assert (await _check(paper, second.id))["review_and_curate_would_refuse"] is False
    checked = await _check(paper, main.id)
    assert (checked["removable"], checked["error"]) == (False, "main_file_not_removable")

    session_id = await asyncio.to_thread(_session_quoting, paper, anchor_file=second.id)
    checked = await _check(paper, second.id)
    assert (checked["removable"], checked["error"], checked["session_ids"]) == (
        False, "file_quoted_in_review", [str(session_id)],
    )
    # Now the paper has a session, which Review & Curate opens: removing the first file
    # (quoted only by the newest result) would not make that button refuse.
    checked = await _check(paper, first.id)
    assert (checked["newest_result_quotes_file"], checked["has_review_session"]) == (True, True)
    assert checked["review_and_curate_would_refuse"] is False
    # The pre-check changes nothing: both files are still ready.
    assert [row.status for row in _rows(paper)] == ["ready", "ready", "ready"]


@pytest.mark.asyncio
@pytest.mark.parametrize("status, swept_to", [
    ("pending", "failed"), ("running", "failed"), ("cancel_requested", "cancelled"),
])
async def test_a_dead_job_stops_blocking_a_removal_even_after_a_refused_attempt(
    owner_id, index, monkeypatch, status, swept_to,
):
    """Review R-stale / M16: through the endpoint, a refusal while the job is live, then the retry once it is dead."""

    from sqlalchemy import text

    from src.lib.pdf_jobs import service as pdf_job_service
    from src.models.sql.pdf_processing_job import PdfProcessingJob

    paper = await _paper_with_supplements(owner_id, index, 20)
    (main, supplement) = _rows(paper)
    _wire(monkeypatch, owner_id)
    job = await asyncio.to_thread(supplement_add.start_supplement_add, paper, owner_id, [_chosen(40)])
    with SessionLocal.begin() as db:
        db.execute(text("UPDATE pdf_processing_jobs SET status = :s WHERE id = :i"), {"s": status, "i": job.job_id})

    with pytest.raises(HTTPException) as refused:
        await _remove(paper, supplement.id)  # the job is live: refused
    assert (refused.value.status_code, refused.value.detail) == (409, {"error": "import_in_progress"})

    # The job goes silent for longer than the stale limit (an add that died).
    silent = pdf_job_service._stale_timeout_seconds() + 60
    with SessionLocal.begin() as db:
        db.execute(text(
            "UPDATE pdf_processing_jobs SET updated_at = now() - make_interval(secs => :s), "
            "started_at = now() - make_interval(secs => :s) WHERE id = :i"
        ), {"s": silent, "i": job.job_id})

    answer = await _remove(paper, supplement.id)  # the same removal, retried
    assert answer["removed"] is True and answer["cleanup_finished"] is True
    assert [row.id for row in _rows(paper)] == [main.id]
    with SessionLocal() as db:
        assert db.get(PdfProcessingJob, UUID(job.job_id)).status == swept_to
