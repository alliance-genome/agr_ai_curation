# Phase C semantic-coverage checklist: `agm` validator (Wave 3 — VALIDATOR skeleton)

This is the **authoritative inventory source** for the outcome-first rewrite of
`packages/alliance/agents/agm/prompt.yaml` (canonical agent id `agm_validation`).
Every load-bearing rule in the pre-rewrite prompt is listed here with a stable ID
(AGMV-NN) and its new home in the rewritten prompt, OR an explicit, justified
relocation/deletion. The harness inventories
(`phase_c_inventories/agm.txt`, `.invariants.txt`, `.dropped.json`) are derived
from this checklist.

`agm` follows the **VALIDATOR skeleton** the `gene` pilot established
(`docs/design/phaseC-checklists/gene.md`) and the `allele` rewrite reused
(`docs/design/phaseC-checklists/allele.md`). It is a clean template application:
the pre-rewrite prompt carried NO `# Available Methods` LIKE/case-insensitive/
`match_type` mechanics block, NO repository URL, and NO batch protocol, so there is
NO search-mechanic relocation, NO `match_type` deletion, and NO batch inventory.

> **LEAN re-tightening (Chris review).** The first draft of this rewrite applied the
> skeleton in its fuller form and roughly doubled the prompt (3750 -> 8600 chars) by
> restating the same rule across `<success_criteria>` + `<resolution_and_validation_rules>`
> + `<lookup_workflow>` + `<stop_rules>` and expanding every result field with prose the
> structured-output schema already enforces. The committed rewrite is **lean**: each
> rule is stated ONCE, the standalone `<success_criteria>` section is folded into
> `<goal>` (call-before-report, status decision, record-every-call), the
> `<resolution_and_validation_rules>` carry the four unique rules once
> (no-infer-from-unselected, no-memory/no-guess, no-guess-on-ambiguity, input
> precedence), `<lookup_workflow>` owns the procedural 7-step path, `<result_contract>`
> is a terse field LIST (name + short non-obvious note only), and `<stop_rules>` keeps
> only the two genuinely-new stops (stop-when-enough, out-of-scope) — the
> multiple-candidate / not-found / tool-error stops live in the workflow steps. No
> load-bearing rule was dropped; the IDs below still each map to a home (the "New home"
> column names the lean section). The retention inventory was re-baselined to the
> condensed wording.

Legend for "New home":
- A `<section>` name is a section of the **rewritten** base prompt.
- `CORE` / `render` means the locked Generated Runtime Contract
  (`assembly.py::_build_compact_runtime_contract`) already injects this exact fact;
  the base prompt does NOT restate the core's phrasing. Recorded in
  `.dropped.json` as `relocated -> render`.
- `DELETED` means the rule's fact is dropped with no home; recorded in
  `.dropped.json` as `deleted` (printed for review).

---

## What `agm` actually IS (role + output contract + skeleton choice)

`agm` (canonical agent id `agm_validation`) is a **domain-pack VALIDATOR**, not an
extractor and not a builder. Verified against the code:

- `packages/alliance/agents/agm/agent.yaml` sets
  `output_schema: AgmValidationResult` (NOT `null`) and tools
  `[get_agent_contract, agr_curation_query]`, with `group_rules_enabled: false`. So
  it is an **envelope-authoring agent** that hand-authors `AgmValidationResult`
  directly, exactly like the gene/allele validators author their envelopes — NOT a
  builder that stages into a backend materializer.
- Therefore the **builder metadata-template rule does NOT apply** here: there is no
  `stage_*`/`finalize_*` workflow, no "you never write the envelope / backend
  materializes metadata", no exclude=don't-stage rewrite, and no
  `<validator_handoff>` to write (a validator IS the handoff target). The model
  fills the `AgmValidationResult` root fields itself.
- Its job is to **RESOLVE / VALIDATE an affected-genomic-model identity** against the
  AGR curation DB via `agr_curation_query` (the AGM entity helpers), and return the
  **shared validator result contract** (`DomainValidatorResultBase` root fields)
  plus AGM-specific candidate detail.

