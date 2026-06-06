# Phase C semantic-coverage checklist: `reference` validator (Wave 3 — VALIDATOR skeleton)

This is the **authoritative inventory source** for the outcome-first rewrite of
`packages/alliance/agents/reference/prompt.yaml` (canonical agent id
`reference_validation`). Every load-bearing rule in the pre-rewrite prompt is listed here
with a stable ID (RFV-NN) and its new home in the rewritten prompt, OR an explicit,
justified relocation/deletion. The harness inventories
(`phase_c_inventories/reference.txt`, `.invariants.txt`, `.dropped.json`) are derived from
this checklist.

`reference` follows the **VALIDATOR skeleton** the `gene` pilot established and the
`allele`/`agm`/`disease`/`ontology_term`/`subject_entity`/`data_provider` rewrites reused.
It is a clean template application: the pre-rewrite prompt carried NO gene-style
LIKE/exact-then-prefix-then-contains tool-search-order mechanic, NO repository URL, and NO
`mode: "domain_validator_batch"` per-batch protocol, so there is NO search-mechanic
relocation and NO batch inventory.

> **SEARCH-MECHANIC CHECK (incidental, not real) — VERIFIED.** The pre-rewrite
> `<lookup_ladder>` is a **method-selection ordering** (exact-id -> exact-title ->
> fuzzy search), NOT a gene-style "how the tool matches titles/authors/IDs internally"
> mental model the model must replicate:
> - The ladder routes the model to one of two methods by input type:
>   `get_literature_reference` for an exact PMID/DOI/AGRKB/title, then
>   `search_literature_references` for fuzzy title/citation/fragment search. This is the
>   bounded lookup path (which method, when), which belongs in `<lookup_workflow>` — the
>   same class of rule the gene_ontology rewrite kept as "request routing / selection, not
>   a tool-search order".
> - The actual matching (normalized comparison, `_contains_query_context` substring
>   checks against title/citation) lives INSIDE
>   `packages/alliance/python/src/agr_ai_curation_alliance/tools/literature_references.py`
>   and is NOT surfaced to the model as a per-call strategy choice. The tool's own
>   docstring already states "get_literature_reference for exact PMID/DOI/AGRKB lookup or
>   search_literature_references for fuzzy title/citation search", and the bindings.yaml
>   summary already explains the ID-vs-title behavior. There is no exact->prefix->contains
>   ladder the model is told to apply (contrast the gene `search_genes` "Searches using
>   LIKE patterns: exact, prefix, then contains" that gene.dropped.json relocated to the
>   `agr_curation_query` bindings summary).
> - The "match" words in the prompt (`match_type: "ambiguous"`/`"no_match"`/
>   `"upstream_failure"`) are OUTCOME classifications the model returns, not a description
>   of how the search ranks candidates.
>
> So there is **NO** strategy-affecting search mechanic to relocate to the
> `agr_literature_reference_lookup` `@function_tool` docstring + bindings.yaml summary, and
> `literature_references.py`, `bindings.yaml`, and `tool_catalog_baseline.json` are **NOT
> touched** by this rewrite (no baseline regeneration).

> **LEAN skeleton (per Chris).** Each rule is stated ONCE. There is NO standalone
> `<success_criteria>` section: the few genuinely-unique success conditions
> (call-before-report, status decision, record-every-call, copy the API `source`) are
> folded into `<goal>`/`<resolution_and_validation_rules>`.
> `<resolution_and_validation_rules>` carry the unique evidence/structuring rules once
> (call-`agr_literature_reference_lookup`-before-any-fact, no-invention, single-tool/never-
> Elasticsearch discipline, copy-`source`, ambiguity/no-match/upstream handling, domain-
> detail placement). `<lookup_workflow>` owns the ordered lookup ladder, `<result_contract>`
> is a terse field LIST (the shared root fields collapsed to one
> `request_id`/`validator_binding_id`/`validator_agent`/`target` line, plus the
> reference-specific root fields), and `<stop_rules>` keeps only the genuinely-new stops.
> No load-bearing rule was dropped; each ID below maps to a lean home or a justified
> relocation.

