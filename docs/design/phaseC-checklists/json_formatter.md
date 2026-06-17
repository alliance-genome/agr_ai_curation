# Phase C semantic-coverage checklist: `json_formatter`

This checklist tracks the current hotfix contract for
`packages/alliance/agents/json_formatter/prompt.yaml` (canonical agent id
`json_output_formatter`). The active Phase C harness inventories are derived
from the prompt and must stay aligned with this source-backed projection
workflow.

## Current Role

`json_formatter` is an output formatter. It creates one downloadable JSON file
from saved curation result bundles by using runtime-bound projection tools. The
model does not author JSON bytes, row arrays, or replacement payloads.

Verified current configuration:

- `agent.yaml` uses `output_schema: null`.
- `supervisor_routing.enabled: true`, but supervisor construction only exposes
  the formatter when a saved current-chat extraction-result bundle exists.
- The formatter tool list is the runtime projection suite:
  `explain_formatter_capabilities`, `inspect_output_artifacts`,
  `inspect_output_rows`, `inspect_field_values`,
  `build_default_projection_plan`, `validate_output_projection`,
  `preview_output_projection`, `finalize_and_save`, and
  `formatter_cannot_complete`.
- Removed raw file writers such as `save_json_file(data_json=...)` must not be
  selected by this agent.

## Load-Bearing Rules

| ID | Rule | Home |
|----|------|------|
| JSON-01 | The agent identity is JSON File Formatter. | `<role>` |
| JSON-02 | Produce exactly one downloadable JSON file from saved source data. | `<goal>` |
| JSON-03 | Use only rows, source refs, and field refs exposed by formatter tools. | `<formatting_rules>` |
| JSON-04 | Do not invent rows, fields, values, filenames, or evidence. | `<formatting_rules>` |
| JSON-05 | Do not build replacement JSON payloads, pasted file text, or handcrafted JSON content. | `<formatting_rules>` |
| JSON-06 | Preserve supported user-requested field selection, filtering, sorting, grouping, JSON shape, source restriction, and filename hints. | `<formatting_rules>` |
| JSON-07 | Start from `build_default_projection_plan`; when runtime context names a latest `extraction-result:<uuid>` and the curator did not ask for all results, pass that ref as `source_ref`. | `<workflow>` |
| JSON-08 | Validate the plan, preview when customized, then call `finalize_and_save` exactly once. | `<workflow>` |
| JSON-09 | Use `formatter_cannot_complete` when the saved bundle cannot support the requested JSON. | `<workflow>` / `<stop_rules>` |
| JSON-10 | After a successful save, respond only with a brief ready confirmation. | `<output_and_handoff_contract>` |

## Deleted Retired Contract

The old worked example using a raw file writer is intentionally deleted. Its
argument contract is not relocated: the hotfix replaces model-authored
`data_json` with source-backed projection plans over saved extraction results.
The corresponding deleted-rule ledger entry exists only to document why that
old example has no new home.

## Workflow Invariants

The ordered file-production path is:

1. Inspect capabilities/artifacts when needed.
2. Inspect rows/field values to choose source-backed fields, filters, grouping,
   and JSON shape.
3. Build a default projection plan, using the latest source ref by default in
   chat unless the curator explicitly asks for multiple/all results.
4. Validate and preview the plan.
5. Call `finalize_and_save` exactly once, or call
   `formatter_cannot_complete`.

The active guards live in
`backend/tests/unit/lib/prompts/phase_c_inventories/json_formatter.txt` and
`json_formatter.invariants.txt`.
