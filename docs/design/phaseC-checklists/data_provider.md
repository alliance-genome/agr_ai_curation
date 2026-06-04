# Phase C semantic-coverage checklist: `data_provider` validator (Wave 3 — VALIDATOR skeleton)

This is the **authoritative inventory source** for the outcome-first rewrite of
`packages/alliance/agents/data_provider/prompt.yaml` (canonical agent id
`data_provider_validation`). Every load-bearing rule in the pre-rewrite prompt is
listed here with a stable ID (DPV-NN) and its new home in the rewritten prompt, OR
an explicit, justified relocation/deletion. The harness inventories
(`phase_c_inventories/data_provider.txt`, `.invariants.txt`, `.dropped.json`) are
derived from this checklist.

`data_provider` follows the **VALIDATOR skeleton** the `gene` pilot established and
the `allele`/`agm`/`subject_entity` rewrites reused. It is a clean template
application: the pre-rewrite prompt carried NO `# Available Methods` LIKE/
case-insensitive/`match_type` mechanics block, NO repository URL, and NO batch
protocol in the editable base, so there is NO search-mechanic relocation, NO
`match_type` deletion, and NO batch inventory.

> **No real search-mechanic (likepat is incidental).** The pre-rewrite prompt's
> `match`-like words are all incidental: "every supplied input **matches** the
> provider facts", "no-**match** explanation", and the result-field tokens
> `match_type`, `matched_value`, `taxon_matches`, `data_provider_candidates[].match_type`.
> There is NO LIKE / case-insensitive / prefix / contains search-order mechanic to
> relocate to the `agr_curation_query` docstring + bindings summary. So
> `agr_curation.py`, `bindings.yaml`, and `tool_catalog_baseline.json` are NOT
> touched by this rewrite.

> **LEAN skeleton (per Chris).** Each rule is stated ONCE. There is NO standalone
> `<success_criteria>` section: the genuinely-unique success conditions
> (call-before-report, the resolve-only-when-all-inputs-match status decision,
> record-every-call, no-guess) are folded into `<goal>` and the
> `<resolution_and_validation_rules>`. The pre-rewrite prompt had NO role framing,
> NO scope/no-transfer block, and NO stop rules; the lean rewrite adds the
> VALIDATOR skeleton's role + scope-no-transfer + stop-rules in curator voice
> (modest growth, the agm pattern), while folding the pre-rewrite `# Goal`,
> `# Supported Request Inputs`, `# Tool Requirement`, `# Lookup Policy`,
> `# Shared Result Contract`, and `# Output Rules` into the lean sections without
> losing a rule. No load-bearing rule was dropped; each ID below maps to a lean
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

## What `data_provider` actually IS (role + output contract + skeleton choice)

`data_provider` (canonical agent id `data_provider_validation`) is a **domain-pack
VALIDATOR**, not an extractor and not a builder. Verified against the code:

- `packages/alliance/agents/data_provider/agent.yaml` sets
  `output_schema: DataProviderValidationResult` (NOT `null`) and tools
  `[get_agent_contract, agr_curation_query]`, with `group_rules_enabled: false`. So
  it is an **envelope-authoring agent** that hand-authors
  `DataProviderValidationResult` directly, exactly like the gene/allele/agm
  validators author their envelopes — NOT a builder that stages into a backend
  materializer.
- Therefore the **builder metadata-template rule does NOT apply** here: there is no
  `stage_*`/`finalize_*` workflow and no `<validator_handoff>` to write (a validator
  IS the handoff target). The model fills the `DataProviderValidationResult` root
  fields itself, including the provider-specific `data_provider_candidates` and
  `mismatch_explanations`.
- Its job is to **VALIDATE an Alliance data-provider abbreviation and its
  provider-to-taxon (and provider-to-name) consistency** against the AGR curation DB
  via `agr_curation_query` (the data-provider helpers), and return the **shared
  validator result contract** (`DomainValidatorResultBase` root fields) plus
  provider-specific candidate detail.

So the rewrite uses the **role-adapted, outcome-first VALIDATOR skeleton**:

`<role>` -> `<goal>` -> `<scope>` -> `<resolution_and_validation_rules>` ->
`<lookup_workflow>` -> `<result_contract>` -> `<stop_rules>`. (No standalone
`<success_criteria>`; its conditions are folded into `<goal>` /
`<resolution_and_validation_rules>`.)

### VALIDATOR framing (load-bearing, per Chris)

The validator is framed as the **stronger specialized resolver** with the provider
lookups and the final say on provider/taxon consistency — NOT a guardrail policing
a "forbidden" extractor. The base prompt IS curator-editable; it is written in
curator voice for a biologist with no developer background ("yours to resolve well,
not hand back"). The pre-rewrite "Role:" line was a bare one-liner with no
resolver framing; the rewrite gives it the positive curator-voice framing.

### NO group rules, NO batch protocol, NO search-mechanic relocation (verified)

- `agent.yaml` has `group_rules_enabled: false`, so there is **no group inventory**.
- The editable base carried **no** per-batch protocol (`supervisor_routing.batchable:
  true` lets the supervisor combine providers, but the base never instructed a batch
  loop). The rewrite does NOT invent one.
- No `# Available Methods` LIKE/exact/prefix/contains search-order or `match_type`
  mechanic block existed (the `match`-words are incidental — see top note). So there
  is NO strategy-affecting search-mechanic to relocate and NO `match_type` deletion.

---

## Template rules applied (Phase C — VALIDATOR template)

### Template rule — builder metadata exclude=don't-stage: **N/A (verified)**

Does not apply: `data_provider_validation` authors `DataProviderValidationResult`
directly. The model EXPRESSES an unresolved outcome by writing `status: "unresolved"`
+ `missing_expected_fields` + `candidates`/`data_provider_candidates` +
`mismatch_explanations` — real top-level, model-authored channels.

### Template rule — no core duplication (de-dup lever 1: required-tool-call + output)

`assembly.py::_build_compact_runtime_contract` already injects, for
`data_provider_validation` (verified by rendering `build_agent_core_prompt`):

- the **required-tool-call policy**: "Required tool-call policy: call at least one
  of agr_curation_query before final output.";
- the **output contract**: "Output contract from agent.yaml: produce JSON matching
  DataProviderValidationResult; the structured-output layer below is authoritative
  for final response shape." PLUS the CRITICAL structured-output block ("Your final
  response MUST be valid JSON matching the DataProviderValidationResult schema
  EXACTLY");
- the **get_agent_contract** pointer.

The pre-rewrite BASE prompt restated the required-tool-call imperative; the rewrite
removes it (de-dup, recorded in `.dropped.json` as `relocated -> render`), but KEEPS
the curator-facing curation rule once. Specifically:

- "You MUST call `agr_curation_query` before returning any data provider validation
  result." (`# Tool Requirement`) -> the bare imperative de-dups to CORE's
  required-tool-call policy; the curator-facing call-before-report rule is folded
  into `<goal>` once ("call it before reporting any provider fact"), and the literal
  `agr_curation_query` token stays in the prompt.
- The pre-rewrite `# Goal` carried the "return structured `DataProviderValidationResult`
  data" / output mandate that the locked core injects. The rewrite no longer restates
  the JSON-only output mandate; the core owns it. The `DataProviderValidationResult`
  token is named once in `<goal>`/`<result_contract>` for curator readability, and the
  provider root-field detail is KEPT because the core does not enumerate it.

### Template rule — Shared Result Contract: **LOAD-BEARING, KEPT (de-dup lever 2)**

VERIFIED: the `# Shared Result Contract` block is NOT injected by any shared prompt
layer — it is the ONLY place these fields are described for this agent. It is
therefore **load-bearing and KEPT** (wording tightened, no field dropped). Every
shared root field plus the provider-specific `data_provider_candidates` and
`mismatch_explanations` roots are retained. The contract test
`test_data_provider_validation_agent.py` asserts the bound methods, the
`abbreviation`/`provider_name`/`taxon` tokens, `provider/taxon mismatch`, the status
tokens, `lookup_attempts`, and `mismatch_explanations` — all retained verbatim.

### Template rule — reason_codes: **none (no `.reason_codes.txt`) — confirmed**

Validators do NOT enumerate exclusion reason codes. `DataProviderValidationResult`
defines no reason-code enum bound to the validator output;
`lookup_attempts[].outcome` is an outcome enum and `mismatch_explanations` is a free
model-written list. So none is created.

---

## Role / goal / success (success conditions folded into goal + rules)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| DPV-01 | Agent identity: a Data Provider Validation Specialist for Alliance Genome Resources curation; validate Organization/data_provider fields by querying package-owned Alliance data-provider helpers. | `<role>` (reframed to curator-voice "stronger specialized resolver"; provider/taxon-consistency purpose retained) |
| DPV-02 | Goal: validate data-provider fields from a `DomainValidationRequest`; return structured `DataProviderValidationResult` data using the shared validator result contract; resolve only from tool evidence. | `<goal>` (verbatim `DomainValidationRequest` + `DataProviderValidationResult` tokens retained) |
| DPV-03 | No-guessing rule: do not guess provider abbreviations, taxon IDs, display names, or provider/taxon relationships. | `<goal>` + `<resolution_and_validation_rules>` (the no-guess invariant) |
| DPV-04 | Success: call `agr_curation_query` before returning any data provider validation result (the machine imperative is CORE DPV-RTC). | `<goal>` (curator-facing call-before-report line KEPT once; literal `agr_curation_query` retained) |
| DPV-05 | Success: resolved only when every supplied input matches the provider facts the tool returns; status `resolved` / `unresolved`. | `<goal>` + `<resolution_and_validation_rules>` + `<result_contract>` (verbatim status tokens; the resolve-only-when-all-match policy) |
| DPV-06 | Success: record every database call in `lookup_attempts`. | `<goal>` + `<result_contract>` (DPV-19) |

## Scope / supported inputs / no-transfer

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| DPV-07 | Supported inputs read from `selected_inputs` and `target.input_values` when present: `abbreviation` (provider abbreviation such as `WB`, `FB`, `MGI`, `ZFIN`, `RGD`, `SGD`, `HGNC`), `provider_name` (optional display name), `taxon` (optional NCBITaxon CURIE), `taxon_id` (optional NCBITaxon CURIE — treat as equivalent to `taxon`). | `<scope>` (the provider `selected_inputs` contract; field list + the MOD-code abbreviations retained as the handoff channel) |
| DPV-08 | No cross-agent transfer (VALIDATOR-template discipline): for a request outside this scope, do not transfer work, invoke another agent, or perform another agent's task; state the scope limit, preserve any in-scope provider lookup, and leave next-step selection to the supervisor/caller. | `<scope>` (added in curator voice, mirroring gene/allele/agm scope; the pre-rewrite prompt had no explicit no-transfer block) |

## Resolution & validation rules (no-memory/no-guess, all-inputs-match, no-guess-on-ambiguity)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| DPV-09 | Resolve only from tool evidence; do not answer from memory or guess an abbreviation, taxon ID, display name, or provider/taxon relationship not obtained from a lookup. | `<resolution_and_validation_rules>` (no-memory/no-guess invariant) |
| DPV-10 | A provider resolves only when every supplied input matches the provider facts the tool returns; a provider/taxon or provider/name conflict is unresolved. | `<resolution_and_validation_rules>` (the consistency principle; the concrete per-step decisions live in the workflow as DPV-13..DPV-14) |
| DPV-11 | No-guessing on ambiguity: when more than one plausible provider candidate remains, do not choose by order, popularity, species assumptions, or training-data knowledge; preserve the candidates and return unresolved. | `<resolution_and_validation_rules>` (verbatim "by order, popularity, species assumptions, or training-data knowledge") + `<lookup_workflow>` step 5 (the procedural outcome) |

## Lookup workflow (two methods + bounded ordered path)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| DPV-12 | Available methods (which-method-when): `get_data_provider` (exact provider lookup; supply `abbreviation`, `provider_name`, and/or `taxon_id`; use this first); `get_data_providers` (provider list helper; use only when the exact lookup cannot run or to explain valid alternatives). | `<lookup_workflow>` (method catalog; verbatim `get_data_provider`, `get_data_providers` — contract-test tokens) |
| DPV-13a | Bounded path step 1 — call `get_data_provider` with the supplied `abbreviation`, `provider_name`, and taxon context (`taxon_id`). | `<lookup_workflow>` (ordered step 1 — invariants file pins this order) |
| DPV-13b | Step 2 — treat a single provider as resolved only when every supplied input matches the provider facts returned by the tool. | `<lookup_workflow>` (ordered step 2) |
| DPV-13c | Step 3 — abbreviation resolves but supplied taxon differs from provider taxon: return `status: "unresolved"`, preserve the provider in `candidates` and `data_provider_candidates`, include the lookup attempt, and explain the provider/taxon mismatch in `curator_message`, `explanation`, and `mismatch_explanations`. | `<lookup_workflow>` (ordered step 3; carries the `provider/taxon mismatch` contract-test token) |
| DPV-13d | Step 4 — provider name conflicts with the abbreviation: return unresolved with all candidate facts and a curator-facing mismatch explanation. | `<lookup_workflow>` (ordered step 4) |
| DPV-13e | Step 5 — multiple plausible candidates remain: return `status: "unresolved"` and preserve the candidates (no-guessing rule DPV-11 applies). | `<lookup_workflow>` (ordered step 5) |
| DPV-13f | Step 6 — tool reports under-development or unavailable helper behavior: return unresolved with a lookup attempt outcome of `error` and a curator-facing message that provider validation could not run in this runtime. | `<lookup_workflow>` (ordered step 6) |
| DPV-13g | Step 7 — tool returns zero exact matches: return unresolved with `lookup_attempts`, `missing_expected_fields`, provider candidates when returned, and a curator-facing no-match explanation. | `<lookup_workflow>` (ordered step 7) |

## Result contract (DataProviderValidationResult — model-authored shared validator contract)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| DPV-14 | Return only the shared validator statuses: `status: "resolved"` when lookup evidence resolves the target and all derivable expected fields are present; `status: "unresolved"` when the provider is not found, ambiguous, conflicts with supplied taxon/name context, lacks required input, has missing expected fields, or cannot be checked because the tool fails. Populate these root fields directly; do not wrap them under another object: `request_id`, `validator_binding_id`, `validator_agent`, `target`, `resolved_values`, `resolved_objects`, `missing_expected_fields`, `candidates`, `lookup_attempts`, `curator_message`, `explanation`, `data_provider_candidates`, `mismatch_explanations`. | `<result_contract>` (verbatim status + backticked root-field tokens, incl. provider-specific `data_provider_candidates` and `mismatch_explanations` [contract-test tokens]) |
| DPV-15 | Resolved providers: copy expected scalar outputs into `resolved_values` using the binding's `expected_result_fields` keys, such as `abbreviation`, `taxon`, `taxon_id`, or `display_name` — e.g. if the binding expects `abbreviation` and `taxon`, return those keys exactly. | `<result_contract>` |
| DPV-16 | `candidates`: generic candidate records for ambiguous, alternate, unknown, or conflicting providers (`value`, `label`, `object_type`, `matched_fields`, `details`). | `<result_contract>` |
| DPV-17 | `data_provider_candidates`: provider-specific candidate detail (`abbreviation`, `taxon_id`, `display_name`, `species`, `match_type`, `matched_value`, `taxon_matches`, `mismatch_explanation`, and lookup context). `mismatch_explanations`: provider/taxon or provider/name mismatch explanations; leave empty for clean resolutions and simple no-match results. | `<result_contract>` (provider-specific root detail retained) |
| DPV-18 | Unresolved providers: keep `resolved_values` empty or partial, list all unfilled expected keys in `missing_expected_fields`, and write a curator-facing `curator_message` that says whether the issue was no match, ambiguity, provider/taxon mismatch, provider/name mismatch, missing input, or a tool error. | `<result_contract>` |
| DPV-19 | `lookup_attempts`: one record per `agr_curation_query` call (provider `agr_curation_query`, method name, query payload, `result_count`, outcome `success`/`not_found`/`ambiguous`/`conflict`/`error`). | `<result_contract>` |
| DPV-20 | Do not use metadata-only validator states as result statuses; envelope composition belongs outside validator results. | `<result_contract>` (verbatim — the no-metadata-status discipline) |

## Stop rules

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| DPV-21 | Stop once you have the database evidence you need; do not keep searching to improve phrasing. | `<stop_rules>` (VALIDATOR-template stop rule added in curator voice; the pre-rewrite prompt had no stop rules) |
| DPV-22 | If the request stays ambiguous after lookup, return the verified candidates rather than guessing. | `<stop_rules>` (folds DPV-11 outcome) |
| DPV-23 | If data is outside this agent's scope, do not fabricate, transfer work, or call another specialist; state the scope limit and return only supported in-scope provider results. | `<stop_rules>` (merged with DPV-08) |

## CORE-injected (no base restatement)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| DPV-RTC | Required tool-call policy: call at least one of `agr_curation_query` before final output. | CORE (`render`). The base keeps the curator-facing call-before-report line (DPV-04) but does not restate the machine imperative. |
| DPV-OUT | Output mandate: produce JSON matching `DataProviderValidationResult`; the structured-output layer is authoritative for final response shape. | CORE (`render`). The base names the `DataProviderValidationResult` token once and keeps the root-field detail the core does not enumerate. |

---

## De-dup summary (the data_provider-validator Phase-C levers)

1. **CORE de-dup:** the required-tool-call imperative (`# Tool Requirement` "You
   MUST call `agr_curation_query` …") and the JSON-output mandate (the `# Goal`
   "return structured `DataProviderValidationResult` data" output-schema
   restatement) are relocated to the locked core (kept as one curator-facing
   call-before-report line + the `DataProviderValidationResult` token once). The
   provider root-field detail is KEPT because the core does not enumerate it.
2. **Shared Result Contract:** verified NOT injected by a shared layer; KEPT
   (load-bearing), wording tightened, no field dropped.
3. **Consolidation:** `# Goal`, `# Supported Request Inputs`, `# Tool Requirement`,
   `# Lookup Policy`, `# Shared Result Contract`, and `# Output Rules` consolidate
   into the lean skeleton (`<goal>` folds success + no-guess; `<scope>` folds inputs
   + the new no-transfer block; `<resolution_and_validation_rules>` folds the
   evidence/consistency/ambiguity rules; `<lookup_workflow>` folds the method
   catalog + the ordered 7-step path; `<result_contract>` folds the shared contract
   + output rules) without losing a rule. The lean rewrite ADDS the
   VALIDATOR-skeleton role + no-transfer + stop-rules the terse pre-rewrite prompt
   lacked (the agm growth pattern).
4. **NO search-mechanic relocation, NO `match_type` deletion, NO repository URL,
   NO batch, NO group rules:** the data_provider prompt never carried those, so none
   is added or relocated. `agr_curation.py` / `bindings.yaml` /
   `tool_catalog_baseline.json` are untouched.

## Contract-test coverage

**No test assertion is edited, deleted, or weakened by this rewrite.** Two contract
tests constrain the data_provider base-prompt content and both pass unchanged:

- `backend/tests/unit/lib/config/test_data_provider_validation_agent.py`
  `::test_data_provider_prompt_and_tool_grant_agree_on_available_methods` requires
  every bound method (`get_data_provider`, `get_data_providers`) backticked, plus the
  fragments `` `abbreviation` ``, `` `provider_name` ``, `` `taxon` ``,
  `provider/taxon mismatch`, `status: "resolved"`, `status: "unresolved"`,
  `lookup_attempts`, and `mismatch_explanations` — all retained — and forbids
  `repair_action` (never introduced).
- `backend/tests/unit/api/test_agent_studio_domain_envelope_prompt_policy.py`
  scans `packages/*/agents/*/prompt.yaml` for legacy planned/blocked/opt-out/repair
  wording; the lean rewrite introduces none.

No re-baseline was needed (no test phrase moved out of the prompt text).
