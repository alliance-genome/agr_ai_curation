"""Group-rule helpers for runtime prompt injection.

These helpers are intentionally located under ``src.lib`` to avoid import-path
collisions between the repository-level ``config/`` data directory and the
Python module used for group-rule logic.
"""

from __future__ import annotations

import logging
from typing import Dict, List

logger = logging.getLogger(__name__)


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


def get_groups_from_cognito(cognito_groups: List[str]) -> List[str]:
    """Backward-compatible wrapper for legacy call-sites."""
    logger.warning(
        "get_groups_from_cognito() is deprecated; use get_groups_from_provider_groups()."
    )
    return get_groups_from_provider_groups(cognito_groups)


def get_available_groups() -> List[str]:
    """Return the valid group IDs from configured group definitions."""
    from src.lib.config.groups_loader import get_valid_group_ids

    return get_valid_group_ids()


def validate_group_rules(group_id: str, component_type: str, component_name: str) -> bool:
    """Check whether group rules exist in prompt cache for a component."""
    del component_type

    from src.lib.prompts.cache import get_prompt_optional, is_initialized

    if not is_initialized():
        logger.warning("Prompt cache not initialized, cannot validate group rules")
        return False

    canonical_id = normalize_group_id(group_id)
    prompt = get_prompt_optional(component_name, "group_rules", group_id=canonical_id)
    return prompt is not None
