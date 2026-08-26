"""Canonical group-scoped availability policy for agents and flow recipes."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def normalize_allowed_group_ids(
    value: Any,
    *,
    field_name: str = "allowed_group_ids",
) -> list[str]:
    """Validate and deterministically order canonical server-side group IDs."""

    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list of canonical group IDs")

    # The policy module is deliberately outside ``src.lib.config`` so package
    # schemas can import it during package initialization. Resolve the registry
    # only when validating concrete values, after module discovery is complete.
    from src.lib.config.groups_loader import get_valid_group_ids

    valid_group_ids = get_valid_group_ids()
    valid_group_id_set = set(valid_group_ids)
    normalized: list[str] = []
    for index, raw_group_id in enumerate(value):
        if not isinstance(raw_group_id, str) or not raw_group_id:
            raise ValueError(
                f"{field_name}[{index}] must be a non-empty canonical group ID"
            )
        if raw_group_id != raw_group_id.strip():
            raise ValueError(
                f"{field_name}[{index}] must not contain surrounding whitespace"
            )
        if raw_group_id not in valid_group_id_set:
            raise ValueError(
                f"Unknown group ID '{raw_group_id}' in {field_name}; valid IDs are: "
                + ", ".join(valid_group_ids)
            )
        if raw_group_id in normalized:
            raise ValueError(
                f"{field_name} must not contain duplicate group ID '{raw_group_id}'"
            )
        normalized.append(raw_group_id)

    requested = set(normalized)
    return [group_id for group_id in valid_group_ids if group_id in requested]


def is_group_access_allowed(
    allowed_group_ids: Iterable[str],
    active_group_ids: Iterable[str],
) -> bool:
    """Return the effective group match; an empty allow-list is unrestricted."""

    allowed = set(allowed_group_ids)
    return not allowed or bool(allowed.intersection(active_group_ids))


def require_allowed_group_ids_narrowing(
    source_allowed_group_ids: list[str],
    requested_allowed_group_ids: list[str],
    *,
    source_name: str = "source",
) -> list[str]:
    """Reject removal or broadening of a non-empty inherited restriction."""

    source = normalize_allowed_group_ids(
        source_allowed_group_ids,
        field_name=f"{source_name}.allowed_group_ids",
    )
    requested = normalize_allowed_group_ids(requested_allowed_group_ids)
    if not source:
        return requested

    added = sorted(set(requested) - set(source))
    if not requested or added:
        detail = (
            "unrestricted access"
            if not requested
            else f"additional groups: {', '.join(added)}"
        )
        raise ValueError(
            f"allowed_group_ids cannot widen the inherited restriction from "
            f"{source_name} ({detail}); allowed values are: {', '.join(source)}"
        )
    return requested
