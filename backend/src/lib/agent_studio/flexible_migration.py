"""One-off conversion of Flexible-extraction custom agents to Custom Output Structures.

inventory (read-only) -> a plan file per agent, reviewed with the agent's owner ->
apply (converts the agent through the normal agent service and re-pins each listed
flow step through the normal flow save) -> rollback (re-pins the recorded steps to
the old, immutable revision). Nothing here guesses fields: the inventory only
suggests merges, and a plan without a profile name and an owner review does not
validate. A plan is applied only from the exact file that was reviewed (its
sha256 digest). Flow steps are re-pinned only in flows the agent's owner owns,
validated as that owner with the owner's active groups recorded in the plan.
Callers own the transaction; nothing here commits.
"""

import hashlib
import re
from collections import Counter, defaultdict
from collections.abc import Iterable
from copy import deepcopy
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.lib.agent_studio.custom_agent_service import update_custom_agent
from src.lib.agent_studio.execution_revision_service import (
    current_execution_receipt,
    get_execution_revision,
)
from src.lib.curation_workspace.models import CurationExtractionResultRecord
from src.lib.flows.flow_service import save_flow_definition
from src.models.sql.agent import Agent
from src.models.sql.agent_execution_revision import AgentExecutionRevision
from src.models.sql.curation_flow import CurationFlow
from src.schemas.agent_execution_revision import AgentExecutionReceipt
from src.schemas.flows import FlowDefinition
from src.schemas.generic_extraction_profile import GenericProfileContract

PLAN_SCHEMA = "flexible-agent-conversion/v1"
RESULT_SCHEMA = "flexible-agent-conversion-result/v1"
INVENTORY_SCHEMA = "flexible-agent-inventory/v1"
GENERIC_OBJECT = "generic_object"
_FILLER = re.compile(r"(^|_)(of|the|or|and)(?=_|$)")


