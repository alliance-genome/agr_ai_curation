# Phase C semantic-coverage checklist: `subject_entity` validator (Wave 3 — VALIDATOR skeleton)

This is the **authoritative inventory source** for the outcome-first rewrite of
`packages/alliance/agents/subject_entity/prompt.yaml` (canonical agent id
`subject_entity_validation`). Every load-bearing rule in the pre-rewrite prompt is
listed here with a stable ID (SEV-NN) and its new home in the rewritten prompt, OR
an explicit, justified relocation/deletion. The harness inventories
(`phase_c_inventories/subject_entity.txt`, `.invariants.txt`, `.dropped.json`) are
derived from this checklist.

`subject_entity` follows the **VALIDATOR skeleton** the `gene` pilot established
(`docs/design/phaseC-checklists/gene.md`) and the `allele`/`agm` rewrites reused. It
is a clean template application: the pre-rewrite prompt carried NO `# Available
Methods` LIKE/case-insensitive/`match_type` mechanics block, NO repository URL, and
NO batch protocol, so there is NO search-mechanic relocation, NO `match_type`
deletion, and NO batch inventory.

`subject_entity` is special among the validators in that it is a **typed-subject
ROUTER**: it resolves the SUBJECT of a phenotype/disease annotation across entity
kinds (gene / allele / AGM) by reading the explicit `subject_type`, selecting one
concrete validator route, and recording that routing provenance. The routing
decision tree is the load-bearing core of this prompt and is preserved in full.

Legend for "New home":
- A `<section>` name is a section of the **rewritten** base prompt.
- `CORE` / `render` means the locked Generated Runtime Contract
  (`assembly.py::_build_compact_runtime_contract`) already injects this exact fact;
  the base prompt does NOT restate the core's phrasing. Recorded in
  `.dropped.json` as `relocated -> render`.
- `DELETED` means the rule's fact is dropped with no home; recorded in
  `.dropped.json` as `deleted` (printed for review).

---

## What `subject_entity` actually IS (role + output contract + skeleton choice)

`subject_entity` (canonical agent id `subject_entity_validation`) is a **domain-pack
VALIDATOR / subject ROUTER**, not an extractor and not a builder. Verified against
the code:

- `packages/alliance/agents/subject_entity/agent.yaml` sets
  `output_schema: SubjectEntityValidationResult` (NOT `null`) and tools
  `[get_agent_contract, agr_curation_query]`, with `group_rules_enabled: false`. So
  it is an **envelope-authoring agent** that hand-authors
  `SubjectEntityValidationResult` directly, exactly like the gene/allele/agm
  validators author their envelopes — NOT a builder that stages into a backend
  materializer.
- Therefore the **builder metadata-template rule does NOT apply** here: there is no
  `stage_*`/`finalize_*` workflow, no "you never write the envelope / backend
  materializes metadata", no exclude=don't-stage rewrite, and no
  `<validator_handoff>` to write (a validator IS the handoff target). The model
  fills the `SubjectEntityValidationResult` root fields itself, including the routing
  provenance (`selected_validator`).
- Its job is to **ROUTE and RESOLVE the typed subject** of a phenotype/disease
  annotation. It reads the explicit `subject_type`, selects exactly one concrete
  validator route (gene / allele / AGM), runs the matching lookup semantics against
  the AGR curation DB via `agr_curation_query`, and returns the **shared validator
  result contract** (`DomainValidatorResultBase` root fields) plus subject-routing
  detail (`normalized_subject_*`, `selected_validator`, `subject_candidates`,
  `unresolved_explanations`).

So the rewrite uses the **role-adapted, outcome-first VALIDATOR skeleton**:

`<role>` -> `<goal>` -> `<success_criteria>` -> `<scope>` ->
`<resolution_and_validation_rules>` -> `<lookup_workflow>` -> `<result_contract>` ->
`<stop_rules>`. The outcome-first ORDER (Role -> Goal -> Success -> Scope -> Rules
-> Workflow -> Output -> Stop) is preserved. For this router, the
`<resolution_and_validation_rules>` carry the route-from-subject_type discipline +
type normalization + per-route selection, and `<lookup_workflow>` carries the
ordered routing decision path (normalize -> select one route -> run that route's
lookup semantics).

### VALIDATOR framing (load-bearing, per Chris)

