"""One-off repair: mark complete papers that the pre-job pipeline left ``pending``.

Before v0.3.0 (24 Feb 2026) the processing pipeline set only the search index's
status; it never wrote ``pdf_documents.status`` (server default ``pending``) or the
processing times. Papers processed then are complete (stored PDF, processed
elements, extraction output, pieces in the search index) but still read
``pending`` with no times and no processing job. Since v0.10.9 the reading tools
refuse a paper unless it is ``completed`` with a completion time, so the AI cannot
read them.

A paper is repaired only when every structural check holds (``CHECKS``). A paper
that is ``pending`` with no job but fails any other check is reported and never
written. The repair sets ``status = 'completed'`` and both processing times to the
paper's upload time (the database holds no real value); ``error_message`` stays
NULL and no job row is created. Nothing else is written.

Run it in the backend container of the release that is live:

    python -m src.lib.pdf_jobs.legacy_completion_repair            # dry run
    python -m src.lib.pdf_jobs.legacy_completion_repair --apply \\
        --expect-count N --expect-digest <digest from the dry run>
    python -m src.lib.pdf_jobs.legacy_completion_repair --restore-from-report PATH \\
        --expect-digest <digest in that report>

The apply step writes in one transaction, all or nothing, and only when the papers
that qualify are exactly the ones the dry run reported (count and digest). The
report holds internal ids, timestamps, statuses and check names; never titles,
file names, owners or text.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence
from uuid import UUID

from sqlalchemy import exists, func, select, update
from sqlalchemy.orm import Session

from src.models.sql.pdf_document import PDFDocument
from src.models.sql.pdf_processing_job import PdfProcessingJob
from src.models.sql.user import User

STATUS_PENDING = "pending"
STATUS_COMPLETED = "completed"
INDEX_DONE = "completed"

CHECKS = (
    "S1_status_pending",
    "S2_no_error_or_processing_times",
    "S3_no_processing_job",
    "S4_uploaded_before_first_job",
    "S5_pdf_stored",
    "S6_processed_elements_readable",
    "S7_extraction_output_present",
    "S8_pieces_in_search_index",
    "S9_index_document_completed",
)

EXIT_NOTHING_TO_DO = 0
EXIT_ERROR = 1
EXIT_WOULD_CHANGE = 3
EXIT_CHANGED = 4
EXIT_PARTIAL_MATCHES = 5


class LegacyRepairError(RuntimeError):
    """The repair cannot run safely; nothing was written."""


class IndexReader(Protocol):
    """Read-only view of the search index for one owner's tenant."""

    def piece_numbers(self, owner_auth_sub: str) -> Mapping[str, list[int]]:
        """Every stored piece number of the owner's tenant, by ``documentId``."""

    def document_statuses(self, owner_auth_sub: str, document_id: str) -> tuple[str, str] | None:
        """The index document's ``processingStatus`` and ``embeddingStatus``, or None."""


@dataclass(frozen=True)
class PreviousState:
    status: str
    error_message: str | None
    processing_started_at: datetime | None
    processing_completed_at: datetime | None

    def to_json(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "error_message": self.error_message,
            "processing_started_at": _iso(self.processing_started_at),
            "processing_completed_at": _iso(self.processing_completed_at),
        }


@dataclass(frozen=True)
class Candidate:
    """A paper that is ``pending`` with no processing job, and its check results."""

    document_id: UUID
    upload_timestamp: datetime
    previous: PreviousState
    failed_checks: tuple[str, ...]

    @property
    def qualifies(self) -> bool:
        return not self.failed_checks


@dataclass(frozen=True)
class RepairReport:
    mode: str
    run_at: datetime
    first_job_created_at: datetime | None
    candidates: tuple[Candidate, ...]
    written: int = 0
    restored: tuple[str, ...] = field(default=())

    @property
    def qualifying(self) -> tuple[Candidate, ...]:
        return tuple(candidate for candidate in self.candidates if candidate.qualifies)

    @property
    def partial(self) -> tuple[Candidate, ...]:
        return tuple(candidate for candidate in self.candidates if not candidate.qualifies)

    @property
    def selection_digest(self) -> str:
        return selection_digest(candidate.document_id for candidate in self.qualifying)

    def exit_code(self) -> int:
        if self.partial:
            return EXIT_PARTIAL_MATCHES
        if self.mode == "apply" and self.written:
            return EXIT_CHANGED
        if self.mode == "restore" and self.restored:
            return EXIT_CHANGED
        if self.mode == "dry_run" and self.qualifying:
            return EXIT_WOULD_CHANGE
        return EXIT_NOTHING_TO_DO

    def to_json(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "run_at": _iso(self.run_at),
            "checks": list(CHECKS),
            "first_job_created_at": _iso(self.first_job_created_at),
            "candidate_count": len(self.candidates),
            "qualifying_count": len(self.qualifying),
            "partial_count": len(self.partial),
            "selection_digest": self.selection_digest,
            "written": self.written,
            "restored": list(self.restored),
            "qualifying": [
                {
                    "document_id": str(candidate.document_id),
                    "upload_timestamp": _iso(candidate.upload_timestamp),
                    "previous": candidate.previous.to_json(),
                    "repaired_to": {
                        "status": STATUS_COMPLETED,
                        "processing_started_at": _iso(candidate.upload_timestamp),
                        "processing_completed_at": _iso(candidate.upload_timestamp),
                    },
                }
                for candidate in self.qualifying
            ],
            "partial": [
                {"document_id": str(candidate.document_id), "failed_checks": list(candidate.failed_checks)}
                for candidate in self.partial
            ],
        }


