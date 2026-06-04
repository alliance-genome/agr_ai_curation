# Phase C semantic-coverage checklist: `gene_extractor` (Wave 1 pilot)

This is the **authoritative inventory source** for the outcome-first rewrite of
`packages/alliance/agents/gene_extractor/prompt.yaml`. Every load-bearing rule in
the pre-rewrite prompt is listed here with a stable ID (GE-NN) and its new home
in the rewritten prompt, OR an explicit, justified relocation/deletion. The
harness inventories (`phase_c_inventories/gene_extractor.txt`, `.fb.txt`,
`.invariants.txt`, `.reason_codes.txt`, `.dropped.json`) are derived from this
checklist.

Legend for "New home":
- A `<section>` name is a section of the **rewritten** base prompt.
- `RELOCATED -> <home>` means the rule's *fact* lives elsewhere on the production
  path (a tool description in `bindings.yaml`, the locked core prompt, or
  `get_agent_contract`); the base prompt no longer restates it. Recorded in
  `.dropped.json` as `relocated` with a machine-checked `new_home`.
- `DELETED` means the sentence is redundant/inaccurate and is dropped with no
  home. Recorded in `.dropped.json` as `deleted` (printed in review).
- `CORE` means the locked Generated Runtime Contract already asserts this; the
  base prompt keeps the curator-facing curation rule but does not own the
  machine contract.

## Skeleton mapping (rewritten prompt sections)

`<role>` -> `<goal>` -> `<success_criteria>` -> `<evidence_and_curation_rules>`
(candidate/focality + evidence include/exclude + exclusion codes + central-focus
efficiency, collapsed) -> `<validator_handoff>` (validator authority + handoff
channel + lookup restriction) -> `<workflow>` (ordered builder tool-loop, the
one place exact path matters) -> `<output_and_handoff_contract>` (field shape +
builder finalize + envelope-forbidden lists + ack) -> `<examples>` (two few-shot
examples, preserved) -> `<stop_rules>`.

---

## Post-review must-fixes (Codex gpt-5.5 gate, judging the prompt as the running model)

Two ambiguities the rewrite exposed were sharpened (no rule lost). These are
**template rules** for the other builder extractors.

### Must-fix 1 — Staging-cardinality correction (GE-10)

The unit staged once is the **gene/evidence pairing**, NOT the gene. One gene with
two distinct retained quotes is two pairings -> two `stage_gene_mention_evidence`
calls (this is exactly what the contract test's
`stage_gene_mention_evidence( == 3` across the two few-shots requires, and what the
builder materializer does: one `gene_mention_evidence` object per (candidate,
evidence) pairing — `conversion.py` ~L797). The old success criterion "each
retained gene is staged exactly once" contradicted `<workflow>`/the examples and
was wrong. Reworded in `<success_criteria>` and kept consistent with `<workflow>`
step 4. The examples are unchanged (the `== 3` count holds).

### Must-fix 2 — Metadata exclusions/ambiguities mechanism (real mechanism, verified)

**Verified against the code** (`gene_builder_tools.py` + `domain_packs/gene/conversion.py::materialize_gene_builder_state`):

- **The model NEVER authors the envelope, including `metadata.*`.** `finalize_gene_extraction`
  -> `finalize_builder_extraction(materialize=_materialize_gene_with_events)` ->
  `materialize_gene_builder_state`, which builds the ENTIRE
  `GeneExtractionResultEnvelope` (curatable_objects + metadata) from the staged
  candidates and the recorded-evidence snapshot.
- **There is NO model-facing channel for exclusions or ambiguities.** The stage
  tool input (`GeneStageInput`) accepts only retained-gene fields — no exclusion
  param, no reason_code param, no ambiguity param. The materializer **hard-codes**
  `metadata.exclusions: []`, `metadata.ambiguities: []`, `metadata.notes: []`, and
  `run_summary.excluded_count/ambiguous_count: 0` (`conversion.py` L865-875). So
  exclusions/ambiguities never reach the envelope through the builder tools.
- **An exclusion/ambiguity decision is expressed by NOT staging the candidate**
  (or `discard_gene_mention_evidence` for one already staged — which takes a
  free-text `reason`). `metadata.evidence_records[]` does include every non-discarded
  recorded-evidence record (`get_active_evidence_records_snapshot()`), but an
  evidence record for a non-staged gene is just an unreferenced record, not an
  exclusion.

