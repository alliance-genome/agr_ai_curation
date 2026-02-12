"""
Group Definition Loader for Config-Driven Architecture.

This module loads group definitions from config/groups.yaml. Groups define
organizational units (formerly called "MODs") with Cognito mapping.

Groups enable:
- Mapping Cognito groups to internal group IDs
- Group-specific rules injection into agent prompts
- Organization-specific customization

Usage:
    from src.lib.config import load_groups, get_group, get_group_for_cognito_group

    # Load all groups at startup
    groups = load_groups()

    # Get a specific group
    fb_group = get_group("FB")

    # Map Cognito group to internal group ID
    group_id = get_group_for_cognito_group("flybase-curators")  # Returns "FB"
"""

import logging
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Any

import yaml

logger = logging.getLogger(__name__)


def _find_project_root() -> Optional[Path]:
    """Find project root by looking for pyproject.toml or docker-compose.yml.

    Returns:
        Path to project root directory, or None if not found
    """
    current = Path(__file__).resolve()
    for parent in [current] + list(current.parents):
        if (parent / "pyproject.toml").exists() or (parent / "docker-compose.yml").exists():
            return parent
    return None


def _get_default_groups_path() -> Path:
    """Get the default groups.yaml path, trying multiple strategies.

    Order of precedence:
    1. GROUPS_CONFIG_PATH environment variable
    2. Project root detection (pyproject.toml or docker-compose.yml)
    3. Relative path from this module (fallback for Docker)

    Returns:
        Path to groups.yaml file
    """
    # Strategy 1: Environment variable
    env_path = os.environ.get("GROUPS_CONFIG_PATH")
    if env_path:
        return Path(env_path)

    # Strategy 2: Project root detection
    project_root = _find_project_root()
    if project_root:
        return project_root / "config" / "groups.yaml"

    # Strategy 3: Relative path fallback (for Docker where backend is at /app/backend)
    return Path(__file__).parent.parent.parent.parent.parent / "config" / "groups.yaml"


# Default path for groups configuration
DEFAULT_GROUPS_PATH = _get_default_groups_path()

# Thread safety lock for initialization
_init_lock = threading.Lock()


@dataclass
class GroupDefinition:
    """
    Group definition loaded from groups.yaml.

    Attributes:
        group_id: Unique identifier (e.g., "FB", "WB", "MGI")
        name: Human-readable name (e.g., "FlyBase")
        description: Brief description of the group
        species: Primary model organism (optional)
        taxon: NCBI Taxon ID (optional)
        cognito_groups: List of Cognito groups that map to this group ID
    """

    group_id: str
    name: str
    description: str = ""
    species: Optional[str] = None
    taxon: Optional[str] = None
    cognito_groups: List[str] = field(default_factory=list)

    @classmethod
    def from_yaml(cls, group_id: str, data: Dict[str, Any]) -> "GroupDefinition":
        """
        Create a GroupDefinition from parsed YAML data.

        Args:
            group_id: The group ID (e.g., "FB")
            data: Parsed YAML dictionary for this group

        Returns:
            GroupDefinition instance
        """
        return cls(
            group_id=group_id,
            name=data.get("name", group_id),
            description=data.get("description", "").strip(),
            species=data.get("species"),
            taxon=data.get("taxon"),
            cognito_groups=data.get("cognito_groups", []),
        )


# Module-level cache for loaded groups
_group_registry: Dict[str, GroupDefinition] = {}
_cognito_to_group: Dict[str, str] = {}  # cognito_group -> group_id
_valid_group_ids: List[str] = []
_initialized: bool = False


