"""Shared payload term helpers for Gene Expression conversion/export code."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .resolvable import TERM_IDENTITY_KEYS, is_resolved


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


def term_present(value: Any) -> bool:
    """Whether the paper supplied this term at all, resolved or not."""

    return isinstance(value, Mapping) and not value_missing_or_blank(value)


def term_payload(value: Any) -> dict[str, Any] | None:
    """The validated ``{curie, name}`` of a resolved term; None for any other value.

    Only the term's own identity keys are read; an unresolved term has none.
    """

    if not is_resolved(value):
        return None
    return {
        key: value[key]
        for key in TERM_IDENTITY_KEYS
        if not value_missing_or_blank(value.get(key))
    }


def term_list(value: Any) -> list[dict[str, Any]]:
    """The validated identities of a list's resolved terms."""

    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [
        term
        for item in value
        if (term := term_payload(item)) is not None
    ]


__all__ = (
    "term_list",
    "term_payload",
    "term_present",
    "value_missing_or_blank",
)
