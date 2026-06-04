# Phase C semantic-coverage checklist: `orthologs` lookup (Wave 3 — LOOKUP skeleton)

This is the **authoritative inventory source** for the outcome-first rewrite of
`packages/alliance/agents/orthologs/prompt.yaml` (canonical agent id `orthologs_lookup`).
Every load-bearing rule in the pre-rewrite prompt is listed here with a stable ID
(ORT-NN) and its new home in the rewritten prompt, OR an explicit, justified
relocation/deletion. The harness inventories (`phase_c_inventories/orthologs.txt`,
`.invariants.txt`, `.dropped.json`) are derived from this checklist.

## What `orthologs` actually IS (role + output contract + skeleton choice)

`orthologs` (canonical agent id `orthologs_lookup`) is a **LOOKUP agent that AUTHORS a
result envelope directly**, not an extractor and not a builder. Verified against the
code:

- `packages/alliance/agents/orthologs/agent.yaml` sets `output_schema: OrthologsResult`
  (which extends `DomainValidatorResultBase`), the single tool `alliance_api_call`, and
  `group_rules_enabled: false`. So it hand-authors `OrthologsResult` directly — it does
  NOT stage into a backend materializer.
- Its job is to **FETCH and RETURN the authoritative cross-species ortholog
  relationships** for a requested Alliance gene from the Alliance Orthology API, and
  return the shared validator result contract (`DomainValidatorResultBase` root fields)
  plus the ortholog-specific roots (`query_gene`, `orthologs`, `high_confidence_count`,
  `species_represented`).

So the rewrite uses the **LOOKUP skeleton** (role-adapted, outcome-first, mirroring the
`agm`/`ontology_term` lean exemplars):
`<role>` -> `<goal>` (success folded in) -> `<scope>` ->
`<resolution_and_evidence_rules>` -> `<lookup_workflow>` -> `<result_contract>` ->
`<stop_rules>`.

### Positive specialist framing (load-bearing, per Chris)

The lookup is framed as the **specialist that fetches and returns the authoritative
orthologs** with the Alliance Orthology API access and the final say on a gene's
cross-species counterparts — "yours to fetch and return well, not hand back". The base
prompt IS curator-editable; it is written in curator voice for a biologist with no
developer background.

### NO group rules, NO batch protocol, NO search-mechanic relocation (verified)

- `agent.yaml` has `group_rules_enabled: false`, so there is **no group inventory** and
  no `.reason_codes.txt` (lookups carry no reason-code enum).
- `agent.yaml`'s `supervisor_routing.batchable: true` lets the supervisor combine gene
  requests, but the editable base never instructed a per-batch protocol. The rewrite
  does NOT invent one (faithful migration).
- The pre-rewrite prompt carried **NO** gene-style "matches by exact / then prefix /
  then contains, case-insensitive, across labels and synonyms" tool-search mechanic. The
  confidence levels (high/moderate/low) and `isBestScore` handling are
  response-interpretation rules, NOT a tool-search-order mechanic. So there is NO
  search-mechanic to relocate; `bindings.yaml`, `rest.py`, and the tool catalog baseline
  are **untouched** by this rewrite.

---

## Template rules applied (Phase C — LOOKUP template)

### Template rule — no core duplication (de-dup lever: output-schema mandate)

`assembly.py::_build_compact_runtime_contract` already injects, for `orthologs_lookup`
(verified by rendering `build_agent_core_prompt('orthologs_lookup')`):

- the **output contract**: "Output contract from agent.yaml: produce JSON matching
  OrthologsResult; the structured-output layer below is authoritative for final response
  shape." PLUS the CRITICAL structured-output block ("Your final response MUST be valid
  JSON matching the OrthologsResult schema EXACTLY").

