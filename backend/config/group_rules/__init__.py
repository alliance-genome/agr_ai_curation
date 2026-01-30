"""
Group-specific rules package.

This package contains YAML configuration files for group-specific rules
(MODs, institutions, teams, etc.) that are injected into agents and tools at runtime.

Usage:
    from config.group_rules import inject_group_rules, get_groups_from_cognito

Structure:
    agents/
        allele/
            mgi.yaml      # Mouse allele rules
            flybase.yaml  # Fly allele rules
            ...
        gene/
            mgi.yaml
            ...
    tools/
        agr_curation/
            ...
    group_config.py   # Loader and injection logic
"""

from .group_config import (
    inject_group_rules,
    get_groups_from_cognito,
    normalize_group_id,
    get_available_groups,
    validate_group_rules,
)

__all__ = [
    "inject_group_rules",
    "get_groups_from_cognito",
    "normalize_group_id",
    "get_available_groups",
    "validate_group_rules",
]