The validator is framed as the **stronger specialized resolver** with deeper DB
access and a curator-editable prompt — NOT a guardrail policing a "forbidden"
extractor. The base prompt IS curator-editable; it is written in curator voice for
a biologist with no developer background. (Positive, capable framing: the subject
router owns the type routing, the route selection, the lookup, and the final
subject-identity call — "yours to resolve well, not hand back".)

### NO group rules, NO batch protocol, NO search-mechanic relocation (verified)

- `agent.yaml` has `group_rules_enabled: false`, so there is **no group inventory**
  (no `.mgi.txt`).
- The pre-rewrite prompt carried **no** batch / bulk-grouping protocol. `agent.yaml`'s
  `supervisor_routing.batchable: true` lets the supervisor combine subjects (one
  result per subject target), but the editable base never instructed a per-batch
  protocol. The rewrite does NOT invent one. No batch inventory phrases.
- The pre-rewrite prompt carried **no** `# Available Methods` block restating LIKE/
  exact/prefix/contains search order, case-insensitivity, or a standalone
  `match_type` mechanic. So there is NO strategy-affecting search-mechanic to
  relocate and NO `match_type` deletion. `agr_curation.py`, `bindings.yaml`, and
  `tool_catalog_baseline.json` are NOT touched by this rewrite.

---

## Template rules applied (Phase C — VALIDATOR template)

### Template rule — builder metadata exclude=don't-stage: **N/A (verified)**

Does not apply: `subject_entity_validation` authors `SubjectEntityValidationResult`
directly (see above). There is no `metadata.*` materializer and no stage tool. The
model EXPRESSES an unresolved outcome by writing `status: "unresolved"` +
`missing_expected_fields` + `candidates`/`subject_candidates` +
`unresolved_explanations` — real top-level, model-authored channels. Preserve that
mechanism; do not import the builder don't-stage rewrite.

### Template rule — no core duplication (de-dup lever 1: required-tool-call + output)

`assembly.py::_build_compact_runtime_contract` already injects, for
`subject_entity_validation` (verified by rendering
`build_agent_core_prompt('subject_entity_validation')`):

- the **required-tool-call policy**: "Required tool-call policy: call at least one of
  agr_curation_query before final output.";
- the **output contract**: "Output contract from agent.yaml: produce JSON matching
  SubjectEntityValidationResult; the structured-output layer below is authoritative
  for final response shape." PLUS the CRITICAL structured-output block ("Your final
  response MUST be valid JSON matching the SubjectEntityValidationResult schema
  EXACTLY");
- the **get_agent_contract** pointer for detailed field/tool/schema/validator facts.

The pre-rewrite BASE prompt restated the required-tool-call imperative; the rewrite
removes it (de-dup, recorded in `.dropped.json` as `relocated -> render`), but KEEPS
the curator-facing curation rule once. Specifically:

- "You MUST call `agr_curation_query` before returning a resolved result. For
  unresolved missing-input or unsupported-type requests, return unresolved without
  making an unrelated lookup." (`# Tool Requirement`) -> the machine imperative
  de-dups to CORE's required-tool-call policy; the curator-facing
  "for unresolved missing-input or unsupported-type requests, return unresolved
  without making an unrelated lookup" judgment is KEPT (it is a routing rule, not the
  bare imperative), and the curator-facing success line "Calls `agr_curation_query`
  before returning a resolved result" is KEPT once with the literal
  `` `agr_curation_query` `` token present.
- The pre-rewrite `# Goal` carried the 'produce SubjectEntityValidationResult /
  structured-output' mandate that the locked core injects. The rewrite no longer
  restates the JSON-only output mandate; the core owns it. The
  `SubjectEntityValidationResult` token is named once in `<goal>`/`<result_contract>`
  for curator readability.

### Template rule — Shared Result Contract: **LOAD-BEARING, KEPT (de-dup lever 2)**

VERIFIED: the `# Shared Result Contract` block is NOT injected by any shared prompt
layer — it is the ONLY place these fields are described for this agent. It is
therefore **load-bearing and KEPT** (wording tightened, no field dropped). Every
shared root field plus the router-specific roots (`normalized_subject_identifier`,
`normalized_subject_type`, `normalized_subject_label`, `taxon`, `selected_validator`,
`subject_candidates`, `unresolved_explanations`) are retained. The contract test
asserts `` `selected_validator` `` and `` `unresolved_explanations` `` verbatim.

