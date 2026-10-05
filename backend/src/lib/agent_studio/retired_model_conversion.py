"""Move saved custom agents and the flow steps that pin them off retired models.

A model retirement's Alembic migration moves the editable ``agents`` rows, but a
custom agent runs from its immutable head revision and a flow step runs the exact
revision it pins, so both still name a retired model. For every custom agent
(archived ones included) this appends, through the append-only revision service
that restore and the release agent-upgrade engine use:

- a copy of each older revision an active flow step pins, then
- a copy of the head, which stays the head,

each identical to its source except ``model_id`` and the reviewed reasoning
mapping, so no step's configuration is swapped for another. Each such step is
re-pinned to the copy of the revision it pinned and its flow is saved through the
normal flow save as the flow's owner, with the owner's groups. A selected-fields
file output reading a re-pinned step moves to the step's new identity only if its
layout was current before. Immutable rows are never edited.

Re-running finds nothing left to do: an existing copy (same agent and
fingerprint) is reused. An agent or flow that cannot be converted is left
unchanged and reported with the reason; nothing is guessed. Callers own the
transaction; nothing here commits.
"""

from copy import deepcopy
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.lib.agent_studio.execution_revision_service import append_execution_revision
from src.lib.config.models_loader import get_model
from src.lib.flows.execution_revisions import (
    flow_execution_revision_findings,
    resolve_flow_execution_revisions,
)
from src.lib.flows.flow_service import save_flow_definition
from src.lib.flows.repin import move_layouts
from src.lib.openai_agents.config import unsupported_reasoning_effort
from src.models.sql.agent import Agent
from src.models.sql.agent_execution_revision import AgentExecutionRevision
from src.models.sql.curation_flow import CurationFlow
from src.schemas.agent_execution_revision import AgentExecutionReceipt, AgentExecutionSnapshot
from src.schemas.flows import FlowDefinition

REPORT_SCHEMA = "retired-model-conversion/v1"


class ConversionRefused(ValueError):
    """The run cannot start; nothing was written."""


def mapped_reasoning(value: str | None, reasoning_map: dict[str, str]) -> str | None:
    """The reviewed mapping, matched as the migration matches it (trimmed, any case)."""
    if value is None:
        return None
    return reasoning_map.get(value.strip().lower(), value)


def _saved(row: AgentExecutionRevision) -> AgentExecutionSnapshot:
    saved = AgentExecutionSnapshot.model_validate(row.snapshot)
    if saved.fingerprint() != row.fingerprint:
        raise ValueError(f"Revision {row.revision} fingerprint mismatch")
    return saved


def _ref(row: AgentExecutionRevision) -> dict[str, Any]:
    return {"id": str(row.id), "revision": row.revision}


def _pins(flow: CurationFlow) -> list[tuple[str, str, str]]:
    """(node_id, agent_key, revision_id) for every custom step that pins a revision."""
    pins = []
    for node in (flow.flow_definition or {}).get("nodes", []):
        data = node.get("data") or {}
        key, revision_id = str(data.get("agent_id") or ""), data.get("agent_revision_id")
        if key.startswith("ca_") and revision_id:
            pins.append((str(node["id"]), key, str(revision_id)))
    return pins


def _retired_revisions(db: Session, revision_ids: set[str], retired_model_ids: frozenset[str]) -> set[str]:
    if not revision_ids:
        return set()
    rows = db.execute(select(AgentExecutionRevision.id, AgentExecutionRevision.snapshot).where(
        AgentExecutionRevision.id.in_([UUID(value) for value in revision_ids])))
    return {str(row_id) for row_id, snapshot in rows if snapshot.get("model_id") in retired_model_ids}


