"""PostgreSQL coverage for the one-off repair of papers the pre-job pipeline left pending.

Papers are built in the real shape of the ones on production: status ``pending``,
no error, no processing times, no processing job, uploaded before the first job
row, with a stored PDF, processed elements, extraction output, unbroken pieces in
the search index and an index document marked completed. The search index is a
fake with that shape. Made-up names only.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command  # pyright: ignore[reportAttributeAccessIssue]
from alembic.config import Config  # pyright: ignore[reportMissingImports]
from sqlalchemy import select, text

from src.lib.openai_agents import document_references
from src.lib.pdf_jobs import legacy_completion_repair as repair
from src.lib.pdf_jobs import service as pdf_job_service
from src.models.sql.database import SessionLocal
from src.models.sql.pdf_document import PDFDocument
from src.models.sql.pdf_processing_job import PdfJobStatus, PdfProcessingJob
from tests.pdf_document_test_support import ensure_test_pdf_owner

BACKEND_ROOT = Path(__file__).resolve().parents[3]
LEGACY_UPLOAD = datetime(2025, 6, 2, 9, 30, tzinfo=timezone.utc)
FIRST_JOB = datetime(2026, 3, 6, 16, 46, tzinfo=timezone.utc)
NOW = datetime(2026, 11, 22, 20, tzinfo=timezone.utc)


class FakeIndex:
    """The search index: pieces per paper and the index document's statuses."""

    def __init__(self):
        self.pieces: dict[str, dict[str, list[int]]] = {}
        self.statuses: dict[str, tuple[str, str]] = {}

    def piece_numbers(self, owner_auth_sub):
        return self.pieces.get(owner_auth_sub, {})

    def document_statuses(self, owner_auth_sub, document_id):
        return self.statuses.get(document_id)


@pytest.fixture(scope="module", autouse=True)
def _schema():
    command.upgrade(Config(str(BACKEND_ROOT / "alembic.ini")), "head")


@pytest.fixture
def world(tmp_path):
    """An owner, a first job row on another paper, storage and the index."""

    auth_sub = f"legacy-repair-{uuid4()}"
    created: list[UUID] = []
    with SessionLocal() as session:
        owner_id = ensure_test_pdf_owner(session, auth_sub=auth_sub)
        anchor_id = uuid4()
        session.add(PDFDocument(
            id=anchor_id, filename="anchor.pdf", file_path=f"{auth_sub}/anchor.pdf",
            file_hash=anchor_id.hex * 2, file_size=10, page_count=1, user_id=owner_id,
            status="completed", upload_timestamp=FIRST_JOB, processing_started_at=FIRST_JOB,
            processing_completed_at=FIRST_JOB,
        ))
        session.flush()
        session.add(PdfProcessingJob(
            document_id=anchor_id, user_id=owner_id, filename="anchor.pdf",
            status=PdfJobStatus.COMPLETED.value, current_stage="completed", progress_percentage=100,
            message="done", created_at=FIRST_JOB, started_at=FIRST_JOB, updated_at=FIRST_JOB,
            completed_at=FIRST_JOB,
        ))
        session.commit()
        created.append(anchor_id)
    state = {
        "auth_sub": auth_sub, "owner_id": owner_id, "storage": tmp_path, "index": FakeIndex(),
        "created": created,
    }
    yield state
    with SessionLocal() as session:
        session.execute(text("DELETE FROM pdf_processing_jobs WHERE document_id = ANY(:ids)"),
                        {"ids": created})
        session.execute(text("DELETE FROM pdf_documents WHERE id = ANY(:ids)"), {"ids": created})
        session.commit()


def _write(storage: Path, relative: str, content: str | bytes) -> None:
    path = storage / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))


