"""Reference adapter candidate normalization for the curation workspace."""

from __future__ import annotations

import math
from copy import deepcopy
from typing import TYPE_CHECKING, Any

from src.lib.curation_adapters.reference.field_layout import (
    REFERENCE_ADAPTER_KEY,
    REFERENCE_FIELD_DEFINITIONS,
    REFERENCE_TYPE_OPTIONS,
)
from src.schemas.curation_prep import CurationPrepCandidate

if TYPE_CHECKING:
    from src.lib.curation_workspace.pipeline import (
        CandidateNormalizationContext,
        NormalizedCandidate,
    )
    from src.lib.curation_workspace.session_service import PreparedDraftFieldInput


REFERENCE_VALIDATION_PLAN_KEY = "reference_field_plan_v1"
REFERENCE_PAYLOAD_BUILDER_KEY = "reference_payload_v1"
REFERENCE_LAYOUT_KEY = "reference_layout_v1"
ALLOWED_REFERENCE_TYPES = {
    option["value"] for option in REFERENCE_TYPE_OPTIONS
}


class ReferenceCandidateNormalizer:
    """Adapter-owned normalizer for reference-style candidates."""

    def normalize(
        self,
        payload: dict[str, Any],
        *,
        prep_candidate: CurationPrepCandidate,
        context: CandidateNormalizationContext,
    ) -> NormalizedCandidate:
        from src.lib.curation_workspace.pipeline import NormalizedCandidate

        normalized_payload, defaulted_fields = _build_reference_payload(payload)
        draft_fields = _build_reference_draft_fields(
            normalized_payload,
            defaulted_fields=defaulted_fields,
        )

        return NormalizedCandidate(
            prep_candidate=prep_candidate,
            normalized_payload=normalized_payload,
            draft_fields=draft_fields,
            display_label=_build_display_label(normalized_payload, candidate_index=context.candidate_index),
            secondary_label=_build_secondary_label(normalized_payload),
            metadata={
                "reference_adapter": {
                    "adapter_key": REFERENCE_ADAPTER_KEY,
                    "layout_key": REFERENCE_LAYOUT_KEY,
                    "payload_builder": REFERENCE_PAYLOAD_BUILDER_KEY,
                    "validation_plan": REFERENCE_VALIDATION_PLAN_KEY,
                },
            },
        )


def _build_reference_payload(
    extracted_payload: dict[str, Any],
) -> tuple[dict[str, Any], set[str]]:
    defaulted_fields: set[str] = set()

    title = _normalize_text(_get_nested_value(extracted_payload, "citation.title"))
    authors = _normalize_authors(_get_nested_value(extracted_payload, "citation.authors"))
    journal = _normalize_text(_get_nested_value(extracted_payload, "citation.journal"))
    publication_year = _normalize_publication_year(
        _get_nested_value(extracted_payload, "citation.publication_year")
    )
    reference_type = _normalize_reference_type(
        _get_nested_value(extracted_payload, "citation.reference_type")
    )
    doi = _normalize_doi(_get_nested_value(extracted_payload, "identifiers.doi"))
    pmid = _normalize_pmid(_get_nested_value(extracted_payload, "identifiers.pmid"))

    if _get_nested_value(extracted_payload, "citation.reference_type") in (None, "", []):
        defaulted_fields.add("citation.reference_type")

    return (
        {
            "citation": {
                "title": title,
                "authors": authors,
                "journal": journal,
                "publication_year": publication_year,
                "reference_type": reference_type,
            },
            "identifiers": {
                "doi": doi,
                "pmid": pmid,
            },
        },
        defaulted_fields,
    )


