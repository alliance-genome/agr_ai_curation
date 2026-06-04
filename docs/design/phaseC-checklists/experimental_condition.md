# Phase C semantic-coverage checklist: `experimental_condition` validator (Wave 3 — COMPOSITE VALIDATOR skeleton)

This is the **authoritative inventory source** for the outcome-first rewrite of
`packages/alliance/agents/experimental_condition/prompt.yaml` (canonical agent id
`experimental_condition_validation`). Every load-bearing rule in the pre-rewrite prompt
is listed here with a stable ID (ECV-NN) and its new home in the rewritten prompt, OR an
explicit, justified relocation/deletion. The harness inventories
(`phase_c_inventories/experimental_condition.txt`, `.invariants.txt`, `.dropped.json`)
are derived from this checklist.

`experimental_condition` follows the **VALIDATOR skeleton** the `gene` pilot established
and the `allele`/`agm`/`disease`/`subject_entity`/`data_provider`/`ontology_term`/
`controlled_vocabulary` rewrites reused — adapted for a **composite** validator. Unlike
the single-component validators, it composes lower-level component evidence (condition
ontology class/id, condition chemical as a ChEBI ontology term, condition taxon,
quantity/unit controlled vocabulary, data-provider context) into one condition-level
decision, so it carries TWO extra root-field groups the single validators do not:
`component_validations[]` (per-component decision records) and the condition-level
status/identifier fields (`condition_status`, `condition_id`, `normalized_components`,
`unresolved_components`). Those are load-bearing and KEPT.

> **SEARCH-MECHANIC CHECK (incidental, not real).** The pre-rewrite prompt's `prefix`
> words are incidental, NOT a gene-style "matches by exact / then prefix / then contains,
> case-insensitive, across labels and synonyms" tool-search mental model: "prefix-mismatched"
> and "has a prefix/type/provider/taxon mismatch" are candidate-classification / conflict
> rules (a component whose returned CURIE prefix conflicts with the expected type is a
> conflict, preserved as a candidate), NOT a description of how the search tool ranks
> candidates. There is NO `LIKE`, NO `contains`, NO `case-insensitive`, NO `match_type`
> search-order language at all. So there is NO strategy-affecting search mechanic to
> relocate to the `agr_curation_query` `@function_tool` docstring + bindings.yaml summary,
> and `agr_curation.py`, `bindings.yaml`, and `tool_catalog_baseline.json` are NOT touched
> by this rewrite.

> **LEAN skeleton (per Chris).** Each rule is stated ONCE. There is NO standalone
> `<success_criteria>` section: the few genuinely-unique success conditions
> (call-the-package-tool-before-resolving-any-component, condition-level status decision)
> are folded into `<goal>`. `<scope>` owns the supported request inputs (the composite's
> `selected_inputs`/`target.input_values` contract) and no-cross-agent-transfer.
> `<resolution_and_validation_rules>` carries the unique grounding/coherence rules once
> (per-component grounding from lookup not memory; component ownership map naming the
> lower-level capabilities + their `agr_curation_query` methods; ChEBI-as-ontology-term
> grounding via `get_ontology_term` ontologytermtype `CHEBITerm`, NOT `chebi_api_call`;
> relation type as coherence context only; the per-component status policy; ambiguous-or-
> missing-required-component keeps the condition unresolved). `<lookup_workflow>` owns the
> bounded ordered composite path. `<result_contract>` is a terse field LIST (the shared
> root fields collapsed to one `request_id`/`validator_binding_id`/`validator_agent`/
> `target` line, plus the composite-specific `component_validations`,
> `normalized_components`, `condition_status`, `condition_id`, `unresolved_components`
> result detail). `<stop_rules>` keeps only the genuinely-new stops. No load-bearing rule
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

## What `experimental_condition` actually IS (role + output contract + skeleton choice)

`experimental_condition` (canonical agent id `experimental_condition_validation`) is a
**domain-pack COMPOSITE VALIDATOR**, not an extractor and not a builder. Verified against
the code:

