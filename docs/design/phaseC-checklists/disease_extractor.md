# Phase C semantic-coverage checklist: `disease_extractor` (Wave 2)

This is the **authoritative inventory source** for the outcome-first rewrite of
`packages/alliance/agents/disease_extractor/prompt.yaml`. Every load-bearing rule
in the pre-rewrite prompt is listed here with a stable ID (DE-NN) and its new
home in the rewritten prompt, OR an explicit, justified relocation/deletion. The
harness inventories (`phase_c_inventories/disease_extractor.txt`,
`.invariants.txt`, `.dropped.json`, and the `.fb.txt` group render) are derived
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

`<role>` -> `<goal>` -> `<success_criteria>` -> `<evidence_and_curation_rules>`
(experimental-support gate + strong/weak disease evidence + strong/weak quote
examples + disambiguation + disease-vs-phenotype + model-vs-disease + drug-term +
exclusion-by-not-staging, collapsed) -> `<subject_and_relation_rules>` (subject
selects the concrete subtype + relation sets, preserved) -> `<validator_handoff>`
(validator authority + DOID/subject/relation/ECO/data-provider delegation +
lookup restriction) -> `<workflow>` (ordered search -> record_evidence -> stage ->
review/finalize) -> `<experimental_condition_rules>` (condition_relations,
preserved) -> `<output_and_handoff_contract>` (field shape + builder stage call +
finalize + no-author envelope) -> `<stop_rules>`.

---

## Template rules applied (Phase C builder-extractor gate)

### Template rule 1 — Metadata exclusions/ambiguities mechanism (verified)

**Verified against the code**
(`tools/disease_builder_tools.py::DiseaseStageInput` +
`domain_packs/disease/builder_conversion.py::materialize_disease_builder_state`):

- **The model NEVER authors the envelope, including `metadata.*`.**
  `finalize_disease_extraction` -> builder finalize ->
  `materialize_disease_builder_state`, which builds the ENTIRE disease envelope
  (`curatable_objects[]` + `metadata`) from the staged candidates and the
  recorded-evidence snapshot.
- **There is NO model-facing channel for exclusions or ambiguities.**
  `DiseaseStageInput` accepts only retained-annotation fields (`pending_ref_id`,
  `mention`, `disease_name`, `disease_curie`, `role`, `confidence`,
  `data_provider`, `evidence_record_ids`, `source_mentions`, `subject_*`,
  `disease_relation_name`, `evidence_code_curies`, `genetic_sex_name`,
  `disease_qualifier_names`, `with_gene_identifiers`, `condition_relations`,
  `negated`) — no exclusion param, no reason_code param, no ambiguity param. The
  materializer **hard-codes** `metadata.exclusions: []`, `metadata.ambiguities:
  []`, `metadata.notes: []`, `metadata.normalization_notes: [<one backend
  string>]`, and `run_summary.excluded_count/ambiguous_count: 0`
  (`builder_conversion.py` L916-934). So exclusions/ambiguities never reach the
  envelope through the builder tools.
- **An exclusion is expressed by NOT staging the candidate** (or
  `discard_disease_observation`, whose `DiseaseDiscardInput` takes a free-text
  `reason`). `metadata.evidence_records[]` includes every non-discarded
  recorded-evidence record, but an evidence record for a non-staged disease is
  just an unreferenced record, not an exclusion.

The pre-rewrite prompt (inherited from the envelope era) told the model in
`<experimental_support_rules>` that background-only diseases "belong in
`metadata.exclusions[]` (do not stage them)". That is contradictory for the
running model — `<structured_output_guidance>` already said not to author prose
outside the ack, and there is no `metadata.exclusions[]` write channel. The
rewrite removes the surface contradiction by stating the real mechanism plainly
(you never type `metadata.*`; exclude by not staging; `discard_disease_observation`
takes a free-text reason; the backend materializes metadata) while keeping the
curation intent: a disease that is experimentally supported but whose ontology
identity or subject attribution stays uncertain is STILL staged with the paper
label preserved and `disease_curie`/subject left for the validator, not silently
dropped. Affected entries reworded: DE-13, DE-29, DE-30, DE-47. No assertion in
`test_disease_extractor_domain_envelope_contract.py` referenced
`metadata.exclusions[]`/`metadata.ambiguities[]`, so no re-baseline was needed
(verified — the contract test asserts only `Do not hand-author
curatable_objects[]`, the builder-tool names, the subject/relation phrases, the
validator-authority phrase, and `agr_species_context_lookup`, all retained
verbatim).

