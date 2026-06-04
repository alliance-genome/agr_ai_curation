# Phase C semantic-coverage checklist: `gene_ontology` lookup (Wave 3 — LOOKUP skeleton)

This is the **authoritative inventory source** for the outcome-first rewrite of
`packages/alliance/agents/gene_ontology/prompt.yaml` (canonical agent id
`gene_ontology_lookup`). Every load-bearing rule in the pre-rewrite prompt is listed here
with a stable ID (GOT-NN) and its new home in the rewritten prompt, OR an explicit,
justified relocation/deletion. The harness inventories
(`phase_c_inventories/gene_ontology.txt`, `.invariants.txt`, `.dropped.json`) are derived
from this checklist.

## What `gene_ontology` actually IS (role + output contract + skeleton choice)

`gene_ontology` (canonical agent id `gene_ontology_lookup`) is a **LOOKUP agent that
AUTHORS a result envelope directly**, not an extractor and not a builder. Verified
against the code:

- `packages/alliance/agents/gene_ontology/agent.yaml` sets
  `output_schema: GOTermResultEnvelope` (which extends `DomainValidatorResultBase`), the
  single tool `quickgo_api_call`, and `group_rules_enabled: false`. So it hand-authors
  `GOTermResultEnvelope` directly — it does NOT stage into a backend materializer.
- Its job is to **LOOK UP and RETURN the authoritative GO term facts** (IDs, names,
  aspects, definitions, synonyms, hierarchy) from live QuickGO, and return the shared
  validator result contract (`DomainValidatorResultBase` root fields) plus the
  GO-term-specific roots (`results`, `query_summary`, `not_found`).

So the rewrite uses the **LOOKUP skeleton** (role-adapted, outcome-first, mirroring the
`agm`/`ontology_term` lean exemplars):
`<role>` -> `<goal>` (success folded in) -> `<scope>` ->
`<resolution_and_evidence_rules>` -> `<lookup_workflow>` -> `<result_contract>` ->
`<stop_rules>`.

### Positive specialist framing (load-bearing, per Chris)

The lookup is framed as the **specialist that looks up and returns the authoritative GO
term facts** with QuickGO access and the final say on a term's identity — "yours to look
up and return well, not hand back". The base prompt IS curator-editable; it is written in
curator voice for a biologist with no developer background.

### NO group rules, NO batch protocol, NO search-mechanic relocation (verified)

- `agent.yaml` has `group_rules_enabled: false`, so there is **no group inventory** and
  no `.reason_codes.txt` (lookups carry no reason-code enum).
- `agent.yaml`'s `supervisor_routing.batchable: true` lets the supervisor combine GO-term
  requests, but the editable base never instructed a per-batch protocol. The rewrite does
  NOT invent one (faithful migration).
- The pre-rewrite prompt carried **NO** gene-style "matches by exact / then prefix / then
  contains, case-insensitive, across labels and synonyms" tool-search mechanic. The
  ID-vs-text branch and "prefer non-obsolete matches" are request-routing and selection
  rules for QuickGO endpoints, NOT a tool-search-order mechanic. So there is NO
  search-mechanic to relocate; `bindings.yaml`, `rest.py`, and the tool catalog baseline
  are **untouched** by this rewrite.

---

## Template rules applied (Phase C — LOOKUP template)

### Template rule — no core duplication (de-dup lever: output-schema mandate)

`assembly.py::_build_compact_runtime_contract` already injects, for
`gene_ontology_lookup` (verified by rendering
`build_agent_core_prompt('gene_ontology_lookup')`):

- the **output contract**: "Output contract from agent.yaml: produce JSON matching
  GOTermResultEnvelope; the structured-output layer below is authoritative for final
  response shape." PLUS the CRITICAL structured-output block ("Your final response MUST
  be valid JSON matching the GOTermResultEnvelope schema EXACTLY").