def _build_reference_draft_fields(
    normalized_payload: dict[str, Any],
    *,
    defaulted_fields: set[str],
) -> list[PreparedDraftFieldInput]:
    from src.lib.curation_workspace.session_service import PreparedDraftFieldInput

    draft_fields: list[PreparedDraftFieldInput] = []

    for definition in REFERENCE_FIELD_DEFINITIONS:
        value = deepcopy(_get_nested_value(normalized_payload, definition.field_key))
        metadata = definition.metadata_payload()
        metadata["seed_source"] = (
            "adapter_default" if definition.field_key in defaulted_fields else "prep_candidate"
        )
        metadata["default_applied"] = definition.field_key in defaulted_fields

        draft_fields.append(
            PreparedDraftFieldInput(
                field_key=definition.field_key,
                label=definition.label,
                value=value,
                seed_value=deepcopy(value),
                field_type=definition.field_type,
                group_key=definition.group_key,
                group_label=definition.group_label,
                order=definition.order,
                required=definition.required,
                metadata=metadata,
            )
        )

    return draft_fields


def _build_display_label(normalized_payload: dict[str, Any], *, candidate_index: int) -> str:
    title = _normalize_text(_get_nested_value(normalized_payload, "citation.title"))
    if title:
        return title

    authors = _normalize_authors(_get_nested_value(normalized_payload, "citation.authors"))
    publication_year = _normalize_publication_year(
        _get_nested_value(normalized_payload, "citation.publication_year")
    )
    if authors and publication_year is not None:
        return f"{authors[0]} ({publication_year})"
    if authors:
        return authors[0]
    return f"Reference {candidate_index + 1}"


def _build_secondary_label(normalized_payload: dict[str, Any]) -> str | None:
    doi = _normalize_doi(_get_nested_value(normalized_payload, "identifiers.doi"))
    if doi:
        return f"DOI {doi}"

    pmid = _normalize_pmid(_get_nested_value(normalized_payload, "identifiers.pmid"))
    if pmid:
        return f"PMID {pmid}"

    journal = _normalize_text(_get_nested_value(normalized_payload, "citation.journal"))
    if journal:
        return journal

    return None


def _get_nested_value(payload: dict[str, Any], field_key: str) -> Any:
    cursor: Any = payload
    for segment in field_key.split("."):
        if not isinstance(cursor, dict):
            return None
        cursor = cursor.get(segment)
    return cursor


def _normalize_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        normalized = " ".join(value.split())
        return normalized or None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(value, float) and not math.isfinite(value):
            return None
        return str(int(value)) if isinstance(value, float) and value.is_integer() else str(value)
    return None


def _normalize_authors(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [author for author in (_normalize_text(part) for part in value.split("\n")) if author]
    if isinstance(value, list):
        authors: list[str] = []
        for entry in value:
            if isinstance(entry, str):
                normalized = _normalize_text(entry)
            elif isinstance(entry, dict):
                normalized = _normalize_text(entry.get("name"))
            else:
                normalized = None
            if normalized:
                authors.append(normalized)
        return authors
    return []


def _normalize_publication_year(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else None
    if isinstance(value, str):
        normalized = value.strip()
        if len(normalized) == 4 and normalized.isdigit():
            return int(normalized)
    return None


def _normalize_reference_type(value: Any) -> str:
    normalized = _normalize_text(value)
    if not normalized:
        return "journal_article"
    normalized_key = normalized.lower().replace(" ", "_").replace("-", "_")
    return normalized_key if normalized_key in ALLOWED_REFERENCE_TYPES else "other"


def _normalize_doi(value: Any) -> str | None:
    normalized = _normalize_text(value)
    if not normalized:
        return None

    lowered = normalized.lower()
    if lowered.startswith("doi:"):
        lowered = lowered[4:].strip()
    if lowered.startswith("https://doi.org/"):
        lowered = lowered[len("https://doi.org/"):]
    if lowered.startswith("http://doi.org/"):
        lowered = lowered[len("http://doi.org/"):]

    return lowered or None


def _normalize_pmid(value: Any) -> str | None:
    normalized = _normalize_text(value)
    if not normalized:
        return None
    lowered = normalized.lower()
    if lowered.startswith("pmid:"):
        lowered = lowered[5:].strip()
    return lowered or None


__all__ = [
    "REFERENCE_ADAPTER_KEY",
    "REFERENCE_LAYOUT_KEY",
    "REFERENCE_PAYLOAD_BUILDER_KEY",
    "REFERENCE_VALIDATION_PLAN_KEY",
    "ReferenceCandidateNormalizer",
]