def selection_digest(document_ids: Iterable[UUID | str]) -> str:
    """SHA-256 of the sorted internal ids; ties an apply to the dry run it follows."""

    joined = "\n".join(sorted(str(document_id) for document_id in document_ids))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


# --- checks ------------------------------------------------------------------------


def _owned_file(storage_root: Path, relative_path: str | None, owner_auth_sub: str) -> Path | None:
    """The stored file under the owner's folder, or None when unset, outside it or missing."""

    if not relative_path:
        return None
    owner_root = (storage_root / owner_auth_sub).resolve()
    resolved = (storage_root / relative_path).resolve()
    if not resolved.is_relative_to(owner_root) or not resolved.is_file():
        return None
    return resolved


def _processed_elements_readable(path: Path | None) -> bool:
    """A JSON list of elements, each an object with text, holding some text in all."""

    if path is None:
        return False
    try:
        elements = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return False
    if not isinstance(elements, list) or not elements:
        return False
    if not all(isinstance(element, dict) and isinstance(element.get("text"), str) for element in elements):
        return False
    return sum(len(element["text"]) for element in elements) > 0


def _unbroken(numbers: Sequence[int]) -> bool:
    return bool(numbers) and sorted(numbers) == list(range(len(numbers)))


def _first_job_created_at(session: Session) -> datetime | None:
    return session.scalar(select(func.min(PdfProcessingJob.created_at)))


def _pending_without_job(session: Session, *, lock: bool) -> list[tuple[PDFDocument, str]]:
    """Papers that are ``pending`` with no processing job, with their owner's auth sub."""

    has_job = exists(select(PdfProcessingJob.id).where(PdfProcessingJob.document_id == PDFDocument.id))
    statement = (
        select(PDFDocument, User.auth_sub)
        .join(User, User.id == PDFDocument.user_id)
        .where(PDFDocument.status == STATUS_PENDING, ~has_job)
        .order_by(PDFDocument.id)
    )
    if lock:
        statement = statement.with_for_update(of=PDFDocument)
    return [(row[0], str(row[1])) for row in session.execute(statement).all()]


def _evaluate(
    paper: PDFDocument,
    owner_auth_sub: str,
    *,
    first_job_created_at: datetime | None,
    storage_root: Path,
    pieces: Mapping[str, list[int]],
    index: IndexReader,
) -> Candidate:
    document_id = str(paper.id)
    failed: list[str] = []
    if paper.status != STATUS_PENDING:
        failed.append("S1_status_pending")
    if (
        paper.error_message is not None
        or paper.processing_started_at is not None
        or paper.processing_completed_at is not None
    ):
        failed.append("S2_no_error_or_processing_times")
    # S3 holds by selection (no job row); it is re-checked under lock at apply time.
    if first_job_created_at is None or paper.upload_timestamp >= first_job_created_at:
        failed.append("S4_uploaded_before_first_job")
    # page_count is NOT NULL and positive by the table's own check.
    if _owned_file(storage_root, paper.file_path, owner_auth_sub) is None:
        failed.append("S5_pdf_stored")
    if not _processed_elements_readable(_owned_file(storage_root, paper.processed_json_path, owner_auth_sub)):
        failed.append("S6_processed_elements_readable")
    if _owned_file(storage_root, paper.pdfx_json_path, owner_auth_sub) is None:
        failed.append("S7_extraction_output_present")
    if not _unbroken(pieces.get(document_id, [])):
        failed.append("S8_pieces_in_search_index")
    if index.document_statuses(owner_auth_sub, document_id) != (INDEX_DONE, INDEX_DONE):
        failed.append("S9_index_document_completed")
    return Candidate(
        document_id=paper.id,
        upload_timestamp=paper.upload_timestamp,
        previous=PreviousState(
            status=paper.status,
            error_message=paper.error_message,
            processing_started_at=paper.processing_started_at,
            processing_completed_at=paper.processing_completed_at,
        ),
        failed_checks=tuple(failed),
    )


