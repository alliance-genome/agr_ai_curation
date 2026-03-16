"""Public AGR curation runtime helpers for package-owned tools.

This boundary exposes the three backend-owned services the packaged AGR
curation tool needs at runtime:
- curation DB connection resolution,
- CURIE prefix validation, and
- group configuration loading for provider-to-taxon mapping.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any


def _load_curation_resolver_module() -> Any:
    """Resolve the backend curation resolver lazily."""
    return import_module("src.lib.database.curation_resolver")


def _load_identifier_validation_module() -> Any:
    """Resolve the backend identifier validator lazily."""
    return import_module("src.lib.identifier_validation")


def _load_groups_loader_module() -> Any:
    """Resolve the backend groups loader lazily."""
    return import_module("src.lib.config.groups_loader")


def get_curation_resolver() -> Any:
    """Return the canonical curation resolver singleton."""
    return _load_curation_resolver_module().get_curation_resolver()


def is_valid_curie(curie: str) -> bool:
    """Validate a CURIE against the loaded runtime prefix state."""
    return _load_identifier_validation_module().is_valid_curie(curie)


def list_groups() -> Any:
    """Load group definitions from runtime/project config."""
    return _load_groups_loader_module().list_groups()


__all__ = [
    "get_curation_resolver",
    "is_valid_curie",
    "list_groups",
]
