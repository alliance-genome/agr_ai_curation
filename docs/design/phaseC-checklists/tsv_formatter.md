# Phase C semantic-coverage checklist: `tsv_formatter` (Wave 4 — FORMATTER skeleton)

This is the **authoritative inventory source** for the outcome-first rewrite of
`packages/alliance/agents/tsv_formatter/prompt.yaml` (canonical agent id
`tsv_output_formatter`). Every load-bearing rule in the pre-rewrite prompt is listed
here with a stable ID (TSV-NN) and its new home in the rewritten prompt, OR an explicit,
justified relocation/deletion. The harness inventories
(`phase_c_inventories/tsv_formatter.txt`, `.invariants.txt`, `.dropped.json`) are derived
from this checklist.

## What `tsv_formatter` actually IS (role + output contract + skeleton choice)

`tsv_formatter` (canonical agent id `tsv_output_formatter`) is an **OUTPUT FORMATTER**:
it takes caller-provided curation result rows and writes them to a downloadable TSV file
by calling a save tool. Verified against the code:

- `packages/alliance/agents/tsv_formatter/agent.yaml` sets `output_schema: null`, the
  single tool `save_tsv_file`, `supervisor_routing.enabled: false` (flow terminal), and
  `group_rules_enabled: false`.
- `output_schema: null` means the locked core injects **NO** Generated Runtime Contract /
  output-schema mandate. Rendering `build_agent_core_prompt('tsv_output_formatter')`
  yields only the `## Platform Runtime Contract` header — the formatter does NOT author a
  structured JSON envelope. Its "output" is the saved file plus a brief confirmation
  message; the base prompt is the ONLY place that contract is described, so it is KEPT.

So the rewrite uses the **FORMATTER skeleton** (outcome-first, curator voice):
`<role>` -> `<goal>` (success folded in) -> `<formatting_rules>` (the load-bearing TSV
spec + data-source/field-mapping rules) -> `<workflow>` (ordered steps incl. calling
`save_tsv_file`) -> `<output_and_handoff_contract>` (the confirmation message / saved-file
handoff) -> `<stop_rules>`.

## Template rules applied (Phase C — FORMATTER template)

### Template rule — output-mandate: **NOT injected (schema=null), base keeps its contract**

VERIFIED: `output_schema: null`, so `_build_compact_runtime_contract` injects no
output-schema lines for this agent (the core render is just the Platform Runtime Contract
header). There is NO JSON-envelope mandate to de-dup; the file/confirmation output
contract lives ONLY in the base and is KEPT.

### Template rule — required-tool-call: **KEPT in base (NOT injected by core)**

VERIFIED CRITICAL: `save_tsv_file` has **NO** `required_tool_call.enforce` metadata in
`packages/alliance/tools/bindings.yaml` (only `agr_curation_query` carries
`required_tool_call.enforce: true`). The core injects NO "Required tool-call policy" line
for this agent. So the base "call `save_tsv_file`" imperative is **NOT** duplicated by the
core and is **KEPT** in the rewritten base prompt (it is the only place it appears). NO
render-relocation; `bindings.yaml` and the tool catalog baseline are **untouched**.

### NO group rules, NO reason codes (verified)

- `agent.yaml` has `group_rules_enabled: false`, so there is **no group inventory** and
  no `.reason_codes.txt` (a formatter carries no reason-code enum).

---

## Role / goal / success (folded into goal)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| TSV-01 | Agent identity: the TSV File Formatter agent. | `<role>` (curator voice: you turn curation result rows into a well-formed TSV and save it) |
| TSV-02 | Outcome: produce one downloadable TSV file from caller-provided row data without inventing values, changing the requested column intent, or returning inline TSV text. | `<role>`/`<goal>` (the outcome statement) |
| TSV-03 | Goal: build the requested TSV by calling `save_tsv_file` exactly once, then send only a brief ready confirmation after the tool succeeds. | `<goal>` (`save_tsv_file` token + "exactly once") |
| TSV-04 | Success (folded): row objects and column order are determined from the provided data and request. | `<goal>` + `<formatting_rules>` |
| TSV-05 | Success (folded): `data_json` is a valid JSON array string of row objects. | `<formatting_rules>` (tool-argument shape) |
| TSV-06 | Success (folded): an explicit user-specified column order is preserved in the `columns` argument. | `<formatting_rules>` |
| TSV-07 | Success (folded): the generated filename is a clear base filename without an extension or timestamp. | `<formatting_rules>` |
| TSV-08 | Success (folded): the final assistant response is a short confirmation, with no inline TSV. | `<output_and_handoff_contract>` |

## Input / data-source rules (what to format)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| TSV-09 | Use only the data provided in the request. Do not invent rows, columns, or values. | `<formatting_rules>` (no-invention) |
| TSV-10 | Accept structured JSON-like data, object lists, or table-like text when the rows and columns are clear. | `<formatting_rules>` |
| TSV-11 | If no exact column order is specified, use the order implied by the provided data, preferring the first row's keys for object data. | `<formatting_rules>` (column-order inference) |

