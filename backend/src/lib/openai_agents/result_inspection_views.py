"""Resolve exact validation views from existing durable envelope checkpoints."""
from copy import deepcopy
from typing import Any, Mapping, Sequence
from uuid import UUID

from sqlalchemy import select

from src.lib.curation_workspace.models import DomainEnvelopeModel
from src.models.sql.database import SessionLocal
from src.models.sql.pdf_document import PDFDocument
from src.models.sql.user import User


class InspectionViewUnavailable(ValueError):
    """A saved checkpoint cannot be read exactly by this caller."""


def _attr(record: Any, key: str) -> Any:
    return record.get(key) if isinstance(record, Mapping) else getattr(record, key, None)


def readable_document_ids(records: Sequence[Any], *, user_id: str) -> set[str]:
    ids = {UUID(str(_attr(record, "document_id"))) for record in records}
    if not ids:
        return set()
    with SessionLocal() as db:
        return {str(value) for value in db.scalars(
            select(PDFDocument.id).join(User, PDFDocument.user_id == User.id).where(
                User.auth_sub == user_id, PDFDocument.id.in_(ids),
                PDFDocument.viewer_mode.is_distinct_from("benchmark_frozen"),
            )
        )}


def validation_view(record: Any, *, revision: int | None, user_id: str) -> dict[str, Any]:
    metadata = _attr(record, "metadata") or {}
    checkpoint = metadata.get("inspection_checkpoint")
    if not isinstance(checkpoint, Mapping):
        raise InspectionViewUnavailable("No post-validation checkpoint was recorded for this extraction.")
    pinned = checkpoint["revision"]
    if revision is not None and revision != pinned:
        raise InspectionViewUnavailable("The requested revision does not match the saved completed-step checkpoint.")
    if str(_attr(record, "document_id")) not in readable_document_ids([record], user_id=user_id):
        raise InspectionViewUnavailable("This result is no longer accessible.")
    with SessionLocal() as db:
        row = db.get(DomainEnvelopeModel, checkpoint["envelope_id"])
        if (row is None or row.revision != pinned
                or row.source_extraction_result_id != str(_attr(record, "extraction_result_id"))
                or str(row.document_id) != str(_attr(record, "document_id"))):
            raise InspectionViewUnavailable("The exact post-validation checkpoint is unavailable or superseded.")
        payload = deepcopy(row.envelope_json)
    if isinstance(record, Mapping):
        result = dict(record)
    else:
        result = record.model_dump(mode="json")
    result.update(payload_json=payload, inspection_view="post_validation", inspection_revision=pinned)
    return result