The pre-rewrite BASE prompt restated this output mandate (`<output>` "Your response is
structured as an OrthologsResult object"); the rewrite removes the JSON-only restatement
(de-dup, recorded in `.dropped.json` as `relocated -> render`), but KEEPS the
`OrthologsResult` token once in `<goal>` and the ortholog root-field detail in
`<result_contract>` (the core does NOT enumerate those fields).

### Template rule — required-tool-call: **KEPT in base (NOT injected by core)**

VERIFIED CRITICAL: `alliance_api_call` has **NO** `required_tool_call.enforce` metadata
in `packages/alliance/tools/bindings.yaml` (only `agr_curation_query` carries
`required_tool_call.enforce: true`). The generic resolver
`required_tool_names_for_available_tools(['alliance_api_call'])` therefore returns an
EMPTY set, and `_build_compact_runtime_contract` injects **NO** "Required tool-call
policy" line for this agent (confirmed by rendering the core: the Generated Runtime
Contract contains only the output-schema lines, no tool-call line). So the base "you
MUST call the alliance_api_call tool before any response about orthologs" imperative is
**NOT** duplicated by the core and is **KEPT** in the rewritten base prompt (it is the
only place it appears).

### Template rule — Shared Result Contract: **LOAD-BEARING, KEPT**

VERIFIED: the shared validator root-field block is NOT injected by any shared prompt
layer — it is the ONLY place these fields are described for this agent. KEPT (wording
tightened, no field dropped). The four request-copy fields
(`request_id`/`validator_binding_id`/`validator_agent`/`target`) collapse to ONE line;
the ortholog-specific roots are retained.

---

## Role / goal / success (folded into goal)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| ORT-01 | Agent identity: an Orthology Query Specialist for the Alliance of Genome Resources; retrieves cross-species ortholog relationships for Alliance gene IDs (human, mouse, fly, worm, zebrafish, yeast, rat orthologs when available). | `<role>` (reframed to curator-voice positive specialist: "fetches and returns the authoritative orthologs", with Alliance Orthology API access + final say) |
| ORT-02 | Goal: return evidence-backed orthology data from the Alliance Orthology API — the queried gene, ortholog genes, species, confidence levels, best-score status, and prediction algorithms that matched or did not match; author an `OrthologsResult`. | `<goal>` (verbatim `OrthologsResult` token retained once) |
| ORT-03 | Success (folded): uses Alliance API results rather than memory or training data. | `<resolution_and_evidence_rules>` (no-invention) |
| ORT-04 | Success (folded): identifies the query gene and each returned ortholog with gene ID, symbol, species, and data provider when available. | `<result_contract>` (field detail) |
| ORT-05 | Success (folded): preserves confidence values as "high", "moderate", or "low". | `<resolution_and_evidence_rules>` (confidence-preservation) |
| ORT-06 | Success (folded): treats "isBestScore: Yes" as the best-scoring ortholog in that species. | `<resolution_and_evidence_rules>` |
| ORT-07 | Success (folded): lists prediction methods that support and do not support each orthology relationship when available. | `<resolution_and_evidence_rules>` + `<result_contract>` |
| ORT-08 | Success (folded): reports "no orthologs found" when the API returns an empty result set; that is valid data, not an error. | `<resolution_and_evidence_rules>` + `<result_contract>` (empty-result handling) |

## Domain context (orthology + confidence + methods)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| ORT-09 | Confidence levels: High (most algorithms agree, most reliable), Moderate (some agree), Low (few agree, less certain). | `<resolution_and_evidence_rules>` (the confidence interpretation; combined with ORT-05) |
| ORT-10 | Common prediction methods include Ensembl Compara, InParanoid, OMA, OrthoFinder, OrthoInspector, PANTHER, PhylomeDB, SonicParanoid, ZFIN, Hieranoid, and Xenbase. | DELETED — pure reference list of method names with no decision rule; the agent reports whatever methods the API returns under `predictionMethodsMatched`/`predictionMethodsNotMatched`, so enumerating known method names changes no behavior. Recorded in `.dropped.json` as `deleted`. (The orthology definition prose — "genes in different species that evolved from a common ancestral gene" — is likewise narrative; the curator-voice `<role>`/`<goal>` already convey "cross-species counterparts", so it is not separately inventoried.) |

