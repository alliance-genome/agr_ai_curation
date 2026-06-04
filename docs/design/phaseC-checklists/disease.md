# Phase C semantic-coverage checklist: `disease` validator (Wave 3 — VALIDATOR skeleton)

This is the **authoritative inventory source** for the outcome-first rewrite of
`packages/alliance/agents/disease/prompt.yaml` (canonical agent id
`disease_validation`). Every load-bearing rule in the pre-rewrite prompt is listed
here with a stable ID (DISV-NN) and its new home in the rewritten prompt, OR an
explicit, justified relocation/deletion. The harness inventories
(`phase_c_inventories/disease.txt`, `.invariants.txt`, `.dropped.json`) are derived
from this checklist.

`disease` follows the **VALIDATOR skeleton** the `gene` pilot established and the
`allele`/`agm`/`subject_entity` rewrites reused. It is a clean template
application: the pre-rewrite prompt carried NO `# Available Methods` LIKE/
case-insensitive/`match_type` mechanics block, NO repository URL, and NO batch
protocol, so there is NO search-mechanic relocation, NO `match_type` deletion, and
NO batch inventory. (The pre-rewrite prompt's only `match`-like words were the
result-field `matched_fields` and "match context" — incidental, not a search
mechanic.)

> **LEAN skeleton (per Chris).** Each rule is stated ONCE. There is NO standalone
> `<success_criteria>` section: the few genuinely-unique success conditions
> (call-before-report, status decision, record-every-call, and the empty-input
> minimal-resolved rule) are folded into `<goal>`. The
> `<resolution_and_validation_rules>` carry the unique evidence rules once
> (no-memory/no-invention, no-direct-SQL, exact_match-vs-preserve-ambiguity,
> no-guess-on-ambiguity, domain-detail-only-in-objects/candidates),
> `<lookup_workflow>` owns the two methods + the procedural DOID/name/top-10/
> no-match/tool-error path, `<result_contract>` is a terse field LIST (the shared
> root fields collapsed to one `request_id`/`validator_binding_id`/`validator_agent`/
> `target` line, plus the disease-specific result detail and the
> `lookup_attempts[].query must always be a JSON object` rule the schema cannot
> express), and `<stop_rules>` keeps only the genuinely-new stops. No load-bearing
> rule was dropped; each ID below maps to a lean home or a justified relocation.

Legend for "New home":
- A `<section>` name is a section of the **rewritten** base prompt.
- `CORE` / `render` means the locked Generated Runtime Contract
  (`assembly.py::_build_compact_runtime_contract`) already injects this exact fact;
  the base prompt does NOT restate the core's phrasing. Recorded in
  `.dropped.json` as `relocated -> render`.
- `DELETED` means the rule's fact is dropped with no home; recorded in
  `.dropped.json` as `deleted` (printed for review).

---

## What `disease` actually IS (role + output contract + skeleton choice)

`disease` (canonical agent id `disease_validation`) is a **domain-pack VALIDATOR**,
not an extractor and not a builder. Verified against the code:

- `packages/alliance/agents/disease/agent.yaml` sets
  `output_schema: DiseaseValidationResult` (NOT `null`) and tools
  `[get_agent_contract, agr_curation_query]`, with `group_rules_enabled: false`. So
  it is an **envelope-authoring agent** that hand-authors `DiseaseValidationResult`
  directly, exactly like the gene/allele/agm validators author their envelopes —
  NOT a builder that stages into a backend materializer.
- Therefore the **builder metadata-template rule does NOT apply** here: there is no
  `stage_*`/`finalize_*` workflow, no "backend materializes metadata", and no
  `<validator_handoff>` to write (a validator IS the handoff target). The model
  fills the `DiseaseValidationResult` root fields itself.
- Its job is to **RESOLVE / VALIDATE a Disease Ontology (DOID) identity** against
  the AGR curation DB via `agr_curation_query` (the ontology helpers), and return
  the **shared validator result contract** (`DomainValidatorResultBase` root
  fields) plus disease ontology record detail.

So the rewrite uses the **role-adapted, outcome-first VALIDATOR skeleton**:

