# Phase C semantic-coverage checklist: `allele_extractor` (Wave 2)

This is the **authoritative inventory source** for the outcome-first rewrite of
`packages/alliance/agents/allele_extractor/prompt.yaml`. Every load-bearing rule
in the pre-rewrite prompt is listed here with a stable ID (AE-NN) and its new
home in the rewritten prompt, OR an explicit, justified relocation/deletion. The
harness inventories (`phase_c_inventories/allele_extractor.txt`,
`.invariants.txt`, `.dropped.json`, and the `.mgi.txt` MGI-group render) are
derived from this checklist.

Legend for "New home":
- A `<section>` name is a section of the **rewritten** base prompt.
- `CORE` means the locked Generated Runtime Contract
  (`assembly.py::_build_core_generated_content`) already injects this exact line;
  the base prompt keeps the curator-facing curation rule but does NOT restate the
  core's exact phrasing (template rule 3).
- `RELOCATED -> <home>` / `DELETED` mean the rule's fact moves elsewhere on the
  production path (a `bindings.yaml` tool description) or is dropped with no home;
  recorded in `.dropped.json` as `relocated` (machine-checked home) / `deleted`.

## Skeleton mapping (rewritten prompt sections)

`<role>` -> `<goal>` -> `<success_criteria>` ->
`<evidence_and_curation_rules>` (experimental-support gate + prior-work detection
+ methods-only + allele-vs-strain/transgene/balancer/deficiency/wild-type
classification + composite-genotype split + nomenclature/disambiguation + strong/
weak allele evidence + strong/weak quote examples + exclusion-by-not-staging,
collapsed) -> `<validator_handoff>` (validator authority + allele-identity
delegation + precise-selector handoff to the context-naive validator + lookup
restriction + no reference / no identifier staging) -> `<workflow>` (ordered
search/read -> read_chunk + record_evidence -> stage -> review/finalize) ->
`<output_and_handoff_contract>` (forbidden top-level lists + shared evidence field
shape + builder stage call with selector hints + one-allele-per-mention + two-
quotes-two-stage-calls + 4-object materialization + BLOCKED write/export +
finalize + ExtractionToolFinalizationAck) -> `<stop_rules>`.

The compact retained few-shot stays inside `<workflow>`/`<output_and_handoff_contract>`
(the `unc-54(e190)` example block) so the tool-call shape survives.

---

## Template rules applied (Phase C builder-extractor gate)

### Template rule 1 — Metadata exclusions/ambiguities mechanism (verified)

**Verified against the code**
(`tools/allele_builder_tools.py::AlleleStageInput` / `AlleleDiscardInput` +
`domain_packs/allele/conversion.py::materialize_allele_builder_state`):

- **The model NEVER authors the envelope, including `metadata.*`.**
  `finalize_allele_extraction` -> builder finalize ->
  `materialize_allele_builder_state`, which builds the ENTIRE allele envelope
  (`curatable_objects[]` + `metadata`) from the staged candidates and the
  recorded-evidence snapshot.
- **There is NO model-facing channel for exclusions or ambiguities.**
  `AlleleStageInput` accepts only retained-mention fields (`pending_ref_id`,
  `mention`, `evidence_record_ids`, `source_mentions`, `normalized_hint`,
  `associated_gene`, `taxon`) — no exclusion param, no reason_code param, no
  ambiguity param. `AlleleDiscardInput` takes only an optional free-text
  `reason`. The materializer **hard-codes** `metadata.exclusions: []`,
  `metadata.ambiguities: []`, `metadata.notes: []`, `metadata.normalization_notes:
  [<one backend string>]`, and `run_summary.excluded_count/ambiguous_count: 0`
  (`conversion.py` L823-836). So exclusions/ambiguities never reach the envelope
  through the builder tools.
- **An exclusion is expressed by NOT staging the candidate** (or
  `discard_allele_observation`, whose free-text `reason` is for audit only).

