"""Generic adapter normalizer for package-owned structured payloads."""

from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING, Any

from src.schemas.curation_prep import CurationPrepCandidate

if TYPE_CHECKING:
    from src.lib.curation_workspace.pipeline import (
        CandidateNormalizationContext,
        NormalizedCandidate,
    )
    from src.lib.curation_workspace.session_service import PreparedDraftFieldInput


class StructuredPayloadCandidateNormalizer:
    """Preserve adapter-owned payload structure without relying on a core fallback path."""

    def normalize(
        self,
        payload: dict[str, Any],
        *,
        prep_candidate: CurationPrepCandidate,
        context: "CandidateNormalizationContext",
    ) -> "NormalizedCandidate":
        from src.lib.curation_workspace.pipeline import NormalizedCandidate

        normalized_payload = _normalized_payload(payload)
        draft_fields = _build_draft_fields(normalized_payload)
        display_values = _scalar_display_values(normalized_payload)
        display_label = display_values[0] if display_values else f"Candidate {context.candidate_index + 1}"
        secondary_label = display_values[1] if len(display_values) > 1 else None

        return NormalizedCandidate(
            prep_candidate=prep_candidate,
            normalized_payload=normalized_payload,
            draft_fields=draft_fields,
            display_label=display_label,
            secondary_label=secondary_label,
            metadata={"normalizer": type(self).__name__},
        )


def _normalized_payload(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = deepcopy(payload)
    entity_name = _entity_name_value(normalized)
    if entity_name is not None and "entity_name" not in normalized:
        return {"entity_name": entity_name, **normalized}
    return normalized


def _entity_name_value(payload: dict[str, Any]) -> str | None:
    for field_key in ("entity_name", "gene_symbol", "label"):
        value = payload.get(field_key)
        if isinstance(value, str):
            normalized = value.strip()
            if normalized:
                return normalized
    return None


def _build_draft_fields(payload: dict[str, Any]) -> list["PreparedDraftFieldInput"]:
    from src.lib.curation_workspace.session_service import PreparedDraftFieldInput

    return [
        PreparedDraftFieldInput(
            field_key=field_key,
            label=_humanize_path(field_key),
            value=value,
            seed_value=deepcopy(value),
            field_type=_payload_field_type(value),
            group_key=_field_group_key(field_key),
            group_label=_humanize_path(_field_group_key(field_key)),
            order=index,
            metadata={"source_field_path": field_key},
        )
        for index, (field_key, value) in enumerate(_iter_payload_field_items(payload))
    ]


def _iter_payload_field_items(payload: Any, *, prefix: str = "") -> list[tuple[str, Any]]:
    if isinstance(payload, dict):
        if not payload and prefix:
            return [(prefix, {})]
        items: list[tuple[str, Any]] = []
        for key, value in payload.items():
            field_key = f"{prefix}.{key}" if prefix else str(key)
            items.extend(_iter_payload_field_items(value, prefix=field_key))
        return items

    if isinstance(payload, list):
        if not payload and prefix:
            return [(prefix, [])]
        items: list[tuple[str, Any]] = []
        for index, value in enumerate(payload):
            field_key = f"{prefix}.{index}" if prefix else str(index)
            items.extend(_iter_payload_field_items(value, prefix=field_key))
        return items

    if not prefix:
        return []
    return [(prefix, payload)]


def _payload_field_type(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if value is None:
        return "null"
    return "json"


def _field_group_key(field_key: str | None) -> str | None:
    if field_key is None or "." not in field_key:
        return None
    return field_key.rsplit(".", 1)[0]


def _humanize_path(path: str | None) -> str | None:
    if path is None:
        return None
    segments = [segment.replace("_", " ").strip() for segment in path.split(".") if segment]
    if not segments:
        return None
    return " / ".join(segment.title() for segment in segments)


def _scalar_display_values(payload: Any) -> list[str]:
    values: list[str] = []
    _collect_scalar_display_values(payload, values)
    return _dedupe(values)


def _collect_scalar_display_values(payload: Any, values: list[str]) -> None:
    if payload is None:
        return
    if isinstance(payload, bool):
        values.append(str(payload).lower())
        return
    if isinstance(payload, (int, float, str)):
        normalized = str(payload).strip()
        if normalized:
            values.append(normalized)
        return
    if isinstance(payload, dict):
        for value in payload.values():
            _collect_scalar_display_values(value, values)
        return
    if isinstance(payload, list):
        for value in payload:
            _collect_scalar_display_values(value, values)


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered

