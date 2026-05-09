"""Allele extractor domain-envelope conversion for curation prep."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from src.lib.curation_workspace.prep_item_conversion import (
    PrepItemConversionResult,
    compact_payload,
    curatable_object_lookup,
    metadata_evidence_records_by_id,
    normalized_evidence_record_ids,
    normalized_optional_string,
    referenced_object_payloads,
    string_values,
)


class AlleleExtractorPrepItemConverter:
    """Convert allele domain-envelope objects into generic prep items."""

    def convert(self, payload: Mapping[str, Any]) -> PrepItemConversionResult:
        raw_curatable_objects = payload.get("curatable_objects")
        if not isinstance(raw_curatable_objects, list):
            return PrepItemConversionResult(
                items=(),
                evidence_records_by_id=metadata_evidence_records_by_id(payload),
            )

        return PrepItemConversionResult(
            items=tuple(_items_from_curatable_objects(raw_curatable_objects)),
            evidence_records_by_id=metadata_evidence_records_by_id(payload),
        )


def _items_from_curatable_objects(
    raw_curatable_objects: Sequence[Any],
) -> list[Mapping[str, Any]]:
    object_lookup = curatable_object_lookup(raw_curatable_objects)
    items: list[Mapping[str, Any]] = []

    for raw_object in raw_curatable_objects:
        if not isinstance(raw_object, Mapping):
            continue
        if raw_object.get("object_type") != "AllelePaperEvidenceAssociation":
            continue

        association_payload = raw_object.get("payload")
        if not isinstance(association_payload, Mapping):
            continue

        mention_payloads = referenced_object_payloads(
            raw_object,
            object_lookup,
            object_type="AlleleMention",
        )
        allele_payloads = referenced_object_payloads(
            raw_object,
            object_lookup,
            object_type="Allele",
        )
        allele_label = _allele_label(
            mention_payloads=mention_payloads,
            allele_payloads=allele_payloads,
        )
        allele_identifier = normalized_optional_string(
            association_payload.get("allele_identifier")
        )
        evidence_record_ids = normalized_evidence_record_ids(
            raw_object.get("evidence_record_ids")
        )
        if allele_label is None or allele_identifier is None:
            continue

        item = compact_payload(
            {
                "label": allele_label,
                "entity_type": "allele",
                "normalized_id": allele_identifier,
                "source_mentions": _source_mentions(
                    association_payload,
                    mention_payloads=mention_payloads,
                    allele_payloads=allele_payloads,
                ),
                "associated_gene": association_payload.get("associated_gene"),
                "confidence": association_payload.get("confidence"),
                "evidence_record_ids": evidence_record_ids,
            }
        )
        if isinstance(item, Mapping):
            items.append(item)

    return items


def _allele_label(
    *,
    mention_payloads: Sequence[Mapping[str, Any]],
    allele_payloads: Sequence[Mapping[str, Any]],
) -> str | None:
    # Prefer the normalized allele object label; mention text preserves source wording
    # when no label is available.
    for allele_payload in allele_payloads:
        normalized = normalized_optional_string(allele_payload.get("allele_symbol"))
        if normalized is not None:
            return normalized
    for mention_payload in mention_payloads:
        normalized = normalized_optional_string(mention_payload.get("mention_text"))
        if normalized is not None:
            return normalized
    return None


def _source_mentions(
    association_payload: Mapping[str, Any],
    *,
    mention_payloads: Sequence[Mapping[str, Any]],
    allele_payloads: Sequence[Mapping[str, Any]],
) -> list[str]:
    return _dedupe_strings(
        [
            *string_values(association_payload.get("source_mentions")),
            *(
                value
                for mention_payload in mention_payloads
                for value in string_values(mention_payload.get("source_mentions"))
            ),
            *(
                value
                for allele_payload in allele_payloads
                for value in string_values(allele_payload.get("source_mentions"))
            ),
            *(
                value
                for mention_payload in mention_payloads
                for value in (
                    normalized_optional_string(mention_payload.get("mention_text")),
                )
                if value is not None
            ),
        ]
    )


def _dedupe_strings(values: Sequence[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = value.strip()
        if not normalized or normalized in seen:
            continue
        deduped.append(normalized)
        seen.add(normalized)
    return deduped


__all__ = ["AlleleExtractorPrepItemConverter"]
