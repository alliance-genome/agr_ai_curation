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
    main, first, second = _rows(paper)
    first_paths = [app_config.get_pdf_storage_path() / path for path in (first.source_markdown_path, first.processed_json_path)]
    assert all(path.is_file() for path in first_paths)
    _wire(monkeypatch, owner_id)

    answer = await _remove(paper, first.id)

    assert answer == {"document_id": str(paper), "file_id": str(first.id), "removed": True, "ready_file_count": 2}
    remaining = _rows(paper)
    assert [row.id for row in remaining] == [main.id, second.id]
    expected = [*range(0, 3), *range(second.chunk_index_start, second.chunk_index_end + 1)]
    assert _indexes(index, str(paper)) == expected  # only the removed file's range is gone
    assert not any(path.exists() for path in first_paths)
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


@pytest.mark.asyncio
async def test_a_piece_delete_failing_after_the_row_is_gone_still_removes_the_file(owner_id, index, monkeypatch):
    paper = await _paper_with_supplements(owner_id, index, 20)
    (_, supplement) = _rows(paper)
    _wire(monkeypatch, owner_id)
    reports = []
    monkeypatch.setattr(
        "src.lib.observability.runtime.report_runtime_exception", lambda exc, **kwargs: reports.append(kwargs),
    )

    def failing_delete(*_args, **_kwargs):
        raise RuntimeError("made-up index failure")

    monkeypatch.setattr(chunk_reads, "delete_chunk_range", failing_delete)
    answer = await _remove(paper, supplement.id)

    assert answer["removed"] is True and len(_rows(paper)) == 1
    assert [r["operation"] for r in reports] == ["file_removal_cleanup_failed"]
    readable = resolve_readable_files(str(paper), TEST_USER_ID)
    assert [(f.chunk_index_start, f.chunk_index_end) for f in readable.files] == [(0, 2)]  # orphans unreadable


@pytest.mark.asyncio
async def test_a_leftover_range_never_meets_a_later_add_and_the_paper_delete_clears_it(owner_id, index, monkeypatch):
    paper = await _paper_with_supplements(owner_id, index, 20)
    (_, supplement) = _rows(paper)
    leftover = list(range(supplement.chunk_index_start, supplement.chunk_index_end + 1))
    _wire(monkeypatch, owner_id)
    monkeypatch.setattr("src.lib.observability.runtime.report_runtime_exception", lambda exc, **kwargs: None)

    def failing_delete(*_args, **_kwargs):
        raise RuntimeError("made-up index failure")

    real_delete = chunk_reads.delete_chunk_range
    monkeypatch.setattr(chunk_reads, "delete_chunk_range", failing_delete)
    await _remove(paper, supplement.id)
    monkeypatch.setattr(chunk_reads, "delete_chunk_range", real_delete)
    assert _indexes(index, str(paper)) == [0, 1, 2, *leftover]  # the range was left behind

    # The same provider file added again gets a fresh range above every number ever used.
    job = await asyncio.to_thread(supplement_add.start_supplement_add, paper, owner_id, [_chosen(20)])
    await _add(paper, owner_id, index, [_chosen(20)], job_id=UUID(job.job_id))
    (_, again) = _rows(paper)
    assert again.chunk_index_start > max(leftover)
    readable = resolve_readable_files(str(paper), TEST_USER_ID)
    assert [(f.chunk_index_start, f.chunk_index_end) for f in readable.files] == [
        (0, 2), (again.chunk_index_start, again.chunk_index_end),
    ]
    from src.lib.pdf_jobs import service as pdf_job_service

    pdf_job_service.mark_completed(job_id=job.job_id, message="made-up")

    # Deleting the paper removes every piece by document id, the leftover range included.
    _wire_delete(monkeypatch, owner_id)
    result = await documents_api.delete_document_endpoint(document_id=str(paper), user={"sub": TEST_USER_ID})
    assert result.success is True
    assert _indexes(index, str(paper)) == []


@pytest.mark.asyncio
async def test_with_a_pool_of_two_a_removal_never_blocks_the_loop_and_a_timeout_is_a_503(owner_id, index, monkeypatch):
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
    with_files = await _paper_with_supplements(owner_id, index, 20, 40)
    main_only = await asyncio.to_thread(_paper, owner_id, index, main_end=1)
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


@pytest.mark.asyncio
async def test_the_list_adds_no_query_per_row(owner_id, index, monkeypatch):
    from sqlalchemy import event

    from src.models.sql.database import engine

    monkeypatch.setattr("src.lib.weaviate_client.documents.get_connection", lambda: index)
    statements: list[str] = []

    def count(_conn, _cursor, statement, *_args):
        statements.append(statement)

    await asyncio.to_thread(_paper, owner_id, index, main_end=1)
    event.listen(engine, "before_cursor_execute", count)
    try:
        await _list_rows()
        few = len(statements)
        statements.clear()
        for _ in range(4):
            await _paper_with_supplements(owner_id, index, 20)
        statements.clear()
        await _list_rows()
        many = len(statements)
    finally:
        event.remove(engine, "before_cursor_execute", count)
    assert many == few  # four more papers, the same number of queries