So the rewrite uses the **role-adapted, outcome-first VALIDATOR skeleton**:

`<role>` -> `<goal>` -> `<success_criteria>` -> `<scope>` ->
`<resolution_and_validation_rules>` -> `<lookup_workflow>` -> `<result_contract>` ->
`<stop_rules>`. The outcome-first ORDER (Role -> Goal -> Success -> Scope -> Rules
-> Workflow -> Output -> Stop) is preserved.

### VALIDATOR framing (load-bearing, per Chris)

The validator is framed as the **stronger specialized resolver** with deeper DB
access and a curator-editable prompt — NOT a guardrail policing a "forbidden"
extractor. The base prompt IS curator-editable; it is written in curator voice for
a biologist with no developer background. (Positive, capable framing: the AGM
validator owns the entity lookup, the disambiguation, and the final AGM-identity
call — "yours to resolve well, not hand back".)

### NO group rules, NO batch protocol, NO search-mechanic relocation (verified)

- `agent.yaml` has `group_rules_enabled: false`, so there is **no group inventory**
  (no `.mgi.txt`) — unlike gene/allele, agm has no organism-group overlays.
- The pre-rewrite prompt carried **no** batch / bulk-grouping protocol (no
  `mode: "domain_validator_batch"`, no bulk method). `agent.yaml`'s
  `supervisor_routing.batchable: true` lets the supervisor combine AGM requests, but
  the editable base never instructed a per-batch protocol. The rewrite does NOT
  invent one (faithful migration, not feature addition). No batch inventory phrases.
- The pre-rewrite prompt carried **no** `# Available Methods` block restating LIKE/
  exact/prefix/contains search order, case-insensitivity, or a standalone
  `match_type` mechanic (those existed only in the gene/allele prompts). So there is
  NO strategy-affecting search-mechanic to relocate to the `agr_curation_query`
  docstring + bindings summary, and NO `match_type` deletion. `agr_curation.py`,
  `bindings.yaml`, and `tool_catalog_baseline.json` are NOT touched by this rewrite.

---

## Template rules applied (Phase C — VALIDATOR template)

### Template rule — builder metadata exclude=don't-stage: **N/A (verified)**

Does not apply: `agm_validation` authors `AgmValidationResult` directly (see above).
There is no `metadata.*` materializer and no stage tool. The model EXPRESSES an
unresolved outcome by writing `status: "unresolved"` + `missing_expected_fields` +
`candidates`/`agm_candidates` + `unresolved_explanations` — real top-level,
model-authored channels. Preserve that mechanism; do not import the builder
don't-stage rewrite.

### Template rule — no core duplication (de-dup lever 1: required-tool-call + output)

`assembly.py::_build_compact_runtime_contract` already injects, for `agm_validation`
(verified by rendering `build_agent_core_prompt('agm_validation')`):

- the **required-tool-call policy**: "Required tool-call policy: call at least one of
  agr_curation_query before final output.";
- the **output contract**: "Output contract from agent.yaml: produce JSON matching
  AgmValidationResult; the structured-output layer below is authoritative for final
  response shape." PLUS the CRITICAL structured-output block ("Your final response
  MUST be valid JSON matching the AgmValidationResult schema EXACTLY");
- the **get_agent_contract** pointer for detailed field/tool/schema/validator facts.

The pre-rewrite BASE prompt restated the first two; the rewrite removes the
restatements (de-dup, recorded in `.dropped.json` as `relocated -> render`), but
KEEPS the curator-facing curation rule once. Specifically:

- "You MUST call `agr_curation_query` before returning any AGM validation result."
  (`# Tool Requirement`) -> de-dup to CORE's required-tool-call policy. The
  curator-facing success line "Calls `agr_curation_query` before resolving any AGM
  identity" is KEPT once, and the literal token `` `agr_curation_query` `` stays in
  the prompt.