def legacy_paper(world, **overrides) -> UUID:
    """A paper in the real shape of the 48: complete, but pending with no times and no job."""

    auth_sub, storage, document_id = world["auth_sub"], world["storage"], uuid4()
    values = {
        "id": document_id, "filename": f"made-up-{document_id}.pdf",
        "file_path": f"{auth_sub}/{document_id}.pdf", "file_hash": document_id.hex * 2,
        "file_size": 2048, "page_count": 7, "user_id": world["owner_id"], "status": "pending",
        "upload_timestamp": LEGACY_UPLOAD + timedelta(minutes=len(world["created"])),
        "processed_json_path": f"{auth_sub}/processed_json/{document_id}.json",
        "pdfx_json_path": f"{auth_sub}/pdfx_json/{document_id}.json",
        "hierarchy_metadata": {"sections": [], "top_level_sections": []},
    }
    values.update(overrides.pop("columns", {}))
    _write(storage, f"{auth_sub}/{document_id}.pdf", b"%PDF-1.7\nmade-up")
    _write(storage, f"{auth_sub}/processed_json/{document_id}.json",
           json.dumps([{"type": "NarrativeText", "text": "Made-up flies grew."}]))
    _write(storage, f"{auth_sub}/pdfx_json/{document_id}.json", "{}")
    world["index"].pieces.setdefault(auth_sub, {})[str(document_id)] = [2, 0, 1]
    world["index"].statuses[str(document_id)] = ("completed", "completed")
    with SessionLocal() as session:
        session.add(PDFDocument(**values))
        session.commit()
    world["created"].append(document_id)
    return document_id


def _dry_run(world):
    return repair.dry_run(SessionLocal, storage_root=world["storage"], index=world["index"], now=NOW)


def _apply(world, report):
    return repair.apply(
        SessionLocal, storage_root=world["storage"], index=world["index"], now=NOW,
        expect_count=len(report.qualifying), expect_digest=report.selection_digest,
    )


def _row(document_id):
    with SessionLocal() as session:
        paper = session.get(PDFDocument, document_id)
        return (paper.status, paper.error_message, paper.processing_started_at,
                paper.processing_completed_at, paper.last_accessed)


def _database_digest() -> dict[str, str]:
    """A digest of every row of every table in the public schema."""

    with SessionLocal() as session:
        tables = session.execute(text(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename"
        )).scalars().all()
        digests = {}
        for table in tables:
            rows = session.execute(text(f'SELECT row_to_json(t)::text FROM "{table}" t')).scalars().all()
            digests[table] = hashlib.sha256("\n".join(sorted(rows)).encode()).hexdigest()
        return digests


def _qualifying_ids(report) -> set[UUID]:
    return {candidate.document_id for candidate in report.qualifying}


# --- the reading check ---------------------------------------------------------------


def test_a_legacy_paper_is_refused_by_the_reading_check_before_and_readable_after(world):
    document_id = legacy_paper(world)

    with SessionLocal() as session:
        with pytest.raises(document_references.DocumentReferenceError):
            document_references._generation(session, str(document_id), world["auth_sub"])

    report = _dry_run(world)
    assert document_id in _qualifying_ids(report)
    applied = _apply(world, report)
    assert applied.written == len(report.qualifying)

    with SessionLocal() as session:
        generation = document_references._generation(session, str(document_id), world["auth_sub"])
    assert datetime.fromisoformat(generation) == world_upload(document_id)
    status, error, started, completed, _ = _row(document_id)
    assert (status, error) == ("completed", None)
    assert started == completed == world_upload(document_id)
    with SessionLocal() as session:
        assert session.scalar(
            select(PdfProcessingJob.id).where(PdfProcessingJob.document_id == document_id)
        ) is None


def world_upload(document_id):
    with SessionLocal() as session:
        return session.get(PDFDocument, document_id).upload_timestamp


# --- every check, failing alone ---------------------------------------------------------


def _break_s5_missing_pdf(world, document_id):
    (world["storage"] / world["auth_sub"] / f"{document_id}.pdf").unlink()


def _break_s6_unreadable_elements(world, document_id):
    _write(world["storage"], f"{world['auth_sub']}/processed_json/{document_id}.json", "not json")


def _break_s7_no_extraction_output(world, document_id):
    (world["storage"] / world["auth_sub"] / "pdfx_json" / f"{document_id}.json").unlink()


def _break_s8_gap_in_pieces(world, document_id):
    world["index"].pieces[world["auth_sub"]][str(document_id)] = [0, 2]


def _break_s8_no_pieces(world, document_id):
    del world["index"].pieces[world["auth_sub"]][str(document_id)]


def _break_s9_index_not_done(world, document_id):
    world["index"].statuses[str(document_id)] = ("completed", "pending")


