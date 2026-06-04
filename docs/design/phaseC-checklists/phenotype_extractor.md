# Phase C semantic-coverage checklist: `phenotype_extractor` (Wave 2)

This is the **authoritative inventory source** for the outcome-first rewrite of
`packages/alliance/agents/phenotype_extractor/prompt.yaml`. Every load-bearing
rule in the pre-rewrite prompt is listed here with a stable ID (PE-NN) and its new
home in the rewritten prompt, OR an explicit, justified relocation/deletion. The
harness inventories (`phase_c_inventories/phenotype_extractor.txt`,
`.invariants.txt`, `.dropped.json`, and the `.fb.txt` FB-group render) are derived
from this checklist.

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
`<evidence_and_curation_rules>` (experimental-support gate + include list +
prior-work detection + methods-only + wild-type/control + strong/weak phenotype
evidence + strong quote examples + exclude-by-not-staging, collapsed) ->
`<phenotype_disambiguation_rules>` (phenotype vs disease + phenotype vs normal
biology + polarity/severity + negative results + genotype-phenotype linking +
composite split + paper-backed subject and phenotype-term hints) ->
`<validator_handoff>` (phenotype-ontology + subject + reference authority +
broad-lookup restriction + the condition-grounding exception + no reference
staging) -> `<workflow>` (ordered search/read -> read_chunk + record_evidence ->
stage -> review/finalize) -> `<experimental_condition_rules>` (preserved nearly
verbatim: ZECO/XCO/ChEBI grounding + the no-quote evidence contract) ->
`<output_and_handoff_contract>` (forbidden top-level lists + shared evidence field
shape + full builder stage-field contract with subject/term/condition hints +
per-annotation cardinality + finalize + finalization acknowledgment) ->
`<stop_rules>`.

The compact retained few-shot stays inline (the `daf-2(e1370)` strong/weak
phenotype quote examples) so the curation distinction survives.

---

## Template rules applied (Phase C builder-extractor gate)

### Template rule 1 — Metadata exclusions/ambiguities mechanism (VERIFIED)

**Verified against the code**
(`tools/phenotype_builder_tools.py::PhenotypeStageInput` / `PhenotypeDiscardInput`
+ `domain_packs/phenotype/conversion.py::materialize_phenotype_builder_state`):

- **The model NEVER authors the envelope, including `metadata.*`.**
  `finalize_phenotype_extraction` -> builder finalize ->
  `materialize_phenotype_builder_state`, which builds the ENTIRE phenotype
  envelope (`curatable_objects[]` + `metadata`) from the staged candidates and the
  recorded-evidence snapshot.
- **There is NO model-facing channel for exclusions or ambiguities.**
  `PhenotypeStageInput` accepts only retained-annotation fields (`pending_ref_id`,
  `phenotype_annotation_object`, `evidence_record_ids`, `source_mentions`,
  `subject_identifier`, `subject_label`, `subject_type`, `subject_taxon`,
  `term_curie`, `term_label`, `data_provider`, `term_taxon_id`,
  `condition_relations`, `negated`) — no exclusion param, no reason_code param, no
  ambiguity param. `PhenotypeDiscardInput` takes only an optional free-text
  `reason`. The materializer **hard-codes** `metadata.exclusions: []`,
  `metadata.ambiguities: []`, `metadata.notes: []`, `metadata.normalization_notes:
  [<one backend string>]`, and `run_summary.excluded_count/ambiguous_count: 0`
  (`conversion.py` L829-847). `raw_mentions` is also built backend-side from the
  staged candidates (`conversion.py` L786-816), not typed by the model. So
  exclusions/ambiguities/raw-mentions never reach the envelope through the builder
  tools.
- **An exclusion is expressed by NOT staging the candidate** (or
  `discard_phenotype_observation`, whose free-text `reason` is for audit only).

