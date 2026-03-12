"""
Group Definition Loader for Config-Driven Architecture.

This module loads group definitions from runtime config or config/groups.yaml.

Groups enable:
- Mapping identity provider groups to internal group IDs
- Group-specific rules injection into agent prompts
- Organization-specific customization

Usage:
    from src.lib.config import load_groups, get_group, get_group_for_provider_group

    # Load all groups at startup
    groups = load_groups()

    # Get a specific group
    fb_group = get_group("FB")

    # Map identity provider group to internal group ID
    group_id = get_group_for_provider_group("flybase-curators")  # Returns "FB"
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from src.lib.config.package_default_sources import resolve_runtime_config_path

logger = logging.getLogger(__name__)


def _get_default_groups_path() -> Path:
    """
    Get the default groups.yaml path, preferring runtime config.

    Order of precedence:
    1. GROUPS_CONFIG_PATH environment variable
    2. Runtime config directory (`AGR_RUNTIME_CONFIG_DIR` or `/runtime/config`)
    3. Project root fallback for repository-backed development
    """
    resolved_path, _ = resolve_runtime_config_path(
        explicit_path=None,
        env_var="GROUPS_CONFIG_PATH",
        filename="groups.yaml",
    )
    return resolved_path


DEFAULT_GROUPS_PATH = _get_default_groups_path()
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
        provider_groups: List of identity provider groups mapped to this group
    """

    group_id: str
    name: str
    description: str = ""
    species: Optional[str] = None
    taxon: Optional[str] = None
    provider_groups: List[str] = field(default_factory=list)

    @property
    def cognito_groups(self) -> List[str]:
        """Deprecated alias for backwards compatibility."""
        return self.provider_groups

    @cognito_groups.setter
    def cognito_groups(self, value: List[str]) -> None:
        """Deprecated alias for backwards compatibility."""
        self.provider_groups = value

    @classmethod
    def from_yaml(cls, group_id: str, data: Dict[str, Any]) -> "GroupDefinition":
        """Create a GroupDefinition from parsed YAML data."""
        if "provider_groups" not in data:
            raise ValueError(
                f"Group '{group_id}' is missing required 'provider_groups' field"
            )
        provider_groups = data.get("provider_groups")
        if not isinstance(provider_groups, list):
            raise ValueError(
                f"Group '{group_id}' field 'provider_groups' must be a list"
            )
        return cls(
            group_id=group_id,
            name=data.get("name", group_id),
            description=data.get("description", "").strip(),
            species=data.get("species"),
            taxon=data.get("taxon"),
            provider_groups=provider_groups,
        )


_group_registry: Dict[str, GroupDefinition] = {}
_provider_to_group: Dict[str, str] = {}
_valid_group_ids: List[str] = []
_identity_provider_type: Optional[str] = None
_group_claim_key: Optional[str] = None
_initialized: bool = False


