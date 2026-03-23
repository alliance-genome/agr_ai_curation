"""Shared validation helpers for pipeline and workspace mutation flows."""

from __future__ import annotations

from typing import Any, Sequence

from src.schemas.curation_workspace import (
    CurationValidationCounts,
    FieldValidationStatus,
)


def dedupe(values: Sequence[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        deduped.append(value)
        seen.add(value)
    return deduped


def field_validation_status(value: Any) -> tuple[FieldValidationStatus, list[str]]:
    if value is None:
        return (
            FieldValidationStatus.INVALID_FORMAT,
            ["Extracted field is empty and needs curator review."],
        )
    if isinstance(value, str) and not value.strip():
        return (
            FieldValidationStatus.INVALID_FORMAT,
            ["Extracted field is blank and needs curator review."],
        )
    if isinstance(value, (list, dict)) and not value:
        return (
            FieldValidationStatus.INVALID_FORMAT,
            ["Extracted field is empty and needs curator review."],
        )
    return (FieldValidationStatus.SKIPPED, [])


def increment_validation_count(
    counts: CurationValidationCounts,
    status: FieldValidationStatus,
) -> None:
    if status == FieldValidationStatus.VALIDATED:
        counts.validated += 1
    elif status == FieldValidationStatus.AMBIGUOUS:
        counts.ambiguous += 1
    elif status == FieldValidationStatus.NOT_FOUND:
        counts.not_found += 1
    elif status == FieldValidationStatus.INVALID_FORMAT:
        counts.invalid_format += 1
    elif status == FieldValidationStatus.CONFLICT:
        counts.conflict += 1
    elif status == FieldValidationStatus.SKIPPED:
        counts.skipped += 1
    elif status == FieldValidationStatus.OVERRIDDEN:
        counts.overridden += 1


__all__ = [
    "dedupe",
    "field_validation_status",
    "increment_validation_count",
]