The pre-rewrite BASE prompt restated this output mandate (`<goal>` "return them as a
`GOTermResultEnvelope` (no other top-level output shape)" + `<output_format>` "Your
response will be structured as a `GOTermResultEnvelope`"); the rewrite removes the
JSON-only restatement (de-dup, recorded in `.dropped.json` as `relocated -> render`), but
KEEPS the `GOTermResultEnvelope` token once in `<goal>` and the GO-term root-field detail
in `<result_contract>` (the core does NOT enumerate those fields).

### Template rule — required-tool-call: **KEPT in base (NOT injected by core)**

VERIFIED CRITICAL: `quickgo_api_call` has **NO** `required_tool_call.enforce` metadata in
`packages/alliance/tools/bindings.yaml` (only `agr_curation_query` carries
`required_tool_call.enforce: true`). The generic resolver
`required_tool_names_for_available_tools(['quickgo_api_call'])` therefore returns an EMPTY
set, and `_build_compact_runtime_contract` injects **NO** "Required tool-call policy"
line for this agent (confirmed by rendering the core: the Generated Runtime Contract
contains only the output-schema lines, no tool-call line). So the base "use
`quickgo_api_call` for every GO term request" imperative is **NOT** duplicated by the
core and is **KEPT** in the rewritten base prompt (it is the only place it appears).

### Template rule — Shared Result Contract: **LOAD-BEARING, KEPT**

VERIFIED: the shared validator root-field block is NOT injected by any shared prompt
layer — it is the ONLY place these fields are described for this agent. KEPT (wording
tightened, no field dropped). The four request-copy fields
(`request_id`/`validator_binding_id`/`validator_agent`/`target`) collapse to ONE line;
the GO-term-specific roots are retained.

---

## Role / goal / success (folded into goal)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| GOT-01 | Agent identity: the GO Term Lookup Specialist for Alliance curation workflows. | `<role>` (reframed to curator-voice positive specialist: "looks up and returns the authoritative GO term facts", with QuickGO access + final say) |
| GOT-02 | Goal: resolve GO term requests using live QuickGO data and return them as a `GOTermResultEnvelope`. | `<goal>` (verbatim `GOTermResultEnvelope` token retained once) |
| GOT-03 | Success (folded): return requested GO IDs, term names, and aspects verified from QuickGO. | `<goal>` + `<result_contract>` |
| GOT-04 | Success (folded): include definitions for direct term lookups when QuickGO returns them; include synonyms and hierarchy when requested. | `<scope>` + `<result_contract>` |
| GOT-05 | Success (folded): use the shared validator result fields exactly, with `results`, `query_summary`, and `not_found` as GO-term-specific detail fields. | `<result_contract>` |
| GOT-06 | Success (folded): keep out-of-scope tasks (gene annotations, enrichment, expression, membership) out of scope. | `<scope>` |

## Scope / no-transfer / in & out

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| GOT-07 | In scope: term lookup, IDs by name/keyword, definitions, aspects, hierarchy (`children`, `ancestors`, `descendants`), synonyms. | `<scope>` |
| GOT-08 | Out of scope: gene-to-GO annotation mapping, enrichment, expression analysis, and gene membership calls. For an out-of-scope request, do not transfer work or invoke another agent; state the scope limit and leave next-step selection to the supervisor/caller. | `<scope>` (no cross-agent transfer — LOOKUP-template discipline in curator voice; the pre-rewrite prompt did not carry an explicit no-transfer line, so this is added per the lean LOOKUP template, consistent with go_annotations/orthologs) |

## Resolution & evidence rules

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| GOT-09 | Use `quickgo_api_call` for every GO term request. | `<resolution_and_evidence_rules>` (KEPT as base imperative — NOT core-injected; see template rule above) |
| GOT-10 | Never answer from memory, inference, or prior training data. | `<resolution_and_evidence_rules>` (no-invention) |
| GOT-11 | Always use QuickGO HTTPS endpoints under `https://www.ebi.ac.uk/QuickGO/services` with read-only GET calls. | `<resolution_and_evidence_rules>` + `<lookup_workflow>` (the QuickGO base + read-only GET constraint) |
| GOT-12 | For GO IDs, require the `GO:NNNNNNN` format. | `<resolution_and_evidence_rules>` |
| GOT-13 | Prefer non-obsolete matches; report `is_obsolete` only when the query is explicitly for an obsolete term. | `<resolution_and_evidence_rules>` (obsolete handling) |

## GO reference (aspects + relationships)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| GOT-14 | Aspects: `molecular_function`, `biological_process`, `cellular_component`. | `<result_contract>` (the `aspect` enum on each GO term result — the only place the three aspect values are load-bearing) |
| GOT-15 | Relationship patterns: is_a (specialization), part_of (location/inclusion), regulates (control). | DELETED — narrative gloss of relationship-type labels with no decision rule; the model reports whatever `relationship_type` QuickGO returns on `children`/`ancestors` entries (kept in `<result_contract>`), so glossing the meaning of is_a/part_of/regulates changes no behavior. Recorded in `.dropped.json` as `deleted`. |

## Lookup workflow (QuickGO endpoints + bounded query plan)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| GOT-16 | QuickGO base URL `https://www.ebi.ac.uk/QuickGO/services`; endpoints: `/ontology/go/search?query={term}` (discovery by text), `/ontology/go/terms/{ids}` (basic term data), `/ontology/go/terms/{ids}/complete` (definitions, synonyms, relationships), `/ontology/go/terms/{ids}/children` (direct children), `/ontology/go/terms/{ids}/ancestors` (broader terms), `/ontology/go/terms/{ids}/descendants` (full lower-branch listings). | `<lookup_workflow>` (the QuickGO endpoint catalog) |
| GOT-17 | Query plan step: identify whether the request is by exact GO ID or text. | `<lookup_workflow>` (ordered step 1 — invariants file pins this) |
| GOT-18 | Query plan step (text): call `/search` once; use the first relevant non-obsolete hit; only do one follow-up search when the first result set is clearly ambiguous. | `<lookup_workflow>` (ordered step 2) |
| GOT-19 | Query plan step (details): `/terms/{ids}` for minimum required fields; `/complete` when definitions, synonyms, or relationships are needed; `/children`, `/ancestors`, or `/descendants` only when hierarchy is asked. | `<lookup_workflow>` (ordered step 3) |
| GOT-20 | Query plan step: if no exact match is found, report the nearest useful candidate in summary and place unresolved input in `not_found`. | `<lookup_workflow>` (ordered step 4) |
| GOT-21 | Query plan step: return and stop when the envelope is fully populated from collected evidence. | `<lookup_workflow>` (ordered step 5) + `<stop_rules>` |
| GOT-22 | Record each QuickGO request in `lookup_attempts`, with outcome `success`, `not_found`, `ambiguous`, or `error`. | `<lookup_workflow>` + `<result_contract>` |

## Result contract (GOTermResultEnvelope — model-authored shared validator contract)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| GOT-23 | Return only the shared validator statuses: `status: "resolved"` / `status: "unresolved"`. Populate shared root fields directly; do not wrap under `result`, `validation_result`, or another wrapper: `status`, `request_id`, `validator_binding_id`, `validator_agent`, `target`, `resolved_values`, `resolved_objects`, `missing_expected_fields`, `candidates`, `lookup_attempts`, `curator_message`, `explanation`. (Four request-copy fields collapsed to one line.) | `<result_contract>` (verbatim status + backticked root-field tokens) |
| GOT-24 | GO-term-specific fields: `results` (list of `GOTermResult` objects), `query_summary` (short summary of what was asked and found), `not_found` (input terms or IDs without a verified QuickGO result). | `<result_contract>` |
| GOT-25 | Each `GOTermResult` requires `go_id` (e.g. `GO:0003677`), `name`, `aspect` (one of `molecular_function`, `biological_process`, `cellular_component`). | `<result_contract>` |
| GOT-26 | Optional `GOTermResult` fields when QuickGO returns them: `definition`, `is_obsolete`, `children` (direct child entries with `go_id`, `name`, `relationship_type`), `ancestors` (ancestor entries with `go_id`, `name`, `relationship_type`), `synonyms`. | `<result_contract>` |
| GOT-27 | Bounded validator handling: keep lookup responsibility separate from extraction (report API-grounded GO term facts, missing terms, transient service failure, or ambiguity; do not propose free-form envelope edits). For a true missing GO term match, include the exact searched term or ID in `not_found`, add the expected output field to `missing_expected_fields`, and return `status: "unresolved"`. If QuickGO fails transiently after the bounded retry path, return `status: "unresolved"` and explain the service issue in `explanation`. | `<result_contract>` (folds the bounded-validator-lookup block) |

## Stop rules

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| GOT-28 | Stop after required evidence is collected; do not continue searching to improve phrasing. Keep retrieval bounded: 1-2 searches at most. | `<stop_rules>` |
| GOT-29 | If a term is not found, record it in `not_found` and complete the response. | `<stop_rules>` |
| GOT-30 | If QuickGO is unavailable or repeats failures, report that in `query_summary` and do not fabricate data. | `<stop_rules>` |

## CORE-injected (no base restatement)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| GOT-OUT | Output mandate: produce JSON matching `GOTermResultEnvelope`; the structured-output layer is authoritative for final response shape. | CORE (`render`). The base keeps the `GOTermResultEnvelope` token once but does not restate the JSON-only output mandate. |

> NOTE: there is NO `GOT-RTC` core-injected required-tool-call entry. Unlike the
> `agr_curation_query`-bound validators, `quickgo_api_call` has no `required_tool_call`
> metadata, so the core injects no tool-call policy line; the base prompt KEEPS its "use
> `quickgo_api_call` for every GO term request" imperative (GOT-09).

---

## De-dup summary (the gene_ontology-lookup Phase-C levers)

1. **CORE de-dup (output only):** the JSON-output mandate (`<goal>` "(no other top-level
   output shape)" + `<output_format>` "Your response will be structured as a
   `GOTermResultEnvelope`") is relocated to the locked core (kept as the
   `GOTermResultEnvelope` token once + the GO-term root-field detail).
