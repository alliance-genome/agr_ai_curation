"""Saved-profile field discovery and compatibility for formatter attachments."""
from typing import Any

from pydantic import ValidationError
from sqlalchemy.orm import Session

from src.lib.agent_studio.authoring_validation import AuthoringValidationFinding, FindingSeverity
from src.lib.curation_workspace.execution_contracts import resolve_receipt_profile
from src.lib.executable_flow_graph import project_executable_flow_graph
from src.lib.flows.output_projection import (
    FlowOutputProjectionPlan, projection_plan_field_refs, projection_plan_predicates,
)
from src.lib.flows.profile_projection import profile_projection_fields
from src.schemas.agent_execution_revision import AgentExecutionReceipt
from src.schemas.flows import FlowDefinition
from src.lib.flows.export_fields import packaged_export_fields, profile_export_fields, source_catalog
from src.lib.flows.selected_export import selection_errors


def profile_projection_findings(
    db: Session | None, definition: FlowDefinition, entries: dict[str, dict[str, Any] | None],
    catalogs: dict[str, dict] | None = None,
) -> list[AuthoringValidationFinding]:
    """Enrich transient resolved entries, never copy profile definitions to flow JSON."""
    graph = project_executable_flow_graph(definition, raise_on_invalid=False)
    nodes = {node.id: node for node in definition.nodes}
    findings: list[AuthoringValidationFinding] = []

    def finding(output_id: str, code: str, message: str, severity: FindingSeverity = "error") -> None:
        findings.append(AuthoringValidationFinding(
            code=code, severity=severity, node_id=output_id,
            path=f"flow_definition.nodes.{output_id}.data.projection_plan",
            message=message,
            fix_hint="Inspect the saved source field catalog and verify the formatter projection before saving.",
        ))

    for attachment in graph.output_attachments:
        output = nodes[attachment.output_node_id]
        declared: dict[str, list[dict[str, Any]]] = {}
        unprofiled = False
        packaged = False
        has_profile = False
        source_catalogs = {}
        for source_id in attachment.source_node_ids:
            entry = entries.get(source_id)
            if not entry:
                # System sources use their installed package, not a custom
                # execution receipt. This profile catalog cannot declare them.
                packaged = not nodes[source_id].data.agent_id.startswith("ca_") or packaged
                if packaged:
                    fields = packaged_export_fields(nodes[source_id].data.agent_id)
                    source_catalogs[source_id] = source_catalog(fields)
                continue
            receipt = AgentExecutionReceipt.model_validate(entry["execution_receipt"])
            if receipt.output_contract.output_mode == "unprofiled_generic":
                unprofiled = True
            if receipt.output_contract.output_mode == "domain":
                packaged = True
            if receipt.output_contract.generic_profile_ref is None:
                fields = packaged_export_fields(nodes[source_id].data.agent_id, entry) if packaged else []
                source_catalogs[source_id] = source_catalog(fields, entry["execution_receipt"])
                if fields:
                    entry["projection_catalog"] = source_catalogs[source_id]
                    entry["projection_fields"] = source_catalogs[source_id]["fields"]
                continue
            has_profile = True
            try:
                if db is None:
                    raise ValueError("Saved profile database is unavailable")
                profile = resolve_receipt_profile(db, receipt)
                if profile is None:
                    raise ValueError("Saved profile is unavailable")
                fields = profile_export_fields(profile_projection_fields(profile.contract))
                source_catalogs[source_id] = source_catalog(fields, entry["execution_receipt"])
                entry["projection_catalog"] = source_catalogs[source_id]
                entry["projection_fields"] = source_catalogs[source_id]["fields"]
                for field in fields:
                    declared.setdefault(field["ref"], []).append(field)
            except ValueError:
                finding(output.id, "unavailable_projection_profile", f"Source '{source_id}' has an unavailable or mismatched saved output structure.")
        if catalogs is not None:
            catalogs.update(source_catalogs)
        if output.data.export_execution_mode == "direct":
            from src.lib.openai_agents.config import get_flow_selected_fields_direct_export
            if not get_flow_selected_fields_direct_export():
                finding(output.id, "direct_export_unavailable", "Direct export is not enabled on this server.")
            if not output.data.projection_plan or output.data.projection_plan.get("selection_mode") != "selected_fields":
                finding(output.id, "direct_export_requires_fields", "Choose structured output fields before using direct export.")
        if output.data.projection_plan is None:
            continue
        try:
            plan = FlowOutputProjectionPlan.model_validate(output.data.projection_plan)
        except ValidationError as exc:
            for error in exc.errors(include_input=False, include_context=False, include_url=False):
                suffix = ".".join(str(part) for part in error["loc"])
                findings.append(AuthoringValidationFinding(
                    code="invalid_profile_projection", severity="error", node_id=output.id,
                    path=f"flow_definition.nodes.{output.id}.data.projection_plan" + (f".{suffix}" if suffix else ""),
                    message=error["msg"],
                    fix_hint="Inspect the formatter_projection_plan output contract schema and correct this field before saving.",
                ))
            continue
        if plan.selection_mode == "selected_fields":
            from src.lib.flows.formatter_capability import resolved_formatter_format
            expected_format = resolved_formatter_format(output.data.agent_id, entries.get(output.id))
            for message in selection_errors(plan, source_catalogs, expected_format):
                finding(output.id, "invalid_selected_export", message)
        if not (has_profile or unprofiled):
            continue
        for ref in projection_plan_field_refs(plan):
            if ref.startswith("attributes."):
                finding(output.id, "invalid_projection_field_reference",
                        f"'{ref}' is a structure path, not an executable formatter field reference. "
                        "Read get_current_flow_projection_plan with view=source_fields and use each field's ref.")
                continue
            if not ref.startswith("object.attribute.") or ref in declared:
                continue
            if unprofiled:
                finding(output.id, "unprofiled_projection_field", f"Field '{ref}' has no declared profile contract on the unprofiled generic source; runtime values are not guaranteed.", "warning")
            elif packaged:
                # A profile cannot declare a packaged source's fields. Preserve
                # its existing runtime reference checks, without reinterpreting
                # it as exploratory/unprofiled generic output.
                continue
            else:
                finding(output.id, "undeclared_projection_field", f"Field '{ref}' is not declared by the attached saved profile revisions. It may have been removed or renamed.")
        for filter_spec in projection_plan_predicates(plan):
            if filter_spec.op not in {"gt", "gte", "lt", "lte"}:
                continue
            specs = declared.get(filter_spec.field_ref, [])
            if specs and any(spec["value_type"] not in {"integer", "number"} for spec in specs):
                # Runtime artifact keys/result IDs are not saved graph node IDs.
                # Mixed attached types can be valid when the plan selects only
                # a numeric source; the runtime checks the actual selected rows.
                selective = bool(plan.source_keys or plan.source_extraction_result_ids)
                potentially_numeric = unprofiled or packaged or any(
                    spec["value_type"] in {"integer", "number"} for spec in specs
                )
                if selective and potentially_numeric:
                    finding(output.id, "runtime_projection_source_type_check",
                            f"Numeric predicate field '{filter_spec.field_ref}' has differing or package-owned source types. "
                            "The selected sources are checked before export; profile-bound sources must declare a scalar number.", "warning")
                else:
                    finding(output.id, "incompatible_projection_field_type", f"Numeric predicate field '{filter_spec.field_ref}' is not a scalar number in the saved profile.")
    return findings
