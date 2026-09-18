"""The single boundary between internal evidence workspace state and canonical provenance.

KANBAN-1773. Evidence tools keep lifecycle state (`status`, `updated_at`, discard
metadata) and a legacy span alias (`span_ids`) alongside provenance on one record.
`EvidenceRecord` forbids all of those, so every writer that puts evidence into a
canonical envelope has to project first.

Before this module, each writer kept its own `allowed_fields` copy and the
validator write-back had none at all, which is how workspace keys reached the
strict envelope and were reported to curators as their own Output Structure
being wrong.

Admission policy stays with the caller. The GO pack requires a verified quote,
gene_expression logs a warning before dropping, chat requires five fields and a
`verified` tool status. This module owns only the projection and the
serialization policy, so callers keep their own rules.
"""

from __future__ import annotations

from typing import Any, Mapping

from pydantic import ValidationError

from src.schemas.models.base import EvidenceRecord

__all__ = [
    "WORKSPACE_ONLY_FIELDS",
    "EvidenceIntegrityError",
    "canonical_evidence_payload",
    "is_discarded",
    "project_to_provenance",
    "try_project_to_provenance",
]


#: Keys the evidence tools write on a workspace record that canonical provenance forbids.
#:
#: ``status``/``workspace_status`` are lifecycle state (record_evidence.py:1144,
#: evidence_workspace.py:722-723). ``span_ids`` duplicates ``source_span_ids``
#: (record_evidence.py:1146-1147). ``created_at`` is preserved across re-records
#: (record_evidence.py:69-72); ``updated_at`` is stamped on every mutation;
#: ``discarded_at`` and ``discard_reason`` are written on discard
#: (evidence_workspace.py:723-725).
WORKSPACE_ONLY_FIELDS: frozenset[str] = frozenset({
    "status",
    "workspace_status",
    "span_ids",
    "created_at",
    "updated_at",
    "discarded_at",
    "discard_reason",
})

_CANONICAL_FIELDS: frozenset[str] = frozenset(EvidenceRecord.model_fields)

#: The legacy alias and the canonical list it mirrors.
_SPAN_ALIAS = "span_ids"
_SPAN_CANONICAL = "source_span_ids"


class EvidenceIntegrityError(Exception):
    """An internal evidence record does not match its own contract.

    Deliberately not a subclass of ``ValueError`` or ``ValidationError``. Broad
    handlers on the extraction path catch both of those and re-report them as a
    curator Output Structure violation, which is the mislabeling this ticket
    exists to remove.
    """

    def __init__(self, message: str, *, unknown_fields: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.unknown_fields = unknown_fields


def is_discarded(record: Any) -> bool:
    """Report workspace lifecycle state.

    Callers select on this *before* projecting, because the canonical record has
    no lifecycle field to select on afterwards.
    """
    if not isinstance(record, Mapping):
        return False
    state = record.get("workspace_status") or record.get("status") or ""
    return str(state).strip() == "discarded"


def project_to_provenance(record: Any) -> EvidenceRecord:
    """Convert one internal workspace record into canonical provenance.

    Raises ``EvidenceIntegrityError`` when the record carries a field that is
    neither canonical provenance nor known workspace state, or when the
    projected payload fails ``EvidenceRecord``. An unexpected field is a defect
    in a writer, so it surfaces instead of being dropped.
    """
    if not isinstance(record, Mapping):
        raise EvidenceIntegrityError(
            f"Evidence record must be a mapping, got {type(record).__name__}"
        )

    unknown = tuple(sorted(
        str(key) for key in record
        if key not in _CANONICAL_FIELDS and key not in WORKSPACE_ONLY_FIELDS
    ))
    if unknown:
        raise EvidenceIntegrityError(
            "Evidence record carries unknown field(s): " + ", ".join(unknown),
            unknown_fields=unknown,
        )

    payload: dict[str, Any] = {
        key: value
        for key, value in record.items()
        if key in _CANONICAL_FIELDS and value is not None
    }

    # The tools write the same values under both names. Keep the canonical list
    # authoritative and adopt the alias only when the canonical one is absent,
    # so a projection never silently replaces a real span selection.
    if _SPAN_CANONICAL not in payload:
        alias = record.get(_SPAN_ALIAS)
        if alias is not None:
            payload[_SPAN_CANONICAL] = alias

    try:
        return EvidenceRecord.model_validate(payload)
    except ValidationError as exc:
        raise EvidenceIntegrityError(
            f"Evidence record failed canonical validation: {exc}"
        ) from exc


def try_project_to_provenance(record: Any) -> EvidenceRecord | None:
    """Project, or return ``None`` when the record cannot be made canonical.

    For callers whose existing policy is to drop a malformed record and carry
    on. Callers that must not lose evidence use ``project_to_provenance``.
    """
    try:
        return project_to_provenance(record)
    except EvidenceIntegrityError:
        return None


def canonical_evidence_payload(record: Any) -> dict[str, Any]:
    """Project and serialize with the one canonical policy.

    ``mode="json"`` and ``exclude_none=True`` match what the pack materializers
    already emit, so envelope payloads keep their current shape.
    """
    return project_to_provenance(record).model_dump(mode="json", exclude_none=True)
