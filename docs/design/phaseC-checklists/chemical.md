# Phase C semantic-coverage checklist: `chemical` validator (Wave 3 — VALIDATOR skeleton)

This is the **authoritative inventory source** for the outcome-first rewrite of
`packages/alliance/agents/chemical/prompt.yaml` (canonical agent id
`chemical_validation`). Every load-bearing rule in the pre-rewrite prompt is listed
here with a stable ID (CHV-NN) and its new home in the rewritten prompt, OR an
explicit, justified relocation/deletion. The harness inventories
(`phase_c_inventories/chemical.txt`, `.dropped.json`) are derived from this checklist.

`chemical` follows the **VALIDATOR skeleton** the `gene` pilot established and the
`allele`/`agm`/`disease`/`ontology_term`/`subject_entity`/`data_provider` rewrites
reused. It is a clean template application: the pre-rewrite prompt carried NO gene-style
LIKE/exact-then-prefix-then-contains/`match_type` tool-search-order mechanic, NO
repository URL, and NO `mode: "domain_validator_batch"` per-batch protocol, so there is
NO search-mechanic relocation, NO `match_type` deletion, and NO batch inventory.

> **SEARCH-MECHANIC CHECK (incidental, not real).** The pre-rewrite prompt's
> `<endpoint_reference>`/`<decision_rules>` "match" words are incidental, NOT a
> gene-style "matches by exact / then prefix / then contains, case-insensitive" ChEBI
> search-order mental model the model must understand:
> - `es_search` is described as "Elasticsearch-powered text search ... relevance-ranked
>   ... case-insensitive, partial matching supported" — this is generic search behavior
>   (the curator/model picks the term; the tool ranks), not an exact->prefix->contains
>   ladder the model must replicate. There is no per-call strategy choice surfaced to the
>   model the way `search_genes`' LIKE-pattern ladder was.
> - the stereochemistry / ionization / obsolete "match the form specified" rules are
>   SELECTION/resolution rules (which returned candidate to accept), KEPT in the prompt —
>   they are not a description of how the search tool ranks candidates internally.
>
> So there is NO strategy-affecting search mechanic to relocate to the `chebi_api_call`
> `@function_tool` docstring + bindings.yaml summary, and `rest.py`, `bindings.yaml`, and
> `tool_catalog_baseline.json` are NOT touched by this rewrite.

> **LEAN skeleton (per Chris).** Each rule is stated ONCE. There is NO standalone
> `<success_criteria>` section: the few genuinely-unique success conditions
> (call-before-report, status decision, record-every-call, preserve stereochemistry/
> ionization distinctions) are folded into `<goal>`/`<resolution_and_validation_rules>`.
> `<resolution_and_validation_rules>` carry the unique evidence/structuring rules once
> (call-`chebi_api_call`-before-any-fact, no-memory/no-guess, CHEBI: prefix convention,
> stereochemistry/ionization/obsolete handling, ambiguity-> unresolved, domain-detail
> placement). `<lookup_workflow>` owns the ChEBI endpoint catalog + the bounded ordered
> path, `<result_contract>` is a terse field LIST (the shared root fields collapsed to
> one `request_id`/`validator_binding_id`/`validator_agent`/`target` line, plus the
> chemical-specific result detail and the `lookup_attempts[].query must always be a JSON
> object` rule the schema cannot express), and `<stop_rules>` keeps only the
> genuinely-new stops. No load-bearing rule was dropped; each ID below maps to a lean
> home or a justified relocation.

Legend for "New home":
- A `<section>` name is a section of the **rewritten** base prompt.
- `CORE` / `render` means the locked Generated Runtime Contract
  (`assembly.py::_build_compact_runtime_contract`) already injects this exact fact; the
  base prompt does NOT restate the core's phrasing. Recorded in `.dropped.json` as
  `relocated -> render`.
- `DELETED` means the rule's fact is dropped with no home; recorded in `.dropped.json` as
  `deleted` (printed for review).

