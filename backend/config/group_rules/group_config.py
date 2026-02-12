"""
Group-specific rule loading and prompt injection.

This module handles:
1. Loading group rules from the prompt cache (pre-rendered from database)
2. Formatting rules for prompt injection
3. Mapping Cognito groups to default organization groups
4. Injecting formatted rules into agent/tool prompts

Usage:
    from config.group_rules import inject_group_rules, get_groups_from_cognito

    # Inject MGI-specific rules into allele agent
    instructions = inject_group_rules(
        base_prompt=ALLELE_AGENT_INSTRUCTIONS,
        group_ids=["MGI"],
        component_type="agents",
        component_name="allele"
    )

    # Get user's default groups from their Cognito groups
    groups = get_groups_from_cognito(["mgi-curators", "developers"])
    # Returns: ["MGI"]
"""

import logging
from pathlib import Path
from typing import Dict, List, Optional, Set, TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from src.models.sql.prompts import PromptTemplate

logger = logging.getLogger(__name__)

# Base path for group rules (same directory as this module)
GROUP_RULES_PATH = Path(__file__).parent


# DEPRECATED: This hardcoded mapping is no longer used.
# Cognito-to-group mapping is now loaded from config/groups.yaml via groups_loader.
# This dict is kept for backwards compatibility with any code that might reference it directly.
# TODO: Remove in future version once all references are migrated.
COGNITO_GROUP_TO_GROUP: Dict[str, List[str]] = {
    # MOD-specific curator groups
    "mgi-curators": ["MGI"],
    "flybase-curators": ["FB"],
    "wormbase-curators": ["WB"],
    "zfin-curators": ["ZFIN"],
    "rgd-curators": ["RGD"],
    "sgd-curators": ["SGD"],
    "hgnc-curators": ["HGNC"],
    # Alliance-wide groups (no default group, user chooses)
    "alliance-admins": [],
    "developers": [],  # Dev users can set via DEV_USER_GROUPS env var
}


# Canonical group ID normalization
# Handles various ways users might specify group IDs
GROUP_ID_ALIASES: Dict[str, str] = {
    # MGI variants
    "mgi": "MGI",
    "mouse": "MGI",
    "mus": "MGI",
    # FlyBase variants
    "fb": "FB",
    "flybase": "FB",
    "fly": "FB",
    "drosophila": "FB",
    # WormBase variants
    "wb": "WB",
    "wormbase": "WB",
    "worm": "WB",
    "celegans": "WB",
    # ZFIN variants
    "zfin": "ZFIN",
    "zebrafish": "ZFIN",
    "danio": "ZFIN",
    # RGD variants
    "rgd": "RGD",
    "rat": "RGD",
    # SGD variants
    "sgd": "SGD",
    "yeast": "SGD",
    "saccharomyces": "SGD",
    # HGNC variants
    "hgnc": "HGNC",
    "human": "HGNC",
}


def normalize_group_id(group_id: str) -> str:
    """
    Normalize a group ID to its canonical form.

    Args:
        group_id: Group identifier (case-insensitive, supports aliases)

    Returns:
        Canonical group ID (e.g., "MGI", "FB", "WB")

    Examples:
        >>> normalize_group_id("mgi")
        "MGI"
        >>> normalize_group_id("flybase")
        "FB"
        >>> normalize_group_id("mouse")
        "MGI"
    """
    normalized = group_id.strip().lower()
    return GROUP_ID_ALIASES.get(normalized, group_id.upper())


def get_groups_from_cognito(cognito_groups: List[str]) -> List[str]:
    """
    Map Cognito group memberships to default organization group(s).

    This function delegates to groups_loader which reads from config/groups.yaml.
    The YAML file is the source of truth for Cognito-to-group mappings.

    Args:
        cognito_groups: List of Cognito group names user belongs to

    Returns:
        List of canonical group IDs to use as defaults

    Example:
        >>> get_groups_from_cognito(["mgi-curators", "developers"])
        ["MGI"]
        >>> get_groups_from_cognito(["alliance-admins"])
        []  # No default, user must choose
    """
    from src.lib.config.groups_loader import get_groups_for_cognito_groups

    return get_groups_for_cognito_groups(cognito_groups)


