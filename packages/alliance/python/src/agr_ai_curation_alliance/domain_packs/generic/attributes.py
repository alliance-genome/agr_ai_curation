"""Generic semantic attribute normalization and validation."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

ATTRIBUTE_KEY_PATTERN = re.compile(r"[^0-9a-zA-Z]+")
RESERVED_ATTRIBUTE_PREFIXES = ("object", "payload", "attribute", "artifact")


def normalize_attribute_key(key: Any) -> str:
    normalized = ATTRIBUTE_KEY_PATTERN.sub("_", str(key or "").strip().lower())
    return re.sub(r"_+", "_", normalized).strip("_")


def normalized_attribute_keys(attributes: Mapping[str, Any] | None) -> list[str]:
    if not isinstance(attributes, Mapping):
        return []
    keys: list[str] = []
    seen: set[str] = set()
    for key in attributes:
        normalized = normalize_attribute_key(key)
        if normalized and normalized not in seen:
            seen.add(normalized)
            keys.append(normalized)
    return keys


def is_scalar_attribute_value(value: Any) -> bool:
    return isinstance(value, (str, int, float, bool)) or value is None


def _attribute_validation_issue(
    *,
    key: Any,
    reason: str,
    message: str,
) -> dict[str, Any]:
    raw_key = str(key or "").strip()
    return {
        "field_path": f"attributes.{raw_key}" if raw_key else "attributes",
        "reason": reason,
        "message": message,
    }


def normalize_generic_attributes(
    attributes: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not attributes:
        return {}, []
    normalized: dict[str, Any] = {}
    issues: list[dict[str, Any]] = []
    for raw_key, value in attributes.items():
        key_text = str(raw_key or "").strip()
        normalized_key = normalize_attribute_key(key_text)
        if not key_text or not normalized_key:
            issues.append(
                _attribute_validation_issue(
                    key=raw_key,
                    reason="empty_attribute_key",
                    message="Generic attribute keys must be non-empty after normalization.",
                )
            )
            continue
        if any(separator in key_text for separator in (".", "/", "\\")):
            issues.append(
                _attribute_validation_issue(
                    key=raw_key,
                    reason="attribute_key_path_separator",
                    message="Generic attribute keys must not contain path separators.",
                )
            )
            continue
        first_segment = normalized_key.split("_", 1)[0]
        if first_segment in RESERVED_ATTRIBUTE_PREFIXES:
            issues.append(
                _attribute_validation_issue(
                    key=raw_key,
                    reason="reserved_attribute_key",
                    message=(
                        "Generic attribute keys must not use reserved prefixes: "
                        f"{', '.join(RESERVED_ATTRIBUTE_PREFIXES)}."
                    ),
                )
            )
            continue
        if normalized_key in normalized:
            issues.append(
                _attribute_validation_issue(
                    key=raw_key,
                    reason="duplicate_normalized_attribute_key",
                    message=(
                        "Generic attribute keys must be unique after snake_case "
                        f"normalization; duplicate key: {normalized_key}."
                    ),
                )
            )
            continue
        if is_scalar_attribute_value(value):
            normalized[normalized_key] = value
            continue
        if isinstance(value, list) and all(is_scalar_attribute_value(item) for item in value):
            normalized[normalized_key] = list(value)
            continue
        issues.append(
            _attribute_validation_issue(
                key=raw_key,
                reason="invalid_attribute_value",
                message=(
                    "Generic attribute values must be JSON scalars or lists of "
                    "JSON scalars; nested objects and row blobs are not allowed."
                ),
            )
        )
    return normalized, issues
