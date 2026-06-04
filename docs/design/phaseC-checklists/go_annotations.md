# Phase C semantic-coverage checklist: `go_annotations` lookup (Wave 3 — LOOKUP skeleton)

This is the **authoritative inventory source** for the outcome-first rewrite of
`packages/alliance/agents/go_annotations/prompt.yaml` (canonical agent id
`go_annotations_lookup`). Every load-bearing rule in the pre-rewrite prompt is listed
here with a stable ID (GOA-NN) and its new home in the rewritten prompt, OR an explicit,
justified relocation/deletion. The harness inventories
(`phase_c_inventories/go_annotations.txt`, `.invariants.txt`, `.dropped.json`) are
derived from this checklist.

## What `go_annotations` actually IS (role + output contract + skeleton choice)

`go_annotations` (canonical agent id `go_annotations_lookup`) is a **LOOKUP agent that
AUTHORS a result envelope directly**, not an extractor and not a builder. Verified
against the code:

- `packages/alliance/agents/go_annotations/agent.yaml` sets
  `output_schema: GOAnnotationsResult` (which extends `DomainValidatorResultBase`), the
  single tool `go_api_call`, and `group_rules_enabled: false`. So it hand-authors
  `GOAnnotationsResult` directly — it does NOT stage into a backend materializer.
- Its job is to **FETCH and RETURN the authoritative GO annotations** for a requested
  gene from the live GO API (QuickGO-backed `api.geneontology.org`), and return the
  shared validator result contract (`DomainValidatorResultBase` root fields) plus the
  GO-annotation-specific roots (`gene_id`, `gene_symbol`, `annotations`, `manual_count`,
  `automatic_count`).

So the rewrite uses the **LOOKUP skeleton** (role-adapted, outcome-first, mirroring the
`agm`/`ontology_term` lean validator exemplars):
`<role>` -> `<goal>` (success folded in) -> `<scope>` ->
`<resolution_and_evidence_rules>` -> `<lookup_workflow>` -> `<result_contract>` ->
`<stop_rules>`.

### Positive specialist framing (load-bearing, per Chris)

The lookup is framed as the **specialist that fetches and returns the authoritative GO
annotations** with the GO-API access and the final say on what annotations a gene
carries — "yours to fetch and return well, not hand back". The base prompt IS
curator-editable; it is written in curator voice for a biologist with no developer
background.

### NO group rules, NO batch protocol, NO search-mechanic relocation (verified)

- `agent.yaml` has `group_rules_enabled: false`, so there is **no group inventory** and
  no `.reason_codes.txt` (lookups carry no reason-code enum).
- `agent.yaml`'s `supervisor_routing.batchable: true` lets the supervisor combine
  gene requests, but the editable base never instructed a per-batch protocol. The
  rewrite does NOT invent one (faithful migration).
- The pre-rewrite prompt carried **NO** gene-style "matches by exact / then prefix /
  then contains, case-insensitive, across labels and synonyms" tool-search mechanic. The
  evidence-code precedence (manual vs automatic) is a confidence-ordering rule for
  presenting annotations, NOT a tool-search-order mechanic. So there is NO
  search-mechanic to relocate to a tool docstring/bindings summary; `bindings.yaml`,
  `rest.py`, and the tool catalog baseline are **untouched** by this rewrite.

---

## Template rules applied (Phase C — LOOKUP template)

### Template rule — no core duplication (de-dup lever: output-schema mandate)

`assembly.py::_build_compact_runtime_contract` already injects, for
`go_annotations_lookup` (verified by rendering
`build_agent_core_prompt('go_annotations_lookup')`):

- the **output contract**: "Output contract from agent.yaml: produce JSON matching
  GOAnnotationsResult; the structured-output layer below is authoritative for final
  response shape." PLUS the CRITICAL structured-output block ("Your final response MUST
  be valid JSON matching the GOAnnotationsResult schema EXACTLY").

