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
from pydantic import ValidationError
from sqlalchemy.orm import Session

from src.lib.agent_studio.execution_revision_service import get_execution_revision
from src.lib.benchmarks.saved_flows import flow_summary
from src.lib.flows.access import visible_flow_filter
from src.lib.flows.execution_revisions import resolve_flow_execution_revisions
from src.lib.observability.runtime import report_runtime_exception, sanitized_runtime_error
from src.lib.openai_agents.config import (get_flow_transfer_max_agents,
                                          get_flow_transfer_max_output_structure_revisions,
                                          get_flow_transfer_max_revisions_per_agent)
from src.models.sql.agent import Agent
from src.models.sql.curation_flow import CurationFlow
from src.models.sql.generic_extraction_profile import GenericExtractionProfileRevision
from src.schemas.agent_execution_revision import AgentExecutionReceipt
from src.schemas.flows import FlowDefinition

from .bundle import (FORMAT, FORMAT_VERSION, BundleTooLarge, FlowBundle, InvalidBundle,
                     check_bundle)
from .reasons import ReasonCode, reason_for_finding

logger = logging.getLogger(__name__)


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

    try:
        definition = FlowDefinition.model_validate(flow.flow_definition)
    except ValidationError:
        return "cannot_run", []  # the stored flow no longer opens: the curator can repair it
    resolved = resolve_flow_execution_revisions(db, definition, user_id=curator.user_id,
                                                active_group_ids=curator.groups)
    errors = [finding for finding in resolved.findings if finding.severity == "error"]
    if errors:
        return reason_for_finding(errors[0].code), []
    try:
        response = flows_api._flow_to_response(flow, viewer_user_id=curator.user_id,
                                               active_group_ids=curator.groups, db=db)
    except HTTPException:
        return "cannot_run", []  # the flows page refuses it the same way
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
                # The authorized snapshot names this exact revision; read it by id
                # (spec 7.3 step 4), never by number through the owner's visibility.
                revision = db.get(GenericExtractionProfileRevision, pin.profile_revision_id)
                if revision is None or ((revision.profile_id, revision.revision, revision.fingerprint)
                                        != (pin.profile_id, pin.revision, pin.fingerprint)):
                    raise InvalidBundle("profile revision")
                entry["revisions"].append({
                    "source_revision_id": str(revision.id), "revision": revision.revision,
                    "fingerprint": revision.fingerprint, "contract": revision.contract,
                })
    per_agent = get_flow_transfer_max_revisions_per_agent()
    if (len(agents) > get_flow_transfer_max_agents()
            or any(len(agent["revisions"]) > per_agent for agent in agents.values())
            or (sum(len(profile["revisions"]) for profile in profiles.values())
                > get_flow_transfer_max_output_structure_revisions())):
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
    return None, bundle_json


def _report_inconsistent(exc: ValueError, flow_id: UUID) -> None:
    """Report through the Sentry facade with only the fixed check code (``InvalidBundle``
    messages are source-owned literals) or the exception class, never its message. The
    companion ERROR log skips log-event promotion so Sentry gets one event."""
    check = str(exc) if isinstance(exc, InvalidBundle) else type(exc).__name__
    report_runtime_exception(
        sanitized_runtime_error(f"Flow export bundle is inconsistent ({check})"),
        component="flow_export", operation="flow_export_inconsistent",
        context={"check": check, "flow_id": str(flow_id)},
        fingerprint=["flow_export", "flow_export_inconsistent", check],
    )
    logger.error("flow_export_inconsistent", extra={
        "event": "flow_export_inconsistent", "check": check, "source_flow_id": str(flow_id),
        "sentry_skip_event": True})


def evaluate_flow(db: Session, flow: CurationFlow, curator: ExportCurator, *, issuer: str,
                  app_version: str, exported_at: str) -> EvaluatedFlow:
    """The flow's list item, and its bundle when it can be imported. One broken flow
    never fails a whole list: an unreadable flow is ``cannot_run``."""
    version = flow_summary(flow).revision
    try:
        reason, bundle_json = _bundle(db, flow, curator, version, issuer=issuer,
                                      app_version=app_version, exported_at=exported_at)
    except ValueError as exc:
        # Every curator-fixable problem is a reason above. Anything else (an integrity
        # failure, a bundle the resolver must refuse) is a defect in main, reported to
        # Sentry. The curator still sees the fixed "cannot_run" reason (the eight reason
        # codes are a cross-repo contract) and the rest of the list still answers.
        _report_inconsistent(exc, flow.id)
        reason, bundle_json = "cannot_run", None
    else:
        if reason is not None:
            logger.info("flow_export_refused", extra={
                "event": "flow_export_refused", "reason": reason,
                "source_flow_id": str(flow.id)})
    return EvaluatedFlow(flow_id=flow.id, name=flow.name, description=flow.description,
                         owned=flow.user_id == curator.user_id, version=version, reason=reason,
                         bundle=bundle_json)
