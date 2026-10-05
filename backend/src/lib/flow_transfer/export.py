"""What main AI Curation exports: the curator's runnable flows and their exact closure.

The gate is what AI Curation itself would run for this curator. The closure is read
only through the curator's own authorized reads, so a teammate's private agent or
output structure is never read, and only the revisions the flow pins are included.
"""

import logging
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from src.lib.agent_studio.execution_revision_service import get_execution_revision
from src.lib.agent_studio.generic_profile_service import get_profile_revision
from src.lib.benchmarks.saved_flows import flow_summary
from src.lib.flows.access import visible_flow_filter
from src.lib.flows.execution_revisions import resolve_flow_execution_revisions
from src.models.sql.agent import Agent
from src.models.sql.curation_flow import CurationFlow
from src.schemas.agent_execution_revision import AgentExecutionReceipt
from src.schemas.flows import FlowDefinition

from .bundle import (FORMAT, FORMAT_VERSION, MAX_AGENTS, MAX_PROFILE_REVISIONS,
                     MAX_REVISIONS_PER_AGENT, BundleTooLarge, FlowBundle, InvalidBundle,
                     check_bundle)
from .reasons import ReasonCode, reason_for_finding

logger = logging.getLogger(__name__)
EXPORT_PAGE_SIZE = 50


@dataclass(frozen=True)
class ExportCurator:
    subject: str
    issuer: str
    user_id: int
    groups: list[str]


@dataclass(frozen=True)
class EvaluatedFlow:
    flow_id: UUID
    name: str
    description: str | None
    owned: bool
    version: str
    reason: ReasonCode | None
    bundle: dict[str, Any] | None


def exportable_flows(db: Session, user_id: int, *, offset: int,
                     limit: int) -> tuple[list[CurationFlow], int]:
    query = db.query(CurationFlow).filter(
        CurationFlow.is_active.is_(True), visible_flow_filter(user_id),
    ).order_by(CurationFlow.name, CurationFlow.id)
    return query.offset(offset).limit(limit).all(), query.count()


def _gate(db: Session, flow: CurationFlow,
          curator: ExportCurator) -> tuple[ReasonCode | None, list[AgentExecutionReceipt]]:
    from src.api import flows as flows_api

    definition = FlowDefinition.model_validate(flow.flow_definition)
    resolved = resolve_flow_execution_revisions(db, definition, user_id=curator.user_id,
                                                active_group_ids=curator.groups)
    errors = [finding for finding in resolved.findings if finding.severity == "error"]
    if errors:
        return reason_for_finding(errors[0].code), []
    response = flows_api._flow_to_response(flow, viewer_user_id=curator.user_id,
                                           active_group_ids=curator.groups, db=db)
    if response.has_critical_issues:
        return "cannot_run", []
    pins = []
    for node in resolved.definition.nodes:
        entry = resolved.entries_by_node.get(node.id)
        if entry is None:
            continue
        receipt = AgentExecutionReceipt.model_validate(entry["execution_receipt"])
        if receipt.output_contract.output_mode == "unprofiled_generic":
            return "flexible_output", []
        pins.append(receipt)
    return None, pins