---

## What `chemical` actually IS (role + output contract + skeleton choice)

`chemical` (canonical agent id `chemical_validation`) is a **domain-pack VALIDATOR**, not
an extractor and not a builder. Verified against the code:

- `packages/alliance/agents/chemical/agent.yaml` sets
  `output_schema: ChemicalValidationResult` (NOT `null`) and tools
  `[get_agent_contract, chebi_api_call]`, with `group_rules_enabled: false`. So it is an
  **envelope-authoring agent** that hand-authors `ChemicalValidationResult` directly,
  exactly like the gene/allele/agm/disease/ontology_term validators author their
  envelopes — NOT a builder that stages into a backend materializer.
- `ChemicalValidationResult` (`packages/alliance/agents/chemical/schema.py`) extends
  `DomainValidatorResultBase` and adds **NO** chemical-specific root field. So chemical
  candidate detail lives in `candidates[].details` and `resolved_objects`, NOT a
  `chemical_candidates` root.
- Its job is to **RESOLVE / VALIDATE chemical names, synonyms, IDs, or structures** to
  ChEBI ontology matches via the package-owned `chebi_api_call` tool, and return the
  **shared validator result contract** (`DomainValidatorResultBase` root fields) plus
  chemical detail inside `resolved_objects` / `candidates[].details`.

So the rewrite uses the **role-adapted, outcome-first VALIDATOR skeleton**:
`<role>` -> `<goal>` (success folded in) -> `<scope>` ->
`<resolution_and_validation_rules>` -> `<lookup_workflow>` -> `<result_contract>` ->
`<stop_rules>`.

### VALIDATOR framing (load-bearing, per Chris)

The validator is framed as the **stronger specialized resolver** with ChEBI access and a
curator-editable prompt — NOT a guardrail policing a "forbidden" extractor. The base
prompt IS curator-editable; it is written in curator voice for a biologist with no
developer background. The chemical validator owns the ChEBI lookup, the
stereochemistry/ionization/obsolete handling, and the final chemical-identity call —
"yours to resolve well, not hand back".

### Required-tool-call: KEPT in base (NOT injected by core) — VERIFIED

`chebi_api_call` has **NO** `required_tool_call.enforce` metadata in
`packages/alliance/tools/bindings.yaml` (only `agr_curation_query` carries
`required_tool_call.enforce: true`). The generic resolver
`required_tool_names_for_available_tools(['get_agent_contract', 'chebi_api_call'])`
therefore returns an EMPTY set, and `_build_compact_runtime_contract` injects **NO**
"Required tool-call policy" line for this agent. So the base "call `chebi_api_call`
before any chemical fact" imperative is **NOT** duplicated by the core and is **KEPT** in
the rewritten base prompt (it is the only place it appears). The literal `chebi_api_call`
token must stay (contract-test requirement).

### NO group rules, NO batch protocol, NO search-mechanic relocation (verified)

- `agent.yaml` has `group_rules_enabled: false`, so there is **no group inventory** and
  no `.reason_codes.txt` (validators carry no reason-code enum).
- `agent.yaml`'s `supervisor_routing.batchable: true` lets the supervisor combine
  chemical requests, but the editable base never instructed a
  `mode: "domain_validator_batch"` per-batch protocol. The rewrite does NOT invent one
  (faithful migration).
- The pre-rewrite prompt carried **no** gene-style search-order mechanic; see the
  SEARCH-MECHANIC CHECK above. `rest.py`, `bindings.yaml`, and `tool_catalog_baseline.json`
  are untouched.

---

## Template rules applied (Phase C — VALIDATOR template)

### Template rule — no core duplication (de-dup lever: output-schema mandate)

`assembly.py::_build_compact_runtime_contract` already injects, for `chemical_validation`:

- the **output contract**: "Output contract from agent.yaml: produce JSON matching
  ChemicalValidationResult; the structured-output layer below is authoritative for final
  response shape." PLUS the CRITICAL structured-output block ("Your final response MUST be
  valid JSON matching the ChemicalValidationResult schema EXACTLY").