@pytest.mark.parametrize(
    ("columns", "breaker", "failed_check"),
    [
        ({"error_message": "made-up failure"}, None, "S2_no_error_or_processing_times"),
        ({"processing_started_at": LEGACY_UPLOAD}, None, "S2_no_error_or_processing_times"),
        ({"upload_timestamp": FIRST_JOB + timedelta(days=1)}, None, "S4_uploaded_before_first_job"),
        ({"file_path": "someone-else/x.pdf"}, None, "S5_pdf_stored"),
        ({}, _break_s5_missing_pdf, "S5_pdf_stored"),
        ({}, _break_s6_unreadable_elements, "S6_processed_elements_readable"),
        ({"processed_json_path": None}, None, "S6_processed_elements_readable"),
        ({}, _break_s7_no_extraction_output, "S7_extraction_output_present"),
        ({}, _break_s8_gap_in_pieces, "S8_pieces_in_search_index"),
        ({}, _break_s8_no_pieces, "S8_pieces_in_search_index"),
        ({}, _break_s9_index_not_done, "S9_index_document_completed"),
    ],
)
def test_a_paper_failing_one_check_is_reported_and_never_written(world, columns, breaker, failed_check):
    complete_id = legacy_paper(world)
    broken_id = legacy_paper(world, columns=columns)
    if breaker is not None:
        breaker(world, broken_id)
    before = _row(broken_id)

    report = _dry_run(world)
    partial = {candidate.document_id: candidate.failed_checks for candidate in report.partial}
    assert partial[broken_id] == (failed_check,)
    assert report.exit_code() == repair.EXIT_PARTIAL_MATCHES

    applied = _apply(world, report)

    assert broken_id not in _qualifying_ids(applied)
    assert _row(broken_id) == before
    assert _row(complete_id)[0] == "completed"


def test_s1_and_s3_papers_are_not_candidates_and_stay_untouched(world):
    failed_id = legacy_paper(world, columns={"status": "failed"})
    with_job_id = legacy_paper(world)
    with SessionLocal() as session:
        session.add(PdfProcessingJob(
            document_id=with_job_id, user_id=world["owner_id"], filename="job.pdf",
            status=PdfJobStatus.RUNNING.value, current_stage="parsing", progress_percentage=10,
            message="running", created_at=NOW, started_at=NOW, updated_at=NOW,
        ))
        session.commit()
    before = {document_id: _row(document_id) for document_id in (failed_id, with_job_id)}

    report = _dry_run(world)
    _apply(world, report)

    candidate_ids = {candidate.document_id for candidate in report.candidates}
    assert failed_id not in candidate_ids and with_job_id not in candidate_ids
    assert {document_id: _row(document_id) for document_id in before} == before


# --- dry run, apply, idempotence, nothing else changes -----------------------------------


def test_the_dry_run_writes_nothing(world):
    legacy_paper(world)
    before = _database_digest()

    report = _dry_run(world)

    assert report.qualifying
    assert _database_digest() == before


def test_apply_changes_only_the_three_columns_of_the_qualifying_papers(world):
    document_id = legacy_paper(world)
    report = _dry_run(world)
    repaired = _qualifying_ids(report)
    assert document_id in repaired
    rows_before = {paper_id: _row(paper_id) for paper_id in repaired}
    with SessionLocal() as session:
        other_documents_before = session.execute(text(
            "SELECT id, row_to_json(d)::text FROM pdf_documents d WHERE NOT (id = ANY(:ids))"
        ), {"ids": list(repaired)}).all()
    digest_before = _database_digest()
    with SessionLocal() as session:
        last_audit_id = session.scalar(text("SELECT coalesce(max(id), 0) FROM audit_log"))

    _apply(world, report)

    digest_after = _database_digest()
    # pdf_documents changed; the database's own audit trigger recorded each update.
    assert {table for table in digest_before if digest_before[table] != digest_after[table]} == {
        "pdf_documents", "audit_log",
    }
    with SessionLocal() as session:
        audit_rows = session.execute(text(
            "SELECT table_name, operation, row_id, changed_fields FROM audit_log WHERE id > :last ORDER BY id"
        ), {"last": last_audit_id}).all()
    assert sorted(row.row_id for row in audit_rows) == sorted(str(paper_id) for paper_id in repaired)
    for row in audit_rows:
        assert (row.table_name, row.operation) == ("pdf_documents", "UPDATE")
        assert set(row.changed_fields) == {"status", "processing_started_at", "processing_completed_at"}
    with SessionLocal() as session:
        assert session.execute(text(
            "SELECT id, row_to_json(d)::text FROM pdf_documents d WHERE NOT (id = ANY(:ids))"
        ), {"ids": list(repaired)}).all() == other_documents_before
    for paper_id in repaired:
        status, error, started, completed, last_accessed = _row(paper_id)
        assert (status, error) == ("completed", None)
        assert started == completed == world_upload(paper_id)
        # The column's on-update default did not run.
        assert last_accessed == rows_before[paper_id][4]