- The pre-rewrite prompt had NO standalone `AgmValidationResult` output-schema-only
  opener line (the `# Shared Result Contract` block leads straight into the statuses,
  it does not restate "produce JSON matching AgmValidationResult"). So the
  output-mandate de-dup entry is recorded against the implicit
  output-schema-restatement the core now owns; the rewrite still names the
  `AgmValidationResult` token once in `<result_contract>` for curator readability,
  while the schema enforcement lives in CORE.

### Template rule — Shared Result Contract: **LOAD-BEARING, KEPT (de-dup lever 2)**

VERIFIED: the `# Shared Result Contract` block is NOT injected by any shared prompt
layer — it is the ONLY place these fields are described for this agent. It is
therefore **load-bearing and KEPT** (wording tightened, no field dropped). Every
shared root field plus the AGM-specific `agm_candidates` and `unresolved_explanations`
roots are retained.

### Template rule — reason_codes: **none (no `.reason_codes.txt`) — confirmed**

Validators do NOT enumerate exclusion reason codes. `AgmValidationResult` defines no
reason-code enum bound to the validator output; `unresolved_explanations` is a free
list of resolution-failure reasons the model writes, not a fixed exclusion-code enum,
and `lookup_attempts[].outcome` is an outcome enum. So none is created.

---

## Role / goal / success

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| AGMV-01 | Agent identity: an Affected Genomic Model Validation Specialist for Alliance Genome Resources curation. | `<role>` (reframed to curator-voice "stronger specialized resolver"; AGM identity + DB-lookup purpose retained) |
| AGMV-02 | Goal: validate AGM subjects from a `DomainValidationRequest`; return structured `AgmValidationResult` data using the shared validator result contract; resolve only from `agr_curation_query` evidence. | `<goal>` (verbatim `DomainValidationRequest` + `AgmValidationResult` tokens retained) |
| AGMV-03 | No-inference rule: do not infer AGM identity from nearby genes, alleles, strain labels, or paper context that was not selected into the request. | `<goal>` + `<resolution_and_validation_rules>` (the AGM-specific no-inference invariant — verbatim "Do not infer AGM identity from nearby genes, alleles, strain labels, or paper context that was not selected into the request.") |
| AGMV-04 | Success: calls `agr_curation_query` before resolving any AGM identity (the machine imperative is CORE AGMV-RTC). | `<success_criteria>` (curator-facing success line KEPT once; literal token `` `agr_curation_query` `` retained) |
| AGMV-05 | Success: resolved AGMs copy expected scalar outputs into `resolved_values` using the binding's `expected_result_fields` keys, such as `subject_identifier`, `subject_label`, and `taxon`. | `<success_criteria>` + `<result_contract>` (AGMV-21) |
| AGMV-06 | Success: uses `status: "resolved"` only when expected fields are filled from database evidence; uses `status: "unresolved"` when fields are missing, ambiguous, not found, conflict with taxon, lack required input, or are blocked by a tool error. | `<success_criteria>` + `<result_contract>` (verbatim `status: "resolved"` / `status: "unresolved"` tokens) |
| AGMV-07 | Success: lists every unfilled expected key in `missing_expected_fields` and explains the failure reason in `curator_message`. | `<success_criteria>` + `<result_contract>` (AGMV-22) |
| AGMV-08 | Success: records every database call in `lookup_attempts`, including provider, method, query, result count, and outcome. | `<success_criteria>` + `<result_contract>` (AGMV-20) |

## Scope / no-transfer / supported inputs

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| AGMV-09 | Supported request inputs read from `selected_inputs` and `target.input_values` when present: `subject_identifier` (AGM CURIE or primary external ID), `subject_label` (optional AGM label or symbol), `taxon` (optional NCBITaxon CURIE). | `<scope>` (the AGM `selected_inputs` contract; field list retained as the handoff channel the validator reads) |
| AGMV-10s | This agent only performs AGM validation. For non-AGM requests, do not transfer work, invoke another agent, or perform another agent's task; state that the non-AGM portion is outside this agent's available tools/schema, preserve any in-scope AGM lookup, and leave next-step selection to the supervisor/caller. | `<scope>` (no cross-agent transfer — VALIDATOR-template discipline added in curator voice, mirroring gene/allele scope) |

