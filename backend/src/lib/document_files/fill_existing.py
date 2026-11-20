"""One-time fill step: measure the main file of every paper saved before the files table.

Run once in the release maintenance window, after the ``c6d7e8f9a0b1``
migration and before traffic reopens:

    docker compose run --rm --no-deps backend python -m src.lib.document_files.fill_existing

What it reads:
- PostgreSQL: every paper (id, owner, status, ``next_chunk_index``) and its
  main file row.
- Disk: each main file's processed-elements JSON (``processed_json_path``
  under ``PDF_STORAGE_PATH``). It adds up the UTF-8 bytes of the text of the
  elements that are cut into pieces (``element_text_bytes``).
- The search index: the ``documentId`` and ``chunkIndex`` of every stored
  piece in each owner's tenant.

What it writes (PostgreSQL only, one transaction):
- on each main file: ``chunk_index_start``, ``chunk_index_end``, ``chunk_count``
  and ``text_bytes``;
- on each paper: ``next_chunk_index``.

It writes nothing to the search index or disk, and touches no evidence,
session, flow result or piece. It stops, writing nothing, if any paper's piece
numbers are not unbroken from 0, if a ready file would have no range or text
size, if a paper already has more than one file, or if a stored value differs
from what it measures. Running it again gives the same result. After writing,
it re-reads PostgreSQL and the search index and checks that every range
matches. It prints counts and internal row ids only.

``--verify`` runs only the final check, read-only.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import func, select

from src.lib.document_files.piece_numbers import PaperOwner, PieceNumbers, scan_papers
from src.lib.document_files.service import element_text_bytes
from src.models.sql.pdf_document import (
    FILE_ROLE_MAIN,
    FILE_STATUS_READY,
    PDFDocument,
    PDFDocumentFile,
)
from src.models.sql.user import User


class FillStepError(RuntimeError):
    """The fill step found a paper it cannot measure; nothing was written."""

    def __init__(self, reason: str, document_ids: list[str]) -> None:
        shown = ", ".join(sorted(document_ids)[:50])
        more = len(document_ids) - 50
        suffix = f" (and {more} more)" if more > 0 else ""
        super().__init__(f"{reason}: {len(document_ids)} papers: {shown}{suffix}")
        self.reason = reason
        self.document_ids = sorted(document_ids)


@dataclass(frozen=True, slots=True)
class MainFileMeasurement:
    document_id: str
    file_id: UUID
    chunk_count: int
    chunk_index_start: int | None
    chunk_index_end: int | None
    text_bytes: int | None


def _require(condition_ids: list[str], reason: str) -> None:
    if condition_ids:
        raise FillStepError(reason, condition_ids)


def _paper_rows(session) -> list[tuple[PDFDocument, PDFDocumentFile | None, str]]:
    rows = session.execute(
        select(PDFDocument, PDFDocumentFile, User.auth_sub)
        .join(User, User.id == PDFDocument.user_id)
        .outerjoin(
            PDFDocumentFile,
            (PDFDocumentFile.document_id == PDFDocument.id)
            & (PDFDocumentFile.role == FILE_ROLE_MAIN),
        )
        .order_by(PDFDocument.id)
    ).all()
    return [(paper, main_file, auth_sub) for paper, main_file, auth_sub in rows]


def _file_counts(session) -> dict[str, int]:
    return {
        str(document_id): int(count)
        for document_id, count in session.execute(
            select(PDFDocumentFile.document_id, func.count()).group_by(PDFDocumentFile.document_id)
        )
    }


def _measure_text(storage_root: Path, relative_path: str | None) -> int | None:
    """Text bytes from the processed JSON, or None when the file holds no JSON on disk."""

    if not relative_path:
        return None
    path = storage_root / relative_path
    if not path.is_file():
        return None
    elements = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(elements, list):
        raise ValueError("Processed JSON is not a list of elements")
    return element_text_bytes(elements)


def measure(session, client: Any, storage_root: Path) -> tuple[list[MainFileMeasurement], dict[str, Any]]:
    """Measure every paper's main file; raise ``FillStepError`` before anything is written."""

    rows = _paper_rows(session)
    _require([str(paper.id) for paper, main_file, _ in rows if main_file is None], "papers without a main file")
    file_counts = _file_counts(session)
    _require(
        [str(paper.id) for paper, _, _ in rows if file_counts.get(str(paper.id), 0) != 1],
        "papers with more than one file (the fill step applies only to papers saved before files existed)",
    )
    owners = [PaperOwner(str(paper.id), auth_sub, paper.status) for paper, _, auth_sub in rows]
    numbers, scan_counts = scan_papers(client, owners)
    _require(
        [document_id for document_id, value in numbers.items() if not value.unbroken],
        "papers whose piece numbers are not unbroken from 0",
    )

    measurements: list[MainFileMeasurement] = []
    unreadable_json: list[str] = []
    for paper, main_file, _ in rows:
        value: PieceNumbers = numbers[str(paper.id)]
        try:
            text_bytes = _measure_text(storage_root, main_file.processed_json_path)
        except (ValueError, UnicodeDecodeError):
            unreadable_json.append(str(paper.id))
            continue
        measurements.append(MainFileMeasurement(
            document_id=str(paper.id),
            file_id=main_file.id,
            chunk_count=value.count,
            chunk_index_start=0 if value.count else None,
            chunk_index_end=value.count - 1 if value.count else None,
            text_bytes=text_bytes,
        ))
    _require(unreadable_json, "papers whose processed JSON cannot be read as elements")

    status_by_file = {main_file.id: main_file.status for _, main_file, _ in rows}
    ready = [item for item in measurements if status_by_file[item.file_id] == FILE_STATUS_READY]
    _require([item.document_id for item in ready if item.chunk_count == 0], "ready files without stored pieces")
    _require([item.document_id for item in ready if item.text_bytes is None], "ready files without processed JSON")
    return measurements, scan_counts


