# Phase C semantic-coverage checklist: `ontology_term` validator (Wave 3 — VALIDATOR skeleton)

This is the **authoritative inventory source** for the outcome-first rewrite of
`packages/alliance/agents/ontology_term/prompt.yaml` (canonical agent id
`ontology_term_validation`). Every load-bearing rule in the pre-rewrite prompt is
listed here with a stable ID (OTV-NN) and its new home in the rewritten prompt, OR
an explicit, justified relocation/deletion. The harness inventories
(`phase_c_inventories/ontology_term.txt`, `.invariants.txt`, `.dropped.json`) are
derived from this checklist.

`ontology_term` follows the **VALIDATOR skeleton** the `gene` pilot established and
the `allele`/`agm`/`disease`/`subject_entity`/`data_provider` rewrites reused. It is a
clean template application: the pre-rewrite prompt carried NO `# Available Methods`
LIKE/exact-then-prefix-then-contains/`match_type` search-order mechanics block, NO
repository URL, and NO `mode: "domain_validator_batch"` per-batch protocol, so there
is NO search-mechanic relocation, NO `match_type` deletion, and NO batch inventory.

> **SEARCH-MECHANIC CHECK (incidental, not real).** The pre-rewrite prompt's
> `exact`/`prefix`/`case-insensitive` words are all incidental, NOT a gene-style
> "matches by exact / then prefix / then contains, case-insensitive, across labels and
> synonyms" tool-search mental model:
> - `exact_match` is a request/lookup parameter flag, not a search-order description.
> - `accepted_prefixes` is a CURIE-prefix allowlist applied AFTER lookup, not a
>   prefix-match search order.
> - the single "case-insensitive comparison" in OTV-13 (exact-match label/synonym
>   comparison after whitespace trim) is a resolution rule, KEPT in the prompt — it is
>   not a description of how the search tool ranks candidates.
>
> So there is NO strategy-affecting search mechanic to relocate to the
> `agr_curation_query` `@function_tool` docstring + bindings.yaml summary, and
> `agr_curation.py`, `bindings.yaml`, and `tool_catalog_baseline.json` are NOT touched
> by this rewrite (mirrors the gene/allele search sentences already in the bindings
> summary; the ontology helpers add no new one).

> **LEAN skeleton (per Chris).** Each rule is stated ONCE. There is NO standalone
> `<success_criteria>` section: the few genuinely-unique success conditions
> (call-before-report, status decision, record-every-call) are folded into `<goal>`.
> `<resolution_and_validation_rules>` carry the unique evidence/structuring rules once
> (no-guess from tool evidence only, narrowest-helper + no-infer-ontology-class,
> accepted_prefixes/allowed_term_curies/unresolved_allowed_term_labels gating,
> exact_match comparison, evidence-only-after-type-selected, no-guess-on-ambiguity,
> repeated-`terms` per-item discipline). `<lookup_workflow>` owns the helper-method
> list + the bounded ordered path, `<result_contract>` is a terse field LIST (the
> shared root fields collapsed to one
> `request_id`/`validator_binding_id`/`validator_agent`/`target` line, plus the
> ontology-specific result detail, the repeated-`terms` ordered-list rule, and the
> `lookup_attempts[].query must always be a JSON object` rule the schema cannot
> express), and `<stop_rules>` keeps only the genuinely-new stops. No load-bearing rule
> was dropped; each ID below maps to a lean home or a justified relocation.

Legend for "New home":
- A `<section>` name is a section of the **rewritten** base prompt.
- `CORE` / `render` means the locked Generated Runtime Contract
  (`assembly.py::_build_compact_runtime_contract`) already injects this exact fact;
  the base prompt does NOT restate the core's phrasing. Recorded in
  `.dropped.json` as `relocated -> render`.
- `DELETED` means the rule's fact is dropped with no home; recorded in
  `.dropped.json` as `deleted` (printed for review).

---

## What `ontology_term` actually IS (role + output contract + skeleton choice)

`ontology_term` (canonical agent id `ontology_term_validation`) is a **domain-pack
VALIDATOR**, not an extractor and not a builder. Verified against the code:

- `packages/alliance/agents/ontology_term/agent.yaml` sets
  `output_schema: OntologyTermValidationResult` (NOT `null`) and tools
  `[get_agent_contract, agr_curation_query]`, with `group_rules_enabled: false`. So it
  is an **envelope-authoring agent** that hand-authors `OntologyTermValidationResult`
  directly, exactly like the gene/allele/agm/disease validators author their
  envelopes — NOT a builder that stages into a backend materializer.
- Therefore the **builder metadata-template rule does NOT apply**: there is no
  `stage_*`/`finalize_*` workflow and no `<validator_handoff>` to write (a validator IS
  the handoff target). The model fills the `OntologyTermValidationResult` root fields
  itself.
- Its job is to **RESOLVE / VALIDATE ontology-term CURIE and label fields** (any
  ontology family — DOID, ECO, GO, MP, WBPhenotype, ZP, NCBITaxon, MMO, ZECO, XCO,
  anatomy, life stage, etc.) against the AGR curation DB via `agr_curation_query`
  ontology helpers, and return the **shared validator result contract**
  (`DomainValidatorResultBase` root fields) plus ontology-specific candidate detail.

So the rewrite uses the **role-adapted, outcome-first VALIDATOR skeleton**:
`<role>` -> `<goal>` (success folded in) -> `<scope>` ->
`<resolution_and_validation_rules>` -> `<lookup_workflow>` -> `<result_contract>` ->
`<stop_rules>`.

### VALIDATOR framing (load-bearing, per Chris)

The validator is framed as the **stronger specialized resolver** with deeper DB
access and a curator-editable prompt — NOT a guardrail policing a "forbidden"
extractor. The base prompt IS curator-editable; it is written in curator voice for a
biologist with no developer background. The ontology-term validator owns the typed
lookup, the prefix/allowlist gating, the per-item resolution of repeated term lists,
and the final ontology-identity call — "yours to resolve well, not hand back".

### NO group rules, NO batch protocol, NO search-mechanic relocation (verified)

- `agent.yaml` has `group_rules_enabled: false`, so there is **no group inventory**.
- `agent.yaml`'s `supervisor_routing.batchable: true` lets the supervisor combine
  ontology requests, but the editable base never instructed a
  `mode: "domain_validator_batch"` per-batch protocol. The rewrite does NOT invent one
  (faithful migration). Note the per-item `terms` discipline (OTV-15/OTV-19) is the
  ARRAY-valued single-field protocol, NOT a cross-request batch protocol; it is KEPT.
- The pre-rewrite prompt carried **no** gene-style search-order mechanic; see the
  SEARCH-MECHANIC CHECK above. `agr_curation.py`, `bindings.yaml`, and
  `tool_catalog_baseline.json` are untouched.

---

## Template rules applied (Phase C — VALIDATOR template)

### Template rule — no core duplication (de-dup lever 1: required-tool-call + output)

`assembly.py::_build_compact_runtime_contract` already injects, for
`ontology_term_validation` (verified by rendering
`build_agent_core_prompt('ontology_term_validation')`):

- the **required-tool-call policy**: "Required tool-call policy: call at least one of
  agr_curation_query before final output.";
- the **output contract**: "Output contract from agent.yaml: produce JSON matching
  OntologyTermValidationResult; the structured-output layer below is authoritative for
  final response shape." PLUS the CRITICAL structured-output block ("Your final
  response MUST be valid JSON matching the OntologyTermValidationResult schema
  EXACTLY");
- the **get_agent_contract** pointer for detailed field/tool/schema/validator facts.

The pre-rewrite BASE prompt restated the first two (`# Tool Requirement` "You MUST
call `agr_curation_query` ...", and the `# Goal` "return structured
`OntologyTermValidationResult` data" output mandate); the rewrite removes the
restatements (de-dup, recorded in `.dropped.json` as `relocated -> render`), but KEEPS
the curator-facing curation rule once (in `<goal>`: "call it before reporting any
ontology fact") and the `OntologyTermValidationResult` token once in `<goal>` for
curator readability.

### Template rule — Shared Result Contract: **LOAD-BEARING, KEPT (de-dup lever 2)**

VERIFIED: the `# Shared Result Contract` block is NOT injected by any shared prompt
layer — it is the ONLY place these fields are described for this agent. KEPT (wording
tightened, no field dropped). Every shared root field plus the ontology-specific
`ontology_term_candidates` root is retained, plus the
`lookup_attempts[].query must always be a JSON object` rule (schema cannot express it).

### Template rule — reason_codes: **none (no `.reason_codes.txt`) — confirmed**

Validators do NOT enumerate exclusion reason codes. `OntologyTermValidationResult`
defines no reason-code enum bound to the validator output; `lookup_attempts[].outcome`
is an outcome enum, not a fixed exclusion-code enum. So none is created.

---

## Role / goal / success (folded into goal)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| OTV-01 | Agent identity: an Ontology Term Validation Specialist for Alliance Genome Resources curation; validates CURIE and label fields via package-owned Alliance ontology helpers. | `<role>` (reframed to curator-voice "stronger specialized resolver" with DB access + final say) |
| OTV-02 | Goal: validate ontology-backed fields from a `DomainValidationRequest`; return structured `OntologyTermValidationResult` using the shared validator result contract; resolve only from tool evidence. | `<goal>` (verbatim `DomainValidationRequest` + `OntologyTermValidationResult` tokens retained) |
| OTV-03 | Success (folded): call `agr_curation_query` before reporting any ontology fact (machine imperative is CORE OTV-RTC). | `<goal>` (curator-facing line KEPT once; literal token `` `agr_curation_query` `` retained) |
| OTV-04 | Success (folded): use `status: "resolved"` only when expected fields are filled from tool evidence; `status: "unresolved"` otherwise. | `<goal>` + `<result_contract>` (verbatim status tokens) |
| OTV-05 | Success (folded): record every `agr_curation_query` call in `lookup_attempts`. | `<goal>` + `<result_contract>` |
| OTV-06 | No-guessing: do not guess CURIEs, labels, prefixes, ontology types, or organism-specific term families. | `<goal>` + `<resolution_and_validation_rules>` (verbatim "Do not guess" — contract-test token) |

## Scope / no-transfer / supported inputs

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| OTV-07 | Supported request inputs read from `selected_inputs` and `target.input_values`: `curie`, `label`/`name`, `terms` (ordered repeated ontology term inputs for one array-valued field; each item a CURIE string, label string, or object with `curie`/`label`/`name`), `ontology_family`, `ontology_term_type`, `accepted_prefixes`, organism/provider context (`data_provider`, `taxon_id`, `organism`, `provider_context`), `provider_taxon_ontology_mappings`, `allowed_term_curies`, `unresolved_allowed_term_labels`, evidence context (`evidence_quote`, `source_chunk_id`, `source_section`, request `evidence[]`), `go_aspect`, `exact_match`. | `<scope>` (the ontology `selected_inputs` contract; field list retained as the handoff channel the validator reads — includes the contract-test backtick tokens `` `curie` ``, `` `label` ``, `` `terms` ``, `` `ontology_family` ``, `` `accepted_prefixes` ``, `` `allowed_term_curies` ``, `` `unresolved_allowed_term_labels` ``, `` `exact_match` ``) |
| OTV-08 | This agent only performs ontology-term validation. For non-ontology requests, do not transfer work, invoke another agent, or perform another agent's task; state the out-of-scope portion is outside this agent's tools and schema, preserve any in-scope ontology lookup, and leave next-step selection to the supervisor/caller. | `<scope>` (no cross-agent transfer — VALIDATOR-template discipline in curator voice, mirroring gene/allele/disease scope) |
| OTV-09 | Dispatcher may preflight-block phenotype provider/taxon contexts with no active DB-verified ontology mapping before this agent is invoked. | `<scope>` (kept as context: the dispatcher pre-block, so the validator understands why some phenotype contexts never reach it) |

## Resolution & validation rules

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| OTV-10 | Resolve only from tool evidence; do not guess CURIEs/labels/prefixes/ontology types/organism-specific families. (see OTV-06) | `<resolution_and_validation_rules>` |
| OTV-11 | Choose the narrowest search helper supported by the request: anatomy -> `search_anatomy_terms` with `data_provider`; life stage -> `search_life_stage_terms` with `data_provider`; Gene Ontology -> `search_go_terms` passing `go_aspect` when supplied; typed label -> `search_ontology_terms` when `ontology_term_type` is supplied. For phenotype requests without `ontology_term_type`, select `ontology_term_type` only when `data_provider` and `taxon_id` exactly match one entry in `provider_taxon_ontology_mappings`; if no structured provider/taxon mapping matches, return `status: "unresolved"` without running a label search — do not infer ontology class from prose, label text, accepted prefixes, or training-data knowledge. | `<resolution_and_validation_rules>` + `<lookup_workflow>` (narrowest-helper judgment; the no-infer-ontology-class rule is the key invariant) |
| OTV-12 | Apply `accepted_prefixes` after lookup: a result with an unaccepted prefix is not resolved; preserve it as a candidate and explain the prefix conflict. | `<resolution_and_validation_rules>` |
| OTV-13 | Apply `exact_match` to label searches: with exact matching, resolve only if the returned label or synonym exactly matches the supplied label after simple whitespace trimming and case-insensitive comparison. | `<resolution_and_validation_rules>` |
| OTV-14 | Apply `allowed_term_curies` after lookup: for repeated `terms`, every resolved item must have a CURIE in this field-scoped list; a resolvable ontology term outside the list is still invalid for the target field — return `status: "unresolved"` with item-addressed diagnostics rather than materializing it. | `<resolution_and_validation_rules>` |
| OTV-15 | Apply `unresolved_allowed_term_labels` before materialization: if an input item exactly matches one of these labels but no authoritative lookup-backed CURIE exists, return `status: "unresolved"` and explain that the schema allows the label but the validator cannot materialize an authoritative ontology term for it. | `<resolution_and_validation_rules>` |
| OTV-16 | Use `evidence_quote`, `source_chunk_id`, `source_section`, and request `evidence[]` to explain or break ties among tool-returned candidates only AFTER the structured ontology family/type is already selected. | `<resolution_and_validation_rules>` |
| OTV-17 | No-guessing on ambiguity: if multiple plausible candidates remain, return `status: "unresolved"` with `candidates` and `ontology_term_candidates`; do not choose one by preference, prefix popularity, ontology family, or training-data knowledge. | `<resolution_and_validation_rules>` + `<lookup_workflow>` (carries the "multiple plausible candidates" contract-test token) |
| OTV-18 | Repeated `terms` discipline: resolve each item independently and preserve input order; if every item supplies a CURIE, call `get_ontology_terms` once with those CURIEs; if any item supplies only a label/name, run the narrowest label helper for that item using the same structured family/type/provider/aspect rules as scalar label lookup; record an item-addressed `lookup_attempts[].query` with `item_index` per per-item label lookup, and include item indexes in curator messages for ambiguous/no-match/conflict/tool-error cases; return `status: "resolved"` for a `terms` request only when every input item resolves to exactly one authoritative term from tool evidence. | `<resolution_and_validation_rules>` (the array-valued single-field protocol) |

## Lookup workflow (helper methods + bounded ordered path)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| OTV-19 | Available ontology helper methods (which-method-when contract tokens): `get_ontology_term` (exact CURIE lookup), `get_ontology_terms` (bulk exact CURIE lookup), `search_ontology_terms` (generic typed label search), `search_anatomy_terms` (species-specific anatomy label search), `search_life_stage_terms` (species-specific developmental stage label search), `search_go_terms` (Gene Ontology label search), `map_curies_to_names` (bulk CURIE-to-name helper). | `<lookup_workflow>` (every granted method backticked — contract-test requires each granted method appear as `` `<method>` `` in content) |
| OTV-20 | Bounded path step 1: if `curie` is present, call `get_ontology_term` first with `term=curie` and `ontology_term_type` when supplied. | `<lookup_workflow>` (ordered step 1 — invariants file pins this) |
| OTV-21 | Bounded path step 2: if `terms` is present, resolve per OTV-18. | `<lookup_workflow>` (ordered step 2) |
| OTV-22 | Bounded path step 3: if only a label/name is present, choose the narrowest helper per OTV-11. | `<lookup_workflow>` (ordered step 3) |
| OTV-23 | Bounded path step 4: apply `accepted_prefixes`/`allowed_term_curies`/`unresolved_allowed_term_labels`/`exact_match` gates after lookup (per OTV-12/13/14/15). | `<lookup_workflow>` (ordered step 4) |
| OTV-24 | Bounded path step 5: if the tool returns zero results, return `status: "unresolved"` with `lookup_attempts`, `missing_expected_fields`, and a curator-facing explanation. | `<lookup_workflow>` (ordered step 5) |
| OTV-25 | Bounded path step 6: if multiple plausible candidates remain, return `status: "unresolved"` with `candidates` and `ontology_term_candidates` (per OTV-17). | `<lookup_workflow>` (ordered step 6) |

## Result contract (OntologyTermValidationResult — model-authored shared validator contract)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| OTV-26 | Return only the shared validator statuses: `status: "resolved"` when lookup evidence resolves the requested target and all expected fields this validator can derive are present; `status: "unresolved"` when the target is not found, remains ambiguous, has a prefix/type/label conflict, has missing expected fields, lacks required organism/provider context, or cannot be checked because the tool fails. Populate root fields exactly; do not wrap under another object: `status` (return `"resolved"` or `"unresolved"`; never omit), `request_id`, `validator_binding_id`, `validator_agent`, `target`, `resolved_values`, `resolved_objects`, `missing_expected_fields`, `candidates`, `lookup_attempts`, `curator_message`, `explanation`, `ontology_term_candidates`. | `<result_contract>` (verbatim status + backticked root-field tokens incl. `` `status` `` and `ontology_term_candidates`; the four request-copy fields collapsed to one line) |
| OTV-27 | `resolved_values`: scalar values keyed by expected result field (such as `curie`, `label`, `name`, `ontology_term_type`, `ontology_family`, or provider-specific keys). For repeated term bindings that expect `terms`, return `resolved_values.terms` as an ordered list with one resolved object per input item — the same length as the input `terms`, each item preserving authoritative `curie` and `name`/`label`, never dropping unresolved items or returning a shorter list. | `<result_contract>` (carries contract-test tokens `resolved_values.terms` and `same length as the input` `` `terms` ``) |
| OTV-28 | `lookup_attempts`: one record per `agr_curation_query` call (provider `agr_curation_query`, method name, query payload, `result_count`, outcome `success`/`not_found`/`ambiguous`/`conflict`/`blocked`/`error`). `lookup_attempts[].query` must always be a JSON object, never a bare string. | `<result_contract>` (carries contract-test tokens `lookup_attempts[].query` and `must always be a JSON object`) |
| OTV-29 | `ontology_term_candidates`: ontology-specific candidate details preserving `curie`, `label`, `ontology_type`, `ontology_family`, `namespace`, `accepted_prefix`, `definition`, `synonyms`, `match_type`, and lookup context when available. | `<result_contract>` |
| OTV-30 | Unresolved scalar or repeated terms: keep `resolved_values` empty or partial, list all unfilled expected keys in `missing_expected_fields`, and write a curator-facing `curator_message` saying whether the issue was no match, ambiguity, prefix/type conflict, missing organism/provider context, or a tool error. For repeated `terms`, preserve resolved and unresolved candidates with item indexes in `candidates`, `lookup_attempts`, or `ontology_term_candidates` instead of inventing missing array entries. | `<result_contract>` |
| OTV-31 | Do not use metadata-only validator states as result statuses. Envelope composition belongs outside validator results. | `<result_contract>` (verbatim — the no-metadata-status discipline) |

## Stop rules

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| OTV-32 | Stop once you have the database evidence you need; do not keep searching to improve phrasing. | `<stop_rules>` (VALIDATOR-template stop rule, parallel to gene/allele/disease) |
| OTV-33 | If the request stays ambiguous after lookup, return the verified candidate set rather than guessing. | `<stop_rules>` (folds OTV-17 ambiguity outcome) |
| OTV-34 | If data is outside this agent's scope, do not fabricate, transfer work, or call another specialist; state the scope limit and return only the supported in-scope ontology results. | `<stop_rules>` (merged with OTV-08) |

## CORE-injected (no base restatement)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| OTV-RTC | Required tool-call policy: call at least one of `agr_curation_query` before final output. | CORE (`render`). The base keeps the curator-facing "call it before reporting any ontology fact" line (OTV-03) but does not restate the machine imperative. |
| OTV-OUT | Output mandate: produce JSON matching `OntologyTermValidationResult`; the structured-output layer is authoritative for final response shape. | CORE (`render`). The base keeps the `OntologyTermValidationResult` token once but does not restate the JSON-only output mandate. |

---

## De-dup summary (the ontology_term-validator Phase-C levers)

1. **CORE de-dup:** the required-tool-call imperative (`# Tool Requirement`) and the
   JSON-output mandate (`# Goal` "return structured `OntologyTermValidationResult`
   data") are relocated to the locked core (kept as one curator-facing line + the
   `OntologyTermValidationResult` token once).
2. **Shared Result Contract:** verified NOT injected by a shared layer; KEPT
   (load-bearing), wording tightened, no field dropped.
3. **Consolidation:** `# Supported Request Inputs`, `# Available ontology helper
   methods`, `# Lookup Policy`, and `# Output Rules` consolidate into `<scope>`
   (inputs) + `<resolution_and_validation_rules>` (no-guess/narrowest-helper/gates/
   exact_match/evidence-after-type/no-guess-on-ambiguity/repeated-terms) +
   `<lookup_workflow>` (methods + ordered bounded path) + `<result_contract>`
   (shared+ontology result fields, repeated-`terms` ordered list, JSON-object rule)
   without losing a rule.
4. **NO search-mechanic relocation, NO `match_type` deletion, NO repository URL, NO
   cross-request batch, NO group rules:** the ontology prompt never carried those, so
   none is added or relocated. `agr_curation.py` / `bindings.yaml` /
   `tool_catalog_baseline.json` are untouched.

## Contract-test coverage

**No test assertion is edited, deleted, or weakened by this rewrite.**
`backend/tests/unit/lib/config/test_ontology_term_validator_contract.py`
(`test_ontology_term_prompt_and_tool_grant_agree_on_available_methods`) constrains the
ontology_term base prompt content: it requires every granted method backticked
(`get_ontology_term`, `get_ontology_terms`, `search_ontology_terms`,
`search_anatomy_terms`, `search_life_stage_terms`, `search_go_terms`,
`map_curies_to_names` — OTV-19) and the fragments `` `curie` ``, `` `label` ``,
`` `terms` ``, `` `ontology_family` ``, `` `accepted_prefixes` ``,
`` `allowed_term_curies` ``, `` `unresolved_allowed_term_labels` ``, `` `exact_match` ``,
`` `status` ``, `lookup_attempts[].query`, `must always be a JSON object`, `Do not
guess`, `status: "resolved"`, `status: "unresolved"`, `multiple plausible candidates`,
`lookup_attempts`, `missing_expected_fields`, `curator_message`,
`resolved_values.terms`, and `same length as the input` `` `terms` `` — all retained
verbatim. It forbids `repair_action` and `under_development` (neither introduced). All
assertions pass unchanged; the schema-validation tests assert against the
`OntologyTermValidationResult` model, not the prompt text, so they are unaffected. No
re-baseline was needed.
