"""Which paper files' text the model was shown, per agent invocation (ALL-1460, Q8).

Every tool that shows the model a paper file's text goes through the run's source
binding: reads (``SourceBinding.retain``), search hits and verified evidence quotes
(``SourceBinding.record_files_shown``, no spans issued). When pieces of a file the
invocation had not been shown before are shown, the binding writes one history
row of kind ``FILES_READ_KIND`` naming the new files, so a run of hundreds of reads
writes one row per file at most. An extraction result's ``files_read`` is the set
of those files for its producing invocations (the extraction agent's, plus its
fan-out writers'), or of its whole run when they are not known, looked up when
the result is saved. Structural only: the files of
the pieces shown, never anything read from the text.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.models.sql.agent_run_history import AgentHistoryEvent, AgentHistoryPayload
from src.models.sql.pdf_document import FILE_STATUS_READY, PDFDocumentFile

FILES_READ_KIND = "document_files_read"


class FilesReadIntegrityError(RuntimeError):
    """A stored files-read history row does not match its payload hash."""


def _rows(db: Session, *, owner_id: str, run_id: str, invocation_ids: list[str] | None) -> list[dict[str, Any]]:
    condition = (
        (AgentHistoryEvent.owner_id == owner_id)
        & (AgentHistoryEvent.run_id == run_id)
        & (AgentHistoryEvent.kind == FILES_READ_KIND)
    )
    if invocation_ids is not None:
        condition &= AgentHistoryEvent.invocation_id.in_(invocation_ids)
    query = (
        select(AgentHistoryEvent, AgentHistoryPayload.content)
        .join(AgentHistoryPayload)
        .where(condition)
        .order_by(AgentHistoryEvent.sequence)
    )
    payloads = []
    for event, content in db.execute(query).all():
        if hashlib.sha256(content.encode("utf-8")).hexdigest() != event.payload_sha256:
            raise FilesReadIntegrityError("Files-read history integrity check failed")
        payloads.append(json.loads(content))
    return payloads


def files_read_for_result(
    db: Session,
    *,
    owner_id: str,
    run_id: str,
    document_id: str,
    invocation_ids: list[str] | None,
) -> list[dict[str, Any]]:
    """The files read for one extraction result, in the paper's tab order.

    ``invocation_ids``: the producing agent invocations; None means the whole run.

    Each entry: ``file_id``, ``file_name`` (as shown when read), ``role``,
    ``converted_artifact_id`` and ``converted_artifact_sha256`` from the file row.
    A file removed (or being removed) before the result was saved keeps the name
    captured when it was read, with no role or artifact.
    """

    names: dict[str, str] = {}
    for payload in _rows(db, owner_id=owner_id, run_id=run_id, invocation_ids=invocation_ids):
        if payload.get("document_id") != document_id:
            continue
        for entry in payload["files"]:
            names.setdefault(entry["file_id"], entry["file_name"])
    if not names:
        return []
    rows = _file_rows(db, names, document_id=document_id)
    files_read = []
    for file_id in _tab_order(names, rows):
        row = rows.get(file_id)
        files_read.append(
            {
                "file_id": file_id,
                "file_name": names[file_id],
                "role": row.role if row is not None else None,
                "converted_artifact_id": row.source_provider_converted_artifact_id if row is not None else None,
                "converted_artifact_sha256": (
                    row.source_converted_artifact_sha256 if row is not None else None
                ),
            }
        )
    return files_read


def _file_rows(db: Session, file_ids, *, document_id: str) -> dict[str, PDFDocumentFile]:
    """The paper's current files among these ids: ``ready`` rows only.

    A row in any other state (a file being removed, a failed leftover) reads exactly
    like a file that is gone: its entry keeps the name read, with no role or artifact.
    """

    return {
        str(row.id): row
        for row in db.scalars(
            select(PDFDocumentFile).where(
                PDFDocumentFile.id.in_([UUID(file_id) for file_id in file_ids]),
                PDFDocumentFile.document_id == UUID(document_id),
                PDFDocumentFile.status == FILE_STATUS_READY,
            )
        )
    }


def _tab_order(file_ids, rows: dict[str, PDFDocumentFile]) -> list[str]:
    """Files in the paper's tab order; removed files after them, in first-seen order."""

    return sorted(
        file_ids,
        key=lambda file_id: (file_id not in rows, rows[file_id].tab_order if file_id in rows else 0),
    )


def union_files_read(
    db: Session, *, document_id: str, inputs: list[list[dict[str, Any]] | None],
) -> list[dict[str, Any]] | None:
    """The files read of a result derived from saved results: the union of theirs.

    De-duplicated by file id (the first stored entry is kept as it was saved), in
    tab order. None if any input has none recorded: a partial list would look
    complete.
    """

    if any(entries is None for entries in inputs):
        return None
    entries_by_id: dict[str, dict[str, Any]] = {}
    for entries in inputs:
        for entry in entries:
            entries_by_id.setdefault(entry["file_id"], entry)
    if not entries_by_id:
        return []
    rows = _file_rows(db, entries_by_id, document_id=document_id)
    return [dict(entries_by_id[file_id]) for file_id in _tab_order(entries_by_id, rows)]


__all__ = [
    "FILES_READ_KIND",
    "FilesReadIntegrityError",
    "files_read_for_result",
    "union_files_read",
]
