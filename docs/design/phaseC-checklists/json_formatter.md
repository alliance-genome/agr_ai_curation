# Phase C semantic-coverage checklist: `json_formatter` (Wave 4 — FORMATTER skeleton)

This is the **authoritative inventory source** for the outcome-first rewrite of
`packages/alliance/agents/json_formatter/prompt.yaml` (canonical agent id
`json_output_formatter`). Every load-bearing rule in the pre-rewrite prompt is listed here
with a stable ID (JSON-NN) and its new home in the rewritten prompt, OR an explicit,
justified relocation/deletion. The harness inventories
(`phase_c_inventories/json_formatter.txt`, `.invariants.txt`, `.dropped.json`) are derived
from this checklist.

## What `json_formatter` actually IS (role + output contract + skeleton choice)

`json_formatter` (canonical agent id `json_output_formatter`) is an **OUTPUT FORMATTER**:
it takes caller-provided structured data and writes it to one downloadable JSON file by
calling a save tool. Verified against the code:

- `packages/alliance/agents/json_formatter/agent.yaml` sets `output_schema: null`, the
  single tool `save_json_file`, `supervisor_routing.enabled: false` (flow terminal), and
  `group_rules_enabled: false`.
- `output_schema: null` means the locked core injects **NO** Generated Runtime Contract /
  output-schema mandate (the core render is just the `## Platform Runtime Contract`
  header). The formatter does NOT author a structured JSON envelope of its own; its
  "output" is the saved file plus a brief confirmation message, and the base prompt is the
  ONLY place that contract is described, so it is KEPT.

So the rewrite uses the **FORMATTER skeleton** (outcome-first, curator voice):
`<role>` -> `<goal>` (success folded in) -> `<formatting_rules>` (the load-bearing JSON
value/structure spec + data-source rules) -> `<workflow>` (ordered steps incl. calling
`save_json_file`) -> `<output_and_handoff_contract>` (the confirmation message /
saved-file handoff) -> `<stop_rules>`.

## Template rules applied (Phase C — FORMATTER template)

### Template rule — output-mandate: **NOT injected (schema=null), base keeps its contract**

VERIFIED: `output_schema: null`, so no output-schema lines are injected for this agent.
There is NO JSON-envelope mandate to de-dup; the file/confirmation output contract lives
ONLY in the base and is KEPT. (Note: the *content* this formatter writes IS JSON, but that
is the saved artifact, not a model-authored structured-output envelope.)

### Template rule — required-tool-call: **KEPT in base (NOT injected by core)**

VERIFIED CRITICAL: `save_json_file` has **NO** `required_tool_call.enforce` metadata in
`packages/alliance/tools/bindings.yaml` (only `agr_curation_query` carries
`required_tool_call.enforce: true`). The core injects NO "Required tool-call policy" line
for this agent. So the base "call `save_json_file`" imperative is **KEPT** in the rewritten
base prompt (it is the only place it appears). NO render-relocation; `bindings.yaml` and
the tool catalog baseline are **untouched**.

### NO group rules, NO reason codes (verified)

- `agent.yaml` has `group_rules_enabled: false`, so there is **no group inventory** and no
  `.reason_codes.txt`.

---

## Role / goal / success (folded into goal)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| JSON-01 | Agent identity: the JSON File Formatter, a terminal output agent that prepares caller-provided structured data as one downloadable JSON artifact. | `<role>` (curator voice; terminal output agent) |
| JSON-02 | Goal: produce exactly one JSON output file that preserves caller-supplied values and structure, without inventing or reformatting semantic content. | `<goal>` |
| JSON-03 | Success (folded): `data_json` passed to `save_json_file` is valid JSON. | `<goal>` + `<formatting_rules>` |
| JSON-04 | Success (folded): object, array, string, numeric, boolean, and null values are preserved exactly. | `<formatting_rules>` (value-type preservation) |
| JSON-05 | Success (folded): the `filename` is a clean base filename (no extension or timestamp). | `<formatting_rules>` |
| JSON-06 | Success (folded): `save_json_file` is called once for the requested output. | `<goal>` (`save_json_file` token + "once") |
| JSON-07 | Success (folded): final response is a short ready confirmation with no inline JSON. | `<output_and_handoff_contract>` |