The pre-rewrite BASE prompt restated this output mandate (`<role>` "return one root JSON
object matching `ChemicalValidationResult`" + `<output_format>` "Return only a
`ChemicalValidationResult` JSON object with the shared fields at the root"); the rewrite
removes the JSON-only restatement (de-dup, recorded in `.dropped.json` as
`relocated -> render`), but KEEPS the `ChemicalValidationResult` token once (contract-test
requirement) and the chemical root-field detail in `<result_contract>` (the core does NOT
enumerate those fields). The curator-facing "Do not wrap the object under
`validation_result`, `result`, or any other property" rule is KEPT (contract-test
requirement).

### Template rule — Shared Result Contract: **LOAD-BEARING, KEPT (de-dup lever)**

VERIFIED: the shared validator root-field block is NOT injected by any shared prompt
layer — it is the ONLY place these fields are described for this agent. KEPT (wording
tightened, no field dropped). Every shared root field is retained, plus the
`lookup_attempts[].query must always be a JSON object` rule (schema cannot express it).
The four request-copy fields (`request_id`/`validator_binding_id`/`validator_agent`/
`target`) collapse to ONE line.

### Template rule — reason_codes: **none (no `.reason_codes.txt`) — confirmed**

Validators do NOT enumerate exclusion reason codes. `ChemicalValidationResult` defines no
reason-code enum bound to the validator output; `lookup_attempts[].outcome` is an outcome
enum, not a fixed exclusion-code enum. So none is created.

---

## Role / goal / success (folded into goal)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| CHV-01 | Agent identity: a Chemical Ontology Specialist for ChEBI and Alliance curation; validates chemical ontology targets. | `<role>` (reframed to curator-voice "stronger specialized resolver" with ChEBI access + final say on chemical identity) |
| CHV-02 | Goal: resolve chemical names, synonyms, IDs, or structures to ChEBI ontology matches and return the shared domain-validator result; preserve `request_id`/`validator_binding_id`/`validator_agent`/`target` exactly. | `<goal>` (verbatim `DomainValidationRequest` + `ChemicalValidationResult` tokens retained) |
| CHV-03 | Success (folded): call `chebi_api_call` before returning any chemical fact (KEPT in base; NOT core-injected — see template rule above). | `<goal>` + `<resolution_and_validation_rules>` (literal `chebi_api_call` token retained; contract-test requirement) |
| CHV-04 | Success (folded): return only `status: "resolved"` or `status: "unresolved"`; resolved only when requested chemical expected fields are API-confirmed and unambiguous, unresolved otherwise. | `<goal>` + `<result_contract>` (verbatim status tokens) |
| CHV-05 | Success (folded): record every ChEBI API call in `lookup_attempts`; report API failures transparently and do not invent missing facts. | `<goal>` + `<result_contract>` |
| CHV-06 | Success (folded): return ChEBI entries that best match the query and biological context; preserve distinctions such as stereochemistry and ionization state when available. | `<resolution_and_validation_rules>` (the chemical selection rule) |
| CHV-07 | ChEBI context: ChEBI (Chemical Entities of Biological Interest) is a curated ontology of small molecular entities; each compound has a unique ChEBI ID (e.g. CHEBI:17234 for D-glucose). | `<scope>` (one curator-facing context sentence) |

