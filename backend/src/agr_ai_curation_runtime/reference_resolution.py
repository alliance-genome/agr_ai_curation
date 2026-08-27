"""Read-only durable reference resolution for package-owned literature tools."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from agr_curation_api.exceptions import AGRAPIError

from .agr_curation import get_curation_resolver


@dataclass(frozen=True)
class CurationReferenceResolution:
    """Outcome of joining one confirmed AGRKB CURIE to the curation database."""

    status: str
    reference: dict[str, Any] | None
    explanation: str


def _reference_payload(reference: Any) -> dict[str, Any]:
    if isinstance(reference, Mapping):
        return dict(reference)
    model_dump = getattr(reference, "model_dump", None)
    if callable(model_dump):
        payload = model_dump(mode="python")
        if isinstance(payload, dict):
            return payload
    raise TypeError(
        "Curation reference lookup returned an unsupported "
        f"{type(reference).__name__} value"
    )


def resolve_curation_reference(curie: str) -> CurationReferenceResolution:
    """Resolve a confirmed AGRKB CURIE to its authoritative integer reference ID."""

    normalized_curie = str(curie or "").strip()
    if not normalized_curie:
        return CurationReferenceResolution(
            status="not_found",
            reference=None,
            explanation="Literature lookup did not supply an AGRKB CURIE for the curation join.",
        )

    resolver = get_curation_resolver()
    db = resolver.get_db_client()
    if db is None:
        return CurationReferenceResolution(
            status="blocked",
            reference=None,
            explanation="The read-only Alliance curation database client is unavailable.",
        )

    try:
        reference = db.get_reference(normalized_curie)
    except AGRAPIError:
        return CurationReferenceResolution(
            status="transient",
            reference=None,
            explanation=(
                "The read-only Alliance curation database reference lookup "
                "failed temporarily."
            ),
        )

    if reference is None:
        return CurationReferenceResolution(
            status="not_found",
            reference=None,
            explanation=(
                f"No Alliance curation reference matched confirmed CURIE {normalized_curie}."
            ),
        )

    payload = _reference_payload(reference)
    resolved_curie = str(payload.get("curie") or "").strip()
    reference_id = payload.get("reference_id")
    if resolved_curie.casefold() != normalized_curie.casefold():
        return CurationReferenceResolution(
            status="conflict",
            reference=None,
            explanation=(
                "Alliance curation reference identity conflicted with the "
                f"literature CURIE {normalized_curie}."
            ),
        )
    if (
        not isinstance(reference_id, int)
        or isinstance(reference_id, bool)
        or reference_id < 1
    ):
        return CurationReferenceResolution(
            status="not_found",
            reference=None,
            explanation=(
                f"Alliance curation reference {normalized_curie} has no integer database ID."
            ),
        )
    if payload.get("obsolete") is True:
        return CurationReferenceResolution(
            status="conflict",
            reference=None,
            explanation=f"Alliance curation reference {normalized_curie} is obsolete.",
        )

    return CurationReferenceResolution(
        status="success",
        reference=payload,
        explanation=(
            f"Resolved {normalized_curie} to authoritative curation reference ID "
            f"{reference_id}."
        ),
    )


__all__ = ["CurationReferenceResolution", "resolve_curation_reference"]
