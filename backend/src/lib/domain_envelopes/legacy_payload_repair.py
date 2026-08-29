"""Repair legacy persisted domain-envelope payload keys.

This module intentionally updates only ``domain_envelopes.envelope_json``. It
does not checkpoint envelopes or rebuild their materialized child tables.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Mapping

from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.orm import Session

from src.schemas.domain_envelope import DomainEnvelope

LEGACY_OBJECTS_KEY = "objects"
CURRENT_OBJECTS_KEY = "extracted_objects"


class LegacyDomainEnvelopeRepairError(RuntimeError):
    """Raised when the legacy repair cannot proceed without ambiguity."""


@dataclass(frozen=True)
class LegacyDomainEnvelopeRepairExpectations:
    """Operator-confirmed counts required before applying the repair."""

    envelopes: int
    candidate_references: int
    sessions: int
    objects: int


@dataclass
class LegacyDomainEnvelopeRepairSummary:
    """Content-free audit summary for a dry-run or apply operation."""

    applied: bool
    envelope_count: int = 0
    candidate_reference_count: int = 0
    session_count: int = 0
    object_count: int = 0
    repaired_envelope_count: int = 0
    envelope_ids: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "mode": "apply" if self.applied else "dry-run",
            "envelope_count": self.envelope_count,
            "candidate_reference_count": self.candidate_reference_count,
            "session_count": self.session_count,
            "object_count": self.object_count,
            "repaired_envelope_count": self.repaired_envelope_count,
            "envelope_ids": self.envelope_ids,
        }


def transform_legacy_domain_envelope_payload(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Rename the one legacy key and validate the resulting current contract."""

    if LEGACY_OBJECTS_KEY not in payload:
        raise LegacyDomainEnvelopeRepairError(
            f"payload does not contain legacy key {LEGACY_OBJECTS_KEY!r}"
        )
    if CURRENT_OBJECTS_KEY in payload:
        raise LegacyDomainEnvelopeRepairError(
            "payload contains both legacy 'objects' and current "
            "'extracted_objects' keys"
        )

    legacy_objects = payload[LEGACY_OBJECTS_KEY]
    if not isinstance(legacy_objects, list):
        raise LegacyDomainEnvelopeRepairError("legacy 'objects' value is not a list")

    transformed = deepcopy(dict(payload))
    transformed[CURRENT_OBJECTS_KEY] = transformed.pop(LEGACY_OBJECTS_KEY)

    round_trip = deepcopy(transformed)
    round_trip[LEGACY_OBJECTS_KEY] = round_trip.pop(CURRENT_OBJECTS_KEY)
    if round_trip != dict(payload):
        raise LegacyDomainEnvelopeRepairError(
            "legacy payload changed beyond the intended key rename"
        )

    try:
        DomainEnvelope.model_validate(transformed)
    except ValidationError as exc:
        validation_errors = "; ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['type']}"
            for error in exc.errors(include_input=False, include_url=False)
        )
        raise LegacyDomainEnvelopeRepairError(
            "renamed payload does not satisfy the current DomainEnvelope contract: "
            f"{validation_errors}"
        ) from None

    return transformed