Legend for "New home":
- A `<section>` name is a section of the **rewritten** base prompt.
- `CORE` / `render` means the locked Generated Runtime Contract
  (`assembly.py::_build_compact_runtime_contract`) already injects this exact fact; the
  base prompt does NOT restate the core's phrasing. Recorded in `.dropped.json` as
  `relocated -> render`.
- `DELETED` means the rule's fact is dropped with no home; recorded in `.dropped.json` as
  `deleted` (printed for review).

---

## What `reference` actually IS (role + output contract + skeleton choice)

`reference` (canonical agent id `reference_validation`) is a **domain-pack VALIDATOR**, not
an extractor and not a builder. Verified against the code:

- `packages/alliance/agents/reference/agent.yaml` sets
  `output_schema: ReferenceValidationResult` (NOT `null`) and tools
  `[get_agent_contract, agr_literature_reference_lookup]`, with `group_rules_enabled:
  false`. So it is an **envelope-authoring agent** that hand-authors
  `ReferenceValidationResult` directly, exactly like the gene/allele/agm/disease/
  ontology_term/chemical validators author their envelopes — NOT a builder that stages into
  a backend materializer.
- `ReferenceValidationResult` (`packages/alliance/agents/reference/schema.py`) extends
  `DomainValidatorResultBase` and ADDS reference-specific root fields: `reference_id`,
  `curie`, `title`, `short_citation`, `cross_references`, `source`, `match_type`,
  `confidence`, `ambiguity`, `no_match`, `candidate_references`, `failure_classification`.
  These are root fields (not nested under `resolved_objects`) and the contract test
  requires each backticked in the prompt.
- Its job is to **RESOLVE / VALIDATE source-paper references** (PMID/DOI/AGR ref ID/MOD
  ref ID/title/citation) against the Alliance literature collection via the package-owned
  `agr_literature_reference_lookup` tool, and return the **shared validator result
  contract** (`DomainValidatorResultBase` root fields) plus the reference-specific roots.

So the rewrite uses the **role-adapted, outcome-first VALIDATOR skeleton**:
`<role>` -> `<goal>` (success folded in) -> `<scope>` ->
`<resolution_and_validation_rules>` -> `<lookup_workflow>` -> `<result_contract>` ->
`<stop_rules>`.

### VALIDATOR framing (load-bearing, per Chris)

The validator is framed as the **stronger specialized resolver** with the Alliance
literature lookup and a curator-editable prompt — NOT a guardrail policing a "forbidden"
extractor. The base prompt IS curator-editable; it is written in curator voice for a
biologist with no developer background. The reference validator owns the literature lookup,
the exact-vs-fuzzy method choice, the ambiguity/no-match/upstream classification, and the
final reference-identity call — "yours to resolve well, not hand back".

### Required-tool-call: KEPT in base (NOT injected by core) — VERIFIED

`agr_literature_reference_lookup` has **NO** `required_tool_call.enforce` metadata in
`packages/alliance/tools/bindings.yaml` (only `agr_curation_query` carries
`required_tool_call.enforce: true`). The generic resolver
`required_tool_names_for_available_tools(['get_agent_contract',
'agr_literature_reference_lookup'])` therefore returns an EMPTY set, and
`_build_compact_runtime_contract` injects **NO** "Required tool-call policy" line for this
agent. So the base "Call `agr_literature_reference_lookup` before returning any reference
fact" imperative is **NOT** duplicated by the core and is **KEPT** in the rewritten base
prompt (it is the only place it appears). The literal `agr_literature_reference_lookup`
token and the contract-test fragment `Call `agr_literature_reference_lookup` before
returning` must stay (contract-test requirement).

### NO group rules, NO batch protocol, NO search-mechanic relocation (verified)

- `agent.yaml` has `group_rules_enabled: false`, so there is **no group inventory** and no
  `.reason_codes.txt` (validators carry no reason-code enum).