## Scope / no-transfer / in & out

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| CHV-08 | In scope: map chemical terms, synonyms, IDs, or structures to ChEBI entries; return ChEBI identifiers, names, definitions, formulas, structures, synonyms, and classifications when present; help curation distinguish biologically distinct chemical forms. | `<scope>` |
| CHV-09 | Out of scope: chemical-gene interactions (report only ChEBI-supported chemical identity/classification; leave downstream biological interpretation to the supervisor/caller), drug target/mechanism claims, pathway biology not in ChEBI, commercial availability/pricing/synthesis methods/protocols. For an out-of-scope request, do not transfer work, invoke another agent, or perform another agent's task; state the scope limit, preserve any in-scope chemical lookup, and leave next-step selection to the supervisor/caller. | `<scope>` (no cross-agent transfer — VALIDATOR-template discipline in curator voice, mirroring gene/allele/disease scope) |
| CHV-10 | Read chemical terms from `selected_inputs`, `target.input_values`, and the requested `target.field_path` / `target.expected_fields`. | `<scope>` (the chemical input channel the validator reads) |

## Resolution & validation rules

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| CHV-11 | Call `chebi_api_call` before any chemical answer; never answer from memory or training data; never guess ChEBI identifiers or chemical classifications; never provide chemical information without querying the API; never return fields absent from tool output as null placeholders. | `<resolution_and_validation_rules>` (the no-invention rule; folds the `<constraints>` MANDATORY/NEVER/ALWAYS block into one positive curator-voice rule) |
| CHV-12 | Use the `CHEBI:` prefix plus numeric ID in responses (e.g. CHEBI:17234); API endpoints require just the number (17234). | `<resolution_and_validation_rules>` + `<lookup_workflow>` (the CURIE convention) |
| CHV-13 | Ground every result in tool output: read inputs from `selected_inputs`/`target`; use search first for name/synonym/structure/ID-like inputs; call the compound endpoint for each likely match before returning or evaluating hierarchy; call ontology parents/children only after a specific match is selected (classification requests). | `<resolution_and_validation_rules>` + `<lookup_workflow>` (the evidence-grounding + endpoint-order rule) |
| CHV-14 | Selection rules: multiple results are expected for generic terms — choose entries that best match biological context; prefer the most specific stereochemical form when explicitly specified; if no form is specified and multiple plausible forms remain, return unresolved with candidates rather than guessing. Stereochemistry: D-glucose, L-glucose, and generic glucose are different entries — match the form specified. Ionization states: charged vs neutral forms have separate IDs — use the biologically relevant form. | `<resolution_and_validation_rules>` (the chemical-form selection mental model) |
| CHV-15 | Obsolete entries: if an entry is marked obsolete, report this and search for the current replacement. | `<resolution_and_validation_rules>` (obsolete handling) |
| CHV-16 | Ambiguous inputs / too-broad queries (e.g. "acid"): return `status: "unresolved"`, include the best-supported match set in `candidates`, and explain ambiguity in `explanation`. | `<resolution_and_validation_rules>` (carries the `ambiguous` contract-test token) |
| CHV-17 | Retry transient API failures once; if it still fails, report the failure and continue defensibly. | `<resolution_and_validation_rules>` + `<stop_rules>` |
| CHV-18 | Domain-specific chemical details belong only inside `resolved_objects` or `candidates[].details`; they must not replace any shared root field. | `<resolution_and_validation_rules>` |

## Lookup workflow (ChEBI endpoints + bounded ordered path)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| CHV-19 | Use `chebi_api_call` with full URLs (must be on the ebi.ac.uk domain); parameters: `url` (full endpoint URL), `method` ("GET" default / "POST"), `headers_json` (optional), `body_json` (optional). | `<lookup_workflow>` (the tool-call shape) |
| CHV-20 | ChEBI endpoint catalog: search chemicals `GET /backend/api/public/es_search/?term={term}` (Elasticsearch-powered, relevance-ranked, case-insensitive partial matching across names/synonyms/InChI/SMILES); compound details `GET /backend/api/public/compound/{chebi_id}/`; parent terms (classification) `GET /backend/api/public/ontology/parents/{chebi_id}/`; child terms (more specific) `GET /backend/api/public/ontology/children/{chebi_id}/`. | `<lookup_workflow>` (the ChEBI endpoint catalog) |
| CHV-21 | Bounded path step 1: search with `es_search` first for all name/synonym/structure/ID-like inputs. | `<lookup_workflow>` (ordered step 1 — invariants file pins this) |
| CHV-22 | Bounded path step 2: for each likely match, call the compound endpoint before returning or evaluating hierarchy. | `<lookup_workflow>` (ordered step 2) |
| CHV-23 | Bounded path step 3: for classification requests, call ontology parents/children only after a specific match is selected. | `<lookup_workflow>` (ordered step 3) |
| CHV-24 | Bounded path step 4: if no entries match a requested expected field, return `status: "unresolved"` and include that field in `missing_expected_fields`; for ambiguous inputs, return unresolved with `candidates` and an ambiguity explanation. | `<lookup_workflow>` (ordered step 4) |
| CHV-25 | Bounded path step 5: if a tool call fails after one retry, stop further nested enrichment for that term and return an `error` lookup attempt. | `<lookup_workflow>` (ordered step 5) + `<stop_rules>` |

