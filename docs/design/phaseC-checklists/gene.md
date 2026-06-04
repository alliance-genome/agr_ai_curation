# Phase C semantic-coverage checklist: `gene` validator (Wave 3 pilot — VALIDATOR SKELETON)

This is the **authoritative inventory source** for the outcome-first rewrite of
`packages/alliance/agents/gene/prompt.yaml` (canonical agent id `gene_validation`).
Every load-bearing rule in the pre-rewrite prompt is listed here with a stable ID
(GV-NN) and its new home in the rewritten prompt, OR an explicit, justified
relocation/deletion. The harness inventories
(`phase_c_inventories/gene.txt`, `.mgi.txt`, `.invariants.txt`, `.dropped.json`)
are derived from this checklist.

`gene` is the **validator pilot**: it establishes the reusable VALIDATOR skeleton
that the other 13 validators + supervisor follow, just as `gene_extractor` is the
builder-extractor pilot.

Legend for "New home":
- A `<section>` name is a section of the **rewritten** base prompt.
- `CORE` means the locked Generated Runtime Contract
  (`assembly.py::_build_core_generated_content`) already injects this exact fact;
  the base prompt does NOT restate the core's phrasing (template rule "no core
  duplication"). Recorded in `.dropped.json` as `relocated -> render` (the fact
  survives in the core half of the assembled render).
- `RELOCATED -> <home>` / `DELETED` mean the rule's fact moves elsewhere on the
  production path (a `bindings.yaml` tool description) or is dropped with no home;
  recorded in `.dropped.json` as `relocated` (machine-checked home) / `deleted`.

---

## What `gene` actually IS (role + output contract + skeleton choice)

`gene` (canonical agent id `gene_validation`) is a **domain-pack VALIDATOR**, not an
extractor and not a builder. Verified against the code:

- `packages/alliance/agents/gene/agent.yaml` sets `output_schema: GeneResultEnvelope`
  (NOT `null`) and tools `[get_agent_contract, agr_curation_query]`. So it is an
  **envelope-authoring agent** that hand-authors `GeneResultEnvelope` directly,
  exactly like `pdf` authors `PdfExtractionResultEnvelope` — NOT a builder that
  stages into a backend materializer.
- Therefore the **builder metadata-template rule does NOT apply** here: there is no
  `stage_*`/`finalize_*` workflow, no "you never write the envelope / backend
  materializes metadata", no exclude=don't-stage rewrite, and no
  `<validator_handoff>` to write (a validator IS the handoff target). The model
  fills the `GeneResultEnvelope` root fields itself.
- Its job is to **RESOLVE / VALIDATE a gene identity** against the AGR curation DB
  via `agr_curation_query`, and return the **shared validator result contract**
  (`DomainValidatorResultBase` root fields) plus gene-specific candidate detail.

So the rewrite uses a **role-adapted, outcome-first VALIDATOR skeleton** (the
template for the other 13 validators + supervisor), NOT the builder-extractor
skeleton:

`<role>` -> `<goal>` -> `<success_criteria>` (end-state: resolved-vs-unresolved
decided correctly, every DB call recorded, no guessing) -> `<scope>` (in/out of
scope; no cross-agent transfer) -> `<resolution_and_validation_rules>` (verify-vs-
resolve mode, no-invention/no-memory, literal-symbol-first, species/provider
selection, batch mode + bulk grouping, identity disambiguation from handoff
context, symbol conventions) -> `<lookup_workflow>` (bounded ordered DB lookup path
+ which-method-when judgment) -> `<result_contract>` (the GeneResultEnvelope root
fields + statuses + gene candidate/object detail) -> `<stop_rules>`. The
outcome-first ORDER (Role -> Goal -> Success -> Scope -> Rules -> Workflow ->
Output -> Stop) is preserved.

### VALIDATOR framing (load-bearing, per Chris)

The validator is framed as the **stronger specialized resolver** with deeper DB
access and a curator-editable prompt — NOT a guardrail policing a "forbidden"
extractor. The base prompt IS curator-editable; it is written in curator voice for
a biologist with no developer background. (Positive, capable framing: the validator
owns the database lookup, the disambiguation, and the final identity call.)

