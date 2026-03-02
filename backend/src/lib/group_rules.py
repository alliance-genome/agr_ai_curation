"""Group-rule helpers for runtime prompt injection.

These helpers are intentionally located under ``src.lib`` to avoid import-path
collisions between the repository-level ``config/`` data directory and the
Python module used for group-rule logic.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from src.models.sql.prompts import PromptTemplate

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


def inject_group_rules(
    base_prompt: str,
    group_ids: List[str],
    component_type: str,
    component_name: str,
    injection_marker: str = "## GROUP-SPECIFIC RULES",
    prompts_out: Optional[List["PromptTemplate"]] = None,
) -> str:
    """Inject group-specific rules into a prompt from cached prompt templates."""
    del component_type

    if not group_ids:
        logger.debug("No group IDs provided, returning base prompt unchanged")
        return base_prompt

    normalized_groups = [normalize_group_id(g) for g in group_ids]
    logger.info("Injecting rules for groups: %s", normalized_groups)

    from src.lib.prompts.cache import get_prompt_optional, is_initialized

    if not is_initialized():
        raise RuntimeError(
            "Prompt cache not initialized. Call initialize_prompt_cache() at startup."
        )

    collected_content: List[str] = []
    collected_groups: List[str] = []

    for group_id in normalized_groups:
        prompt = get_prompt_optional(component_name, "group_rules", group_id=group_id)
        if not prompt:
            logger.debug("No cached group rules found for %s/%s", component_name, group_id)
            continue

        collected_content.append(prompt.content)
        collected_groups.append(group_id)
        if prompts_out is not None:
            prompts_out.append(prompt)
        logger.debug(
            "Loaded %s rules for %s from cache (v%s)",
            group_id,
            component_name,
            prompt.version,
        )

    if not collected_content:
        logger.warning(
            "No group rules found in cache for %s/%s",
            normalized_groups,
            component_name,
        )
        return base_prompt

    formatted_rules = "\n".join(collected_content)
    group_list = ", ".join(collected_groups)
    injection_block = f"""
{injection_marker}

The following rules are specific to the organization group(s) you are working with: {group_list}
Apply these rules when searching for and interpreting results.

{formatted_rules}

## END GROUP-SPECIFIC RULES
"""

    if injection_marker in base_prompt:
        logger.debug("Found injection marker, replacing at marker position")
        return base_prompt.replace(injection_marker, injection_block)

    logger.debug("No injection marker found, appending to end of prompt")
    return base_prompt + "\n" + injection_block


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
