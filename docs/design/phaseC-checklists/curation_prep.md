# Phase C semantic-coverage checklist: `curation_prep` (Wave 4 — config-only; PREP skeleton)

This is the **authoritative inventory source** for the outcome-first rewrite of the
config-only `curation_prep` base prompt (canonical agent id `curation_prep`). Every
load-bearing rule in the pre-rewrite prompt is listed here with a stable ID (PREP-NN) and
its new home, OR an explicit, justified relocation/deletion. The harness inventories
(`phase_c_inventories/curation_prep.txt`, `.invariants.txt`, `.dropped.json`) are derived
from this checklist.

## Config-only (NOT dual-tree)

`curation_prep` lives **ONLY** at `config/agents/curation_prep/prompt.yaml`; there is no
`packages/alliance/agents/curation_prep` twin. The config-divergence guard does not apply
(no pair). `assembled_prompt_text('curation_prep')` resolves the config copy directly.

## What `curation_prep` actually IS (role + output contract + skeleton choice)

`curation_prep` (canonical agent id `curation_prep`) is the **review-prep author**: it reads
upstream extraction/flow context and authors a `CurationPrepAgentOutput` that selects
persisted domain-envelope revisions for downstream review-row projection. It is the bridge
from extraction into a structured curator-review workspace. Verified against the code:

- `config/agents/curation_prep/agent.yaml` sets `output_schema: CurationPrepAgentOutput`,
  `tools: []`, `supervisor_routing.enabled: false` (flow terminal), and
  `group_rules_enabled: false`.