The pre-rewrite prompt (inherited from the envelope era; phenotype_extractor still
carries the same latent contradiction) told the model to "put excluded genes in
`metadata.exclusions[]`" / "ambiguous ones in `metadata.ambiguities[]`" while ALSO
saying "do not emit `metadata.*`" and "leave excluded/ambiguous mentions unstaged."
That is contradictory to the running model. The rewrite removes the surface
contradiction by stating the real mechanism plainly (you never type `metadata.*`;
exclude by not staging; reason codes name your exclusion/discard reasoning and the
`discard` reason) while keeping the curation intent (genuinely uncertain-IDENTITY
focal genes are STILL staged with `proposed_*` unset for the validator, so they are
not silently dropped). Affected entries reworded: GE-13, GE-32, GE-53, GE-55,
GE-56, GE-59, GE-60, GE-77. No count/ordering assertion in the contract test was
touched (verified: all phrases/counts still hold).

---

## Role / goal / success

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| GE-01 | Agent identity: "Gene Extraction Agent for biological curation at the Alliance of Genome Resources." | `<role>` (retained verbatim) |
| GE-02 | Goal: identify genes focal to the curator's question AND directly supported by experimental data in this paper; stage evidence-backed findings via builder tools. | `<goal>` |
| GE-03 | No-hand-author rule: "Do not hand-author `curatable_objects[]`; the backend builds GeneExtractionResultEnvelope from staged builder state." | `<goal>` (retained verbatim — also a CORE/schema guarantee) |
| GE-04 | Evidence is backend-verified source text from `read_chunk.evidence_spans[].span_id` values; not a summary, not a paper-gene association write target. | `<goal>` (kept; the span-id mechanic is the load-bearing half) |
| GE-05 | Must use document retrieval before answering. | `<success_criteria>` + `<workflow>` |
| GE-06 | Retained genes are individual gene entities, not families, pathways, reagents, protein complexes, authors, or bibliography-only strings. | `<success_criteria>` + `<evidence_and_curation_rules>` |
| GE-07 | Each retained gene is supported by direct data from THIS paper (mutant phenotype, expression assay, perturbation, interaction, rescue, localization, regulation, functional characterization). | `<success_criteria>` + `<evidence_and_curation_rules>` |
| GE-08 | Every evidence record is created from backend-generated `read_chunk.evidence_spans[].span_id` values. | `<success_criteria>` (verbatim retained — cross-cutting contract token) |
| GE-09 | Retained genes carry evidence-backed identity hints only; active validator bindings own final `primary_external_id`, `gene_symbol`, and `taxon` decisions. | `<success_criteria>` + `<validator_handoff>` (verbatim retained) |
| GE-10 | Each retained gene/EVIDENCE PAIRING is staged once with `stage_gene_mention_evidence` after `record_evidence(span_ids=[...])`; one gene with two distinct retained quotes is two pairings = two stage calls. | `<success_criteria>` + `<workflow>` (REWORDED, must-fix 1: see "Staging-cardinality correction" below) |
| GE-11 | `finalize_gene_extraction` is called exactly once after all retained, excluded, and ambiguous candidates are accounted for. | `<success_criteria>` + `<workflow>` (verbatim "called exactly once") |
| GE-12 | Final response is only the small `ExtractionToolFinalizationAck`; backend builds objects/metadata. | `<success_criteria>` + `<output_and_handoff_contract>` |
| GE-13 | The model never authors the envelope, including `metadata.*`; the backend materializes objects, evidence records, and metadata from staged candidates + recorded evidence. | `<success_criteria>` + `<output_and_handoff_contract>` (REWORDED, must-fix 2: the old "exclusions/ambiguities are preserved as `metadata.*` audit you write" framing was inaccurate for the builder pattern — see "Metadata mechanism correction" below) |

## `<search_context>` block (DROPPED / RELOCATED)