## Input / value-preservation / structure rules (what to format, how)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| JSON-08 | Use only data from the request and established conversation context. | `<formatting_rules>` (no-invention) |
| JSON-09 | Do not infer, summarize, omit, or reorder fields unless the user explicitly asks for a transformed structure. | `<formatting_rules>` (structure-integrity) |
| JSON-10 | Keep JSON value types unchanged and escape special characters correctly. | `<formatting_rules>` (type/escaping) |
| JSON-11 | Default to `pretty=true` unless the user asks for compact output. | `<formatting_rules>` (pretty default) |

## Tool / workflow (calling save_json_file)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| JSON-12 | `save_json_file` parameters: `data_json` (required) valid JSON string to write; `filename` (required) base filename without extension or timestamp; `pretty` (optional) `true` for pretty-print, `false` for compact output. | `<workflow>` (tool-argument spec) |
| JSON-13 | Always include the requested data and filename exactly as intended by the caller. | `<workflow>` |
| JSON-14 | Do not paste JSON into the assistant response; call `save_json_file` and return only a confirmation. | `<workflow>` (the tool-call imperative — KEPT, not core-injected) |
| JSON-15 | Call `save_json_file` only when the required data is present and parseable. | `<workflow>` (precondition) |

## Output / handoff contract (the saved file + confirmation)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| JSON-16 | On success, respond only that the JSON file is ready. | `<output_and_handoff_contract>` |
| JSON-17 | Do not include status logs, markdown artifacts, or pasted payloads. | `<output_and_handoff_contract>` |

## Stop / abstain rules

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| JSON-18 | If data is missing, empty, malformed, or the request implies fabricated records, ask for corrected input and stop. | `<stop_rules>` (folds the input-precondition stop) |
| JSON-19 | Ask for corrected input when required data is missing, empty, or malformed. | `<stop_rules>` |
| JSON-20 | Ask for corrected input when the request requires fabricated or summarized records. | `<stop_rules>` |
| JSON-21 | Do not call the tool when JSON cannot be parsed. | `<stop_rules>` |

---

## Dropped / relocated / deleted

| ID | Phrase | Disposition |
|----|--------|-------------|
| JSON-EX | The worked `# Example` block (gene-result input + the `save_json_file(...)` worked call). | **deleted** — a redundant illustration of the `save_json_file` argument spec already stated load-bearing in `<workflow>` (JSON-12). The lean skeleton states each tool-argument rule ONCE without a separate worked example; the argument shape (`data_json` valid JSON string, `filename` base name, `pretty` flag) survives in `<workflow>`. Recorded in `json_formatter.dropped.json` as `deleted` (no home). |

## De-dup summary (the json_formatter Phase-C levers)

1. **NO core de-dup:** schema=null -> no output-mandate injection; the file/confirmation
   output contract lives only in the base and is KEPT.
2. **Required-tool-call NOT de-dupped:** `save_json_file` is not core-enforced, so the base
   KEEPS the tool-call imperative.
3. **Consolidation:** `# Role`/`# Goal`/`# Success criteria`/`# Constraints`/
   `# Tool contract`/`# Evidence and validation rules`/`# Output`/`# Stop rules`
   consolidate into the lean FORMATTER skeleton without losing a rule; the worked
   `# Example` is deleted as a redundant illustration of JSON-12.
4. **NO group rules, NO reason codes:** the prompt never carried those; `bindings.yaml`
   and the tool-catalog baseline are untouched.

## Workflow invariants (ordered)

The formatter's ordered file-production path: read the caller-supplied structured data ->
build `data_json` as valid JSON (default `pretty=true`) -> call `save_json_file` once ->
confirm the file is ready. Recorded in `json_formatter.invariants.txt`.

## Contract-test coverage

**No dedicated prompt-content contract test exists for `json_formatter`.** Test references
to `json_output_formatter`/`save_json_file` are tool-name allowlists, config-loader
fixtures, catalog/registry fixtures, and the agent-documentation baseline (whose
capabilities/summary/limitations come from `docs.yaml`, NOT `prompt.yaml`). No prompt-text
assertion is edited, deleted, or weakened by this rewrite. The only guards over this base
prompt are the Phase C retention/invariant/dropped-list harness seeded by this checklist.
