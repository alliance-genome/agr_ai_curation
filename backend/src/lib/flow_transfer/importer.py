"""Import a verified flow into the benchmark resolver as the curator's own private copy.

Design section 8.3. Flow save validation looks agents up through its own database
session, so the private copies of output structures, agents and their revisions are
written and committed first (phase 1, all or nothing). The flow, its definition and
the import record then change together in one transaction (phase 2). Phase 1 rows
are private, deterministic and reused unchanged by a retry, so a phase 2 failure
leaves nothing a curator can see. Callers own the sessions and the commits.
"""

from copy import deepcopy
from dataclasses import dataclass
from typing import Callable, Literal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.lib.agent_studio.custom_agent_service import custom_agent_name_exists, make_custom_agent_id
from src.lib.agent_studio.execution_revision_service import insert_imported_execution_revision
from src.lib.agent_studio.profile_mapping_service import (persist_capability_references,
                                                          validate_profile_mappings)
from src.lib.benchmarks.saved_flows import flow_summary
from src.lib.flows.execution_revisions import (flow_execution_revision_findings,
                                               resolve_flow_execution_revisions)
from src.lib.flows.flow_service import save_flow_definition
from src.lib.flows.repin import move_layouts
from src.models.sql.agent import Agent
from src.models.sql.agent_execution_revision import AgentExecutionRevision
from src.models.sql.benchmark_flow_import import BenchmarkFlowImport
from src.models.sql.curation_flow import CurationFlow
from src.models.sql.generic_extraction_profile import (
    GenericExtractionProfile as Profile,
    GenericExtractionProfileRevision as ProfileRevision,
)
from src.models.sql.user import User
from src.schemas.agent_execution_revision import AgentExecutionSnapshot
from src.schemas.flows import FLOW_NAME_MAX_CHARS, FlowDefinition
from src.schemas.generic_extraction_profile import normalize_profile_contract

from .bundle import BundleAgent, BundleProfileRevision, CheckedBundle
from .ids import DerivedKind, derived_id
from .reasons import ReasonCode, reason_for_finding, reason_for_save_refusal

AGENT_NAME_MAX_CHARS = 255
# Exactly the editable head fields Workshop clone copies from a saved snapshot.
HEAD_FIELDS = (
    "model_id", "model_temperature", "model_reasoning", "instructions", "tool_ids",
    "group_tool_policy", "allowed_group_ids", "inherited_allowed_group_ids", "group_rules_enabled",
    "group_rules_component", "group_prompt_overrides", "template_source",
)


@dataclass(frozen=True)
class ImportContext:
    user_id: int
    subject: str
    groups: list[str]
    export_issuer: str


@dataclass(frozen=True)
class ImportResult:
    outcome: Literal["imported", "updated", "unchanged"]
    flow_id: UUID
    version: int


class ImportRefused(ValueError):
    """Something the curator fixes in AI Curation; ``reason`` is one plain code."""

    def __init__(self, reason: ReasonCode, check: str) -> None:
        super().__init__(check)
        self.reason = reason


class ImportConflict(ValueError):
    """A derived id or a revision number is already taken by different content."""


def copy_name(source: str, is_taken: Callable[[str], bool], *, max_chars: int) -> str:
    """``source`` when free, else the established (Copy), (Copy 2) convention, cut to fit."""
    if not is_taken(source):
        return source
    number = 1
    while True:
        suffix = " (Copy)" if number == 1 else f" (Copy {number})"
        candidate = source[: max_chars - len(suffix)] + suffix
        if not is_taken(candidate):
            return candidate
        number += 1


def _derived(ctx: ImportContext, kind: DerivedKind, source_id: UUID) -> UUID:
    return derived_id(kind, source_id, export_issuer=ctx.export_issuer, importer_sub=ctx.subject)


def _lock_curator(db: Session, ctx: ImportContext) -> None:
    """Serialize one curator's imports (two tabs, double clicks)."""
    db.execute(select(User.id).where(User.id == ctx.user_id).with_for_update()).scalar_one()