The entire `<search_context>` block is removed per the Phase C extractor
skeleton. Search-backend facts already live in the `search_document` /
`read_chunk` / `read_section` / `read_subsection` tool descriptions on the
production path; the inaccurate size claim is deleted.

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| GE-14 | Search backend is hybrid (semantic + BM25) under `search_mode="auto"`. | RELOCATED -> `bindings:search_document` (tool summary: "default blended search combines meaning-based and exact-word matching") |
| GE-15 | Pass `search_mode="lexical"` for exact gene symbols, IDs, strains, alleles, probes, reagents, etc. | RELOCATED -> `bindings:search_document` (synonym: "Exact-word matching is best for specific gene symbols, strains, alleles, probes, or identifiers") |
| GE-16 | `section_keywords` restricts search to specific sections. | RELOCATED -> `bindings:search_document` (synonym: "can be limited to particular sections") |
| GE-17 | `read_section`/`read_subsection` retrieve ALL chunks under a named section via LLM-resolved hierarchy (full coverage, not page order). | RELOCATED -> `bindings:read_section` / `bindings:read_subsection` (summaries: "complete text of every passage in a section, grouped by the paper's own structure rather than by page order") |
| GE-18 | For retained evidence, `read_chunk(chunk_id)` then select backend-generated `evidence_spans[].span_id`; do not write quote text yourself. | RELOCATED -> `bindings:read_chunk` + KEPT as curation guidance in `<evidence_and_curation_rules>`/`<workflow>` (the span-id selection mechanic is curation discipline, not just a tool fact) |
| GE-19 | Multiple `span_ids` in one `record_evidence` call -> one evidence record; separate calls for disjoint evidence units. | KEPT in `<evidence_and_curation_rules>` as "directly support one evidence unit" (the `record_evidence` summary in bindings restates the one-call-one-record fact; the curation guidance to use separate calls for disjoint units is retained). Cross-cutting test requires the literal token "evidence unit". |
| GE-20 | "Content is returned up to ~1500 characters per chunk hit for search, but read tools return full chunk text." | DELETED (the ~1500-char figure is an inaccurate, brittle, non-contract implementation detail — explicitly dropped, not relocated) |
| GE-21 | Short-query lexical auto-boost; cross-encoder rerank + MMR diversification. | DELETED (internal ranking mechanics, not a curation rule; the curator-facing "very short queries automatically lean on exact-word matching" already lives in `bindings:search_document`) |

## Tools / ordered workflow (the one place exact path matters)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| GE-22 | Must call document retrieval tools before answering. | `<workflow>` step 1 (also GE-05) |
| GE-23 | Workflow order: search/read -> provisional focal shortlist -> read_chunk + record_evidence -> stage -> list/patch/discard -> finalize once. | `<workflow>` (ordered list; the invariants file pins this order) |
| GE-24 | For central-focus questions, build a short provisional list of focal genes BEFORE selecting evidence; do not record every gene from a broad search. | `<workflow>` + `<evidence_and_curation_rules>` (verbatim "build a short provisional list of focal genes before selecting evidence") |
| GE-25 | `stage_gene_mention_evidence` passes: verified `evidence_record_ids`, stable `pending_ref_id`, paper `mention`, `confidence`, 1-3 `identity_resolution_notes`, species/taxon/provider hints. | `<workflow>` + `<output_and_handoff_contract>` |
| GE-26 | `list_staged_gene_mention_evidence` to review, `patch_`/`discard_` to fix, then `finalize_gene_extraction(candidate_ids=[...])` once with the staged candidate IDs. | `<workflow>` (verbatim "Call `list_staged_gene_mention_evidence` to review") |
| GE-27 | Tool roster (search_document, read_section/subsection, read_chunk, record_evidence, list/get_recorded_evidence, attach/detach_evidence_to_object, discard_recorded_evidence, update_recorded_evidence_metadata, agr_species_context_lookup, stage/patch/discard/list_staged_gene_mention_evidence, finalize_gene_extraction). | `<workflow>` (compact roster retained; one line per builder verb) |

