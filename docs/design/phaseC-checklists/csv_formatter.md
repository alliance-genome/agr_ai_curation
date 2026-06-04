# Phase C semantic-coverage checklist: `csv_formatter` (Wave 4 — FORMATTER skeleton)

This is the **authoritative inventory source** for the outcome-first rewrite of
`packages/alliance/agents/csv_formatter/prompt.yaml` (canonical agent id
`csv_output_formatter`). Every load-bearing rule in the pre-rewrite prompt is listed here
with a stable ID (CSV-NN) and its new home in the rewritten prompt, OR an explicit,
justified relocation/deletion. The harness inventories
(`phase_c_inventories/csv_formatter.txt`, `.invariants.txt`, `.dropped.json`) are derived
from this checklist.

## What `csv_formatter` actually IS (role + output contract + skeleton choice)

`csv_formatter` (canonical agent id `csv_output_formatter`) is an **OUTPUT FORMATTER**: it
takes caller-provided structured row data and writes it to one downloadable CSV file by
calling a save tool. Verified against the code:

- `packages/alliance/agents/csv_formatter/agent.yaml` sets `output_schema: null`, the
  single tool `save_csv_file`, `supervisor_routing.enabled: false` (flow terminal), and
  `group_rules_enabled: false`.
- `output_schema: null` means the locked core injects **NO** Generated Runtime Contract /
  output-schema mandate (the core render is just the `## Platform Runtime Contract`
  header). The formatter does NOT author a structured JSON envelope; its "output" is the
  saved file plus a brief confirmation message, and the base prompt is the ONLY place that
  contract is described, so it is KEPT.

So the rewrite uses the **FORMATTER skeleton** (outcome-first, curator voice):
`<role>` -> `<goal>` (success folded in) -> `<formatting_rules>` (the load-bearing CSV
spec + data-handling rules) -> `<workflow>` (ordered steps incl. calling `save_csv_file`)
-> `<output_and_handoff_contract>` (the confirmation message / saved-file handoff) ->
`<stop_rules>`.

## Template rules applied (Phase C — FORMATTER template)

### Template rule — output-mandate: **NOT injected (schema=null), base keeps its contract**

VERIFIED: `output_schema: null`, so no output-schema lines are injected for this agent.
There is NO JSON-envelope mandate to de-dup; the file/confirmation output contract lives
ONLY in the base and is KEPT.

### Template rule — required-tool-call: **KEPT in base (NOT injected by core)**

VERIFIED CRITICAL: `save_csv_file` has **NO** `required_tool_call.enforce` metadata in
`packages/alliance/tools/bindings.yaml` (only `agr_curation_query` carries
`required_tool_call.enforce: true`). The core injects NO "Required tool-call policy" line
for this agent. So the base "call `save_csv_file`" imperative is **KEPT** in the rewritten
base prompt (it is the only place it appears). NO render-relocation; `bindings.yaml` and
the tool catalog baseline are **untouched**.

### NO group rules, NO reason codes (verified)

- `agent.yaml` has `group_rules_enabled: false`, so there is **no group inventory** and no
  `.reason_codes.txt`.

---

## Role / goal / success (folded into goal)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| CSV-01 | Agent identity: the CSV File Formatter agent; transform structured row data into a properly formatted downloadable CSV file. | `<role>` (curator voice) |
| CSV-02 | Goal: create one downloadable CSV artifact from the data the user or upstream agent provided. | `<goal>` |
| CSV-03 | Success (folded): the file is created by calling `save_csv_file` exactly once when valid row data is available. | `<goal>` (`save_csv_file` token + "exactly once") |
| CSV-04 | Success (folded): the tool receives row data as a JSON array string, a filename, and optional column order. | `<goal>` + `<workflow>` |
| CSV-05 | Success (folded): the generated CSV opens cleanly in spreadsheet software with stable headers and row values. | `<goal>` (the spreadsheet-readability outcome) |
| CSV-06 | Success (folded): after the tool succeeds, the final response briefly confirms that the file is ready for download. | `<output_and_handoff_contract>` |

