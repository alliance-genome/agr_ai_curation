"""Reference lookup inputs carried by one loaded chat document."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any


def _mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, Mapping) else {}
    return {}


def _texts(value: Any) -> list[str]:
    values = value if isinstance(value, list) else [value]
    normalized: list[str] = []
    for item in values:
        text = str(item or "").strip()
        if text and text not in normalized:
            normalized.append(text)
    return normalized


def _prefixed_identifiers(value: Any, prefix: str) -> list[str]:
    identifiers: list[str] = []
    for text in _texts(value):
        identifier = text if text.casefold().startswith(f"{prefix.casefold()}:") else f"{prefix}:{text}"
        if identifier not in identifiers:
            identifiers.append(identifier)
    return identifiers


def reference_lookup_inputs_from_document(
    document: Mapping[str, Any],
) -> dict[str, Any]:
    """Return non-secret PMID/DOI/AGRKB/title inputs from loaded provenance."""

    metadata = _mapping(document.get("metadata"))
    provenance = _mapping(
        document.get("source_provenance") or metadata.get("source_provenance")
    )
    external_ids = _mapping(provenance.get("external_ids"))

    pmids = _prefixed_identifiers(external_ids.get("pmid"), "PMID")
    dois = _prefixed_identifiers(external_ids.get("doi"), "DOI")
    curie = str(provenance.get("reference_curie") or "").strip() or None
    title = str(document.get("title") or metadata.get("title") or "").strip() or None

    return {
        **({"curie": curie} if curie else {}),
        **({"pmid": pmids[0]} if len(pmids) == 1 else {}),
        **({"pmids": pmids} if len(pmids) > 1 else {}),
        **({"doi": dois[0]} if len(dois) == 1 else {}),
        **({"dois": dois} if len(dois) > 1 else {}),
        **({"title": title} if title else {}),
    }


__all__ = ["reference_lookup_inputs_from_document"]
