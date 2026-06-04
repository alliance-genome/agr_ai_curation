# Phase C semantic-coverage checklist: `allele` validator (Wave 3 — VALIDATOR skeleton)

This is the **authoritative inventory source** for the outcome-first rewrite of
`packages/alliance/agents/allele/prompt.yaml` (canonical agent id
`allele_validation`). Every load-bearing rule in the pre-rewrite prompt is listed
here with a stable ID (AV-NN) and its new home in the rewritten prompt, OR an
explicit, justified relocation/deletion. The harness inventories
(`phase_c_inventories/allele.txt`, `.mgi.txt`, `.invariants.txt`, `.dropped.json`)
are derived from this checklist.

`allele` follows the **VALIDATOR skeleton** the `gene` pilot established
(`docs/design/phaseC-checklists/gene.md`). It is the second validator rewrite; it
reuses the pilot's de-dup decisions verbatim where they apply (core de-dup,
search-mechanic relocation, shared-result-contract retention).

Legend for "New home":
- A `<section>` name is a section of the **rewritten** base prompt.
- `CORE` / `render` means the locked Generated Runtime Contract
  (`assembly.py::_build_compact_runtime_contract`) already injects this exact fact;
  the base prompt does NOT restate the core's phrasing. Recorded in
  `.dropped.json` as `relocated -> render`.
- `RELOCATED -> <home>` / `DELETED` mean the rule's fact moves elsewhere on the
  production path (a `bindings.yaml` tool description) or is dropped with no home;
  recorded in `.dropped.json` as `relocated` (machine-checked home) / `deleted`.

---

## What `allele` actually IS (role + output contract + skeleton choice)

`allele` (canonical agent id `allele_validation`) is a **domain-pack VALIDATOR**,
not an extractor and not a builder. Verified against the code:

- `packages/alliance/agents/allele/agent.yaml` sets
  `output_schema: AlleleResultEnvelope` (NOT `null`) and tools
  `[get_agent_contract, agr_curation_query]`. So it is an **envelope-authoring
  agent** that hand-authors `AlleleResultEnvelope` directly, exactly like the gene
  validator authors `GeneResultEnvelope` — NOT a builder that stages into a backend
  materializer.
- Therefore the **builder metadata-template rule does NOT apply** here: there is no
  `stage_*`/`finalize_*` workflow, no "you never write the envelope / backend
  materializes metadata", no exclude=don't-stage rewrite, and no
  `<validator_handoff>` to write (a validator IS the handoff target). The model
  fills the `AlleleResultEnvelope` root fields itself.
- Its job is to **RESOLVE / VALIDATE an allele identity** against the AGR curation
  DB via `agr_curation_query`, and return the **shared validator result contract**
  (`DomainValidatorResultBase` root fields) plus allele-specific candidate detail.

So the rewrite uses the **role-adapted, outcome-first VALIDATOR skeleton**:

`<role>` -> `<goal>` -> `<success_criteria>` -> `<scope>` ->
`<resolution_and_validation_rules>` -> `<lookup_workflow>` -> `<result_contract>` ->
`<stop_rules>`. The outcome-first ORDER (Role -> Goal -> Success -> Scope -> Rules
-> Workflow -> Output -> Stop) is preserved.

### VALIDATOR framing (load-bearing, per Chris)

The validator is framed as the **stronger specialized resolver** with deeper DB
access and a curator-editable prompt — NOT a guardrail policing a "forbidden"
extractor. The base prompt IS curator-editable; it is written in curator voice for
a biologist with no developer background. (Positive, capable framing: the validator
owns the database lookup, the disambiguation, and the final allele-identity call —
"yours to resolve well, not hand back".)

### Batch mode: **N/A — allele has NO base-prompt batch protocol (verified)**

