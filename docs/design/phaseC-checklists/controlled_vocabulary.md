# Phase C semantic-coverage checklist: `controlled_vocabulary` validator (Wave 3 — VALIDATOR skeleton)

This is the **authoritative inventory source** for the outcome-first rewrite of
`packages/alliance/agents/controlled_vocabulary/prompt.yaml` (canonical agent id
`controlled_vocabulary_validation`). Every load-bearing rule in the pre-rewrite prompt
is listed here with a stable ID (CVV-NN) and its new home in the rewritten prompt, OR
an explicit, justified relocation/deletion. The harness inventories
(`phase_c_inventories/controlled_vocabulary.txt`, `.invariants.txt`, `.dropped.json`)
are derived from this checklist.

`controlled_vocabulary` follows the **VALIDATOR skeleton** the `gene` pilot established
and the `allele`/`agm`/`disease`/`subject_entity`/`data_provider`/`ontology_term`
rewrites reused. It is a clean template application: the pre-rewrite prompt carried NO
`# Available Methods` LIKE/exact-then-prefix-then-contains/`match_type` search-order
mechanics block, NO repository URL, and NO `mode: "domain_validator_batch"` per-batch
protocol, so there is NO search-mechanic relocation, NO `match_type` deletion, and NO
batch inventory.

> **SEARCH-MECHANIC CHECK (incidental, not real).** The pre-rewrite prompt's `exact`
> words are incidental, NOT a gene-style "matches by exact / then prefix / then
> contains, case-insensitive, across labels and synonyms" tool-search mental model:
> `exact_match` is a request/lookup parameter flag (default for validator bindings),
> and "exact controlled vocabulary lookup" / "single non-obsolete exact candidate" are
> resolution rules, NOT a description of how the search tool ranks candidates. There is
> NO `prefix`/`contains`/`case-insensitive` search-order language at all. So there is
> NO strategy-affecting search mechanic to relocate to the `agr_curation_query`
> `@function_tool` docstring + bindings.yaml summary, and `agr_curation.py`,
> `bindings.yaml`, and `tool_catalog_baseline.json` are NOT touched by this rewrite.

> **LEAN skeleton (per Chris).** Each rule is stated ONCE. There is NO standalone
> `<success_criteria>` section: the few genuinely-unique success conditions
> (call-before-report, status decision, record-every-call) are folded into `<goal>`.
> `<resolution_and_validation_rules>` carry the unique evidence rules once
> (resolve-only-from-tool-evidence + the domain invariant "do not convert vocabulary
> rows into ontology CURIEs", subset verbatim-forward + subset-membership=not_found,
> single-non-obsolete-exact=resolved, obsolete=unresolved, multiple-candidates=
> unresolved-no-guess). `<lookup_workflow>` owns the two helper methods + the bounded
> ordered path, `<result_contract>` is a terse field LIST (the shared root fields
> collapsed to one `request_id`/`validator_binding_id`/`validator_agent`/`target` line,
> plus the vocabulary-specific result detail), and `<stop_rules>` keeps only the
> genuinely-new stops. No load-bearing rule was dropped; each ID below maps to a lean
> home or a justified relocation.

Legend for "New home":
- A `<section>` name is a section of the **rewritten** base prompt.
- `CORE` / `render` means the locked Generated Runtime Contract
  (`assembly.py::_build_compact_runtime_contract`) already injects this exact fact;
  the base prompt does NOT restate the core's phrasing. Recorded in
  `.dropped.json` as `relocated -> render`.
- `DELETED` means the rule's fact is dropped with no home; recorded in
  `.dropped.json` as `deleted` (printed for review).

---

## What `controlled_vocabulary` actually IS (role + output contract + skeleton choice)

`controlled_vocabulary` (canonical agent id `controlled_vocabulary_validation`) is a
**domain-pack VALIDATOR**, not an extractor and not a builder. Verified against the
code:

- `packages/alliance/agents/controlled_vocabulary/agent.yaml` sets
  `output_schema: ControlledVocabularyValidationResult` (NOT `null`) and tools
  `[get_agent_contract, agr_curation_query]`, with `group_rules_enabled: false`. So it
  is an **envelope-authoring agent** that hand-authors
  `ControlledVocabularyValidationResult` directly, exactly like the
  gene/allele/agm/disease/ontology_term validators author their envelopes — NOT a
  builder that stages into a backend materializer.
- Therefore the **builder metadata-template rule does NOT apply**: there is no
  `stage_*`/`finalize_*` workflow and no `<validator_handoff>` to write (a validator IS
  the handoff target). The model fills the `ControlledVocabularyValidationResult` root
  fields itself.
- Its job is to **RESOLVE / VALIDATE controlled-vocabulary (VocabularyTerm) fields** —
  validating that a value is an allowed member of a named vocabulary (and an optional
  subset) — against the AGR curation DB via `agr_curation_query` controlled-vocabulary
  helpers, and return the **shared validator result contract**
  (`DomainValidatorResultBase` root fields) plus vocabulary-specific candidate detail.

So the rewrite uses the **role-adapted, outcome-first VALIDATOR skeleton**:
`<role>` -> `<goal>` (success folded in) -> `<scope>` ->
`<resolution_and_validation_rules>` -> `<lookup_workflow>` -> `<result_contract>` ->
`<stop_rules>`.

### VALIDATOR framing (load-bearing, per Chris)

The validator is framed as the **stronger specialized resolver** with deeper DB access
and a curator-editable prompt — NOT a guardrail policing a "forbidden" extractor. The
base prompt IS curator-editable; it is written in curator voice for a biologist with no
developer background. The controlled-vocabulary validator owns the vocabulary lookup,
the subset gating, the obsolete-term judgment, and the final allowed-value call —
"yours to resolve well, not hand back".

### NO group rules, NO batch protocol, NO search-mechanic relocation (verified)

- `agent.yaml` has `group_rules_enabled: false`, so there is **no group inventory**.
- `agent.yaml`'s `supervisor_routing.batchable: true` lets the supervisor combine
  vocabulary requests, but the editable base never instructed a
  `mode: "domain_validator_batch"` per-batch protocol. The rewrite does NOT invent one
  (faithful migration).
- The pre-rewrite prompt carried **no** gene-style search-order mechanic; see the
  SEARCH-MECHANIC CHECK above. `agr_curation.py`, `bindings.yaml`, and
  `tool_catalog_baseline.json` are untouched.

---

## Template rules applied (Phase C — VALIDATOR template)

### Template rule — no core duplication (de-dup lever 1: required-tool-call + output)

`assembly.py::_build_compact_runtime_contract` already injects, for
`controlled_vocabulary_validation` (verified by rendering
`build_agent_core_prompt('controlled_vocabulary_validation')`):

- the **required-tool-call policy**: "Required tool-call policy: call at least one of
  agr_curation_query before final output.";
