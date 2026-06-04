# Phase C semantic-coverage checklist: `gene_expression` (Wave 2)

This is the **authoritative inventory source** for the outcome-first rewrite of
`packages/alliance/agents/gene_expression/prompt.yaml`. Every load-bearing rule
in the pre-rewrite prompt is listed here with a stable ID (GEX-NN) and its new
home in the rewritten prompt, OR an explicit, justified relocation/deletion. The
harness inventories (`phase_c_inventories/gene_expression.txt`,
`.invariants.txt`, `.reason_codes.txt`, `.dropped.json`) are derived from this
checklist.

Legend for "New home":
- A `<section>` name is a section of the **rewritten** base prompt.
- `CORE` means the locked Generated Runtime Contract (`assembly.py::_build_core_generated_content`)
  already injects this exact line; the base prompt keeps the curator-facing
  curation rule but does NOT restate the core's exact phrasing (template rule 3).
- `RELOCATED -> <home>` / `DELETED` mean the rule's fact moves elsewhere on the
  production path or is dropped with no home; recorded in `.dropped.json`.

## Skeleton mapping (rewritten prompt sections)

`<role>` -> `<goal>` -> `<success_criteria>` -> `<evidence_and_curation_rules>`
(inclusion gate + strong/weak evidence + rescue/marker/background exclusions +
negative/unchanged + entity/reagent discipline + exclusion-by-not-staging +
canonical reason codes, collapsed) -> `<validator_handoff>` (validator authority
+ three-step resolver workflow + lookup restriction) -> `<workflow>` (ordered
search -> record_evidence -> resolver -> stage -> review/finalize) ->
`<evidence_record_contract>` (field shape + workspace review) ->
`<experimental_condition_rules>` (condition_relations, preserved) ->
`<output_and_handoff_contract>` (no-author envelope + builder workflow + staged
field contract + forbidden lists) -> `<stop_rules>`.

---

## Template rules applied (Phase C builder-extractor gate)

### Template rule 1 — Metadata exclusions/ambiguities mechanism (verified)

**Verified against the code**
(`agr_curation.py::GeneExpressionStageInput` +
`domain_packs/gene_expression/conversion.py::materialize_gene_expression_builder_state`):

- **The model NEVER authors the envelope, including `metadata.*`.**
  `finalize_gene_expression_extraction` ->
  `finalize_builder_extraction(materialize=_materialize_gene_expression_with_events)`
  -> `materialize_gene_expression_builder_state`, which builds the ENTIRE
  `GeneExpressionEnvelope` (curatable_objects + metadata) from staged candidates
  and the recorded-evidence snapshot.
- **There is NO model-facing channel for exclusions or ambiguities.**
  `GeneExpressionStageInput` accepts only retained-observation fields
  (`pending_ref_id`, `evidence_record_ids`, `where_expressed_statement`,
  `subject`, `reference`, `controlled_fields`, `condition_relations`) — no
  exclusion param, no reason_code param, no ambiguity param. The materializer
  **hard-codes** `metadata.exclusions: []`, `metadata.ambiguities: []`,
  `metadata.notes: []`, `metadata.normalization_notes: [<one backend string>]`,
  and `run_summary.excluded_count/ambiguous_count: 0` (`conversion.py`
  L795-812). So exclusions/ambiguities never reach the envelope through the
  builder tools.
- **An exclusion is expressed by NOT staging the candidate** (or
  `discard_gene_expression_observation`, which takes a free-text `reason`).
  `metadata.evidence_records[]` includes every non-discarded recorded-evidence
  record, but an evidence record for a non-staged finding is just an
  unreferenced record, not an exclusion.

The pre-rewrite prompt (inherited from the envelope era) told the model to
preserve non-curatable candidates in `metadata.exclusions[]` and uncertain ones
in `metadata.ambiguities[]`/`metadata.normalization_notes[]`/`metadata.notes[]`
while ALSO saying "do not author the envelope JSON". That is contradictory for
the running model. The rewrite removes the surface contradiction by stating the
real mechanism plainly (you never type `metadata.*`; exclude by not staging;
reason codes name your exclusion/discard reasoning; the backend materializes
metadata) while keeping the curation intent (a finding that is experimentally
supported but whose controlled identity stays uncertain is STILL staged with the
paper label preserved and the unresolved selector left for the validator).
Affected entries reworded: GEX-13, GEX-22, GEX-31, GEX-32, GEX-44, GEX-45,
GEX-46. No policy-test assertion referenced `metadata.exclusions[]` /
`metadata.ambiguities[]`, so no re-baseline was needed (verified).

