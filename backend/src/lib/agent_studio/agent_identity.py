"""Package-configured canonical public identity rules for Agent Studio agents."""

from __future__ import annotations

def retired_agent_id_replacement(value: object) -> str | None:
    """Return the package-declared canonical replacement for a retired ID."""
    from src.lib.config.agent_loader import get_retired_agent_id_replacements

    normalized = str(value or "").strip()
    return get_retired_agent_id_replacements().get(normalized)


def require_canonical_agent_identity(value: object, *, field_name: str) -> str:
    """Return a normalized identity or reject a package-declared retired ID."""

    normalized = str(value or "").strip()
    replacement = retired_agent_id_replacement(normalized)
    if replacement is not None:
        raise ValueError(
            f"{field_name} '{normalized}' is a retired agent ID; "
            f"use '{replacement}'."
        )
    return normalized