def load_groups(
    groups_path: Optional[Path] = None,
    force_reload: bool = False,
) -> Dict[str, GroupDefinition]:
    """
    Load group definitions from groups.yaml.

    This function is thread-safe; concurrent calls will block until
    initialization is complete.

    Args:
        groups_path: Path to groups.yaml (default: config/groups.yaml)
        force_reload: Force reload even if already initialized

    Returns:
        Dictionary mapping group_id to GroupDefinition

    Raises:
        FileNotFoundError: If groups_path doesn't exist
        yaml.YAMLError: If YAML parsing fails
    """
    global _group_registry, _cognito_to_group, _valid_group_ids, _initialized

    # Thread-safe initialization
    with _init_lock:
        if _initialized and not force_reload:
            return _group_registry

        if groups_path is None:
            groups_path = DEFAULT_GROUPS_PATH

        if not groups_path.exists():
            raise FileNotFoundError(f"Groups configuration not found: {groups_path}")

        logger.info('Loading group definitions from: %s', groups_path)

        with open(groups_path, "r") as f:
            data = yaml.safe_load(f)

        if not data or "groups" not in data:
            logger.warning('No groups defined in %s', groups_path)
            _group_registry = {}
            _cognito_to_group = {}
            _valid_group_ids = []
            _initialized = True
            return _group_registry

        _group_registry = {}
        _cognito_to_group = {}
        _valid_group_ids = []

        groups_data = data.get("groups", {})
        for group_id, group_data in groups_data.items():
            try:
                group = GroupDefinition.from_yaml(group_id, group_data)
                _group_registry[group_id] = group
                _valid_group_ids.append(group_id)

                # Build reverse mapping from Cognito groups to group ID
                for cognito_group in group.cognito_groups:
                    if cognito_group in _cognito_to_group:
                        logger.warning(
                            f"Cognito group '{cognito_group}' mapped to multiple group IDs: "
                            f"{_cognito_to_group[cognito_group]} and {group_id}"
                        )
                    _cognito_to_group[cognito_group] = group_id

                logger.info(
                    f"Loaded group: {group_id} ({group.name}) - "
                    f"cognito_groups={group.cognito_groups}"
                )

            except Exception as e:
                logger.error('Failed to load group %s: %s', group_id, e)
                raise

        _initialized = True
        logger.info('Loaded %s group definitions', len(_group_registry))

        return _group_registry


def get_group(group_id: str) -> Optional[GroupDefinition]:
    """
    Get a group definition by its group ID.

    Args:
        group_id: The group identifier (e.g., "FB", "WB")

    Returns:
        GroupDefinition or None if not found
    """
    if not _initialized:
        load_groups()

    return _group_registry.get(group_id)


def get_group_for_cognito_group(cognito_group: str) -> Optional[str]:
    """
    Map a Cognito group name to an internal group ID.

    Args:
        cognito_group: The Cognito group name (e.g., "flybase-curators")

    Returns:
        Group ID (e.g., "FB") or None if not mapped
    """
    if not _initialized:
        load_groups()

    return _cognito_to_group.get(cognito_group)


def get_groups_for_cognito_groups(cognito_groups: List[str]) -> List[str]:
    """
    Map multiple Cognito groups to internal group IDs.

    Args:
        cognito_groups: List of Cognito group names

    Returns:
        List of unique group IDs (duplicates removed, order preserved)
    """
    if not _initialized:
        load_groups()

    seen = set()
    result = []
    for cognito_group in cognito_groups:
        group_id = _cognito_to_group.get(cognito_group)
        if group_id and group_id not in seen:
            seen.add(group_id)
            result.append(group_id)

    return result


def list_groups() -> List[GroupDefinition]:
    """
    List all loaded group definitions.

    Returns:
        List of GroupDefinition objects
    """
    if not _initialized:
        load_groups()

    return list(_group_registry.values())


def get_valid_group_ids() -> List[str]:
    """
    Get list of valid group IDs.

    This replaces the VALID_MOD_IDS environment variable usage.

    Returns:
        List of valid group ID strings
    """
    if not _initialized:
        load_groups()

    return _valid_group_ids.copy()


def get_cognito_to_group_mapping() -> Dict[str, str]:
    """
    Get the complete Cognito group to internal group ID mapping.

    Returns:
        Dictionary mapping Cognito group names to group IDs
    """
    if not _initialized:
        load_groups()

    return _cognito_to_group.copy()


def is_initialized() -> bool:
    """Check if groups have been loaded."""
    return _initialized


def reset_cache() -> None:
    """Reset the groups cache (for testing)."""
    global _group_registry, _cognito_to_group, _valid_group_ids, _initialized
    _group_registry = {}
    _cognito_to_group = {}
    _valid_group_ids = []
    _initialized = False