def _differs(stored: Any, measured: Any) -> bool:
    return stored is not None and stored != measured


def apply(session, measurements: list[MainFileMeasurement]) -> dict[str, int]:
    """Write the measurements in the caller's transaction; stop if a stored value differs."""

    papers = {
        str(paper.id): paper
        for paper in session.scalars(
            select(PDFDocument).where(
                PDFDocument.id.in_([UUID(item.document_id) for item in measurements])
            ).with_for_update()
        )
    }
    files = {
        file_row.id: file_row
        for file_row in session.scalars(
            select(PDFDocumentFile).where(
                PDFDocumentFile.id.in_([item.file_id for item in measurements])
            ).with_for_update()
        )
    }
    conflicts: list[str] = []
    written = unchanged = 0
    for item in measurements:
        file_row = files[item.file_id]
        paper = papers[item.document_id]
        if (
            _differs(file_row.chunk_index_start, item.chunk_index_start)
            or _differs(file_row.chunk_index_end, item.chunk_index_end)
            or _differs(file_row.chunk_count, item.chunk_count)
            or _differs(file_row.text_bytes, item.text_bytes)
            or _differs(paper.next_chunk_index, item.chunk_count)
        ):
            conflicts.append(item.document_id)
            continue
        if (
            file_row.chunk_count == item.chunk_count
            and file_row.chunk_index_start == item.chunk_index_start
            and file_row.text_bytes == item.text_bytes
            and paper.next_chunk_index == item.chunk_count
        ):
            unchanged += 1
            continue
        file_row.chunk_index_start = item.chunk_index_start
        file_row.chunk_index_end = item.chunk_index_end
        file_row.chunk_count = item.chunk_count
        file_row.text_bytes = item.text_bytes
        paper.next_chunk_index = item.chunk_count
        written += 1
    _require(conflicts, "papers whose stored measurements differ from the measured values")
    return {"papers_written": written, "papers_already_filled": unchanged}


def verify(session, client: Any) -> dict[str, Any]:
    """Read-only: every ready file has a range, count and size that match the search index."""

    rows = _paper_rows(session)
    owners = [PaperOwner(str(paper.id), auth_sub, paper.status) for paper, _, auth_sub in rows]
    numbers, _ = scan_papers(client, owners)
    file_rows = session.scalars(select(PDFDocumentFile)).all()
    by_paper: dict[str, list[PDFDocumentFile]] = {}
    for file_row in file_rows:
        by_paper.setdefault(str(file_row.document_id), []).append(file_row)

    missing: list[str] = []
    mismatched: list[str] = []
    counter_wrong: list[str] = []
    for paper, _, _ in rows:
        document_id = str(paper.id)
        stored = numbers[document_id]
        ranged = 0
        highest = -1
        for file_row in by_paper.get(document_id, []):
            has_range = file_row.chunk_index_start is not None
            if file_row.status == FILE_STATUS_READY and (
                not has_range or file_row.chunk_count is None or file_row.text_bytes is None
            ):
                missing.append(document_id)
            if has_range:
                ranged += file_row.chunk_count
                highest = max(highest, file_row.chunk_index_end)
        if ranged != stored.count or (stored.count and highest != stored.highest):
            mismatched.append(document_id)
        if paper.next_chunk_index is None or paper.next_chunk_index < highest + 1:
            counter_wrong.append(document_id)
    return {
        "papers": len(rows),
        "files": len(file_rows),
        "ready_files_missing_range_or_size": sorted(set(missing)),
        "papers_whose_ranges_differ_from_index": sorted(mismatched),
        "papers_with_wrong_next_piece_number": sorted(counter_wrong),
    }


def _verify_ok(report: dict[str, Any]) -> bool:
    return not (
        report["ready_files_missing_range_or_size"]
        or report["papers_whose_ranges_differ_from_index"]
        or report["papers_with_wrong_next_piece_number"]
    )


def run(*, verify_only: bool) -> dict[str, Any]:
    from src.config import get_pdf_storage_path
    from src.lib.weaviate_client.connection import get_connection
    from src.models.sql.database import SessionLocal

    storage_root = Path(get_pdf_storage_path())
    report: dict[str, Any] = {}
    with get_connection().session() as client:
        if not verify_only:
            with SessionLocal() as session, session.begin():
                measurements, scan_counts = measure(session, client, storage_root)
                report.update(scan_counts)
                report.update(apply(session, measurements))
                report["main_files_measured"] = len(measurements)
                report["main_files_without_processed_json"] = sum(
                    1 for item in measurements if item.text_bytes is None
                )
        with SessionLocal() as session:
            report["verify"] = verify(session, client)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Measure existing papers' main files (one-time fill step).")
    parser.add_argument("--verify", action="store_true", help="Only check stored ranges against the index; read-only.")
    args = parser.parse_args(argv)
    try:
        report = run(verify_only=args.verify)
    except FillStepError as exc:
        print(json.dumps({"error": exc.reason, "document_ids": exc.document_ids}, indent=2))
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if _verify_ok(report["verify"]) else 1


if __name__ == "__main__":
    sys.exit(main())
