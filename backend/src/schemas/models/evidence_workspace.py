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

from typing import Any, Callable, Iterable, Mapping

from pydantic import ValidationError

from src.schemas.models.base import EvidenceRecord

__all__ = [
    "WORKSPACE_ONLY_FIELDS",
    "EvidenceIntegrityError",
    "canonical_evidence_payload",
    "is_discarded",
    "normalize_workspace_records",
    "project_to_provenance",
    "strip_workspace_fields",
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


def project_to_provenance(record: Any, *, strict: bool = True) -> EvidenceRecord:
    """Convert one internal workspace record into canonical provenance.

    With ``strict`` (the default, used at the canonical envelope boundary) a
    field that is neither canonical provenance nor known workspace state raises
    ``EvidenceIntegrityError``, because an unexpected field is a writer defect
    and must not be swallowed.

    With ``strict=False`` an unexpected field is filtered out instead, which is
    what the domain-pack materializers did with their own ``allowed_fields``
    copies. Their callers already drop a record they cannot validate, so raising
    there would turn one bad record into a failed extraction.
    """
    if not isinstance(record, Mapping):
        raise EvidenceIntegrityError(
            f"Evidence record must be a mapping, got {type(record).__name__}"
        )

    if strict:
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


def strip_workspace_fields(record: Mapping[str, Any]) -> dict[str, Any]:
    """Remove workspace state from an existing record, preserving everything else.

    This is the surgical counterpart to ``project_to_provenance``. Use it when
    *updating* a record that already lives in an envelope, rather than building
    a canonical record from a workspace one.

    The difference matters. ``project_to_provenance`` whitelists to declared
    fields, which is right for a materializer constructing canonical evidence,
    but at the validator write-back it would silently delete any other key the
    envelope legitimately carries. Domain-pack payload evidence is not a closed
    namespace, so this removes exactly the seven known workspace keys, merges
    the span alias, and leaves every other key untouched.
    """
    stripped = {
        key: value for key, value in record.items()
        if key not in WORKSPACE_ONLY_FIELDS
    }
    if _SPAN_CANONICAL not in stripped:
        alias = record.get(_SPAN_ALIAS)
        if alias is not None:
            stripped[_SPAN_CANONICAL] = alias
    return stripped


def normalize_workspace_records(
    records: Iterable[Any],
    *,
    admit: Callable[[EvidenceRecord], bool] | None = None,
    on_drop: Callable[[str, Exception | None], None] | None = None,
    strict: bool = False,
) -> list[dict[str, Any]]:
    """Select, project, de-duplicate and serialize a workspace collection.

    This replaces the seven identical ``allowed_fields = set(EvidenceRecord.model_fields)``
    loops that each domain pack carried. The loop order is preserved exactly,
    because the packs depend on it:

    1. Skip anything that is not a mapping.
    2. Skip discarded records, reading lifecycle state *before* projecting.
       Canonical provenance has no lifecycle field to select on afterwards.
    3. Require a non-empty ``evidence_record_id`` that has not been kept yet.
    4. Project to canonical provenance; drop the record if that fails.
    5. Apply the caller's ``admit`` predicate.
    6. Only then mark the ID as seen, so a rejected record does not consume it.

    ``admit`` carries per-pack policy: GO requires a verified quote
    (go/conversion.py:610). ``on_drop`` carries per-pack reporting:
    gene_expression logs a warning (gene_expression/conversion.py:913). Chat has
    a stricter policy still and supplies its own predicate.
    """
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()

    for record in records:
        if not isinstance(record, Mapping):
            continue
        if is_discarded(record):
            continue

        evidence_id = str(record.get("evidence_record_id") or "").strip()
        if not evidence_id or evidence_id in seen:
            continue

        try:
            projected = project_to_provenance(record, strict=strict)
        except EvidenceIntegrityError as exc:
            if strict:
                raise
            if on_drop is not None:
                on_drop(evidence_id, exc)
            continue

        if admit is not None and not admit(projected):
            continue

        seen.add(evidence_id)
        normalized.append(projected.model_dump(mode="json", exclude_none=True))

    return normalized
