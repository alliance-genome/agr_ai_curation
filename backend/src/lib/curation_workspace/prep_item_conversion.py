"""Shared contracts and helpers for package-owned prep item conversion."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class PrepItemConversionResult:
    """Items and evidence registry produced by a package-owned extraction mapper."""

    items: tuple[Mapping[str, Any], ...]
    evidence_records_by_id: Mapping[str, Mapping[str, Any]]


class PrepItemConverter(Protocol):
    """Package-owned converter for extraction output shapes that are not items[]."""

    def convert(self, payload: Mapping[str, Any]) -> PrepItemConversionResult:
        """Return generic prep items plus their canonical evidence registry."""


def evidence_records_by_id_from_records(raw_records: Any) -> dict[str, Mapping[str, Any]]:
    """Index evidence records from one already-resolved canonical collection."""

    if not isinstance(raw_records, list):
        return {}

    records_by_id: dict[str, Mapping[str, Any]] = {}
    for raw_record in raw_records:
        if not isinstance(raw_record, Mapping):
            continue
        evidence_record_id = normalized_optional_string(raw_record.get("evidence_record_id"))
        if evidence_record_id is None:
            continue
        records_by_id[evidence_record_id] = raw_record
    return records_by_id


def top_level_evidence_records_by_id(payload: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    """Index the legacy items[] evidence registry from its canonical top-level location."""

    return evidence_records_by_id_from_records(payload.get("evidence_records"))


def metadata_evidence_records_by_id(payload: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    """Index domain-envelope evidence records from metadata.evidence_records[]."""

    metadata = payload.get("metadata")
    if not isinstance(metadata, Mapping):
        return {}
    return evidence_records_by_id_from_records(metadata.get("evidence_records"))


def curatable_object_lookup(
    raw_curatable_objects: Sequence[Any],
) -> dict[tuple[str, str], Mapping[str, Any]]:
    """Build a lookup for domain-envelope objects by supported reference fields."""

    object_lookup: dict[tuple[str, str], Mapping[str, Any]] = {}
    for raw_object in raw_curatable_objects:
        if not isinstance(raw_object, Mapping):
            continue
        for ref_key in ("pending_ref_id", "object_id"):
            ref_value = normalized_optional_string(raw_object.get(ref_key))
            if ref_value is not None:
                object_lookup[(ref_key, ref_value)] = raw_object
    return object_lookup


def referenced_object_payloads(
    raw_object: Mapping[str, Any],
    object_lookup: Mapping[tuple[str, str], Mapping[str, Any]],
    *,
    object_type: str,
) -> list[Mapping[str, Any]]:
    """Return payloads for referenced objects of one type."""

    raw_refs = raw_object.get("object_refs")
    if not isinstance(raw_refs, list):
        return []

    payloads: list[Mapping[str, Any]] = []
    for raw_ref in raw_refs:
        if not isinstance(raw_ref, Mapping) or raw_ref.get("object_type") != object_type:
            continue
        referenced_object = None
        for ref_key in ("pending_ref_id", "object_id"):
            ref_value = normalized_optional_string(raw_ref.get(ref_key))
            if ref_value is None:
                continue
            referenced_object = object_lookup.get((ref_key, ref_value))
            if referenced_object is not None:
                break
        if referenced_object is None:
            continue
        payload = referenced_object.get("payload")
        if isinstance(payload, Mapping):
            payloads.append(payload)
    return payloads


def string_values(value: Any) -> list[str]:
    """Normalize one string or list of strings to a compact string list."""

    if isinstance(value, list):
        return [
            normalized
            for item in value
            for normalized in (normalized_optional_string(item),)
            if normalized is not None
        ]
    normalized = normalized_optional_string(value)
    return [normalized] if normalized is not None else []


def normalized_evidence_record_ids(value: Any) -> list[str]:
    """Normalize evidence record ids while preserving order."""

    if not isinstance(value, list):
        return []

    evidence_record_ids: list[str] = []
    seen: set[str] = set()
    for candidate in value:
        evidence_record_id = normalized_optional_string(candidate)
        if evidence_record_id is None or evidence_record_id in seen:
            continue
        seen.add(evidence_record_id)
        evidence_record_ids.append(evidence_record_id)
    return evidence_record_ids


def normalized_optional_string(value: Any) -> str | None:
    """Return a stripped non-empty string, or None."""

    if value is None or not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def compact_payload(value: Any) -> Any:
    """Drop None/empty list/empty object values from JSON-compatible payloads."""

    if isinstance(value, Mapping):
        compacted: dict[str, Any] = {}
        for key, item in value.items():
            compacted_item = compact_payload(item)
            if compacted_item is None:
                continue
            compacted[str(key)] = compacted_item
        return compacted or None
    if isinstance(value, list):
        compacted_items = [compact_payload(item) for item in value]
        compacted_items = [item for item in compacted_items if item is not None]
        return compacted_items or None
    if isinstance(value, str):
        return normalized_optional_string(value)
    if isinstance(value, bool):
        return value
    return value


__all__ = [
    "PrepItemConversionResult",
    "PrepItemConverter",
    "compact_payload",
    "curatable_object_lookup",
    "evidence_records_by_id_from_records",
    "metadata_evidence_records_by_id",
    "normalized_evidence_record_ids",
    "normalized_optional_string",
    "referenced_object_payloads",
    "string_values",
    "top_level_evidence_records_by_id",
]