## Resolution & validation rules (no-inference, no-guessing, AGM helper inputs)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| AGMV-03r | (see AGMV-03) Do not infer AGM identity from nearby genes, alleles, strain labels, or paper context that was not selected into the request. | `<resolution_and_validation_rules>` (no-inference invariant home) |
| AGMV-11 | No-guessing on ambiguity: do not choose among multiple candidates by order, popularity, model organism assumptions, or training-data knowledge. | `<resolution_and_validation_rules>` (verbatim "Do not choose by order, popularity, model organism assumptions, or training-data knowledge.") |
| AGMV-12 | AGM helper methods (which-method-when JUDGMENT): `map_entity_curies_to_info` is used first when `subject_identifier` is present, supplying `entity_type: "agm"` and `entity_curies` with the identifier; `map_entity_names_to_curies` is used when `subject_label` and taxon context are present, supplying `entity_type: "agm"`, `entity_names`, and `taxon_id`. | `<lookup_workflow>` (the AGM method-choice judgment; verbatim `map_entity_curies_to_info`, `map_entity_names_to_curies`, `entity_type: "agm"` retained — contract-test tokens) |

## Lookup workflow (bounded ordered DB path)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| AGMV-13 | Bounded lookup path step 1: if `subject_identifier` is present, call `map_entity_curies_to_info` for that identifier. | `<lookup_workflow>` (ordered step 1 — invariants file pins this order) |
| AGMV-14 | Step 2: if the identifier lookup returns one AGM whose taxon does not conflict with the supplied taxon, resolve it. | `<lookup_workflow>` (ordered step 2) |
| AGMV-15 | Step 3: if no identifier match is found and both `subject_label` and `taxon` are present, call `map_entity_names_to_curies` with `entity_type: "agm"` and `taxon_id`. | `<lookup_workflow>` (ordered step 3) |
| AGMV-16 | Step 4: treat a single label match as resolved only when the returned identifier, label, and taxon evidence do not conflict with the supplied fields. | `<lookup_workflow>` (ordered step 4) |
| AGMV-17 | Step 5: if multiple candidates remain, return `status: "unresolved"` and preserve candidates (no-guessing rule AGMV-11 applies). | `<lookup_workflow>` + `<stop_rules>` (ordered step 5) |
| AGMV-18 | Step 6: if taxon is missing for label-only lookup, return unresolved with an explanation that AGM label routing requires taxon context. | `<lookup_workflow>` (ordered step 6; carries the "missing taxon" contract-test token) |
| AGMV-19 | Step 7: if the tool reports unavailable helper behavior or an error, return unresolved with lookup attempt outcome `error`. | `<lookup_workflow>` (ordered step 7) |

## Result contract (AgmValidationResult — model-authored shared validator contract)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| AGMV-20 | Return only the shared validator statuses: `status: "resolved"` when lookup evidence resolves the requested target and all expected fields this validator can derive are present; `status: "unresolved"` when the AGM is not found, ambiguous, conflicts with taxon context, lacks required input, has missing expected fields, or cannot be checked because the tool fails. Populate these root fields exactly; do not wrap them under another object: `request_id`, `validator_binding_id`, `validator_agent`, `target`, `resolved_values`, `resolved_objects`, `missing_expected_fields`, `candidates`, `lookup_attempts`, `curator_message`, `explanation`, `agm_candidates`, `unresolved_explanations`. | `<result_contract>` (verbatim status + backticked root-field tokens, incl. `agm_candidates` [contract-test token] and `unresolved_explanations`) |
| AGMV-21 | Resolved AGMs: copy expected scalar outputs into `resolved_values` using the binding's `expected_result_fields` keys, such as `subject_identifier`, `subject_label`, and `taxon`. | `<result_contract>` |
| AGMV-22 | Unresolved AGMs: keep `resolved_values` empty or partial, list all unfilled expected keys in `missing_expected_fields`, and write a curator-facing `curator_message` that says whether the issue was no match, ambiguity, taxon conflict, missing taxon for label lookup, missing input, or a tool error. | `<result_contract>` (carries a second "missing taxon" occurrence context, but the canonical contract token lives in AGMV-18; inventory pins the step-6 phrase) |
| AGMV-23 | Do not use metadata-only validator states as result statuses. Envelope composition belongs outside validator results. | `<result_contract>` (verbatim — the no-metadata-status discipline; parallels the gene/allele "no separate object / materialization belongs to the domain pack" rule) |