The pre-rewrite BASE prompt was **incorrect/contradictory** about this. Three
places told the model to write audit material into `metadata.*` it cannot reach:
`<success_criteria>` ("Raw mentions, excluded candidates, ambiguity notes,
normalization notes, evidence, and run summaries are preserved as `metadata.*`
audit information"); `<evidence_rules>` ("Exclude or move to
`metadata.exclusions[]`"); and `<structured_output_guidance>` ("Keep audit
material, exclusions, ambiguities, normalization notes, and run counters in the
configured metadata locations") — while ALSO saying "do not add prose outside the
final finalization acknowledgment" and "Do not hand-author `curatable_objects[]`".
That is the latent contradiction the pilot flagged for phenotype. The rewrite
removes it by stating the real mechanism plainly (you never type `metadata.*`;
exclude by not staging; `discard_phenotype_observation` takes a free-text reason;
the backend materializes metadata from staged candidates and recorded evidence)
while preserving the curation intent (template rule 1): **a phenotype that is
experimentally supported but whose ontology identity or subject attribution stays
uncertain is STILL staged with the free-text statement preserved and the selectors
left for the validator, not silently dropped.** Affected entries reworded: PE-09,
PE-12, PE-13, PE-26, PE-44, PE-45. No assertion in
`test_phenotype_extractor_domain_envelope_contract.py` referenced
`metadata.exclusions[]`/`metadata.ambiguities[]` against the BASE prompt (the
fixture/schema tests build those metadata blocks themselves, backend-side; the
base-prompt assertion test
`test_phenotype_extractor_prompt_agent_and_group_rules_name_domain_contract`
asserts only the builder-tool names, `Do not hand-author curatable_objects[]`,
the validator-authority phrases, `agr_species_context_lookup`, `broad curation
lookup tools`, and the absence of `repair_*`/`normalized_id`/`candidate_terms`),
so no re-baseline was needed (verified — all asserted phrases are retained
verbatim and the forbidden ones stay absent).

### Template rule 2 — Staging cardinality matches the workflow unit (PE-09)

The unit staged once is the **retained phenotype assertion (one
PhenotypeAnnotation curatable_unit per finalized candidate)**, NOT "per entity".
The materializer emits one annotation object per finalized candidate
(`conversion.py` loops candidates, incrementing `annotation_index` once per kept
candidate). Composite descriptions are split into individual phenotype assertions
-> separate stage calls. The pre-rewrite success criterion "Each retained
phenotype is staged exactly once with `stage_phenotype_observation`" was already
per-annotation (correct unit); the rewrite keeps it per-assertion and reconciles
it with the composite-split rule (PE-04) so a passage describing several distinct
phenotypes reads as a separate stage call per phenotype assertion. No count
assertion exists in the contract test (verified).

### Template rule 3 — Do not re-duplicate core validator-delegation lines

`assembly.py::_build_core_generated_content` already injects, for the active
phenotype extraction agent (verified by rendering
`build_agent_core_prompt('phenotype_extractor')`), the five fragments the
cross-cutting `test_extractor_prompts_delegate_unresolved_state_to_validators`
requires: `Active validator binding`, `validator-bound unresolved candidates`,
`Active validator bindings own`, `validator result fields`, `envelope validation
findings`; plus `active-run evidence workspace`, the compact `Validators own these
fields` line naming `PhenotypeSubject.subject_identifier` /
`PhenotypeAnnotation.phenotype_terms[0].curie` / `taxon`, and `Active validator
bindings own validator result fields`. The base prompt keeps **phenotype-specific**
validator-authority guidance (the phenotype domain pack's active validator
bindings own final phenotype ontology term identity; subject + reference remain
under-development bindings; keep `term_curie` unset when the paper provides no
exact ontology identifier; lookup restriction) but does NOT restate the core's
exact phrasing. The base retains the verbatim phrases the contract test asserts:
`active validator bindings own` and `broad curation lookup tools`.

---

## Role / goal / success

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| PE-01 | Agent identity: Phenotype Extraction Agent for biological curation at the Alliance of Genome Resources. | `<role>` (retained) |
| PE-02 | Goal: extract experimentally supported phenotype assertions from this paper and stage each evidence-backed finding via the builder tools for active-validator resolution. "Do not hand-author `curatable_objects[]`; the backend builds the PhenotypeResultEnvelope from staged builder state." | `<goal>` (retained verbatim — contract-test token `Do not hand-author \`curatable_objects[]\``) |
| PE-03 | For each retained phenotype, preserve the phenotype description as written, the causative genotype/allele/treatment/condition, polarity, severity or penetrance when stated, organism, and life stage when stated; evidence is backend-verified source text from `read_chunk.evidence_spans[].span_id`; active validator bindings own final phenotype ontology and subject identity. | `<goal>` + `<phenotype_disambiguation_rules>` + `<validator_handoff>` |
| PE-04 | Composite descriptions are split into individual phenotype assertions while preserving shared context (per template rule 2: separate stage calls). | `<success_criteria>` + `<phenotype_disambiguation_rules>` + `<output_and_handoff_contract>` |
| PE-05 | Used document retrieval before answering. | `<success_criteria>` + `<workflow>` |
| PE-06 | Retained phenotypes are individual phenotype assertions supported by experiments reported in THIS paper. | `<success_criteria>` + `<evidence_and_curation_rules>` |
| PE-07 | Every evidence record is created from backend-generated `read_chunk.evidence_spans[].span_id` values. | `<success_criteria>` (cross-cutting token retained) |
| PE-08 | Each retained phenotype carries the free-text statement, the source mentions, evidence_record_ids, and (when paper-supported) pending subject and phenotype-term hints; active validator bindings own final ontology and subject identity. | `<success_criteria>` + `<output_and_handoff_contract>` |
| PE-09 | Each retained phenotype is staged exactly once with `stage_phenotype_observation` after `record_evidence(span_ids=[...])` creates supporting source text. | `<success_criteria>` + `<workflow>` (template rule 2: per-assertion unit) |
| PE-10 | `finalize_phenotype_extraction` is called exactly once after all retained candidates are staged. | `<success_criteria>` + `<workflow>` (verbatim "exactly once") |
| PE-11 | Final response is only the small finalization acknowledgment; backend-built objects and metadata reflect the deduplicated verified evidence set. | `<success_criteria>` + `<output_and_handoff_contract>` |
| PE-12 | The model never authors the envelope, including `metadata.*`; audit material (raw mentions, exclusions, ambiguities, normalization notes, run summaries) is backend-materialized from staged state, NOT typed by the model. | `<success_criteria>` + `<output_and_handoff_contract>` (REWORDED, template rule 1: states the real mechanism — you never type `metadata.*`; exclude by not staging — and drops the old "preserve as `metadata.*` audit information" instruction the model cannot satisfy) |
| PE-13 | A phenotype that is experimentally supported but whose ontology identity or subject attribution stays uncertain is still STAGED with the free-text statement preserved and the selectors left for the validator, not silently dropped. | `<success_criteria>` + `<stop_rules>` (REWORDED, template rule 1) |
| PE-14 | No invented phenotype descriptions, ontology identifiers, genotype/subject associations, or evidence text. | `<success_criteria>` + `<stop_rules>` + `<output_and_handoff_contract>` |

## `<search_context>` block (DROPPED / RELOCATED)

The entire `<search_context>` block is removed per the Phase C extractor
skeleton. Search-backend facts already live in the `search_document` /
`read_section` / `read_subsection` / `read_chunk` tool descriptions on the
production path. The one non-search evidence-policy fact (read_chunk returns the
backend-generated `evidence_spans[].span_id` values to select) is relocated into
`<workflow>`/`<output_and_handoff_contract>`. (The pre-rewrite phenotype block has
NO "~1500-char" size claim — that template deletion does not apply; the
"~1500-char" entry stays a `deleted` note for parity with the worked examples.)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| PE-15 | Paper was ingested through a PDF processing pipeline into structured chunks annotated with page, section, subsection, and element type. | DELETED (backend ingestion detail, not a curation rule; the page/section/subsection provenance fields already surface through `read_chunk`/`record_evidence` results and the shared evidence field shape in `<output_and_handoff_contract>`) |
| PE-16 | `search_document` queries a Weaviate vector database; default `search_mode="auto"` preserves hybrid search. | RELOCATED -> `bindings:search_document` (summary: "The default blended search combines meaning-based and exact-word matching") |
| PE-17 | Pass `search_mode="lexical"` for exact phenotype labels, genotype handles, allele names, strain names, reagent identifiers, PMIDs/DOIs, and other controlled tokens. | RELOCATED -> `bindings:search_document` (synonym: "Exact-word matching is best for specific gene symbols, strains, alleles, probes, or identifiers") |
| PE-18 | Pass `search_mode="hybrid_lexical_first"` when normal hybrid search should retry with lexical-heavy matching. | DELETED (internal retry-mode mechanic, not a curation rule; the curator-facing "very short queries automatically lean on exact-word matching" already lives in `bindings:search_document`) |
| PE-19 | `read_section`/`read_subsection` retrieve ALL chunks under a named section using the LLM-resolved hierarchy, useful for complete coverage of Results, figure legends, or Methods context. | RELOCATED -> `bindings:read_section`/`bindings:read_subsection` (summaries: "complete text of every passage in a section, grouped by the paper's own structure rather than by page order ... full coverage") |
| PE-20 | For retained evidence, call `read_chunk(chunk_id)` and select the backend-generated `evidence_spans[].span_id` values that directly support one evidence unit. | `<workflow>` + `<output_and_handoff_contract>` (KEPT — the span-id selection mechanic is curation discipline; the cross-cutting tests require the literal `read_chunk.evidence_spans[].span_id` and `record_evidence(span_ids=[...])` tokens, retained once in the workflow/output block) |
| PE-21 | The "~1500-char chunk" / search-context size claim. | DELETED (no such size claim exists in the pre-rewrite phenotype prompt; recorded for parity so the deletion ledger is explicit) |

## Tools / ordered workflow (the one place exact path matters)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| PE-22 | Tool roster (search_document; read_chunk; read_section/read_subsection; record_evidence; list/get_recorded_evidence; attach/detach_evidence_to_object; discard_recorded_evidence; update_recorded_evidence_metadata; get_agent_contract; agr_species_context_lookup; search_domain_field_terms/inspect_ontology_term/resolve_domain_field_term; stage/patch/discard/list_staged_phenotype_observations; finalize_phenotype_extraction). | `<workflow>` (compact roster retained; the evidence-workspace verbs in a supporting line; the domain-field-grounding trio surfaced in `<experimental_condition_rules>`/`<validator_handoff>`) |
| PE-23 | Workflow order: search/read (Results/figures/Methods) -> read_chunk + record_evidence once a phenotype is both curatable and experimentally supported in THIS paper -> stage each retained phenotype -> list/patch/discard -> finalize once. | `<workflow>` (ordered list; `.invariants.txt` pins the order) |
| PE-24 | `stage_phenotype_observation` payload: stable `pending_ref_id`, the free-text `phenotype_annotation_object` (statement exactly as the paper supports it), verified `evidence_record_ids`, one or more `source_mentions`, optional pending subject hints (`subject_identifier`/`subject_label`/`subject_type`/`subject_taxon`), optional pending phenotype-term hints (`term_label`; `term_curie` only when the paper directly provides an exact ontology identifier; `data_provider`/`term_taxon_id` for validator context), `condition_relations` only when stated, and `negated=true` only on an explicit negative result. | `<output_and_handoff_contract>` (full staged-field contract retained) |
| PE-25 | `list_staged_phenotype_observations` to review, `patch_`/`discard_phenotype_observation` to fix, then `finalize_phenotype_extraction(candidate_ids=[...])` exactly once with the candidate IDs the stage calls returned. | `<workflow>` (verbatim `finalize_phenotype_extraction(candidate_ids=`) |
| PE-26 | `agr_species_context_lookup` only when a paper-supported organism clue needs narrow provider/species/taxon context for validator selectors; broad curation lookup tools are NOT used to resolve phenotype terms or subject entities. | `<validator_handoff>` (verbatim `agr_species_context_lookup` + `broad curation lookup tools` — contract-test tokens) |

## Validator authority / handoff / lookup restriction

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| PE-27 | Active validator bindings declared by the phenotype domain pack own final phenotype ontology term identity; the extractor stages evidence-backed pending term candidates (label and, when directly available, an exact paper-supplied CURIE) plus provider/taxon hints; it does not invent ontology identifiers, repair validator failures, or present lookup hints as validated results. | `<validator_handoff>` (verbatim "active validator bindings own" via the phrase "active validator bindings own final phenotype ontology"; `repair`-free) |
| PE-28 | Keep `term_curie` unset when the paper supports a phenotype label but does not provide an exact ontology identifier — the active ontology validator resolves the candidate. | `<validator_handoff>` (REWORDED context, template rule 1: still-uncertain-but-supported phenotype is STILL staged) |
| PE-29 | Do not call broad curation lookup tools to resolve phenotype terms or subject entities; extraction-time lookup for those is limited to provider/species/taxon context through `agr_species_context_lookup`. | `<validator_handoff>` |
| PE-30 | The ONE exception is experimental-condition grounding: use `search_domain_field_terms` / `resolve_domain_field_term` / `inspect_ontology_term` to ground ZECO condition class/id CURIEs as in `<experimental_condition_rules>` (obscure ontology terms must be looked up, not guessed). The composite condition validator still owns final condition identity and coherence. | `<validator_handoff>` + `<experimental_condition_rules>` |
| PE-31 | Subject and reference resolution remain under-development validator bindings; stage paper-backed subject hints when available, but the concrete Gene/Allele/AGM subtype and durable reference are resolved downstream; pending subject/reference and pending ontology resolution keep export and write blocked, which is the intended posture for this pack. | `<validator_handoff>` |
| PE-32 | single_reference (the source paper) is resolved downstream from the curation workspace document identity, not from free text. Do NOT stage a reference. | `<validator_handoff>` |

## Experimental-support gate / include / prior-work / methods / control

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| PE-33 | A phenotype is "experimentally supported" when this paper presents observational or quantitative data for that phenotype, including summary or conclusion statements about experiments performed in this paper. | `<evidence_and_curation_rules>` |
| PE-34 | Include: a direct observed phenotype caused by a specific genotype/allele/perturbation/treatment/condition; quantitative or qualitative results reporting penetrance/severity/developmental stage/comparison/polarity/conditional context; explicit negative results (no phenotype detected under defined conditions); a figure/table/panel/Results statement a curator could cite directly. | `<evidence_and_curation_rules>` |
| PE-35 | Exclude: phenotypes mentioned only as previously reported, predicted, hypothesized, narrative, or review-style claims. | `<evidence_and_curation_rules>` (retained VERBATIM as a trailing-period sentence — `test_non_gene_extractor_prompts_include_record_evidence_domain_guidance` asserts the exact substring "Phenotypes mentioned only as previously reported, predicted, hypothesized, narrative, or review-style claims."; the rewrite leads with "Exclude these, and leave them unstaged:" so the asserted sentence stays intact and period-terminated) |
| PE-36 | Exclude: parenthetical author-year citations signaling the finding originates from prior work rather than this paper. | `<evidence_and_curation_rules>` |
| PE-37 | Exclude: methods/protocol mentions (strain lists, buffer recipes, construct generation) unless the paper also presents experimental results involving that entity. | `<evidence_and_curation_rules>` |
| PE-38 | Exclude: wild-type/control baseline descriptions that do not state a mutant or perturbed phenotype assertion. | `<evidence_and_curation_rules>` |
| PE-39 | Exclusions are expressed by NOT staging the candidate; there is no `metadata.*` write channel; `discard_phenotype_observation` takes a free-text reason for audit. | `<evidence_and_curation_rules>` (REWORDED from "Exclude or move to `metadata.exclusions[]`", template rule 1) |

## Phenotype evidence (strong/weak + quote examples)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| PE-40 | Call `record_evidence` only for evidence spans that support a curatable phenotype finding in THIS paper. (Record only for phenotypes you intend to stage.) | `<evidence_and_curation_rules>` |
| PE-41 | Strong phenotype evidence usually: describes an observed phenotype caused by a specific genotype/allele/perturbation/treatment; reports penetrance/severity/developmental stage/comparison/polarity for that phenotype; explicitly states a negative result such as no phenotype detected under defined conditions; gives a figure/table/results statement a curator could cite directly for the assertion. | `<evidence_and_curation_rules>` (non-gene-evidence policy shares the "Strong ... evidence usually does one or more of the following:" family; the cross-cutting test requires the literal `Strong quote examples:` header, retained) |
| PE-42 | Strong quote examples ("daf-2(e1370) adults produced 40% fewer progeny than wild type."; "No axon guidance defects were observed in unc-40 mutants under these conditions."). | `<evidence_and_curation_rules>` (cross-cutting test requires the literal `Strong quote examples:` header — retained) |

## Phenotype disambiguation (phenotype vs disease / normal biology / polarity / negative / linking)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| PE-43 | Phenotype vs disease: phenotypes are observable characteristics of an organism with a specific genotype; in model organisms observable traits (neurodegeneration, motor defects, sensory/cilia defects) are phenotypes; when a model-organism phenotype is described as resembling a human disease, capture the observable trait as a phenotype and treat the disease name as context only. | `<phenotype_disambiguation_rules>` |
| PE-44 | Phenotype vs normal biology: "Neurons migrate to the cortex during development" -> normal process, not a phenotype; "Neurons fail to migrate to the cortex in Reelin mutants" -> phenotype (migration failure); the key is whether the observation describes a deviation from normal caused by a specific genotype or condition. | `<phenotype_disambiguation_rules>` |
| PE-45 | Phenotype polarity and severity: distinguish "loss of X" / "gain of X" / "reduced X" / "enhanced X"; "lethal" alone differs from stage-specific lethality (embryonic, larval, adult); penetrance: "fully penetrant lethality" vs "30% penetrant lethality". | `<phenotype_disambiguation_rules>` |
| PE-46 | Negative results: "No phenotype was observed in X mutants" IS a finding; capture it with `negated=true`; absence of discussion about a phenotype is NOT a negative result. | `<phenotype_disambiguation_rules>` + `<output_and_handoff_contract>` (verbatim `negated=true`) |
| PE-47 | Genotype-phenotype linking: always associate each phenotype with the specific genotype/condition that causes it; when multiple genotypes are discussed, link each phenotype to the correct genotype (staged as the paper-backed subject hint). | `<phenotype_disambiguation_rules>` |

## Evidence span workflow (de-duped, see below)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| PE-48 | When a phenotype is curatable and experimentally supported, `read_chunk(chunk_id)` for the relevant chunk, select the `evidence_spans[].span_id` values that directly support one evidence unit, then `record_evidence(span_ids=[...])`; multiple `span_ids` in one call make one evidence record; use separate calls for truly disjoint evidence units; do not write/reconstruct/trim/paraphrase source quote text yourself. | `<workflow>` + `<output_and_handoff_contract>` (KEPT — cross-cutting `record_evidence(span_ids=[...])` + "evidence unit" tokens, once) |
| PE-49 | If span-backed evidence cannot be verified with `record_evidence`, do not stage that source text as supporting evidence; pass the returned `evidence_record_ids` to `stage_phenotype_observation`. | `<output_and_handoff_contract>` + `<stop_rules>` |

## Experimental conditions (ZECO/XCO/ChEBI grounding; no-quote evidence contract)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| PE-50 | Experimental conditions describe the experimental variables under which a phenotype was observed (chemical treatments, temperature/radiation exposures); extract ALL conditions the paper explicitly states for a retained phenotype, and ONLY those — never infer/default/invent; conditions are subtle and sparse, most assertions have none; omit `condition_relations` entirely when none stated. | `<experimental_condition_rules>` (retained nearly verbatim) |
| PE-51 | `condition_relations` is a list of ConditionRelation entries: `condition_relation_type` named exactly as a Condition Relation Type term (typically `has_condition`, or `induced_by`/`ameliorated_by`/`exacerbated_by`; negated forms `not_induced_by`/`not_ameliorated_by`/`not_exacerbated_by` only on an explicit negative; never infer a negated relation from absence of an effect). | `<experimental_condition_rules>` |
| PE-52 | Each condition sets only paper-stated fields: `condition_class_curie` (ZECO class; GROUND via `search_domain_field_terms`/`resolve_domain_field_term` with `field_path="condition_relations.conditions.condition_class.curie"`; do NOT guess ZECO from memory); `condition_id_curie` (more specific ZECO/XCO; ground via `field_path="condition_relations.conditions.condition_id.curie"`); `condition_chemical_curie` (ChEBI CURIE for a chemical treatment); `condition_taxon_curie` (NCBITaxon only when a distinct taxon, rare); `condition_free_text` (quantity/dose qualifier as written); `condition_summary` (short synthesized phrase + anchoring source statement). | `<experimental_condition_rules>` |
| PE-53 | A condition's MEANING is the COMBINATION of its fields; stage them together so the composite condition validator checks per-field existence AND cross-field coherence. | `<experimental_condition_rules>` |
| PE-54 | EVIDENCE CONTRACT: conditions carry NO quote text; they are read from the SAME evidence the annotation already cites (`record_evidence(span_ids=[...])` -> `evidence_record_ids`); the backend resolves the exact source text for the composite validator; never type a condition quote. | `<experimental_condition_rules>` |

## Output / handoff contract (PhenotypeResultEnvelope)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| PE-55 | Backend builder finalization creates the PhenotypeResultEnvelope and materializes the curatable_objects and `metadata.*` from staged builder state and recorded evidence; you never type that graph. Do not hand-author the result objects (`curatable_objects[]`, the legacy top-level semantic lists, `normalized_payload`, `annotation_drafts`, or full-envelope JSON); drive everything through the builder tools. | `<output_and_handoff_contract>` (NOTE: `test_phenotype_editable_prompts_do_not_duplicate_generated_contract_facts` forbids the literal fragments `top-level \`items[]\``, `` `annotations[]` ``, and `PhenotypeAnnotation` in the editable prompt — these are generated-contract internals injected by the runtime contract. Phenotype is a BUILDER extractor, so it must NOT enumerate the legacy `items[]`/`annotations[]` list the way an envelope extractor does; the no-hand-author guidance is phrased as "the legacy top-level semantic lists" instead. `curatable_objects[]` is still present via "Do not hand-author `curatable_objects[]`", which the non-gene-evidence guard requires.) |
| PE-56 | Every evidence record carries the shared field shape copied from the `record_evidence` response, never typed by you (`entity`, `verified_quote`, `page`, `section`, optional `subsection`, `chunk_id`, optional `figure_reference`); cite verified evidence with `evidence_record_ids` only; before finalization, review the active-run evidence workspace and keep only the evidence records the staged phenotypes rely on. | `<output_and_handoff_contract>` |
| PE-57 | For each retained phenotype, `stage_phenotype_observation` exactly once with the full staged-field contract (PE-24); when a passage describes multiple distinct phenotypes, stage a separate observation per phenotype assertion (template rule 2 sub-case). | `<output_and_handoff_contract>` |
| PE-58 | `finalize_phenotype_extraction(candidate_ids=[...])` once after every retained phenotype is staged, with the candidate IDs the stage calls returned; the backend then materializes one pending phenotype annotation per finalized candidate with BLOCKED export/write posture; your final response is the small finalization acknowledgment only. | `<output_and_handoff_contract>` (verbatim `finalize_phenotype_extraction`; the per-candidate object is described as "one pending phenotype annotation" — the literal token `PhenotypeAnnotation` is forbidden in the editable prompt by `test_phenotype_editable_prompts_do_not_duplicate_generated_contract_facts`) |

## Stop / abstain rules

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| PE-59 | If no phenotype finding has direct experimental support in this paper, stage nothing and report the empty result (finalizing with an empty candidate list is not valid; simply do not stage candidates). | `<stop_rules>` (REWORDED, template rule 1: drops the old "keep raw mentions/exclusions/ambiguities as audit context" instruction the model cannot satisfy) |
| PE-60 | If phenotype identity or genotype attribution is unresolved after using the available paper context, prefer keeping the free-text statement with paper-backed hints over forcing normalization; leave ontology/subject identity to the validators. | `<stop_rules>` (template rule 1: uncertain-but-supported phenotype STILL staged) |
| PE-61 | If span resolution fails, call `read_chunk` again for the current span IDs or drop that evidence; do not invent quotes or merge disconnected fragments into one implied contiguous passage. | `<stop_rules>` |
| PE-62 | Never invent phenotype descriptions, ontology identifiers, genotype/subject associations, or evidence text. | `<stop_rules>` + `<output_and_handoff_contract>` |

## FB / MOD group-rule hooks (rendered with the group)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| PE-63 | The 7 group rules (FB/HGNC/MGI/RGD/SGD/WB/ZFIN) carry organism-specific phenotype nomenclature, life-stage vocabulary, FBcv/MP/etc. ontology-hint guidance, and the shared "let the active ontology validator resolve the final term" footer; the base rewrite must keep rendering cleanly under each. | Group rules (`group_rules/*.yaml`) — UNCHANGED in this task (the group_rules metadata-framing cleanup is a separate tracked task). `.fb.txt` pins the load-bearing FB phrases (including `let the active ontology validator resolve the final term`, the phrase `test_phenotype_extractor_prompt_agent_and_group_rules_name_domain_contract` asserts via `validator resolve the final term`) so the base rewrite does not break the combined render. |

---

## De-dup of the evidence-span mechanic (per skeleton)

The pre-rewrite prompt restated the `record_evidence` span mechanic in four
places (`<search_context>` last bullet, `<tools>` workflow step 2, and the
`<phenotype_specific_evidence_guidance>` lead-in / `<structured_output_guidance>`).
The `record_evidence` bindings.yaml summary already carries the mechanical fact
("Turns chosen snippets ... Each call saves one piece of evidence; if several
snippets are chosen together, they are stored as one joined quote"), and the
locked core injects the span-evidence policy and the `active-run evidence
workspace` token. The rewrite keeps the **curation guidance** (record only for
spans that support a curatable phenotype finding in THIS paper; one call = one
evidence unit; no paraphrase; multiple span_ids = one record) once, in
`<evidence_and_curation_rules>` + `<workflow>` + `<output_and_handoff_contract>`,
and stops restating the tool mechanic. The literal tokens the cross-cutting
contract tests require (`read_chunk.evidence_spans[].span_id`,
`record_evidence(span_ids=[...])`, "evidence unit", "Strong quote examples:",
`curatable_objects[]`, `evidence_record_ids`, "active-run evidence workspace" from
the core) are retained exactly once each in the workflow/evidence/output block (or
supplied by the locked core for "active-run evidence workspace").

## Reason codes (none in this prompt — no `.reason_codes.txt`)

**Divergence from the task brief, resolved by code + precedent.** The task brief
framed phenotype_extractor as "a domain-envelope extractor [that] enumerates
canonical reason_codes" and asked for a `.reason_codes.txt` sourced from the
`ExclusionReasonCode` enum. The code says otherwise, and the two most-recent
builder-extractor rewrites (disease, allele) set the precedent:

- phenotype_extractor is a **builder** extractor, not an envelope extractor —
  `packages/alliance/agents/phenotype_extractor/agent.yaml` has `output_schema:
  null`, so `_is_builder_extractor("phenotype_extractor")` is True in
  `test_non_gene_evidence_prompt_policy.py`.
- The pre-rewrite phenotype BASE prompt enumerates **no** canonical exclusion
  `reason_code` list (verified — `_listed_reason_codes(content)` returns the empty
  set; there is no `<exclusion_reason_codes>` / "Exclude with canonical
  reason_code when applicable:" block).
- `PhenotypeStageInput`/`PhenotypeDiscardInput` expose **no** `reason_code`
  parameter (the discard `reason` is free text), and the phenotype domain pack
  defines **no** phenotype-specific `ExclusionReasonCode` members.

Therefore NO `phenotype_extractor.reason_codes.txt` is created — exactly as
disease and allele (both builder extractors with no pre-existing enumeration)
correctly created none. Introducing one would ADD a rule the prompt never carried,
which the Phase C loss-full-but-preserve gate forbids; it would also make the
model name canonical codes it has no channel to record (the materializer
hard-codes `exclusions: []`). The reason-code-schema guard
`test_extractor_prompt_reason_codes_match_schema_contract` stays green: the
rewritten BASE prompt lists no codes (the empty set is a subset of the schema enum,
and the non-empty requirement applies only to envelope extractors). Gene_extractor
and gene_expression DID get a `.reason_codes.txt` because their pre-rewrite prompts
already enumerated reason codes (a load-bearing rule to preserve); phenotype is in
the disease/allele bucket, not the gene/gene_expression bucket.

## Contract-test re-baseline (test_phenotype_extractor_domain_envelope_contract.py)

`test_phenotype_extractor_prompt_agent_and_group_rules_name_domain_contract`
asserts specific phrases against the BASE prompt content. Re-baseline decisions
(no count/ordering weakened; every asserted phrase preserved verbatim):

- All BASE-prompt-asserted phrases are **retained verbatim** in the rewrite, so
  the existing assertions pass **unchanged**: ``Do not hand-author
  `curatable_objects[]` `` (PE-02), `stage_phenotype_observation` (PE-24),
  `finalize_phenotype_extraction` (PE-25/PE-58), `agr_species_context_lookup`
  (PE-26), `active validator bindings own` (PE-27, present via "active validator
  bindings own final phenotype ontology"), `broad curation lookup tools` (PE-26/
  PE-29).
- The negative assertions stay satisfied (verified — none introduced):
  `repair_mode`, `repair_notes`, `repair_hints`, `normalized_id`,
  `candidate_terms` are ABSENT from the rewritten base prompt content.
- The group-rule assertions (`validator resolve the final term` present;
  `PhenotypeAnnotation`/`PhenotypeSubject`/`PhenotypeTerm`/`metadata.ambiguities[]`
  absent) target `group_rules/*.yaml`, which this task does NOT edit, so they are
  unaffected.
- The schema/fixture tests (`..._schema_accepts_domain_pack_objects_and_metadata`,
  `..._domain_pack_loads_tool_verified_pending_fixture`, etc.) build their
  `metadata.exclusions[0].reason_code == "previously_reported"` blocks themselves,
  backend-side, from fixtures — they assert nothing about the BASE prompt text and
  are unaffected by the prompt rewrite.

Two additional pre-existing phenotype-only guards constrained the wording (no
assertion in either was edited — the prompt was written to satisfy them):

- `test_assembly.py::test_phenotype_editable_prompts_do_not_duplicate_generated_contract_facts`
  forbids the literal fragments `top-level \`items[]\``, `` `annotations[]` ``, and
  the bare token `PhenotypeAnnotation` (among other generated-contract internals)
  in the editable phenotype prompt — those are injected by the runtime contract.
  The rewrite therefore does NOT enumerate the legacy `items[]`/`annotations[]`
  list (phenotype is a builder extractor, not an envelope extractor) and refers to
  the per-candidate object as "one pending phenotype annotation" rather than
  "PhenotypeAnnotation". `curatable_objects[]` stays present via "Do not
  hand-author `curatable_objects[]`" (PE-02/PE-55).
- `test_non_gene_evidence_prompt_policy.py::test_non_gene_extractor_prompts_include_record_evidence_domain_guidance[phenotype_extractor]`
  asserts the exact period-terminated substring "Phenotypes mentioned only as
  previously reported, predicted, hypothesized, narrative, or review-style claims."
  (PE-35). The rewrite keeps that sentence intact by leading the bullet with
  "Exclude these, and leave them unstaged:".

**Conclusion: no test assertion is edited, deleted, or weakened by this rewrite.**
Every asserted phrase is preserved verbatim in the base prompt or lives in an
untouched group-rule file. If any assertion had needed to move, it would be listed
here with a same-commit replacement; none did.