def _convert_agent(
    db: Session, agent: Agent, pinned_ids: set[str], *, retired_model_ids: frozenset[str],
    target_model_id: str, reasoning_map: dict[str, str], notes: str,
) -> tuple[dict[str, Any], dict[str, str]]:
    """Returns the agent's report entry and {old revision id: its copy's id}."""
    target = get_model(target_model_id)

    def converted(row: AgentExecutionRevision) -> AgentExecutionSnapshot:
        saved = _saved(row)
        reasoning = mapped_reasoning(saved.model_reasoning, reasoning_map)
        if unsupported_reasoning_effort(target, reasoning) is not None:
            raise ValueError(f"revision {row.revision} reasoning {saved.model_reasoning!r} is not offered "
                             f"by {target_model_id} and has no reviewed mapping")
        return saved.model_copy(update={"model_id": target_model_id, "model_reasoning": reasoning})

    def append(snapshot: AgentExecutionSnapshot) -> AgentExecutionRevision:
        return append_execution_revision(
            db, agent, snapshot, user_id=agent.user_id,
            expected_revision_id=agent.execution_revision_id,
            allow_archived_profile=True, notes=notes,
        )

    head = db.get(AgentExecutionRevision, agent.execution_revision_id)
    older = [db.get(AgentExecutionRevision, UUID(value)) for value in pinned_ids if value != str(head.id)]
    if any(row.agent_id != agent.id for row in older):
        raise ValueError("a flow step pins a revision of another agent")
    # Snapshots are checked before anything is appended.
    planned = [(row, converted(row)) for row in sorted(older, key=lambda row: row.revision)]
    head_retired = _saved_model(head) in retired_model_ids
    head_snapshot = converted(head) if head_retired else _saved(head)
    copies, moves = [], {}
    for row, snapshot in planned:
        existing = db.execute(
            select(AgentExecutionRevision).where(
                AgentExecutionRevision.agent_id == agent.id,
                AgentExecutionRevision.fingerprint == snapshot.fingerprint())
            .order_by(AgentExecutionRevision.revision.desc()).limit(1)
        ).scalar_one_or_none()
        copy = existing or append(snapshot)
        moves[str(row.id)] = str(copy.id)
        copies.append({"from": _ref(row), "to": _ref(copy), "reused": existing is not None,
                       "model_reasoning": [_saved_reasoning(row), snapshot.model_reasoning]})
    if head_retired or agent.execution_revision_id != head.id:
        # A pinned copy became the head; the head's own configuration is appended
        # again (with the model change when the head is on a retired model).
        copy = append(head_snapshot)
        if head_retired:
            moves[str(head.id)] = str(copy.id)
        copies.append({"from": _ref(head), "to": _ref(copy), "reused": False, "head": True,
                       "model_reasoning": [_saved_reasoning(head), head_snapshot.model_reasoning]})
    # As a restore does, the editable row follows its executable head's model.
    agent.model_id = head_snapshot.model_id
    agent.model_reasoning = head_snapshot.model_reasoning
    db.flush()
    return {"agent_key": agent.agent_key, "agent_id": str(agent.id), "name": agent.name,
            "is_active": bool(agent.is_active), "old_head": _ref(head),
            "new_head": _ref(db.get(AgentExecutionRevision, agent.execution_revision_id)),
            "copies": copies}, moves


def _saved_model(row: AgentExecutionRevision) -> str | None:
    return row.snapshot.get("model_id")


def _saved_reasoning(row: AgentExecutionRevision) -> str | None:
    return row.snapshot.get("model_reasoning")


def _receipt(db: Session, revision_id: str) -> dict:
    row = db.get(AgentExecutionRevision, UUID(revision_id))
    agent = db.get(Agent, row.agent_id)
    return AgentExecutionReceipt(
        agent_id=agent.id, agent_key=agent.agent_key, agent_revision_id=row.id,
        revision=row.revision, fingerprint=row.fingerprint,
        output_contract=_saved(row).output_contract,
    ).model_dump(mode="json")


def _canonical(definition: dict) -> dict:
    """The flow as the application parses it (schema defaults applied)."""
    return FlowDefinition.model_validate(deepcopy(definition)).model_dump(mode="json")


def _require_only_pins_changed(before: dict, after: dict, moves: dict[str, str],
                               layouts: list[dict[str, Any]]) -> None:
    """The save may change only the moved pins, their receipts, derived validation
    groups, check descriptors (not which checks or their on/off choice) and the
    layout identities moved above."""
    old, new = _canonical(before), _canonical(after)
    if {k: v for k, v in old.items() if k != "nodes"} != {k: v for k, v in new.items() if k != "nodes"}:
        raise ValueError("the save would change flow settings other than the re-pinned steps")
    if [node["id"] for node in old["nodes"]] != [node["id"] for node in new["nodes"]]:
        raise ValueError("the save would change the flow's steps")
    moved_layouts = {(item["output_node_id"], item["source_node_id"]): item["to"] for item in layouts}
    for old_node, new_node in zip(old["nodes"], new["nodes"]):
        node_id = old_node["id"]
        if {k: v for k, v in old_node.items() if k != "data"} != {k: v for k, v in new_node.items() if k != "data"}:
            raise ValueError(f"the save would change step {node_id}")
        expected = deepcopy(old_node["data"])
        if node_id in moves:
            expected["agent_revision_id"] = moves[node_id]
            expected["execution_receipt"] = new_node["data"].get("execution_receipt")
            if (new_node["data"]["agent_revision_id"] != moves[node_id]
                    or (expected["execution_receipt"] or {}).get("agent_revision_id") != moves[node_id]):
                raise ValueError(f"step {node_id} was not re-pinned")
        for source in (expected.get("projection_plan") or {}).get("selected_sources") or []:
            if (node_id, source.get("node_id")) in moved_layouts:
                source["schema_fingerprint"] = moved_layouts[(node_id, source["node_id"])]
        # The save derives validation groups, and refreshes each check's descriptor
        # from the installed packages as any curator save does; which checks a step
        # has and whether each is on must stay the same.
        derived = {"validation_groups", "validation_attachments"}
        if ({k: v for k, v in expected.items() if k not in derived}
                != {k: v for k, v in new_node["data"].items() if k not in derived}):
            raise ValueError(f"the save would change step {node_id} beyond its pinned revision")
        if _check_choices(expected) != _check_choices(new_node["data"]):
            raise ValueError(f"the save would change step {node_id}'s checks")