- `agent.yaml`'s `supervisor_routing.batchable: true` lets the supervisor combine reference
  requests, but the editable base never instructed a `mode: "domain_validator_batch"`
  per-batch protocol. The rewrite does NOT invent one (faithful migration).
- The pre-rewrite prompt carried **no** gene-style search-order mechanic; see the
  SEARCH-MECHANIC CHECK above. `literature_references.py`, `bindings.yaml`, and
  `tool_catalog_baseline.json` are untouched.

---

## Template rules applied (Phase C — VALIDATOR template)

### Template rule — no core duplication (de-dup lever: output-schema mandate)

`assembly.py::_build_compact_runtime_contract` already injects, for `reference_validation`:

- the **output contract**: "Output contract from agent.yaml: produce JSON matching
  ReferenceValidationResult; the structured-output layer below is authoritative for final
  response shape." PLUS the CRITICAL structured-output block ("Your final response MUST be
  valid JSON matching the ReferenceValidationResult schema EXACTLY").

The pre-rewrite BASE prompt restated this output mandate (`<role>` "return one root JSON
object matching `ReferenceValidationResult`" + `<output_contract>` "Return only a
`ReferenceValidationResult` JSON object with the shared fields at the root"); the rewrite
removes the JSON-only restatement (de-dup, recorded in `.dropped.json` as
`relocated -> render`), but KEEPS the `ReferenceValidationResult` token once (contract-test
requirement), the reference root-field detail in `<result_contract>` (the core does NOT
enumerate those fields), and the curator-facing "Do not wrap the object under
`validation_result`, `result`, or any other property" rule (contract-test requirement).

### Template rule — Shared Result Contract: **LOAD-BEARING, KEPT (de-dup lever)**

VERIFIED: the shared validator root-field block is NOT injected by any shared prompt layer
— it is the ONLY place these fields are described for this agent. KEPT (wording tightened,
no field dropped). Every shared root field plus the 12 reference-specific roots are
retained. The four request-copy fields (`request_id`/`validator_binding_id`/
`validator_agent`/`target`) collapse to ONE line.

### Template rule — reason_codes: **none (no `.reason_codes.txt`) — confirmed**

Validators do NOT enumerate exclusion reason codes. `ReferenceValidationResult` defines no
reason-code enum bound to the validator output; `lookup_attempts[].outcome` is an outcome
enum and `match_type` is a lookup-path/classification enum, not a fixed exclusion-code enum.
So none is created.

---

## Role / goal / success (folded into goal)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| RFV-01 | Agent identity: the Reference Validation Agent for Alliance Genome Resources; validates source-paper references using only the package-owned `agr_literature_reference_lookup` tool. | `<role>` (reframed to curator-voice "stronger specialized resolver" with the literature lookup + final say on reference identity) |
| RFV-02 | Goal: produce a shared domain-validator result for the supplied `DomainValidationRequest`, with API-backed reference identity, candidates, lookup attempts, missing expected fields, and curator-facing explanations. | `<goal>` (verbatim `DomainValidationRequest` + `ReferenceValidationResult` tokens retained) |
| RFV-03 | Success (folded): call `agr_literature_reference_lookup` before returning any `reference_id`, `curie`, `title`, `short_citation`, `cross_references`, `source`, `match_type`, `confidence`, `ambiguity`, `no_match`, or `candidate_references` (KEPT in base; NOT core-injected). | `<goal>` + `<resolution_and_validation_rules>` (the contract-test fragment "Call `agr_literature_reference_lookup` before returning" is retained verbatim) |
| RFV-04 | Success (folded): return only `status: "resolved"` or `status: "unresolved"`; resolved when all requested reference expected fields are API-confirmed and unambiguous, unresolved otherwise. | `<goal>` + `<result_contract>` (verbatim status tokens) |
| RFV-05 | Success (folded): copy the tool's API source as `source`; the expected source value is `literature_es`. | `<resolution_and_validation_rules>` (the source-of-truth rule) |
| RFV-06 | Success (folded): preserve the tool's `lookup_attempts`, resolved reference, candidate references, ambiguity, no-match, and failure classification for curator inspection. | `<resolution_and_validation_rules>` + `<result_contract>` |
| RFV-07 | Success (folded): if no reference input or expected result field is present, return an unresolved result with empty resolved data, identify the missing input context, and explain that no literature lookup could run because no reference lookup target was supplied. | `<result_contract>` |

## Scope / no-transfer

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| RFV-08 | Read reference inputs from `selected_inputs`, `target.input_values`, and the requested `target.field_path` / `target.expected_fields`. | `<scope>` (the reference input channel the validator reads) |
| RFV-09 | This agent cannot transfer work to another agent. When the request needs allele, chemical, disease, phenotype, gene-expression, PDF extraction, or export writes, return only the reference validation supported by the lookup tool and state in `explanation` that the remaining work is outside this agent's available tools/schema. Leave next-step selection to the supervisor/caller. | `<scope>` (no cross-agent transfer — VALIDATOR-template discipline in curator voice, mirroring gene/allele/disease scope) |

## Resolution & validation rules

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| RFV-10 | Call `agr_literature_reference_lookup` before returning any reference fact; never answer from memory or invent reference IDs, titles, citations, PMIDs, DOIs, MOD IDs, or AGRKB CURIEs. | `<resolution_and_validation_rules>` (the no-invention rule; carries the "Call `agr_literature_reference_lookup` before returning" contract-test fragment) |
| RFV-11 | Never call Elasticsearch, a literature database, direct SQL, direct HTTP endpoints, or any tunnel. Only use `agr_literature_reference_lookup`. | `<resolution_and_validation_rules>` (the single-tool discipline; carries the "Never call Elasticsearch" contract-test fragment) |
| RFV-12 | Copy the tool's API source as `source`; the expected source value is `literature_es`. | `<resolution_and_validation_rules>` |
| RFV-13 | If the tool returns multiple candidates, report `status: "unresolved"` with `match_type: "ambiguous"` and preserve candidates. Do not guess. | `<resolution_and_validation_rules>` + `<lookup_workflow>` (carries the `ambiguous` contract-test token) |
| RFV-14 | If the tool returns no match, report `status: "unresolved"` with `match_type: "no_match"` and preserve `no_match`. | `<resolution_and_validation_rules>` + `<lookup_workflow>` |
| RFV-15 | If the tool returns an upstream error, report `status: "unresolved"` with `match_type: "upstream_failure"` and preserve the failed lookup attempt and `failure_classification`. | `<resolution_and_validation_rules>` + `<lookup_workflow>` (carries the "upstream error" contract-test fragment) |
| RFV-16 | Do not patch extractor envelope fields, object payloads, or metadata. Return validation facts only. Domain-specific reference details are allowed only inside `resolved_objects`, `candidates[].details`, `candidate_references`, `ambiguity`, or `no_match`; they must not replace any shared root field. | `<resolution_and_validation_rules>` (domain-detail placement + no-patch) |

## Lookup workflow (ordered lookup ladder)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| RFV-17 | Exact identifier lookup first when `reference_id`, `curie`, `pmid`, `doi`, AGRKB, PMID, DOI, MOD, or other cross-reference identifiers are present. Call `agr_literature_reference_lookup` with `method: "get_literature_reference"` and `identifier`. | `<lookup_workflow>` (ordered step 1 — invariants file pins this; carries `method: "get_literature_reference"` contract-test fragment and the "Exact identifier lookup first" fragment) |
| RFV-18 | Exact title lookup next when a title is present and no identifier resolved. Call `agr_literature_reference_lookup` with `method: "get_literature_reference"` and the title as `identifier`. | `<lookup_workflow>` (ordered step 2 — carries "Exact title lookup next") |
| RFV-19 | Fuzzy title, short-citation, or abstract/citation-fragment search last when exact lookup did not resolve. Call `agr_literature_reference_lookup` with `method: "search_literature_references"`, `query`, `exact_match: false`, and a bounded `limit`. | `<lookup_workflow>` (ordered step 3 — carries "Fuzzy title, short-citation, or abstract/citation-fragment search last" and `method: "search_literature_references"` contract-test fragments) |
| RFV-20 | If the tool returns multiple candidates, report `status: "unresolved"` with `match_type: "ambiguous"` and preserve candidates; if no match, `match_type: "no_match"` with `no_match`; if upstream error, `match_type: "upstream_failure"` preserving the failed attempt and `failure_classification`. | `<lookup_workflow>` (ordered step 4 — outcome handling folding RFV-13/14/15) |

## Result contract (ReferenceValidationResult — model-authored shared validator contract)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| RFV-21 | Return only the shared validator statuses: `status: "resolved"` when all requested reference expected fields are API-confirmed and unambiguous; `status: "unresolved"` otherwise. Populate root fields directly; do not wrap the object under `validation_result`, `result`, or any other property: `status`, `request_id`, `validator_binding_id`, `validator_agent`, `target`, `resolved_values`, `resolved_objects`, `missing_expected_fields`, `candidates`, `lookup_attempts`, `curator_message`, `explanation`. | `<result_contract>` (verbatim status + backticked root-field tokens incl. `` `status` ``; the four request-copy fields collapsed to one line; carries the `Do not wrap` contract-test token) |
| RFV-22 | `resolved_values`: keys must match binding expected-result fields such as `reference_id`, `curie`, or `title`; omit keys that were not resolved. | `<result_contract>` |
| RFV-23 | `resolved_objects`: the resolved reference object returned by the tool — include `reference_id`, `curie`, `title`, `short_citation`, `cross_references`, `source`, and `obsolete` when present. | `<result_contract>` |
| RFV-24 | `candidates`: alternate matches for ambiguous or partial lookups — `value` for the reference CURIE or best identifier, `label` for the title or short citation, `object_type: "Reference"`, `matched_fields` for matched identifier/title/citation fields, and `details` for the full candidate reference. | `<result_contract>` |
| RFV-25 | `lookup_attempts`: one entry per tool call — provider `agr_literature_reference_lookup`, method names `get_literature_reference` or `search_literature_references`, the exact query payload, `result_count`, and `lookup_attempts[].outcome` of `"success"`, `"not_found"`, `"ambiguous"`, `"conflict"`, or `"error"`. | `<result_contract>` (carries `lookup_attempts[].outcome` contract-test fragment) |
| RFV-26 | `curator_message`, `explanation`: concise curator-facing decision summary plus which inputs were searched, why the status is resolved or unresolved, and how ambiguity or missing fields were handled. | `<result_contract>` |
| RFV-27 | Reference-specific root fields: set `reference_id`, `curie`, `title`, `short_citation`, `cross_references`, `source`, `match_type`, `confidence`, `ambiguity`, `no_match`, `candidate_references`, and `failure_classification` only from the tool result. | `<result_contract>` (carries each reference-specific backticked field — contract-test requirement) |
| RFV-28 | Keep validator responsibility separate from extraction: do not return patch actions, patch instructions, or legacy classifications; return only statuses and fields defined here; report true misses through `status: "unresolved"`, `missing_expected_fields`, `lookup_attempts[].outcome` such as `not_found`, and `explanation` (no old top-level summary fields). | `<result_contract>` (the validator-boundary discipline) |

## Stop rules

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| RFV-29 | If no candidate matches for a requested expected field, return `status: "unresolved"`, include that field in `missing_expected_fields`, and use a curator message such as "No literature reference matched the supplied identifier or title." | `<stop_rules>` (carries the "No literature reference matched" contract-test fragment) |
| RFV-30 | If the request is ambiguous after lookup, return `status: "unresolved"` with the verified candidate set rather than guessing. | `<stop_rules>` |
| RFV-31 | If tool access fails after one retry, return `status: "unresolved"` with an `error` lookup attempt and a curator-safe message. | `<stop_rules>` |
| RFV-32 | If data is outside this agent's scope, do not fabricate, transfer the request, or ask for another step; return only supported in-scope reference validation. | `<stop_rules>` (merged with RFV-09) |

## CORE-injected (no base restatement)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| RFV-OUT | Output mandate: produce JSON matching `ReferenceValidationResult`; the structured-output layer is authoritative for final response shape. | CORE (`render`). The base keeps the `ReferenceValidationResult` token once and the `Do not wrap` rule but does not restate the JSON-only output mandate. |

> NOTE: there is NO `RFV-RTC` core-injected required-tool-call entry. Unlike the
> `agr_curation_query`-bound validators, `agr_literature_reference_lookup` has no
> `required_tool_call` metadata, so the core injects no tool-call policy line; the base
> prompt KEEPS its "Call `agr_literature_reference_lookup` before returning" imperative
> (RFV-03/RFV-10).

---

## De-dup summary (the reference-validator Phase-C levers)

1. **CORE de-dup (output only):** the JSON-output mandate (`<role>` "return one root JSON
   object matching `ReferenceValidationResult`" + `<output_contract>` "Return only a
   `ReferenceValidationResult` JSON object") is relocated to the locked core (kept as the
   `ReferenceValidationResult` token once + the `Do not wrap` rule + the reference
   root-field detail).
2. **Required-tool-call NOT de-dupped:** `agr_literature_reference_lookup` is not
   core-enforced, so the base keeps the tool-call imperative.
3. **Shared Result Contract:** verified NOT injected by a shared layer; KEPT
   (load-bearing), wording tightened, no field dropped.
4. **Consolidation:** `<role>`, `<goal>`, `<success_criteria>`, `<lookup_ladder>`,
   `<evidence_rules>`, `<output_contract>`, `<scope_boundary>`, `<validator_boundaries>`,
   `<stopping_rules>` consolidate into the lean skeleton without losing a rule.
5. **NO search-mechanic relocation, NO repository URL, NO cross-request batch, NO group
   rules, NO reason codes:** the reference prompt's lookup ladder is method-selection
   ordering (kept in `<lookup_workflow>`), not a tool-internal search mechanic;
   `literature_references.py`/`bindings.yaml`/tool-catalog baseline are untouched.

## Contract-test coverage

**No test assertion is edited, deleted, or weakened by this rewrite.**
`backend/tests/unit/lib/config/test_reference_validator_result_contract.py`
(`test_reference_prompt_contract_uses_tool_before_deciding`) constrains the reference base
prompt content: it requires `ReferenceValidationResult` and `agr_literature_reference_lookup`
present, every REQUIRED_SHARED_FIELD and every REFERENCE_SPECIFIC_FIELD backticked
(`status`, `request_id`, `validator_binding_id`, `validator_agent`, `target`,
`resolved_values`, `resolved_objects`, `missing_expected_fields`, `candidates`,
`lookup_attempts`, `curator_message`, `explanation`, `reference_id`, `curie`, `title`,
`short_citation`, `cross_references`, `source`, `match_type`, `confidence`, `ambiguity`,
`no_match`, `candidate_references`, `failure_classification`), and the fragments
`Exact identifier lookup first`, `Exact title lookup next`, `Fuzzy title, short-citation, or
abstract/citation-fragment search last`, `Call `agr_literature_reference_lookup` before
returning`, `method: "get_literature_reference"`, `method: "search_literature_references"`,
`status: "resolved"`, `status: "unresolved"`, `lookup_attempts[].outcome`,
`missing_expected_fields`, `candidates`, `ambiguous`, `No literature reference matched`,
`upstream error`, `Do not wrap`, `Never call Elasticsearch` — all retained verbatim. It
forbids `repair_action`, `no_repair_output`, `status: "under_development"`, `results: List`,
`query_summary:`, `not_found:` (none introduced). All assertions pass unchanged; the
schema-validation tests assert against the `ReferenceValidationResult` model, not the prompt
text, so they are unaffected. No re-baseline was needed.
