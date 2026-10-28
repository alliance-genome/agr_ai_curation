"""Flow-source-scoped choices applied before extractor validator dispatch."""

from contextvars import ContextVar, Token
from typing import Any, Mapping, Sequence


_SELECTIONS: ContextVar[tuple[tuple[str, str, str | None], ...]] = ContextVar(
    "flow_validator_selections", default=()
)


def effective_flow_validation_groups(
    groups: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Allow automatic checks and explicit opt-outs, never flow overrides."""
    for group in groups:
        if group.get("state") not in {"automatic", "skipped"}:
            raise ValueError(
                "Replacement and supplemental validators are not supported in flows. "
                "Remove the validator attachment connection and use the extractor's automatic checks."
            )
    return [dict(group) for group in groups]


def set_flow_validator_selections(groups: Sequence[Mapping[str, Any]]) -> Token:
    """Bind validated choices only for the current source specialist invocation."""
    effective = effective_flow_validation_groups(groups)
    return _SELECTIONS.set(tuple(
        (str(group.get("binding_id") or group.get("validator_binding_id")),
         str(group["state"]), group.get("validator_node_id"))
        for group in effective
        if group.get("state") == "skipped"
        and (group.get("binding_id") or group.get("validator_binding_id"))
    ))


def reset_flow_validator_selections(token: Token) -> None:
    _SELECTIONS.reset(token)


def current_flow_validator_selections() -> dict[str, dict[str, Any]]:
    return {binding: {"state": state, "validator_node_id": node}
            for binding, state, node in _SELECTIONS.get()}