### Template rule 2 — Staging cardinality matches the workflow unit (GEX-10)

The unit staged once is the **retained curatable finding (one
GeneExpressionAnnotation = one expression pattern)**, NOT "exactly once per
gene". The materializer emits one annotation object per finalized candidate
(`conversion.py` "exactly one GeneExpressionAnnotation object per annotation"),
and `stage_gene_expression_observation` is the per-finding unit; distinct
expression patterns are distinct observations -> distinct stage calls. The
rewrite states "Each retained finding is staged once ... Two distinct expression
patterns are two observations, so they are two stage calls", consistent with
`<workflow>` step 4. No count assertion exists in the policy test (verified).

### Template rule 3 — Do not re-duplicate core validator-delegation lines

`assembly.py::_build_core_generated_content` already injects, for every active
extraction agent, the lines:
- "validator-bound unresolved candidates must be allowed through the schema when
  evidence supports the candidate but normalized identity is pending"
- "Active validator bindings own validator result fields and envelope validation
  findings; do not author validator outputs yourself"
- "Validators own these fields; do not invent their identifiers: ..."

The base prompt keeps **gene-expression-specific** validator-authority guidance
(domain-pack bindings own relation/data-provider/anatomy/stage/assay/condition
verification; resolver-only writes; lookup restriction) but does NOT restate the
core's exact phrasing. The cross-cutting
`test_extractor_prompts_delegate_unresolved_state_to_validators` checks the
EFFECTIVE prompt (core + base): `validator-bound unresolved candidates`,
`Active validator bindings own`, and `validator result fields` are satisfied by
CORE; `Active validator binding` and `envelope validation findings` are
satisfied by the base. Verified.

---

## Role / goal / success

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| GEX-01 | Agent identity: Gene Expression Extraction Agent for biological curation. | `<role>` (Alliance-of-Genome-Resources scope made explicit to match the extractor family) |
| GEX-02 | Goal: extract experimentally supported gene expression assertions from this paper and stage them via builder tools. | `<goal>` |
| GEX-03 | No-hand-author rule: do not author the `GeneExpressionEnvelope`/`curatable_objects[]`; the backend materializes the envelope from staged builder state. | `<goal>` + `<output_and_handoff_contract>` |
| GEX-04 | Evidence is backend-verified source text from `read_chunk.evidence_spans[].span_id` values. | `<goal>` + `<success_criteria>` (cross-cutting token retained verbatim) |
| GEX-05 | Must use document retrieval before answering. | `<success_criteria>` + `<workflow>` |
| GEX-06 | Each retained annotation supported by an experiment in THIS paper. | `<success_criteria>` + `<evidence_and_curation_rules>` |
| GEX-07 | Each retained evidence record created from backend-generated `read_chunk.evidence_spans[].span_id` values. | `<success_criteria>` (verbatim) |
| GEX-08 | Active-run evidence reviewed before finalization, retained evidence attached to the intended staged observation. | `<success_criteria>` + `<evidence_record_contract>` |
| GEX-09 | Validator-bound fields carry evidence-backed selector inputs only; active validator bindings own final relation and data-provider verification. | `<success_criteria>` + `<validator_handoff>` (verbatim) |
| GEX-10 | Distinct expression patterns are separate annotations; each retained finding staged once with `stage_gene_expression_observation`. | `<success_criteria>` + `<workflow>` (REWORDED, template rule 2: per-finding/per-pattern, not per-gene) |
| GEX-11 | `finalize_gene_expression_extraction` called once after every retained observation is valid; final reply is a short status ack only. | `<success_criteria>` + `<workflow>` + `<output_and_handoff_contract>` |
| GEX-12 | No invented genes, IDs, reagent details, ontology terms, or evidence text. | `<success_criteria>` + `<stop_rules>` |
| GEX-13 | The model never authors the envelope, including `metadata.*`; the backend materializes objects/evidence/exclusions/ambiguities/run-counts from staged candidates + recorded evidence. | `<success_criteria>` + `<output_and_handoff_contract>` (REWORDED, template rule 1) |

