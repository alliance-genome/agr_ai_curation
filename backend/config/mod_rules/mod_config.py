"""
MOD-specific rule loading and prompt injection.

This module handles:
1. Loading MOD rules from the prompt cache (pre-rendered from database)
2. Formatting rules for prompt injection
3. Mapping Cognito groups to default MODs
4. Injecting formatted rules into agent/tool prompts

Usage:
    from config.mod_rules.mod_config import inject_mod_rules, get_mods_from_cognito_groups

    # Inject MGI-specific rules into allele agent
    instructions = inject_mod_rules(
        base_prompt=ALLELE_AGENT_INSTRUCTIONS,
        mod_ids=["MGI"],
        component_type="agents",
        component_name="allele"
    )

    # Get user's default MODs from their Cognito groups
    mods = get_mods_from_cognito_groups(["mgi-curators", "developers"])
    # Returns: ["MGI"]
"""

import logging
from pathlib import Path
from typing import Dict, List, Optional, Set, TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from src.models.sql.prompts import PromptTemplate

logger = logging.getLogger(__name__)

# Base path for mod rules (same directory as this module)
MOD_RULES_PATH = Path(__file__).parent


# Cognito group → MOD mapping
# This maps Cognito group names to their associated MOD IDs
# Alliance-wide groups get empty list (user chooses per session)
COGNITO_GROUP_TO_MOD: Dict[str, List[str]] = {
    # MOD-specific curator groups
    "mgi-curators": ["MGI"],
    "flybase-curators": ["FB"],
    "wormbase-curators": ["WB"],
    "zfin-curators": ["ZFIN"],
    "rgd-curators": ["RGD"],
    "sgd-curators": ["SGD"],
    "hgnc-curators": ["HGNC"],
    # Alliance-wide groups (no default MOD, user chooses)
    "alliance-admins": [],
    "developers": [],  # Dev users can set via DEV_USER_MODS env var
}