def _check_choices(data: dict) -> dict[str, Any]:
    return {item.get("attachment_id"): item.get("enabled")
            for item in data.get("validation_attachments") or []}


def _repin_flow(db: Session, flow: CurationFlow, moves: dict[str, str],
                groups: list[str]) -> list[dict[str, Any]]:
    """Re-pin the moved steps and save the flow as its owner; returns the moved layouts."""
    before = deepcopy(flow.flow_definition)
    definition = FlowDefinition.model_validate(deepcopy(before))
    old_receipts = {}
    for node in definition.nodes:
        if node.id in moves:
            old_receipts[node.id] = _receipt(db, str(node.data.agent_revision_id))
            node.data.agent_revision_id = UUID(moves[node.id])
            # The exact revision supplies its receipt when the flow is resolved.
            node.data.execution_receipt = None
    resolved = resolve_flow_execution_revisions(db, definition, user_id=flow.user_id,
                                                active_group_ids=groups)
    unavailable = [finding.to_dict() for finding in resolved.findings
                   if finding.node_id in moves and finding.severity == "error"]
    if unavailable:
        raise ValueError(f"re-pinned steps are not runnable: {unavailable}")
    layouts = move_layouts(definition, resolved.projection_catalogs, old_receipts)
    save_flow_definition(db, flow, definition, active_group_ids=groups)
    _require_only_pins_changed(before, flow.flow_definition, moves, layouts)
    db.flush()
    findings = flow_execution_revision_findings(db, flow.flow_definition, user_id=flow.user_id,
                                                active_group_ids=groups)
    if findings:
        raise ValueError(f"the saved flow still has blocking findings: {findings}")
    return layouts


def _refusal(error: Exception) -> Any:
    if isinstance(error, HTTPException):
        # The flow's own validation findings, as a curator save would report them.
        return error.detail
    return str(error)


def _retired_heads(db: Session, retired_model_ids: frozenset[str]) -> list[Agent]:
    return list(db.execute(
        select(Agent)
        .join(AgentExecutionRevision, AgentExecutionRevision.id == Agent.execution_revision_id)
        .where(Agent.agent_key.startswith("ca_", autoescape=True),
               AgentExecutionRevision.snapshot["model_id"].astext.in_(sorted(retired_model_ids)))
    ).scalars())


def remaining(db: Session, retired_model_ids: frozenset[str]) -> dict[str, int]:
    """What still names a retired model: agent heads, and pins in active and deleted flows."""
    pins = {True: [], False: []}
    for flow in db.execute(select(CurationFlow)).scalars():
        pins[bool(flow.is_active)].extend(revision for _, _, revision in _pins(flow))
    retired = _retired_revisions(db, set(pins[True]) | set(pins[False]), retired_model_ids)
    heads = _retired_heads(db, retired_model_ids)
    return {
        "active_agent_heads": sum(1 for agent in heads if agent.is_active),
        "archived_agent_heads": sum(1 for agent in heads if not agent.is_active),
        "active_flow_pins": sum(1 for revision in pins[True] if revision in retired),
        "deleted_flow_pins": sum(1 for revision in pins[False] if revision in retired),
    }