---

## Template rules applied (Phase C — VALIDATOR template)

### Template rule — builder metadata exclude=don't-stage: **N/A (verified)**

Does not apply: `gene_validation` authors `GeneResultEnvelope` directly (see above).
There is no `metadata.*` materializer and no stage tool. The model EXPRESSES an
unresolved outcome by writing `status: "unresolved"` + `missing_expected_fields` +
`candidates`/`gene_candidates` — real top-level, model-authored channels. Preserve
that mechanism; do not import the builder don't-stage rewrite.

### Template rule — no core duplication (de-dup lever 1: required-tool-call + output)

`assembly.py::_build_core_generated_content` already injects, for `gene_validation`
(verified by rendering `build_agent_core_prompt('gene_validation')`):

- the **required-tool-call policy**: "call at least one of agr_curation_query before
  final output";
- the **output contract**: "produce JSON matching GeneResultEnvelope; the
  structured-output layer below is authoritative for final response shape" PLUS the
  "CRITICAL: ALWAYS PRODUCE STRUCTURED OUTPUT AS VALID JSON ... must be valid JSON
  matching the GeneResultEnvelope schema" block;
- the **get_agent_contract** pointer for detailed field/tool/schema/validator facts.

The pre-rewrite BASE prompt restated some of these; the rewrite removes the
restatements (de-dup, recorded in `.dropped.json` as `relocated -> render`), but
KEEPS the curator-facing curation rule once. Specifically:

- "You MUST call the `agr_curation_query` tool before providing any response about
  genes." (`## Tool Requirement`) -> de-dup to CORE's required-tool-call policy. The
  curator-facing success line "Calls `agr_curation_query` before providing any gene
  information" is KEPT once, and the literal token `` `agr_curation_query` `` stays
  in the prompt (a gene-contract-test requirement — GV-RC below).
- "Structured output is enforced as `GeneResultEnvelope`. Keep prose concise..."
  (`# Output` opener) -> de-dup to CORE's structured-output block, which is
  authoritative for the response shape. The gene-specific FIELD detail of
  `GeneResultEnvelope` (gene_id/symbol/taxon/species/data_provider + optional
  fields) is KEPT (GV-35/GV-36), because the core does not enumerate those fields.

### Template rule — tool MECHANICS vs curation JUDGMENT (de-dup lever 1, "Available Gene Methods")