### Template rule 2 — Staging cardinality matches the workflow unit (DE-09)

The unit staged once is the **retained disease assertion (one concrete
GeneDiseaseAnnotation / AlleleDiseaseAnnotation / AGMDiseaseAnnotation per
finalized candidate)**, NOT "per entity". The materializer emits one annotation
object per finalized candidate (`builder_conversion.py` loops candidates and
increments `annotation_index` once per candidate). Composite mentions are split
into separate disease assertions -> separate stage calls. The pre-rewrite success
criterion "Each retained disease is staged exactly once with
`stage_disease_observation`" was already per-annotation (correct unit); the
rewrite keeps it per-disease-assertion and reconciles it with the composite-split
rule (DE-04) so "one disease that the paper asserts twice with distinct subjects"
reads as two assertions = two stage calls. No count assertion exists in the
contract test (verified).

### Template rule 3 — Do not re-duplicate core validator-delegation lines

`assembly.py::_build_core_generated_content` already injects, for every active
extraction agent, the lines:
- "validator-bound unresolved candidates must be allowed through the schema when
  evidence supports the candidate but normalized identity is pending"
- "Active validator bindings own validator result fields and envelope validation
  findings; do not author validator outputs yourself"
- "Validators own these fields; do not invent their identifiers: ..."