def inject_group_rules(
    base_prompt: str,
    group_ids: List[str],
    component_type: str,
    component_name: str,
    injection_marker: str = "## GROUP-SPECIFIC RULES",
    prompts_out: Optional[List["PromptTemplate"]] = None,
) -> str:
    """
    Inject group-specific rules into an agent/tool prompt.

    This function uses the prompt cache to get pre-rendered group rules.
    Group rules are stored in the database with prompt_type="group_rules".

    The prompt cache MUST be initialized before calling this function.
    There is no fallback - if the cache is not initialized, a RuntimeError is raised.

    Args:
        base_prompt: The base prompt to inject into
        group_ids: List of group identifiers (e.g., ["MGI", "FB"])
        component_type: Unused, kept for backwards compatibility
        component_name: Name of the agent or tool (maps to agent_name in cache)
        injection_marker: Where to inject (if present) or append
        prompts_out: Optional list to collect PromptTemplate objects used
                     (for execution logging via context tracking)

    Returns:
        Prompt with group rules injected

    Raises:
        RuntimeError: If prompt cache is not initialized

    Example:
        >>> prompts_used = []
        >>> instructions = inject_group_rules(
        ...     base_prompt=ALLELE_AGENT_INSTRUCTIONS,
        ...     group_ids=["MGI"],
        ...     component_type="agents",
        ...     component_name="allele",
        ...     prompts_out=prompts_used
        ... )
        >>> "MGI-SPECIFIC RULES" in instructions
        True
        >>> len(prompts_used)
        1
    """
    if not group_ids:
        logger.debug("No group IDs provided, returning base prompt unchanged")
        return base_prompt

    # Normalize all group IDs
    normalized_groups = [normalize_group_id(g) for g in group_ids]
    logger.info('Injecting rules for groups: %s', normalized_groups)

    # Load from cache (no fallback - cache must be initialized)
    from src.lib.prompts.cache import get_prompt_optional, is_initialized

    if not is_initialized():
        raise RuntimeError(
            "Prompt cache not initialized. Call initialize_prompt_cache() at startup."
        )

    return _inject_from_cache(
        base_prompt=base_prompt,
        normalized_groups=normalized_groups,
        component_name=component_name,
        injection_marker=injection_marker,
        prompts_out=prompts_out,
    )


def _inject_from_cache(
    base_prompt: str,
    normalized_groups: List[str],
    component_name: str,
    injection_marker: str,
    prompts_out: Optional[List["PromptTemplate"]] = None,
) -> str:
    """
    Inject group rules from the prompt cache.

    Group rules are stored with:
    - agent_name: component_name (e.g., "gene", "allele")
    - prompt_type: "group_rules"
    - group_id: normalized group ID (e.g., "MGI", "FB")
    """
    from src.lib.prompts.cache import get_prompt_optional

    collected_content = []
    collected_groups = []

    for group_id in normalized_groups:
        prompt = get_prompt_optional(component_name, "group_rules", group_id=group_id)
        if prompt:
            collected_content.append(prompt.content)
            collected_groups.append(group_id)
            if prompts_out is not None:
                prompts_out.append(prompt)
            logger.debug('Loaded %s rules for %s from cache (v%s)', group_id, component_name, prompt.version)
        else:
            logger.debug('No cached group rules found for %s/%s', component_name, group_id)

    if not collected_content:
        logger.warning('No group rules found in cache for %s/%s', normalized_groups, component_name)
        return base_prompt

    # Group rules are pre-rendered in the database, just concatenate them
    formatted_rules = "\n".join(collected_content)

    # Wrap in clear section markers
    group_list = ", ".join(collected_groups)
    injection_block = f"""
{injection_marker}

The following rules are specific to the organization group(s) you are working with: {group_list}
Apply these rules when searching for and interpreting results.

{formatted_rules}

## END GROUP-SPECIFIC RULES
"""

    # If marker exists in prompt, replace that section
    if injection_marker in base_prompt:
        logger.debug("Found injection marker, replacing at marker position")
        return base_prompt.replace(injection_marker, injection_block)
    else:
        # Append to end of prompt
        logger.debug("No injection marker found, appending to end of prompt")
        return base_prompt + "\n" + injection_block


def get_available_groups() -> List[str]:
    """
    Get list of all valid group IDs.

    This function delegates to groups_loader which reads from config/groups.yaml.
    The YAML file is the source of truth for available groups.

    Returns:
        List of group IDs defined in config/groups.yaml

    Example:
        >>> get_available_groups()
        ["FB", "HGNC", "MGI", "RGD", "SGD", "WB", "ZFIN"]
    """
    from src.lib.config.groups_loader import get_valid_group_ids

    return get_valid_group_ids()


def validate_group_rules(group_id: str, component_type: str, component_name: str) -> bool:
    """
    Validate that rules exist for a specific group/component combination.

    This function checks the prompt cache for group rules, which are loaded
    from config/agents/*/group_rules/*.yaml at startup.

    Args:
        group_id: Group identifier
        component_type: Unused (kept for backwards compatibility)
        component_name: Name of the agent or tool (maps to agent_name in cache)

    Returns:
        True if group rules exist in the prompt cache
    """
    from src.lib.prompts.cache import get_prompt_optional, is_initialized

    if not is_initialized():
        logger.warning("Prompt cache not initialized, cannot validate group rules")
        return False

    canonical_id = normalize_group_id(group_id)
    prompt = get_prompt_optional(component_name, "group_rules", group_id=canonical_id)

    return prompt is not None