`<role>` -> `<goal>` -> `<scope>` -> `<resolution_and_validation_rules>` ->
`<lookup_workflow>` -> `<result_contract>` -> `<stop_rules>`. (No standalone
`<success_criteria>`; its conditions are folded into `<goal>`.)

### VALIDATOR framing (load-bearing, per Chris)

The validator is framed as the **stronger specialized resolver** with the Disease
Ontology lookups and a curator-editable prompt — NOT a guardrail policing a
"forbidden" extractor. The base prompt IS curator-editable; it is written in
curator voice for a biologist with no developer background ("yours to resolve well,
not hand back").

### NO group rules, NO batch protocol, NO search-mechanic relocation (verified)

- `agent.yaml` has `group_rules_enabled: false`, so there is **no group inventory**.
- The pre-rewrite prompt carried **no** per-batch protocol in the editable base
  (`supervisor_routing.batchable: true` lets the supervisor combine diseases, but
  the base never instructed a batch loop). The rewrite does NOT invent one.
- The pre-rewrite prompt carried **no** `# Available Methods` block restating LIKE/
  exact/prefix/contains search order, case-insensitivity, or a standalone
  `match_type` mechanic. So there is NO strategy-affecting search-mechanic to
  relocate and NO `match_type` deletion. `agr_curation.py`, `bindings.yaml`, and
  `tool_catalog_baseline.json` are NOT touched by this rewrite.

---

## Template rules applied (Phase C — VALIDATOR template)

### Template rule — builder metadata exclude=don't-stage: **N/A (verified)**

Does not apply: `disease_validation` authors `DiseaseValidationResult` directly. The
model EXPRESSES an unresolved outcome by writing `status: "unresolved"` +
`missing_expected_fields` + `candidates` — real top-level, model-authored channels.

### Template rule — no core duplication (de-dup lever 1: required-tool-call + output)

`assembly.py::_build_compact_runtime_contract` already injects, for
`disease_validation` (verified by rendering `build_agent_core_prompt`):

- the **required-tool-call policy**: "Required tool-call policy: call at least one
  of agr_curation_query before final output.";
- the **output contract**: "Output contract from agent.yaml: produce JSON matching
  DiseaseValidationResult; the structured-output layer below is authoritative for
  final response shape." PLUS the CRITICAL structured-output block ("Your final
  response MUST be valid JSON matching the DiseaseValidationResult schema EXACTLY");
- the **get_agent_contract** pointer.

The pre-rewrite BASE prompt restated these; the rewrite removes the restatements
(de-dup, recorded in `.dropped.json` as `relocated -> render`), but KEEPS the
curator-facing curation rule once. Specifically:

- "Call `agr_curation_query` before returning any disease term, DOID, synonym,
  definition, or ontology type." (`<success_criteria>`) -> the bare imperative
  de-dups to CORE's required-tool-call policy; the curator-facing
  call-before-report rule is folded into `<goal>` once, and the literal
  `agr_curation_query` token stays in the prompt.
- "return one root JSON object matching `DiseaseValidationResult`" /
  "Return only a `DiseaseValidationResult` JSON object" -> the JSON-only output
  mandate de-dups to CORE; the `DiseaseValidationResult` token is named once in
  `<goal>`/`<result_contract>` for curator readability, and the disease root-field
  detail is KEPT because the core does not enumerate it.

### Template rule — Shared Result Contract: **LOAD-BEARING, KEPT (de-dup lever 2)**

VERIFIED: the shared result-contract block is NOT injected by any shared prompt
layer — it is the ONLY place these fields are described for this agent. It is
therefore **load-bearing and KEPT** (wording tightened, no field dropped),
including the `lookup_attempts[].query must always be a JSON object` rule the schema
cannot express. The contract test
`test_disease_chemical_validator_result_contract.py` asserts the full
`REQUIRED_SHARED_FIELDS` set (backticked) plus the fragments below, so every field
is retained verbatim.

### Template rule — reason_codes: **none (no `.reason_codes.txt`) — confirmed**

Validators do NOT enumerate exclusion reason codes. `DiseaseValidationResult`
defines no reason-code enum bound to the validator output;
`lookup_attempts[].outcome` is an outcome enum. So none is created.

---

## Role / goal / success (success conditions folded into goal)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| DISV-01 | Agent identity: the Disease Ontology validator for Alliance Genome Resources curation; validate disease ontology targets using package-owned Alliance lookup helpers. | `<role>` (reframed to curator-voice "stronger specialized resolver"; DOID-resolution purpose retained) |
| DISV-02 | Goal: produce a shared domain-validator result for the supplied `DomainValidationRequest` with package-tool-grounded DOID decisions, candidates, lookup attempts, missing expected fields, and curator-facing explanations; return `DiseaseValidationResult`. | `<goal>` (verbatim `DomainValidationRequest` + `DiseaseValidationResult` tokens retained) |
| DISV-03 | Success: call `agr_curation_query` before returning any disease term, DOID, synonym, definition, or ontology type (the machine imperative is CORE DISV-RTC). | `<goal>` (curator-facing call-before-report line KEPT once; literal `agr_curation_query` retained) |
| DISV-04 | Success: return only `status: "resolved"` or `status: "unresolved"` for active validator results, status resolved only when requested fields are package-tool-confirmed and unambiguous. | `<goal>` + `<result_contract>` (verbatim status tokens) |
| DISV-05 | Success: if no disease input or expected result field is present, return a minimal resolved result with empty resolved data and explain that no disease lookup was requested. | `<goal>` (empty-input minimal-resolved rule retained) |
| DISV-06 | Success: put resolved scalar outputs requested by the binding in `resolved_values`; put full disease ontology records in `resolved_objects`; put alternate or ambiguous matches in `candidates`. | `<result_contract>` (DISV-18..DISV-20) |
| DISV-07 | Success: record every database call in `lookup_attempts`. | `<goal>` + `<result_contract>` (DISV-21) |

## Scope / no-transfer / supported inputs / domain limits

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| DISV-08 | Supported inputs: read disease terms from `selected_inputs`, `target.input_values`, and the requested `target.field_path` / `target.expected_fields`. | `<scope>` (the disease input-reading contract) |
| DISV-09 | Domain limits: cannot resolve gene-disease associations, phenotype detail, prevalence, or non-DOID disease statistics; direct disease-hierarchy traversal and obsolete-history analysis are not exposed by this package tool path — return unresolved for those. | `<scope>` (the package-tool scope ceiling; "return unresolved" for hierarchy/obsolete-history retained) |
| DISV-10 | No cross-agent transfer: cannot transfer work to another agent. For gene/chemical/phenotype/PDF/cross-source work, return any package-supported disease validation and state in `explanation` that the remaining work is outside this agent's tools/schema; leave next-step selection to the supervisor/caller. The daf-16 example is preserved. | `<scope>` (no-transfer discipline + the worked example retained) |

## Resolution & evidence rules (no-invention, no-SQL, ambiguity)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| DISV-11 | No-memory / no-invention: never answer from memory or invent a DOID, name, definition, synonym, or ontology type; never return term metadata without package-tool lookup evidence. | `<resolution_and_validation_rules>` (no-invention invariant; "if the database does not support it, you do not have it") |
| DISV-12 | No direct SQL: do not use or request direct SQL; this validation path must use `agr_curation_query`. (The `curation_db_sql` token must NOT appear — contract test forbids it.) | `<resolution_and_validation_rules>` (no-SQL rule retained without the forbidden token) |
| DISV-13 | `exact_match`: use `exact_match: true` when the binding or request requires exact matching; otherwise preserve ambiguity instead of guessing. | `<resolution_and_validation_rules>` (also referenced in the method param list in `<lookup_workflow>`) |
| DISV-14 | No-guess-on-ambiguity: when more than one candidate remains, do not guess; preserve the candidates and return the target unresolved. | `<resolution_and_validation_rules>` + `<stop_rules>` |
| DISV-15 | Domain detail placement: domain-specific disease details are allowed only inside `resolved_objects` or `candidates[].details`; they must not replace any shared root field. | `<resolution_and_validation_rules>` (retained verbatim) |

## Lookup workflow (two methods + bounded ordered path)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| DISV-16 | Available methods (which-method-when): `get_ontology_term` (exact CURIE lookup; required `term`, optional `ontology_term_type`); `search_ontology_terms` (typed label/synonym search; required `term`, `ontology_term_type`, optional `exact_match`, `include_synonyms`, `limit`). Use `ontology_term_type: "DOTerm"` for Disease Ontology label searches and exact DOID validation. | `<lookup_workflow>` (method catalog; verbatim `get_ontology_term`, `search_ontology_terms`, `ontology_term_type: "DOTerm"`) |
| DISV-17a | Bounded path step 1 — DOID input: call `get_ontology_term` with `term: <DOID>` and `ontology_term_type: "DOTerm"`. | `<lookup_workflow>` (ordered step 1 — invariants file pins this order) |
| DISV-17b | Step 2 — disease name input: call `search_ontology_terms` with `term: <label>`, `ontology_term_type: "DOTerm"`, `include_synonyms: true`, and a small `limit`. | `<lookup_workflow>` (ordered step 2) |
| DISV-17c | Step 3 — many matches: return the top 10 alphabetically by name and preserve them as candidates. | `<lookup_workflow>` (ordered step 3; "top 10 alphabetically by name") |
| DISV-17d | Step 4 — no tool-backed match for a requested expected field: return `status: "unresolved"` and include that field in `missing_expected_fields`. | `<lookup_workflow>` (ordered step 4) |
| DISV-17e | Step 5 — tool lookup fails after one retry: return `status: "unresolved"` with an `error` lookup attempt and a curator-safe message. | `<lookup_workflow>` (ordered step 5; "after one retry") |

## Result contract (DiseaseValidationResult — model-authored shared validator contract)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| DISV-18 | Return only `DiseaseValidationResult` with shared fields at the root. Do not wrap the object under `validation_result`, `result`, or any other property. Populate `status`, `request_id`, `validator_binding_id`, `validator_agent`, `target`, `resolved_values`, `resolved_objects`, `missing_expected_fields`, `candidates`, `lookup_attempts`, `curator_message`, `explanation`. | `<result_contract>` (verbatim backticked root-field tokens; "Do not wrap" retained as contract-test fragment) |
| DISV-19 | `resolved_values`: keys match binding expected-result fields such as `curie`, `label`, or `ontology_term_type`; omit keys that were not resolved. `resolved_objects`: package-tool-grounded disease ontology records (`curie`, `name`, and any `definition`, `ontology_type`, `synonyms`). | `<result_contract>` |
| DISV-20 | `candidates`: alternate matches for ambiguous/partial lookups — `value` (DOID or candidate label), `label` (disease name), `object_type: "DOTerm"`, `matched_fields`, `details` (definition, synonyms, ontology type, match context). | `<result_contract>` (the `DOTerm` object_type token retained) |
| DISV-21 | `lookup_attempts`: one entry per `agr_curation_query` lookup — provider `agr_curation_query`, method names such as `get_ontology_term`/`search_ontology_terms`, exact query payload, result count, outcome `success`/`not_found`/`ambiguous`/`conflict`/`error`. `lookup_attempts[].query` must always be a JSON object, never a bare string. `lookup_attempts[].outcome` carries the per-call result. | `<result_contract>` (verbatim "must always be a JSON object", `lookup_attempts[].outcome`, `lookup_attempts[].query` — contract-test fragments) |
| DISV-22 | `curator_message`/`explanation`: concise curator-facing decision summary; explanation says which inputs were searched, why resolved/unresolved, and how ambiguity or missing fields were handled. | `<result_contract>` |
| DISV-23 | Validator boundary: do not use metadata-only validator states as result statuses; keep validator responsibility separate from extraction (no patch actions / no legacy top-level summary fields — report true misses through `status: "unresolved"`, `missing_expected_fields`, `lookup_attempts[].outcome`, and `explanation`). | `<result_contract>` (no-metadata-status discipline; the legacy `results`/`query_summary`/`not_found` top-level summary fields are NEVER emitted — contract test forbids them) |

## Stop rules

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| DISV-24 | Stop once you have the database evidence you need; do not keep searching to improve phrasing. | `<stop_rules>` (VALIDATOR-template stop rule in curator voice) |
| DISV-25 | If the request is ambiguous after lookup, return the verified candidate set rather than guessing. | `<stop_rules>` (folds DISV-14 outcome) |
| DISV-26 | If data is outside this agent's scope, do not fabricate, transfer the request, or ask for another step; return only supported in-scope disease ontology validation. | `<stop_rules>` (merged with DISV-10) |

## CORE-injected (no base restatement)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| DISV-RTC | Required tool-call policy: call at least one of `agr_curation_query` before final output. | CORE (`render`). The base keeps the curator-facing call-before-report line (DISV-03) but does not restate the machine imperative. |
| DISV-OUT | Output mandate: produce JSON matching `DiseaseValidationResult`; the structured-output layer is authoritative for final response shape. | CORE (`render`). The base names the `DiseaseValidationResult` token once and keeps the root-field detail the core does not enumerate. |

---

## De-dup summary (the disease-validator Phase-C levers)

1. **CORE de-dup:** the required-tool-call imperative (the pre-rewrite
   `<success_criteria>` "Call `agr_curation_query` before returning any disease
   term…") and the JSON-output mandate ("return one root JSON object matching
   `DiseaseValidationResult`" / "Return only a `DiseaseValidationResult` JSON
   object") are relocated to the locked core (kept as one curator-facing
   call-before-report line + the `DiseaseValidationResult` token once). The disease
   root-field detail is KEPT because the core does not enumerate it.
2. **Shared Result Contract:** verified NOT injected by a shared layer; KEPT
   (load-bearing), wording tightened, no field dropped, including
   `lookup_attempts[].query must always be a JSON object`.
3. **Consolidation:** `<success_criteria>`, `<evidence_rules>`, `<constraints>`,
   `<search_strategy>`, `<output_contract>`, `<scope_boundary>`,
   `<validator_boundaries>`, and `<stopping_rules>` consolidate into the lean
   skeleton (`<goal>` folds success; `<scope>` folds inputs + domain limits +
   no-transfer; `<resolution_and_validation_rules>` folds evidence rules;
   `<lookup_workflow>` folds method catalog + the ordered path;
   `<result_contract>` folds the output contract + validator-boundary discipline;
   `<stop_rules>` keeps the genuinely-new stops) without losing a rule.
4. **NO search-mechanic relocation, NO `match_type` deletion, NO repository URL,
   NO batch, NO group rules:** the disease prompt never carried those, so none is
   added or relocated. `agr_curation.py` / `bindings.yaml` /
   `tool_catalog_baseline.json` are untouched.

## Contract-test coverage

**No test assertion is edited, deleted, or weakened by this rewrite.** Two contract
tests constrain the disease base-prompt content and both pass unchanged:

- `backend/tests/unit/lib/config/test_disease_chemical_validator_result_contract.py`
  `::test_disease_and_chemical_prompt_contracts_use_shared_fields` requires the
  `DiseaseValidationResult` token, the `agr_curation_query` token, every
  `REQUIRED_SHARED_FIELDS` member backticked, and the fragments
  `status: "resolved"`, `status: "unresolved"`, `lookup_attempts[].outcome`,
  `lookup_attempts[].query`, `must always be a JSON object`,
  `missing_expected_fields`, `candidates`, `ambiguous`, `Do not wrap` — all
  retained — and forbids `repair_action`, `no_repair_output`,
  `status: "under_development"`, `results: List`, `query_summary:`, `not_found:`,
  and (disease-only) `curation_db_sql`. The lean rewrite contains none of the
  forbidden tokens.
- `backend/tests/unit/api/test_agent_studio_domain_envelope_prompt_policy.py`
  scans `packages/*/agents/*/prompt.yaml` for legacy planned/blocked/opt-out/repair
  wording; the lean rewrite introduces none.

No re-baseline was needed (no test phrase moved out of the prompt text).