- the **output contract**: "Output contract from agent.yaml: produce JSON matching
  ControlledVocabularyValidationResult; the structured-output layer below is
  authoritative for final response shape." PLUS the CRITICAL structured-output block
  ("Your final response MUST be valid JSON matching the
  ControlledVocabularyValidationResult schema EXACTLY");
- the **get_agent_contract** pointer for detailed field/tool/schema/validator facts.

The pre-rewrite BASE prompt restated the first two (`# Tool Requirement` "You MUST call
`agr_curation_query` ...", and the `# Goal` "return structured
`ControlledVocabularyValidationResult` data" output mandate); the rewrite removes the
restatements (de-dup, recorded in `.dropped.json` as `relocated -> render`), but KEEPS
the curator-facing curation rule once (in `<goal>`: "call it before reporting any
vocabulary fact") and the `ControlledVocabularyValidationResult` token once in `<goal>`
for curator readability.

### Template rule — Shared Result Contract: **LOAD-BEARING, KEPT (de-dup lever 2)**

VERIFIED: the `# Shared Result Contract` block is NOT injected by any shared prompt
layer — it is the ONLY place these fields are described for this agent. KEPT (wording
tightened, no field dropped). Every shared root field plus the vocabulary-specific
`controlled_vocabulary_candidates` root is retained.

### Template rule — reason_codes: **none (no `.reason_codes.txt`) — confirmed**

Validators do NOT enumerate exclusion reason codes. `ControlledVocabularyValidationResult`
defines no reason-code enum bound to the validator output; `lookup_attempts[].outcome`
is an outcome enum, not a fixed exclusion-code enum. So none is created.

---

## Role / goal / success (folded into goal)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| CVV-01 | Agent identity: a Controlled Vocabulary Validation Specialist for Alliance Genome Resources curation; validates VocabularyTerm-backed fields via package-owned Alliance controlled vocabulary helpers. | `<role>` (reframed to curator-voice "stronger specialized resolver" with DB access + final say) |
| CVV-02 | Goal: validate controlled-vocabulary fields from a `DomainValidationRequest`; return structured `ControlledVocabularyValidationResult` using the shared validator result contract; resolve only from tool evidence. | `<goal>` (verbatim `DomainValidationRequest` + `ControlledVocabularyValidationResult` tokens retained) |
| CVV-03 | Success (folded): call `agr_curation_query` before reporting any vocabulary fact (machine imperative is CORE CVV-RTC). | `<goal>` (curator-facing line KEPT once; literal token `` `agr_curation_query` `` retained) |
| CVV-04 | Success (folded): use `status: "resolved"` only when expected fields are filled from tool evidence; `status: "unresolved"` otherwise. | `<goal>` + `<result_contract>` (verbatim status tokens) |
| CVV-05 | Success (folded): record every `agr_curation_query` call in `lookup_attempts`. | `<goal>` + `<result_contract>` |
| CVV-06 | Domain invariant + no-guessing: do not convert vocabulary rows into ontology CURIEs, and do not guess internal IDs, names, abbreviations, synonyms, or obsolete state. | `<goal>` + `<resolution_and_validation_rules>` (verbatim "Do not convert vocabulary rows into ontology CURIEs" — contract-test token) |

## Scope / no-transfer / supported inputs

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| CVV-07 | Supported request inputs read from `selected_inputs` and `target.input_values`: `vocabulary` (required vocabulary name/label such as `Disease Relation`, `Condition Relation Type`, or `relation`), `subset` (optional vocabularytermset name(s)/id constraint), `term_name`, `abbreviation`, `synonym`, `include_obsolete`, `exact_match` (exact matching is the default for validator bindings). | `<scope>` (the controlled-vocabulary `selected_inputs` contract; field list retained as the handoff channel the validator reads) |
| CVV-08 | This agent only performs controlled-vocabulary validation. For non-vocabulary requests, do not transfer work, invoke another agent, or perform another agent's task; state the out-of-scope portion is outside this agent's tools and schema, preserve any in-scope vocabulary lookup, and leave next-step selection to the supervisor/caller. | `<scope>` (no cross-agent transfer — VALIDATOR-template discipline in curator voice, mirroring gene/allele/disease/ontology_term scope) |

## Resolution & validation rules

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| CVV-09 | Resolve only from tool evidence; do not convert vocabulary rows into ontology CURIEs; do not guess internal IDs/names/abbreviations/synonyms/obsolete state. (see CVV-06) | `<resolution_and_validation_rules>` |
| CVV-10 | Subset handling: when `subset` is present you MUST forward it verbatim as the `subset` argument to every `agr_curation_query` lookup call so candidates are restricted to that subset's members; when absent, do not pass `subset` and the full vocabulary is searched. A term that exists in the vocabulary but is NOT a member of the subset must be treated as unresolved (valid-but-wrong-context for this field): the subset-restricted lookup returns zero candidates, so report `not_found` with a curator-facing message that the term is not permitted for this field's subset. | `<resolution_and_validation_rules>` (the subset verbatim-forward + subset-membership=not_found invariant) |
| CVV-11 | Treat a single non-obsolete exact candidate as resolved when it satisfies the binding's expected fields. | `<resolution_and_validation_rules>` |
| CVV-12 | Treat obsolete terms as unresolved. Preserve obsolete matches in `candidates` and `controlled_vocabulary_candidates` and explain that the term exists but is obsolete. | `<resolution_and_validation_rules>` (carries the contract-test token `obsolete`) |
| CVV-13 | Treat multiple plausible candidates as unresolved. Preserve all candidates; do not choose one by order, popularity, or training-data knowledge. | `<resolution_and_validation_rules>` |

## Lookup workflow (helper methods + bounded ordered path)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| CVV-14 | Available controlled vocabulary helper methods (which-method-when contract tokens): `get_vocabulary_term` (exact controlled vocabulary lookup; required `vocabulary` plus one of `term`/`term_name`/`abbreviation`/`synonym`; optional `subset`, `include_synonyms`, `include_obsolete`, `limit`), `search_vocabulary_terms` (controlled vocabulary search; optional `vocabulary`, `subset`, `term`, `term_name`, `abbreviation`, `synonym`, `exact_match`, `include_synonyms`, `include_obsolete`, `limit`; at least one vocabulary or term query must be supplied). | `<lookup_workflow>` (both granted methods backticked — contract-test requires `get_vocabulary_term` and `search_vocabulary_terms` in content) |
| CVV-15 | Bounded path step 1: if `vocabulary` and one term query are present, call `get_vocabulary_term` first with `include_synonyms: true`, `include_obsolete: true`, and a limit large enough to detect ambiguity. | `<lookup_workflow>` (ordered step 1 — invariants file pins this) |
| CVV-16 | Bounded path step 2: if the exact lookup returns no candidates and the target allows broader search, call `search_vocabulary_terms` with `exact_match: false` and a small limit. | `<lookup_workflow>` (ordered step 2) |
| CVV-17 | Bounded path step 3: treat a single non-obsolete exact candidate as resolved (per CVV-11); treat obsolete or multiple candidates as unresolved (per CVV-12/CVV-13). | `<lookup_workflow>` (ordered step 3) |
| CVV-18 | Bounded path step 4: if the tool reports under-development or unavailable helper behavior, return unresolved with a lookup attempt outcome of `error` and a curator-facing message that vocabulary validation could not run in this runtime. | `<lookup_workflow>` (ordered step 4) |
| CVV-19 | Bounded path step 5: if the tool returns zero results, return unresolved with `lookup_attempts`, `missing_expected_fields`, and a curator-facing no-match explanation. | `<lookup_workflow>` (ordered step 5) |

## Result contract (ControlledVocabularyValidationResult — model-authored shared validator contract)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| CVV-20 | Return only the shared validator statuses: `status: "resolved"` when lookup evidence resolves the requested target and all expected fields this validator can derive are present; `status: "unresolved"` when the target is not found, remains ambiguous, is obsolete, lacks required vocabulary/query input, has missing expected fields, or cannot be checked because the tool fails. Populate root fields exactly; do not wrap under another object: `request_id`, `validator_binding_id`, `validator_agent`, `target`, `resolved_values`, `resolved_objects`, `missing_expected_fields`, `candidates`, `lookup_attempts`, `curator_message`, `explanation`, `controlled_vocabulary_candidates`. | `<result_contract>` (verbatim status + backticked root-field tokens incl. `controlled_vocabulary_candidates`; the four request-copy fields collapsed to one line; carries the contract-test token `ambiguous`) |
| CVV-21 | `resolved_values`: scalar values keyed by expected result field, such as `term_name`, `vocabulary`, `internal_id`, `abbreviation`, or `obsolete`. For resolved terms, copy expected scalar outputs into `resolved_values` using the binding's `expected_result_fields` keys (e.g. `term_name`, `vocabulary`, `internal_id`). | `<result_contract>` |
| CVV-22 | `lookup_attempts`: one record per `agr_curation_query` call (provider `agr_curation_query`, method name, query payload, `result_count`, outcome `success`/`not_found`/`ambiguous`/`conflict`/`error`). | `<result_contract>` |
| CVV-23 | `controlled_vocabulary_candidates`: vocabulary-specific candidate details preserving `internal_id`, `vocabulary`, `vocabulary_label`, `term_name`, `abbreviation`, `definition`, `obsolete`, `synonyms`, `match_type`, `matched_value`, and lookup context when available. | `<result_contract>` |
| CVV-24 | Unresolved terms: keep `resolved_values` empty or partial, list all unfilled expected keys in `missing_expected_fields`, and write a curator-facing `curator_message` saying whether the issue was no match, ambiguity, obsolete term, missing vocabulary/query input, or a tool error. | `<result_contract>` |
| CVV-25 | Do not use metadata-only validator states as result statuses. Envelope composition belongs outside validator results. | `<result_contract>` (verbatim — the no-metadata-status discipline) |

## Stop rules

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| CVV-26 | Stop once you have the database evidence you need; do not keep searching to improve phrasing. | `<stop_rules>` (VALIDATOR-template stop rule, parallel to gene/allele/disease/ontology_term) |
| CVV-27 | If the request stays ambiguous after lookup, return the verified candidate set rather than guessing. | `<stop_rules>` (folds CVV-13 ambiguity outcome) |
| CVV-28 | If data is outside this agent's scope, do not fabricate, transfer work, or call another specialist; state the scope limit and return only the supported in-scope vocabulary results. | `<stop_rules>` (merged with CVV-08) |

## CORE-injected (no base restatement)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| CVV-RTC | Required tool-call policy: call at least one of `agr_curation_query` before final output. | CORE (`render`). The base keeps the curator-facing "call it before reporting any vocabulary fact" line (CVV-03) but does not restate the machine imperative. |
| CVV-OUT | Output mandate: produce JSON matching `ControlledVocabularyValidationResult`; the structured-output layer is authoritative for final response shape. | CORE (`render`). The base keeps the `ControlledVocabularyValidationResult` token once but does not restate the JSON-only output mandate. |

---

## De-dup summary (the controlled_vocabulary-validator Phase-C levers)

1. **CORE de-dup:** the required-tool-call imperative (`# Tool Requirement`) and the
   JSON-output mandate (`# Goal` "return structured
   `ControlledVocabularyValidationResult` data") are relocated to the locked core
   (kept as one curator-facing line + the `ControlledVocabularyValidationResult` token
   once).
2. **Shared Result Contract:** verified NOT injected by a shared layer; KEPT
   (load-bearing), wording tightened, no field dropped.
3. **Consolidation:** `# Supported Request Inputs`, `# Available controlled vocabulary
   helper methods`, `# Lookup Policy`, and `# Output Rules` consolidate into `<scope>`
   (inputs) + `<resolution_and_validation_rules>` (no-CURIE-conversion/no-guess/subset/
   single-exact/obsolete/multiple) + `<lookup_workflow>` (methods + ordered bounded
   path) + `<result_contract>` (shared+vocabulary result fields) without losing a rule.
4. **NO search-mechanic relocation, NO `match_type` deletion, NO repository URL, NO
   cross-request batch, NO group rules:** the vocabulary prompt never carried those, so
   none is added or relocated. `agr_curation.py` / `bindings.yaml` /
   `tool_catalog_baseline.json` are untouched.

## Contract-test coverage

**No test assertion is edited, deleted, or weakened by this rewrite.**
`backend/tests/unit/lib/config/test_controlled_vocabulary_validation_agent.py`
(`test_controlled_vocabulary_prompt_declares_lookup_policy`) constrains the
controlled_vocabulary base prompt content: it requires `get_vocabulary_term`,
`search_vocabulary_terms`, `obsolete`, `ambiguous`, and `Do not convert vocabulary rows
into ontology CURIEs` — all retained verbatim. The schema-validation tests assert
against the `ControlledVocabularyValidationResult` model, not the prompt text, so they
are unaffected. No re-baseline was needed.