def _closure(db: Session, curator: ExportCurator,
             pins: list[AgentExecutionReceipt]) -> tuple[list[dict], list[dict]] | None:
    agents: dict[UUID, dict[str, Any]] = {}
    profiles: dict[UUID, dict[str, Any]] = {}
    seen: set[UUID] = set()
    for receipt in pins:
        if receipt.agent_revision_id in seen:
            continue
        seen.add(receipt.agent_revision_id)
        row, saved = get_execution_revision(db, receipt.agent_id, receipt.agent_revision_id,
                                            curator.user_id, active_group_ids=curator.groups)
        if receipt.agent_id not in agents:
            source = db.get(Agent, receipt.agent_id)
            agents[receipt.agent_id] = {
                "source_agent_id": str(source.id), "source_agent_key": source.agent_key,
                "name": source.name, "description": source.description, "icon": source.icon,
                "category": source.category, "revisions": [],
            }
        agents[receipt.agent_id]["revisions"].append({
            "source_revision_id": str(row.id), "revision": row.revision,
            "fingerprint": row.fingerprint, "snapshot": row.snapshot,
        })
        pin = saved.output_contract.generic_profile_ref
        if pin is not None:
            entry = profiles.setdefault(pin.profile_id, {"source_profile_id": str(pin.profile_id),
                                                         "revisions": []})
            if all(item["source_revision_id"] != str(pin.profile_revision_id)
                   for item in entry["revisions"]):
                revision = get_profile_revision(db, pin.profile_id, pin.revision, curator.user_id,
                                                include_archived=True)
                entry["revisions"].append({
                    "source_revision_id": str(revision.id), "revision": revision.revision,
                    "fingerprint": revision.fingerprint, "contract": revision.contract,
                })
    if (len(agents) > MAX_AGENTS
            or any(len(agent["revisions"]) > MAX_REVISIONS_PER_AGENT for agent in agents.values())
            or sum(len(profile["revisions"]) for profile in profiles.values()) > MAX_PROFILE_REVISIONS):
        return None
    for agent in agents.values():
        agent["revisions"].sort(key=lambda item: item["revision"])
    for profile in profiles.values():
        profile["revisions"].sort(key=lambda item: item["revision"])
    return (sorted(agents.values(), key=lambda item: item["source_agent_id"]),
            sorted(profiles.values(), key=lambda item: item["source_profile_id"]))


def _bundle(db: Session, flow: CurationFlow, curator: ExportCurator, version: str, *, issuer: str,
            app_version: str, exported_at: str) -> tuple[ReasonCode | None, dict[str, Any] | None]:
    reason, pins = _gate(db, flow, curator)
    if reason is not None:
        return reason, None
    closure = _closure(db, curator, pins)
    if closure is None:
        return "too_large", None
    agents, profiles = closure
    bundle_json = FlowBundle.model_validate({
        "format": FORMAT, "format_version": FORMAT_VERSION,
        "source": {"issuer": issuer, "app_version": app_version},
        "exported_for": {"iss": curator.issuer, "sub": curator.subject},
        "exported_at": exported_at,
        "flow": {"source_flow_id": str(flow.id), "source_version": version,
                 "owned": flow.user_id == curator.user_id, "name": flow.name,
                 "description": flow.description, "definition": flow.flow_definition},
        "agents": agents, "profiles": profiles,
    }).model_dump(mode="json")
    try:
        check_bundle(bundle_json)
    except BundleTooLarge:
        return "too_large", None
    except InvalidBundle as exc:
        # Main would hand the resolver a bundle it must refuse: a defect, not a curator problem.
        logger.error("flow_export_inconsistent", extra={
            "event": "flow_export_inconsistent", "check": str(exc), "source_flow_id": str(flow.id)})
        return "cannot_run", None
    return None, bundle_json


def evaluate_flow(db: Session, flow: CurationFlow, curator: ExportCurator, *, issuer: str,
                  app_version: str, exported_at: str) -> EvaluatedFlow:
    """The flow's list item, and its bundle when it can be imported. One broken flow
    never fails a whole list: an unreadable flow is ``cannot_run``."""
    version = flow_summary(flow).revision
    try:
        reason, bundle_json = _bundle(db, flow, curator, version, issuer=issuer,
                                      app_version=app_version, exported_at=exported_at)
    except (ValueError, HTTPException):
        reason, bundle_json = "cannot_run", None
    if reason is not None:
        logger.info("flow_export_refused", extra={
            "event": "flow_export_refused", "reason": reason, "source_flow_id": str(flow.id)})
    return EvaluatedFlow(flow_id=flow.id, name=flow.name, description=flow.description,
                         owned=flow.user_id == curator.user_id, version=version, reason=reason,
                         bundle=bundle_json)