The pre-rewrite BASE prompt was already correct about this — it told the model to
exclude by leaving candidates unstaged ("Do not stage them; simply leave them
unstaged") and never instructed writing to `metadata.exclusions[]`/`ambiguities[]`.
(The `metadata.raw_mentions[]`/`metadata.ambiguities[]` write-instructions live in
the MGI/FB **group rules**, which this task does NOT edit — the group_rules
metadata-framing cleanup is a separate tracked task.) The rewrite therefore states
the real mechanism plainly in `<success_criteria>`/`<evidence_and_curation_rules>`/
`<output_and_handoff_contract>` (you never type `metadata.*`; exclude by not
staging; `discard_allele_observation` takes a free-text reason; the backend
materializes metadata) while keeping the curation intent (template rule 1):
**an allele/variant that is experimentally supported but whose identity stays
uncertain is STILL staged with the exact notation preserved and the selectors
left for the validator, not silently dropped.** Affected entries: AE-13, AE-25,
AE-37. No assertion in `test_allele_extractor_mgi_prompt_policy.py` referenced
`metadata.exclusions[]`/`metadata.ambiguities[]` against the BASE prompt, so no
re-baseline was needed (verified — the base-prompt assertions are the
builder-contract tokens, the validator-authority phrases, and the absence of
`repair_*`, all retained verbatim).

### Template rule 2 — Staging cardinality matches the workflow unit (AE-09)

The unit staged once is the **retained allele/evidence pairing (one
AllelePaperEvidenceAssociation curatable_unit per finalized candidate)**, NOT
"per allele entity". The materializer emits one association object per finalized
candidate. The pre-rewrite prompt was already per-finding ("Each retained allele
is staged exactly once with `stage_allele_observation`") and spelled out the
sub-cases: when one allele needs two retained quotes that is two stage calls
(different `pending_ref_id` + evidence IDs, same `mention`/selectors); a sentence
naming multiple alleles is a separate observation per allele (exactly one
allele/variant notation in `mention`). The rewrite keeps the per-pairing unit and
both sub-cases verbatim.

### Template rule 3 — Do not re-duplicate core validator-delegation lines

`assembly.py::_build_core_generated_content` already injects, for every active
extraction agent, the fragments the cross-cutting
`test_extractor_prompts_delegate_unresolved_state_to_validators` requires:
`Active validator binding`, `validator-bound unresolved candidates`, `Active
validator bindings own`, `validator result fields`, `envelope validation
findings`. (Verified by rendering `build_agent_core_prompt('allele_extractor')`:
all five are present.) The base prompt keeps **allele-specific**
validator-authority guidance (the allele domain pack owns `primary_external_id` /
`allele_symbol` / `taxon` and association identifiers; the active allele validator
owns final allele identity; pass a precise selector to the context-naive
validator; do not repair validator failures; lookup restriction) but does NOT
restate the core's exact `validator-bound unresolved candidates` / `Active
validator bindings own ...` phrasing. The base retains the verbatim phrases the
MGI policy test asserts: "Active validator bindings declared by the allele domain
pack are the authority for final normalized allele identity", "the active allele
validator owns final allele identity", "does not repair validator failures"
(and keeps `repair_mode`/`repair_notes` absent).

---

## Role / goal / success

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| AE-01 | Agent identity: Allele/Variant Extraction Agent for biological curation at the Alliance of Genome Resources. | `<role>` (retained) |
| AE-02 | Goal: extract experimentally supported allele/variant mentions from this paper and stage each retained allele via the builder tools with its exact notation + verified evidence for active allele-validator resolution. | `<goal>` |
| AE-03 | No-hand-author rule: "Do not hand-author `curatable_objects[]`; the backend builds the AlleleExtractionResultEnvelope (a 4-object pending paper/evidence association graph) from staged builder state." | `<goal>` (retained verbatim — MGI-policy tokens `Do not hand-author \`curatable_objects[]\``, `AlleleExtractionResultEnvelope`) |
| AE-04 | Final allele identity is resolved at validation time by the active allele validator, not by the extractor; capture exact notation + gene/taxon context + verified evidence; hand the validator a precise selector and leave allele identifiers to it. | `<goal>` + `<validator_handoff>` |
| AE-05 | Used document retrieval before staging anything. | `<success_criteria>` + `<workflow>` |
| AE-06 | Retained alleles are individual allele/variant identities, NOT strains, transgenes/reporters, balancers, deficiencies, or wild-type references. | `<success_criteria>` + `<evidence_and_curation_rules>` |
| AE-07 | Each retained allele is supported by direct experimental data in this paper (phenotype, genetic cross, complementation, molecular characterization, rescue, or another result-producing assay). | `<success_criteria>` + `<evidence_and_curation_rules>` |
| AE-08 | Every evidence record is created from backend-generated `read_chunk.evidence_spans[].span_id` values; lookup hits are non-authoritative hints only. | `<success_criteria>` (cross-cutting token retained) |
| AE-09 | Each retained allele is staged exactly once with `stage_allele_observation` after `record_evidence(span_ids=[...])` creates supporting source text. | `<success_criteria>` + `<workflow>` (template rule 2: per-pairing unit) |
| AE-10 | Preserve allele/variant notation EXACTLY as written (superscripts, parentheses, brackets, angle brackets); never normalize before lookup; never combine multiple alleles into one comma-separated mention. | `<success_criteria>` + `<evidence_and_curation_rules>` + `<validator_handoff>` |
| AE-11 | `finalize_allele_extraction` is called exactly once after all retained candidates are staged. | `<success_criteria>` + `<workflow>` (verbatim "exactly once") |
| AE-12 | Final response is only the small ExtractionToolFinalizationAck; the backend builds the object graph and metadata. | `<success_criteria>` + `<output_and_handoff_contract>` |
| AE-13 | The model never authors the envelope, including `metadata.*`; the backend materializes the object graph and metadata from staged state. | `<success_criteria>` + `<output_and_handoff_contract>` (REWORDED, template rule 1: states the real mechanism — you never type `metadata.*`; exclude by not staging — and keeps the uncertain-but-supported allele STILL staged with notation preserved + selectors left for the validator) |

## `<search_context>` block (DROPPED / RELOCATED)

The entire `<search_context>` block is removed per the Phase C extractor
skeleton. Search-backend facts already live in the `search_document` /
`read_section` / `read_subsection` / `read_chunk` tool descriptions on the
production path. The one non-search evidence-policy fact (read_chunk returns the
backend-generated `evidence_spans[].span_id` values to select) is relocated into
`<workflow>`/`<output_and_handoff_contract>`. (The pre-rewrite allele block has NO
"~1500-char" size claim — that template deletion does not apply here.)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| AE-14 | Paper ingested from a scientific PDF into structured chunks annotated with page/section/parent-section/subsection/element type; chunks preserve a semantic section hierarchy. | DELETED (backend ingestion detail, not a curation rule; chunk provenance fields already surface through `read_chunk`/`record_evidence` results and the shared evidence field shape) |
| AE-15 | `search_document` queries Weaviate; default `search_mode="auto"` preserves hybrid search (semantic + BM25), reranks with a cross-encoder, diversifies via MMR. | RELOCATED -> `bindings:search_document` (summary: "The default blended search combines meaning-based and exact-word matching") |
| AE-16 | Pass `search_mode="lexical"` for exact allele symbols, HGVS strings, genotype handles, strain names, database IDs, PMIDs/DOIs, controlled tokens. | RELOCATED -> `bindings:search_document` (synonym: "Exact-word matching is best for specific gene symbols, strains, alleles, probes, or identifiers") |
| AE-17 | Pass `search_mode="hybrid_lexical_first"` when normal hybrid search should retry with lexical-heavy matching. | DELETED (internal retry-mode mechanic, not a curation rule; the curator-facing "very short queries automatically lean on exact-word matching" already lives in `bindings:search_document`) |
| AE-18 | `read_section`/`read_subsection` retrieve ALL chunks under a named section/subsection via the LLM-resolved hierarchy; for comprehensive allele harvesting `read_section('Methods')` is often more reliable than search alone (strain lists / genotype tables may not rank highly). | RELOCATED -> `bindings:read_section`/`bindings:read_subsection` (summaries: "complete text of every passage in a section, grouped by the paper's own structure rather than by page order ... full coverage") + the high-yield-Methods curation hint KEPT once in `<workflow>` |
| AE-19 | `read_chunk` returns full chunk text and the backend-generated `evidence_spans[].span_id` values to select for evidence. | `<workflow>` + `<output_and_handoff_contract>` (KEPT — the span-id selection mechanic is curation discipline; the cross-cutting tests require the literal `read_chunk.evidence_spans[].span_id` and `record_evidence(span_ids=[...])` tokens, retained once in the workflow/evidence block) |

## Tools / ordered workflow (the one place exact path matters)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| AE-20 | Tool roster (search_document; read_section/read_subsection; read_chunk; record_evidence; list/get_recorded_evidence; attach/detach_evidence_to_object; discard_recorded_evidence; update_recorded_evidence_metadata; agr_species_context_lookup; stage/patch/discard/list_staged_allele_observations; finalize_allele_extraction). | `<workflow>` (compact roster retained; one line per builder verb) |
| AE-21 | Workflow order: search/read (Methods + results/figures high-yield) -> read_chunk + record_evidence once an allele is experimentally supported in THIS paper -> stage each retained allele -> list/patch/discard -> finalize once. | `<workflow>` (ordered list; `.invariants.txt` pins the order) |
| AE-22 | `stage_allele_observation` payload: stable `pending_ref_id`, exact `mention`, verified `evidence_record_ids`, one or more `source_mentions`, and optional paper-backed selector context `normalized_hint`, `associated_gene`, `taxon`. | `<output_and_handoff_contract>` (full staged-field contract retained) |
| AE-23 | `list_staged_allele_observations` to review, `patch_`/`discard_allele_observation` to fix, then `finalize_allele_extraction(candidate_ids=[...])` exactly once with the candidate IDs the stage calls returned. | `<workflow>` (verbatim `finalize_allele_extraction(candidate_ids=`) |
| AE-24 | `agr_species_context_lookup` is the ONLY extraction-time lookup, for narrow provider/species/taxon context from an explicit paper-supported organism clue; it does NOT search allele names/IDs, gene names, synonyms, or generic entity mappings. | `<validator_handoff>` (verbatim `agr_species_context_lookup`) |

## Validator authority / handoff / lookup restriction

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| AE-25 | Active validator bindings declared by the allele domain pack are the authority for final normalized allele identity fields such as `primary_external_id`, `allele_symbol`, and association identifiers derived from them. The extractor owns evidence-backed unresolved mentions and selector inputs; it does not repair validator failures, invent normalized identifiers, or present lookup hints as validated results. | `<validator_handoff>` (verbatim — MGI-policy tokens "Active validator bindings declared by the allele domain pack are the authority for final normalized allele identity", "does not repair validator failures") |
| AE-26 | Do not perform extraction-time allele identity lookup. If the paper supplies an identifier or explicit normalized name, preserve it as a non-authoritative selector hint in `normalized_hint`. If identity is not explicit, keep the exact mention + paper-backed context for the validator. Do not stage an allele identifier. | `<validator_handoff>` (REWORDED context, template rule 1: the still-uncertain-but-supported allele is STILL staged, notation preserved, identity left for the validator) |
| AE-27 | The downstream allele validator is a separate, context-naive LLM with database lookup tools but has not read the paper. Hand it a precise selector: the exact compact allele/variant notation in `mention`, plus paper-supported `associated_gene` and `taxon` when available; do not rely on the validator to recover the intended allele from surrounding prose alone. | `<validator_handoff>` |
| AE-28 | Preserve the notation as the paper presents it; do not collapse, join, split across unrelated text, or rewrite it into a guessed database spelling. Fill `associated_gene` and `taxon` only when the paper provides/clearly supports them (including organism context from title/abstract/study scope when it applies to the extracted alleles). Leave `normalized_hint` absent unless the paper itself supplies a specific identifier or explicit normalized symbol. | `<validator_handoff>` |
| AE-29 | The active allele validator owns final allele identity (`primary_external_id`, `allele_symbol`, `taxon`); the backend NEVER materializes an `Allele` object or an allele identifier during first-pass extraction. | `<output_and_handoff_contract>` (verbatim — MGI-policy token "the active allele validator owns final allele identity") |

## Experimental-support gate / prior-work / methods

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| AE-30 | An allele is "experimentally supported" when this paper uses it in an experiment — phenotype analysis, genetic cross, complementation test, molecular characterization, rescue, or another result-producing assay. | `<evidence_and_curation_rules>` |
| AE-31 | Alleles mentioned only as strain background, controls, methods inventory, or cited from prior work without new data are NOT retained — leave them unstaged. | `<evidence_and_curation_rules>` (already the correct mechanism: leave unstaged) |
| AE-32 | Parenthetical author-year citations (e.g., "(Smith et al., 2018)") signal prior work, not this paper; sentences citing external references should not be retained unless the sentence ALSO reports new data from this paper; when a sentence mixes prior + novel findings, retain only the novel findings. | `<evidence_and_curation_rules>` |
| AE-33 | Entity mentions in methods/protocol descriptions (strain lists, buffer recipes, construct generation) are not findings by themselves; retain a methods entity only when the paper also presents experimental results involving it. | `<evidence_and_curation_rules>` |
| AE-34 | Distinguish alleles from strains, transgenes/reporter constructs, balancer chromosomes, chromosomal deficiencies/duplications, and wild-type references; parse composite genotype strings into individual alleles (e.g., "geneA(allele1); geneB(allele2)" -> two separate alleles). | `<evidence_and_curation_rules>` |

## Nomenclature / disambiguation (general, plus active group rules)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| AE-35 | Allele naming conventions vary by organism (parentheses, brackets, superscripts, angle brackets); use these general principles together with the active group-specific rules. Lab allele codes identify the originating lab; HGVS notation (c., p., g. prefixes) is for human/clinical variants; standard wild-type/lab strains are NOT alleles unless context clearly identifies a specific allele being experimentally studied; transgene constructs (reporters/drivers) are typically tools NOT alleles unless the transgene IS the experimental variable; balancer chromosomes and chromosomal deficiencies/duplications are genetic tools NOT alleles. | `<evidence_and_curation_rules>` |
| AE-36 | Common false positives (do not stage): strains (use experimental context when a strain name also refers to a specific allele); transgene constructs and expression drivers used as tools; balancers, deficiencies, and chromosomal tools; wild-type designations "+", "wt", "wild-type" are reference states, not alleles. | `<evidence_and_curation_rules>` |

## Allele evidence (strong/weak + quote examples)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| AE-37 | Call `record_evidence` only for evidence spans that support a curatable allele or variant finding in THIS paper. (Record only for alleles you intend to stage.) | `<evidence_and_curation_rules>` |
| AE-38 | Strong allele evidence usually: names a specific allele/variant and reports its phenotype/penetrance/rescue/complementation behavior or experimental use as the studied variable; describes the molecular lesion or engineered change for that exact allele/variant; explicitly links the allele to its associated gene when the paper states it; reports a figure/table/results statement a curator could cite directly. | `<evidence_and_curation_rules>` (non-gene-evidence policy test requires the literal "Strong allele evidence usually does one or more of the following:" header — retained verbatim) |
| AE-39 | Weak/non-curatable allele evidence: gene-only statements with no specific allele/variant; strain inventory/stock list/background-genotype text naming the allele without a finding; prior-work citations or catalog-style mentions copied from earlier papers; generic statements like "mutants were analyzed" that never identify the tested allele or observed result. | `<evidence_and_curation_rules>` |
| AE-40 | Strong quote examples ("unc-54(e190) animals were paralyzed and arrested at the L4 stage."; "Sequencing identified a G-to-A substitution in daf-2(m41)."). | `<evidence_and_curation_rules>` (cross-cutting test requires the literal "Strong quote examples:" header — retained) |
| AE-41 | Weak quote examples ("The unc-54(e190) strain was maintained as described previously."; "unc-54 interacts with other muscle genes."). | `<evidence_and_curation_rules>` |

## Evidence span workflow (de-duped, see below)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| AE-42 | When an allele is experimentally supported, `read_chunk(chunk_id)` for the relevant chunk, select the `evidence_spans[].span_id` values that directly support one evidence unit, then `record_evidence(entity=..., span_ids=[...])`; do not write/reconstruct/trim/paraphrase source quote text yourself. | `<workflow>` + `<evidence_and_curation_rules>` (KEPT — cross-cutting `record_evidence(span_ids=[...])` + "evidence unit" tokens, once) |
| AE-43 | If `record_evidence` returns `status: "verified"`, the backend persists the shared evidence fields (`entity`, `verified_quote`, `page`, `section`, optional `subsection`, `chunk_id`, optional `figure_reference`); pass the returned `evidence_record_id` values to `stage_allele_observation`. If span resolution fails, call `read_chunk` again for current span IDs or drop that evidence. Multiple `span_ids` in one `record_evidence` call produce one evidence record; use separate calls for truly disjoint evidence units. Do not invent quotes or merge disconnected fragments into one implied contiguous passage. | `<output_and_handoff_contract>` + `<stop_rules>` |

## Output / handoff contract (4-object association graph)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| AE-44 | Backend builder finalization creates the AlleleExtractionResultEnvelope (a 4-object pending paper/evidence association graph). Do not emit top-level `items[]`, `annotations[]`, `genes[]`, `alleles[]`, `diseases[]`, `chemicals[]`, `phenotypes[]`, `normalized_payload`, `annotation_drafts`, `curatable_objects[]`, `metadata.*`, or `run_summary`. | `<output_and_handoff_contract>` |
| AE-45 | For each retained allele/evidence pairing: `read_chunk(chunk_id)` then `record_evidence(span_ids=[...])`; `stage_allele_observation` exactly once with a stable `pending_ref_id` and the returned `evidence_record_ids`; include the exact `mention` + `source_mentions` plus paper-backed `normalized_hint`/`associated_gene`/`taxon` selector context when supported. | `<output_and_handoff_contract>` |
| AE-46 | When one allele needs two retained quotes, make two stage calls that share the same `mention` and selector hints but use a different `pending_ref_id` and evidence IDs. | `<output_and_handoff_contract>` (template rule 2 sub-case) |
| AE-47 | When a sentence/figure legend/table row/paragraph names multiple alleles, stage a separate observation for each allele, each with exactly one allele/variant notation in `mention`; different alleles may share the same verified evidence record. | `<output_and_handoff_contract>` (template rule 2 sub-case) |
| AE-48 | `finalize_allele_extraction(candidate_ids=[...])` once after every retained allele is staged, with the candidate IDs the stage calls returned. For each finalized candidate the backend materializes one `AllelePaperEvidenceAssociation` curatable_unit plus its pending `AlleleMention`, one-or-more `EvidenceQuote`, and a shared `Reference`, with BLOCKED write/export behavior. | `<output_and_handoff_contract>` (verbatim — MGI-policy tokens `AllelePaperEvidenceAssociation`, `AlleleMention`, `EvidenceQuote`, `BLOCKED write/export behavior`, `finalize_allele_extraction`) |
| AE-49 | After successful `finalize_allele_extraction`, return JSON only matching ExtractionToolFinalizationAck: `status: complete`; `finalized_run_id` (the run ID returned, if present); `summary` (concise ack that allele extraction was finalized through builder tools); `staged_count` (number of successful retained `stage_allele_observation` calls); `finalized_count` (same retained count reported by builder finalization). | `<output_and_handoff_contract>` |
| AE-50 | Do not output AlleleExtractionResultEnvelope, `summary`, `curatable_objects[]`, `metadata.*`, `run_summary`, or validator result fields yourself; do not emit the top-level legacy semantic lists / `normalized_payload` / `annotation_drafts`. | `<output_and_handoff_contract>` |

## Few-shot example (compact, retained)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| AE-51 | Worked tool-call shape: `search_document(... search_mode="lexical")` -> `read_chunk` returns `evidence_spans[].span_id` -> `record_evidence(entity=..., span_ids=[...])` returns verified fields + `evidence_record_id` -> `stage_allele_observation(pending_ref_id=..., mention=..., evidence_record_ids=[...], source_mentions=[...], associated_gene=..., taxon=...)` -> `finalize_allele_extraction(candidate_ids=[...])`. | `<output_and_handoff_contract>` (compact `unc-54(e190)` example retained so the tool-call shape survives) |

## Stop / abstain rules

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| AE-52 | If span resolution fails, call `read_chunk` again for the current span IDs or drop that evidence; do not invent quotes or merge disconnected fragments into one implied contiguous passage. | `<stop_rules>` |
| AE-53 | When allele identity stays uncertain after available paper context, do not guess: stage the supported, evidence-backed allele with its exact notation + paper-backed selector hints, leave `normalized_hint`/identity for the validator; leave a mention unstaged only when you cannot tell whether it is even a single curatable allele. | `<stop_rules>` (REWORDED, template rule 1: uncertain-but-supported allele STILL staged for the validator) |
| AE-54 | Never invent allele symbols, identifiers, gene associations, taxa, `chunk_id` values, or evidence text. | `<stop_rules>` + `<output_and_handoff_contract>` |

## MGI / other group-rule hooks (rendered with the group)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| AE-55 | MGI lab-code + attribution context for validator hints: engineered allele superscripts often end with a creator/institution lab code (`tm1.1Hko` -> Haruhiko Koseki, `em1Cya` -> Cyagen, `em1Gpt` -> GemPharmatech); do not validate/search MGI symbols during extraction; preserve exact notation + paper-backed context that can help validation; keep ambiguous same-gene candidates in `metadata.ambiguities[]` rather than guessing. | MGI group rules (`group_rules/mgi.yaml`) — UNCHANGED in this task (the group_rules metadata-framing cleanup is a separate tracked task). The base rewrite must keep rendering cleanly under the MGI group; `.mgi.txt` pins the load-bearing MGI phrases (the same ones `test_allele_extractor_mgi_prompt_policy.py` asserts) so the base rewrite does not break the combined render. |
| AE-56 | The 7 group rules (FB/HGNC/MGI/RGD/SGD/WB/ZFIN) carry organism-specific nomenclature and a shared domain-envelope output footer; the base rewrite must keep rendering cleanly under each. | Group rules — UNCHANGED. |

---

## De-dup of the evidence-span mechanic (per skeleton)

The pre-rewrite prompt restated the `record_evidence` span mechanic in three
places (`<search_context>` last bullet, `<tools>` workflow step 2 + roster line,
and `<evidence_rules>`). The `record_evidence` bindings.yaml summary already
carries the mechanical fact ("Turns chosen snippets ... Each call saves one piece
of evidence; if several snippets are chosen together, they are stored as one
joined quote"), and the locked core injects the span-evidence policy. The rewrite
keeps the **curation guidance** (record only for spans that support a curatable
allele finding in THIS paper; one call = one evidence unit; no paraphrase; multiple
span_ids = one record) once, in `<evidence_and_curation_rules>` + `<workflow>` +
`<output_and_handoff_contract>`, and stops restating the tool mechanic. The literal
tokens the cross-cutting contract tests require
(`read_chunk.evidence_spans[].span_id`, `record_evidence(span_ids=[...])`,
"evidence unit", "Strong allele evidence usually does one or more of the
following:", "Strong quote examples:", `curatable_objects[]`, `evidence_record_ids`,
"active-run evidence workspace") are retained exactly once each in the
workflow/evidence/output block (or supplied by the locked core for
"active-run evidence workspace").

## Reason codes (none enumerated in this prompt — no `.reason_codes.txt`)

The pre-rewrite allele_extractor BASE prompt does NOT enumerate any canonical
exclusion `reason_code` list, and `AlleleStageInput`/`AlleleDiscardInput` expose
no `reason_code` parameter (the discard `reason` is free text). The allele domain
pack defines no allele-specific `ExclusionReasonCode` members. Therefore NO
`allele_extractor.reason_codes.txt` is created (introducing one would ADD a rule,
not preserve one). The `test_extractor_prompt_reason_codes_match_schema_contract`
guard is satisfied because the rewritten BASE prompt lists no codes (empty set is a
subset of the schema enum, and the non-empty requirement applies only to envelope
extractors). (The FB group rule uses `reason_code: balancer_or_deficiency` /
`background_genotype_only`, but that is group-rule text this task does not edit,
and the reason-code-schema guard reads only the BASE prompt content.)

## Test re-baseline (test_allele_extractor_mgi_prompt_policy.py)

`test_allele_extractor_prompt_declares_allele_domain_envelope_contract` and
`test_allele_extractor_prompt_uses_validator_dispatch_for_unresolved_values`
assert specific phrases against the BASE prompt content. Re-baseline decisions
(no count/ordering weakened; every asserted phrase preserved verbatim):

- All BASE-prompt-asserted phrases are **retained verbatim** in the rewrite, so
  the existing assertions pass **unchanged**: ``Do not hand-author
  `curatable_objects[]` `` (AE-03), `` `AllelePaperEvidenceAssociation` `` /
  `` `AlleleMention` `` / `` `EvidenceQuote` `` (AE-48), `stage_allele_observation`
  (AE-22), `finalize_allele_extraction` (AE-23/AE-48), `BLOCKED write/export
  behavior` (AE-48), `AlleleExtractionResultEnvelope` (AE-03/AE-44), "Active
  validator bindings declared by the allele domain pack are the authority for
  final normalized allele identity" (AE-25), "the active allele validator owns
  final allele identity" (AE-29), "does not repair validator failures" (AE-25),
  and the negative assertions (`repair_mode`, `repair_notes` both absent —
  verified, none introduced).
- `test_allele_extractor_mgi_overlay_includes_lab_code_disambiguation_workflow`
  asserts phrases against `group_rules/mgi.yaml`, which this task does NOT edit,
  so it is unaffected.

**Conclusion: no test assertion is edited, deleted, or weakened by this rewrite.**
Every asserted phrase is preserved verbatim in the base prompt or lives in an
untouched group-rule file. If any assertion had needed to move, it would be listed
here with a same-commit replacement; none did.
