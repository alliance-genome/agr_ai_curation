# Phase C semantic-coverage checklist: `chat_output` (Wave 4 — DUAL-TREE; CHAT-RESPONSE skeleton)

This is the **authoritative inventory source** for the outcome-first rewrite of the
`chat_output` base prompt (canonical agent id `chat_output_formatter`). Every load-bearing
rule in the pre-rewrite prompt is listed here with a stable ID (CHAT-NN) and its new home,
OR an explicit, justified relocation/deletion. The harness inventories
(`phase_c_inventories/chat_output.txt`, `.invariants.txt`, `.dropped.json`) are derived
from this checklist.

## DUAL-TREE — critical

`chat_output` is the **only dual-tree agent**: its base prompt exists at BOTH
`packages/alliance/agents/chat_output/prompt.yaml` AND
`config/agents/chat_output/prompt.yaml`, and the pre-rewrite copies are **byte-identical**
(5052 bytes each). The Phase C config-divergence guard
(`test_config_and_packages_prompts_are_identical`) requires the two copies to stay
byte-identical, and `chat_output` is NOT on the divergence allowlist. So the rewrite writes
the SAME new content to BOTH paths.

`assembled_prompt_text('chat_output_formatter')` resolves to the **config** copy (the
override layer wins); since both copies are identical the inventory built from the assembled
render is valid for both trees.

## What `chat_output` actually IS (role + output contract + skeleton choice)

`chat_output` (canonical agent id `chat_output_formatter`) is the **terminal CHAT-RESPONSE
formatter**: it turns the curation results an upstream agent hands it into the curator-facing
plain-text/markdown reply shown in the chat window. Verified against the code:

- Both `config/agents/chat_output/agent.yaml` and
  `packages/alliance/agents/chat_output/agent.yaml` set `output_schema: null`, `tools: []`,
  `supervisor_routing.enabled: false` (flow terminal), and `group_rules_enabled: false`.
- `output_schema: null` -> the locked core injects **NO** Generated Runtime Contract /
  output-schema mandate (the core render is just the `## Platform Runtime Contract` header).
  The chat formatter does NOT author a structured-output envelope; its "output" IS the
  markdown chat message, and the base prompt is the ONLY place that response shape is
  described, so it is KEPT.
- `tools: []` -> **NO** Required tool-call policy / tool-summary injection. There is nothing
  to relocate to a tool description; the base keeps all its content.
- `group_rules_enabled: false` -> **no group inventory**, no `.reason_codes.txt`.

So the rewrite uses the lean **CHAT-RESPONSE skeleton** (outcome-first, curator voice):
`<role>` -> `<goal>` (unique success conditions folded in) -> `<formatting_rules>` (the
response-style/content/evidence rules, each ONCE) -> `<output_contract>` (the plain-text
markdown response shape) -> `<stop_rules>`.

## Template rules applied (Phase C)

### Template rule — output-mandate: **NOT injected (schema=null), base keeps its contract**

VERIFIED: `output_schema: null`, so no output-schema lines are injected. There is NO
JSON-envelope mandate to de-dup; the markdown response contract lives ONLY in the base and is
KEPT. The chat formatter must NOT return JSON — see CHAT-18.

### Template rule — required-tool-call / tool-summary: **NOT injected (no tools)**

VERIFIED: `tools: []`, so the core injects no tool-policy lines. There is nothing to
relocate to a `bindings.yaml` tool description; the base keeps all content.

### NO group rules, NO reason codes (verified)

Both `agent.yaml` copies have `group_rules_enabled: false`, so there is **no group
inventory** and no `.reason_codes.txt`.

---