- `packages/alliance/agents/experimental_condition/agent.yaml` sets
  `output_schema: ExperimentalConditionValidationResult` (NOT `null`) and tools
  `[get_agent_contract, agr_curation_query]`, with `group_rules_enabled: false`. So it is
  an **envelope-authoring agent** that hand-authors
  `ExperimentalConditionValidationResult` directly, exactly like the
  gene/allele/agm/disease/ontology_term/controlled_vocabulary validators author their
  envelopes — NOT a builder that stages into a backend materializer.
- Therefore the **builder metadata-template rule does NOT apply**: there is no
  `stage_*`/`finalize_*` workflow and no `<validator_handoff>` to write (a validator IS
  the handoff target). The model fills the `ExperimentalConditionValidationResult` root
  fields itself, including the composite `component_validations[]` records.
- Its job is to **RESOLVE / VALIDATE a composite `ExperimentalCondition`** — a
  `ConditionRelation` carrying a relation type plus grounded `ExperimentalCondition`
  components (a ZECO/XCO/MMO condition class naming the experimental-variable TYPE, an
  optional ChEBI chemical, an optional NCBITaxon taxon, optional quantity/unit controlled
  vocabulary, and data-provider context) — by grounding each present component against the
  AGR curation DB via `agr_curation_query`, then composing component decisions into one
  condition-level decision, and returning the **shared validator result contract**
  (`DomainValidatorResultBase` root fields) plus the composite-specific result detail.

So the rewrite uses the **role-adapted, outcome-first VALIDATOR skeleton**:
`<role>` -> `<goal>` (success folded in) -> `<scope>` ->
`<resolution_and_validation_rules>` -> `<lookup_workflow>` -> `<result_contract>` ->
`<stop_rules>`.

### VALIDATOR framing (load-bearing, per Chris)

The validator is framed as the **stronger specialized resolver** with deeper DB access
and a curator-editable prompt — NOT a guardrail policing a "forbidden" extractor. The
base prompt IS curator-editable; it is written in curator voice for a biologist with no
developer background. The experimental-condition validator owns the per-component
grounding, the ChEBI-as-ontology-term resolution, the relation-coherence judgment, and
the final composite condition-level call — "yours to resolve well, not hand back".

### NO group rules, NO search-mechanic relocation (verified)