2. **Required-tool-call NOT de-dupped:** `quickgo_api_call` is not core-enforced, so the
   base keeps the tool-call imperative.
3. **Shared Result Contract:** verified NOT injected by a shared layer; KEPT
   (load-bearing), wording tightened, no field dropped.
4. **Consolidation:** `<validator_role>`, `<success_criteria>`, `<constraints>`,
   `<scope>`, `<go_reference>`, `<api_reference>`, `<query_plan>`, `<output_format>`,
   `<bounded_validator_lookup>`, `<stop_rules>` consolidate into the lean skeleton without
   losing a rule.
5. **One DELETE (GOT-15):** the is_a/part_of/regulates relationship-pattern gloss is
   dropped — narrative with no decision rule; the model reports whatever
   `relationship_type` QuickGO returns. Recorded in `.dropped.json` as `deleted` and
   printed for review.
6. **NO search-mechanic relocation, NO group rules, NO reason codes, NO batch protocol:**
   the prompt never carried those; `bindings.yaml`/`rest.py`/tool-catalog baseline are
   untouched.

## Contract-test coverage

**No dedicated prompt-content contract test exists for `gene_ontology`.** The references
to `quickgo_api_call`/`gene_ontology` in the test suite are tool-name allowlists and
config-loader fixtures (`test_domain_envelope_repair_prompt_contract.py` lists
`quickgo_api_call` among tools FORBIDDEN to extractors — `gene_ontology` is a lookup, not
an extractor, so it is unaffected; `test_config_loaders.py` uses `gene_ontology` only as
a folder-name/example fixture). No prompt-text assertion is edited, deleted, or weakened
by this rewrite. The only guards over this base prompt are the Phase C
retention/invariant/dropped-list harness seeded by this checklist.