Unlike the gene validator, the allele BASE prompt carries **no** batch /
bulk-grouping protocol: no `mode: "domain_validator_batch"`, no
`search_alleles_bulk` instruction, no "group compatible requests" rule. The
`search_alleles_bulk` method DOES exist in the tool and in `bindings.yaml`
`methods.search_alleles_bulk`, and `agent.yaml`'s `supervisor_routing.batchable:
true` allows the supervisor to combine allele requests, but the editable base
prompt never instructed a per-batch bulk-grouping protocol. The rewrite does NOT
invent one (that would be a new rule, not a faithful migration). No batch inventory
phrases, no batch invariant, no `search_alleles_bulk` token is required in the base
prompt.

---

## Template rules applied (Phase C — VALIDATOR template)

### Template rule — builder metadata exclude=don't-stage: **N/A (verified)**

Does not apply: `allele_validation` authors `AlleleResultEnvelope` directly (see
above). There is no `metadata.*` materializer and no stage tool. The model
EXPRESSES an unresolved outcome by writing `status: "unresolved"` +
`missing_expected_fields` + `candidates`/`allele_candidates` — real top-level,
model-authored channels. Preserve that mechanism; do not import the builder
don't-stage rewrite.

### Template rule — no core duplication (de-dup lever 1: required-tool-call + output)

`assembly.py::_build_compact_runtime_contract` already injects, for
`allele_validation` (verified by rendering `build_agent_core_prompt('allele_validation')`):

- the **required-tool-call policy**: "Required tool-call policy: call at least one of
  agr_curation_query before final output.";
- the **output contract**: "Output contract from agent.yaml: produce JSON matching
  AlleleResultEnvelope; the structured-output layer below is authoritative for final
  response shape." PLUS the CRITICAL structured-output block ("you MUST produce the
  AlleleResultEnvelope structured output", "must be valid JSON matching the
  AlleleResultEnvelope schema EXACTLY");
- the **get_agent_contract** pointer for detailed field/tool/schema/validator facts.

The pre-rewrite BASE prompt restated some of these; the rewrite removes the
restatements (de-dup, recorded in `.dropped.json` as `relocated -> render`), but
KEEPS the curator-facing curation rule once. Specifically:

- "You MUST call the `agr_curation_query` tool before providing any response about
  alleles." (`## Tool Requirement`) -> de-dup to CORE's required-tool-call policy.
  The curator-facing success line "Calls `agr_curation_query` before providing
  allele information" is KEPT once, and the literal token `` `agr_curation_query` ``
  stays in the prompt (a shared-validator-contract-test requirement — AV-RC below).
- "Structured output is enforced as `AlleleResultEnvelope`. Keep prose concise..."
  (`# Output` opener) -> de-dup to CORE's structured-output block, which is
  authoritative for the response shape. The allele-specific FIELD detail of
  `AlleleResultEnvelope` (allele_id/symbol/species/data_provider + optional fields)
  is KEPT (AV-33/AV-34), because the core does not enumerate those fields.

### Template rule — tool MECHANICS vs curation JUDGMENT (de-dup lever 1, "Available Allele Methods")

The pre-rewrite `# Available Allele Methods` block restated pure
`agr_curation_query` tool MECHANICS that the curator tool catalog
(`bindings.yaml` `agr_curation_query` `methods.*`) already carries: method names,
required/optional param lists, and per-method one-line behavior ("Find alleles whose
symbol contains the text you give" / "exactly matches"). The rewrite removes the
restated mechanics and KEEPS the curation JUDGMENT (which method to choose when,
the literal-symbol-first rule, the keep-evidence-out-of-the-query rule).

**Verified — what bindings.yaml carries** (the harness home-check for
`bindings:agr_curation_query` searches the tool's top-level `description` +
`metadata.documentation.summary`):

- bindings `methods.search_alleles.description`: "Find alleles whose symbol contains
  the text you give." (carries the contains/partial-match semantics, curator-level)
- bindings `methods.get_allele_by_exact_symbol.description`: "Find the allele whose
  official symbol exactly matches." (carries exact-match semantics)
- bindings `methods.search_alleles_bulk.description`: "Search for many allele symbols
  at once in a single call." (carries the bulk semantics)
- bindings `methods.get_allele_by_id.description`: "Get full details for one allele
  by its ID."

So the **per-method MECHANICS** are carried by the curator catalog and are
relocated. The remaining low-level mechanics the catalog did NOT originally carry
split two ways (the SAME decision as gene):

- **Search-order + case-insensitive mechanic — RELOCATED.** "Searches using LIKE
  patterns: exact, prefix, then contains" and "Is case-insensitive" are
  **STRATEGY-AFFECTING**, not dead implementation detail: the surviving
  `<lookup_workflow>` step "narrow by adding characters" depends on the model knowing
  why a short query like `Ulk1` returns many candidates (the exact->prefix->contains
  model). So this mechanic is **RELOCATED** to the model-facing `agr_curation_query`
  tool documentation: one concise allele-specific sentence added to the
  `@function_tool` docstring (`packages/alliance/python/.../tools/agr_curation.py`,
  next to the gene sentence near `search_alleles`) so the MODEL sees it, AND the same
  sentence added to the bindings.yaml `agr_curation_query`
  `metadata.documentation.summary` (curator-facing) so the harness
  `bindings:agr_curation_query` home-check passes. Recorded in `.dropped.json` as
  `relocated -> bindings:agr_curation_query`. Relocated wording:
  **"search_alleles matches by exact, then prefix, then contains (case-insensitive),
  across symbols, full names, and synonyms -- so a shorter query returns more
  candidates and adding characters narrows them."**
- **`match_type` mechanic — DELETED (fine).** "Returns a `match_type` field showing
  how it matched" is dropped with no home (recorded as `deleted`, printed for
  review): `match_type` survives as a documented field of the result-contract
  `allele_candidates` detail (AV-32, kept), so the value is not lost; only the
  standalone tool-output restatement is dropped.

