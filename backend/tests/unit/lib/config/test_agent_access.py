"""Tests for canonical agent and flow-recipe group availability policy."""

import pytest

from src.lib.agent_access import (
    normalize_allowed_group_ids,
    require_allowed_group_ids_narrowing,
)
from src.lib.config.agent_loader import AgentDefinition


def test_normalize_allowed_group_ids_uses_registry_order_and_canonical_case():
    assert normalize_allowed_group_ids(["RGD", "FB"]) == ["FB", "RGD"]


@pytest.mark.parametrize("value", [["rgd"], ["UNKNOWN"], [" RGD"], ["RGD", "RGD"]])
def test_normalize_allowed_group_ids_fails_closed(value):
    with pytest.raises(ValueError, match="group ID|duplicate|whitespace"):
        normalize_allowed_group_ids(value)


def test_inherited_access_can_only_narrow():
    assert require_allowed_group_ids_narrowing(
        ["FB", "RGD"], ["RGD"], source_name="template"
    ) == ["RGD"]
    with pytest.raises(ValueError, match="cannot widen"):
        require_allowed_group_ids_narrowing(["RGD"], [])
    with pytest.raises(ValueError, match="additional groups: FB"):
        require_allowed_group_ids_narrowing(["RGD"], ["FB", "RGD"])


def test_agent_definition_loads_unrestricted_and_rgd_access():
    unrestricted = AgentDefinition.from_yaml("demo", {"agent_id": "demo"})
    restricted = AgentDefinition.from_yaml(
        "demo",
        {"agent_id": "demo", "access": {"allowed_group_ids": ["RGD"]}},
    )

    assert unrestricted.access.allowed_group_ids == []
    assert restricted.access.allowed_group_ids == ["RGD"]


@pytest.mark.parametrize(
    "access, expected",
    [
        ({"allowed_group_ids": ["NOT_A_GROUP"]}, "Unknown group ID"),
        ({"allowed_group_ids": "RGD"}, "must be a list"),
        ({"groups": ["RGD"]}, "unknown fields"),
        (["RGD"], "must be a mapping"),
    ],
)
def test_agent_definition_rejects_invalid_access(access, expected):
    with pytest.raises(ValueError, match=expected):
        AgentDefinition.from_yaml("demo", {"agent_id": "demo", "access": access})
