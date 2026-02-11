"""Application-level prompt cache.

Usage:
    from src.lib.prompts.cache import get_prompt, get_prompt_by_version

    # Anywhere in the codebase - no dependencies needed
    # Agent IDs match catalog_service.py AGENT_REGISTRY keys
    prompt = get_prompt("pdf")  # Base prompt (mod_id=None)
    prompt = get_prompt("gene", mod_id="FB")  # MOD-specific rules for FlyBase
    prompt = get_prompt_by_version("gene", version=3, mod_id="WB")  # Pinned version
"""

from typing import Dict, Optional
from datetime import datetime, timezone
import threading
import logging
from sqlalchemy.orm import Session

from .models import PromptTemplate
from .context import get_prompt_override

logger = logging.getLogger(__name__)


class PromptNotFoundError(Exception):
    """Raised when a required prompt is not found in the database."""

    pass


# Module-level state
_active_cache: Dict[str, PromptTemplate] = {}  # agent:type:mod -> active prompt
_version_cache: Dict[str, PromptTemplate] = {}  # agent:type:mod:version -> specific version
_lock = threading.Lock()
_initialized: bool = False
_loaded_at: Optional[datetime] = None


def initialize(db: Session) -> None:
    """
    Load all prompts into cache. Called once at application startup.

    Args:
        db: Database session for loading prompts
    """
    global _active_cache, _version_cache, _initialized, _loaded_at

    # Load all prompts (both active and historical for version pinning)
    all_prompts = db.query(PromptTemplate).all()

    new_active_cache: Dict[str, PromptTemplate] = {}
    new_version_cache: Dict[str, PromptTemplate] = {}

    for prompt in all_prompts:
        # Cache key includes group_id (or 'base' for NULL)
        group_key = prompt.group_id or "base"

        # Always add to version cache (for pinned flows)
        version_key = f"{prompt.agent_name}:{prompt.prompt_type}:{group_key}:v{prompt.version}"
        new_version_cache[version_key] = prompt

        # Add to active cache if it's the active version
        if prompt.is_active:
            active_key = f"{prompt.agent_name}:{prompt.prompt_type}:{group_key}"
            new_active_cache[active_key] = prompt

    with _lock:
        _active_cache = new_active_cache
        _version_cache = new_version_cache
        _initialized = True
        _loaded_at = datetime.now(timezone.utc)

    logger.info(
        f"Prompt cache initialized: {len(new_active_cache)} active prompts, "
        f"{len(new_version_cache)} total versions"
    )


def refresh(db: Session) -> None:
    """
    Refresh cache after admin updates. Atomic swap - non-disruptive.

    In-flight requests keep their old prompt references.
    New requests get new prompts immediately.
    """
    initialize(db)
    logger.info("Prompt cache refreshed")


def get_prompt(
    agent_name: str,
    prompt_type: str = "system",
    mod_id: Optional[str] = None,
) -> PromptTemplate:
    """
    Get the active prompt for an agent. Zero DB queries.

    Args:
        agent_name: Catalog ID, e.g., 'pdf', 'gene', 'supervisor'
        prompt_type: e.g., 'system' (default), 'group_rules'
        mod_id: e.g., 'FB', 'WB', 'MGI' (None for base prompts)

    Returns:
        PromptTemplate with content and version info

    Raises:
        RuntimeError: If cache not initialized
        PromptNotFoundError: If no active prompt exists (fail fast)
    """
    if not _initialized:
        raise RuntimeError(
            "Prompt cache not initialized. Call initialize() at startup."
        )

    override = get_prompt_override()
    if (
        override
        and prompt_type == "system"
        and override.agent_name == agent_name
    ):
        return _build_override_prompt_template(
            agent_name=agent_name,
            content=override.content,
            custom_agent_id=override.custom_agent_id,
        )

    # Cache key includes group ID for group-specific prompts
    key = f"{agent_name}:{prompt_type}:{mod_id or 'base'}"

    if key not in _active_cache:
        raise PromptNotFoundError(
            f"No active prompt found for agent='{agent_name}', "
            f"type='{prompt_type}', mod='{mod_id}'. Database prompts are required."
        )

    return _active_cache[key]


