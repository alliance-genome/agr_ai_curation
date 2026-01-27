"""
MOD-specific rules package.

This package contains YAML configuration files for Model Organism Database
(MOD) specific rules that are injected into agents and tools at runtime.

Usage:
    from config.mod_rules.mod_config import inject_mod_rules, get_mods_from_cognito_groups

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
    mod_config.py   # Loader and injection logic
"""

from .mod_config import (
    inject_mod_rules,
    get_mods_from_cognito_groups,
    normalize_mod_id,
    get_available_mods,
    validate_mod_rules,
)

__all__ = [
    "inject_mod_rules",
    "get_mods_from_cognito_groups",
    "normalize_mod_id",
    "get_available_mods",
    "validate_mod_rules",
]
