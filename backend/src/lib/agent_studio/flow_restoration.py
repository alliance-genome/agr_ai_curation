"""Narrow, draft-only recovery; never a Save or execution validation bypass."""

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from pydantic import ValidationError

from src.schemas.flows import FlowDefinition
from .authoring_validation import _pydantic_flow_findings


def inspect_instruction_restoration(base: Mapping[str, Any], candidate: Mapping[str, Any]):
    """Return canonical findings only for an exact entry-node addition, else None.

    Existing graph content is immutable here. The caller still checks user access
    and fingerprints. Remaining incomplete topology is reported, never hidden.
    """
    before, after = deepcopy(dict(base)), deepcopy(dict(candidate))
    for definition in (before, after):
        if definition.get("task_instructions_default_only") is False:
            definition.pop("task_instructions_default_only")
    old_nodes, new_nodes = before.get("nodes", []), after.get("nodes", [])
    if not isinstance(old_nodes, list) or not isinstance(new_nodes, list):
        return None
    if any(not isinstance(n, dict) or not isinstance(n.get("data"), dict) for n in old_nodes + new_nodes):
        return None
    if any(n.get("type") == "task_input" or n.get("data", {}).get("agent_id") == "task_input" for n in old_nodes):
        return None
    added = [n for n in new_nodes if n.get("type") == "task_input"]
    if len(added) != 1 or [n for n in new_nodes if n is not added[0]] != old_nodes:
        return None
    task = added[0]
    if set(task) - {"id", "type", "position", "data"}:
        return None
    data = task.get("data", {})
    if data.get("agent_id") != "task_input" or set(data) - {
        "agent_id", "agent_display_name", "agent_description", "task_instructions",
        "custom_instructions", "output_key",
    } or data.get("custom_instructions", ""):
        return None
    if not isinstance(data.get("task_instructions"), str):
        return None
    if any(n.get("id") == task.get("id") or n.get("data", {}).get("output_key") == data.get("output_key") for n in old_nodes):
        return None
    if after.get("entry_node_id") != task.get("id"):
        return None
    after["nodes"] = old_nodes
    for value in (before, after):
        value.pop("entry_node_id", None)
    if before != after:
        return None
    try:
        FlowDefinition.model_validate(candidate)
        return []
    except ValidationError as exc:
        findings = _pydantic_flow_findings(exc, candidate)
        allowed = {"ambiguous_entry", "ambiguous_terminal", "disconnected", "entry_mismatch", "task_input_not_entry"}
        return findings if findings and all(f.code in allowed for f in findings) else None