## Role / goal / success (folded into goal)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| CHAT-01 | Agent identity: the chat output formatter, a flow-terminal formatter for Alliance curation workflows. | `<role>` (curator voice; flow-terminal) |
| CHAT-02 | Goal: produce a curator-facing markdown message that makes provided curation results easy to review in chat — show what was found, what evidence/source context supports it, and what still needs attention. | `<goal>` |
| CHAT-03 | Success (folded): uses only the extracted data, curation results, and context provided by earlier agents. | `<goal>` / `<formatting_rules>` (no-invention) |
| CHAT-04 | Success (folded): preserves scientific meaning, identifiers, labels, values, and caveats exactly as provided. | `<formatting_rules>` (fidelity) |
| CHAT-05 | Success (folded): give the curator a concise summary before detail when results are available. | `<goal>` + `<output_contract>` |
| CHAT-06 | Success (folded): group related facts together and choose bullets, tables, or short paragraphs based on readability. | `<formatting_rules>` |
| CHAT-07 | Success (folded): surface validation issues, warnings, caveats, missing fields, and manual-verification needs. | `<formatting_rules>` + `<output_contract>` |
| CHAT-08 | Success (folded): keep the message chat-ready, with enough structure for fast curator scanning. | `<goal>` + `<formatting_rules>` |

## Constraints / evidence / fidelity rules (each ONCE)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| CHAT-09 | Do not add new interpretation, curation decisions, literature claims, ontology term resolutions, or identifiers. | `<formatting_rules>` (no-invention) |
| CHAT-10 | Do not fabricate or guess at missing information. | `<formatting_rules>` (no-invention) |
| CHAT-11 | For domain-pack runs, treat `domain_envelope.objects` as the semantic source of truth; use envelope/object/field/finding references exactly as provided. | `<formatting_rules>` (envelope truth) |
| CHAT-12 | Treat review rows, chat summaries, CSV/TSV/JSON payloads, and submission payloads as projections from domain-envelope objects. | `<formatting_rules>` (projections) |
| CHAT-13 | If both domain-envelope state and legacy candidate/prep fields appear, use envelope references as truth and describe legacy fields only as historical outputs or projections. | `<formatting_rules>` (envelope-vs-legacy) |
| CHAT-14 | Do not treat `items[]`, `annotations[]`, `genes[]`, `alleles[]`, `diseases[]`, `chemicals[]`, `phenotypes[]`, `CurationPrepCandidate`, `NormalizedCandidate`, `normalized_payload`, or `annotation_drafts` as semantic truth for current domain-envelope runs. | `<formatting_rules>` (legacy-not-truth) |
| CHAT-15 | Preserve `envelope_id`, `envelope_revision`, `object_id`, `pending_ref_id`, `field_path`, `finding_id`, history `event_id`, `validator_binding_id`, `projection_key`, and blocker codes exactly when supplied. | `<formatting_rules>` (identifier fidelity) |
| CHAT-16 | Summarize validation findings, lookup attempts, curator edits, flow validator replacements/skips, and export/submission blockers without inventing fixes or IDs. | `<formatting_rules>` (summarize-without-inventing) |
| CHAT-17 | Explain `lookup_attempts` as an audit trail; use the top-level lookup status, projection status, or validation finding status to describe the final outcome. | `<formatting_rules>` (audit-trail) |
| CHAT-18 | If data is incomplete, explicitly note what is missing or requires manual verification. | `<formatting_rules>` + `<stop_rules>` |
| CHAT-19 | Bold important identifiers and key terms when it improves scannability. | `<formatting_rules>` (scannability) |
| CHAT-20 | Use code formatting for IDs (e.g., `WB:WBGene00001234`). | `<formatting_rules>` (ID formatting) |
| CHAT-21 | Keep formatting useful, not decorative; avoid heavy structure when a short answer is clearer. | `<formatting_rules>` (restraint) |