## Inclusion gate / previously-reported detection

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| GEX-14 | Keep an annotation only when evidence in this paper supports an expression finding (incl. summary/conclusion statements about experiments in this paper). | `<evidence_and_curation_rules>` |
| GEX-15 | Strong indicators of direct results: figure/table/panel references such as "Fig. 3A" or "Table 2". | `<evidence_and_curation_rules>` |
| GEX-16 | Summary/conclusion statements describing results from THIS paper are valid; do not exclude them as non-experimental claims. Distinguish from summaries of previously published (externally cited) results. | `<evidence_and_curation_rules>` |
| GEX-17 | Parenthetical author-year citations (e.g. "(Smith et al., 2018)") signal prior work; exclude as previously_reported unless the sentence also reports new data. | `<evidence_and_curation_rules>` |
| GEX-18 | When a sentence contains both previously published and novel findings, extract the novel and exclude the previously reported portions, even in one sentence. | `<evidence_and_curation_rules>` |

## Strong / weak evidence + exclusions

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| GEX-19 | Strong gene expression evidence: states where/when expressed/not/enriched/reduced/induced; ties anatomy/stage/cell type/timing/condition; reports expression change after perturbation as an expression finding; figure/table/results statement citable for the pattern. | `<evidence_and_curation_rules>` |
| GEX-20 | Weak/non-curatable: rescue/ectopic overexpression where "expression" is the tool; marker-only/promoter-driven labeling; methods-only reporter/probe/strain setup; generic gene mention without a direct expression observation. | `<evidence_and_curation_rules>` (cross-cutting domain snippet "Rescue or ectopic overexpression statements where \"expression\" is only the experimental tool" retained verbatim) |
| GEX-21 | Strong and weak quote examples (unc-25/fgf8a/no-GFP-signal; rescue/Tg(kdrl:EGFP)/GFP-reporter). | `<evidence_and_curation_rules>` ("Strong quote examples:" heading retained; cross-cutting test token; midbrain-hindbrain/Tg(kdrl:EGFP) retained) |
| GEX-22 | Rescue/overexpression false positives excluded with reason_code rescue_experiment_not_expression; cue phrases; "Expression of GeneX in TissueY rescued the mutant phenotype" example; only retain when reporting WHERE a gene is expressed as a novel observation. | `<evidence_and_curation_rules>` (REWORDED lead-in to canonical-reason-code framing, template rule 1) |
| GEX-23 | Exclude marker-only visualization and promoter-driven localization used only as labeling. | `<evidence_and_curation_rules>` |
| GEX-24 | Exclude mutant-background-only observations when no direct wild-type expression finding is established. | `<evidence_and_curation_rules>` |
| GEX-25 | Exclude structural/fusion marker statements that are not new expression findings; keep valid directly-supported positives. | `<evidence_and_curation_rules>` |

## Negative / unchanged expression

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| GEX-26 | Explicit no-expression results captured with payload `negated: true`, absence wording preserved in the linked evidence record. | `<evidence_and_curation_rules>` ("negated: true" retained; the legacy "metadata evidence/raw-mention records" phrasing folded into "the linked evidence record") |
| GEX-27 | "Expression unchanged in perturbation" is generally NOT a new finding. | `<evidence_and_curation_rules>` |
| GEX-28 | Absence of discussion about expression is NOT a negative result. | `<evidence_and_curation_rules>` |

## Entity / reagent discipline

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| GEX-29 | Capture reagent genotype strings exactly as written, without inference. | `<evidence_and_curation_rules>` (verbatim — policy-test token) |
| GEX-30 | Methods/protocol entity mentions are not findings by themselves; retain a methods entity only when results involving it are also presented. | `<evidence_and_curation_rules>` |

## Exclusion-by-not-staging + canonical reason codes

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| GEX-31 | A non-curatable candidate is excluded by NOT staging it (or `discard_gene_expression_observation` for one already staged, which takes a free-text reason); there is no `metadata.*` the model writes to. | `<success_criteria>` + `<evidence_and_curation_rules>` (REWORDED, template rule 1: replaces "preserved in metadata.exclusions[]") |
| GEX-32 | Do not `record_evidence` for every expression-related phrase; record only for genes you intend to stage or to clarify a non-trivial decision. | `<evidence_and_curation_rules>` (REWORDED, template rule 1) |
| GEX-33 | Exclusion reason codes are a canonical subset of `ExclusionReasonCode`: previously_reported, non_experimental_claim, marker_only_visualization, promoter_driven_marker_localization, mutant_background_only, structural_label_or_fusion_only, rescue_experiment_not_expression, insufficient_experimental_evidence, out_of_scope, ambiguous_entity, duplicate_mention, unsupported_entity_type. | `<evidence_and_curation_rules>` (header "Exclude with canonical reason_code when applicable:" retained EXACT so `_listed_reason_codes` parses them; `.reason_codes.txt` sourced from the schema enum, the canonical owner) |

