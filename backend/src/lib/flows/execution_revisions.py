"""Resolve custom flow nodes against immutable executable revisions, never heads.

The flow remains mutable. These are node references, not snapshots of the flow.
The caller owns persistence and supplies the authenticated user/group context.
"""

from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.lib.agent_studio.authoring_validation import AuthoringValidationFinding
from src.lib.agent_studio.execution_revision_service import (
    ExecutionRevisionNotFoundError,
    authorize_execution_receipt,
    get_execution_revision,
)
from src.models.sql.agent import Agent
from src.schemas.agent_execution_revision import AgentExecutionReceipt, AgentExecutionSnapshot
from src.schemas.flows import FlowDefinition, FlowNode


@dataclass(frozen=True)
class ResolvedFlowRevisions:
    definition: FlowDefinition
    entries_by_node: dict[str, dict[str, Any] | None]
    findings: tuple[AuthoringValidationFinding, ...]


class FlowExecutionRevisionError(ValueError):
    """Safe machine-readable findings raised before any flow specialist starts."""

    def __init__(self, findings: tuple[AuthoringValidationFinding, ...]):
        self.findings = [finding.to_dict() for finding in findings]
        super().__init__("Flow has unavailable or mismatched executable revisions; repair the identified nodes")


def flow_execution_revision_findings(
    db: Session, definition: dict, *, user_id: int | None, active_group_ids: list[str],
) -> list[dict[str, Any]]:
    """HTTP/pre-run adapter: report safe contract findings before starting work."""
    if not any(isinstance(node, dict) and isinstance(node.get("data"), dict)
               and str(node["data"].get("agent_id", "")).startswith("ca_")
               for node in definition.get("nodes", [])):
        return []
    try:
        parsed = FlowDefinition.model_validate(definition)
    except ValidationError:
        return [AuthoringValidationFinding(
            code="invalid_flow_execution_contract", severity="error",
            path="flow_definition.nodes", message="The saved flow has invalid execution references.",
            fix_hint="Open the flow, repair the identified references and validate before running.",
        ).to_dict()]
    resolved = resolve_flow_execution_revisions(
        db, parsed, user_id=user_id, active_group_ids=active_group_ids,
    )
    return [finding.to_dict() for finding in resolved.findings if finding.severity == "error"]


def _revision_entry(
    node: FlowNode, receipt: AgentExecutionReceipt, saved: AgentExecutionSnapshot,
) -> dict[str, Any]:
    from src.lib.agent_studio.catalog_service import _required_context_for_tool_ids

    required_params = _required_context_for_tool_ids(saved.tool_ids)
    structured = saved.output_contract.output_state == "structured_extraction"
    return {
        "agent_id": node.data.agent_id,
        "name": node.data.agent_display_name,
        "display_name": node.data.agent_display_name,
        "category": "Extraction" if structured else "Custom",
        "subcategory": "",
        "is_active": True, "visible": True,
        "requires_document": "document_id" in required_params,
        "required_params": required_params,
        "output_schema_key": saved.output_contract.output_schema_key,
        "produces_flow_artifacts": structured,
        "batch_capabilities": ["pdf_extraction"] if structured and "document_id" in required_params else [],
        "curation": saved.curation,
        "structured_finalization": saved.structured_finalization,
        "supervisor": {},
        "execution_receipt": receipt.model_dump(mode="json"),
    }


def resolve_flow_execution_revisions(
    db: Session | None, definition: FlowDefinition, *, user_id: int | None,
    active_group_ids: list[str],
) -> ResolvedFlowRevisions:
    """Authorize each node independently, preserving different pins of one agent.

    Selecting an explicit revision UUID may omit its redundant receipt; the exact
    revision supplies that receipt. An absent UUID is never resolved to today's
    head. This also makes unresolved legacy nodes inspectable but non-executable.
    """
    candidate = definition.model_copy(deep=True)
    entries: dict[str, dict[str, Any] | None] = {}
    findings: list[AuthoringValidationFinding] = []
    for node in candidate.nodes:
        if not node.data.agent_id.startswith("ca_"):
            continue
        entries[node.id] = None
        code = "unavailable_execution_revision"
        if node.data.agent_revision_id is None:
            code = "missing_execution_revision"
        else:
            try:
                if user_id is None or db is None:
                    raise ExecutionRevisionNotFoundError("Authenticated curator required")
                supplied = node.data.execution_receipt
                if supplied is not None:
                    receipt = authorize_execution_receipt(
                        db, supplied.model_dump(mode="json"), user_id,
                        active_group_ids=active_group_ids,
                    )
                    row, saved = get_execution_revision(
                        db, receipt.agent_id, receipt.agent_revision_id, user_id,
                        active_group_ids=active_group_ids,
                    )
                else:
                    # Read only routing identity, not mutable executable fields.
                    agent_id = db.execute(
                        select(Agent.id).where(Agent.agent_key == node.data.agent_id)
                    ).scalar_one_or_none()
                    if agent_id is None:
                        raise ExecutionRevisionNotFoundError("Executable revision unavailable")
                    row, saved = get_execution_revision(
                        db, agent_id, node.data.agent_revision_id, user_id,
                        active_group_ids=active_group_ids,
                    )
                    receipt = AgentExecutionReceipt(
                        agent_id=agent_id, agent_key=node.data.agent_id,
                        agent_revision_id=row.id, revision=row.revision,
                        fingerprint=row.fingerprint, output_contract=saved.output_contract,
                    )
                node.data.execution_receipt = receipt
                entry = _revision_entry(node, receipt, saved)
                entry["authenticated_group_ids"] = list(active_group_ids)
                entry["authenticated_user_id"] = user_id
                entries[node.id] = entry
                continue
            except ExecutionRevisionNotFoundError:
                pass
            except ValueError:
                code = "execution_contract_mismatch"
        findings.append(AuthoringValidationFinding(
            code=code, severity="error", node_id=node.id,
            path=f"flow_definition.nodes.{node.id}.data.agent_revision_id",
            message=(
                "Select an immutable executable revision for this custom node."
                if code == "missing_execution_revision" else
                "The pinned agent/profile revision is unavailable or does not match its contract."
            ),
            fix_hint="Select an accessible exact agent revision and verify downstream field references.",
        ))
    from src.lib.flows.profile_authoring import profile_projection_findings
    findings.extend(profile_projection_findings(db, candidate, entries))
    return ResolvedFlowRevisions(candidate, entries, tuple(findings))