## CSV formatting spec (load-bearing format conventions)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| CSV-07 | First row MUST contain column headers. | `<formatting_rules>` (verbatim, including the MUST directive) |
| CSV-08 | Use comma (,) as the field delimiter. | `<formatting_rules>` |
| CSV-09 | Wrap fields containing commas, quotes, or newlines in double quotes. | `<formatting_rules>` (quoting) |
| CSV-10 | Escape double quotes within fields by doubling them (`""` for a literal `"`). | `<formatting_rules>` (escaping) |
| CSV-11 | Use consistent column ordering across all rows. | `<formatting_rules>` |
| CSV-12 | Handle missing values as empty strings (not `null` or `None`). | `<formatting_rules>` |
| CSV-13 | Preserve data types as strings in the generated CSV. | `<formatting_rules>` |

## Data handling rules (what to format)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| CSV-14 | Infer a stable column order from the incoming data when `columns` is not already clear. | `<formatting_rules>` (column-order inference) |
| CSV-15 | Include all provided rows that belong in the requested CSV. | `<formatting_rules>` |
| CSV-16 | Do not invent rows, values, columns, or filenames when the required data is missing. | `<formatting_rules>` (no-invention) |
| CSV-17 | If data is empty or invalid, report the issue instead of inventing rows or inline CSV. | `<stop_rules>` |

## Tool / workflow (calling save_csv_file)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| CSV-18 | `save_csv_file` parameters: `data_json` (required) JSON array string of row objects, do not pass raw CSV text; `filename` (required) base filename without extension or timestamp; `columns` (optional) JSON array string listing the CSV columns in the desired order. | `<workflow>` (tool-argument spec) |
| CSV-19 | The tool generates the CSV file. Do not paste CSV into the assistant response instead of calling the tool. | `<workflow>` + `<output_and_handoff_contract>` |
| CSV-20 | Always create the downloadable file by calling `save_csv_file`; do not return raw CSV as plain text. | `<workflow>` (the tool-call imperative — KEPT, not core-injected) |

## Output / handoff contract (the saved file + confirmation)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| CSV-21 | Keep the final response concise and confirmation-only after a successful tool call. | `<output_and_handoff_contract>` |

## Stop / abstain rules

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| CSV-17 (also) | If data is empty or invalid, report the issue instead of inventing rows or inline CSV. | `<stop_rules>` |

---

## Dropped / relocated / deleted

| ID | Phrase | Disposition |
|----|--------|-------------|
| CSV-EX | The worked `Example:` block (gene-result input + the `save_csv_file(...)` worked call). | **deleted** — a redundant illustration of the `save_csv_file` argument spec already stated load-bearing in `<workflow>` (CSV-18). The lean skeleton states each tool-argument rule ONCE without a separate worked example; the argument shape (`data_json` JSON array string, `filename` base name, `columns` order) survives in `<workflow>`. Recorded in `csv_formatter.dropped.json` as `deleted` (no home). |

## De-dup summary (the csv_formatter Phase-C levers)

1. **NO core de-dup:** schema=null -> no output-mandate injection; the file/confirmation
   output contract lives only in the base and is KEPT.
2. **Required-tool-call NOT de-dupped:** `save_csv_file` is not core-enforced, so the base
   KEEPS the tool-call imperative.
3. **Consolidation:** `Role:`/`Goal:`/`Success criteria:`/`Tool contract:`/
   `CSV standards:`/`Data handling:`/`Output:` consolidate into the lean FORMATTER
   skeleton without losing a rule; the worked `Example:` is deleted as a redundant
   illustration of CSV-18.
4. **NO group rules, NO reason codes:** the prompt never carried those; `bindings.yaml`
   and the tool-catalog baseline are untouched.

## Workflow invariants (ordered)

The formatter's ordered file-production path: read the row data and column order from the
request -> build the row data as a JSON array string (inferring a stable column order when
`columns` is not clear) -> call `save_csv_file` exactly once -> confirm the file is ready
for download. Recorded in `csv_formatter.invariants.txt`.

## Contract-test coverage

**No dedicated prompt-content contract test exists for `csv_formatter`.** Test references
to `csv_output_formatter`/`save_csv_file` are tool-name allowlists, config-loader
fixtures, catalog/registry fixtures, and the agent-documentation baseline (whose
capabilities/summary/limitations come from `docs.yaml`, NOT `prompt.yaml`). No prompt-text
assertion is edited, deleted, or weakened by this rewrite. The only guards over this base
prompt are the Phase C retention/invariant/dropped-list harness seeded by this checklist.