def load_groups(
    groups_path: Optional[Path] = None,
    force_reload: bool = False,
) -> Dict[str, GroupDefinition]:
    """
    Load group definitions from groups.yaml.

    This function is thread-safe; concurrent calls will block until
    initialization is complete.
    """
    global _group_registry, _provider_to_group, _valid_group_ids
    global _identity_provider_type, _group_claim_key, _initialized

    with _init_lock:
        if _initialized and not force_reload:
            return _group_registry

        if groups_path is None:
            groups_path = _get_default_groups_path()

        if not groups_path.exists():
            raise FileNotFoundError(f"Groups configuration not found: {groups_path}")

        logger.info("Loading group definitions from: %s", groups_path)

        with open(groups_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        if not data:
            raise ValueError(
                f"Groups configuration is empty: {groups_path}. "
                "Define identity_provider and groups explicitly."
            )
        if "groups" not in data:
            raise ValueError(
                f"Groups configuration missing required top-level 'groups' key: {groups_path}"
            )
        identity_provider = data.get("identity_provider")
        if not isinstance(identity_provider, dict):
            raise ValueError(
                f"Groups configuration missing required 'identity_provider' section: {groups_path}"
            )

        provider_type = str(identity_provider.get("type", "")).strip()
        group_claim = str(identity_provider.get("group_claim", "")).strip()
        if not provider_type:
            raise ValueError(
                f"Groups configuration requires identity_provider.type: {groups_path}"
            )
        if not group_claim:
            raise ValueError(
                f"Groups configuration requires identity_provider.group_claim: {groups_path}"
            )

        _identity_provider_type = provider_type
        _group_claim_key = group_claim

        _group_registry = {}
        _provider_to_group = {}
        _valid_group_ids = []

        groups_data = data.get("groups", {})
        for group_id, group_data in groups_data.items():
            try:
                group = GroupDefinition.from_yaml(group_id, group_data)
                _group_registry[group_id] = group
                _valid_group_ids.append(group_id)

                for provider_group in group.provider_groups:
                    if provider_group in _provider_to_group:
                        logger.warning(
                            "Provider group '%s' mapped to multiple group IDs: %s and %s",
                            provider_group,
                            _provider_to_group[provider_group],
                            group_id,
                        )
                    _provider_to_group[provider_group] = group_id

                logger.info(
                    "Loaded group: %s (%s) - provider_groups=%s",
                    group_id,
                    group.name,
                    group.provider_groups,
                )
            except Exception as exc:
                logger.error("Failed to load group %s: %s", group_id, exc)
                raise

        _initialized = True
        logger.info(
            "Loaded %s group definitions (identity_provider=%s, group_claim=%s)",
            len(_group_registry),
            _identity_provider_type,
            _group_claim_key,
        )
        return _group_registry


def get_group(group_id: str) -> Optional[GroupDefinition]:
    """Get a group definition by its group ID."""
    if not _initialized:
        load_groups()
    return _group_registry.get(group_id)


def get_group_for_provider_group(provider_group: str) -> Optional[str]:
    """Map provider group name to internal group ID."""
    if not _initialized:
        load_groups()
    return _provider_to_group.get(provider_group)


def get_groups_for_provider_groups(provider_groups: List[str]) -> List[str]:
    """Map multiple provider groups to internal group IDs."""
    if not _initialized:
        load_groups()

    seen = set()
    result: List[str] = []
    for provider_group in provider_groups:
        group_id = _provider_to_group.get(provider_group)
        if group_id and group_id not in seen:
            seen.add(group_id)
            result.append(group_id)
    return result


def list_groups() -> List[GroupDefinition]:
    """List all loaded group definitions."""
    if not _initialized:
        load_groups()
    return list(_group_registry.values())


def get_valid_group_ids() -> List[str]:
    """Get list of valid group IDs."""
    if not _initialized:
        load_groups()
    return _valid_group_ids.copy()


def get_provider_to_group_mapping() -> Dict[str, str]:
    """Get full provider-group to internal-group mapping."""
    if not _initialized:
        load_groups()
    return _provider_to_group.copy()


def get_identity_provider_type() -> str:
    """Return configured identity provider type."""
    if not _initialized:
        load_groups()
    if not _identity_provider_type:
        raise RuntimeError("Group configuration not initialized with identity provider type")
    return _identity_provider_type


def get_group_claim_key() -> str:
    """Return configured JWT claim name for group membership."""
    if not _initialized:
        load_groups()
    if not _group_claim_key:
        raise RuntimeError("Group configuration not initialized with group claim key")
    return _group_claim_key


def get_group_for_cognito_group(cognito_group: str) -> Optional[str]:
    """Deprecated wrapper for backwards compatibility."""
    logger.warning(
        "get_group_for_cognito_group() is deprecated; use get_group_for_provider_group()."
    )
    return get_group_for_provider_group(cognito_group)


def get_groups_for_cognito_groups(cognito_groups: List[str]) -> List[str]:
    """Deprecated wrapper for backwards compatibility."""
    logger.warning(
        "get_groups_for_cognito_groups() is deprecated; use "
        "get_groups_for_provider_groups()."
    )
    return get_groups_for_provider_groups(cognito_groups)


def get_cognito_to_group_mapping() -> Dict[str, str]:
    """Deprecated wrapper for backwards compatibility."""
    logger.warning(
        "get_cognito_to_group_mapping() is deprecated; use "
        "get_provider_to_group_mapping()."
    )
    return get_provider_to_group_mapping()


def is_initialized() -> bool:
    """Check whether groups are loaded."""
    return _initialized


def reset_cache() -> None:
    """Reset loaded groups cache (primarily for tests)."""
    global _group_registry, _provider_to_group, _valid_group_ids
    global _identity_provider_type, _group_claim_key, _initialized
    _group_registry = {}
    _provider_to_group = {}
    _valid_group_ids = []
    _identity_provider_type = None
    _group_claim_key = None
    _initialized = False