def repair_legacy_domain_envelope_payloads(
    db: Session,
    *,
    apply: bool = False,
    expectations: LegacyDomainEnvelopeRepairExpectations | None = None,
) -> LegacyDomainEnvelopeRepairSummary:
    """Audit or atomically repair every envelope carrying the legacy key.

    Apply mode locks the bounded target rows and requires the operator to repeat
    all counts printed by the immediately preceding dry-run.
    """

    if apply and expectations is None:
        raise LegacyDomainEnvelopeRepairError(
            "apply mode requires explicit dry-run count expectations"
        )

    lock_clause = " FOR UPDATE" if apply else ""
    rows = db.execute(text("""
            SELECT envelope_id, session_id, revision, source_payload_hash,
                   envelope_json
            FROM domain_envelopes
            WHERE envelope_json ? 'objects'
            ORDER BY envelope_id
            """ + lock_clause)).mappings().all()

    transformed_by_id: dict[str, dict[str, Any]] = {}
    object_count = 0
    session_ids: set[str] = set()
    for row in rows:
        payload = row["envelope_json"]
        if not isinstance(payload, Mapping):
            raise LegacyDomainEnvelopeRepairError(
                f"envelope {row['envelope_id']!r} payload is not a JSON object"
            )
        transformed = transform_legacy_domain_envelope_payload(payload)
        if transformed.get("envelope_id") != row["envelope_id"]:
            raise LegacyDomainEnvelopeRepairError(
                f"envelope {row['envelope_id']!r} payload ID does not match its row"
            )
        transformed_by_id[row["envelope_id"]] = transformed
        object_count += len(transformed[CURRENT_OBJECTS_KEY])
        if row["session_id"] is not None:
            session_ids.add(str(row["session_id"]))

    envelope_ids = list(transformed_by_id)
    candidate_reference_count = _candidate_reference_count(db, envelope_ids)
    summary = LegacyDomainEnvelopeRepairSummary(
        applied=apply,
        envelope_count=len(envelope_ids),
        candidate_reference_count=candidate_reference_count,
        session_count=len(session_ids),
        object_count=object_count,
        envelope_ids=envelope_ids,
    )

    if apply:
        _assert_expected_counts(summary, expectations)
        for envelope_id, transformed in transformed_by_id.items():
            result = db.execute(
                text("""
                    UPDATE domain_envelopes
                    SET envelope_json = CAST(:envelope_json AS jsonb)
                    WHERE envelope_id = :envelope_id
                      AND envelope_json ? 'objects'
                      AND NOT envelope_json ? 'extracted_objects'
                    """),
                {
                    "envelope_id": envelope_id,
                    "envelope_json": _json_dumps(transformed),
                },
            )
            if result.rowcount != 1:
                raise LegacyDomainEnvelopeRepairError(
                    f"envelope {envelope_id!r} changed after target selection"
                )
        remaining = db.scalar(
            text(
                "SELECT count(*) FROM domain_envelopes WHERE envelope_json ? 'objects'"
            )
        )
        if remaining != 0:
            raise LegacyDomainEnvelopeRepairError(
                f"legacy envelope rows remain after repair: {remaining}"
            )
        summary.repaired_envelope_count = len(envelope_ids)

    return summary


def _candidate_reference_count(db: Session, envelope_ids: list[str]) -> int:
    if not envelope_ids:
        return 0
    return int(
        db.scalar(
            text("""
                SELECT count(*)
                FROM curation_candidates
                WHERE envelope_id = ANY(CAST(:envelope_ids AS text[]))
                """),
            {"envelope_ids": envelope_ids},
        )
        or 0
    )


def _assert_expected_counts(
    summary: LegacyDomainEnvelopeRepairSummary,
    expectations: LegacyDomainEnvelopeRepairExpectations | None,
) -> None:
    assert expectations is not None
    actual = {
        "envelopes": summary.envelope_count,
        "candidate_references": summary.candidate_reference_count,
        "sessions": summary.session_count,
        "objects": summary.object_count,
    }
    expected = {
        "envelopes": expectations.envelopes,
        "candidate_references": expectations.candidate_references,
        "sessions": expectations.sessions,
        "objects": expectations.objects,
    }
    mismatches = [
        f"{name}: expected {expected[name]}, found {actual[name]}"
        for name in expected
        if expected[name] != actual[name]
    ]
    if mismatches:
        raise LegacyDomainEnvelopeRepairError(
            "apply count expectations do not match the locked target set ("
            + "; ".join(mismatches)
            + ")"
        )


def _json_dumps(payload: Mapping[str, Any]) -> str:
    import json

    return json.dumps(payload, separators=(",", ":"), sort_keys=True)