## Validator authority / handoff / lookup restriction

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| GE-28 | Active validator bindings declared by the gene domain pack are the authority for final normalized gene identity fields covered by validation. | `<validator_handoff>` (verbatim retained) |
| GE-29 | The concrete active gene validator binding identity (`alliance_gene_reference_lookup`). | RELOCATED -> `contract:gene_extractor:validator_bindings` (the base prompt keeps the rule; the binding id is owned by the contract) |
| GE-30 | Extraction-time lookup is limited to provider/species/taxon context via `agr_species_context_lookup`. | `<validator_handoff>` (verbatim "agr_species_context_lookup" retained — cross-cutting test token) |
| GE-31 | Do not search gene names, gene symbols, gene synonyms, gene IDs, or generic entity-to-CURIE mappings. | `<validator_handoff>` (verbatim retained) |
| GE-32 | When a gene is focal/supported but its identity is uncertain, still stage it with evidence + paper-backed hints, leaving `proposed_*` unset; the validator resolves it and any unresolved outcome becomes an envelope validation finding for curator review. Only a mention that is not clearly a single gene is left unstaged. | `<validator_handoff>` + `<stop_rules>` (REWORDED, must-fix 2) |
| GE-33 | The active gene validator is a separate, context-naive LLM agent with database lookup tools; it has no paper access unless you pass context through the envelope. | `<validator_handoff>` (verbatim "context-naive LLM agent" retained — cross-cutting test token) |
| GE-34 | Treat `payload.identity_resolution_notes` as a high-value handoff channel; use concise, paper-backed notes to preserve aliases/symbols/full names/locus labels, organism/strain/provider/taxon clues, short quote/sentence context, figure/table/assay/mutant/phenotype/interaction/expression context, and disambiguation rationale. | `<validator_handoff>` (verbatim "high-value handoff channel" + "`payload.identity_resolution_notes`" retained — test tokens) |
| GE-35 | For `payload.mention`, preserve the most specific paper-backed gene/protein label useful as a database lookup phrase; do not collapse to a broader family/pathway/reagent/generic protein label. | `<validator_handoff>` (verbatim "most specific paper-backed gene or protein" + "useful as a database lookup" retained — test tokens) |
| GE-36 | Never invent symbols, identifiers, taxa, species, or quote text; only paper-supported hints in the handoff. | `<validator_handoff>` + `<stop_rules>` |

## Candidate / focality / false-positive filtering

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| GE-37 | Search titles, abstracts, results, figure legends, tables, relevant methods/discussion for symbols/full names/synonyms; capture mentions exactly as written (case, hyphens, punctuation). | `<evidence_and_curation_rules>` |
| GE-38 | Use the curator's question to decide which candidates are focal enough to investigate. | `<evidence_and_curation_rules>` |
| GE-39 | Resolve composite mentions into individual genes when the paper clearly means individual entities (e.g. "BRCA1/2" -> BRCA1 and BRCA2; "par-3/par-6" -> par-3 and par-6). | `<evidence_and_curation_rules>` (verbatim example retained) |
| GE-40 | Common-word gene names require surrounding biological/experimental context ("not", "can", "was", "hedgehog", "white"). | `<evidence_and_curation_rules>` |
| GE-41 | Distinguish individual genes from gene families, protein complexes, pathways, reagents, author names. | `<evidence_and_curation_rules>` |
| GE-42 | Exclude chemical/drug codes, treatment names, assay reagents, compound identifiers as `unsupported_entity_type` unless the paper explicitly identifies them as genes. | `<evidence_and_curation_rules>` (verbatim "unsupported_entity_type") |
| GE-43 | Exclude gene symbols found only in catalog numbers, strain/reagent names, or reference lists without experimental context. | `<evidence_and_curation_rules>` |

## Evidence rules (include / exclude / span workflow)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| GE-44 | Good gene evidence: direct experimental findings (function/phenotype/localization/interaction/regulation/expression); figure/table gene-specific results from this paper; results tying gene to perturbation/mutant/rescue/knockdown/assay; functional characterization explaining centrality. | `<evidence_and_curation_rules>` |
| GE-45 | Weak/non-curatable evidence: methods-only mentions (strain construction, plasmid assembly, primer design, reagent sourcing); prior-work citations; discussion-only speculation/hypotheses/future work; generic pathway/family lists; reagent/catalog/author/reference-only mentions. | `<evidence_and_curation_rules>` |
| GE-46 | Do not reuse chemical/phenotype `record_evidence` records as gene evidence. | `<evidence_and_curation_rules>` |
| GE-47 | When a gene is focal AND experimentally supported: `read_chunk(chunk_id)`, select supporting `evidence_spans[].span_id` values, then `record_evidence(entity=..., span_ids=[...])`. Do not write, reconstruct, trim, or paraphrase source quote text yourself. | `<workflow>` + `<evidence_and_curation_rules>` (verbatim `record_evidence(span_ids=[...])` token) |
| GE-48 | On `status: "verified"`, the verified record carries `entity` (specific gene label, not "gene" / not chemical/phenotype/treatment), `chunk_id`, `verified_quote`, `page`, `section`, optional `subsection`, optional `figure_reference`. | `<output_and_handoff_contract>` (field shape) |
| GE-49 | If span resolution fails, `read_chunk` again for current span IDs or drop that evidence. Do not invent quotes or merge disconnected fragments into one implied contiguous passage. | `<stop_rules>` |

