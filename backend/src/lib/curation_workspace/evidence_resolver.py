"""Deterministic evidence-anchor resolver for tool-verified prep evidence."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Callable, Sequence

from sqlalchemy.orm import Session

from src.lib.curation_workspace.session_service import PreparedEvidenceRecordInput
from src.models.sql.database import SessionLocal
from src.schemas.curation_prep import CurationPrepCandidate
from src.schemas.curation_workspace import (
    CurationEvidenceSource,
    EvidenceAnchor,
)

if TYPE_CHECKING:
    from src.lib.curation_workspace.pipeline import EvidenceResolutionContext, NormalizedCandidate


SessionFactory = Callable[[], Session]
UserIdResolver = Callable[[str], str | None]
ChunkLoader = Callable[[str, str], Sequence[dict[str, Any]]]

_FIGURE_REFERENCE_PATTERN = re.compile(r"\bfig(?:ure)?\b", re.IGNORECASE)
_TABLE_REFERENCE_PATTERN = re.compile(r"\btable\b", re.IGNORECASE)


class DeterministicEvidenceAnchorResolver:
    """Pass through tool-verified evidence anchors with light reference normalization."""

    def __init__(
        self,
        *,
        session_factory: SessionFactory = SessionLocal,
        user_id_resolver: UserIdResolver | None = None,
        chunk_loader: ChunkLoader | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._user_id_resolver = user_id_resolver
        self._chunk_loader = chunk_loader

    def resolve(
        self,
        candidate: CurationPrepCandidate,
        *,
        normalized_candidate: "NormalizedCandidate",
        context: "EvidenceResolutionContext",
    ) -> list[PreparedEvidenceRecordInput]:
        _ = (
            normalized_candidate,
            context,
            self._session_factory,
            self._user_id_resolver,
            self._chunk_loader,
        )

        primary_fields: set[str] = set()
        resolved_records: list[PreparedEvidenceRecordInput] = []

        for evidence_record in candidate.evidence_records:
            field_keys = list(evidence_record.field_paths)
            resolved_records.append(
                PreparedEvidenceRecordInput(
                    source=CurationEvidenceSource.EXTRACTED,
                    field_keys=field_keys,
                    field_group_keys=_field_group_keys(field_keys),
                    is_primary=(
                        not field_keys
                        or any(field_key not in primary_fields for field_key in field_keys)
                    ),
                    anchor=_normalized_anchor(evidence_record.anchor).model_dump(mode="json"),
                    warnings=[],
                )
            )
            primary_fields.update(field_keys)

        return resolved_records


def _normalized_anchor(anchor: EvidenceAnchor) -> EvidenceAnchor:
    figure_reference, table_reference = _normalized_references(
        anchor.figure_reference,
        anchor.table_reference,
    )
    return anchor.model_copy(
        update={
            "figure_reference": figure_reference,
            "table_reference": table_reference,
        }
    )


def _normalized_references(
    figure_reference: str | None,
    table_reference: str | None,
) -> tuple[str | None, str | None]:
    normalized_figure = _normalized_optional_string(figure_reference)
    normalized_table = _normalized_optional_string(table_reference)

    if normalized_figure and _TABLE_REFERENCE_PATTERN.search(normalized_figure):
        return None, normalized_figure
    if normalized_table and _FIGURE_REFERENCE_PATTERN.search(normalized_table):
        return normalized_table, None

    return normalized_figure, normalized_table


def _field_group_key(field_path: str | None) -> str | None:
    if not field_path or "." not in field_path:
        return None
    return field_path.rsplit(".", 1)[0]


def _field_group_keys(field_paths: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    group_keys: list[str] = []
    for field_path in field_paths:
        group_key = _field_group_key(field_path)
        if not group_key or group_key in seen:
            continue
        seen.add(group_key)
        group_keys.append(group_key)
    return group_keys


def _normalized_optional_string(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


__all__ = ["DeterministicEvidenceAnchorResolver"]
