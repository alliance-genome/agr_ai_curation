"""Project-agnostic sanitation for PostgreSQL text-backed persistence."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, overload


_NUL_CHARACTER = "\x00"


@overload
def sanitize_persisted_text(value: str) -> str: ...


@overload
def sanitize_persisted_text(value: None) -> None: ...


def sanitize_persisted_text(value: str | None) -> str | None:
    """Remove characters PostgreSQL cannot store in text values."""

    if value is None:
        return None
    return value.replace(_NUL_CHARACTER, "")


def sanitize_persisted_json_value(value: Any) -> Any:
    """Recursively sanitize strings and string keys in JSON-like values."""

    if isinstance(value, str):
        return sanitize_persisted_text(value)

    if isinstance(value, Mapping):
        return {
            sanitize_persisted_text(key) if isinstance(key, str) else key:
            sanitize_persisted_json_value(nested_value)
            for key, nested_value in value.items()
        }

    if isinstance(value, list):
        return [sanitize_persisted_json_value(item) for item in value]

    if isinstance(value, tuple):
        return tuple(sanitize_persisted_json_value(item) for item in value)

    return value


__all__ = ["sanitize_persisted_json_value", "sanitize_persisted_text"]