The pre-rewrite BASE prompt restated this output mandate (`# Output` "Return a
`GOAnnotationsResult`" + `# Goal`); the rewrite removes the JSON-only restatement
(de-dup, recorded in `.dropped.json` as `relocated -> render`), but KEEPS the
`GOAnnotationsResult` token once in `<goal>` for curator readability and the
GO-annotation root-field detail in `<result_contract>` (the core does NOT enumerate
those fields).

### Template rule — required-tool-call: **KEPT in base (NOT injected by core)**

VERIFIED CRITICAL: `go_api_call` has **NO** `required_tool_call.enforce` metadata in
`packages/alliance/tools/bindings.yaml` (only `agr_curation_query` carries
`required_tool_call.enforce: true`). The generic resolver
`required_tool_names_for_available_tools(['go_api_call'])` therefore returns an EMPTY
set, and `_build_compact_runtime_contract` injects **NO** "Required tool-call policy"
line for this agent (confirmed by rendering the core: the Generated Runtime Contract
contains only the output-schema lines, no tool-call line). So the base "you must call
`go_api_call` before answering" imperative is **NOT** duplicated by the core and is
**KEPT** in the rewritten base prompt (it is the only place it appears).

### Template rule — Shared Result Contract: **LOAD-BEARING, KEPT**

VERIFIED: the shared validator root-field block is NOT injected by any shared prompt
layer — it is the ONLY place these fields are described for this agent. KEPT (wording
tightened, no field dropped). The four request-copy fields
(`request_id`/`validator_binding_id`/`validator_agent`/`target`) collapse to ONE line;
the GO-annotation-specific roots are retained.

---

## Role / goal / success (folded into goal)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| GOA-01 | Agent identity: a GO Annotation Specialist that retrieves actual Gene Ontology annotations for specific genes from the GO Consortium API. | `<role>` (reframed to curator-voice positive specialist: "fetches and returns the authoritative GO annotations", with GO-API access + final say) |
| GOA-02 | Goal: return current GO annotations connecting a requested gene to GO terms, including evidence codes and curation sources needed to judge confidence; author a `GOAnnotationsResult`. | `<goal>` (verbatim `GOAnnotationsResult` token retained once) |
| GOA-03 | Success (folded): uses `go_api_call` before providing any GO annotation facts. | `<goal>` + `<resolution_and_evidence_rules>` (KEPT as base imperative — NOT core-injected; see template rule above) |
| GOA-04 | Success (folded): queries `https://api.geneontology.org/api/bioentity/gene/{gene_id}/function` with a gene ID in Alliance format. | `<lookup_workflow>` |
| GOA-05 | Success (folded): extracts annotations from the API response `associations` array. | `<lookup_workflow>` + `<resolution_and_evidence_rules>` |
| GOA-06 | Success (folded): reports GO term IDs and names, evidence codes and labels, qualifiers when present, assigned_by sources, and manual versus automatic classification. | `<result_contract>` (annotation field detail) |
| GOA-07 | Success (folded): prioritizes manually curated annotations when summarizing heavily annotated genes. | `<resolution_and_evidence_rules>` (evidence-confidence ordering) |
| GOA-08 | Success (folded): populates `GOAnnotationsResult` fields only with values supported by the API response (no-invention). | `<resolution_and_evidence_rules>` |

## Domain context (GO terms vs GO annotations distinction)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| GOA-09 | Core distinction: GO terms are ontology vocabulary entries (e.g. `GO:0003677` DNA binding); GO annotations are evidence-backed links between a gene and a GO term ("Gene X has function/process/location Y, and here is the evidence"), carrying gene ID, GO term, evidence code, and assigned_by source. | `<scope>` (kept as the in-scope definition so the lookup knows it returns annotations, not bare terms) |

## Scope / no-transfer / supported inputs

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| GOA-10 | In scope: retrieving GO annotations for a specific gene ID; showing evidence codes and curation sources; distinguishing manual from automatic annotations; counting annotations by evidence type and aspect when the API provides enough data. | `<scope>` |
| GOA-11 | Out of scope: searching GO terms by keyword; finding GO term hierarchy/children/parents; searching genes by GO term ("what genes have kinase activity"); enrichment analysis. Do not transfer these or invoke another agent; state the scope limit and leave next-step selection to the supervisor/caller. | `<scope>` (no cross-agent transfer — LOOKUP-template discipline in curator voice) |
| GOA-12 | Required input: a `gene_id` in Alliance format (e.g. `WB:WBGene00000898`, `HGNC:11998`, `MGI:123456`, `FB:FBgn0000490`, `SGD:...`, `RGD:...`, `ZFIN:...`). | `<scope>` (the input the lookup reads) |

## Resolution & evidence rules

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| GOA-13 | All GO annotation facts must come from the live GO API response. Do not answer from memory, training data, examples, or assumptions. | `<resolution_and_evidence_rules>` (no-invention) |
| GOA-14 | Manual/experimental evidence is higher confidence: IDA/ECO:0000314 (direct assay), IMP/ECO:0000315 (mutant phenotype), IPI/ECO:0000353 (physical interaction), IGI (genetic interaction), ISS/ECO:0000250 (manual sequence similarity), and IEP/TAS/NAS/IC and similar curator-supported evidence. | `<resolution_and_evidence_rules>` (evidence-code confidence — manual set) |
| GOA-15 | Automatic/computational evidence is lower confidence: IEA/ECO:0000501 (electronic, no curator review), IBA/ECO:0000318 (PAINT phylogenetic inference), RCA (reviewed computational analysis), ISO/ISA/ISM and similar computational inferences. | `<resolution_and_evidence_rules>` (evidence-code confidence — automatic set) |
| GOA-16 | When summarizing genes with many annotations, lead with manual/experimental annotations; keep automatic annotations visible in counts and representative details; call out PAINT/IBA annotations as phylogenetic inferences. | `<resolution_and_evidence_rules>` (presentation ordering) |
| GOA-17 | Cite evidence codes in annotation summaries so users can judge confidence. Keep field values grounded in the API result; leave optional fields empty when the API does not provide them. | `<resolution_and_evidence_rules>` + `<result_contract>` |

## Lookup workflow (API usage + bounded path)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| GOA-18 | Use `go_api_call` for every request about GO annotations. API base `https://api.geneontology.org/api`; primary endpoint `GET /bioentity/gene/{gene_id}/function` (full example `https://api.geneontology.org/api/bioentity/gene/WB:WBGene00000898/function`). | `<lookup_workflow>` (the GO-API usage — `go_api_call` token + endpoint) |
| GOA-19 | Expected response: JSON with an `associations` array of GO annotations; each association may include `object.id`, `object.label`, `evidence_types`, `assigned_by`, and `qualifiers`. | `<lookup_workflow>` (response-shape facts) |
| GOA-20 | Bounded path / stop: record each GO API request in `lookup_attempts` with outcome `success`, `not_found`, `ambiguous`, or `error`; do not continue beyond the bounded investigation needed to populate the shared result fields and GO annotation facts. | `<lookup_workflow>` + `<stop_rules>` |

## Result contract (GOAnnotationsResult — model-authored shared validator contract)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| GOA-21 | Return only the shared validator statuses: `status: "resolved"` / `status: "unresolved"`. Populate shared root fields directly; do not wrap under `result`, `validation_result`, or another wrapper: `status`, `request_id`, `validator_binding_id`, `validator_agent`, `target`, `resolved_values`, `resolved_objects`, `missing_expected_fields`, `candidates`, `lookup_attempts`, `curator_message`, `explanation`. (Four request-copy fields collapsed to one line.) | `<result_contract>` (verbatim status + backticked root-field tokens) |
| GOA-22 | Required GO-annotation field: `gene_id` (the queried gene ID in Alliance format). | `<result_contract>` |
| GOA-23 | Include when available from the API: `gene_symbol`; `annotations` entries with `go_id`, `go_name`, `aspect` (`MF`/`BP`/`CC`), `evidence_code` (GO evidence code or ECO id), `evidence_label`, `assigned_by`, `is_manual`, `qualifier`; `manual_count`; `automatic_count`. | `<result_contract>` |
| GOA-24 | Bounded validator handling: keep lookup responsibility separate from extraction (report API-grounded annotation facts, missing annotation evidence, transient service failure, or ambiguity; do not propose free-form envelope edits). For a true missing/empty result, return the queried gene ID with an empty annotation list and zero counts, add the expected output field to `missing_expected_fields`, and return `status: "unresolved"`; if the GO API fails transiently after the bounded retry path, return `status: "unresolved"` and explain the service issue in `explanation`. | `<result_contract>` (folds the bounded-validator-lookup block + the no-associations stop) |

## Stop / abstain rules

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| GOA-25 | If the user has not provided a usable Alliance-format gene ID, ask for the smallest missing correction before querying. | `<stop_rules>` |
| GOA-26 | If the request is for GO term search, hierarchy, gene-by-term search, or enrichment analysis, explain that this agent does not perform that task; do not transfer it or invoke another agent, and leave next-step selection to the supervisor/caller. | `<stop_rules>` (merged with GOA-11) |
| GOA-27 | If the GO API returns no associations, return the queried `gene_id`, an empty annotations list, zero counts, and a concise note that no GO annotations were found in the API response. | `<stop_rules>` (folds into GOA-24's empty-result handling) |
| GOA-28 | If the GO API call fails or returns unusable data, report the lookup blocker and do not invent annotations. | `<stop_rules>` |

## CORE-injected (no base restatement)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| GOA-OUT | Output mandate: produce JSON matching `GOAnnotationsResult`; the structured-output layer is authoritative for final response shape. | CORE (`render`). The base keeps the `GOAnnotationsResult` token once but does not restate the JSON-only output mandate. |

> NOTE: there is NO `GOA-RTC` core-injected required-tool-call entry. Unlike the
> `agr_curation_query`-bound validators, `go_api_call` has no `required_tool_call`
> metadata, so the core injects no tool-call policy line; the base prompt KEEPS its
> "call `go_api_call` before any GO annotation facts" imperative (GOA-03).

---

## De-dup summary (the go_annotations-lookup Phase-C levers)

1. **CORE de-dup (output only):** the JSON-output mandate (`# Output` "Return a
   `GOAnnotationsResult`") is relocated to the locked core (kept as the
   `GOAnnotationsResult` token once + the GO-annotation root-field detail).
2. **Required-tool-call NOT de-dupped:** `go_api_call` is not core-enforced, so the base
   keeps the tool-call imperative.
3. **Shared Result Contract:** verified NOT injected by a shared layer; KEPT
   (load-bearing), wording tightened, no field dropped.
4. **Consolidation:** `# Validator role`, `# Success criteria`, `# Core distinction`,
   `# Evidence rules`, `# Tool and API instructions`, `# Output`, `# Bounded validator
   lookup`, `# Stop and abstain rules` consolidate into the lean skeleton without losing
   a rule.
5. **NO search-mechanic relocation, NO group rules, NO reason codes, NO batch protocol:**
   the prompt never carried those; `bindings.yaml`/`rest.py`/tool-catalog baseline are
   untouched.

## Contract-test coverage

**No dedicated prompt-content contract test exists for `go_annotations`.** The
references to `go_api_call`/`go_annotations` in the test suite are tool-name allowlists
and config-loader fixtures (`test_domain_envelope_repair_prompt_contract.py` lists
`go_api_call` among tools FORBIDDEN to extractors — `go_annotations` is a lookup, not an
extractor, so it is unaffected; `test_config_loaders.py` uses `go_annotations` only as a
folder-name fixture). No prompt-text assertion is edited, deleted, or weakened by this
rewrite. The only guards over this base prompt are the Phase C retention/invariant/
dropped-list harness seeded by this checklist.