## Scope / no-transfer / supported inputs

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| ORT-11 | Required input: gene ID in Alliance format with a prefix — WB (C. elegans) `WB:WBGene00000898`, Human (HGNC) `HGNC:11998`, Mouse (MGI) `MGI:97490`, Fly (FlyBase) `FB:FBgn0000014`, Zebrafish (ZFIN) `ZFIN:ZDB-GENE-...`, Yeast (SGD) `SGD:S000001855`, Rat (RGD) `RGD:2001`. | `<scope>` (the input the lookup reads) |
| ORT-12 | Symbol alone will not work; this agent requires Alliance-format IDs. If only a symbol is available, state that ID resolution must happen before this agent can run; do not guess or construct IDs from symbols. | `<scope>` (input-requirement + no-guess-IDs) |
| ORT-13 | Out of scope: search for genes by symbol, get GO annotations, get disease associations, find paralogs, build phylogenetic trees, compare protein sequences or domains. For an out-of-scope request, do not transfer work or invoke another agent; state the scope limit and leave next-step selection to the supervisor/caller. | `<scope>` (no cross-agent transfer — LOOKUP-template discipline in curator voice) |

## Resolution & evidence rules

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| ORT-14 | All field values must come from Alliance API results, never from memory; do not answer from memory or training data, guess ortholog relationships, or provide orthology information without querying the API. | `<resolution_and_evidence_rules>` (no-invention) |
| ORT-15 | Response parsing: parse the "results" array from the ortholog endpoint; each relationship is nested under `geneToGeneOrthologyGenerated` with `subjectGene` (original gene), `objectGene` (ortholog gene), `confidence.name` ("high"/"moderate"/"low"), `isBestScore.name` ("Yes"/"No"), `predictionMethodsMatched` (algorithms that agree), `predictionMethodsNotMatched` (algorithms that disagree). | `<resolution_and_evidence_rules>` (response-shape interpretation; carries the confidence/best-score/methods tokens) |
| ORT-16 | For curation context, note high-confidence orthologs and especially human orthologs when those relationships are present in the Alliance API response. Do not compare annotations, disease relevance, or curation gaps unless those fields are explicitly present in the orthology API result. | `<resolution_and_evidence_rules>` (curation-context guidance + stay-in-data) |

## Lookup workflow (API usage + bounded path)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| ORT-17 | You MUST call the `alliance_api_call` tool before providing any response about orthologs. Use full Alliance API URLs: orthologs `https://www.alliancegenome.org/api/gene/{gene_id}/orthologs`; gene details (only when needed to fill missing query-gene metadata) `https://www.alliancegenome.org/api/gene/{gene_id}`. | `<lookup_workflow>` (the Alliance ortholog API usage — `alliance_api_call` token + endpoints; KEPT as base imperative, NOT core-injected) |
| ORT-18 | Prefer the minimum API calls needed to answer correctly; after the ortholog endpoint returns enough data for the requested gene, stop gathering evidence and populate the result. | `<lookup_workflow>` + `<stop_rules>` (bounded path) |
| ORT-19 | Record each Alliance API request in `lookup_attempts`, with outcome `success`, `not_found`, `ambiguous`, or `error`. | `<lookup_workflow>` + `<result_contract>` |

## Result contract (OrthologsResult — model-authored shared validator contract)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| ORT-20 | Return only the shared validator statuses: `status: "resolved"` / `status: "unresolved"`. Populate shared root fields directly; do not wrap under `result`, `validation_result`, or another wrapper: `status`, `request_id`, `validator_binding_id`, `validator_agent`, `target`, `resolved_values`, `resolved_objects`, `missing_expected_fields`, `candidates`, `lookup_attempts`, `curator_message`, `explanation`. (Four request-copy fields collapsed to one line.) | `<result_contract>` (verbatim status + backticked root-field tokens) |
| ORT-21 | Required ortholog field: `query_gene` `{gene_id, symbol, species, data_provider}`. | `<result_contract>` |
| ORT-22 | Include when available: `orthologs` (each with ortholog `{gene_id, symbol, species, data_provider}`, confidence, is_best_score, methods_matched, methods_not_matched); `high_confidence_count` (number of high-confidence orthologs); `species_represented` (species with orthologs found). | `<result_contract>` |
| ORT-23 | Bounded validator handling: keep lookup responsibility separate from extraction (report API-grounded orthology facts, empty ortholog results, transient service failure, or ambiguity; do not propose free-form envelope edits). For a true empty ortholog result, preserve the empty result, add the expected output field to `missing_expected_fields` when the binding expected a relationship, and return `status: "unresolved"`. If Alliance API access fails transiently after the bounded retry path, return `status: "unresolved"` and explain the service issue in `explanation`. | `<result_contract>` (folds the bounded-validator-lookup block) |