## Output contract (the markdown chat message)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| CHAT-22 | Return the curator-facing markdown message only. Do not return JSON, tool calls, schema-like wrappers, or process notes. | `<output_contract>` (markdown-only; no JSON) |
| CHAT-23 | Organize around these sections when they fit the available data: Summary (brief overview of what was found); Details (structured presentation of envelope objects, projected review rows, or supplied curation data); Validation and blockers (findings, lookup audit notes, curator edits, flow validator replacements/skips, or export/submission blockers when present); Notes (warnings, caveats, missing data, or additional context). | `<output_contract>` (section scaffold) |
| CHAT-24 | Use clear markdown headings (`##` for main sections), bullet points for lists, and tables for structured data when tables make the result easier to review; keep summaries concise but comprehensive. | `<output_contract>` (markdown mechanics) |

## Stop / abstain rules

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| CHAT-25 | If no curation results or extracted data are provided, state that no curation results were provided and do not invent content. | `<stop_rules>` |
| CHAT-26 | If a field is unclear or unavailable, mark it as missing or requiring manual verification instead of filling it in. | `<stop_rules>` |
| CHAT-27 | When the curator-facing markdown message is complete, stop; do not add formatting rationale. | `<stop_rules>` |

---

## Dropped / relocated / deleted

| ID | Phrase | Disposition |
|----|--------|-------------|
| CHAT-EX | The worked `## Example Output` block (a "Gene Expression Summary" table with `daf-16`/`age-1`/`daf-2` rows plus a `### Notes` list). | **deleted** — a redundant illustration of the section scaffold (CHAT-23) and markdown mechanics (CHAT-24: `##` headings, tables, bullets, code-formatted IDs) that are stated load-bearing in `<output_contract>`. The lean skeleton states each response-shape rule ONCE without a worked specimen; no rule is lost (the table/heading/notes shape survives in CHAT-23/CHAT-24). Recorded in `chat_output.dropped.json` as `deleted` (no home). |

## De-dup summary (the chat_output Phase-C levers)

1. **NO core de-dup:** schema=null -> no output-mandate injection; tools=[] -> no
   tool-policy injection. The entire markdown response contract lives only in the base and
   is KEPT.
2. **Consolidation:** `## Goal` / `## Success Criteria` / `## Constraints and Evidence Rules`
   / `## Output Expectations` / `## Stop and Abstain Rules` consolidate into the lean
   CHAT-RESPONSE skeleton (`<role>` -> `<goal>` -> `<formatting_rules>` ->
   `<output_contract>` -> `<stop_rules>`) without losing a rule; each rule appears ONCE.
3. **Worked example deleted:** the `## Example Output` specimen is a redundant illustration
   of CHAT-23/CHAT-24 and is deleted (no home).
4. **NO group rules, NO reason codes:** the prompt never carried those; `bindings.yaml` and
   the tool-catalog baseline are untouched.
5. **DUAL-TREE:** the SAME new content is written to BOTH `config/agents/chat_output/` and
   `packages/alliance/agents/chat_output/`; the config-divergence guard stays green.

## NO workflow invariants (no ordered tool/file path)

`chat_output` has no tools and no ordered file-production path — it composes one markdown
message. There is no ordered workflow to lock, so there is **no `chat_output.invariants.txt`**
(unlike the save-tool formatters). The retention + dropped-list guards cover it.

## Contract-test coverage

**No dedicated prompt-content contract test exists for `chat_output`.** Test references to
`chat_output_formatter` are tool-event friendly-name allowlists, config/registry/flow
fixtures, runtime-label maps, and the agent-documentation baseline (whose
capabilities/summary/limitations come from `docs.yaml`, NOT `prompt.yaml`). The
domain-envelope prompt-policy guard
(`test_validator_dispatch_cleanup_guardrail_rejects_stale_active_surface_terms`) scans both
`chat_output` prompt.yaml copies for FORBIDDEN legacy validator-dispatch terms
(`planned validators`, `blocked validators`, `validator_state: planned`, `opt_out_reason`,
etc.); the lean rewrite introduces none of those. No prompt-text assertion is edited,
deleted, or weakened by this rewrite. The only content guards over this base prompt are the
Phase C retention/dropped-list/config-divergence harness seeded by this checklist.