(Verified by rendering `build_agent_core_prompt('disease_extractor')`: the core
supplies all five fragments the cross-cutting
`test_extractor_prompts_delegate_unresolved_state_to_validators` requires —
`Active validator binding`, `validator-bound unresolved candidates`, `Active
validator bindings own`, `validator result fields`, `envelope validation
findings`.) The base prompt keeps **disease-specific** validator-authority
guidance (the disease domain pack owns DOID / subject / relation / ECO /
data-provider verification; lookup restriction; keep `disease_curie` unset when
no exact DOID) but does NOT restate the core's exact `validator-bound unresolved
candidates` / `Active validator bindings own ...` phrasing. The base retains the
verbatim phrase the disease contract test asserts: "Active validator bindings
declared by the disease domain pack own final disease ontology".

---

## Role / goal / success

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| DE-01 | Agent identity: Disease Extraction Agent for biological curation at the Alliance of Genome Resources. | `<role>` (retained) |
| DE-02 | Goal: extract experimentally supported disease assertions from this paper and stage each evidence-backed finding via the builder tools for active validator resolution. | `<goal>` |
| DE-03 | No-hand-author rule: "Do not hand-author `curatable_objects[]`; the backend builds the DiseaseExtractionResultEnvelope from staged builder state and materializes the CONCRETE GeneDiseaseAnnotation / AlleleDiseaseAnnotation / AGMDiseaseAnnotation chosen by the subject kind." | `<goal>` (retained verbatim — contract-test tokens `Do not hand-author curatable_objects[]`, `DiseaseExtractionResultEnvelope`, `GeneDiseaseAnnotation / AlleleDiseaseAnnotation / AGMDiseaseAnnotation`) |
| DE-04 | Composite mentions (e.g. "breast and ovarian cancer") are split into individual disease assertions / separate stage calls. | `<success_criteria>` + `<evidence_and_curation_rules>` |
| DE-05 | Per-retained-disease capture set: mention (as written), disease term name (+ DOID only when paper supplies an exact identifier), SUBJECT when paper-supported, role, data provider, ECO evidence codes when stated, relation. | `<goal>` + `<output_and_handoff_contract>` |
| DE-06 | Evidence is backend-verified source text selected from `read_chunk.evidence_spans[].span_id` values. | `<goal>` + `<success_criteria>` (cross-cutting token retained) |
| DE-07 | Must use document retrieval before answering. | `<success_criteria>` + `<workflow>` |
| DE-08 | Every evidence record is created from backend-generated `read_chunk.evidence_spans[].span_id` values. | `<success_criteria>` (verbatim) |
| DE-09 | Each retained disease assertion is staged once with `stage_disease_observation` after `record_evidence(span_ids=[...])` creates supporting source text. | `<success_criteria>` + `<workflow>` (template rule 2: per-assertion unit; composite/distinct subjects = distinct stage calls) |
| DE-10 | Each retained disease carries: mention, disease term name (+ DOID only when supported), role, confidence, data provider, verified `evidence_record_ids`, one or more `source_mentions`, and when paper-supported the subject (`subject_type` + `subject_identifier`/`subject_label`), `disease_relation_name`, and ECO `evidence_code_curies`. | `<success_criteria>` + `<output_and_handoff_contract>` |
| DE-11 | `finalize_disease_extraction` is called exactly once after all retained candidates are staged. | `<success_criteria>` + `<workflow>` (verbatim "exactly once") |
| DE-12 | Final response is only the small finalization acknowledgment. | `<success_criteria>` + `<output_and_handoff_contract>` |
| DE-13 | The model never authors the envelope, including `metadata.*`; backend-built objects and metadata reflect the deduplicated verified evidence set. | `<success_criteria>` + `<output_and_handoff_contract>` (REWORDED, template rule 1: replaced the latent "belongs in `metadata.exclusions[]`" framing with the real mechanism — you never type `metadata.*`; exclude by not staging) |

## `<search_context>` block (DROPPED / RELOCATED)

The entire `<search_context>` block is removed per the Phase C extractor
skeleton. Search-backend facts already live in the `search_document` /
`read_section` / `read_subsection` / `read_chunk` tool descriptions on the
production path. The one non-search evidence-policy sentence is relocated into
`<evidence_and_curation_rules>`/`<workflow>`. (The pre-rewrite disease block has
NO "~1500-char" size claim — that template deletion does not apply here.)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| DE-14 | Paper ingested through a PDF pipeline into structured chunks annotated with page/section/subsection/element type. | DELETED (backend ingestion detail, not a curation rule; chunk provenance fields already surface through `read_chunk`/`record_evidence` results) |
| DE-15 | `search_document` queries Weaviate; default `search_mode="auto"` preserves hybrid search. | RELOCATED -> `bindings:search_document` (summary: "The default blended search combines meaning-based and exact-word matching") |
| DE-16 | Pass `search_mode="lexical"` for exact disease names, ontology IDs, abbreviations, strain names, PMIDs/DOIs, controlled tokens. | RELOCATED -> `bindings:search_document` (synonym: "Exact-word matching is best for specific gene symbols, strains, alleles, probes, or identifiers") |
| DE-17 | Pass `search_mode="hybrid_lexical_first"` when hybrid search should retry with lexical-heavy matching. | DELETED (internal retry-mode mechanic, not a curation rule; the curator-facing "very short queries automatically lean on exact-word matching" already lives in `bindings:search_document`) |
| DE-18 | `read_section`/`read_subsection` retrieve ALL chunks under a named section via the LLM-resolved hierarchy for complete coverage. | RELOCATED -> `bindings:read_section` / `bindings:read_subsection` (summaries: "complete text of every passage in a section, grouped by the paper's own structure rather than by page order ... full coverage") |
| DE-19 | For retained evidence, `read_chunk(chunk_id)` and select backend-generated `evidence_spans[].span_id` values that directly support one evidence unit. | `<workflow>` + `<evidence_and_curation_rules>` (KEPT — the span-id selection mechanic is curation discipline; the cross-cutting tests require the literal `record_evidence(span_ids=[...])` and "evidence unit" tokens, retained once in the evidence/workflow block) |

## Tools / ordered workflow (the one place exact path matters)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| DE-20 | Tool roster (search_document; read_section/read_subsection; read_chunk; record_evidence; list/get_recorded_evidence; attach/detach_evidence_to_object; discard_recorded_evidence; update_recorded_evidence_metadata; agr_species_context_lookup; stage/patch/discard/list_staged_disease_observations; finalize_disease_extraction; plus the condition-grounding resolver trio). | `<workflow>` (compact roster retained; one line per builder verb) |
| DE-21 | Workflow order: search/read -> read_chunk + record_evidence once a disease assertion is curatable + experimentally supported -> stage each retained disease -> list/patch/discard -> finalize once. | `<workflow>` (ordered list; `.invariants.txt` pins the order) |
| DE-22 | `stage_disease_observation` payload: stable `pending_ref_id`, exact `mention`, `disease_name` (DO label hint), `disease_curie` only when paper supplies exact DOID, `role` (primary/background/comparative/model_context/unspecified), `confidence` (high/medium/low), `data_provider`, verified `evidence_record_ids`, one or more `source_mentions`, optional subject (`subject_type` gene/allele/agm + `subject_identifier` + `subject_label`), optional `disease_relation_name`, optional `evidence_code_curies`. | `<output_and_handoff_contract>` (full staged-field contract retained) |
| DE-23 | Optional sparse staged slots, explicit-only (never inferred/defaulted): `genetic_sex_name` (female/hermaphrodite/male/pooled sexes/unknown sex) only when the paper states the sex; `disease_qualifier_names` (susceptibility_to/resistance_to/severity_of/onset_of/penetrance_of/disease_progression_of/sexual_dimorphism_in) only when explicitly stated; `with_gene_identifiers` only when a distinct with/from gene is stated (do not duplicate the subject gene); `negated=true` only when the paper explicitly reports the association was NOT supported. | `<output_and_handoff_contract>` (sparse optional slots retained; never-infer guard kept) |
| DE-24 | `list_staged_disease_observations` to review, `patch_`/`discard_disease_observation` to fix, then `finalize_disease_extraction(candidate_ids=[...])` exactly once with the candidate IDs the stage calls returned. | `<workflow>` (verbatim `finalize_disease_extraction(candidate_ids=`) |

## Subject / relation rules (concrete-subtype selection)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| DE-25 | The SUBJECT selects the concrete LinkML subtype: `subject_type: gene` -> GeneDiseaseAnnotation (relations `is_implicated_in`, `is_marker_for`, `is_implicated_via_orthology`, `is_marker_via_orthology`); `subject_type: allele` -> AlleleDiseaseAnnotation (relation `is_implicated_in`); `subject_type: agm` -> AGMDiseaseAnnotation (relations `is_model_of`, `is_ameliorated_model_of`, `is_exacerbated_model_of`). | `<subject_and_relation_rules>` (verbatim — contract-test tokens `subject SELECTS which concrete subtype is written`, `is_model_of`) |
| DE-26 | When a model-organism strain/transgenic line recapitulates a human disease, subject = AGM and relation = `is_model_of`; when a gene's functional perturbation is implicated, subject = gene and relation = `is_implicated_in`. Choose the relation from the subject-type's allowed set; the active relation validator confirms it against the Disease Relation vocabulary subset. | `<subject_and_relation_rules>` |
| DE-27 | If the paper does not identify a durable gene/allele/AGM subject, stage the disease without a subject; the backend materializes the abstract DiseaseAnnotation and the active subject validator records that the subject is unresolved. | `<subject_and_relation_rules>` |

## Validator authority / handoff / lookup restriction

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| DE-28 | Active validator bindings declared by the disease domain pack own final disease ontology term identity, subject entity identity, relation vocabulary, ECO evidence codes, and data-provider identity. The extractor stages evidence-backed candidates plus paper-backed hints; it does not invent ontology identifiers, repair validator failures, or present lookup hints as validated results. | `<validator_handoff>` (verbatim — contract-test token "Active validator bindings declared by the disease domain pack own final disease ontology") |
| DE-29 | Keep `disease_curie` unset when the paper supports a disease label but does not provide an exact DOID — the active ontology validator resolves the candidate. | `<validator_handoff>` (REWORDED context, template rule 1: the still-uncertain-but-supported disease is STILL staged, label preserved, `disease_curie` left for the validator) |
| DE-30 | Do not call broad curation lookup tools to resolve disease terms, subjects, relations, evidence codes, or providers; extraction-time lookup for those is limited to provider/species/taxon context through `agr_species_context_lookup`. The ONE exception is experimental-condition grounding (ZECO/ChEBI), per `<experimental_condition_rules>`. | `<validator_handoff>` (verbatim `agr_species_context_lookup`) |
| DE-31 | single_reference (the source paper) is resolved downstream from the workspace document identity, not from free text; do NOT stage a reference (pending reference resolution is the intended posture). | `<validator_handoff>` |
| DE-32 | Do NOT extract or stage the annotation_type / curation method; the backend fixes it to manually_curated. | `<validator_handoff>` |

## Experimental-support gate

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| DE-33 | Retain only disease mentions with experimental support in this paper: new data relevant to that disease (disease-model characterization, gene-disease functional evidence, therapeutic study, or similar reported findings). | `<evidence_and_curation_rules>` |
| DE-34 | Diseases mentioned only in introduction/background without new findings, or used only as population descriptors, are excluded (do not stage). | `<evidence_and_curation_rules>` (REWORDED, template rule 1: the old "belong in `metadata.exclusions[]`" framing replaced with "do not stage them") |
| DE-35 | Parenthetical author-year citations (e.g., "(Smith et al., 2018)") signal prior work, not this paper. | `<evidence_and_curation_rules>` |
| DE-36 | When a sentence contains both previously published and novel findings, stage the novel findings and exclude the previously reported portions. | `<evidence_and_curation_rules>` |
| DE-37 | Entity mentions in methods/protocol descriptions are not findings by themselves; retain a methods entity only when the paper also presents experimental results involving it. | `<evidence_and_curation_rules>` |

## Disease evidence (strong/weak + quote examples)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| DE-38 | Call `record_evidence` only for spans that support a curatable disease relationship in THIS paper. | `<evidence_and_curation_rules>` |
| DE-39 | Strong disease evidence: links a studied gene/allele/perturbation/pathway to a disease or disease model; states the model organism/cellular system recapitulates a named human disease; describes pathological/therapeutic findings relevant to the disease in this paper; gives a figure/table/results statement tying the disease to the reported experiment. | `<evidence_and_curation_rules>` |
| DE-40 | Strong quote examples ("Loss of abc1 caused a cardiomyopathy-like phenotype ... model of dilated cardiomyopathy."; "Patient-derived DEF2 variants were associated with congenital nephrotic syndrome in our cohort."). | `<evidence_and_curation_rules>` (cross-cutting test requires the literal "Strong quote examples:" header — retained) |
| DE-41 | Weak/non-curatable disease evidence: disease mentioned only as motivation/background/population context; gene + disease near each other but no stated relationship; prior-work association with no new data; symptoms/phenotypes named without a disease concept. | `<evidence_and_curation_rules>` (contract test requires "The disease is mentioned only as motivation, background, or population context" — retained verbatim) |
| DE-42 | Weak quote examples (do not stage): "ABC1 has been implicated in dilated cardiomyopathy in previous studies."; "Parkinson disease is a major neurodegenerative disorder." | `<evidence_and_curation_rules>` |

## Disambiguation guidance

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| DE-43 | Disease name variability: same disease many names (Parkinson's disease / Parkinson disease / PD / parkinsonism) -> record the evidence-backed mention text; leave ontology resolution to the validator. Composite mentions -> split into separate stage calls. Define-then-use abbreviations -> link subsequent uses to the full disease name. | `<evidence_and_curation_rules>` |
| DE-44 | Disease vs phenotype/symptom: "Ataxia" is a disease when diagnosed/treated, a phenotype when an observable mutant trait (out of scope). "Neurodegeneration", "lethality", "sterility" are typically phenotypes/processes, not diseases. | `<evidence_and_curation_rules>` |
| DE-45 | Disease models vs actual diseases: the disease is the human condition; the model organism provides the experimental system. Capture the disease, set role=model_context, capture the AGM subject with relation `is_model_of` when the line recapitulates the disease. Do NOT exclude disease mentions just because they appear in model-organism studies. | `<evidence_and_curation_rules>` |
| DE-46 | Drug mentions containing disease terms: "anti-cancer drug X" -> "cancer" is an adjective, not a disease finding; only stage the disease if the paper presents data on the disease itself. | `<evidence_and_curation_rules>` |

## Stop / abstain rules

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| DE-47 | Stop searching when the sections where disease evidence normally appears have been checked and further searches return duplicate/background-only candidates. If no disease finding has direct experimental support, stage nothing and report the empty result. If identity/subject attribution is unresolved after available paper context, keep the evidence-backed mention with paper-backed hints over forcing normalization; leave ontology/subject/relation identity to the validators. If span-backed evidence cannot be verified with `record_evidence`, do not stage that source text. | `<stop_rules>` (REWORDED lead, template rule 1: the uncertain-but-supported disease is STILL staged for the validator, not dropped) |
| DE-48 | Follow the builder tool-loop exactly; no prose outside the final finalization acknowledgment. Never invent disease names, ontology identifiers, subject identities, relations, evidence codes, or evidence text. | `<stop_rules>` + `<output_and_handoff_contract>` |

## Experimental conditions (preserved block)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| DE-49 | Extract ALL experimental conditions the paper explicitly states for a retained disease assertion, and ONLY those — never infer/default/invent. Conditions are subtle and sparse; most assertions have none. When none, omit `condition_relations` entirely. | `<experimental_condition_rules>` (preserved) |
| DE-50 | `condition_relation_type` named exactly as a Condition Relation Type vocabulary term — typically `has_condition`, or `induced_by`/`ameliorated_by`/`exacerbated_by` when the paper frames it that way; negated forms only on an explicit negative result; never infer a negated relation from absence of an effect. | `<experimental_condition_rules>` (preserved) |
| DE-51 | Per-condition fields set only when stated: `condition_class_curie` (ZECO class TYPE) GROUNDED via `search_domain_field_terms`/`resolve_domain_field_term` with `field_path="condition_relations.conditions.condition_class.curie"`; `condition_id_curie` (ZECO/XCO) grounded similarly; `condition_chemical_curie` (ChEBI); `condition_taxon_curie` (NCBITaxon, rare); `condition_free_text` (dose/qualifier); `condition_summary`. Do NOT guess ZECO identifiers from memory. | `<experimental_condition_rules>` (preserved) |
| DE-52 | A condition's MEANING is the COMBINATION of its fields; stage them together so the composite condition validator can check per-field existence AND cross-field coherence. EVIDENCE CONTRACT: conditions carry NO quote text; they are read from the SAME evidence the annotation already cites; the backend resolves source text. Never type a condition quote. | `<experimental_condition_rules>` (preserved) |

## Output / handoff contract

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| DE-53 | Stage one retained disease assertion per `stage_disease_observation` call with the full staged-field contract (DE-22/DE-23). | `<output_and_handoff_contract>` |
| DE-54 | After all retained candidates are staged and reviewed, call `finalize_disease_extraction(candidate_ids=[...])` exactly once; the backend materializes the concrete subtype objects and metadata. Final reply is the small finalization acknowledgment only. | `<output_and_handoff_contract>` |

## FB / MOD group-rule hooks (rendered with the group)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| DE-55 | FB disease-model context: Drosophila is extensively used to model human diseases; when a paper describes a Drosophila model of a human disease, capture the human disease and note model_context as the role; do NOT exclude disease mentions just because they appear in a Drosophila study. | FB group rules (`group_rules/fb.yaml`) — UNCHANGED in this task (the group_rules metadata-framing cleanup is a separate tracked task). The base rewrite must keep rendering cleanly under the FB group; `.fb.txt` pins the load-bearing FB disease-model phrases so the base rewrite does not break the combined render. |
| DE-56 | FB fly-phenotype vs human-disease split: fly phenotypes go to phenotype_extractor; the human disease reference is disease model context, captured here. | FB group rules — UNCHANGED. |

---

## De-dup of the evidence-span mechanic (per skeleton)

The pre-rewrite prompt restated the `record_evidence` span mechanic in three
places (`<search_context>` final bullet, `<tools>` workflow step 2, and
`<disease_specific_evidence_guidance>`/`<structured_output_guidance>`). The
`record_evidence` bindings.yaml summary ("Turns chosen snippets from a passage
into a saved piece of evidence with the exact verified quote ... Each call saves
one piece of evidence; if several snippets are chosen together, they are stored
as one joined quote") already carries the mechanical fact. The locked core also
injects the span-evidence policy ("retained PDF evidence must come from
`read_chunk.evidence_spans[].span_id` values. Use `record_evidence` with
`span_ids` ... review the active-run evidence workspace"). The rewrite keeps the
**curation guidance** (record only for spans that support a curatable disease
relationship; one call = one evidence unit; no paraphrase) once, in
`<evidence_and_curation_rules>` + `<workflow>`, and stops restating the tool
mechanic. The literal tokens the cross-cutting contract tests require
(`read_chunk.evidence_spans[].span_id`, `record_evidence(span_ids=[...])`,
"evidence unit") are retained exactly once each in the workflow/evidence block.

## Reason codes (none in this prompt — no `.reason_codes.txt`)

Unlike gene_extractor and gene_expression, the pre-rewrite disease_extractor
prompt does NOT enumerate any canonical exclusion `reason_code` list, and
`DiseaseStageInput`/`DiseaseDiscardInput` expose no `reason_code` parameter (the
discard reason is free text). The disease domain pack does not define
disease-specific `ExclusionReasonCode` members. Therefore NO
`disease_extractor.reason_codes.txt` is created (introducing one would ADD a rule,
not preserve one). The `test_extractor_prompt_reason_codes_match_schema_contract`
guard is satisfied because the rewritten prompt lists no codes (empty set is a
subset of the schema enum, and the non-empty requirement applies only to envelope
extractors).

## Contract-test re-baseline (test_disease_extractor_domain_envelope_contract.py)

`test_disease_extractor_prompt_agent_and_group_rules_name_domain_contract`
asserts specific phrases against the base prompt content. Re-baseline decisions
(no count/ordering weakened; every asserted phrase is preserved verbatim):

- All asserted base-prompt phrases are **retained verbatim** in the rewrite, so
  the existing assertions pass **unchanged**: `Do not hand-author
  \`curatable_objects[]\`` (DE-03), `DiseaseExtractionResultEnvelope` (DE-03),
  `GeneDiseaseAnnotation / AlleleDiseaseAnnotation / AGMDiseaseAnnotation`
  (DE-03), `stage_disease_observation` (DE-22), `finalize_disease_extraction`
  (DE-24), `subject SELECTS which concrete subtype is written` (DE-25),
  `` `is_model_of` `` (DE-25), `Active validator bindings declared by the disease
  domain pack own final disease ontology` (DE-28), `agr_species_context_lookup`
  (DE-30), `active validator bindings own final disease ontology` (DE-28,
  lowercase substring), and the negative assertions (`agr_curation_query`,
  `repair_mode`, `repair_notes`, `repair_hints` all absent — verified, none
  introduced).
- The group-rule assertions (`DiseaseAnnotation`, `PendingDiseaseAssertionPayload`,
  `disease_annotation_object.name`, `agr_curation_query` absent) target
  `group_rules/*.yaml`, which this task does NOT edit, so they are unaffected.

**Conclusion: no contract-test assertion is edited, deleted, or weakened by this
rewrite.** Every asserted phrase is preserved verbatim in the base prompt or
lives in an untouched group-rule file. If any assertion had needed to move, it
would have been listed here with a same-commit replacement; none did.