def latest_import(db: Session, ctx: ImportContext, source_flow_id: UUID) -> BenchmarkFlowImport | None:
    return db.execute(
        select(BenchmarkFlowImport).where(
            BenchmarkFlowImport.user_id == ctx.user_id,
            BenchmarkFlowImport.export_issuer == ctx.export_issuer,
            BenchmarkFlowImport.source_flow_id == source_flow_id,
        ).order_by(BenchmarkFlowImport.version.desc()).limit(1)
    ).scalar_one_or_none()


def latest_imports(db: Session, *, user_id: int, export_issuer: str,
                   source_flow_ids: list[UUID]) -> list[BenchmarkFlowImport]:
    """The latest import of each named source flow whose copy is still an active flow."""
    rows = db.execute(
        select(BenchmarkFlowImport)
        .join(CurationFlow, CurationFlow.id == BenchmarkFlowImport.flow_id)
        .where(BenchmarkFlowImport.user_id == user_id,
               BenchmarkFlowImport.export_issuer == export_issuer,
               BenchmarkFlowImport.source_flow_id.in_(source_flow_ids),
               CurationFlow.is_active.is_(True), CurationFlow.user_id == user_id)
        .order_by(BenchmarkFlowImport.source_flow_id, BenchmarkFlowImport.version.desc())
    ).scalars().all()
    latest: dict[UUID, BenchmarkFlowImport] = {}
    for row in rows:
        latest.setdefault(row.source_flow_id, row)
    return list(latest.values())


def unchanged_import(db: Session, ctx: ImportContext, checked: CheckedBundle) -> ImportResult | None:
    """The same AI Curation version is already the current, untouched copy: write nothing."""
    source = checked.bundle.flow
    latest = latest_import(db, ctx, source.source_flow_id)
    if latest is None or latest.source_version != source.source_version:
        return None
    flow = db.get(CurationFlow, latest.flow_id)
    if flow is None or not flow.is_active or flow_summary(flow).revision != latest.flow_revision:
        return None
    return ImportResult(outcome="unchanged", flow_id=latest.flow_id, version=latest.version)


def _import_profile_revision(db: Session, ctx: ImportContext, profile_id: UUID, revision_id: UUID,
                             revision: BundleProfileRevision) -> None:
    parsed = normalize_profile_contract(revision.contract)
    existing = db.get(ProfileRevision, revision_id)
    if existing is not None:
        if existing.profile_id != profile_id or existing.fingerprint != parsed.fingerprint():
            raise ImportConflict("output structure revision")
        return
    taken = db.execute(select(ProfileRevision.id).where(
        ProfileRevision.profile_id == profile_id, ProfileRevision.revision == revision.revision,
    )).scalar_one_or_none()
    if taken is not None:
        raise ImportConflict("output structure revision number")
    try:
        capabilities = validate_profile_mappings(parsed, active_group_ids=ctx.groups,
                                                 user_id=ctx.user_id)
    except ValueError:
        raise ImportRefused("cannot_run", "output structure checks") from None
    row = ProfileRevision(id=revision_id, profile_id=profile_id, revision=revision.revision,
                          fingerprint=parsed.fingerprint(), contract=parsed.model_dump(mode="json"),
                          creator_id=ctx.user_id)
    db.add(row)
    db.flush()
    try:
        persist_capability_references(db, row, capabilities)
    except ValueError:
        raise ImportRefused("cannot_run", "output structure check versions") from None


def _import_profiles(db: Session, ctx: ImportContext,
                     checked: CheckedBundle) -> dict[UUID, tuple[UUID, UUID]]:
    """Source output structure revision id -> (copy's profile id, copy's revision id)."""
    pins: dict[UUID, tuple[UUID, UUID]] = {}
    for profile in checked.bundle.profiles:
        profile_id = _derived(ctx, "profile", profile.source_profile_id)
        newest = max(profile.revisions, key=lambda item: item.revision)
        contract = normalize_profile_contract(newest.contract)
        row = db.get(Profile, profile_id)
        if row is None:
            row = Profile(id=profile_id, owner_id=ctx.user_id, project_id=None, visibility="private",
                          archived=False, name=contract.name, description=contract.description,
                          semantic_class=contract.semantic_class, head_revision=newest.revision)
            db.add(row)
        elif row.owner_id != ctx.user_id:
            raise ImportConflict("output structure owner")
        for revision in profile.revisions:
            revision_id = _derived(ctx, "profile_revision", revision.source_revision_id)
            _import_profile_revision(db, ctx, profile_id, revision_id, revision)
            pins[revision.source_revision_id] = (profile_id, revision_id)
        if newest.revision > row.head_revision:
            row.head_revision = newest.revision
            row.name, row.description = contract.name, contract.description
            row.semantic_class = contract.semantic_class
        db.flush()
    return pins