## Central-focus efficiency

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| GE-50 | For "the focus"/"central"/"central experimental focus" questions, prioritize precision over exhaustive mention capture; provisional shortlist from title/abstract/Results-Discussion headings/figures-tables, then verify evidence only for shortlisted genes. Do not use evidence collection as a screening mechanism for every gene in a broad search. | `<evidence_and_curation_rules>` (merged with GE-24) |
| GE-51 | Retain genes the paper directly investigates as experimental subjects or central mechanistic conclusions; exclude measured/compared/listed-as-altered/pathway-neighbor/complex-component/reagent/control/downstream readouts unless framed as central focus. | `<evidence_and_curation_rules>` |
| GE-52 | Keep one strongest verified quote per retained gene by default; add a second only when it changes the curator decision or disambiguates focal identity; total retained `gene_mention_evidence` normally 1-3, at most 4 unless the user asks for exhaustive evidence. | `<evidence_and_curation_rules>` |
| GE-53 | Record evidence only for genes you intend to stage; do not `record_evidence` for every mention or for genes you are excluding. | `<evidence_and_curation_rules>` (REWORDED, must-fix 2: there is no `metadata.exclusions[]` channel, so "record exclusion evidence" was misleading — recorded evidence only reaches the envelope when referenced by a staged candidate) |
| GE-54 | Once each retained focal gene has one verified quote plus species/provider context when available, proceed to finalize instead of searching for more support. | `<workflow>` + `<stop_rules>` |

## Exclusion / abstain rules

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| GE-55 | Genes mentioned only as background, prior work, discussion speculation, pathway context, or reagent setup are excluded by leaving them unstaged (no `metadata.exclusions[]` the model writes to). | `<evidence_and_curation_rules>` (REWORDED, must-fix 2: an exclusion is expressed by NOT staging) |
| GE-56 | Do not `record_evidence` for every gene mentioned anywhere; record only for genes you will stage. | `<evidence_and_curation_rules>` (merged with GE-53) |
| GE-57 | Methods-only mentions are not findings; retain a gene from methods only when the paper also reports results involving it elsewhere. | `<evidence_and_curation_rules>` |
| GE-58 | Parenthetical author-year citations signal prior work; if a sentence mixes prior work with a new finding, keep only the new finding and exclude the previously reported part. | `<evidence_and_curation_rules>` |
| GE-59 | When identity stays uncertain after reasonable species-context checks, do not guess: stage the focal/supported gene with its hints and leave `proposed_*` unset for the validator, or leave the mention unstaged if it is not clearly a single gene. Do not invent quotes, proposed symbols/identifiers, species assignments, `chunk_id` values, or evidence text. | `<stop_rules>` (REWORDED, must-fix 2: uncertain IDENTITY is still staged for the validator; there is no `metadata.ambiguities[]` the model writes to) |

## Exclusion reason codes (canonical enum — sourced from schema `ExclusionReasonCode`)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| GE-60 | Name your exclusion/discard reasoning with a canonical reason_code value: `previously_reported`, `non_experimental_claim`, `insufficient_experimental_evidence`, `out_of_scope`, `ambiguous_entity`, `duplicate_mention`, `unsupported_entity_type`, `gene_family_not_individual`, `author_or_reagent_name`, `reference_list_only`. | `<evidence_and_curation_rules>` (full enumerated list retained; `.reason_codes.txt` sourced from the schema enum, the canonical owner, not the prompt. REWORDED lead-in, must-fix 2: reason codes name the model's exclusion REASONING and the `discard_gene_mention_evidence` reason — they are NOT written to a `metadata.exclusions[]` array, which the builder path leaves empty) |

## Output / handoff contract

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| GE-61 | Every evidence record uses the shared field shape: `entity`, `verified_quote`, `page`, `section`, optional `subsection`, `chunk_id`, optional `figure_reference`. | `<output_and_handoff_contract>` |
| GE-62 | Apply that shape through builder tools: `read_chunk` -> `record_evidence(span_ids=[...])`; pass returned `evidence_record_id` to `stage_gene_mention_evidence`. | `<output_and_handoff_contract>` |
| GE-63 | Exactly one stage call per retained gene/evidence pairing; for two retained quotes of one gene, two stage calls sharing `mention` + identity hints but different `pending_ref_id` and evidence IDs. | `<output_and_handoff_contract>` |
| GE-64 | `finalize_gene_extraction(candidate_ids=[...])` once with the staged candidate IDs. Do NOT stage candidates you intend to exclude; leave excluded/ambiguous mentions unstaged. | `<output_and_handoff_contract>` + `<workflow>` (verbatim `finalize_gene_extraction(candidate_ids=`) |
| GE-65 | Do not place free-text evidence summaries in staging fields; the quote must come from `record_evidence` as `verified_quote`; backend copies verified source text into final metadata. | `<output_and_handoff_contract>` |
| GE-66 | Backend builder finalization creates the GeneExtractionResultEnvelope. Do not emit top-level `items[]`, `annotations[]`, `genes[]`, `alleles[]`, `diseases[]`, `chemicals[]`, `phenotypes[]`, `normalized_payload`, `annotation_drafts`, `curatable_objects[]`, `metadata.*`, or `run_summary`. | `<output_and_handoff_contract>` (verbatim "Backend builder finalization creates the GeneExtractionResultEnvelope" + the forbidden-list tokens — cross-cutting test tokens) |
| GE-67 | Per retained pairing: `read_chunk(chunk_id)` -> `record_evidence(span_ids=[...])` -> `stage_gene_mention_evidence` once with stable `pending_ref_id` + returned `evidence_record_ids`; include `mention`, `confidence`, and paper-backed hints (`species`, `taxon_hint`, `data_provider_hint`, `proposed_gene_symbol`, `proposed_taxon`, 1-3 `identity_resolution_notes`). | `<output_and_handoff_contract>` (merged with GE-25/GE-62) |
| GE-68 | Include `proposed_primary_external_id` only when the paper itself supplies a specific identifier; do not guess or look up IDs in the extractor. | `<output_and_handoff_contract>` (verbatim "proposed_primary_external_id" — contract-test token) |
| GE-69 | Backend materializes one `gene_mention_evidence` object per finalized candidate; model never hand-authors that graph. | `<output_and_handoff_contract>` |
| GE-70 | Notes may quote short paper snippets or summarize nearby context, but must not contain invented database identifiers or unsupported species assignments. | `<output_and_handoff_contract>` + `<stop_rules>` |
| GE-71 | Active validator bindings own final Alliance Gene identity decisions and materialize `primary_external_id`, `gene_symbol`, `taxon`; when unresolved, preserve the evidence-backed candidate in staging hints or finalization ambiguities and leave the unresolved state to validator result fields and envelope validation findings. | `<validator_handoff>` + `<output_and_handoff_contract>` (verbatim "Active validator bindings own final Alliance Gene identity decisions") |

## Few-shot examples (structurally load-bearing — counts/ordering)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| GE-72 | Example 1: retain a focal gene with TWO verified quotes -> two `record_evidence` calls -> two `stage_gene_mention_evidence` calls (same mention, different pending_ref_id/evidence) -> one `finalize_gene_extraction(candidate_ids=[...])` with two candidate ids. | `<examples>` Example 1 (preserved; contributes 2 of the 3 required `stage_gene_mention_evidence(` occurrences) |
| GE-73 | Example 2: skip prior-work background (par-6 citation), verify the real focal gene (yurt mutant phenotype) -> one stage call -> finalize with one candidate; "par-6 is excluded background; do not stage it." | `<examples>` Example 2 (preserved; contributes the 3rd `stage_gene_mention_evidence(` occurrence) |
| GE-74 | Example reasoning teaches: prior-work citation is NOT a new focal gene finding; a new mutant phenotype IS. | `<examples>` Example 2 reasoning (preserved) |