### Template rule — reason_codes: **none (no `.reason_codes.txt`) — confirmed**

Validators do NOT enumerate exclusion reason codes. `SubjectEntityValidationResult`
defines no reason-code enum bound to the validator output; `unresolved_explanations`
is a free model-written list and `lookup_attempts[].outcome` is an outcome enum. So
none is created.

---

## Role / goal / success

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| SEV-01 | Agent identity: a typed annotation subject router for Alliance Genome Resources curation that validates phenotype and disease subjects using the explicit `subject_type` selected from the domain envelope. | `<role>` (reframed to curator-voice "stronger specialized resolver / router"; router identity + DB-lookup purpose retained) |
| SEV-02 | Goal: validate a biological annotation subject from a `DomainValidationRequest`; return structured `SubjectEntityValidationResult` data using the shared validator result contract; the subject may be a Gene, Allele, or AGM. | `<goal>` (verbatim `DomainValidationRequest` + `SubjectEntityValidationResult` tokens retained) |
| SEV-03 | Route ONLY from the selected `subject_type`; never guess a type from labels, identifiers, sibling objects, paper context, or training-data knowledge. | `<goal>` + `<resolution_and_validation_rules>` (verbatim contract-test token "Route only from the selected `subject_type`"; no-guess-the-type invariant) |
| SEV-04 | Success: calls `agr_curation_query` before returning a resolved result (the machine imperative is CORE SEV-RTC). | `<success_criteria>` (curator-facing success line KEPT once; literal `` `agr_curation_query` `` retained) |
| SEV-05 | Success: resolved subjects copy expected scalar outputs into `resolved_values` using the binding's `expected_result_fields` keys, such as `subject_identifier`, `subject_type`, `subject_label`, and `taxon`. | `<success_criteria>` + `<result_contract>` (SEV-19) |
| SEV-06 | Success: uses `status: "resolved"` only when the selected route returns exactly one subject match and no supplied subject_type/identifier/label/taxon context conflicts with lookup evidence; uses `status: "unresolved"` otherwise. | `<success_criteria>` + `<result_contract>` + `<resolution_and_validation_rules>` (verbatim status tokens; the decision policy SEV-15) |
| SEV-07 | Success: lists every unfilled expected key in `missing_expected_fields` and explains the failure reason in `curator_message`. | `<success_criteria>` + `<result_contract>` (SEV-20) |
| SEV-08 | Success: records every database call in `lookup_attempts`, including provider, method, query, result count, and outcome. | `<success_criteria>` + `<result_contract>` (SEV-18) |

## Scope / no-transfer / supported inputs

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| SEV-09 | Supported request inputs read from `selected_inputs` and `target.input_values` when present: `subject_type` (required route selector; accepted normalized values `gene`, `allele`, `agm`), `subject_identifier` (required subject CURIE or primary external ID), `subject_label` (optional subject symbol or label), `taxon` (optional NCBITaxon CURIE). | `<scope>` (the router `selected_inputs` contract; field list retained as the handoff channel) |
| SEV-10 | Missing-input rule: if `subject_type` or `subject_identifier` is missing from the request, return `status: "unresolved"` with a missing-input explanation. Deterministic selector failures normally prevent those requests before they reach you; still preserve this behavior for direct agent tests. | `<scope>` + `<resolution_and_validation_rules>` (missing-input discipline retained, incl. the direct-agent-test note) |
| SEV-10s | This agent only routes and validates annotation subjects. For a request outside this router's gene/allele/AGM scope, do not transfer work, invoke another agent, or perform another agent's task; state the scope limit, preserve any in-scope subject lookup, and leave next-step selection to the supervisor/caller. | `<scope>` (no cross-agent transfer — VALIDATOR-template discipline added in curator voice, mirroring gene/allele scope) |