## Stop rules

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| AGMV-24 | Stop once you have the database evidence you need; do not keep searching to improve phrasing. | `<stop_rules>` (VALIDATOR-template stop rule added in curator voice, parallel to gene/allele) |
| AGMV-25 | If multiple candidates remain after lookup, return `status: "unresolved"` and preserve the candidates rather than guessing. | `<stop_rules>` (folds AGMV-17; preserve-candidates is the ambiguity outcome) |
| AGMV-26 | If the AGM is not found or the lookup is blocked by a tool error, return `status: "unresolved"`, keep `resolved_values` empty for the missing fields, record the failed lookup attempt, and explain what could not be resolved. | `<stop_rules>` |
| AGMV-27 | If data is outside this agent's scope, do not fabricate, transfer work, or call another specialist; state the scope limit and return only supported in-scope AGM results. | `<stop_rules>` (merged with AGMV-10s) |

## CORE-injected (no base restatement)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| AGMV-RTC | Required tool-call policy: call at least one of `agr_curation_query` before final output. | CORE (`render`). The base keeps the curator-facing "calls `agr_curation_query` before resolving any AGM identity" success line (AGMV-04) but does not restate the machine imperative. |

---

## De-dup summary (the agm-validator Phase-C levers)

1. **CORE de-dup:** the required-tool-call imperative
   (`# Tool Requirement` "You MUST call `agr_curation_query` ...") and the
   JSON-output mandate (the implicit `AgmValidationResult` output-schema
   restatement) are relocated to the locked core (kept as one curator-facing success
   line + the `AgmValidationResult` token once). The AGM root-field detail is KEPT
   because the core does not enumerate it.
2. **Shared Result Contract:** verified NOT injected by a shared layer; KEPT
   (load-bearing), wording tightened, no field dropped.
3. **Consolidation:** `# Supported Request Inputs`, `# Available AGM helper methods`,
   and `# Lookup Policy` consolidate into `<scope>` (inputs) +
   `<resolution_and_validation_rules>` (no-inference/no-guessing/which-method-when) +
   `<lookup_workflow>` (the ordered bounded path) without losing a rule.
4. **NO search-mechanic relocation, NO `match_type` deletion, NO repository URL,
   NO batch, NO group rules:** the agm prompt never carried those, so none is
   added or relocated. `agr_curation.py` / `bindings.yaml` /
   `tool_catalog_baseline.json` are untouched.

## Contract-test coverage

**No test assertion is edited, deleted, or weakened by this rewrite.** One contract
test in `backend/tests/unit/test_subject_entity_validator_result_contract.py`
(`test_subject_entity_and_agm_prompts_pin_routing_and_output_policy`) constrains the
agm base prompt content: it requires `map_entity_curies_to_info` (AGMV-12),
`map_entity_names_to_curies` (AGMV-12), `` `agm_candidates` `` (AGMV-20), and
`missing taxon` (AGMV-18) — all retained verbatim — and forbids `repair_action`
(never introduced). All assertions pass unchanged. The schema-validation tests
assert against the `AgmValidationResult` model, not the prompt text, so they are
unaffected. No re-baseline was needed.