The pre-rewrite `# Available Gene Methods` block (L175-230) restated pure
`agr_curation_query` tool MECHANICS that the curator tool catalog
(`bindings.yaml` `agr_curation_query` `methods.*`) already carries: method names,
required/optional param lists, and per-method one-line behavior ("Find genes whose
symbol contains the text you give" / "exactly matches"). The rewrite removes the
restated mechanics and KEEPS the curation JUDGMENT (which method to choose when,
the literal-symbol-first rule, the rutabaga->rut synonym example, the bulk-grouping
rule).

**Verified — what bindings.yaml carries vs does NOT carry** (the harness home-check
for `bindings:agr_curation_query` searches the tool's top-level `description` +
`metadata.documentation.summary`, NOT the per-method blocks or the Python
docstring):

- bindings `description`/`summary`: "Look things up in the Alliance Curation
  Database — genes, alleles, ... through one tool with many specific lookup
  methods." + "A single tool for looking up Alliance curation data. It offers many
  specific methods — search for a gene, fetch an allele by ID, ... Different agents
  use the methods that fit their job."
- bindings `methods.search_genes.description`: "Find genes whose symbol contains the
  text you give." (carries the contains/partial-match semantics, curator-level)
- bindings `methods.get_gene_by_exact_symbol.description`: "Find the gene whose
  official symbol exactly matches." (carries exact-match semantics)
- bindings `methods.search_genes_bulk.description`: "Search for many gene symbols at
  once in a single call." (carries the bulk semantics)
- bindings `methods.get_gene_by_id.description`: "Get full details for one gene by
  its ID."

So the **per-method MECHANICS** (which method does exact vs contains, bulk, by-id)
are carried by the curator catalog and are relocated. The handful of low-level
implementation mechanics the catalog does NOT carry —
**"LIKE patterns: exact, prefix, then contains", "case-insensitive", "Returns a
`match_type` field showing how it matched", "Searches symbols, full names, and
synonyms"** — are pure internal mechanics with low curation value. They are
**DELETED with no home** (recorded in `.dropped.json` as `deleted`, printed for
review), because: (a) they are not a curation judgment the curator edits, (b)
`match_type` survives as a field in the result-contract `gene_candidates` detail
(GV-36, kept), and (c) adding them to the model-facing tool docstring/bindings would
re-introduce the exact duplication this lever removes. No curation guidance is lost:
the rewrite keeps "use `search_genes` because it handles exact, partial, synonym,
and alternative-name matches" as the method-choice JUDGMENT in `<lookup_workflow>`.

> NOTE for the next 13 validators (allele/etc.): the allele validator carries the
> identical "Available Allele Methods" mechanics block (`search_alleles` LIKE/
> case-insensitive/match_type). Apply the SAME decision: relocate the per-method
> which-method-when semantics (carried by bindings `methods.*`), delete the pure
> internal mechanics, keep the literal-symbol-first + method-choice judgment.

### Template rule — Shared Result Contract: **LOAD-BEARING, KEPT (de-dup lever 2)**

VERIFIED (per task): the `# Shared Result Contract` block (L248-266) is NOT injected
by any shared prompt layer — it is the ONLY place these fields are described for this
agent (the core injects only the JSON-output mandate, not the field roster; the
`get_agent_contract` output_schema topic is a read-only helper the model would have
to call, not injected text). It is therefore **load-bearing and KEPT** (wording
tightened, no field dropped). Every shared root field is retained verbatim as a
backticked token because the gene contract test
(`test_gene_and_allele_prompts_describe_shared_validator_policy`) asserts each one.

### Template rule — reason_codes: **none (no `.reason_codes.txt`) — confirmed**

Validators do NOT enumerate exclusion reason codes (confirmed per task; this matches
the disease/allele/phenotype-validator precedent). The pre-rewrite gene prompt
enumerates **no** canonical reason-code list, and `GeneResultEnvelope` defines no
reason-code enum bound to the validator output. The `lookup_attempts[].outcome`
values (`success`, `not_found`, `ambiguous`, `conflict`, `error`) are an OUTCOME
enum, not exclusion reason codes; they live in the result contract (GV-31) and are
NOT promoted to a `.reason_codes.txt`. So none is created.

---

## Role / goal / success

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| GV-01 | Agent identity: a Gene Data Specialist for the Alliance Genome Resources Curation Database who helps curators identify and validate gene information by querying the AGR curation database. | `<role>` (reframed to curator-voice "stronger specialized resolver"; identity + DB-query purpose retained) |
| GV-02 | Goal: validate gene symbols, names, IDs, species, genomic locations, cross-references, gene types, and full names from database evidence; return structured `GeneResultEnvelope` data using the shared validator result contract, without guessing, unsupported normalization, or memory-based gene facts. | `<goal>` |
| GV-03 | Success: calls `agr_curation_query` before providing any gene information. | `<success_criteria>` (curator-facing success line KEPT once; the imperative "you MUST call before responding" is CORE's required-tool-call policy, GV-RTC). Literal token `` `agr_curation_query` `` retained. |
| GV-04 | Success: searches the literal symbol, name, or ID supplied by the user or paper first. | `<success_criteria>` + `<resolution_and_validation_rules>` (literal-symbol-first; also GV-12) |
| GV-05 | Cleans genotype notation only after the tool flags it or the input clearly contains zygosity notation that is not stored as a gene symbol. | `<resolution_and_validation_rules>` (symbol handling; merged with GV-19) |
| GV-06 | Uses species context to set `data_provider` when a species is stated, and searches all species when no species is stated. | `<success_criteria>` + `<resolution_and_validation_rules>` (species selection; also GV-17) |
| GV-07 | Copies authoritative scalar fields into `resolved_values`, records candidate matches in `candidates` and `gene_candidates`, treats `resolved_objects` as optional diagnostic lookup context only; the gene domain pack materializes scalar fields on the `gene_mention_evidence` target and does not create a separate Gene object. | `<success_criteria>` + `<result_contract>` (GV-28/GV-30) |
| GV-08 | Uses `status: "resolved"` only when expected fields are filled from database evidence; uses `status: "unresolved"` when fields are missing, ambiguous, not found, or blocked by tool errors. | `<success_criteria>` + `<result_contract>` (verbatim `status: "resolved"` / `status: "unresolved"` tokens — contract-test requirement) |
| GV-09 | Lists unresolved expected fields in `missing_expected_fields`; explains missing data or ambiguity in `curator_message` and `explanation`. | `<success_criteria>` + `<result_contract>` |
| GV-10 | Records every database call in `lookup_attempts`, including provider, method, query, result count, outcome, and any short message. | `<success_criteria>` + `<result_contract>` (GV-31) |
| GV-11 | Uses only fields returned by query results for gene IDs, symbols, names, species, provider codes, gene types, genomic locations, cross-references, and synonyms. | `<success_criteria>` + `<resolution_and_validation_rules>` (no-invention) |

## Scope / no-transfer

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| GV-13s | Handles gene symbols, names, IDs such as `WB:WBGene00001234`, genomic locations, cross-references, gene types, full names, and synonyms. | `<scope>` (verbatim example CURIE retained) |
| GV-14s | This agent only performs gene validation. For non-gene requests, do not transfer work, invoke another agent, or perform another agent's task; state that the non-gene portion is outside this agent's available tools/schema, preserve any in-scope gene lookup, and leave next-step selection to the supervisor/caller. | `<scope>` (no cross-agent transfer — verbatim discipline retained) |

## Resolution & validation rules (verify/resolve, no-invention, literal-first, species, batch, disambiguation, symbol conventions)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| GV-12 | CRITICAL: NEVER assume a symbol is an abbreviation for another gene name; ALWAYS search the LITERAL symbol from the paper first. Worked example (p53 -> do NOT silently jump to Trp53; search "p53" literally so synonym matching finds it). The rule: (1) FIRST search the EXACT symbol from the paper; (2) ONLY IF that fails with zero results, consider alternatives; (3) NEVER substitute a gene name with what you think it "really means". The DB stores historical/common/alias names as synonyms, but only if you search the literal symbol. | `<resolution_and_validation_rules>` (the load-bearing never-assume-equivalence + literal-symbol-first rule; worked example retained; "NEVER ASSUME" emphasis kept as a true invariant) |
| GV-13 | Tool requirement: do not answer from memory or training data, guess gene IDs, guess symbols or names, or provide gene information without querying. | `<resolution_and_validation_rules>` (no-memory/no-guessing; the "MUST call before responding" imperative is CORE GV-RTC, but the no-memory/no-guess curation rule is KEPT) |
| GV-14 | Domain-envelope validation inputs: when called from an active domain-pack validator binding, read the request's `selected_inputs` as extractor proposals and context, NOT as already-validated facts. The fields gene extraction may provide: `mention`, `proposed_gene_id`, `proposed_symbol`, `proposed_taxon`, `taxon_hint`, `data_provider_hint`, `species`, `evidence_quote`, `identity_resolution_notes`. | `<resolution_and_validation_rules>` (the `selected_inputs` contract; field list retained as the handoff channel the validator reads) |
| GV-15 | Verify mode vs resolve mode: use VERIFY mode when `proposed_gene_id`, `proposed_symbol`, or `proposed_taxon` is present (confirm DB facts match the proposal, report conflicts explicitly); use RESOLVE mode when only `mention` plus species/taxon/provider context is present (bounded lookup -> resolved values or unresolved candidates). | `<resolution_and_validation_rules>` (verify-vs-resolve modes — load-bearing) |
| GV-16 | Provider-selection precedence: prefer `data_provider_hint` first, then `taxon_hint`, then `species`-derived provider context when choosing a provider filter. | `<resolution_and_validation_rules>` (provider precedence) |
| GV-17 | Species filtering table: when the query/paper mentions a species, pass the matching `data_provider` (mouse->MGI, fly->FB, worm->WB, human->HGNC, zebrafish->ZFIN, rat->RGD, yeast->SGD); if no species is mentioned, omit `data_provider` and search all species. | `<resolution_and_validation_rules>` (full species->provider table retained) |
| GV-18 | Extractor-context-for-disambiguation: the extractor that built the request had access to the paper; **you do not**. Treat `evidence_quote`, `identity_resolution_notes`, `target.input_values`, and the request-level `evidence` records as your paper context; use them to choose bounded searches and disambiguate. If the primary `mention` is broad/generic/multi-candidate, inspect the provided paper context for a more specific paper-supported search phrase, alias, full name, synonym, locus-style wording, organism clue, or protein/gene relationship before returning unresolved. Do not invent a query term not supported by the request context, and do not guess a final identifier. | `<resolution_and_validation_rules>` (verbatim contract-test tokens: `` `identity_resolution_notes` ``, "you do not", "request-level `evidence` records", "more specific paper-supported search phrase") |
| GV-19 | Symbol handling: search tools send supplied symbols to the curation DB lookup layer without local symbol-shape rejection. If a mention includes genotype, zygosity, strain background, tissue, construct, or surrounding notation, use the evidence quote + species/provider context to decide whether DB candidates resolve the intended gene; do not assume local formatting rules are authoritative — let database evidence and returned candidates drive the final decision. | `<resolution_and_validation_rules>` (symbol handling; merged with GV-05) |
| GV-20 | Symbol conventions: search the paper's literal symbol first even when organism naming conventions suggest another capitalization or ortholog. Common conventions: C. elegans (WB) lowercase-hyphen (`daf-16`); Drosophila (FB) often lowercase/mixed (`rut`, `Notch`, `white`); Mouse (MGI) capitalized first letter (`Pax6`, `Trp53`); Human (HGNC) all caps (`TP53`, `BRCA1`); Zebrafish (ZFIN) lowercase (`tp53`, `pax6a`); Rat (RGD) like mouse (`Tp53`); Yeast (SGD) all caps (`ACT1`). | `<resolution_and_validation_rules>` (organism convention guidance retained, compacted) |
| GV-21 | Synonym example: paper says "rutabaga" (full name); DB official symbol is "rut" (abbreviated); `search_genes` finds both because it searches synonyms. | `<resolution_and_validation_rules>` (rutabaga->rut synonym example KEPT — curation judgment, not tool mechanic) |
| GV-22 | Batch mode: when the input has `mode: "domain_validator_batch"` and a `requests` list, validate every request and return a single object with `results`; the `results` array must contain exactly one validator result per `request_id`; copy request identity fields from each request. | `<resolution_and_validation_rules>` (verbatim contract-test tokens: `mode: "domain_validator_batch"`) |
| GV-23 | Batch bulk grouping rule: group compatible requests by shared `data_provider_hint`, `taxon_hint`, or species-derived provider context. For each group with >1 symbol/name mention, call `agr_curation_query` exactly once with `method: "search_genes_bulk"`, `gene_symbols: [...]`, the shared `data_provider`, `include_synonyms: true`, and a bounded `limit`. Do not call `search_genes_bulk` separately for each request in the same group; do not send one-symbol `gene_symbols` lists when several requests can share a list lookup. Map each returned bulk item back to the matching request and preserve unresolved/ambiguous inputs as individual unresolved results instead of dropping them. The shared bulk lookup is only a first-pass candidate fetch; for any request that remains ambiguous, use that request's paper context for focused follow-up lookups before deciding it is unresolved. | `<resolution_and_validation_rules>` (verbatim contract-test tokens: "group compatible requests", `method: "search_genes_bulk"`, `gene_symbols: [...]`, "Do not call", "separately for each request", "Map each returned bulk item back to the matching request", "paper context for focused follow-up lookups") |

## Lookup workflow (bounded ordered DB path + which-method-when judgment)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| GV-24 | Use the minimum lookup path sufficient for correctness: if the user/paper provides a CURIE/ID, call `get_gene_by_id`; if you have a symbol/name/partial-name/synonym, call `search_genes`; if you are confident you already have the exact official database symbol, call `get_gene_by_exact_symbol`. | `<lookup_workflow>` (which-method-when JUDGMENT retained; per-method MECHANICS relocated to bindings) |
| GV-25 | Bounded lookup path (ordered): (1) determine whether the input is a CURIE/ID or a symbol/name; (2) for CURIE/ID input, call `get_gene_by_id`; (3) for symbol/name input, call `search_genes` because it handles exact matches, partial matches, synonyms, and alternative names; (4) if too many results return, narrow by adding more characters or applying the known species `data_provider`; (5) if no result returns, report "Gene not found in database" with the exact search attempted. | `<lookup_workflow>` (ordered bounded path — the invariants file pins this order) |
| GV-26 | Troubleshooting judgment: too many results -> add a species filter with `data_provider`; full gene name (e.g. "rutabaga") -> use `search_genes` so synonyms match; old/deprecated symbol -> use `search_genes` because synonym matching may find the current symbol; wrong species assumed -> specify `data_provider` whenever species is known; multiple genes with the same name -> check species and taxon in the query results. | `<lookup_workflow>` (consolidated with GV-25/GV-26 troubleshooting; de-duped against `# Lookup Guidance`) |
| GV-27 | `search_genes_bulk` is recommended for batch symbol/name lookup; one shared call should cover a whole compatible group in domain validator batch mode. | `<lookup_workflow>` (method-choice for batch; mechanics relocated; merged with GV-23) |

## Result contract (GeneResultEnvelope — model-authored shared validator contract)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| GV-28 | Return only the shared validator statuses: `status: "resolved"` when lookup evidence resolves the requested target and all expected fields this validator can derive are present; `status: "unresolved"` when the target is not found, remains ambiguous, has missing expected fields, or cannot be checked because the tool fails. | `<result_contract>` (verbatim status tokens) |
| GV-29 | Populate these root fields exactly; do not wrap them under another object: `request_id` (copy from request), `validator_binding_id` (copy), `validator_agent` (copy package+agent identity), `target` (copy target object). | `<result_contract>` (verbatim backticked field tokens — each asserted by the contract test) |
| GV-30 | `resolved_values`: scalar values keyed by expected result field (gene CURIE, symbol, species, data provider, full name). `resolved_objects`: optional diagnostic lookup context only — do not rely on it for materialization and do not invent a separate Gene object when the active binding expects scalar `resolved_values`. `missing_expected_fields`: expected fields this validator could not populate. `candidates`: generic candidate records for exact/ambiguous/alternate matches, with `value`, `label`, `object_type`, `matched_fields`, and relevant `details`. | `<result_contract>` (verbatim field tokens) |
| GV-31 | `lookup_attempts`: one record per `agr_curation_query` call — provider `agr_curation_query`, method name, query payload, `result_count`, outcome (`success`, `not_found`, `ambiguous`, `conflict`, `error`). `curator_message`: concise curator-facing summary of what resolved, what is missing, or why ambiguity remains. `explanation`: plain-language decision explanation tied to database evidence and lookup attempts. | `<result_contract>` (verbatim field tokens; outcome enum retained) |
| GV-32 | `gene_candidates`: gene-specific candidate details preserving useful lookup fields — `gene_id`, `symbol`, `species`, `data_provider`, `name`, `gene_type`, `genomic_location`, `cross_references`, `synonyms`, and `match_type`. | `<result_contract>` (verbatim `gene_candidates`; `match_type` survives here as a candidate detail field) |
| GV-33 | For each resolved or candidate gene object include: `gene_id` (Alliance CURIE, e.g. `WB:WBGene00001234`), `symbol` (e.g. `daf-16`), `taxon` (NCBI Taxon CURIE when returned/derived), `species` (full name, e.g. `Caenorhabditis elegans`), `data_provider` (WB, FB, MGI, HGNC, ZFIN, RGD, or SGD). | `<result_contract>` (gene-object field shape — NOT carried by CORE; KEPT) |
| GV-34 | Include optional fields only when returned by query results: `name`, `gene_type`, `genomic_location` (chromosome, start, end, strand when available), `cross_references`, `synonyms`. | `<result_contract>` |
| GV-35 | Structured output is enforced as `GeneResultEnvelope`; keep prose concise and compatible with that schema. | DE-DUP -> CORE (`render`). The locked core's structured-output block mandates valid JSON matching `GeneResultEnvelope` and is authoritative for response shape; the base no longer restates the JSON-only mandate. The gene-object FIELD detail (GV-33/GV-34) is KEPT. |
| GV-36 | `GeneResultEnvelope` token present in the prompt. | `<result_contract>` (token retained once for curator readability; the schema enforcement lives in CORE) |

## Stop rules

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| GV-37 | Stop after required database evidence is collected; do not continue searching to improve phrasing. | `<stop_rules>` |
| GV-38 | If queried for multiple genes, return all found genes and record unresolved inputs through `missing_expected_fields`, `lookup_attempts`, `curator_message`, and `explanation`. | `<stop_rules>` |
| GV-39 | If a gene is not found, return `status: "unresolved"`, keep `resolved_values` empty for the missing fields, record the failed lookup attempt, and explain "Gene not found in database" when appropriate. | `<stop_rules>` |
| GV-40 | If the request is ambiguous after lookup, return verified candidates rather than guessing. | `<stop_rules>` |
| GV-41 | If data is outside this agent's scope, do not fabricate, transfer work, or call another specialist; state the scope limit and return only supported in-scope gene results. | `<stop_rules>` (merged with GV-14s) |

## CORE-injected (no base restatement)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| GV-RTC | Required tool-call policy: call at least one of `agr_curation_query` before final output. | CORE (`render`). The base keeps the curator-facing "calls `agr_curation_query` before providing gene information" success line (GV-03) and the no-memory/no-guess rule (GV-13), but does not restate the machine imperative. |

## Repository URL line (DELETED)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| GV-REPO | "Repository: https://github.com/alliance-genome/agr_curation_api_client" | DELETED. A developer-facing repo link is not a curation rule and not curator-voice content; it instructs the model on nothing. Dropped with no home (recorded in `.dropped.json` as `deleted`, printed in review). |

## Group-rule hooks (rendered with the group)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| GV-GRP | The 7 group rules (FB/HGNC/MGI/RGD/SGD/WB/ZFIN) carry organism-specific gene-search-strategy + result-interpretation overlays (placeholders today, e.g. MGI: "Mouse gene symbols use initial capital letter"; WB: "C. elegans gene names follow pattern: 3- to 4-letter prefix + hyphen + number"). The base rewrite must keep rendering cleanly under each group. | Group rules (`group_rules/*.yaml`) — UNCHANGED in this task. One sample-group inventory (`gene.mgi.txt`) is added to verify the base rewrite renders cleanly under a group (mirrors how the extractor harness samples one group, e.g. gene_extractor.fb); no contract test asserts gene group-rule content. |

---

## De-dup summary (the gene-validator Phase-C levers)

1. **Tool MECHANICS vs JUDGMENT (`# Available Gene Methods`):** the per-method
   which-method-when semantics are carried by the curator catalog
   (`bindings:agr_curation_query` `methods.*`) and relocated; the pure internal
   mechanics (LIKE exact/prefix/contains, case-insensitive, `match_type` field,
   searches-synonyms) are deleted with no home (low curation value;
   `match_type` survives in the `gene_candidates` result detail). The curation
   JUDGMENT (which method to choose when, literal-symbol-first, rutabaga->rut
   synonym example) is KEPT.
2. **Shared Result Contract:** verified NOT injected by a shared layer; KEPT
   (load-bearing), wording tightened, no field dropped.
3. **Consolidation:** `# Lookup Guidance`, `# Troubleshooting`, `# Symbol Handling`,
   `# Symbol Conventions` overlap heavily; consolidated into
   `<resolution_and_validation_rules>` + `<lookup_workflow>` without losing a rule.
4. **CORE de-dup:** the required-tool-call imperative and the JSON-only output
   mandate are relocated to the locked core (kept as curator-facing success lines
   once); the gene-object field detail is KEPT because the core does not enumerate
   it.
5. **Repository URL:** deleted (developer link, not a curation rule).

## Contract-test re-baseline

**No test assertion is edited, deleted, or weakened by this rewrite.** Three gene
tests in `backend/tests/unit/test_gene_allele_validator_result_contract.py`
constrain the gene base prompt content; every asserted fragment is **retained
verbatim** in the rewrite, so all assertions pass unchanged:

- `test_gene_and_allele_prompts_describe_shared_validator_policy`: requires
  `` `status: "resolved"` ``, `` `status: "unresolved"` ``, `` `agr_curation_query` ``,
  and each shared root field as `` `field` `` (request_id, validator_binding_id,
  validator_agent, target, resolved_values, resolved_objects,
  missing_expected_fields, candidates, lookup_attempts, curator_message,
  explanation) — all retained (GV-08/GV-28/GV-29/GV-30/GV-31). Forbidden:
  `under_development`, `mark_under_development`, `repair_action`, `extractor_patch`
  — none introduced.
- `test_gene_prompt_requires_shared_bulk_lookup_for_batch_requests`: requires
  `mode: "domain_validator_batch"`, "group compatible requests",
  `method: "search_genes_bulk"`, "gene_symbols: [...]", "Do not call",
  "separately for each request", "Map each returned bulk item back to the matching
  request" — all retained (GV-22/GV-23).
- `test_gene_prompt_uses_extractor_handoff_context_for_disambiguation`: requires
  `` `identity_resolution_notes` ``, "you do not", "request-level `evidence`
  records", "more specific paper-supported search phrase", "paper context for
  focused follow-up lookups" — all retained (GV-18/GV-23). Forbidden paper-specific
  strings (`Actin 5C`, `Opsin-1`, `Crumbs (Crb)`) — none introduced.

The schema-validation tests (`test_gene_and_allele_validator_schemas_expose_shared_root_fields`,
`test_gene_validator_accepts_resolved_shared_contract_payload`,
`test_gene_and_allele_schemas_reject_chat_era_summary_fields`) assert against the
`GeneResultEnvelope` model, not the prompt text, so they are unaffected. No
re-baseline was needed.

> **VALIDATOR-template note for the next 13 implementers + supervisor:**
> 1. A validator AUTHORS its `*ResultEnvelope` directly (output_schema set) — there
>    is NO builder/stage/finalize workflow and NO metadata-template rule; do not
>    import the extractor don't-stage rewrite.
> 2. The locked core already injects the required-tool-call policy + JSON-output
>    mandate + get_agent_contract pointer — de-dup those, keep one curator-facing
>    success line.
> 3. The Shared Result Contract field roster is load-bearing in the prompt (not
>    injected) — KEEP it; the per-field backticked tokens are asserted by the
>    validator contract tests.
> 4. The "Available <X> Methods" block is the main de-dup lever: relocate the
>    per-method which-method-when semantics (carried by bindings `methods.*`),
>    delete the pure internal mechanics, KEEP the literal-symbol-first +
>    method-choice JUDGMENT and any synonym worked example.
> 5. Validators have NO reason_codes (no `.reason_codes.txt`); the
>    `lookup_attempts[].outcome` enum is an outcome list, not exclusion reason codes.
> 6. Keep the never-assume-equivalence + literal-symbol-first CRITICAL rule and the
>    no-memory/no-guessing rule — these are the validator's true invariants.
