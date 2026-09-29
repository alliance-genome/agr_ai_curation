"""Authorized, side-effect-free saved-flow discovery for benchmark preparation.

Discovery receipts describe a mutable saved flow at read time. They are not
executable snapshots; the execution-freeze boundary must reauthorize selection.
"""

from typing import Any, Literal
from uuid import UUID

from sqlalchemy.orm import Session

from src.lib.agent_studio.catalog_service import get_active_visible_agent_metadata
from src.lib.agent_studio.execution_revision_service import get_execution_revision
from src.lib.flows.access import get_visible_flow, visible_flow_filter
from src.lib.flows.execution_revisions import resolve_flow_execution_revisions
from src.lib.flows.formatter_capability import snapshot_formatter_format
from src.models.sql.curation_flow import CurationFlow
from src.schemas.flows import FlowDefinition

from .execution_context import BenchmarkCuratorContext
from .flow_contracts import (
    BenchmarkFlowOutputContract,
    OutputKind,
    discover_output_contract,
    step_output_kind,
)
from .flow_stages import BenchmarkFlowStage, authorize_scheduled_validator, flow_stages
from .flow_model_defaults import stage_model_defaults
from .models import FrozenStrictModel
from .suites import _digest


class BenchmarkSavedFlowSummary(FrozenStrictModel):
    source_kind: Literal["saved_flow"] = "saved_flow"
    flow_id: UUID
    title: str
    description: str | None
    revision: str


StepProblem = Literal["needs_resave", "unavailable_agent", "unreadable_structure"]

UNREADABLE_FLOW_REASON = (
    "A flow node or its saved output structure is unavailable. Open the flow in AI Curation "
    "and verify its agents and revisions."
)
UNDECLARED_REASON = (
    "One or more steps have no declared output structure; select a verified source for mapping."
)
UNAVAILABLE_AGENT_REASON = (
    "This step's agent isn't available to you. It may be private to the flow's owner or no "
    "longer exist."
)
UNREADABLE_STRUCTURE_REASON = (
    "This step's saved output structure can't be read. Open the flow in AI Curation and "
    "re-save this step's agent."
)
RESAVE_MODEL = ("A step uses a model or setting that is no longer available. Open the flow in "
                "AI Curation and re-save that step's agent.")
RESAVE_LOOKUPS = ("A step's agent still has database lookup tools. Open it in AI Curation and "
                  "re-save it without them.")
AGENT_UNAVAILABLE = "A step's agent isn't available to you."
CHOOSE_FIELDS_AGAIN = ("A file output step needs its fields chosen again. Open the flow in "
                       "AI Curation.")
CANNOT_RUN = ("This flow can't run right now. Open it in AI Curation and check its steps and "
              "validators.")

# Findings raised after the resolver authorized the step's pinned receipt: its structure is
# still readable from that receipt; only running it needs a re-save.
RECOVERABLE_CODES = frozenset({
    "unavailable_model", "unsupported_reasoning_effort", "extraction_identity_lookup_tools",
})
UNAVAILABLE_CODES = frozenset({
    "missing_execution_revision", "unavailable_execution_revision", "execution_contract_mismatch",
})
PROJECTION_CODES = frozenset({
    "invalid_profile_projection", "unavailable_projection_profile", "invalid_selected_export",
    "invalid_projection_field_reference", "undeclared_projection_field",
    "direct_export_requires_fields", "incompatible_projection_field_type",
})


def run_problem_for_code(code: str) -> str:
    """The fixed curator sentence for a finding code; a finding's own message is never used."""
    if code in ("unavailable_model", "unsupported_reasoning_effort"):
        return RESAVE_MODEL
    if code == "extraction_identity_lookup_tools":
        return RESAVE_LOOKUPS
    if code in UNAVAILABLE_CODES:
        return AGENT_UNAVAILABLE
    if code in PROJECTION_CODES:
        return CHOOSE_FIELDS_AGAIN
    return CANNOT_RUN


class BenchmarkSavedFlowNodeContract(FrozenStrictModel):
    node_id: str
    title: str
    output_key: str
    contract: BenchmarkFlowOutputContract
    problem: StepProblem | None = None
    output_kind: OutputKind
    validated_bindings: tuple[str, ...] = ()


class BenchmarkSavedFlowContracts(FrozenStrictModel):
    flow: BenchmarkSavedFlowSummary
    nodes: tuple[BenchmarkSavedFlowNodeContract, ...]
    status: Literal["verified", "not_verified"]
    reason: str | None = None
    runnable: bool
    run_problem: str | None = None
    stages: tuple[BenchmarkFlowStage, ...] = ()
    route_default_conflicts: tuple[str, ...] = ()


class BenchmarkSavedFlowPage(FrozenStrictModel):
    items: tuple[BenchmarkSavedFlowSummary, ...]
    total_items: int
    next_offset: int | None = None


def flow_summary(flow: CurationFlow) -> BenchmarkSavedFlowSummary:
    return BenchmarkSavedFlowSummary(
        flow_id=flow.id, title=flow.name, description=flow.description,
        revision=_digest({
            "flow_id": str(flow.id), "name": flow.name,
            "description": flow.description, "definition": flow.flow_definition,
        }),
    )


def visible_saved_flows(session: Session, curator: BenchmarkCuratorContext):
    """Return a query so API pagination bounds database reads before hydration."""
    return session.query(CurationFlow).filter(
        CurationFlow.is_active.is_(True), visible_flow_filter(curator.db_user_id),
    ).order_by(CurationFlow.name, CurationFlow.id)


def _unavailable_step(base: dict[str, Any], problem: StepProblem, reason: str) -> dict[str, Any]:
    return {**base, "problem": problem, "output_kind": "unavailable",
            "contract": BenchmarkFlowOutputContract(status="not_verified", reason=reason)}