def _rewritten(saved: AgentExecutionSnapshot,
               profile_pins: dict[UUID, tuple[UUID, UUID]]) -> AgentExecutionSnapshot:
    """The same snapshot pinned to the copy's output structure; its fingerprint changes."""
    data = saved.model_dump(mode="json")
    ref = data["output_contract"].get("generic_profile_ref")
    if ref is not None:
        profile_id, revision_id = profile_pins[UUID(ref["profile_revision_id"])]
        ref["profile_id"], ref["profile_revision_id"] = str(profile_id), str(revision_id)
    return AgentExecutionSnapshot.model_validate(data)


def _copy_head(row: Agent, saved: AgentExecutionSnapshot) -> None:
    for field in HEAD_FIELDS:
        setattr(row, field, deepcopy(getattr(saved, field)))
    row.output_schema_key = saved.output_contract.output_schema_key


def _import_agent(db: Session, ctx: ImportContext, agent: BundleAgent, checked: CheckedBundle,
                  profile_pins: dict[UUID, tuple[UUID, UUID]]) -> None:
    agent_id = _derived(ctx, "agent", agent.source_agent_id)
    key = make_custom_agent_id(agent_id)
    snapshots = {revision.source_revision_id: _rewritten(checked.snapshots[revision.source_revision_id],
                                                         profile_pins)
                 for revision in agent.revisions}
    row = db.get(Agent, agent_id)
    if row is not None and (row.user_id != ctx.user_id or row.agent_key != key):
        raise ImportConflict("agent owner")
    name = copy_name(agent.name, lambda candidate: custom_agent_name_exists(
        db, ctx.user_id, candidate, excluding_id=agent_id), max_chars=AGENT_NAME_MAX_CHARS)
    if row is None:
        newest = max(agent.revisions, key=lambda item: item.revision)
        row = Agent(id=agent_id, agent_key=key, user_id=ctx.user_id, visibility="private",
                    project_id=None, shared_at=None, is_active=True, version=1,
                    supervisor_enabled=False, supervisor_batchable=False, show_in_palette=True,
                    name=name)
        _copy_head(row, snapshots[newest.source_revision_id])
        db.add(row)
    row.name, row.description, row.icon, row.category = name, agent.description, agent.icon, agent.category
    row.is_active = True
    db.flush()
    for revision in sorted(agent.revisions, key=lambda item: item.revision):
        revision_id = _derived(ctx, "agent_revision", revision.source_revision_id)
        snapshot = snapshots[revision.source_revision_id]
        existing = db.get(AgentExecutionRevision, revision_id)
        if existing is not None:
            if existing.agent_id != agent_id or existing.fingerprint != snapshot.fingerprint():
                raise ImportConflict("agent revision")
            continue
        taken = db.execute(select(AgentExecutionRevision.id).where(
            AgentExecutionRevision.agent_id == agent_id,
            AgentExecutionRevision.revision == revision.revision,
        )).scalar_one_or_none()
        if taken is not None:
            raise ImportConflict("agent revision number")
        insert_imported_execution_revision(db, row, snapshot, revision_id=revision_id,
                                           revision_number=revision.revision, creator_id=ctx.user_id)
    head = db.execute(select(AgentExecutionRevision).where(AgentExecutionRevision.agent_id == agent_id)
                      .order_by(AgentExecutionRevision.revision.desc()).limit(1)).scalar_one()
    row.execution_revision_id = head.id
    _copy_head(row, AgentExecutionSnapshot.model_validate(head.snapshot))
    db.flush()


