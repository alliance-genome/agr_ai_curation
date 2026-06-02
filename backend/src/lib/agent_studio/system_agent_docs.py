"""Loader for curator-facing docs of synthetic flow nodes (no agent bundle folder).

Keeps the prose for task_input / curation_prep in YAML, not Python literals.
"""
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

_DOCS_PATH = Path(__file__).with_name("system_agent_docs.yaml")


@lru_cache(maxsize=1)
def _load() -> Dict[str, Dict[str, Any]]:
    with open(_DOCS_PATH, "r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    return data


def get_system_agent_documentation(agent_id: str) -> Optional[Dict[str, Any]]:
    """Return the documentation dict for a synthetic flow node, or None."""
    return _load().get(agent_id)
