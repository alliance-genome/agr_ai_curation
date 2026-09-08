export function buildFlowVerificationPrompt(
  flowName = 'my current curation flow',
  requestId?: number,
): string {
  const requestSuffix = requestId === undefined ? '' : `\n\n[Request ID: ${requestId}]`

  return `Verify ${flowName} using this targeted evidence protocol.

REQUIRED SEQUENCE:
Budget-aware inspection: use get_current_flow_topology(section="all") for all topology sections together and get_current_flow_projection_plan(node_id, view="complete_plan") for the complete saved plan. Follow returned next_call only when incomplete; do not reread the same facts field by field. Empty instruction fields need no fetch. Inspect only agents used by this flow, not unrelated catalog entries. For a direct structured export node, its agent/group/custom output prompts do not execute: explain that scoped behavior instead of auditing those inactive prompts. Extraction prompts still execute and require review. If the available evidence or remaining turn budget is insufficient, report verification INCOMPLETE with the specific outstanding checks; never infer PASS.
1. Call get_current_flow() first. Treat current_flow_manifest_v1 as authoritative. Verification MUST FAIL if has_critical_issues=true or any finding has severity CRITICAL.
2. Reconstruct exact instructions with get_current_flow_instructions(node_id, field, cursor, limit): task_instructions, every nonempty active custom_instructions, and each step_goal relevant to a judgment. Execute the returned next_call until complete=true for every required field.
3. Inspect get_current_flow_topology(section="all") covering issues, control_path, control_edges, output_bindings, and validation_sidecars. Fetch relevant scalar node details, output projection-plan fields or JSON-Pointer sections, validation-warning pages, and validation-schedule sections (selections, scheduled_validators, opt_outs, replacement_validators, supplemental_validators, inactive_metadata) only when the verification criteria require them. For every paged current-flow detail response, execute its returned next_call until complete=true and no next_call remains.
4. Only if output capability or placement remains uncertain from current-flow metadata, call get_available_agents(category="Output") and follow returned next_call until complete=true with no next_call remaining. Output agents are attachment branches with ordered source_steps, not terminal control nodes; do not require the control path to end with an Output agent.
5. Before judging an agent prompt, call get_prompt(agent_id, group_id, view="summary"), then reconstruct every required view="effective_prompt" or selected view="layer" through next_cursor until complete=true. A custom-instruction judgment requires the exact node custom_instructions and the complete relevant base/effective prompt.
6. For document/PDF capability claims, use get_tool_inventory(agent_id=<node agent>) or another focused query and follow next_cursor until truncated=false and no next_cursor remains before judging capability or reporting PASS. Then use method/PDF-level get_tool_details(tool_id, agent_id). Do not use an unsafe global inventory or oversized parent-tool metadata.
7. For domain or validator claims, call get_domain_pack_validation_plan(agent_id=<node agent> or domain_pack_id=<id>) for its summary, then fetch only evidence-relevant section pages from object_definitions, fields, validators, validator_bindings, field_policies, or validation_attachments until complete.

PASS GATE:
- Never report PASS if any required detail is incomplete, a selected text/section has another page, or any required response is compacted_tool_result.
- Duplicate output_key is HIGH unless authoritative validation classifies it CRITICAL.
- Suggestions must be supported by exact instructions or inspected metadata. Do not page through unrelated catalogs or domain metadata speculatively.

OUTPUT:
### FLOW VERIFICATION: [PASS/FAIL/INCOMPLETE]
**Critical:** [list or "None"]
**High:** [list or "None"]
**Suggestions:** [evidence-based only, or "None"]${requestSuffix}`
}
