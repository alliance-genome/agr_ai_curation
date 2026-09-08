"""Use the normal curator workflow's compatible, installed recipe templates."""

from collections.abc import Iterable
from typing import Any

from src.lib.agent_studio.flow_tools import FLOW_AGENT_IDS, _filter_flow_templates
from src.lib.packages.flow_recipes import load_flow_recipe_catalog


def load_benchmark_flow_templates(active_groups: Iterable[str]) -> list[dict[str, Any]]:
    return _filter_flow_templates(
        set(FLOW_AGENT_IDS), load_flow_recipe_catalog(), active_group_ids=list(active_groups),
    )
