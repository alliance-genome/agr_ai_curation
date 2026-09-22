"""Flow-source-scoped choices applied before extractor validator dispatch."""

from contextvars import ContextVar, Token
from typing import Any, Mapping, Sequence


_SELECTIONS: ContextVar[tuple[tuple[str, str, str | None], ...]] = ContextVar(
    "flow_validator_selections", default=()
)


def effective_flow_validation_groups(
    groups: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Select one custom replacement per binding on this extraction source."""
    custom = {}
    skipped = set()
    for group in groups:
        binding = group.get("binding_id") or group.get("validator_binding_id")
        state = group.get("state")
        if state in {"replaced", "supplemental"}:
            if not binding or not group.get("validator_node_id"):
                raise ValueError("Custom validator selection requires a binding and validator node")
            if binding in custom:
                raise ValueError(f"Conflicting custom validators for binding {binding}")
            custom[binding] = group
        elif state == "skipped":
            skipped.add(binding)
    if skipped.intersection(custom):
        raise ValueError("A validator binding cannot be both skipped and custom-selected")
    return [dict(group) for group in groups if not (
        group.get("state") == "automatic"
        and (group.get("binding_id") or group.get("validator_binding_id")) in custom
    )]


def set_flow_validator_selections(groups: Sequence[Mapping[str, Any]]) -> Token:
    """Bind validated choices only for the current source specialist invocation."""
    effective = effective_flow_validation_groups(groups)
    return _SELECTIONS.set(tuple(
        (str(group.get("binding_id") or group.get("validator_binding_id")),
         str(group["state"]), group.get("validator_node_id"))
        for group in effective
        if group.get("state") in {"replaced", "supplemental", "skipped"}
        and (group.get("binding_id") or group.get("validator_binding_id"))
    ))


def reset_flow_validator_selections(token: Token) -> None:
    _SELECTIONS.reset(token)


def current_flow_validator_selections() -> dict[str, dict[str, Any]]:
    return {binding: {"state": state, "validator_node_id": node}
            for binding, state, node in _SELECTIONS.get()}
