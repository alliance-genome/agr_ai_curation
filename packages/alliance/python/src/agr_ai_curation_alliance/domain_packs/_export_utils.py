"""Shared helpers for Alliance domain-envelope export adapters."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from src.schemas.domain_envelope import parse_field_path


MISSING = object()


def canonical_json(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return a deterministic JSON-compatible mapping."""

    return json.loads(json.dumps(payload, sort_keys=True))


def payload_value(payload: Mapping[str, Any], field_path: str) -> Any:
    """Resolve one object-local field path against an envelope object payload."""

    current: Any = payload
    for part in parse_field_path(field_path):
        if isinstance(part, str):
            if not isinstance(current, Mapping) or part not in current:
                return MISSING
            current = current[part]
            continue

        if (
            not isinstance(current, Sequence)
            or isinstance(current, (str, bytes, bytearray))
            or part >= len(current)
        ):
            return MISSING
        current = current[part]
    return current


def required_value_present(payload: Mapping[str, Any], field_path: str) -> bool:
    """Return whether a required payload field exists and is non-empty."""

    value = payload_value(payload, field_path)
    if value is MISSING or value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (Mapping, list, tuple, set)):
        return bool(value)
    return True


def string_value(payload: Mapping[str, Any], field_path: str) -> str | None:
    """Return a stripped string payload value, or None when absent or blank."""

    value = payload_value(payload, field_path)
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def mapping_value(payload: Mapping[str, Any], field_path: str) -> dict[str, Any]:
    """Return a shallow dict payload value, or an empty dict when absent."""

    value = payload_value(payload, field_path)
    return dict(value) if isinstance(value, Mapping) else {}


def list_value(payload: Mapping[str, Any], field_path: str) -> list[Any]:
    """Return a list payload value, or an empty list when absent."""

    value = payload_value(payload, field_path)
    return list(value) if isinstance(value, list) else []


def candidate_payload(candidate: Mapping[str, Any]) -> dict[str, Any]:
    """Return the selected domain-envelope object payload from a candidate bundle."""

    payload = candidate.get("payload")
    return dict(payload) if isinstance(payload, Mapping) else {}


def candidate_object_id(candidate: Mapping[str, Any]) -> str | None:
    """Return the stable object identifier from a domain-envelope candidate bundle."""

    value = candidate.get("object_id")
    return value.strip() if isinstance(value, str) and value.strip() else None


def candidate_object_type(candidate: Mapping[str, Any]) -> str | None:
    """Return the object type from a domain-envelope candidate bundle."""

    value = candidate.get("object_type")
    return value.strip() if isinstance(value, str) and value.strip() else None


def adapter_blocker(
    *,
    candidate: Mapping[str, Any],
    code: str,
    message: str,
    field_path: str | None = None,
    details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a deterministic, object-addressable adapter blocker payload."""

    return {
        "candidate_id": candidate.get("candidate_id"),
        "envelope_id": candidate.get("envelope_id"),
        "object_id": candidate_object_id(candidate),
        "object_type": candidate_object_type(candidate),
        "field_path": field_path,
        "severity": "blocker",
        "status": "open",
        "code": code,
        "message": message,
        "details": dict(details or {}),
    }


def missing_field_blockers(
    *,
    candidate: Mapping[str, Any],
    payload: Mapping[str, Any],
    required_field_paths: Sequence[str],
    code: str,
    message_prefix: str,
) -> list[dict[str, Any]]:
    """Return field-addressed blockers for required payload fields."""

    blockers: list[dict[str, Any]] = []
    for field_path in required_field_paths:
        if required_value_present(payload, field_path):
            continue
        blockers.append(
            adapter_blocker(
                candidate=candidate,
                code=code,
                field_path=field_path,
                message=f"{message_prefix}: {field_path}.",
                details={"missing_field": field_path},
            )
        )
    return blockers


def first_string(
    payload: Mapping[str, Any],
    field_paths: Sequence[str],
) -> tuple[str | None, str | None]:
    """Return the first populated string among field paths and the matched path."""

    for field_path in field_paths:
        value = string_value(payload, field_path)
        if value is not None:
            return value, field_path
    return None, None


def source_reference_id_from_context(
    *,
    candidate: Mapping[str, Any],
    domain_envelopes: Sequence[Mapping[str, Any]],
) -> int | str | None:
    """Resolve source_reference.reference_id from payload or linked Reference objects."""

    payload = candidate_payload(candidate)
    direct_value = payload_value(payload, "source_reference.reference_id")
    if direct_value is not MISSING and direct_value is not None:
        if not isinstance(direct_value, str) or direct_value.strip():
            return direct_value

    reference_ref_keys = _candidate_ref_keys(candidate, object_type="Reference")
    if not reference_ref_keys:
        return None

    for raw_envelope in domain_envelopes:
        objects = raw_envelope.get("objects") if isinstance(raw_envelope, Mapping) else None
        if not isinstance(objects, list):
            continue
        for raw_object in objects:
            if not isinstance(raw_object, Mapping):
                continue
            if _object_ref_key(raw_object) not in reference_ref_keys:
                continue
            reference_payload = raw_object.get("payload")
            if not isinstance(reference_payload, Mapping):
                continue
            value = payload_value(reference_payload, "reference_id")
            if value is not MISSING and value is not None:
                if not isinstance(value, str) or value.strip():
                    return value
    return None


def _candidate_ref_keys(
    candidate: Mapping[str, Any],
    *,
    object_type: str,
) -> set[tuple[str, str]]:
    raw_object = candidate.get("object")
    object_refs = raw_object.get("object_refs") if isinstance(raw_object, Mapping) else None
    if not isinstance(object_refs, list):
        return set()

    ref_keys: set[tuple[str, str]] = set()
    for ref in object_refs:
        if not isinstance(ref, Mapping) or ref.get("object_type") != object_type:
            continue
        object_id = ref.get("object_id")
        pending_ref_id = ref.get("pending_ref_id")
        if isinstance(object_id, str) and object_id.strip():
            ref_keys.add(("object_id", object_id.strip()))
        if isinstance(pending_ref_id, str) and pending_ref_id.strip():
            ref_keys.add(("pending_ref_id", pending_ref_id.strip()))
    return ref_keys


def _object_ref_key(raw_object: Mapping[str, Any]) -> tuple[str, str] | None:
    object_id = raw_object.get("object_id")
    pending_ref_id = raw_object.get("pending_ref_id")
    if isinstance(object_id, str) and object_id.strip():
        return ("object_id", object_id.strip())
    if isinstance(pending_ref_id, str) and pending_ref_id.strip():
        return ("pending_ref_id", pending_ref_id.strip())
    return None