## Validator authority / resolver workflow / lookup restriction

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| GEX-34 | Active validator bindings declared by the gene-expression domain pack are the authority for final normalized fields (relation vocabulary, data-provider identity, anatomy/stage/assay/condition); extractor owns evidence-backed unresolved objects/metadata/selector inputs; no repairing validator failures, no inventing CV identifiers, no presenting lookup hints as validated results. | `<validator_handoff>` (verbatim "Active validator bindings declared by the gene-expression domain pack are the authority") |
| GEX-35 | Three-step resolver workflow for controlled/ontology selectors: `search_domain_field_terms` -> `inspect_ontology_term` -> `resolve_domain_field_term` before writing any final controlled selector. | `<validator_handoff>` + `<workflow>` (tool tokens retained; policy-test tokens) |
| GEX-36 | Search/inspect outputs are staging evidence only; only `resolve_domain_field_term` output may justify a controlled selector; validators/materializers own final acceptance. `slot_hint` routes anatomy vs cellular_component. | `<validator_handoff>` ("slot_hint" retained — policy-test token) |
| GEX-37 | No resolver option -> keep the paper label in metadata/provenance or a nullable pending selector and record the unresolved field path; do not invent ontology IDs/vocab terms/enums from memory. Paper-supplied CURIE preserved as a non-authoritative hint. Ambiguity preserved when anatomy and cellular-component both plausible or no candidate found. | `<validator_handoff>` |
| GEX-38 | Extraction-time lookup limited to `agr_species_context_lookup`; no DB/gene-identity/reference/broad-ontology/AGR-DB/SQL/API lookup. Paper-supported provider context may set `payload.data_provider.abbreviation`; validator still verifies. Unresolved outcomes remain envelope validation findings. | `<validator_handoff>` ("agr_species_context_lookup" + "broad expression ontology lookup" retained — policy-test tokens) |

## Tools / ordered workflow

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| GEX-39 | Must call document retrieval tools before answering; use `read_section`/`read_subsection` for complete coverage; stop searching a candidate when no direct evidence, continue overall. | `<workflow>` step 1 |
| GEX-40 | For each retained finding: `read_chunk(chunk_id)`, select `evidence_spans[].span_id` for one evidence unit, `record_evidence(entity=..., span_ids=[...])`; do not write quote text; on verified use returned `evidence_record_id`; reuse an existing record for the same span IDs + entity (do not call `record_evidence` again for the same unit). | `<workflow>` step 2 + `<evidence_record_contract>` (verbatim record_evidence tokens, "reuse that `evidence_record_id`", "do not call `record_evidence` again for the") |
| GEX-41 | Resolver loop per controlled selector: search -> inspect when needed -> resolve. | `<workflow>` step 3 |
| GEX-42 | `stage_gene_expression_observation` once per retained finding with pending_ref_id, verified evidence IDs, where_expressed_statement, subject, reference, resolved controlled values. | `<workflow>` step 4 + `<output_and_handoff_contract>` |
| GEX-43 | `list_staged_gene_expression_observations` to review, `patch_gene_expression_observation` for blocking issues, `discard_gene_expression_observation` for non-retained, then `finalize_gene_expression_extraction` once. | `<workflow>` step 5 + `<output_and_handoff_contract>` |
| GEX-44 | Resolver examples (anatomy WBbt site; assay MMO). | `<workflow>` (preserved) |

## Evidence record contract

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| GEX-45 | Shared evidence field shape: entity, verified_quote, page, section, optional subsection, chunk_id, optional figure_reference. | `<evidence_record_contract>` |
| GEX-46 | Apply the shape: stage `evidence_record_ids`; active-run evidence workspace = dedup union; backend materialization owns `metadata.evidence_records[]`, `curatable_objects[].evidence_record_ids`, `curatable_objects[].metadata_refs`. | `<evidence_record_contract>` (REWORDED, template rule 1: removed the "metadata.exclusions[].evidence_record_ids / metadata.ambiguities[].evidence_record_ids" bullet, which described a non-existent model write channel; "active-run evidence workspace" + "metadata.evidence_records[]" retained — cross-cutting/policy tokens) |
| GEX-47 | Multiple `span_ids` in one `record_evidence` call -> one evidence record; separate calls for disjoint units; span-failure recovery; no paraphrase/merge. | `<evidence_record_contract>` (verbatim "Multiple `span_ids` in one `record_evidence` call produce one evidence record" — cross-cutting token) |
| GEX-48 | Source quote and provenance fields are immutable after recording; workspace edits only metadata. | `<evidence_record_contract>` (verbatim — cross-cutting token) |
| GEX-49 | Final evidence review: `list_recorded_evidence`/`get_recorded_evidence`; choose `pending_ref_id` before attachment; stable ref `gene-expression-annotation-pef-1`; `attach_evidence_to_object` with pending_ref_id + field_path; retry once on invalid_request; `detach_evidence_from_object`/`discard_recorded_evidence`/`update_recorded_evidence_metadata`; never edit source quote/span/chunk/provenance. | `<evidence_record_contract>` (all evidence-workspace tool tokens + "Choose the retained observation's `pending_ref_id` before attachment" + "retry once with the" retained — policy/cross-cutting tokens) |

