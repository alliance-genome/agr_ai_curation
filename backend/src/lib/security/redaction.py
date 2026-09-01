"""Central secret classification and mechanical redaction primitives."""

from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any

REDACTED = "[Filtered]"

SENSITIVE_KEY_MARKERS = (
    "authorization",
    "cookie",
    "csrf",
    "password",
    "secret",
    "token",
    "api_key",
    "apikey",
    "access_key",
    "private_key",
    "session",
    "credential",
    "dsn",
)

SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"pk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"(?i)(bearer|basic)\s+[^\s,;]+"),
)


def is_sensitive_key(key: object) -> bool:
    """Return whether a field/header name identifies secret-bearing data."""

    normalized = str(key).lower().replace("-", "_")
    return any(marker in normalized for marker in SENSITIVE_KEY_MARKERS)


def redact_secrets(value: Any, *, depth: int = 0, max_depth: int = 8) -> Any:
    """Redact secret fields and credential-shaped strings in nested values."""

    if depth > max_depth:
        return REDACTED
    if isinstance(value, str):
        redacted = value
        for pattern in SECRET_PATTERNS:
            redacted = pattern.sub(REDACTED, redacted)
        return redacted
    if isinstance(value, Mapping):
        return {
            str(key): REDACTED
            if is_sensitive_key(key)
            else redact_secrets(item, depth=depth + 1, max_depth=max_depth)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [
            redact_secrets(item, depth=depth + 1, max_depth=max_depth)
            for item in value
        ]
    return value


__all__ = [
    "REDACTED",
    "SECRET_PATTERNS",
    "SENSITIVE_KEY_MARKERS",
    "is_sensitive_key",
    "redact_secrets",
]
