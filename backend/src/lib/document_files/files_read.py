"""Which paper files' text the model was shown, per agent invocation (ALL-1460, Q8).

Every document read that shows pieces to the model goes through the run's source
binding (``document_references.SourceBinding.retain``). When a read shows pieces of
a file the invocation had not been shown before, the binding writes one history
row of kind ``FILES_READ_KIND`` naming the new files, so a run of hundreds of reads
writes one row per file at most. An extraction result's ``files_read`` is the set
of those files for its producing invocation (or its whole run when the invocation
is not known), looked up when the result is saved. Structural only: the files of
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
from src.models.sql.pdf_document import PDFDocumentFile

FILES_READ_KIND = "document_files_read"


class FilesReadIntegrityError(RuntimeError):
    """A stored files-read history row does not match its payload hash."""


def _rows(db: Session, *, owner_id: str, run_id: str, invocation_id: str | None) -> list[dict[str, Any]]:
    condition = (
        (AgentHistoryEvent.owner_id == owner_id)
        & (AgentHistoryEvent.run_id == run_id)
        & (AgentHistoryEvent.kind == FILES_READ_KIND)
    )
    if invocation_id is not None:
        condition &= AgentHistoryEvent.invocation_id == invocation_id
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


def files_already_read(
    db: Session, *, owner_id: str, run_id: str, invocation_id: str, document_id: str,
) -> set[str]:
    """The file ids this invocation has already been shown in this document."""

    return {
        entry["file_id"]
        for payload in _rows(db, owner_id=owner_id, run_id=run_id, invocation_id=invocation_id)
        if payload.get("document_id") == document_id
        for entry in payload["files"]
    }


def files_read_for_result(
    db: Session,
    *,
    owner_id: str,
    run_id: str,
    document_id: str,
    invocation_id: str | None,
) -> list[dict[str, Any]]:
    """The files read for one extraction result, in the paper's tab order.

    Each entry: ``file_id``, ``file_name`` (as shown when read), ``role``,
    ``converted_artifact_id`` and ``converted_artifact_sha256`` from the file row.
    A file removed before the result was saved keeps the name captured when it was
    read, with no role or artifact.
    """

    names: dict[str, str] = {}
    for payload in _rows(db, owner_id=owner_id, run_id=run_id, invocation_id=invocation_id):
        if payload.get("document_id") != document_id:
            continue
        for entry in payload["files"]:
            names.setdefault(entry["file_id"], entry["file_name"])
    if not names:
        return []
    rows = {
        str(row.id): row
        for row in db.scalars(
            select(PDFDocumentFile).where(PDFDocumentFile.id.in_([UUID(file_id) for file_id in names]))
        )
    }
    ordered = sorted(
        names,
        key=lambda file_id: (file_id not in rows, rows[file_id].tab_order if file_id in rows else 0),
    )
    files_read = []
    for file_id in ordered:
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


__all__ = [
    "FILES_READ_KIND",
    "FilesReadIntegrityError",
    "files_already_read",
    "files_read_for_result",
]