## Experimental conditions

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| GEX-50 | Experimental conditions are explicit-only, sparse; omit `condition_relations` when none; stage as a list of ConditionRelation. | `<experimental_condition_rules>` (preserved) |
| GEX-51 | `condition_relation_type` vocabulary (has_condition default; induced_by/ameliorated_by/exacerbated_by; negated forms only on explicit negative). | `<experimental_condition_rules>` (preserved) |
| GEX-52 | Per-condition fields: condition_class_curie (ZECO, grounded), condition_id_curie, condition_chemical_curie (ChEBI), condition_taxon_curie, condition_free_text, condition_summary; grounded via search/resolve with the named field_paths; composite meaning. | `<experimental_condition_rules>` (preserved) |
| GEX-53 | Conditions carry NO quote text; read from the same evidence the annotation cites; composite condition validator owns identity/coherence. | `<experimental_condition_rules>` (preserved) |

## Output / handoff contract + staged field contract

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| GEX-54 | Do not author/return full `GeneExpressionEnvelope` JSON; backend owns construction/evidence/provenance/defaults/validation/persistence. | `<output_and_handoff_contract>` (verbatim "Do not author or return full `GeneExpressionEnvelope` JSON" — policy token) |
| GEX-55 | Builder workflow: stage per finding; list/patch/discard; finalize; return short status; do not include the materialized envelope in the final message. | `<output_and_handoff_contract>` (verbatim "include the materialized envelope in the final assistant message" — policy token) |
| GEX-56 | Use Alliance/LinkML field paths, not legacy flat helper names; required staged values enumerated incl. relation/assay/stage resolver calls + anatomy/cellular-component resolver call. | `<output_and_handoff_contract>` (verbatim "relation resolver call, assay resolver call, stage resolver call" — policy token) |
| GEX-57 | `relation.name` resolved via `resolve_domain_field_term` before staging, set only from the resolved controlled-vocabulary value; is_expressed_in is the current fixture option but the resolver/binding owns the choice. | `<output_and_handoff_contract>` ("`relation.name`" + "controlled-vocabulary value" retained — policy tokens) |
| GEX-58 | Stage controlled selectors with resolved value as selected_value; backend records `metadata.provenance.helper_selections[]`; do not author helper_selections yourself. | `<output_and_handoff_contract>` ("metadata.provenance.helper_selections[]" + "do not author" retained — policy tokens) |
| GEX-59 | `data_provider.abbreviation` set from paper/`agr_species_context_lookup` (ZFIN/MGI/FB/WB mapping); LinkML-required; validator owns final verification. | `<output_and_handoff_contract>` ("`data_provider.abbreviation`" + "zebrafish / Danio rerio => `ZFIN`" retained — policy tokens) |
| GEX-60 | Spatial context under `expression_pattern.where_expressed` with resolver-backed anatomical_structure/cellular_component; at least one present; cellular-component-only sites (nucleus/cytoplasm) valid via GO CC slot. | `<output_and_handoff_contract>` ("`expression_pattern.where_expressed`" + "cellular-component-only sites such as nucleus or cytoplasm are valid" retained — policy tokens) |
| GEX-61 | Temporal context under `when_expressed_stage_name` / developmental_stage_start.name from resolver/explicit IDs, else literal stage phrase in metadata/provenance. | `<output_and_handoff_contract>` ("`when_expressed_stage_name`" retained — policy token) |
| GEX-62 | Assay context under `expression_experiment.expression_assay_used` from resolver MMO/explicit IDs, else explicit label as unresolved metadata. | `<output_and_handoff_contract>` ("`expression_experiment.expression_assay_used`" retained — policy token) |
| GEX-63 | Gene Expression 0.7.0: expression_experiment.single_reference = annotation single_reference; expression_experiment.entity_assayed = subject. | `<output_and_handoff_contract>` (preserved) |
| GEX-64 | Reagent/genotype/reporter/construct/strain/specimen/allele context under explicit LinkML paths (detection_reagents/specimen_genomic_model/specimen_alleles) or exact paper text in metadata with field_path + unresolved reason. | `<output_and_handoff_contract>` (preserved) |
| GEX-65 | Experimental conditions staged via `condition_relations`, not field-addressed metadata. | `<output_and_handoff_contract>` (preserved) |
| GEX-66 | Do not stage `evidence_text`, `evidence_page_numbers`, `evidence_figure_references`, `evidence_internal_citations` in payload. | `<output_and_handoff_contract>` (verbatim "Do not stage `evidence_text`" — policy token) |
| GEX-67 | Do not emit top-level legacy semantic lists (items/annotations/genes/alleles/diseases/chemicals/phenotypes) or full-envelope JSON; never invent genes/IDs/reagent details/evidence text. | `<output_and_handoff_contract>` (verbatim "Do not emit top-level legacy semantic lists" — policy token) |