- `agent.yaml` has `group_rules_enabled: false`, so there is **no group inventory**.
- `agent.yaml`'s `supervisor_routing.batchable: true` plus `batch_capabilities:
  [domain_validator_batch]` let the supervisor combine condition requests, but the editable
  base never instructed a `mode: "domain_validator_batch"` per-batch protocol. The rewrite
  does NOT invent one (faithful migration). The composite still validates one condition per
  target.
- The pre-rewrite prompt carried **no** gene-style search-order mechanic; see the
  SEARCH-MECHANIC CHECK above. `agr_curation.py`, `bindings.yaml`, and
  `tool_catalog_baseline.json` are untouched.

---

## Template rules applied (Phase C — VALIDATOR template)

### Template rule — no core duplication (de-dup lever 1: required-tool-call + output)

`assembly.py::_build_compact_runtime_contract` already injects, for
`experimental_condition_validation` (verified by rendering
`build_agent_core_prompt('experimental_condition_validation')`):

- the **required-tool-call policy**: "Required tool-call policy: call at least one of
  agr_curation_query before final output.";
- the **output contract**: "Output contract from agent.yaml: produce JSON matching
  ExperimentalConditionValidationResult; the structured-output layer below is
  authoritative for final response shape." PLUS the CRITICAL structured-output block
  ("Your final response MUST be valid JSON matching the
  ExperimentalConditionValidationResult schema EXACTLY");
- the **get_agent_contract** pointer for detailed field/tool/schema/validator facts.

The pre-rewrite BASE prompt restated the required-tool imperative (the `# Lower-Level
Capabilities` "You MUST call the relevant package tool before marking any present ...
component as resolved") and the output mandate (the `# Goal` "return structured
`ExperimentalConditionValidationResult` data" + the `# Shared Result Contract` "Return only
the shared validator statuses" opener). The rewrite removes the literal restatement of the
machine required-tool imperative and the JSON-only output mandate (de-dup, recorded in
`.dropped.json` as `relocated -> render`), but KEEPS the curator-facing curation rule once
(in `<resolution_and_validation_rules>`: "call the relevant package tool before marking any
present component resolved") and the `ExperimentalConditionValidationResult` token once in
`<goal>` for curator readability.

### Template rule — Shared Result Contract: **LOAD-BEARING, KEPT (de-dup lever 2)**

VERIFIED: the `# Shared Result Contract` block is NOT injected by any shared prompt layer —
it is the ONLY place these fields are described for this agent. KEPT (wording tightened, no
field dropped). Every shared root field plus the composite-specific roots
(`condition_status`, `condition_id`, `normalized_components`, `component_validations`,
`unresolved_components`) is retained. The contract-test requires `component_validations` and
`unresolved_components` (and the status tokens) to be present verbatim.

### Template rule — reason_codes: **none (no `.reason_codes.txt`) — confirmed**

Validators do NOT enumerate exclusion reason codes. `ExperimentalConditionValidationResult`
defines no reason-code enum bound to the validator output; `lookup_attempts[].outcome` is an
outcome enum and the per-component `status` is a small validator-status enum, not a fixed
exclusion-code enum. So none is created.

---

## Role / goal / success (folded into goal)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| ECV-01 | Agent identity: an Experimental Condition Validation Specialist for Alliance Genome Resources curation; validates ONE composite `ExperimentalCondition` target by composing lower-level component + package-tool evidence into one condition-level decision. | `<role>` (reframed to curator-voice "stronger specialized composite resolver" with DB access + final say) |
| ECV-02 | Goal: validate an experimental-condition payload from a `DomainValidationRequest`; return structured `ExperimentalConditionValidationResult` using the shared validator result contract. The condition may include relation vocabulary, condition class/id ontology terms, chemicals, taxa, data-provider context, free text, quantity/unit constraints, and evidence quote context. | `<goal>` (verbatim `DomainValidationRequest` + `ExperimentalConditionValidationResult` tokens retained) |
| ECV-03 | Success (folded): the condition resolves only when every required component is grounded from package-tool evidence; the condition-level `status`/`condition_status` is the composed decision. | `<goal>` + `<result_contract>` (verbatim status tokens) |

## Scope / supported inputs / no-transfer

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| ECV-04 | Supported request inputs read from `selected_inputs` and `target.input_values` when present: `condition_statement` (source text or synthesized condition summary); `condition_components` (structured component list or payload object); `condition_relation_type` (the parent condition-relation name e.g. `has_condition`, supplied as coherence CONTEXT ONLY — the dedicated condition-relation binding owns its vocabulary validation; do not re-resolve it here); `condition_class_curie`/`condition_class_name`/`condition_id_curie`/`condition_id_name` (ZECO/XCO/MMO-style condition ontology inputs); `condition_chemical_curie`/`condition_chemical_name`/`chemical_name` (ChEBI chemical inputs, validated as ontology terms (ontologytermtype `CHEBITerm`) via `agr_curation_query` `get_ontology_term`); `condition_taxon_curie`/`taxon`/`taxon_id` (NCBITaxon inputs); `data_provider_abbreviation`/`data_provider_name`/`data_provider` (provider context for taxon/provider consistency); `condition_quantity`/`condition_unit`/`condition_free_text`/`evidence_quote` (supplemental context that can explain the condition but must not override lookup evidence). | `<scope>` (the composite `selected_inputs` contract; field list retained as the handoff channel the validator reads) |
| ECV-05 | This agent only performs composite experimental-condition validation. For work outside that scope, do not transfer work, invoke another agent, or perform another agent's task; state the out-of-scope portion is outside this agent's tools and schema, preserve any in-scope condition lookup, and leave next-step selection to the supervisor/caller. | `<scope>` (no cross-agent transfer — VALIDATOR-template discipline in curator voice, mirroring the single validators) |

## Resolution & validation rules (grounding + component ownership + coherence + per-component policy)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| ECV-06 | Per-component grounding from lookup, not memory: you MUST call the relevant package tool before marking any present ontology, chemical (ChEBI as ontology term), data-provider, or taxon component as resolved. Each component CURIE is grounded via lookup, never guessed from memory or training-data knowledge. If a required lower-level capability is absent, unavailable, or cannot be executed, leave that component unresolved and explain that the condition binding cannot be active yet. | `<resolution_and_validation_rules>` (the curator-facing call-before-resolve grounding rule — machine required-tool imperative is CORE ECV-RTC) |
| ECV-07 | Component ownership map (use the same tools/semantics owned by the lower-level validator bundles; do not invent a separate lookup policy): condition class/id ontology terms and condition taxon use the `ontology_term_validation` capability through `agr_curation_query` methods `get_ontology_term`, `get_ontology_terms`, `search_ontology_terms`, and `map_curies_to_names`; condition chemical (ChEBI) is an ontology term in the curation DB (ontologyterm table, ontologytermtype `CHEBITerm`) validated via `agr_curation_query` `get_ontology_term` (or `get_ontology_terms`) exactly like ZECO/taxon — call `get_ontology_term` with `term=<CHEBI:...>`; do NOT call `chebi_api_call` for condition chemicals, the ChEBI REST endpoint is not the resolution path for condition components; quantity/unit vocabulary uses the `controlled_vocabulary_validation` capability through `agr_curation_query` methods `get_vocabulary_term` and `search_vocabulary_terms`; data-provider context uses the `data_provider_validation` capability through `agr_curation_query` methods `get_data_provider` and `get_data_providers`. | `<resolution_and_validation_rules>` (the component ownership map; ALL granted methods backticked + the capability tokens `ontology_term_validation`/`controlled_vocabulary_validation`/`data_provider_validation` + ``ontologytermtype `CHEBITerm` `` + `` `chebi_api_call` `` — contract-test requirements) |
| ECV-08 | Relation type is COHERENCE CONTEXT ONLY: the dedicated condition-relation binding owns relation-vocabulary validation and already resolves the relation name (e.g. `has_condition`) against its own vocabulary. Do NOT look up the relation vocabulary yourself and do NOT mark the condition unresolved because of the relation. Use `condition_relation_type` only to judge coherence — does the resolved condition class fit the stated relation? — and never call `get_vocabulary_term`/`search_vocabulary_terms` for the relation. | `<resolution_and_validation_rules>` (the relation coherence-only rule — composite-coherence check, load-bearing) |
| ECV-09 | Per-component status policy: build one `component_validations[]` entry for every present or required component — set `validator_agent` to the lower-level owner above, preserve each component's field path and selected inputs, copy component lookup attempts into `component_validations[].lookup_attempts`, and preserve ambiguous, obsolete, conflicting, prefix-mismatched, or alternate records in `component_validations[].candidates`. Use `status: "resolved"` only when tool evidence resolves the component and all expected component fields are present; `status: "unresolved"` when a required component is missing, ambiguous, not found, obsolete, conflicting, has a prefix/type/provider/taxon mismatch, or the tool fails; `status: "not_present"` for optional components that are absent; `status: "not_checked"` only for supplemental free text or evidence context that has no lower-level lookup. | `<resolution_and_validation_rules>` (the per-component decision policy; the four per-component status values + `selected_inputs` channel — load-bearing component contract) |
| ECV-10 | Composite-coherence: ambiguous or missing required component results MUST keep the root condition `status: "unresolved"`, `condition_status: "unresolved"`, and list the component in `unresolved_components`. | `<resolution_and_validation_rules>` (the composite roll-up rule that ties component failures to the condition-level decision) |

## Lookup workflow (bounded ordered composite path)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| ECV-11 | Bounded path step 1: enumerate every present or required component of the condition (relation type as coherence context only). | `<lookup_workflow>` (ordered step 1 — invariants file pins this) |
| ECV-12 | Bounded path step 2: for each present grounded component (condition class/id, condition chemical as ChEBI ontology term, condition taxon, quantity/unit, data provider), call the owning capability's `agr_curation_query` method before resolving it; record each call in that component's `lookup_attempts`. | `<lookup_workflow>` (ordered step 2) |
| ECV-13 | Bounded path step 3: resolve a component only when tool evidence returns a single unambiguous match satisfying its expected fields; preserve ambiguous, obsolete, conflicting, or alternate records as that component's candidates and leave it unresolved. | `<lookup_workflow>` (ordered step 3) |
| ECV-14 | Bounded path step 4: judge relation coherence using `condition_relation_type` as context only — does the resolved condition class fit the stated relation? — without looking up the relation vocabulary. | `<lookup_workflow>` (ordered step 4) |
| ECV-15 | Bounded path step 5: compose the condition-level decision — `status`/`condition_status` resolved only when every required component resolved and the condition-level expected fields can be populated from lookup evidence; otherwise unresolved with the blocking components listed in `unresolved_components`. | `<lookup_workflow>` (ordered step 5) |

## Result contract (ExperimentalConditionValidationResult — model-authored shared + composite contract)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| ECV-16 | Return only the shared validator statuses: `status: "resolved"` when every required condition component is resolved and the condition-level expected fields can be populated from lookup evidence; `status: "unresolved"` when any required component is missing, ambiguous, unavailable, or only partially resolved. Populate root fields exactly; do not wrap them under another object: `request_id`, `validator_binding_id`, `validator_agent`, `target` (copied from the request), `resolved_values`, `resolved_objects`, `missing_expected_fields`, `candidates`, `lookup_attempts`, `curator_message`, `explanation`. | `<result_contract>` (verbatim status tokens; the four request-copy fields collapsed to one line) |
| ECV-17 | `resolved_values`: scalar condition-level values keyed by the binding's expected-result fields, such as `condition_id` and `normalized_components`. For resolved conditions, copy expected scalar outputs into `resolved_values` using the binding's `expected_result_fields` keys; include `condition_id` only when derived from provider evidence or a stable condition key explicitly supplied by the request. | `<result_contract>` |
| ECV-18 | `resolved_objects`: normalized condition facts returned or selected from component lookups. `missing_expected_fields`: expected fields this composite validator could not populate. `candidates`: generic condition-level candidates or component candidates that require curator selection. `lookup_attempts`: aggregate every tool lookup attempted for the condition. `curator_message`/`explanation`: concise curator-facing summary of resolved/unresolved components plus a plain-language condition decision tied to component evidence and lookup attempts; for unresolved conditions the `curator_message` names the blocking components. | `<result_contract>` |
| ECV-19 | Composite-specific roots: `condition_status` (copy the root status as a condition-level decision); `condition_id` (resolved ExperimentalCondition identifier or stable condition key when available); `normalized_components` (resolved component snapshots with component type, field path, resolved values, resolved objects, source inputs, and lower-level `validator_agent`); `component_validations` (per-component decision records with candidates, lookup attempts, missing fields, curator message, and explanation); `unresolved_components` (component types or field paths keeping the condition unresolved). For unresolved conditions, preserve any partial `normalized_components` and keep unfilled expected keys in `missing_expected_fields`. | `<result_contract>` (the composite roots — contract-test requires `component_validations` + `unresolved_components` verbatim) |
| ECV-20 | Do not use metadata-only validator states as result statuses. Do not return compatibility fields, legacy summary stores, or envelope patches; envelope composition belongs outside validator results. | `<result_contract>` (verbatim — the no-metadata-status + no-compat-fields discipline; note `repair_action` must NOT appear, per contract test) |

## Stop rules

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| ECV-21 | Stop once you have the database evidence each component needs; do not keep searching to improve phrasing. | `<stop_rules>` (VALIDATOR-template stop rule, parallel to the single validators) |
| ECV-22 | If a component stays ambiguous after lookup, preserve its verified candidate set and keep the condition unresolved rather than guessing. | `<stop_rules>` (folds ECV-09/ECV-10 ambiguity outcome) |
| ECV-23 | If data is outside this agent's scope, do not fabricate, transfer work, or call another specialist; state the scope limit and return only the supported in-scope composite results. | `<stop_rules>` (merged with ECV-05) |

## CORE-injected (no base restatement)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| ECV-RTC | Required tool-call policy: call at least one of `agr_curation_query` before final output. | CORE (`render`). The base keeps the curator-facing "call the relevant package tool before marking any present component resolved" rule (ECV-06) but does not restate the machine imperative. |
| ECV-OUT | Output mandate: produce JSON matching `ExperimentalConditionValidationResult`; the structured-output layer is authoritative for final response shape. | CORE (`render`). The base keeps the `ExperimentalConditionValidationResult` token once but does not restate the JSON-only output mandate. |

---

## De-dup summary (the experimental_condition-validator Phase-C levers)

1. **CORE de-dup:** the required-tool-call imperative (`# Lower-Level Capabilities` "You
   MUST call the relevant package tool ...") and the JSON-output mandate (`# Goal` "return
   structured `ExperimentalConditionValidationResult` data" + `# Shared Result Contract`
   "Return only the shared validator statuses" opener) are relocated to the locked core
   (kept as one curator-facing call-before-resolve line + the
   `ExperimentalConditionValidationResult` token once).
2. **Shared + composite Result Contract:** verified NOT injected by a shared layer; KEPT
   (load-bearing), wording tightened, no field dropped — including the composite roots
   `component_validations`/`unresolved_components`/`condition_status`/`condition_id`/
   `normalized_components`.
3. **Consolidation:** `# Supported Request Inputs`, `# Lower-Level Capabilities`,
   `# Component Policy`, `# Shared Result Contract`, and `# Output Rules` consolidate into
   `<scope>` (inputs + no-transfer) + `<resolution_and_validation_rules>` (grounding +
   component ownership map + relation-coherence-only + per-component status policy +
   composite roll-up) + `<lookup_workflow>` (bounded ordered composite path) +
   `<result_contract>` (shared + composite result fields) without losing a rule.
4. **NO search-mechanic relocation, NO `match_type` deletion, NO repository URL, NO group
   rules:** the condition prompt never carried those, so none is added or relocated.
   `agr_curation.py` / `bindings.yaml` / `tool_catalog_baseline.json` are untouched.

## Contract-test coverage

**No test assertion is edited, deleted, or weakened by this rewrite.**
`backend/tests/unit/lib/config/test_experimental_condition_validation_agent.py`
(`test_experimental_condition_prompt_and_tool_grant_name_lower_level_methods`) constrains
the experimental_condition base prompt content: it requires every granted
`agr_curation_query` method backticked (`get_ontology_term`, `get_ontology_terms`,
`search_ontology_terms`, `map_curies_to_names`, `get_vocabulary_term`,
`search_vocabulary_terms`, `get_data_provider`, `get_data_providers`), plus
`` `ontology_term_validation` ``, `` `controlled_vocabulary_validation` ``,
`` `data_provider_validation` ``, ``ontologytermtype `CHEBITerm` ``, `` `chebi_api_call` ``,
`status: "resolved"`, `status: "unresolved"`, `component_validations`,
`unresolved_components` — all retained verbatim — AND that `repair_action` does NOT appear.
The schema-validation tests assert against the `ExperimentalConditionValidationResult`
model, not the prompt text, so they are unaffected. No re-baseline was needed.