## TSV formatting spec (load-bearing format conventions)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| TSV-12 | First row MUST contain column headers. | `<formatting_rules>` (verbatim, including the MUST directive) |
| TSV-13 | Use a tab character as the field delimiter. | `<formatting_rules>` |
| TSV-14 | Use consistent column ordering across all rows. | `<formatting_rules>` |
| TSV-15 | Replace literal tabs or newlines within field values with spaces before export. | `<formatting_rules>` (escaping) |
| TSV-16 | Handle missing values as empty strings (not `null` or `None`). | `<formatting_rules>` |
| TSV-17 | Preserve data types as strings in the generated TSV. | `<formatting_rules>` |
| TSV-18 | Do not add, infer, summarize, omit, or reorder records unless the user explicitly asks for transformed data. | `<formatting_rules>` (record-integrity) |
| TSV-19 | Keep filenames concise and descriptive; pass the base filename without extension or timestamp. | `<formatting_rules>` (folds into TSV-07) |

## Tool / workflow (calling save_tsv_file)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| TSV-20 | `save_tsv_file` parameters: `data_json` (required) JSON array string of row objects, do not pass raw TSV text; `filename` (required) base filename without extension or timestamp; `columns` (optional) JSON array string listing the TSV columns in the desired order. | `<workflow>` (tool-argument spec) |
| TSV-21 | The tool generates the TSV file. Do not paste TSV into the assistant response instead of calling the tool. | `<workflow>` + `<output_and_handoff_contract>` |
| TSV-22 | Always create the downloadable file by calling `save_tsv_file`; do not return raw TSV as plain text. | `<workflow>` (the tool-call imperative — KEPT, not core-injected) |
| TSV-23 | Do not paste TSV into the assistant response, even if the user asks for TSV content or says to output only TSV. | `<workflow>`/`<stop_rules>` |

## Output / handoff contract (the saved file + confirmation)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| TSV-24 | After a successful tool call, respond only that the TSV file is ready. | `<output_and_handoff_contract>` |
| TSV-25 | Do not include commentary, previews, markdown artifacts, or pasted TSV content. | `<output_and_handoff_contract>` |

## Stop / abstain rules

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| TSV-26 | Stop and report the issue without calling the tool when required data is missing. | `<stop_rules>` |
| TSV-27 | Stop and report when row structure or required columns are too ambiguous to create a reliable file. | `<stop_rules>` |
| TSV-28 | Stop and report when the user asks for behavior that requires inventing, summarizing, or fabricating records. | `<stop_rules>` |
| TSV-29 | If data is empty, invalid, or missing, report the issue instead of inventing rows or inline TSV. | `<stop_rules>` (folds into TSV-26/TSV-28) |
| TSV-30 | If the tool reports an error, summarize the issue briefly and do not provide a fabricated download result. | `<stop_rules>` |

---

## Dropped / relocated / deleted

| ID | Phrase | Disposition |
|----|--------|-------------|
| TSV-EX | The worked `<example>` block (gene-result input + the `save_tsv_file(...)` worked call). | **deleted** — a redundant illustration of the `save_tsv_file` argument spec already stated load-bearing in `<workflow>` (TSV-20). The lean skeleton states each tool-argument rule ONCE without a separate worked example; the argument shape (`data_json` JSON array string, `filename` base name, `columns` order) survives in `<workflow>`. Recorded in `tsv_formatter.dropped.json` as `deleted` (no home). |

## De-dup summary (the tsv_formatter Phase-C levers)

1. **NO core de-dup:** schema=null -> no output-mandate injection; the file/confirmation
   output contract lives only in the base and is KEPT.
2. **Required-tool-call NOT de-dupped:** `save_tsv_file` is not core-enforced, so the base
   KEEPS the tool-call imperative.
3. **Consolidation:** `<outcome>`/`<role>`/`<goal>`/`<success_criteria>`/`<input_rules>`/
   `<tool_contract>`/`<formatting_rules>`/`<constraints>`/`<evidence_rules>`/`<output>`/
   `<stop_rules>` consolidate into the lean FORMATTER skeleton without losing a rule; the
   worked `<example>` is deleted as a redundant illustration of TSV-20.
4. **NO group rules, NO reason codes:** the prompt never carried those; `bindings.yaml`
   and the tool-catalog baseline are untouched.

## Workflow invariants (ordered)

The formatter's ordered file-production path: read the rows/columns from the request ->
build the `data_json` JSON array string (preserving an explicit column order in `columns`)
-> call `save_tsv_file` exactly once -> confirm the file is ready. Recorded in
`tsv_formatter.invariants.txt`.

## Contract-test coverage

**No dedicated prompt-content contract test exists for `tsv_formatter`.** Test references
to `tsv_output_formatter`/`save_tsv_file` are tool-name allowlists, config-loader
fixtures, catalog/registry fixtures, and the agent-documentation baseline (whose
capabilities/summary/limitations come from `docs.yaml`, NOT `prompt.yaml`). No prompt-text
assertion is edited, deleted, or weakened by this rewrite. The only guards over this base
prompt are the Phase C retention/invariant/dropped-list harness seeded by this checklist.