def test_a_second_run_finds_nothing_and_changes_nothing(world):
    document_id = legacy_paper(world)
    _apply(world, _dry_run(world))
    after_first = _row(document_id)

    second = _dry_run(world)
    assert document_id not in {candidate.document_id for candidate in second.candidates}
    _apply(world, second)

    assert _row(document_id) == after_first


@pytest.mark.parametrize("mismatch", ["count", "digest", "new_paper"])
def test_apply_refuses_when_the_selection_is_not_the_dry_runs(world, mismatch):
    document_id = legacy_paper(world)
    report = _dry_run(world)
    expect_count, expect_digest = len(report.qualifying), report.selection_digest
    if mismatch == "count":
        expect_count += 1
    elif mismatch == "digest":
        expect_digest = "0" * 64
    else:
        legacy_paper(world)
    before = _database_digest()

    with pytest.raises(repair.LegacyRepairError, match="selection_changed"):
        repair.apply(SessionLocal, storage_root=world["storage"], index=world["index"], now=NOW,
                     expect_count=expect_count, expect_digest=expect_digest)

    assert _database_digest() == before
    assert _row(document_id)[0] == "pending"


# --- restore -----------------------------------------------------------------------------


def test_restore_puts_back_what_the_apply_report_recorded(world):
    document_id = legacy_paper(world)
    before = _row(document_id)
    applied = _apply(world, _dry_run(world))
    report_json = json.loads(json.dumps(applied.to_json()))

    restored = repair.restore(SessionLocal, report=report_json,
                              expect_digest=report_json["selection_digest"], now=NOW)

    assert str(document_id) in restored.restored
    assert _row(document_id) == before


def test_restore_refuses_when_a_paper_changed_since_the_repair(world):
    document_id = legacy_paper(world)
    applied = _apply(world, _dry_run(world))
    report_json = json.loads(json.dumps(applied.to_json()))
    with SessionLocal() as session:
        session.execute(text("UPDATE pdf_documents SET processing_completed_at = :at WHERE id = :id"),
                        {"at": NOW, "id": document_id})
        session.commit()
    changed = _row(document_id)

    with pytest.raises(repair.LegacyRepairError):
        repair.restore(SessionLocal, report=report_json, expect_digest=report_json["selection_digest"], now=NOW)

    assert _row(document_id) == changed


# --- the orphan repair never fails a processed paper -------------------------------------


def test_orphan_repair_skips_a_pending_paper_with_processed_output(world, monkeypatch):
    monkeypatch.setenv("PDF_NO_JOB_ORPHAN_THRESHOLD_SECONDS", "86400")
    monkeypatch.setenv("PDF_NO_JOB_ORPHAN_BATCH_SIZE", "1000")
    monkeypatch.setenv("PDF_NO_JOB_ORPHAN_REPAIR_TIMEOUT_SECONDS", "30")
    monkeypatch.setenv("PDF_NO_JOB_ORPHAN_REPAIR_RETRY_COUNT", "0")
    legacy_id = legacy_paper(world)
    only_extraction_id = legacy_paper(world, columns={"processed_json_path": None})
    orphan_id = legacy_paper(world, columns={"processed_json_path": None, "pdfx_json_path": None})

    summary = pdf_job_service.reconcile_pending_documents_without_jobs(apply=True, now=NOW)

    assert str(orphan_id) in {record.document_id for record in summary.records}
    skipped = {record.document_id for record in summary.skipped}
    assert {str(legacy_id), str(only_extraction_id)} <= skipped
    assert _row(legacy_id)[0] == "pending"
    assert _row(only_extraction_id)[0] == "pending"
    assert _row(orphan_id)[0] == "failed"
    with SessionLocal() as session:
        assert session.scalar(
            select(PdfProcessingJob.id).where(PdfProcessingJob.document_id == legacy_id)
        ) is None