def evaluate(session: Session, *, storage_root: Path, index: IndexReader, lock: bool = False) -> tuple[
    datetime | None, tuple[Candidate, ...]
]:
    """Check every paper that is ``pending`` with no job; read-only."""

    first_job = _first_job_created_at(session)
    rows = _pending_without_job(session, lock=lock)
    pieces_by_owner: dict[str, Mapping[str, list[int]]] = {}
    candidates = []
    for paper, owner_auth_sub in rows:
        if owner_auth_sub not in pieces_by_owner:
            pieces_by_owner[owner_auth_sub] = index.piece_numbers(owner_auth_sub)
        candidates.append(
            _evaluate(
                paper,
                owner_auth_sub,
                first_job_created_at=first_job,
                storage_root=storage_root,
                pieces=pieces_by_owner[owner_auth_sub],
                index=index,
            )
        )
    return first_job, tuple(candidates)


# --- runs --------------------------------------------------------------------------


def dry_run(session_factory: Callable[[], Session], *, storage_root: Path, index: IndexReader,
            now: datetime) -> RepairReport:
    with session_factory() as session:
        first_job, candidates = evaluate(session, storage_root=storage_root, index=index)
        session.rollback()
    return RepairReport(mode="dry_run", run_at=now, first_job_created_at=first_job, candidates=candidates)


def apply(
    session_factory: Callable[[], Session],
    *,
    storage_root: Path,
    index: IndexReader,
    now: datetime,
    expect_count: int,
    expect_digest: str,
) -> RepairReport:
    """Repair every qualifying paper in one transaction, or nothing.

    The papers are locked, every check is run again, and the qualifying set must be
    exactly the one the dry run reported (``expect_count`` and ``expect_digest``).
    """

    with session_factory() as session:
        try:
            first_job, candidates = evaluate(session, storage_root=storage_root, index=index, lock=True)
            qualifying = [candidate for candidate in candidates if candidate.qualifies]
            digest = selection_digest(candidate.document_id for candidate in qualifying)
            if len(qualifying) != expect_count or digest != expect_digest:
                raise LegacyRepairError(
                    "selection_changed: the qualifying papers are not the ones the dry run reported "
                    f"(found {len(qualifying)}, expected {expect_count})"
                )
            written = 0
            for candidate in qualifying:
                result = session.execute(
                    update(PDFDocument)
                    .where(
                        PDFDocument.id == candidate.document_id,
                        PDFDocument.status == STATUS_PENDING,
                        PDFDocument.error_message.is_(None),
                        PDFDocument.processing_started_at.is_(None),
                        PDFDocument.processing_completed_at.is_(None),
                    )
                    .values(
                        status=STATUS_COMPLETED,
                        processing_started_at=candidate.upload_timestamp,
                        processing_completed_at=candidate.upload_timestamp,
                        # Keep the column's on-update default from touching it.
                        last_accessed=PDFDocument.last_accessed,
                    )
                    .execution_options(synchronize_session=False)
                )
                if result.rowcount != 1:
                    raise LegacyRepairError(f"Paper {candidate.document_id} changed during the repair")
                written += 1
            session.commit()
        except Exception:
            session.rollback()
            raise
    return RepairReport(
        mode="apply", run_at=now, first_job_created_at=first_job, candidates=candidates, written=written,
    )


def restore(
    session_factory: Callable[[], Session],
    *,
    report: Mapping[str, Any],
    expect_digest: str,
    now: datetime,
) -> RepairReport:
    """Put back the previous values an apply report recorded, in one transaction, or nothing.

    Only papers that still hold exactly what the repair wrote (``completed``, both
    times at the upload time, no error, no job) are restored; otherwise nothing is.
    """

    if report.get("mode") != "apply" or report.get("selection_digest") != expect_digest:
        raise LegacyRepairError("restore needs the apply report whose selection digest is given")
    entries = report.get("qualifying")
    if not isinstance(entries, list) or selection_digest(entry["document_id"] for entry in entries) != expect_digest:
        raise LegacyRepairError("the report's papers do not match its selection digest")
    restored: list[str] = []
    with session_factory() as session:
        try:
            for entry in entries:
                document_id = UUID(entry["document_id"])
                upload_timestamp = datetime.fromisoformat(entry["upload_timestamp"])
                previous = entry["previous"]
                has_job = exists(select(PdfProcessingJob.id).where(PdfProcessingJob.document_id == document_id))
                result = session.execute(
                    update(PDFDocument)
                    .where(
                        PDFDocument.id == document_id,
                        PDFDocument.status == STATUS_COMPLETED,
                        PDFDocument.error_message.is_(None),
                        PDFDocument.processing_started_at == upload_timestamp,
                        PDFDocument.processing_completed_at == upload_timestamp,
                        ~has_job,
                    )
                    .values(
                        status=previous["status"],
                        error_message=previous["error_message"],
                        processing_started_at=_parse(previous["processing_started_at"]),
                        processing_completed_at=_parse(previous["processing_completed_at"]),
                        last_accessed=PDFDocument.last_accessed,
                    )
                    .execution_options(synchronize_session=False)
                )
                if result.rowcount != 1:
                    raise LegacyRepairError(
                        f"Paper {document_id} no longer holds what the repair wrote; nothing restored"
                    )
                restored.append(str(document_id))
            session.commit()
        except Exception:
            session.rollback()
            raise
    return RepairReport(mode="restore", run_at=now, first_job_created_at=None, candidates=(), restored=tuple(restored))