def import_dependencies(db: Session, ctx: ImportContext, checked: CheckedBundle) -> None:
    """Phase 1: private copies of output structures, agents and the pinned revisions."""
    _lock_curator(db, ctx)
    profile_pins = _import_profiles(db, ctx, checked)
    for agent in checked.bundle.agents:
        _import_agent(db, ctx, agent, checked, profile_pins)
    db.flush()


def _flow_name(db: Session, user_id: int, source: str, flow_id: UUID) -> str:
    taken = set(db.scalars(select(CurationFlow.name).where(
        CurationFlow.user_id == user_id, CurationFlow.is_active.is_(True), CurationFlow.id != flow_id,
    )).all())
    return copy_name(source, taken.__contains__, max_chars=FLOW_NAME_MAX_CHARS)


def import_flow(db: Session, ctx: ImportContext, checked: CheckedBundle) -> ImportResult:
    """Phase 2: the curator's flow copy and its import record, all or nothing."""
    _lock_curator(db, ctx)
    source = checked.bundle.flow
    definition = FlowDefinition.model_validate(deepcopy(source.definition))
    pins = []
    for node in definition.nodes:
        if node.id not in checked.pins:
            continue
        source_agent, source_revision = checked.pins[node.id]
        node.data.agent_id = make_custom_agent_id(_derived(ctx, "agent", source_agent))
        node.data.agent_revision_id = _derived(ctx, "agent_revision", source_revision)
        # The copy's exact revision supplies its receipt when the flow is resolved.
        node.data.execution_receipt = None
        pin = {"node_id": node.id, "source_agent_revision_id": str(source_revision),
               "agent_revision_id": str(node.data.agent_revision_id)}
        ref = checked.snapshots[source_revision].output_contract.generic_profile_ref
        if ref is not None:
            pin["source_profile_revision_id"] = str(ref.profile_revision_id)
            pin["profile_revision_id"] = str(_derived(ctx, "profile_revision", ref.profile_revision_id))
        pins.append(pin)
    resolved = resolve_flow_execution_revisions(db, definition, user_id=ctx.user_id,
                                                active_group_ids=ctx.groups)
    errors = [finding for finding in resolved.findings if finding.severity == "error"]
    if errors:
        raise ImportRefused(reason_for_finding(errors[0].code), "resolve")
    try:
        move_layouts(definition, resolved.projection_catalogs, checked.receipts)
    except ValueError:
        raise ImportRefused("fields_need_choosing", "layout") from None
    flow_id = _derived(ctx, "flow", source.source_flow_id)
    flow = db.get(CurationFlow, flow_id)
    if flow is not None and flow.user_id != ctx.user_id:
        raise ImportConflict("flow owner")
    name = _flow_name(db, ctx.user_id, source.name, flow_id)
    created = flow is None
    if created:
        flow = CurationFlow(id=flow_id, user_id=ctx.user_id, flow_definition={})
    flow.visibility, flow.project_id, flow.shared_at, flow.is_active = "private", None, None, True
    flow.name, flow.description = name, source.description
    try:
        save_flow_definition(db, flow, definition, active_group_ids=ctx.groups)
    except HTTPException as exc:
        if exc.status_code != 422:
            raise
        raise ImportRefused(reason_for_save_refusal(exc.detail), "save") from None
    if created:
        db.add(flow)
    db.flush()
    findings = flow_execution_revision_findings(db, flow.flow_definition, user_id=ctx.user_id,
                                                active_group_ids=ctx.groups)
    if findings:
        raise ImportRefused(reason_for_finding(str(findings[0].get("code"))), "after save")
    previous = latest_import(db, ctx, source.source_flow_id)
    version = 1 if previous is None else previous.version + 1
    db.add(BenchmarkFlowImport(
        user_id=ctx.user_id, export_issuer=ctx.export_issuer, source_flow_id=source.source_flow_id,
        source_version=source.source_version, flow_id=flow.id, version=version,
        flow_revision=flow_summary(flow).revision, pins=pins, bundle_sha256=checked.sha256,
        source_app_version=checked.bundle.source.app_version,
    ))
    db.flush()
    return ImportResult(outcome="imported" if previous is None else "updated", flow_id=flow.id,
                        version=version)