## Result contract (ChemicalValidationResult — model-authored shared validator contract)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| CHV-26 | Return only the shared validator statuses: `status: "resolved"` when all requested chemical expected fields are API-confirmed and unambiguous; `status: "unresolved"` otherwise. Populate root fields directly; do not wrap the object under `validation_result`, `result`, or any other property: `status`, `request_id`, `validator_binding_id`, `validator_agent`, `target`, `resolved_values`, `resolved_objects`, `missing_expected_fields`, `candidates`, `lookup_attempts`, `curator_message`, `explanation`. | `<result_contract>` (verbatim status + backticked root-field tokens incl. `` `status` ``; the four request-copy fields collapsed to one line; carries the `Do not wrap` contract-test token) |
| CHV-27 | `resolved_values`: keys must match binding expected-result fields such as `chebi_id` or `name`; omit keys that were not resolved. | `<result_contract>` |
| CHV-28 | `resolved_objects`: API-grounded ChEBI records — use `chebi_id`, `name`, and any available `definition`, `formula`, `inchi`, `smiles`, `classifications`, `synonyms`, obsolete status, and replacement details. | `<result_contract>` |
| CHV-29 | `candidates`: alternate matches for ambiguous or partial lookups — `value` for the CHEBI ID or candidate label, `label` for the chemical name, `object_type: "ChemicalTerm"`, `matched_fields` for matched input fields, `details` for domain facts (formula, synonyms, classifications, stereochemistry notes, obsolete status, API payload IDs). `candidates[].score` is only a normalized 0.0-1.0 confidence; put raw ChEBI/Elasticsearch relevance scores greater than 1.0 in `candidates[].details.raw_score` and leave `score` null. | `<result_contract>` |
| CHV-30 | `lookup_attempts`: one entry per ChEBI API lookup — provider `ebi_chebi`, method names such as `es_search`, `compound`, `ontology.parents`, or `ontology.children`, the exact query payload or URL, `result_count`, outcome `success`/`not_found`/`ambiguous`/`conflict`/`error`. `lookup_attempts[].query` must always be a JSON object, never a bare string; use shapes like `{"term": "estradiol"}`, `{"compound_id": "16469"}`, or `{"url": "..."}`. | `<result_contract>` (carries contract-test tokens `lookup_attempts[].query`, `lookup_attempts[].outcome`, `must always be a JSON object`) |
| CHV-31 | `curator_message`, `explanation`: concise curator-facing decision summary plus which inputs were searched, why the status is resolved or unresolved, and how ambiguity or missing fields were handled. | `<result_contract>` |
| CHV-32 | If no chemical input or expected result field is present, return a minimal resolved result with empty resolved data and explain that no chemical lookup was requested. | `<result_contract>` |
| CHV-33 | Keep validator responsibility separate from extraction: do not return patch actions, patch instructions, or legacy classifications; return only statuses and fields defined here; report true misses through `status: "unresolved"`, `missing_expected_fields`, `lookup_attempts[].outcome` such as `not_found`, and `explanation` (no old top-level summary fields). | `<result_contract>` (the validator-boundary discipline) |

