"""Prepare for Curation stops for a result that quotes a file removed from its paper (ALL-1460, P10b, Q2).

Every evidence record written since 0.12.0 names the paper file it quotes
(``file_id``). Before a result's envelopes become a review session, each such id
must still be a ready file of the paper. The check is structural: it collects the
ids from the stored envelope (any ``file_id`` key) and compares them with the
paper's file rows; it never reads the quoted text. Records saved before files were
recorded carry no ``file_id`` and pass, as before.

The quoted file rows are locked ``FOR SHARE`` until the preparation's transaction
ends, so a file removal (which takes the row ``FOR UPDATE``) and a preparation are
ordered: a removal that runs first leaves the preparation refused; a preparation
that runs first attaches its envelopes to the session, and the removal then finds
the session quoting the file and refuses.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.models.sql.pdf_document import FILE_STATUS_READY, PDFDocumentFile

RESULT_QUOTES_REMOVED_FILE = "result_quotes_removed_file"
# The exception's own text, for logs: routes send only the code, and a flow or batch
# shows ``curator_errors``' message for it. No model reads it. A proposal awaiting
# Chris's sign-off (ALL-1460 P10b-api).
_MESSAGE = "This result quotes a file that was removed from the paper. Run the extraction again to review it."


class ResultQuotesRemovedFile(ValueError):
    """A result's evidence names a file that is no longer a ready file of its paper."""

    code = RESULT_QUOTES_REMOVED_FILE

    def __init__(self) -> None:
        super().__init__(_MESSAGE)


def quoted_file_ids(envelope_json: Any) -> set[str]:
    """Every ``file_id`` value in a stored envelope (evidence records and their fragments)."""

    return {value for value in _file_id_values(envelope_json) if isinstance(value, str) and value}


def _file_id_values(node: Any) -> Iterator[Any]:
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "file_id":
                yield value
            else:
                yield from _file_id_values(value)
    elif isinstance(node, list):
        for item in node:
            yield from _file_id_values(item)


def require_quoted_files_ready(db: Session, *, document_id: str, envelope_jsons: Iterable[Any]) -> None:
    """Refuse with ``ResultQuotesRemovedFile`` unless every quoted file is a ready file of the paper."""

    quoted: set[str] = set()
    for envelope_json in envelope_jsons:
        quoted |= quoted_file_ids(envelope_json)
    if not quoted:
        return
    try:
        ids = {UUID(value) for value in quoted}
    except ValueError:
        raise ResultQuotesRemovedFile() from None
    rows = db.scalars(
        select(PDFDocumentFile)
        .where(PDFDocumentFile.id.in_(ids))
        .with_for_update(read=True)
    ).all()
    ready = {
        row.id for row in rows
        if str(row.document_id) == str(document_id) and row.status == FILE_STATUS_READY
    }
    if ready != ids:
        raise ResultQuotesRemovedFile()
