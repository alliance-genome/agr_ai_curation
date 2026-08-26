"""Package-owned group scoping for specialist tool exposure."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from src.lib.agent_access import normalize_allowed_group_ids


@dataclass(frozen=True)
class GroupToolRule:
    """Expose one tool only when an authenticated group matches."""

    tool_id: str
    allowed_group_ids: list[str]
    field_paths: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_id": self.tool_id,
            "allowed_group_ids": list(self.allowed_group_ids),
            "field_paths": list(self.field_paths),
        }


@dataclass(frozen=True)
class GroupToolPolicy:
    """Validated package policy loaded from an agent definition."""

    rules: list[GroupToolRule] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"rules": [rule.to_dict() for rule in self.rules]}


@dataclass(frozen=True)
class GroupToolResolution:
    """Resolved tool IDs and bounded audit metadata for one agent run."""

    tool_ids: list[str]
    active_group_ids: list[str]
    base_tool_ids: list[str]
    added_tool_ids: list[str]
    denied_tool_ids: list[str]

    def audit_metadata(self) -> dict[str, Any]:
        return {
            "active_group_ids": list(self.active_group_ids),
            "base_tool_ids": list(self.base_tool_ids),
            "added_tool_ids": list(self.added_tool_ids),
            "denied_tool_ids": list(self.denied_tool_ids),
        }


def parse_group_tool_policy(
    value: Any,
    *,
    field_name: str = "group_tool_policy",
) -> GroupToolPolicy:
    """Validate the forward-only YAML/JSON group-tool policy shape."""

    if value is None:
        return GroupToolPolicy()
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be a mapping")

    unknown_fields = sorted(set(value) - {"rules"})
    if unknown_fields:
        raise ValueError(
            f"{field_name} contains unknown fields: {', '.join(unknown_fields)}"
        )
    raw_rules = value.get("rules", [])
    if not isinstance(raw_rules, list):
        raise ValueError(f"{field_name}.rules must be a list")

    rules: list[GroupToolRule] = []
    seen_tool_ids: set[str] = set()
    for index, raw_rule in enumerate(raw_rules):
        rule_field = f"{field_name}.rules[{index}]"
        if not isinstance(raw_rule, Mapping):
            raise ValueError(f"{rule_field} must be a mapping")
        unknown_rule_fields = sorted(
            set(raw_rule) - {"tool_id", "allowed_group_ids", "field_paths"}
        )
        if unknown_rule_fields:
            raise ValueError(
                f"{rule_field} contains unknown fields: "
                + ", ".join(unknown_rule_fields)
            )

        raw_tool_id = raw_rule.get("tool_id")
        if not isinstance(raw_tool_id, str) or not raw_tool_id.strip():
            raise ValueError(f"{rule_field}.tool_id must be a non-empty string")
        tool_id = raw_tool_id.strip()
        if tool_id != raw_tool_id:
            raise ValueError(f"{rule_field}.tool_id must not contain whitespace")
        if tool_id in seen_tool_ids:
            raise ValueError(f"{field_name} contains duplicate tool_id '{tool_id}'")
        seen_tool_ids.add(tool_id)

        allowed_group_ids = normalize_allowed_group_ids(
            raw_rule.get("allowed_group_ids"),
            field_name=f"{rule_field}.allowed_group_ids",
        )
        if not allowed_group_ids:
            raise ValueError(f"{rule_field}.allowed_group_ids must not be empty")

        raw_field_paths = raw_rule.get("field_paths")
        if not isinstance(raw_field_paths, list) or not raw_field_paths:
            raise ValueError(f"{rule_field}.field_paths must be a non-empty list")
        field_paths: list[str] = []
        for field_index, raw_path in enumerate(raw_field_paths):
            if not isinstance(raw_path, str) or not raw_path.strip():
                raise ValueError(
                    f"{rule_field}.field_paths[{field_index}] must be a non-empty string"
                )
            field_path = raw_path.strip()
            if field_path != raw_path:
                raise ValueError(
                    f"{rule_field}.field_paths[{field_index}] must not contain whitespace"
                )
            if field_path in field_paths:
                raise ValueError(
                    f"{rule_field}.field_paths must not contain duplicate '{field_path}'"
                )
            field_paths.append(field_path)

        rules.append(
            GroupToolRule(
                tool_id=tool_id,
                allowed_group_ids=allowed_group_ids,
                field_paths=field_paths,
            )
        )

    return GroupToolPolicy(rules=rules)


def resolve_group_tool_policy(
    base_tool_ids: Iterable[str],
    policy_value: GroupToolPolicy | Mapping[str, Any] | None,
    active_group_ids: list[str] | None,
) -> GroupToolResolution:
    """Apply additive/restrictive rules using authenticated group IDs only."""

    policy = (
        policy_value
        if isinstance(policy_value, GroupToolPolicy)
        else parse_group_tool_policy(policy_value)
    )
    base = list(dict.fromkeys(str(tool_id).strip() for tool_id in base_tool_ids))
    active = normalize_allowed_group_ids(
        active_group_ids or [],
        field_name="active_groups",
    )
    active_set = set(active)
    resolved = list(base)
    added: list[str] = []
    denied: list[str] = []

    for rule in policy.rules:
        allowed = bool(active_set.intersection(rule.allowed_group_ids))
        is_base_tool = rule.tool_id in base
        if allowed:
            if not is_base_tool:
                resolved.append(rule.tool_id)
                added.append(rule.tool_id)
            continue

        if is_base_tool:
            resolved.remove(rule.tool_id)
        denied.append(rule.tool_id)

    exposed_base = [tool_id for tool_id in base if tool_id in resolved]
    return GroupToolResolution(
        tool_ids=resolved,
        active_group_ids=active,
        base_tool_ids=exposed_base,
        added_tool_ids=added,
        denied_tool_ids=denied,
    )
