"""
Group-specific rule loading and prompt injection.

This module handles:
1. Loading group rules from the prompt cache (pre-rendered from database)
2. Formatting rules for prompt injection
3. Injecting formatted rules into agent/tool prompts

Usage:
    from config.group_rules import inject_group_rules

    # Inject MGI-specific rules into allele agent
    instructions = inject_group_rules(
        base_prompt=ALLELE_AGENT_INSTRUCTIONS,
        group_ids=["MGI"],
        component_type="agents",
        component_name="allele"
    )
"""

import logging
from typing import List, Optional, TYPE_CHECKING

from src.lib.group_rules import normalize_group_id

if TYPE_CHECKING:
    from src.models.sql.prompts import PromptTemplate

logger = logging.getLogger(__name__)


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
    Rules come from manifest-declared package agent bundles and explicit overrides.

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
    from src.lib.prompts.cache import is_initialized

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
