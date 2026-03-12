"""
Identifier validation utility.

Loads allowed CURIE prefixes from runtime state.
This is a hard requirement: if the file is missing or unreadable, an exception is raised.
"""
from __future__ import annotations

import json
import logging
from functools import lru_cache
from typing import Set
from src.lib.packages.paths import get_identifier_prefix_file_path

logger = logging.getLogger(__name__)


class PrefixLoadError(RuntimeError):
    pass


def get_prefix_file_path():
    """Return the active identifier-prefix JSON path for this runtime."""
    return get_identifier_prefix_file_path()


@lru_cache(maxsize=1)
def load_prefixes() -> Set[str]:
    """Load allowed prefixes from JSON; raise if missing/unreadable."""
    prefix_file = get_prefix_file_path()
    if not prefix_file.exists():
        raise PrefixLoadError(f"Prefix file not found: {prefix_file}")
    try:
        data = json.loads(prefix_file.read_text(encoding="utf-8"))
        prefixes = set(data.get("prefixes", []))
        if not prefixes:
            raise PrefixLoadError(
                f"Prefix file loaded but contains no prefixes: {prefix_file}"
            )
        logger.info("Loaded %s identifier prefixes from %s", len(prefixes), prefix_file)
        return prefixes
    except Exception as e:  # noqa: BLE001
        raise PrefixLoadError(f"Failed to load prefixes from {prefix_file}: {e}") from e


def is_valid_curie(curie: str) -> bool:
    """Return True if curie has an allowed prefix and contains ':'."""
    if not curie or ":" not in curie:
        return False
    prefix, _ = curie.split(":", 1)
    prefixes = load_prefixes()
    return prefix in prefixes