## Output contract / finalization ack

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| GE-75 | After successful `finalize_gene_extraction`, return JSON only, matching `ExtractionToolFinalizationAck`: `status: complete`, `finalized_run_id`, `summary` (concise ack), `staged_count`, `finalized_count`. | `<output_and_handoff_contract>` (ack shape preserved) |
| GE-76 | Do not output GeneExtractionResultEnvelope, `summary`, `curatable_objects[]`, `metadata.*`, `run_summary`, or validator result fields yourself; backend builds/validates the final envelope from staged state. | `<output_and_handoff_contract>` (merged with GE-66) |
| GE-77 | `staged_count` and `finalized_count` must match the model's stage calls. Never invent proposed gene symbols, proposed identifiers, species assignments, `chunk_id` values, or evidence text. | `<output_and_handoff_contract>` + `<stop_rules>` (REWORDED, must-fix 2: `run_summary` excluded_count/ambiguous_count are backend-materialized, hard-coded 0 in the builder path; the ack counts the model owns are staged_count/finalized_count) |

## FB group-rule hooks (rendered with the FB group)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| GE-78 | FB nomenclature: Drosophila gene symbols use case to indicate dominance (uppercase first letter = dominant, lowercase = recessive). | FB group rules (`group_rules/fb.yaml`) — unchanged; base rewrite must not break the FB-rendered retention. |
| GE-79 | FB multi-species disambiguation: Drosophila p53 is "p53" or "Dmp53", NOT Trp53/TP53. | FB group rules — unchanged. |
| GE-80 | FB shared output contract still binds to `gene_mention_evidence`. | FB group rules — unchanged. |

---

## De-dup of the evidence-span mechanic (per skeleton)

The pre-rewrite prompt restated the `record_evidence` span mechanic in three
places (`<search_context>` evidence-selection bullet, `<evidence_rules>`, and
`<output_alignment_rules>`/`<domain_envelope_contract>`). The `record_evidence`
bindings.yaml summary ("Turns chosen snippets from a passage into a saved piece
of evidence with the exact verified quote ... Each call saves one piece of
evidence; if several snippets are chosen together, they are stored as one joined
quote") already carries the mechanical fact. The rewrite keeps the **curation
guidance** (what makes a span worth recording, one-call-per-disjoint-unit,
no-paraphrase) once, in `<evidence_and_curation_rules>` + `<workflow>`, and stops
restating the tool mechanic. The literal tokens the cross-cutting contract tests
require (`read_chunk.evidence_spans[].span_id`, `record_evidence(span_ids=[...])`,
"evidence unit") are retained exactly once each in the workflow/evidence section.

## Contract-test re-baseline (test_gene_extractor_domain_envelope_contract.py)

The seed contract test
(`test_gene_extractor_prompt_agent_and_group_rules_name_domain_envelope_contract`)
asserts specific phrases and COUNTS against the base prompt content. Re-baseline
decisions (no count/ordering weakened; every moved phrase gets a same-commit
replacement keyed to a checklist ID):

- All asserted phrases (GE-03, GE-09, GE-25, GE-28, GE-33, GE-34, GE-35, GE-68,
  GE-71, the `agr_species_context_lookup` token, the negative assertions for
  example-gene names / repair surfaces / legacy GeneAssertion tokens) are
  **retained verbatim** in the rewrite, so the existing assertions still pass
  **unchanged**.
- `example_object_count = prompt_content.count("stage_gene_mention_evidence(") == 3`
  (GE-72 + GE-73): the rewrite keeps exactly two example stage calls (Example 1)
  plus one (Example 2) = 3 occurrences of `stage_gene_mention_evidence(`. **Count
  preserved at == 3, not weakened.**
- `prompt_content.count("record_evidence(") >= example_object_count` (>= 3):
  preserved — the two examples carry 4 `record_evidence(` calls (2 in Example 1,
  1 in Example 2) plus the one-line guidance, so the count stays >= 3.
- `prompt_content.count("finalize_gene_extraction(") >= 2`: preserved — both
  examples end in a `finalize_gene_extraction(` call.
- `finalize_gene_extraction(candidate_ids=` present (GE-64): retained verbatim.
- `kept_count=` absent: preserved (the rewrite never uses the legacy summary
  signature).

**Conclusion: no contract-test assertion is edited, deleted, or weakened by this
rewrite.** Every count/ordering assertion is satisfied by the preserved few-shot
examples and verbatim-retained phrases. If any assertion had needed to move, it
would have been listed here with a same-commit replacement assertion targeting
the new wording; none did.