## Stop rules

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| CHV-34 | Stop once you have the ChEBI evidence you need; do not keep searching to improve phrasing. | `<stop_rules>` (VALIDATOR-template stop rule, parallel to gene/allele/disease) |
| CHV-35 | If a tool call fails after one retry, stop further nested enrichment for that term and return an `error` lookup attempt (folds CHV-17/CHV-25). | `<stop_rules>` |
| CHV-36 | If the request needs non-ChEBI knowledge, stop and return only what ChEBI confirms, with a scope note for unsupported portions; do not fabricate, transfer work, or call another specialist. | `<stop_rules>` (merged with CHV-09) |

## CORE-injected (no base restatement)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| CHV-OUT | Output mandate: produce JSON matching `ChemicalValidationResult`; the structured-output layer is authoritative for final response shape. | CORE (`render`). The base keeps the `ChemicalValidationResult` token once and the `Do not wrap` rule but does not restate the JSON-only output mandate. |

> NOTE: there is NO `CHV-RTC` core-injected required-tool-call entry. Unlike the
> `agr_curation_query`-bound validators, `chebi_api_call` has no `required_tool_call`
> metadata, so the core injects no tool-call policy line; the base prompt KEEPS its "call
> `chebi_api_call` before any chemical answer" imperative (CHV-03/CHV-11).

---

## De-dup summary (the chemical-validator Phase-C levers)

1. **CORE de-dup (output only):** the JSON-output mandate (`<role>` "return one root JSON
   object matching `ChemicalValidationResult`" + `<output_format>` "Return only a
   `ChemicalValidationResult` JSON object") is relocated to the locked core (kept as the
   `ChemicalValidationResult` token once + the `Do not wrap` rule + the chemical root-field
   detail).
2. **Required-tool-call NOT de-dupped:** `chebi_api_call` is not core-enforced, so the
   base keeps the tool-call imperative.
3. **Shared Result Contract:** verified NOT injected by a shared layer; KEPT
   (load-bearing), wording tightened, no field dropped.
4. **Consolidation:** `<role>`, `<goal>`, `<success_criteria>`, `<context>`, `<scope>`,
   `<constraints>`, `<evidence_rules>`, `<endpoint_reference>`, `<api_endpoints>`,
   `<tool_usage>`, `<decision_rules>`, `<output_format>`, `<validator_boundaries>`,
   `<stop_rules>` consolidate into the lean skeleton without losing a rule.
5. **NO search-mechanic relocation, NO `match_type` deletion, NO repository URL, NO
   cross-request batch, NO group rules, NO reason codes:** the chemical prompt never
   carried those; `rest.py`/`bindings.yaml`/tool-catalog baseline are untouched.

## Contract-test coverage

**No test assertion is edited, deleted, or weakened by this rewrite.**
`backend/tests/unit/lib/config/test_disease_chemical_validator_result_contract.py`
(`test_disease_and_chemical_prompt_contracts_use_shared_fields`) constrains the chemical
base prompt content: it requires `ChemicalValidationResult` and `chebi_api_call` present,
every REQUIRED_SHARED_FIELD backticked (`status`, `request_id`, `validator_binding_id`,
`validator_agent`, `target`, `resolved_values`, `resolved_objects`,
`missing_expected_fields`, `candidates`, `lookup_attempts`, `curator_message`,
`explanation`), and the fragments `status: "resolved"`, `status: "unresolved"`,
`lookup_attempts[].outcome`, `lookup_attempts[].query`, `must always be a JSON object`,
`missing_expected_fields`, `candidates`, `ambiguous`, `Do not wrap` — all retained
verbatim. It forbids `repair_action`, `no_repair_output`, `status: "under_development"`,
`results: List`, `query_summary:`, `not_found:` (none introduced). All assertions pass
unchanged; the schema-validation tests assert against the `ChemicalValidationResult`
model, not the prompt text, so they are unaffected. No re-baseline was needed.