# Canonical MOD ID normalization
# Handles various ways users might specify MOD IDs
MOD_ID_ALIASES: Dict[str, str] = {
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


def normalize_mod_id(mod_id: str) -> str:
    """
    Normalize a MOD ID to its canonical form.

    Args:
        mod_id: MOD identifier (case-insensitive, supports aliases)

    Returns:
        Canonical MOD ID (e.g., "MGI", "FB", "WB")

    Examples:
        >>> normalize_mod_id("mgi")
        "MGI"
        >>> normalize_mod_id("flybase")
        "FB"
        >>> normalize_mod_id("mouse")
        "MGI"
    """
    normalized = mod_id.strip().lower()
    return MOD_ID_ALIASES.get(normalized, mod_id.upper())


def get_mods_from_cognito_groups(groups: List[str]) -> List[str]:
    """
    Map Cognito group memberships to default MOD(s).

    Args:
        groups: List of Cognito group names user belongs to

    Returns:
        List of canonical MOD IDs to use as defaults

    Example:
        >>> get_mods_from_cognito_groups(["mgi-curators", "developers"])
        ["MGI"]
        >>> get_mods_from_cognito_groups(["alliance-admins"])
        []  # No default, user must choose
    """
    mods: Set[str] = set()

    for group in groups:
        group_lower = group.lower()
        if group_lower in COGNITO_GROUP_TO_MOD:
            mods.update(COGNITO_GROUP_TO_MOD[group_lower])

    return list(mods)


def inject_mod_rules(
    base_prompt: str,
    mod_ids: List[str],
    component_type: str,
    component_name: str,
    injection_marker: str = "## MOD-SPECIFIC RULES",
    prompts_out: Optional[List["PromptTemplate"]] = None,
) -> str:
    """
    Inject MOD-specific rules into an agent/tool prompt.

    This function uses the prompt cache to get pre-rendered MOD rules.
    MOD rules are stored in the database with prompt_type="mod_rules".

    The prompt cache MUST be initialized before calling this function.
    There is no fallback - if the cache is not initialized, a RuntimeError is raised.

    Args:
        base_prompt: The base prompt to inject into
        mod_ids: List of MOD identifiers (e.g., ["MGI", "FB"])
        component_type: Unused, kept for backwards compatibility
        component_name: Name of the agent or tool (maps to agent_name in cache)
        injection_marker: Where to inject (if present) or append
        prompts_out: Optional list to collect PromptTemplate objects used
                     (for execution logging via context tracking)

    Returns:
        Prompt with MOD rules injected

    Raises:
        RuntimeError: If prompt cache is not initialized

    Example:
        >>> prompts_used = []
        >>> instructions = inject_mod_rules(
        ...     base_prompt=ALLELE_AGENT_INSTRUCTIONS,
        ...     mod_ids=["MGI"],
        ...     component_type="agents",
        ...     component_name="allele",
        ...     prompts_out=prompts_used
        ... )
        >>> "MGI-SPECIFIC RULES" in instructions
        True
        >>> len(prompts_used)
        1
    """
    if not mod_ids:
        logger.debug("No MOD IDs provided, returning base prompt unchanged")
        return base_prompt

    # Normalize all MOD IDs
    normalized_mods = [normalize_mod_id(m) for m in mod_ids]
    logger.info(f"Injecting rules for MODs: {normalized_mods}")

    # Load from cache (no fallback - cache must be initialized)
    from src.lib.prompts.cache import get_prompt_optional, is_initialized

    if not is_initialized():
        raise RuntimeError(
            "Prompt cache not initialized. Call initialize_prompt_cache() at startup."
        )

    return _inject_from_cache(
        base_prompt=base_prompt,
        normalized_mods=normalized_mods,
        component_name=component_name,
        injection_marker=injection_marker,
        prompts_out=prompts_out,
    )


def _inject_from_cache(
    base_prompt: str,
    normalized_mods: List[str],
    component_name: str,
    injection_marker: str,
    prompts_out: Optional[List["PromptTemplate"]] = None,
) -> str:
    """
    Inject MOD rules from the prompt cache.

    MOD rules are stored with:
    - agent_name: component_name (e.g., "gene", "allele")
    - prompt_type: "mod_rules"
    - mod_id: normalized MOD ID (e.g., "MGI", "FB")
    """
    from src.lib.prompts.cache import get_prompt_optional

    collected_content = []
    collected_mods = []

    for mod_id in normalized_mods:
        prompt = get_prompt_optional(component_name, "mod_rules", mod_id=mod_id)
        if prompt:
            collected_content.append(prompt.content)
            collected_mods.append(mod_id)
            if prompts_out is not None:
                prompts_out.append(prompt)
            logger.debug(f"Loaded {mod_id} rules for {component_name} from cache (v{prompt.version})")
        else:
            logger.debug(f"No cached MOD rules found for {component_name}/{mod_id}")

    if not collected_content:
        logger.warning(f"No MOD rules found in cache for {normalized_mods}/{component_name}")
        return base_prompt

    # MOD rules are pre-rendered in the database, just concatenate them
    formatted_rules = "\n".join(collected_content)

    # Wrap in clear section markers
    mod_list = ", ".join(collected_mods)
    injection_block = f"""
{injection_marker}

The following rules are specific to the Model Organism Database(s) you are working with: {mod_list}
Apply these rules when searching for and interpreting results.

{formatted_rules}

## END MOD-SPECIFIC RULES
"""

    # If marker exists in prompt, replace that section
    if injection_marker in base_prompt:
        logger.debug("Found injection marker, replacing at marker position")
        return base_prompt.replace(injection_marker, injection_block)
    else:
        # Append to end of prompt
        logger.debug("No injection marker found, appending to end of prompt")
        return base_prompt + "\n" + injection_block


def get_available_mods() -> List[str]:
    """
    Get list of all MODs that have rules defined.

    Returns:
        List of MOD IDs with at least one rule file

    Example:
        >>> get_available_mods()
        ["MGI", "FB", "WB", ...]
    """
    available = set()

    # Check agents directory
    agents_path = MOD_RULES_PATH / "agents"
    if agents_path.exists():
        for component_dir in agents_path.iterdir():
            if component_dir.is_dir():
                for yaml_file in component_dir.glob("*.yaml"):
                    mod_id = yaml_file.stem.upper()
                    available.add(mod_id)

    # Check tools directory
    tools_path = MOD_RULES_PATH / "tools"
    if tools_path.exists():
        for component_dir in tools_path.iterdir():
            if component_dir.is_dir():
                for yaml_file in component_dir.glob("*.yaml"):
                    mod_id = yaml_file.stem.upper()
                    available.add(mod_id)

    return sorted(available)


def validate_mod_rules(mod_id: str, component_type: str, component_name: str) -> bool:
    """
    Validate that rules exist for a specific MOD/component combination.

    Args:
        mod_id: MOD identifier
        component_type: "agents" or "tools"
        component_name: Name of the agent or tool

    Returns:
        True if rules file exists and is valid YAML
    """
    canonical_id = normalize_mod_id(mod_id)
    filename = f"{canonical_id.lower()}.yaml"
    filepath = MOD_RULES_PATH / component_type / component_name / filename

    if not filepath.exists():
        return False

    try:
        with open(filepath, "r") as f:
            data = yaml.safe_load(f)
            return isinstance(data, dict) and "mod_id" in data
    except Exception:
        return False