## Stop rules

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| ORT-24 | Preserve empty ortholog results as "no orthologs found" (valid data, not an error). | `<stop_rules>` (folds ORT-08/ORT-23 empty-result handling) |
| ORT-25 | If only a symbol is available, state that ID resolution must happen before this agent can run; do not guess or construct IDs from symbols. | `<stop_rules>` (folds ORT-12) |
| ORT-26 | If the Alliance API call fails or returns unusable data, report the lookup blocker and do not invent ortholog relationships. | `<stop_rules>` (derived from ORT-14 no-invention + ORT-23 transient-failure) |

## CORE-injected (no base restatement)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| ORT-OUT | Output mandate: produce JSON matching `OrthologsResult`; the structured-output layer is authoritative for final response shape. | CORE (`render`). The base keeps the `OrthologsResult` token once but does not restate the JSON-only output mandate. |

> NOTE: there is NO `ORT-RTC` core-injected required-tool-call entry. Unlike the
> `agr_curation_query`-bound validators, `alliance_api_call` has no `required_tool_call`
> metadata, so the core injects no tool-call policy line; the base prompt KEEPS its "you
> MUST call the `alliance_api_call` tool before any response about orthologs" imperative
> (ORT-17).

---

## De-dup summary (the orthologs-lookup Phase-C levers)

1. **CORE de-dup (output only):** the JSON-output mandate (`<output>` "Your response is
   structured as an OrthologsResult object") is relocated to the locked core (kept as the
   `OrthologsResult` token once + the ortholog root-field detail).
2. **Required-tool-call NOT de-dupped:** `alliance_api_call` is not core-enforced, so the
   base keeps the tool-call imperative.
3. **Shared Result Contract:** verified NOT injected by a shared layer; KEPT
   (load-bearing), wording tightened, no field dropped.
4. **Consolidation:** `<validator_role>`, `<success_criteria>`, `<domain_context>`,
   `<evidence_and_tool_rules>`, `<response_rules>`, `<output>`,
   `<bounded_validator_lookup>` consolidate into the lean skeleton without losing a rule.
5. **One DELETE (ORT-10):** the prediction-method NAME reference list (Ensembl Compara,
   InParanoid, OMA, ...) is dropped — it is a pure reference list with no decision rule;
   the agent reports whatever methods the API returns. Recorded in `.dropped.json` as
   `deleted` and printed for review.
6. **NO search-mechanic relocation, NO group rules, NO reason codes, NO batch protocol:**
   the prompt never carried those; `bindings.yaml`/`rest.py`/tool-catalog baseline are
   untouched.

## Contract-test coverage

**No dedicated prompt-content contract test exists for `orthologs`.** The references to
`alliance_api_call`/`orthologs` in the test suite are tool-name allowlists and
project-agnostic guardrail regexes (`test_domain_envelope_repair_prompt_contract.py`
lists `alliance_api_call` among tools FORBIDDEN to extractors — `orthologs` is a lookup,
not an extractor, so it is unaffected; `test_project_agnostic_runtime_guardrails.py`
matches `alliance_api_call` as a tool name, not prompt text). No prompt-text assertion is
edited, deleted, or weakened by this rewrite. The only guards over this base prompt are
the Phase C retention/invariant/dropped-list harness seeded by this checklist.
