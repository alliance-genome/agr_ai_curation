"""Canonical group context shared by TraceReview analysis views."""

from typing import Any, Dict


def group_context_from_metadata(metadata: Dict[str, Any]) -> Dict[str, Any]:
    """Read optional active groups without interpreting organization-owned IDs."""
    # Removed legacy active_mods read in ALL-1084: current trace producers emit active_groups.
    active_groups = metadata.get("active_groups") or []
    return {
        "active_groups": active_groups,
        "injection_active": bool(active_groups),
        "group_count": len(active_groups),
    }