## Unresolved-validation / stop rules (relocated into `<stop_rules>`)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| GEX-68 | Span-failure recovery (read_chunk again or drop; no invented quotes/merged fragments). | `<evidence_record_contract>` (Phase C lean cut: the duplicate `<stop_rules>` copy was removed as cross-section restatement; the verbatim home is now `<evidence_record_contract>`, and the retention `.txt` line was re-baselined to that copy's wording, "for current span IDs") |
| GEX-69 | Active validator bindings own final relation/data-provider/subject/reference/assay/anatomy/stage/cellular-component/UBERON-GO-slim verification + condition relation type + per-condition validation; reagent/specimen/allele validators under development; unresolved required values stay envelope validation findings; do not describe under-development fields as database-validated. | `<stop_rules>` (REWORDED from `<unresolved_validation>`, template rule 1: dropped "preserve candidate context in metadata.ambiguities[]/normalization_notes[]/notes[]" — the model has no write channel; "envelope validation findings" retained — cross-cutting token) |

---

## De-dup against the locked core (template rule 3)

The cross-cutting `test_extractor_prompts_delegate_unresolved_state_to_validators`
checks the EFFECTIVE prompt (core + base) for five fragments. Two —
`validator-bound unresolved candidates` and `Active validator bindings own
validator result fields` (hence `validator result fields`) — are injected by the
CORE (`assembly.py` L356-362, L416-420) and are NOT restated in the base prompt.
`Active validator binding` and `envelope validation findings` are kept once in
the base prompt's `<validator_handoff>` / `<stop_rules>` in
gene-expression-specific phrasing. This avoids duplicating the core's exact
lines while keeping curator-facing validator-authority guidance.

## Contract / policy-test re-baseline (test_gene_expression_prompt_policy.py)

`test_gene_expression_prompt_includes_daniela_policy_gates` asserts ~80
`in content` / `not in content` phrases against the base prompt (no count
assertions). Re-baseline decision:

- **Every asserted `in content` phrase is retained verbatim** in the rewrite
  (verified by a token-presence pass), so all positive assertions still pass
  unchanged.
- **Every asserted `not in content` phrase remains absent** (verified), so all
  negative assertions still pass unchanged.
- The policy test does NOT assert `metadata.exclusions[]` / `metadata.ambiguities[]`
  presence, so removing the inaccurate metadata-write framing required **no**
  assertion edit.

**Conclusion: no policy-test assertion is edited, deleted, or weakened.** The
two cross-cutting contract suites
(`test_record_evidence_prompt_contract.py` — gene_expression is its pilot —
and `test_non_gene_evidence_prompt_policy.py`,
`test_domain_envelope_repair_prompt_contract.py`) pass unchanged: all their
required gene_expression fragments are retained in the rewritten base or
supplied by the core, and all forbidden fragments stay absent.

## Group-rule note (out of scope for this base rewrite)

The WB/ZFIN group rules
(`packages/alliance/agents/gene_expression/group_rules/{wb,zfin}.yaml`) still
carry the inherited `metadata.exclusions[]` / `metadata.ambiguities[]` framing
and the legacy "Shared domain-envelope output contract". They are a separate
Phase C scope item and were NOT edited here. The base rewrite keeps rendering
cleanly under both groups (verified by the policy-test group overlays). When the
group rules are rewritten, the same template-rule-1 correction should be applied.