## Resolution & validation rules (route-from-type, type normalization, per-route selection, decision policy)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| SEV-03r | (see SEV-03) Route only from the selected `subject_type`; never guess a type. | `<resolution_and_validation_rules>` (no-guess-the-type invariant home) |
| SEV-11 | Type normalization: normalize `subject_type` only by exact, explicit type labels: `gene`/`Gene`/`GENE` -> `gene`; `allele`/`Allele`/`ALLELE` -> `allele`; `agm`/`AGM`/`affected_genomic_model`/`Affected Genomic Model` -> `agm`. Any other value is unsupported. | `<resolution_and_validation_rules>` (exact-label normalization table retained) |
| SEV-12 | Unsupported type: return unresolved and do not call a gene, allele, or AGM lookup path for unsupported subject types. | `<resolution_and_validation_rules>` (unsupported-type stop; no unrelated lookup) |
| SEV-13 | Select exactly one concrete route. Gene route: set `selected_validator.validator_agent` to package `agr.alliance`, agent `gene_validation`; use the gene validator lookup semantics (`get_gene_by_id` when `subject_identifier` is a gene CURIE, otherwise `search_genes` or `get_gene_by_exact_symbol` using `subject_label`/identifier text plus taxon-derived provider context when available). | `<lookup_workflow>` (gene route — verbatim `gene_validation` + `selected_validator` contract tokens; the route's lookup semantics retained as which-method-when judgment) |
| SEV-14 | Allele route: set `selected_validator.validator_agent` to package `agr.alliance`, agent `allele_validation`; use the allele validator lookup semantics (`get_allele_by_id` when `subject_identifier` is an allele CURIE, otherwise `search_alleles` or `get_allele_by_exact_symbol` using `subject_label`/identifier text plus taxon-derived provider context when available). | `<lookup_workflow>` (allele route — verbatim `allele_validation`; lookup semantics retained) |
| SEV-15g | AGM route: set `selected_validator.validator_agent` to package `agr.alliance`, agent `agm_validation`; use `map_entity_curies_to_info` with `entity_type: "agm"` for identifier lookup, and `map_entity_names_to_curies` with `entity_type: "agm"` only when label and taxon context are present. | `<lookup_workflow>` (AGM route — verbatim `agm_validation`; AGM helper-method semantics retained) |
| SEV-15 | Decision policy — resolved: return `status: "resolved"` only when the selected route returns exactly one subject match and no supplied subject_type, identifier, label, or taxon context conflicts with the lookup evidence. | `<resolution_and_validation_rules>` (decision policy resolved condition) |
| SEV-16 | Decision policy — unresolved: return `status: "unresolved"` when `subject_type` is missing/ambiguous/unsupported/conflicts with the selected route; `subject_identifier` is missing; taxon context required for the selected label lookup is absent; the selected route returns zero candidates, multiple candidates, a taxon conflict, or a tool error; or any expected result field cannot be populated from lookup evidence. | `<resolution_and_validation_rules>` (decision policy unresolved conditions — full list retained) |
| SEV-17 | No-envelope-composition: do not repair or compose envelope objects; extractors and materializers own envelope composition. This validator returns only decisions, facts, selected validator provenance, candidates, lookup attempts, and explanations. | `<result_contract>` (verbatim — the no-repair/no-compose discipline; `repair_action` is NEVER introduced, per contract test) |

## Lookup workflow (ordered routing decision path + per-route lookup semantics)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| SEV-WF | Ordered routing path: (1) normalize `subject_type` by exact label; (2) for an unsupported type, return unresolved without calling a lookup path; (3) select exactly one concrete route (gene / allele / AGM) and set `selected_validator`; (4) run that route's lookup semantics against `agr_curation_query`; (5) resolve only on exactly one non-conflicting match, otherwise return unresolved and preserve candidates. | `<lookup_workflow>` (ordered routing path — invariants file pins normalize -> unsupported-stop -> select-route -> run-lookup -> resolve/unresolve order) |

## Result contract (SubjectEntityValidationResult — model-authored shared validator contract)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| SEV-18 | Populate these root fields exactly; do not wrap them under another object: `status`, `request_id`, `validator_binding_id`, `validator_agent`, `target`, `resolved_values`, `resolved_objects`, `missing_expected_fields`, `candidates`, `lookup_attempts`, `curator_message`, `explanation`, `normalized_subject_identifier`, `normalized_subject_type`, `normalized_subject_label`, `taxon`, `selected_validator`, `subject_candidates`, `unresolved_explanations`. | `<result_contract>` (verbatim status + backticked root-field tokens, incl. `selected_validator` and `unresolved_explanations` [contract-test tokens]) |
| SEV-19 | Resolved subjects: copy expected scalar outputs into `resolved_values` using the binding's `expected_result_fields` keys, such as `subject_identifier`, `subject_type`, `subject_label`, and `taxon`. | `<result_contract>` |
| SEV-20 | Unresolved subjects: keep `resolved_values` empty or partial, list all unfilled expected keys in `missing_expected_fields`, preserve candidates returned by the selected route, and write a curator-facing `curator_message` that says whether the issue was missing input, unsupported type, no match, ambiguity, taxon conflict, missing taxon context, or tool error. | `<result_contract>` |
| SEV-21 | Do not use metadata-only validator states as result statuses. Do not return `under_development`. | `<result_contract>` (verbatim — the no-metadata-status discipline; `under_development` named as the forbidden status) |

## Stop rules

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| SEV-22 | Stop once the selected route's evidence is sufficient to populate the result; do not keep searching to improve phrasing. | `<stop_rules>` (VALIDATOR-template stop rule added in curator voice) |
| SEV-23 | If the selected route returns zero or multiple candidates, a taxon conflict, or a tool error, return `status: "unresolved"`, preserve any candidates, record the lookup attempts, and explain what could not be resolved. | `<stop_rules>` (folds SEV-16 outcomes) |
| SEV-24 | If `subject_type` or `subject_identifier` is missing, or the type is unsupported, return `status: "unresolved"` with the matching explanation and do not call an unrelated lookup. | `<stop_rules>` (folds SEV-10 / SEV-12) |
| SEV-25 | If data is outside this router's scope, do not fabricate, transfer work, or call another specialist; state the scope limit and return only supported in-scope subject results. | `<stop_rules>` (merged with SEV-10s) |

## CORE-injected (no base restatement)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| SEV-RTC | Required tool-call policy: call at least one of `agr_curation_query` before final output. | CORE (`render`). The base keeps the curator-facing "calls `agr_curation_query` before returning a resolved result" success line (SEV-04) and the "for unresolved missing-input or unsupported-type requests, return unresolved without making an unrelated lookup" routing judgment (SEV-12), but does not restate the bare machine imperative. |

---

## De-dup summary (the subject_entity-validator Phase-C levers)

1. **CORE de-dup:** the required-tool-call imperative
   (`# Tool Requirement` "You MUST call `agr_curation_query` ...") and the
   JSON-output mandate (the implicit `SubjectEntityValidationResult` output-schema
   restatement in `# Goal`) are relocated to the locked core (kept as one
   curator-facing success line + the `SubjectEntityValidationResult` token once; the
   "return unresolved without an unrelated lookup" routing judgment is KEPT). The
   router root-field detail is KEPT because the core does not enumerate it.
2. **Shared Result Contract:** verified NOT injected by a shared layer; KEPT
   (load-bearing), wording tightened, no field dropped.
3. **Consolidation:** `# Supported Request Inputs`, `# Routing Policy`,
   `# Tool Requirement`, and `# Decision Policy` consolidate into `<scope>` (inputs +
   missing-input) + `<resolution_and_validation_rules>` (route-from-type, type
   normalization, decision policy) + `<lookup_workflow>` (the ordered routing path +
   per-route lookup semantics) without losing a rule.
4. **NO search-mechanic relocation, NO `match_type` deletion, NO repository URL,
   NO batch, NO group rules:** the subject_entity prompt never carried those, so none
   is added or relocated. `agr_curation.py` / `bindings.yaml` /
   `tool_catalog_baseline.json` are untouched.

## Contract-test coverage

**No test assertion is edited, deleted, or weakened by this rewrite.** One contract
test in `backend/tests/unit/test_subject_entity_validator_result_contract.py`
(`test_subject_entity_and_agm_prompts_pin_routing_and_output_policy`) constrains the
subject_entity base prompt content: it requires
`Route only from the selected \`subject_type\`` (SEV-03), `gene_validation` (SEV-13),
`allele_validation` (SEV-14), `agm_validation` (SEV-15g), `` `selected_validator` ``
(SEV-13/SEV-18), and `` `unresolved_explanations` `` (SEV-18) — all retained
verbatim — and forbids `repair_action` (never introduced; SEV-17 keeps the
no-repair/no-compose discipline without the `repair_action` token). All assertions
pass unchanged. The schema-validation tests assert against the
`SubjectEntityValidationResult` model, not the prompt text, so they are unaffected.
No re-baseline was needed.