def get_prompt_by_version(
    agent_name: str,
    version: int,
    prompt_type: str = "system",
    mod_id: Optional[str] = None,
) -> PromptTemplate:
    """
    Get a specific version of a prompt (for pinned flows). Zero DB queries.

    Args:
        agent_name: Catalog ID, e.g., 'pdf', 'gene', 'supervisor'
        version: Specific version number to retrieve
        prompt_type: e.g., 'system' (default), 'group_rules'
        mod_id: e.g., 'FB', 'WB', 'MGI' (None for base prompts)

    Returns:
        PromptTemplate for the specified version

    Raises:
        RuntimeError: If cache not initialized
        PromptNotFoundError: If version doesn't exist
    """
    if not _initialized:
        raise RuntimeError(
            "Prompt cache not initialized. Call initialize() at startup."
        )

    key = f"{agent_name}:{prompt_type}:{mod_id or 'base'}:v{version}"

    if key not in _version_cache:
        raise PromptNotFoundError(
            f"Prompt version {version} not found for agent='{agent_name}', "
            f"type='{prompt_type}', mod='{mod_id}'."
        )

    return _version_cache[key]


def get_prompt_optional(
    agent_name: str,
    prompt_type: str = "system",
    mod_id: Optional[str] = None,
) -> Optional[PromptTemplate]:
    """
    Get the active prompt if it exists, None otherwise.

    Use this for optional prompts like MOD rules where missing is acceptable.

    Args:
        agent_name: Catalog ID, e.g., 'pdf', 'gene', 'supervisor'
        prompt_type: e.g., 'system' (default), 'group_rules'
        mod_id: e.g., 'FB', 'WB', 'MGI' (None for base prompts)

    Returns:
        PromptTemplate if found, None otherwise

    Raises:
        RuntimeError: If cache not initialized
    """
    if not _initialized:
        raise RuntimeError(
            "Prompt cache not initialized. Call initialize() at startup."
        )

    override = get_prompt_override()
    if (
        override
        and prompt_type == "system"
        and override.agent_name == agent_name
    ):
        return _build_override_prompt_template(
            agent_name=agent_name,
            content=override.content,
            custom_agent_id=override.custom_agent_id,
        )

    key = f"{agent_name}:{prompt_type}:{mod_id or 'base'}"
    return _active_cache.get(key)


def _build_override_prompt_template(
    agent_name: str,
    content: str,
    custom_agent_id: str,
) -> PromptTemplate:
    """Build an in-memory PromptTemplate object for custom-agent prompt overrides."""
    return PromptTemplate(
        id=None,
        agent_name=agent_name,
        prompt_type="system",
        group_id=None,
        content=content,
        version=1,
        is_active=True,
        created_at=datetime.now(timezone.utc),
        created_by="custom_agent",
        source_file=f"custom_agent:{custom_agent_id}",
        description="Runtime prompt override from custom agent",
    )


def get_cache_info() -> dict:
    """Return cache status for health checks."""
    return {
        "initialized": _initialized,
        "loaded_at": _loaded_at.isoformat() if _loaded_at else None,
        "active_prompts": len(_active_cache),
        "total_versions": len(_version_cache),
    }


def is_initialized() -> bool:
    """Check if the cache has been initialized."""
    return _initialized


def get_all_active_prompts() -> Dict[str, PromptTemplate]:
    """
    Get all active prompts from the cache.

    Used by catalog_service.py to build the prompt catalog.
    Returns a copy to prevent external modification.

    Returns:
        Dict mapping cache key to PromptTemplate.
        Keys are formatted as: agent_name:prompt_type:group_id_or_base

    Raises:
        RuntimeError: If cache not initialized
    """
    if not _initialized:
        raise RuntimeError(
            "Prompt cache not initialized. Call initialize() at startup."
        )
    return dict(_active_cache)