def _parse(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value is not None else None


# --- the search index --------------------------------------------------------------


class WeaviateIndexReader:
    """Read-only access to the owner's tenant in the search index."""

    def __init__(self, client: Any) -> None:
        self._client = client

    def _collections(self, owner_auth_sub: str):
        from src.lib.weaviate_helpers import get_user_collections

        return get_user_collections(self._client, owner_auth_sub)

    def piece_numbers(self, owner_auth_sub: str) -> Mapping[str, list[int]]:
        chunk_collection, _ = self._collections(owner_auth_sub)
        numbers: dict[str, list[int]] = defaultdict(list)
        for item in chunk_collection.iterator(return_properties=["documentId", "chunkIndex"]):
            document_id = item.properties.get("documentId")
            chunk_index = item.properties.get("chunkIndex")
            if not isinstance(document_id, str) or type(chunk_index) is not int:
                raise LegacyRepairError("A stored piece has no documentId or no integer chunkIndex")
            numbers[document_id].append(chunk_index)
        return dict(numbers)

    def document_statuses(self, owner_auth_sub: str, document_id: str) -> tuple[str, str] | None:
        _, document_collection = self._collections(owner_auth_sub)
        found = document_collection.query.fetch_object_by_id(document_id)
        if found is None:
            return None
        properties = found.properties
        return (str(properties.get("processingStatus")), str(properties.get("embeddingStatus")))


# --- command -----------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="Write the repair (needs --expect-count and --expect-digest)")
    mode.add_argument("--restore-from-report", metavar="PATH", help="Put back the values an apply report recorded")
    parser.add_argument("--expect-count", type=int, help="Qualifying papers the dry run reported")
    parser.add_argument("--expect-digest", help="Selection digest the dry run (or the apply report) gave")
    return parser


def main(
    argv: list[str] | None = None,
    *,
    session_factory: Callable[[], Session] | None = None,
    storage_root: Path | None = None,
    index: IndexReader | None = None,
) -> int:
    args = _parser().parse_args(argv)
    now = datetime.now(timezone.utc)
    try:
        if args.apply and (args.expect_count is None or not args.expect_digest):
            raise LegacyRepairError("--apply needs --expect-count and --expect-digest from a dry run")
        if args.restore_from_report and not args.expect_digest:
            raise LegacyRepairError("--restore-from-report needs --expect-digest")
        if session_factory is None:
            from src.models.sql.database import SessionLocal

            session_factory = SessionLocal
        if args.restore_from_report:
            report_json = json.loads(Path(args.restore_from_report).read_text(encoding="utf-8"))
            report = restore(session_factory, report=report_json, expect_digest=args.expect_digest, now=now)
        else:
            if storage_root is None:
                from src.config import get_pdf_storage_path

                storage_root = Path(get_pdf_storage_path())
            if index is None:
                from src.lib.weaviate_client.connection import get_connection

                with get_connection().session() as client:
                    return _run(args, session_factory, storage_root, WeaviateIndexReader(client), now)
            return _run(args, session_factory, storage_root, index, now)
    except Exception as exc:  # noqa: BLE001 - reported as a content-free error with its type
        print(json.dumps({"error": type(exc).__name__, "message": str(exc)}), file=sys.stderr)
        return EXIT_ERROR
    print(json.dumps(report.to_json(), indent=2, sort_keys=True))
    return report.exit_code()


def _run(args, session_factory, storage_root: Path, index: IndexReader, now: datetime) -> int:
    if args.apply:
        report = apply(
            session_factory, storage_root=storage_root, index=index, now=now,
            expect_count=args.expect_count, expect_digest=args.expect_digest,
        )
    else:
        report = dry_run(session_factory, storage_root=storage_root, index=index, now=now)
    print(json.dumps(report.to_json(), indent=2, sort_keys=True))
    return report.exit_code()


if __name__ == "__main__":
    raise SystemExit(main())
