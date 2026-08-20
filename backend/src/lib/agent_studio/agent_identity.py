"""Canonical public identity rules for Agent Studio agents."""

from __future__ import annotations


RETIRED_VALIDATOR_AGENT_ALIASES = frozenset(
    {"gene", "allele", "disease", "chemical"}
)


def require_canonical_agent_identity(value: object, *, field_name: str) -> str:
    """Return a normalized agent identity or reject a retired validator alias."""

    normalized = str(value or "").strip()
    if normalized in RETIRED_VALIDATOR_AGENT_ALIASES:
        raise ValueError(
            f"{field_name} '{normalized}' is a retired validator alias; "
            f"use '{normalized}_validation'."
        )
    return normalized