def convert(
    db: Session, *, retired_model_ids: frozenset[str], target_model_id: str, reasoning_map: dict[str, str],
    owner_groups: dict[int, list[str]], notes: str,
) -> dict[str, Any]:
    """Convert every custom agent head and active-flow pin on a retired model; report each."""
    offered = sorted(model_id for model_id in retired_model_ids if get_model(model_id) is not None)
    if not retired_model_ids or offered:
        raise ConversionRefused(f"Retired models still in the model catalog: {offered}")
    if get_model(target_model_id) is None:
        raise ConversionRefused(f"{target_model_id} is not in the model catalog")
    before = remaining(db, retired_model_ids)
    flows = list(db.execute(select(CurationFlow).where(CurationFlow.is_active.is_(True))
                            .order_by(CurationFlow.id).with_for_update()).scalars())
    pinned = {flow.id: _pins(flow) for flow in flows}
    retired_pins = _retired_revisions(
        db, {revision for pins in pinned.values() for _, _, revision in pins}, retired_model_ids)
    affected = [flow for flow in flows
                if any(revision in retired_pins for _, _, revision in pinned[flow.id])]
    missing = sorted({flow.user_id for flow in affected} - set(owner_groups))
    if missing:
        raise ConversionRefused("No reviewed active groups for flow owner(s) "
                                f"{missing}; add them to the owner groups file")

    pins_by_agent: dict[str, set[str]] = {}
    for flow in affected:
        for _, key, revision in pinned[flow.id]:
            if revision in retired_pins:
                pins_by_agent.setdefault(key, set()).add(revision)
    agents = {agent.agent_key: agent for agent in _retired_heads(db, retired_model_ids)}
    for key in set(pins_by_agent) - set(agents):
        agents[key] = db.execute(select(Agent).where(Agent.agent_key == key)).scalar_one()

    owners: dict[int, dict[str, Any]] = {}

    def owner(user_id: int) -> dict[str, Any]:
        return owners.setdefault(user_id, {"user_id": user_id, "agents": [], "flows": [],
                                           "refused_agents": [], "refused_flows": []})

    moves: dict[str, str] = {}
    for key in sorted(agents, key=lambda value: (agents[value].user_id, value)):
        agent = agents[key]
        savepoint = db.begin_nested()
        try:
            entry, agent_moves = _convert_agent(
                db, agent, pins_by_agent.get(key, set()), retired_model_ids=retired_model_ids,
                target_model_id=target_model_id, reasoning_map=reasoning_map, notes=notes)
        except ValueError as error:
            savepoint.rollback()
            owner(agent.user_id)["refused_agents"].append(
                {"agent_key": key, "name": agent.name, "reason": str(error)})
            continue
        savepoint.commit()
        moves.update(agent_moves)
        owner(agent.user_id)["agents"].append(entry)

    for flow in affected:
        steps = {node_id: moves[revision] for node_id, _, revision in pinned[flow.id]
                 if revision in moves}
        entry = {"flow_id": str(flow.id), "name": flow.name, "steps": [
            {"node_id": node_id, "agent_key": key, "from_revision_id": revision,
             "to_revision_id": moves.get(revision)}
            for node_id, key, revision in pinned[flow.id] if revision in retired_pins]}
        savepoint = db.begin_nested()
        try:
            if any(step["to_revision_id"] is None for step in entry["steps"]):
                raise ValueError("a pinned agent could not be converted (see refused_agents)")
            entry["layouts"] = _repin_flow(db, flow, steps, owner_groups[flow.user_id])
        except (ValueError, HTTPException) as error:
            savepoint.rollback()
            db.refresh(flow)
            owner(flow.user_id)["refused_flows"].append({**entry, "reason": _refusal(error)})
            continue
        savepoint.commit()
        owner(flow.user_id)["flows"].append(entry)

    report = [owners[user_id] for user_id in sorted(owners)]
    return {
        "schema_version": REPORT_SCHEMA,
        "from_models": sorted(retired_model_ids), "to_model": target_model_id, "reasoning_map": reasoning_map,
        "before": before, "after": remaining(db, retired_model_ids),
        "counts": {
            "owners": len(report),
            # An agent whose pins only reuse copies from an earlier run is listed, not counted.
            "agents_converted": sum(1 for item in report for agent in item["agents"]
                                    if any(not copy["reused"] for copy in agent["copies"])),
            "revisions_appended": sum(1 for item in report for agent in item["agents"]
                                      for copy in agent["copies"] if not copy["reused"]),
            "flows_repinned": sum(len(item["flows"]) for item in report),
            "steps_repinned": sum(len(flow["steps"]) for item in report for flow in item["flows"]),
            "agents_refused": sum(len(item["refused_agents"]) for item in report),
            "flows_refused": sum(len(item["refused_flows"]) for item in report),
        },
        "owners": report,
    }