No curation guidance is lost: the rewrite keeps "use `search_alleles` because it
handles compact notations, partial symbols, and synonyms" as the method-choice
JUDGMENT in `<lookup_workflow>`.

### Template rule — Shared Result Contract: **LOAD-BEARING, KEPT (de-dup lever 2)**

VERIFIED: the `# Shared Result Contract` block is NOT injected by any shared prompt
layer — it is the ONLY place these fields are described for this agent. It is
therefore **load-bearing and KEPT** (wording tightened, no field dropped). Every
shared root field is retained verbatim as a backticked token because the shared
validator contract test
(`test_gene_and_allele_prompts_describe_shared_validator_policy`) asserts each one
for BOTH gene and allele.

### Template rule — reason_codes: **none (no `.reason_codes.txt`) — confirmed**

Validators do NOT enumerate exclusion reason codes. `AlleleResultEnvelope` defines
no reason-code enum bound to the validator output. The `lookup_attempts[].outcome`
values (`success`, `not_found`, `ambiguous`, `conflict`, `blocked`, `error`) are an
OUTCOME enum, not exclusion reason codes; they live in the result contract (AV-31)
and are NOT promoted to a `.reason_codes.txt`. So none is created.

---

## Role / goal / success

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| AV-01 | Agent identity: an Allele Data Specialist for the Alliance Genome Resources Curation Database who helps curators identify and validate allele information by querying the AGR curation database. | `<role>` (reframed to curator-voice "stronger specialized resolver"; identity + DB-query purpose retained) |
| AV-02 | Goal: validate allele symbols, names, IDs, species, obsolete status, and extinction status from database evidence; return structured `AlleleResultEnvelope` data using the shared validator result contract, without guessing or unsupported normalization. | `<goal>` |
| AV-03 | Success: calls `agr_curation_query` before providing allele information. | `<success_criteria>` (curator-facing success line KEPT once; the imperative "you MUST call before responding" is CORE's required-tool-call policy, AV-RTC). Literal token `` `agr_curation_query` `` retained. |
| AV-04 | Success: for domain validation requests, uses `selected_inputs.mention` as the primary query, with optional `selected_inputs.normalized_hint`, `selected_inputs.associated_gene`, `selected_inputs.taxon`, and `selected_inputs.evidence_quote` only as disambiguating context. | `<success_criteria>` + `<resolution_and_validation_rules>` (the `selected_inputs` handoff contract; AV-14) |
| AV-05 | Success: searches the literal compact allele symbol, notation, or ID supplied by the user or paper first; do not rewrite or normalize it before the first lookup. | `<success_criteria>` + `<resolution_and_validation_rules>` (literal-symbol-first; "do not rewrite or normalize it before the first lookup" is a contract-test token) |
| AV-06 | Success: never pass a whole evidence sentence, descriptive clause, phenotype sentence, or paragraph into `allele_symbol`. Evidence text is context for judging candidates, not the allele search query. | `<success_criteria>` (verbatim contract-test tokens: "Never pass a whole evidence sentence", "Evidence text is context for judging candidates") |
| AV-07 | Success: uses species context to set `data_provider` when a species is stated, and searches all species when no species is stated. | `<success_criteria>` + `<resolution_and_validation_rules>` (species selection; AV-17) |
| AV-08 | Success: reports each resolved allele in `resolved_objects`, copies expected scalar fields into `resolved_values`, and records candidate matches in `candidates` and `allele_candidates`. | `<success_criteria>` + `<result_contract>` (AV-30) |
| AV-09 | Success: uses `status: "resolved"` only when expected fields are filled from database evidence; uses `status: "unresolved"` when fields are missing, ambiguous, not found, or blocked by tool errors. | `<success_criteria>` + `<result_contract>` (verbatim `status: "resolved"` / `status: "unresolved"` tokens — contract-test requirement) |
| AV-10 | Success: lists unresolved expected fields in `missing_expected_fields`; explains missing data or ambiguity in `curator_message` and `explanation`. | `<success_criteria>` + `<result_contract>` |
| AV-11 | Success: records every database call in `lookup_attempts`, including provider, method, query, result count, outcome, and any short message. | `<success_criteria>` + `<result_contract>` (AV-31) |
| AV-12 | Success: uses only fields returned by query results for allele IDs, symbols, names, species, provider codes, status flags, synonyms, and attribution. | `<success_criteria>` + `<resolution_and_validation_rules>` (no-invention) |

## Scope / no-transfer

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| AV-13s | Handle allele symbols, names, full names, IDs, species information, obsolete status, and extinction status. For `AlleleMention` validator bindings, resolve final validated allele identity from the mention text and database evidence; do not assume extractor lookup hints are authoritative. | `<scope>` (verbatim `AlleleMention` retained; extractor-hints-not-authoritative discipline kept) |
| AV-14s | This agent only performs allele validation. For non-allele requests, do not transfer work, invoke another agent, or perform another agent's task; state that the non-allele portion is outside this agent's available tools/schema, preserve any in-scope allele lookup, and leave next-step selection to the supervisor/caller. | `<scope>` (no cross-agent transfer — verbatim discipline retained) |

## Resolution & validation rules (never-assume-equivalence, no-invention, literal-first, handoff inputs, species, symbol handling)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| AV-15 | CRITICAL: NEVER assume a symbol is an abbreviation for another gene name; ALWAYS search the LITERAL symbol from the paper first. Worked example (BMAL1 fl/fl mice -> do NOT silently jump to Arntl; search "BMAL1" literally so synonym matching finds stored alleles). The rule: (1) FIRST search the exact compact symbol, notation, or ID from the paper; (2) ONLY IF that fails with zero results, consider alternatives; (3) NEVER substitute a gene name with what you think it "really means". The DB stores historical names that differ from current gene nomenclature as synonyms, but only if you search the literal symbol. | `<resolution_and_validation_rules>` (the load-bearing never-assume-equivalence + literal-symbol-first rule; worked example retained; "NEVER ASSUME"/"ALWAYS" emphasis kept as a true invariant) |
| AV-16 | Tool requirement: do not answer from memory or training data, guess allele IDs, guess symbols or names, or provide allele information without querying. | `<resolution_and_validation_rules>` (no-memory/no-guessing; the "MUST call before responding" imperative is CORE AV-RTC, but the no-memory/no-guess curation rule is KEPT) |
| AV-14 | Domain-envelope validation inputs (`selected_inputs`): use `selected_inputs.mention` as the primary query; treat `selected_inputs.normalized_hint`, `selected_inputs.associated_gene`, `selected_inputs.taxon`, and `selected_inputs.evidence_quote` as disambiguating context only, NOT as already-validated facts. The extractor's lookup hints are proposals, not authoritative identity. | `<resolution_and_validation_rules>` (the `selected_inputs` handoff contract; field list retained as the channel the validator reads) |
| AV-17 | Species filtering table: when the query/paper mentions a species, pass the matching `data_provider` (mouse->MGI, fly->FB, worm->WB, human->HGNC, zebrafish->ZFIN, rat->RGD, yeast->SGD); if no species is mentioned, or if the species/provider context is uncertain, omit `data_provider` rather than guessing; the database search can search across taxa. | `<resolution_and_validation_rules>` (full species->provider table retained; verbatim contract-test tokens "omit `data_provider` rather than guessing", "across taxa") |
| AV-18 | Symbol handling — keep evidence out of the query: search the source-supported allele mention exactly as supplied first; the DB lookup layer is responsible for fuzzy matching, synonym matching, and database-specific symbol rendering differences; do not rewrite the paper text into a guessed database spelling before the first lookup. Keep supporting evidence quotes out of the `allele_symbol` argument; use them only to decide which returned candidate, if any, matches the intended allele. | `<resolution_and_validation_rules>` (verbatim contract-test tokens: "do not rewrite or normalize it before the first lookup" [from AV-05], "Keep supporting evidence quotes out of the `allele_symbol` argument") |
| AV-19 | Symbol handling — surrounding notation: when a paper mention includes genotype, zygosity, strain background, tissue, construct, or other surrounding notation, use the evidence quote, `associated_gene`, `normalized_hint`, and species/provider context to decide whether the returned database candidates resolve the intended allele. If the literal source-supported search returns no candidates, only try additional compact allele-like search strings that are directly supported by `selected_inputs`, the evidence quote, or database-returned candidate context. Do not use a full sentence or surrounding prose as the search string. Record all lookup attempts and leave the target unresolved when the database evidence is still missing or ambiguous. | `<resolution_and_validation_rules>` (verbatim contract-test token: "Do not use a full sentence or surrounding prose as the search string") |

## Lookup workflow (bounded ordered DB path + which-method-when judgment)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| AV-20 | Use the minimum lookup path sufficient for correctness: if the user/PDF provides a CURIE/ID, call `get_allele_by_id`; if you have a compact allele notation, gene-associated allele symbol, or partial allele symbol, call `search_alleles`; if you have an exact full symbol, call `get_allele_by_exact_symbol`. Keep supporting evidence quotes out of the `allele_symbol` argument — use them only to decide which returned candidate matches. | `<lookup_workflow>` (which-method-when JUDGMENT retained; per-method MECHANICS relocated to bindings; the keep-evidence-out clause is AV-18's contract token home) |
| AV-21 | Bounded lookup path (ordered): (1) determine whether the input is a CURIE/ID or a compact symbol/notation; (2) for CURIE/ID input, call `get_allele_by_id`; (3) for symbol/notation input, call `search_alleles`; (4) if too many results return, narrow by adding more characters or applying the known species `data_provider`; (5) if no result returns, report "Allele not found in database" with the exact search attempted. | `<lookup_workflow>` (ordered bounded path — the invariants file pins this order) |
| AV-22 | Troubleshooting judgment: too many results -> add a species filter with `data_provider`; a compact notation or partial symbol (e.g. "Ulk1") -> use `search_alleles` so it returns the matching allele variants; a deprecated or synonym-stored symbol -> use `search_alleles` because synonym matching may surface the current allele; multiple alleles sharing a symbol -> check the species, associated gene, and notation on each query result before deciding. | `<lookup_workflow>` (consolidated troubleshooting judgment; de-duped against the pre-rewrite `# Lookup Examples`) |

## Result contract (AlleleResultEnvelope — model-authored shared validator contract)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| AV-28 | Return only the shared validator statuses: `status: "resolved"` when lookup evidence resolves the requested target and all expected fields this validator can derive are present; `status: "unresolved"` when the target is not found, remains ambiguous, has missing expected fields, or cannot be checked because the tool fails. | `<result_contract>` (verbatim status tokens) |
| AV-29 | Populate these root fields exactly; do not wrap them under another object: `request_id` (copy from request), `validator_binding_id` (copy), `validator_agent` (copy package+agent identity), `target` (copy target object). | `<result_contract>` (verbatim backticked field tokens — each asserted by the contract test) |
| AV-30 | `resolved_values`: scalar values keyed by expected result field (allele CURIE, symbol, species, data provider, associated gene, status flags). `resolved_objects`: database-returned allele facts for resolved matches. `missing_expected_fields`: expected fields this validator could not populate. `candidates`: generic candidate records for exact/ambiguous/alternate matches, with `value`, `label`, `object_type`, `matched_fields`, and relevant `details`. | `<result_contract>` (verbatim field tokens) |
| AV-31 | `lookup_attempts`: one record per `agr_curation_query` call — provider `agr_curation_query`, method name, query payload, `result_count`, outcome (`success`, `not_found`, `ambiguous`, `conflict`, `blocked`, `error`). `curator_message`: concise curator-facing summary of what resolved, what is missing, or why ambiguity remains. `explanation`: plain-language decision explanation tied to database evidence and lookup attempts. | `<result_contract>` (verbatim field tokens; outcome enum retained, incl. allele-specific `blocked`) |
| AV-32 | `allele_candidates`: allele-specific candidate details preserving useful lookup fields — `allele_id`, `symbol`, `species`, `data_provider`, `name`, `associated_gene`, `is_obsolete`, `is_extinct`, `synonyms`, `fullname_attribution`, and `match_type`. | `<result_contract>` (verbatim `allele_candidates`; `match_type` survives here as a candidate detail field) |
| AV-33 | For each resolved or candidate allele object include: `allele_id` (Alliance CURIE, e.g. `WB:WBVar00012345` or `MGI:3689906`), `symbol` (e.g. `e1370` or `Ulk1<sup>tm1Thsn</sup>`), `species` (full name, e.g. `Caenorhabditis elegans` or `Mus musculus`), `data_provider` (WB, FB, MGI, HGNC, ZFIN, RGD, or SGD). | `<result_contract>` (allele-object field shape — NOT carried by CORE; KEPT) |
| AV-34 | Include optional fields only when available in query results: `name` (full allele name), `associated_gene` (gene symbol/ID this allele belongs to), `is_obsolete` (true if marked obsolete), `is_extinct` (true if the strain no longer exists), `synonyms`, `fullname_attribution` (creator/institution from fullname for MGI/RGD only, with `value`, `confidence` ("probable" or "uncertain"), and `source` ("fullname_suffix")). | `<result_contract>` (optional allele-object field detail retained) |
| AV-35 | Structured output is enforced as `AlleleResultEnvelope`; keep prose concise and compatible with that schema. | DE-DUP -> CORE (`render`). The locked core's structured-output block mandates valid JSON matching `AlleleResultEnvelope` and is authoritative for response shape; the base no longer restates the JSON-only mandate. The allele-object FIELD detail (AV-33/AV-34) is KEPT. |
| AV-36 | `AlleleResultEnvelope` token present in the prompt. | `<result_contract>` (token retained once for curator readability; the schema enforcement lives in CORE) |

## Stop rules

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| AV-37 | Stop when the query results are sufficient to populate the result; do not keep searching to improve phrasing. | `<stop_rules>` |
| AV-38 | If database evidence is missing or ambiguous, do not guess; return `status: "unresolved"`, preserve available candidates, record lookup attempts, list missing expected fields, and explain what could not be resolved. | `<stop_rules>` |
| AV-39 | If an allele is not found, return `status: "unresolved"`, keep `resolved_values` empty for the missing fields, record the failed lookup attempt, and explain "Allele not found in database" when appropriate. | `<stop_rules>` (parallel to the gene not-found stop rule; "Allele not found in database" is the lookup-path step 5 phrase, reused in <lookup_workflow>) |
| AV-40 | If the request is ambiguous after lookup, return verified candidates rather than guessing. | `<stop_rules>` (folded into AV-38; preserve-candidates is the ambiguity outcome) |
| AV-41 | If data is outside this agent's scope, do not fabricate, transfer work, or call another specialist; state the scope limit and return only supported in-scope allele results. | `<stop_rules>` (merged with AV-14s) |

## CORE-injected (no base restatement)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| AV-RTC | Required tool-call policy: call at least one of `agr_curation_query` before final output. | CORE (`render`). The base keeps the curator-facing "calls `agr_curation_query` before providing allele information" success line (AV-03) and the no-memory/no-guess rule (AV-16), but does not restate the machine imperative. |

## Repository URL line (DELETED)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| AV-REPO | "Repository: https://github.com/alliance-genome/agr_curation_api_client" | DELETED. A developer-facing repo link is not a curation rule and not curator-voice content; it instructs the model on nothing. Dropped with no home (recorded in `.dropped.json` as `deleted`, printed in review). |

## Group-rule hooks (rendered with the group)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| AV-GRP | The 7 group rules (FB/HGNC/MGI/RGD/SGD/WB/ZFIN) carry organism-specific allele-search-strategy + result-interpretation overlays (MGI/RGD are substantial; e.g. MGI: "Strip Genotype Notation Before Searching", "Selecting from Multiple Search Results", `fullname_attribution` disambiguation). The base rewrite must keep rendering cleanly under each group. | Group rules (`group_rules/*.yaml`) — UNCHANGED in this task. One sample-group inventory (`allele.mgi.txt`) is added to verify the base rewrite renders cleanly under a group (mirrors how the gene harness samples `gene.mgi.txt`); no contract test asserts allele group-rule content. |

---

## De-dup summary (the allele-validator Phase-C levers)

1. **Tool MECHANICS vs JUDGMENT (`# Available Allele Methods`):** the per-method
   which-method-when semantics are carried by the curator catalog
   (`bindings:agr_curation_query` `methods.*`) and relocated; the search-order +
   case-insensitive mechanic (LIKE exact/prefix/contains, case-insensitive,
   searches-synonyms) is **RELOCATED** to the model-facing `agr_curation_query`
   docstring + bindings `documentation.summary` (it is strategy-affecting, since
   `<lookup_workflow>`'s "add characters to narrow" depends on it); only the
   standalone `match_type` restatement is deleted with no home (`match_type` survives
   in the `allele_candidates` result detail). The curation JUDGMENT (which method to
   choose when, literal-symbol-first, keep-evidence-out-of-the-query) is KEPT.
2. **Shared Result Contract:** verified NOT injected by a shared layer; KEPT
   (load-bearing), wording tightened, no field dropped.
3. **Consolidation:** `# Symbol Handling`, `# Available Allele Methods`,
   `# Lookup Examples` overlap heavily; consolidated into
   `<resolution_and_validation_rules>` + `<lookup_workflow>` without losing a rule.
   The per-query `# Lookup Examples` (Ulk1/e1370/Mx1-cre/MGI:3689906) are example
   tool calls whose curation lesson is preserved in the troubleshooting judgment
   (AV-22); the verbatim example-call lines themselves are not load-bearing rules and
   are not inventoried.
4. **CORE de-dup:** the required-tool-call imperative and the JSON-only output
   mandate are relocated to the locked core (kept as curator-facing success lines
   once); the allele-object field detail is KEPT because the core does not enumerate
   it.
5. **Repository URL:** deleted (developer link, not a curation rule).
6. **Batch:** N/A — the allele base prompt never carried a batch/bulk protocol, so
   none is added (faithful migration, not feature addition).

## Contract-test coverage

**No test assertion is edited, deleted, or weakened by this rewrite.** Two contract
tests in `backend/tests/unit/test_gene_allele_validator_result_contract.py`
constrain the allele base prompt content; every asserted fragment is **retained
verbatim** in the rewrite, so all assertions pass unchanged:

- `test_gene_and_allele_prompts_describe_shared_validator_policy` (gene + allele):
  requires `` `status: "resolved"` ``, `` `status: "unresolved"` ``,
  `` `agr_curation_query` ``, and each shared root field as `` `field` ``
  (request_id, validator_binding_id, validator_agent, target, resolved_values,
  resolved_objects, missing_expected_fields, candidates, lookup_attempts,
  curator_message, explanation) — all retained (AV-09/AV-28/AV-29/AV-30/AV-31).
  Forbidden: `under_development`, `mark_under_development`, `repair_action`,
  `extractor_patch` — none introduced.
- `test_allele_prompt_keeps_evidence_quotes_out_of_symbol_queries` (allele-specific):
  requires "do not rewrite or normalize it before the first lookup" (AV-05),
  "Never pass a whole evidence sentence" (AV-06), "Evidence text is context for
  judging candidates" (AV-06), "Keep supporting evidence quotes out of the
  `allele_symbol` argument" (AV-18), "Do not use a full sentence or surrounding prose
  as the search string" (AV-19), "omit `data_provider` rather than guessing" (AV-17),
  "across taxa" (AV-17) — all retained verbatim. Forbidden: `` `N fa-g` -> search
  `N[fa-g]` ``, "after stripping genotype notation", "Automatically tries original" —
  none introduced.

The schema-validation tests assert against the `AlleleResultEnvelope` model, not the
prompt text, so they are unaffected. No re-baseline was needed.