class _Closed(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StepPin(_Closed):
    flow_id: UUID
    node_id: str = Field(min_length=1)


class ConversionPlan(_Closed):
    schema_version: Literal["flexible-agent-conversion/v1"]
    agent_id: UUID
    expected_head_revision_id: UUID
    active_group_ids: list[str]
    profile: GenericProfileContract
    custom_prompt: str = Field(min_length=1)
    # Each merged attribute key -> the profile field that absorbs it.
    key_merges: dict[str, str] = Field(default_factory=dict)
    # Review notes only: records what the owner decided for other record kinds.
    # Nothing is applied from it; the profile and prompt carry the decision.
    other_object_kinds: dict[str, Literal["dropped", "attribute"]] = Field(default_factory=dict)
    # Required but may be empty: an agent with no flow steps is converted on its own.
    steps: list[StepPin]
    owner_review: str = Field(min_length=1)

    @model_validator(mode="after")
    def merges_are_declared(self) -> "ConversionPlan":
        """A merge only records what the profile declares: a source label of its target field."""
        labels = {field.key: set(field.source_labels) for field in self.profile.fields}
        undeclared = sorted(key for key, target in self.key_merges.items()
                            if key not in labels.get(target, set()))
        if undeclared:
            raise ValueError("key_merges must name a source label of their target field: "
                             + ", ".join(undeclared))
        return self


class RepinnedStep(_Closed):
    flow_id: UUID
    node_id: str
    old_revision_id: UUID
    new_revision_id: UUID


class ConversionResult(_Closed):
    schema_version: Literal["flexible-agent-conversion-result/v1"]
    agent_id: UUID
    old_revision_id: UUID
    new_revision_id: UUID
    steps: list[RepinnedStep]


def load_reviewed_plan(raw: bytes, sha256: str) -> ConversionPlan:
    """Parse a plan file only if its bytes are the ones that were reviewed."""
    if hashlib.sha256(raw).hexdigest() != sha256.strip().lower():
        raise ValueError("The plan file does not match the reviewed digest")
    return ConversionPlan.model_validate_json(raw)


def merge_key(key: str) -> str:
    """A suggestion only: keys that differ by case, filler words or a plural share one."""
    base = re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_")
    base = re.sub(r"_+", "_", _FILLER.sub(r"\1", base)).strip("_")
    return base[:-1] if base.endswith("s") and len(base) > 3 else base


def attribute_inventory(payloads: Iterable[Any]) -> dict[str, Any]:
    """Counts only: record kinds, semantic classes, attribute keys and how often each is a list."""
    results = 0
    kinds: Counter[str] = Counter()
    classes: Counter[str] = Counter()
    keys: Counter[str] = Counter()
    lists: Counter[str] = Counter()
    for payload in payloads:
        results += 1
        extracted = payload.get("extracted_objects") if isinstance(payload, dict) else None
        for item in extracted if isinstance(extracted, list) else []:
            if not isinstance(item, dict):
                continue
            kind = str(item.get("object_type"))
            kinds[kind] += 1
            raw_body = item.get("payload")
            body = raw_body if isinstance(raw_body, dict) else {}
            semantic_class = body.get("semantic_class")
            if kind == GENERIC_OBJECT and isinstance(semantic_class, str):
                classes[semantic_class] += 1
            raw_attributes = body.get("attributes")
            attributes = raw_attributes if isinstance(raw_attributes, dict) else {}
            for key, value in attributes.items():
                keys[key] += 1
                if isinstance(value, list):
                    lists[key] += 1
    ordered = sorted(keys.items(), key=lambda pair: (-pair[1], pair[0]))
    return {
        "results": results,
        "object_types": dict(kinds.most_common()),
        "semantic_classes": dict(classes.most_common()),
        "attribute_keys": [{"key": key, "objects": count, "list_values": lists[key]}
                           for key, count in ordered],
    }


def draft_plan(agent: Any, tally: dict[str, Any], steps: list[dict[str, Any]]) -> dict[str, Any]:
    """A plan to review with the owner; its empty name and review keep it from validating.

    Only steps that pin the agent's current head are drafted: apply re-pins a step
    only from the revision it converts.
    """
    head = str(agent.execution_revision_id)
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for entry in tally["attribute_keys"]:
        groups[merge_key(entry["key"])].append(entry)
    fields: list[dict[str, Any]] = []
    merges: dict[str, str] = {}
    for members in groups.values():
        canonical = members[0]["key"]
        for other in members[1:]:
            merges[other["key"]] = canonical
        objects = sum(member["objects"] for member in members)
        as_list = sum(member["list_values"] for member in members) * 2 > objects
        fields.append({
            "key": canonical, "source_labels": [member["key"] for member in members[1:]],
            "nullable": True,
            "value_schema": ({"kind": "array", "items": {"kind": "string"}} if as_list
                             else {"kind": "string"}),
        })
    classes = list(tally["semantic_classes"])
    return {
        "schema_version": PLAN_SCHEMA, "agent_id": str(agent.id),
        "expected_head_revision_id": str(agent.execution_revision_id), "active_group_ids": [],
        "profile": {"name": "", "semantic_class": classes[0] if classes else "", "fields": fields},
        "custom_prompt": agent.instructions, "key_merges": merges, "other_object_kinds": {},
        "steps": [{"flow_id": step["flow_id"], "node_id": step["node_id"]} for step in steps
                  if step["agent_revision_id"] == head],
        "owner_review": "",
    }


def _flexible_agents(db: Session) -> list[Agent]:
    return list(db.execute(
        select(Agent)
        .join(AgentExecutionRevision, AgentExecutionRevision.id == Agent.execution_revision_id)
        .where(Agent.is_active.is_(True), Agent.agent_key.startswith("ca_", autoescape=True),
               AgentExecutionRevision.output_mode == "unprofiled_generic")
        .order_by(Agent.agent_key)
    ).scalars())


def _result_payloads(db: Session, agent_key: str) -> Iterable[Any]:
    return db.execute(select(CurationExtractionResultRecord.payload_json)
                      .where(CurationExtractionResultRecord.agent_key == agent_key)).scalars()


def _steps_using(db: Session, agent: Agent) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Steps (with their pinned revision) in the owner's active flows, and in other users'."""
    owned, others = [], []
    for flow in db.execute(select(CurationFlow).where(CurationFlow.is_active.is_(True))
                           .order_by(CurationFlow.id)).scalars():
        for node in (flow.flow_definition or {}).get("nodes", []):
            data = node.get("data") or {}
            if data.get("agent_id") == agent.agent_key:
                step = {"flow_id": str(flow.id), "node_id": str(node["id"]),
                        "agent_revision_id": data.get("agent_revision_id")}
                (owned if flow.user_id == agent.user_id else others).append(step)
    return owned, others


def inventory(db: Session) -> dict[str, Any]:
    """Every active custom agent whose current revision is Flexible; reads only."""
    agents = []
    for agent in _flexible_agents(db):
        tally = attribute_inventory(_result_payloads(db, agent.agent_key))
        owned, other_owner_steps = _steps_using(db, agent)
        head = str(agent.execution_revision_id)
        agents.append({
            "agent_id": str(agent.id), "agent_key": agent.agent_key,
            "owner_user_id": agent.user_id,
            "head_revision_id": head,
            **tally,
            "steps": [step for step in owned if step["agent_revision_id"] == head],
            # Owned steps pinning another revision: apply refuses them, so they are
            # not drafted; their owner re-pins them to the head first, or leaves them.
            "not_current_steps": [step for step in owned if step["agent_revision_id"] != head],
            # Flows owned by someone else are never re-pinned by this script; they
            # keep their pinned revision until their owner re-pins them.
            "other_owner_steps": other_owner_steps,
            "draft_plan": draft_plan(agent, tally, owned),
        })
    return {"schema_version": INVENTORY_SCHEMA, "agents": agents}


def _pinned_flow(db: Session, pin: StepPin, agent: Agent, revision_id: UUID) -> CurationFlow:
    flow = db.get(CurationFlow, pin.flow_id)
    if flow is None or not flow.is_active:
        raise ValueError(f"Flow {pin.flow_id} is not an active flow")
    # The flow is saved as its owner with the plan's (the agent owner's) groups.
    if flow.user_id != agent.user_id:
        raise ValueError(f"Flow {pin.flow_id} is not owned by the agent's owner")
    nodes = [node for node in flow.flow_definition.get("nodes", []) if node.get("id") == pin.node_id]
    data = (nodes[0].get("data") or {}) if len(nodes) == 1 else {}
    if data.get("agent_id") != agent.agent_key or data.get("agent_revision_id") != str(revision_id):
        raise ValueError(f"Flow {pin.flow_id} step {pin.node_id} no longer pins revision {revision_id}")
    return flow


def _repin(db: Session, flow: CurationFlow, node_id: str, receipt: AgentExecutionReceipt,
           active_group_ids: list[str]) -> None:
    definition = deepcopy(flow.flow_definition)
    node = next(node for node in definition["nodes"] if node["id"] == node_id)
    node["data"]["agent_revision_id"] = str(receipt.agent_revision_id)
    node["data"]["execution_receipt"] = receipt.model_dump(mode="json")
    save_flow_definition(db, flow, FlowDefinition.model_validate(definition),
                         active_group_ids=active_group_ids)


def apply(db: Session, plan: ConversionPlan) -> ConversionResult:
    """Convert the agent as its owner, then re-pin every listed step; checks all before writing."""
    agent = db.get(Agent, plan.agent_id)
    if agent is None or not agent.is_active or not agent.agent_key.startswith("ca_"):
        raise ValueError("The plan's agent is not an active custom agent")
    if agent.execution_revision_id != plan.expected_head_revision_id:
        raise ValueError("The agent changed since the plan was written; run inventory again")
    old_revision_id = agent.execution_revision_id
    _, saved = get_execution_revision(db, agent.id, old_revision_id, agent.user_id,
                                      active_group_ids=plan.active_group_ids)
    if saved.output_contract.output_mode != "unprofiled_generic":
        raise ValueError("The agent no longer uses Flexible extraction")
    flows = [(pin, _pinned_flow(db, pin, agent, old_revision_id)) for pin in plan.steps]
    # Attaches the profile builder tools and records a new immutable revision.
    update_custom_agent(db, agent, expected_revision_id=old_revision_id,
                        new_generic_profile=plan.profile, custom_prompt=plan.custom_prompt,
                        active_group_ids=plan.active_group_ids)
    receipt = current_execution_receipt(db, agent.agent_key, agent.user_id,
                                        active_group_ids=plan.active_group_ids)
    steps = []
    for pin, flow in flows:
        _repin(db, flow, pin.node_id, receipt, plan.active_group_ids)
        steps.append(RepinnedStep(flow_id=pin.flow_id, node_id=pin.node_id,
                                  old_revision_id=old_revision_id,
                                  new_revision_id=receipt.agent_revision_id))
    db.flush()
    return ConversionResult(schema_version=RESULT_SCHEMA, agent_id=agent.id,
                            old_revision_id=old_revision_id,
                            new_revision_id=receipt.agent_revision_id, steps=steps)


def rollback(db: Session, plan: ConversionPlan, result: ConversionResult) -> list[RepinnedStep]:
    """Re-pin each recorded step to the old revision; the agent head is left as it is.

    Restoring the agent head to Flexible is refused by design (Flexible is retired);
    old runs are untouched either way.
    """
    if result.agent_id != plan.agent_id:
        raise ValueError("The result file is for another agent")
    agent = db.get(Agent, result.agent_id)
    if agent is None:
        raise ValueError("The plan's agent no longer exists")
    row, saved = get_execution_revision(db, agent.id, result.old_revision_id, agent.user_id,
                                        active_group_ids=plan.active_group_ids)
    old = AgentExecutionReceipt(agent_id=agent.id, agent_key=agent.agent_key,
                                agent_revision_id=row.id, revision=row.revision,
                                fingerprint=row.fingerprint, output_contract=saved.output_contract)
    flows = [(step, _pinned_flow(db, StepPin(flow_id=step.flow_id, node_id=step.node_id),
                                 agent, step.new_revision_id))
             for step in result.steps]
    for step, flow in flows:
        _repin(db, flow, step.node_id, old, plan.active_group_ids)
    db.flush()
    return result.steps