def _step(session: Session, curator: BenchmarkCuratorContext, node: Any,
          resolved_entries: dict[str, dict[str, Any] | None], codes: dict[str, str],
          entries: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """One step's contract, independent of every other step's failures."""
    base = {"node_id": node.id, "title": node.data.agent_display_name,
            "output_key": node.data.output_key}
    agent_id = node.data.agent_id
    problem: StepProblem | None = None
    metadata: dict[str, Any] | None
    if node.id in resolved_entries:
        metadata = resolved_entries[node.id]
        if metadata is None:
            receipt = node.data.execution_receipt
            # Recovery keys on the finding code, never on a receipt being present: a
            # supplied receipt that failed authorization is still on the node.
            if codes.get(node.id) not in RECOVERABLE_CODES or receipt is None:
                return _unavailable_step(base, "unavailable_agent", UNAVAILABLE_AGENT_REASON)
            # The resolver authorized this exact receipt before its model/tool checks failed;
            # discover_output_contract authorizes it again.
            metadata = {"execution_receipt": receipt.model_dump(mode="json")}
            problem = "needs_resave"
    else:
        try:
            metadata = get_active_visible_agent_metadata(
                agent_id, db_user_id=curator.db_user_id,
                authenticated_groups=list(curator.active_groups),
            )
        except ValueError:
            metadata = None
        if metadata is None:
            return _unavailable_step(base, "unavailable_agent", UNAVAILABLE_AGENT_REASON)
    try:
        contract = discover_output_contract(session, curator, agent_id=agent_id, metadata=metadata)
        if problem == "needs_resave" and receipt.output_contract.output_state == "none":
            # A saved formatter copy declares its format on the revision, not the receipt;
            # read it from the same authorized revision the resolver pinned.
            _, saved = get_execution_revision(
                session, receipt.agent_id, receipt.agent_revision_id, curator.db_user_id,
                active_group_ids=list(curator.active_groups),
            )
            metadata = {**metadata, "output_formatter_format": snapshot_formatter_format(saved)}
        output_kind = step_output_kind(agent_id, metadata, contract)
    except ValueError:
        return _unavailable_step(base, "unreadable_structure", UNREADABLE_STRUCTURE_REASON)
    if problem is None:
        entries[node.id] = metadata
    return {**base, "contract": contract, "problem": problem, "output_kind": output_kind}


def _run_problem(errors: list[Any], steps: list[dict[str, Any]]) -> str:
    if errors:
        return run_problem_for_code(errors[0].code)
    problem = next(step["problem"] for step in steps if step["problem"] is not None)
    return AGENT_UNAVAILABLE if problem == "unavailable_agent" else CANNOT_RUN


def saved_flow_contracts(
    session: Session, curator: BenchmarkCuratorContext, flow_id: UUID,
    *, expected_revision: str | None = None,
) -> BenchmarkSavedFlowContracts:
    """Reauthorize the flow, then describe every step on its own.

    A shared flow is not permission to inspect its owner's private agents or
    profiles: such a step is reported unavailable with one generic reason and no
    receipt. Only an unreadable flow definition blanks the whole flow. Stages and
    model defaults are read only when every step resolved; ``stages`` is empty
    whenever the flow can't run. Callers translate access failures into their
    standard sanitized API errors.
    """
    flow = get_visible_flow(session, flow_id, curator.db_user_id)
    summary = flow_summary(flow)
    if expected_revision is not None and expected_revision != summary.revision:
        raise ValueError("Saved flow changed; refresh flow discovery before selecting it")
    try:
        definition = FlowDefinition.model_validate(flow.flow_definition)
        resolved = resolve_flow_execution_revisions(
            session, definition, user_id=curator.db_user_id,
            active_group_ids=list(curator.active_groups),
        )
    except ValueError:
        return BenchmarkSavedFlowContracts(
            flow=summary, nodes=(), status="not_verified", reason=UNREADABLE_FLOW_REASON,
            runnable=False, run_problem=CANNOT_RUN,
        )
    errors = [finding for finding in resolved.findings if finding.severity == "error"]
    codes: dict[str, str] = {}
    for finding in errors:
        if finding.node_id is not None:
            codes.setdefault(finding.node_id, finding.code)
    entries: dict[str, dict[str, Any]] = {}
    steps = [
        _step(session, curator, node, resolved.entries_by_node, codes, entries)
        for node in resolved.definition.nodes if node.type != "task_input"
    ]
    runnable = not errors and all(step["problem"] is None for step in steps)
    stages: tuple[BenchmarkFlowStage, ...] = ()
    conflicts: tuple[str, ...] = ()
    run_problem = None if runnable else _run_problem(errors, steps)
    if runnable:
        try:
            stages = flow_stages(
                resolved.definition, entries,
                authorize_validator=lambda node_id, binding: authorize_scheduled_validator(
                    session, curator, entries[node_id], binding,
                ),
            )
            stages, conflicts = stage_model_defaults(session, curator, stages)
        except ValueError:
            runnable, stages, conflicts, run_problem = False, (), (), CANNOT_RUN
    nodes = tuple(
        BenchmarkSavedFlowNodeContract(**step, validated_bindings=tuple(sorted({
            stage.binding_id for stage in stages
            if stage.binding_id and stage.source_node_id == step["node_id"]
        })))
        for step in steps
    )
    verified = bool(nodes) and all(node.contract.status == "verified" for node in nodes)
    return BenchmarkSavedFlowContracts(
        flow=summary, nodes=nodes, stages=stages, route_default_conflicts=conflicts,
        status="verified" if verified else "not_verified",
        reason=None if verified else UNDECLARED_REASON,
        runnable=runnable, run_problem=run_problem,
    )
