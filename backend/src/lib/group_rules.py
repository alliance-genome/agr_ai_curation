"""Group-rule helpers for runtime prompt injection.

These helpers are intentionally located under ``src.lib`` to avoid import-path
collisions between the repository-level ``config/`` data directory and the
Python module used for group-rule logic.
"""

from __future__ import annotations

from typing import Dict, List


# Canonical group ID normalization.
GROUP_ID_ALIASES: Dict[str, str] = {
    "mgi": "MGI",
    "mouse": "MGI",
    "mus": "MGI",
    "fb": "FB",
    "flybase": "FB",
    "fly": "FB",
    "drosophila": "FB",
    "wb": "WB",
    "wormbase": "WB",
    "worm": "WB",
    "celegans": "WB",
    "zfin": "ZFIN",
    "zebrafish": "ZFIN",
    "danio": "ZFIN",
    "rgd": "RGD",
    "rat": "RGD",
    "sgd": "SGD",
    "yeast": "SGD",
    "saccharomyces": "SGD",
    "hgnc": "HGNC",
    "human": "HGNC",
}


def normalize_group_id(group_id: str) -> str:
    """Normalize a group ID to canonical form (e.g. ``mgi`` -> ``MGI``)."""
    normalized = group_id.strip().lower()
    return GROUP_ID_ALIASES.get(normalized, group_id.upper())


def get_groups_from_provider_groups(provider_groups: List[str]) -> List[str]:
    """Map identity-provider groups to default organization group IDs."""
    from src.lib.config.groups_loader import get_groups_for_provider_groups

    return get_groups_for_provider_groups(provider_groups)
