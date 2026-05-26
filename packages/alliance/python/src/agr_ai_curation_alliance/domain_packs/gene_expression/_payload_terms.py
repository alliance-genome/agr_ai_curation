"""Shared payload term helpers for Gene Expression conversion/export code."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


TERM_SELECTOR_FIELDS = ("curie", "name", "abbreviation")


def value_missing_or_blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, Mapping):
        return len(value) == 0
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return len(value) == 0
    return False


def term_payload(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    payload = {
        selector: value.get(selector)
        for selector in TERM_SELECTOR_FIELDS
        if not value_missing_or_blank(value.get(selector))
    }
    return payload or None


def has_term_selector(value: Any) -> bool:
    return term_payload(value) is not None


def term_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [
        term
        for item in value
        if (term := term_payload(item)) is not None
    ]


__all__ = (
    "has_term_selector",
    "term_list",
    "term_payload",
    "value_missing_or_blank",
)
