"""Which paper files' text the model was shown, per agent invocation (ALL-1460, Q8).

Every tool that shows the model a paper file's text goes through the run's source
binding: reads (``SourceBinding.retain``), search hits and verified evidence quotes
(``SourceBinding.record_files_shown``, no spans issued). When pieces of a file the
invocation had not been shown before are shown, the binding writes one history
row of kind ``FILES_READ_KIND`` naming the new files, so a run of hundreds of reads
writes one row per file at most. An extraction result's ``files_read`` is the set
of those files for its producing invocations (the extraction agent's, plus its
fan-out writers'), or of its whole run when they are not known, looked up when
the result is saved. Each file is recorded as it was when read (name, role,
converted artifact and its sha256), and a result marks the files that are no
longer ready files of the paper when it is saved as ``removed``. Structural only:
the files of the pieces shown, never anything read from the text.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.models.sql.agent_run_history import AgentHistoryEvent, AgentHistoryPayload
from src.models.sql.pdf_document import FILE_ROLES, FILE_STATUS_READY, PDFDocumentFile

FILES_READ_KIND = "document_files_read"


class FilesReadIntegrityError(RuntimeError):
    """A stored files-read history row does not match its payload hash or required shape."""


def _decode_payload(content: str) -> dict[str, Any]:
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise FilesReadIntegrityError("Files-read history payload is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise FilesReadIntegrityError("Files-read history payload must be a JSON object")
    return payload


def _validated_payload(payload: Any) -> tuple[str, list[dict[str, Any]]]:
    if not isinstance(payload, dict):
        raise FilesReadIntegrityError("Files-read history payload must be a JSON object")
    payload_document_id = payload.get("document_id")
    if not isinstance(payload_document_id, str) or not payload_document_id.strip():
        raise FilesReadIntegrityError("Files-read history payload must name a document_id")
    files = payload.get("files")
    if not isinstance(files, list) or any(not isinstance(entry, dict) for entry in files):
        raise FilesReadIntegrityError("Files-read history payload must hold a list of file entries")
    return payload_document_id, files


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
    payloads: list[dict[str, Any]] = []
    for event, content in db.execute(query).all():
        if hashlib.sha256(content.encode("utf-8")).hexdigest() != event.payload_sha256:
            raise FilesReadIntegrityError("Files-read history integrity check failed")
        payloads.append(_decode_payload(content))
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

    Each entry: ``file_id``, ``file_name``, ``role``, ``converted_artifact_id`` and
    ``converted_artifact_sha256`` as they were when the file was read, and
    ``removed``: the file is no longer a ready file of the paper. A file read by
    several invocations keeps its first entry; they are the same (see
    ``SourceBinding._record_files_read``).
    """

    read: dict[str, dict[str, Any]] = {}
    for payload in _rows(db, owner_id=owner_id, run_id=run_id, invocation_ids=invocation_ids):
        payload_document_id, files = _validated_payload(payload)
        if payload_document_id != document_id:
            continue
        for entry in files:
            normalized = _as_read(entry)
            read.setdefault(normalized["file_id"], normalized)
    return _with_removed(db, read, document_id=document_id)


_READ_TIME_KEYS = ("role", "converted_artifact_id", "converted_artifact_sha256")


def _as_read(entry: Any) -> dict[str, Any]:
    """A history entry as a result's entry.

    A row written before read-time values were recorded (a run in flight across the
    0.12.0 deploy) names the file only: its role and converted text are unknown and
    stay null; nothing is looked up from the file as it is now.
    """

    if not isinstance(entry, dict):
        raise FilesReadIntegrityError("A files-read entry must be a JSON object")
    file_id = entry.get("file_id")
    if not isinstance(file_id, str) or not file_id.strip():
        raise FilesReadIntegrityError("A files-read entry must have a non-empty file_id")
    file_name = entry.get("file_name")
    if not isinstance(file_name, str) or not file_name.strip():
        raise FilesReadIntegrityError("A files-read entry must have a non-empty file_name")

    recorded = [key for key in _READ_TIME_KEYS if key in entry]
    if recorded and len(recorded) != len(_READ_TIME_KEYS):
        raise FilesReadIntegrityError("A files-read entry has only some of its read-time values")
    if not recorded:
        return {
            "file_id": file_id,
            "file_name": file_name,
            "role": None,
            "converted_artifact_id": None,
            "converted_artifact_sha256": None,
        }

    role = entry["role"]
    if not isinstance(role, str) or role not in FILE_ROLES:
        raise FilesReadIntegrityError("A files-read entry has an invalid role")
    converted_artifact_id = entry["converted_artifact_id"]
    if converted_artifact_id is not None and not isinstance(converted_artifact_id, str):
        raise FilesReadIntegrityError("A files-read entry has a non-string converted_artifact_id")
    converted_artifact_sha256 = entry["converted_artifact_sha256"]
    if converted_artifact_sha256 is not None and not isinstance(converted_artifact_sha256, str):
        raise FilesReadIntegrityError("A files-read entry has a non-string converted_artifact_sha256")
    return {
        "file_id": file_id,
        "file_name": file_name,
        "role": role,
        "converted_artifact_id": converted_artifact_id,
        "converted_artifact_sha256": converted_artifact_sha256,
    }


def _with_removed(db: Session, read: dict[str, dict[str, Any]], *, document_id: str) -> list[dict[str, Any]]:
    """The entries in tab order, each marked ``removed`` as the paper's files are now."""

    if not read:
        return []
    rows = _file_rows(db, read, document_id=document_id)
    return [{**read[file_id], "removed": file_id not in rows} for file_id in _tab_order(read, rows)]


def _file_rows(db: Session, file_ids, *, document_id: str) -> dict[str, PDFDocumentFile]:
    """The paper's current files among these ids: ``ready`` rows only.

    A row in any other state (a file being removed, a failed leftover) counts exactly
    like a file that is gone: its entry is marked ``removed``.
    """

    try:
        parsed_ids = [UUID(file_id) for file_id in file_ids]
    except (TypeError, ValueError) as exc:
        raise FilesReadIntegrityError("A files-read entry has an invalid file_id") from exc
    return {
        str(row.id): row
        for row in db.scalars(
            select(PDFDocumentFile).where(
                PDFDocumentFile.id.in_(parsed_ids),
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

    De-duplicated by file id (the first stored entry is kept with the values it was
    saved with), in tab order, and marked ``removed`` again as the paper's files are
    now. None if any input has none recorded: a partial list would look complete.
    """

    read: dict[str, dict[str, Any]] = {}
    for entries in inputs:
        if entries is None:
            return None
        for entry in entries:
            read.setdefault(entry["file_id"], {key: value for key, value in entry.items() if key != "removed"})
    return _with_removed(db, read, document_id=document_id)


__all__ = [
    "FILES_READ_KIND",
    "FilesReadIntegrityError",
    "files_read_for_result",
    "union_files_read",
]
