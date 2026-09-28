"""Central secret classification and mechanical redaction primitives."""

from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
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
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bpk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    # Header context is sufficient even when the credential is short or opaque.
    # Accept plain headers and stringified JSON/dict headers.
    re.compile(r"(?i)\bauthorization[\"']?[ \t]*:[ \t]*[\"']?(?:bearer|basic)[ \t]+[^\s,;\"']+"),
    # KANBAN-1771 (v0.9.18). Sentry content redaction is now off by default, so
    # these patterns are the only backstop for a credential VALUE under an
    # ordinary key, such as a SQLAlchemy connection error that stringifies its
    # DSN. They also apply to application log lines via redact_secrets. Each
    # matches a genuine credential shape, never ordinary curator content.
    # URL userinfo only: between "//" and "@", with no "/", "?" or "#" in it,
    # per RFC 3986. That keeps a host:port?query@... URL intact, allows an
    # empty username (redis://:password@host), and leaves the scheme, "@" and
    # host visible so a redacted DSN still says which server it was.
    re.compile(r"(?<=//)[^:@/?#\s]*:[^@/?#\s]*(?=@)"),
    re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]+"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"AIza[0-9A-Za-z_-]{35}"),
    # The whole key, not just its header line: the base64 body is the secret.
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL),
)

_AUTH_SCHEME_PATTERN = re.compile(
    r"\b(bearer|basic)[ \t]+([A-Za-z0-9._~+/-]+=*)(?![\w=])", re.IGNORECASE
)


def _redact_auth_credential(match: re.Match[str]) -> str:
    """Distinguish standalone auth values from words such as 'Basic cellular'."""
    scheme, credential = match.groups()
    if scheme.lower() == "basic":
        try:
            decoded = base64.b64decode(credential, validate=True)
        except (binascii.Error, ValueError):
            return match.group()
        # Basic encodes user:password; alphabet/length alone also matches prose.
        return REDACTED if b":" in decoded else match.group()

    # A standalone opaque bearer value must be token-length and contain more
    # than an ordinary single-case word. This is a credential-shape heuristic,
    # not an execution limit. Header values are scrubbed regardless of shape.
    if len(credential) >= 16 and (
        not credential.isalpha()
        or (not credential.islower() and not credential.isupper() and not credential.istitle())
    ):
        return REDACTED
    return match.group()


_ACTIVE_SECRET_VALUES: ContextVar[tuple[str, ...]] = ContextVar(
    "active_secret_values", default=()
)


@contextmanager
def active_secret_redaction(secret: str) -> Iterator[None]:
    """Mechanically scrub an opaque secret for the active request boundary."""

    if not secret:
        raise ValueError("active redaction secret is required")
    token = _ACTIVE_SECRET_VALUES.set((*_ACTIVE_SECRET_VALUES.get(), secret))
    try:
        yield
    finally:
        _ACTIVE_SECRET_VALUES.reset(token)


def _redact_active_secrets(value: str) -> str:
    redacted = value
    for secret in sorted(set(_ACTIVE_SECRET_VALUES.get()), key=len, reverse=True):
        redacted = redacted.replace(secret, REDACTED)
    return redacted


def is_sensitive_key(key: object) -> bool:
    """Return whether a field/header name identifies secret-bearing data."""

    normalized = str(key).lower().replace("-", "_")
    return any(marker in normalized for marker in SENSITIVE_KEY_MARKERS)


def redact_secrets(value: Any, *, depth: int = 0, max_depth: int = 8) -> Any:
    """Redact secret fields and credential-shaped strings in nested values."""

    if depth > max_depth:
        return REDACTED
    if isinstance(value, str):
        redacted = _redact_active_secrets(value)
        redacted = _AUTH_SCHEME_PATTERN.sub(_redact_auth_credential, redacted)
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
    "active_secret_redaction",
    "is_sensitive_key",
    "redact_secrets",
]
