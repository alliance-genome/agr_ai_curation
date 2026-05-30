"""Fail-fast environment helpers for model / LLM configuration.

`.env` is the single source of truth for all model/LLM configuration. There are
intentionally NO hardcoded model/LLM fallbacks in code: when a required value is
missing the helpers raise :class:`ConfigError` so the misconfiguration surfaces
immediately and gets fixed, instead of silently running on the wrong model.

The only place a model literal default may live is a package ``agent.yaml``
(``model_config``), which is the per-package configuration contract.
"""

from __future__ import annotations

import os
from typing import Optional, Sequence


class ConfigError(RuntimeError):
    """Raised when a required model/LLM configuration value is missing or invalid."""


def require_env(key: str, *, hint: Optional[str] = None) -> str:
    """Return a required env var, or raise ConfigError. No code fallback."""
    value = os.getenv(key)
    if value is None or not value.strip():
        suffix = f" {hint}" if hint else ""
        raise ConfigError(
            f"Required environment variable '{key}' is not set. Model/LLM "
            f"configuration must be defined in .env (no hardcoded fallback)."
            f"{suffix}"
        )
    return value.strip()


def require_env_choice(key: str, allowed: Sequence[str]) -> str:
    """Return a required env var constrained to ``allowed``, or raise ConfigError."""
    value = require_env(key)
    if value not in allowed:
        raise ConfigError(
            f"Environment variable '{key}'={value!r} is invalid; "
            f"expected one of {tuple(allowed)}."
        )
    return value


def optional_env_float(key: str) -> Optional[float]:
    """Return a float env var if set, else None. Raises if set but unparseable."""
    raw = os.getenv(key)
    if raw is None or not raw.strip():
        return None
    try:
        return float(raw.strip())
    except ValueError as exc:
        raise ConfigError(
            f"Environment variable '{key}'={raw!r} is not a valid float."
        ) from exc
