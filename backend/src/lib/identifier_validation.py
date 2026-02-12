"""
Identifier validation utility.

Loads allowed CURIE prefixes from backend/config/identifier_prefixes.json.
This is a hard requirement: if the file is missing or unreadable, an exception is raised.
"""
from __future__ import annotations

import json
import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Set

logger = logging.getLogger(__name__)

PREFIX_FILE = Path(__file__).resolve().parent.parent.parent / "config" / "identifier_prefixes.json"


class PrefixLoadError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def load_prefixes() -> Set[str]:
    """Load allowed prefixes from JSON; raise if missing/unreadable."""
    if not PREFIX_FILE.exists():
        raise PrefixLoadError(f"Prefix file not found: {PREFIX_FILE}")
    try:
        data = json.loads(PREFIX_FILE.read_text(encoding="utf-8"))
        prefixes = set(data.get("prefixes", []))
        if not prefixes:
            raise PrefixLoadError(f"Prefix file loaded but contains no prefixes: {PREFIX_FILE}")
        logger.info('Loaded %s identifier prefixes from %s', len(prefixes), PREFIX_FILE)
        return prefixes
    except Exception as e:  # noqa: BLE001
        raise PrefixLoadError(f"Failed to load prefixes from {PREFIX_FILE}: {e}") from e


def is_valid_curie(curie: str) -> bool:
    """Return True if curie has an allowed prefix and contains ':'."""
    if not curie or ":" not in curie:
        return False
    prefix, _ = curie.split(":", 1)
    prefixes = load_prefixes()
    return prefix in prefixes