- `output_schema: CurationPrepAgentOutput` -> the locked core injects the **output mandate**
  for that schema: a `## Generated Runtime Contract` line ("produce JSON matching
  CurationPrepAgentOutput; the structured-output layer below is authoritative for final
  response shape") AND the full `## CRITICAL: ALWAYS PRODUCE STRUCTURED OUTPUT AS VALID JSON`
  block naming `CurationPrepAgentOutput`. So the base's own "Produce a CurationPrepAgentOutput
  compatible with the structured output schema" opener is now redundant with the core
  injection and is **dropped** (relocated -> render). The schema **token**
  (`CurationPrepAgentOutput`) and the result-object **field detail** (`envelope_refs`,
  `review_row_count`, `candidates`, `run_metadata`) are KEPT once in the base.
- `tools: []` -> NO Required tool-call policy / tool-summary injection; nothing relocates to
  a tool description.
- `group_rules_enabled: false` -> no group inventory, no `.reason_codes.txt`.

This agent **authors the envelope directly** — it is NOT a builder. The rewrite uses the lean
**PREP skeleton** (outcome-first, curator voice, positive framing): `<role>` -> `<goal>`
(success conditions folded in) -> `<preparation_rules>` (each ONCE) -> `<workflow>` (the
ordered read -> consider-together -> select-or-withhold path) -> `<output_contract>`
(`CurationPrepAgentOutput` fields, terse) -> `<stop_rules>`.

## Template rules applied (Phase C)

### Template rule — output-mandate: **INJECTED by core; base opener DROPPED, schema token KEPT once**

VERIFIED: `output_schema: CurationPrepAgentOutput`, so the core injects the
produce-structured-output mandate. The base's redundant "Produce a CurationPrepAgentOutput
compatible with the structured output schema" sentence is **dropped (relocated -> render)** —
the mandate to produce that JSON now lives in the core injection (its declared new_home is
`render`, where the injected `## CRITICAL: ALWAYS PRODUCE STRUCTURED OUTPUT AS VALID JSON`
block + `produce JSON matching CurationPrepAgentOutput` line resolve). The schema name
`CurationPrepAgentOutput` and the field-level detail are KEPT once in `<output_contract>`
because the base is the only place the prep-specific field rules (envelope_refs shape,
candidates=[], review_row_count, run_metadata placeholders) are described.

### Template rule — required-tool-call / tool-summary: **NOT injected (no tools)**

VERIFIED: `tools: []`, so the core injects no tool-policy lines. Nothing relocates to a
`bindings.yaml` tool description; the base keeps all content.

### NO group rules, NO reason codes (verified)

`agent.yaml` has `group_rules_enabled: false`, so there is **no group inventory** and no
`.reason_codes.txt`.

---

## Role / goal / success (folded into goal)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| PREP-01 | Agent identity: the Curation Prep Agent, the bridge from upstream extraction/flow context into a structured curator-review workspace. | `<role>` (curator voice) |
| PREP-02 | Goal: convert the provided CurationPrepAgentInput into a CurationPrepAgentOutput; select persisted domain envelope revisions for review-row projection. | `<goal>` |
| PREP-03 | The envelope JSON and object IDs are the semantic source of truth; prep output must only carry envelope_refs and review_row_count for new domain-envelope review flows. | `<goal>` / `<output_contract>` |
| PREP-04 | Success (folded): read the entire input payload before deciding what candidates to emit. | `<workflow>` (read-all-first) |
| PREP-05 | Success (folded): extraction_results, scope_confirmation, evidence_records, conversation_history, and adapter_metadata are considered together. | `<workflow>` (consider-together) |
| PREP-06 | Success (folded): each emitted envelope_ref names envelope_id, envelope_revision, source_extraction_result_id, domain_pack_id, and review_row_count. | `<preparation_rules>` + `<output_contract>` (envelope_ref shape) |
| PREP-07 | Success (folded): review rows are regenerated downstream from persisted envelope objects, not from prep candidates, normalized payloads, or legacy semantic lists. | `<preparation_rules>` (regenerated-downstream) |
| PREP-08 | Success (folded): incomplete or ambiguous envelopes are clearly withheld or marked for curator/deterministic follow-up in run_metadata warnings. | `<preparation_rules>` + `<stop_rules>` |

## Preparation / constraint rules (each ONCE)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| PREP-09 | Requires upstream extraction output; do not behave like a standalone extraction, PDF reading, or summarization agent. | `<preparation_rules>` (requires-upstream) |
| PREP-10 | Do not discover new evidence, identifiers, or field values on your own; only reuse what is supported by the input payload. | `<preparation_rules>` (no-discovery) |
| PREP-11 | Do not replace curator review or approval; the job is to prepare the review session, not to finalize curation. | `<role>` / `<preparation_rules>` (prepare-not-finalize) |
| PREP-12 | Only emit envelope_refs for adapter/profile combinations that are in scope. | `<preparation_rules>` (in-scope-only) |
| PREP-13 | Prefer adapter-required fields and field_hints when choosing what to include. | `<preparation_rules>` (prefer-required/hints) |
| PREP-14 | Use controlled_vocabulary and normalization_hints when they help you choose stable values. | `<preparation_rules>` (stable-values) |
| PREP-15 | Never invent evidence, identifiers, or field values that are not supported by the input. | `<preparation_rules>` (no-invention) |

## Evidence / candidate rules (envelope-vs-legacy)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| PREP-16 | Do not emit CurationPrepCandidate or NormalizedCandidate payload semantics for new domain-envelope review flows. | `<preparation_rules>` (no-legacy-candidate-payloads) |
| PREP-17 | Do not read or reconstruct legacy semantic stores such as items, annotations, genes, alleles, diseases, chemicals, phenotypes, normalized_payload, or annotation_drafts as review-row truth. | `<preparation_rules>` (no-legacy-stores-as-truth) |
| PREP-18 | Preserve provider-agnostic envelope/object/revision references so review rows can be regenerated when materialization logic changes. | `<preparation_rules>` (provider-agnostic refs) |

## Output contract (CurationPrepAgentOutput fields, terse)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| PREP-19 | For new domain-envelope review flows, set candidates to [] and use envelope_refs plus review_row_count. | `<output_contract>` (candidates=[]) |
| PREP-20 | Keep notes concise and user-visible; rely on the schema layer for field validation rather than restating the full schema in prose. | `<output_contract>` (concise notes) |
| PREP-21 | The service layer will overwrite run_metadata.model_name and run_metadata.token_usage after the structured output is validated; until then set run_metadata.model_name to "service-populated" and run_metadata.token_usage.input_tokens / output_tokens / total_tokens to 0. | `<output_contract>` (run_metadata placeholders) |
| PREP-22 | Use run_metadata.processing_notes and run_metadata.warnings for concise, user-visible notes only when needed. | `<output_contract>` (processing_notes/warnings) |

## Stop / abstain rules

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| PREP-23 | If the input lacks upstream extraction output, do not synthesize one. | `<stop_rules>` |
| PREP-24 | If the input is insufficient for a persisted envelope revision, return an empty envelope_refs list and explain the blocker in run_metadata warnings. | `<stop_rules>` |
| PREP-25 | Stop once every in-scope persisted envelope revision has been selected or explicitly withheld. | `<stop_rules>` |

---

## Dropped / relocated / deleted

| ID | Phrase | Disposition |
|----|--------|-------------|
| PREP-OUT | "Produce a CurationPrepAgentOutput compatible with the structured output schema." | **relocated -> render** — the produce-structured-output mandate is now injected by the locked core (`output_schema: CurationPrepAgentOutput` -> `## Generated Runtime Contract` "produce JSON matching CurationPrepAgentOutput" + `## CRITICAL: ALWAYS PRODUCE STRUCTURED OUTPUT AS VALID JSON` block naming `CurationPrepAgentOutput`). Restating it in the base would duplicate the locked mandate. The schema token `CurationPrepAgentOutput` and the prep-specific field rules (PREP-19..PREP-22) are KEPT once in `<output_contract>`. Recorded in `curation_prep.dropped.json` as `relocated` with `new_home: render` (synonym: the injected `produce JSON matching CurationPrepAgentOutput` line). |

## De-dup summary (the curation_prep Phase-C levers)

1. **Core de-dup (output mandate):** schema=CurationPrepAgentOutput -> core injects the
   produce-structured-output mandate; the base's redundant "Produce a CurationPrepAgentOutput
   compatible with the structured output schema" opener is dropped (relocated -> render). The
   schema token + prep-specific field rules survive once in `<output_contract>`.
2. **NO tool de-dup:** tools=[] -> no tool-policy injection; nothing relocates to a tool
   description.
3. **Consolidation:** `## Goal` / `## Success criteria` / `## Constraints` / `## Evidence and
   candidate rules` / `## Stop and abstain rules` / `## Output expectations` consolidate into
   the lean PREP skeleton (`<role>` -> `<goal>` -> `<preparation_rules>` -> `<workflow>` ->
   `<output_contract>` -> `<stop_rules>`) without losing a rule; each rule appears ONCE.
4. **NO group rules, NO reason codes:** the prompt never carried those; `bindings.yaml` and
   the tool-catalog baseline are untouched.

## Workflow invariants (ordered)

The prep agent's ordered selection path: read the entire input payload before deciding what
to emit -> consider extraction_results, scope_confirmation, evidence_records,
conversation_history, and adapter_metadata together -> select each in-scope persisted
envelope revision or explicitly withhold it. Recorded in `curation_prep.invariants.txt`.

## Contract-test coverage

**No dedicated prompt-content contract test exists for `curation_prep`.** Test references to
`curation_prep` / `CurationPrepAgentOutput` are schema tests, config/registry/catalog
fixtures, curation-workspace service/invocation tests, and the agent-documentation baseline
(whose capabilities/summary/limitations come from `docs.yaml`, NOT `prompt.yaml`). The
domain-envelope prompt-policy guard
(`test_validator_dispatch_cleanup_guardrail_rejects_stale_active_surface_terms`) scans
`config/agents/curation_prep/prompt.yaml` for FORBIDDEN legacy validator-dispatch terms; the
lean rewrite introduces none of those. No prompt-text assertion is edited, deleted, or
weakened by this rewrite. The only content guards over this base prompt are the Phase C
retention/invariant/dropped-list harness seeded by this checklist.
