"""Shared policy for which agents may run as ordinary flow steps."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


INTERNAL_FLOW_AGENT_IDS = frozenset({"supervisor", "task_input"})


def agent_allows_ordinary_flow_step(
    agent_id: str,
    entry: Mapping[str, Any] | None = None,
    *,
    category: str | None = None,
    supervisor_enabled: bool | None = None,
) -> bool:
    """Return whether an agent may be added/executed as a normal flow step.

    Validation agents that are not supervisor-callable are attachment-only:
    they must run through domain-pack validation groups on extraction nodes.
    """

    if agent_id in INTERNAL_FLOW_AGENT_IDS:
        return False

    if entry is not None:
        category = category if category is not None else entry.get("category")
        if supervisor_enabled is None:
            supervisor = entry.get("supervisor")
            if isinstance(supervisor, Mapping):
                supervisor_enabled = bool(supervisor.get("enabled"))

    if category != "Validation":
        return True

    return bool(supervisor_enabled)


def flow_palette_show_in_palette(
    agent_id: str,
    entry: Mapping[str, Any],
) -> bool:
    """Return the FlowBuilder palette visibility for a catalog agent entry."""

    frontend = entry.get("frontend")
    configured_visible = True
    if isinstance(frontend, Mapping):
        configured_visible = frontend.get("show_in_palette", True) is not False

    return configured_visible and agent_allows_ordinary_flow_step(agent_id, entry)


def attachment_only_validator_reason(agent_name: str) -> str:
    """Explain why an attachment-only validator was rejected as a flow step."""

    return (
        f"{agent_name} is an attachment-only validator. Add it as a validation "
        "attachment on an extraction step so it receives a structured extraction "
        "envelope and DomainValidationRequest."
    )
