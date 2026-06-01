# Experimental conditions (condition_relations) — full cross-type validation

Date: 2026-06-01. Status: **DESIGN — Chris said "go big": ALL host-annotation types, validate EVERYTHING
validate-able always, extract ALL conditions.** Supersedes the earlier "D6 DEFER" decision.
Origin: D6 in the disease-approach doc (deferred 2026-05-31); reopened after the multivalued-validation
engine landed (which conditions need).

## Goal
Make experimental conditions a first-class, fully-validated, extracted part of EVERY host annotation type
(disease, phenotype, gene-expression). Validate every validate-able condition field against its ontology/CV,
for every condition (not just `[0]`), on every host. Reuse the proven validators + the multivalued engine.

## The model (one shared structure, three host attachments)

```
Annotation.condition_relations[]                 (multivalued)
   └─ ConditionRelation
        ├─ condition_relation_type               CV 'Condition Relation Type'
        └─ conditions[]                          (multivalued)
             └─ ExperimentalCondition  — validate-able ontology refs + free text
```
`ConditionRelation` / `ExperimentalCondition` are SHARED: the curation DB attaches them to all three hosts
via IDENTICAL join tables `{annotation_id, conditionrelations_id}`:
`diseaseannotation_conditionrelation`, `phenotypeannotation_conditionrelation`,
`geneexpressionannotation_conditionrelation`. So this is genuinely one structure validated the same way
everywhere — build it generically once, attach to three packs.

## Curation-DB reality (2026-06-01 survey)

**Relation type** — CV `'Condition Relation Type'`, 7 members: `has_condition` (19,193 — dominant),
`ameliorated_by` (189), `induced_by` (113), `exacerbated_by` (43), `not_ameliorated_by`/`not_exacerbated_by`/
`not_induced_by` (few). (Negated variants exist; map to the same CV + a negation flag if the model uses one.)

**ExperimentalCondition validate-able fields + ontology + fill counts:**

| Field | Ontology | Filled | Proposed validator |
|---|---|---|---|
| `condition_class` | ZECO | 15,338 | `ontology_term_validation` (ontology=ZECO) |
| `condition_id` | ZECO (+XCO) | 3,956 | `ontology_term_validation` (ZECO/XCO) |
| `condition_chemical` | ChEBI (+WBMol/`WB:`) | 7,975 | `chemical_validation` (the KEPT agent; ChEBI) |
| `condition_taxon` | NCBITaxon | 237 | `ontology_term_validation` (NCBITaxon) |
| `condition_anatomy` | ZFA/XAO (organism-specific) | 353 | `ontology_term_validation` (anatomy; organism-scoped) |
| `condition_geneontology` | GO | 14 | `gene_ontology` validation |
| `condition_quantity` / `condition_free_text` / `condition_summary` | — (free text) | — | not validated |

All ontology fields are OPTIONAL and sparse — a condition has a `condition_class` (ZECO) almost always, a
chemical often, the rest rarely. So the contract is "validate each field that is present, against its
ontology" (optional per-field selectors; absent → no finding).

## Current state per pack
- **disease**: FULL field scaffolding already declared (domain_pack.yaml ~736-911) but inert —
  `disease_condition_relation_lookup` (CV relation-type) is ACTIVE, the composite
  `experimental_condition_validation` is `under_development` ("until the validation contract is complete"),
  and the builder does NOT stage conditions. Fields use the legacy `[0]` convention.
- **gene_expression**: partial condition scaffolding (~8 refs).
- **phenotype**: NONE.

Kept assets for exactly this: the `experimental_condition` agent (composite) + the `chemical` agent
(`chemical_validation`, ChEBI). `ontology_term_validation` is generic over ontologies (like the CV tool is
over vocabularies), so it covers ZECO / anatomy / taxon by an ontology parameter.

## DECISIONS (proposed — Chris to red-line)

- **D-A Scope = ALL host types.** Build the condition structure + validation + extraction for disease,
  phenotype, AND gene-expression. (Chris: "all host-annotation.")
- **D-B Validate EVERYTHING validate-able, always.** Every ExperimentalCondition ontology field present gets
  validated against its ontology (table above). (Chris: "validate everything that is validate-able, always.")
- **D-C Extract ALL conditions.** The extractor stages the full nested structure for every condition stated
  in the paper. (Chris: "extraction lets do all conditions.")
- **D-D COMPOSITE at the CONDITION grain — REVISED 2026-06-01 (Chris).** First chose per-field; Chris then
  raised that the MEANING of a condition is the COMBINATION of fields, not each field alone — confirmed by the
  data: `condition_class` names the experimental-variable TYPE ("chemical treatment", "temperature exposure",
  "radiation", …) and the companion fields fill it (chemical+quantity / dose / etc.). Per-field validation
  passes incoherent combinations (e.g. class="temperature exposure" + a ChEBI chemical — each field valid, the
  condition is nonsense) and misses missing-companion cases. So the UNIT of validation is the `ExperimentalCondition`,
  not the field. Use the KEPT `experimental_condition` agent (one binding per condition object), which its prompt
  already defines as: "validate one composite ExperimentalCondition target by COMPOSING lower-level validator and
  package-tool evidence into a SINGLE CONDITION-LEVEL DECISION" — i.e. it does the per-field ontology/CV/chemical
  existence lookups (composing the lower-level validators) AND the cross-field coherence in one verdict. So we
  get per-field existence + cross-field meaning together, and the contract is substantially already written (the
  agent exists; it's only `under_development` because the binding isn't activated). Still fans out per-condition
  via the multivalued engine + batches. (The per-field-only option is rejected: it can't see the combination.)
- **D-E Generic, replicated per host pack.** The condition field block + the per-field bindings are identical
  across the three host packs (same model_ref `ExperimentalConditionPayload`, same validator agents). Define
  once, replicate consistently (the pack architecture is self-contained per pack; confirm there is no
  shared-include mechanism — if there is, use it). The VALIDATION is generic; only the per-pack declaration is
  replicated.

## TWO LAYERS — extraction-time grounding tool + validate-later bindings (Chris's question)

Conditions, like every field, pass through two distinct layers:
1. **Extraction-time grounding (a TOOL the extractor calls).** TODAY most extractors have NO CV/ontology
   lookup tool — they stage mentions/CURIEs from the paper + the model's own knowledge, and the validators
   resolve later. The `agr_curation_query` shared CV/ontology lookup tool is currently wired to the VALIDATOR
   agents (disease, ontology_term, controlled_vocabulary, experimental_condition, gene, …), not the extractors.
   The lone precedent for an extractor-side lookup is the gene_expression extractor's `inspect_ontology_term`.
   **DECISION (proposed, matches Chris's framing): GIVE the condition extractor a grounding lookup tool** — the
   same shared CV/ontology lookup (`agr_curation_query`, vocabulary/ontology-parameterized) exposed to the
   extractor (mirroring gene_expression's `inspect_ontology_term`), so it grounds condition terms
   (relation-type CV, ZECO class/id, ChEBI chemical, GO, anatomy, taxon) to REAL CURIEs while reading. This
   matters MORE for conditions than for DOID/ECO because ZECO/ChEBI terms are obscure — the LLM should look
   them up, not guess from memory. So: extractor uses the lookup tool to stage grounded conditions →
2. **Validation (the binding layer) — composite per condition, EVIDENCE-GROUNDED.** One `experimental_condition`
   validation per `ExperimentalCondition` (D-D), fanned out per-condition by the multivalued engine + batched. It
   composes the per-field ontology/CV/chemical lookups + a condition-level coherence verdict. CRITICALLY it
   validates against the SOURCE TEXT, not just the ontologies: the validation request already carries the matched
   object's evidence records (verified quotes + `source_chunk_id`/`source_section`), and the validators are
   prompted to "resolve only from tool evidence" using `evidence_quote`/`evidence[]` as source context (verified:
   ontology_term + controlled_vocabulary + experimental_condition prompts all do this TODAY, for every validated
   field — so this discipline is already universal, not new). The `experimental_condition` agent specifically takes
   `condition_statement` (source text / synthesized summary) + `evidence_quote`.

   **Builder requirement (so the chunk reaches the right condition):** the extractor must STAGE each
   `ExperimentalCondition` WITH its anchoring source text — the verified quote / `condition_free_text` /
   `condition_statement` it came from — so the per-condition composite validator sees "the paper said: *treated
   with 3 pM sirolimus*" beside the `(chemical treatment, sirolimus, 3 pM)` it is checking. This is the same
   per-element evidence discipline we want everywhere; conditions make it explicit.

Net: same shared lookup tool on BOTH sides (extractor grounds with it; validator re-checks with it), and the
validator always sees the most-important source chunk for what it's validating — exactly the symmetry +
evidence-grounding Chris described.

## EVIDENCE CONTRACT (REQUIRED — and consistent across EVERYTHING validated)

The model must NEVER hand us free-text quotes for validation (hallucination risk). The active builder path
already enforces this and conditions MUST follow it identically:
- The extractor provides **hints** (`mention` / `normalized_hint` / `source_mentions`) it owns, plus
  **`span_ids`** (pointers copied from `read_chunk(...).chunk.evidence_spans[].span_id`) via `record_evidence`.
  MULTIPLE span_ids are allowed in one call.
- The BACKEND resolves `span_ids` and **copies the EXACT source text** into `verified_quote` (backend-owned,
  span-provenance preserved). The model never types the quote. `verified_quote` is a backend artifact despite
  the misleading name.
- Builder staging references evidence by **`evidence_record_ids`** (pointers), never a quote string. So a staged
  `ExperimentalCondition` carries `evidence_record_ids` → the per-condition composite validator receives the
  backend-resolved exact source text for the spans the condition came from.
- REQUIREMENT for conditions: stage each `ExperimentalCondition` WITH its `evidence_record_ids` (the spans the
  condition was read from) so the composite validator validates the `(class, chemical, quantity, …)` combo
  against the real sentence(s). No condition-specific quote text from the LLM.
- This is the SAME contract everywhere validated; the consistency audit (task b) confirms no active extractor/
  validator path accepts a free-text quote. (The only `verified_quote`-as-LLM-output uses are the DEAD legacy
  envelope schemas — gene/phenotype/allele `schema.py`, output_schema:null builders now — flagged for cleanup,
  not a live risk.)

## KEY TECHNICAL RISK — two-level nested multivalued (the one real engineering question)

Conditions are multivalued at TWO levels: `condition_relations[i].conditions[j].condition_class`. The
multivalued engine we just landed fans out ONE declared multivalued field over its elements (`field[i]`). It
does NOT yet fan out a multivalued field NESTED inside another multivalued element. Full validation of every
condition on an annotation with multiple condition_relations each holding multiple conditions needs TWO-LEVEL
fan-out. Options:
1. **Extend the engine to nested multivalued** — fan `condition_relations[i]`, and within each, fan
   `conditions[j]`, producing targets `condition_relations[i].conditions[j].<field>`. This is the correct,
   generic fix (the index-capable write-back already supports `a[i].b[j].c` paths — `parse_field_path` +
   `_set_payload_value` handle multi-index paths; the gap is the match FAN-OUT recursing into nested
   multivalued fields). Likely the right "go big" answer.
2. **Single-level in practice** — if `condition_relations` is almost always length-1 (one relation per
   annotation, holding the conditions), only `conditions[]` is meaningfully multivalued, and one level of
   fan-out on `conditions[]` suffices. NEED A DB CHECK: how often does one annotation have >1 condition_relation
   vs >1 condition within a relation? (Survey before deciding.)

RESOLVED 2026-06-01 (survey): BOTH levels have real multiplicity, so OPTION 1 (extend the engine to nested
two-level fan-out) is REQUIRED for the go-big "validate everything always" scope:
- `conditions[]` per condition_relation: 19,549 relations; 3,379 (17%) hold >1 condition (up to 9). Inner list
  is COMMONLY multi -> must fan out.
- `condition_relations[]` per annotation: ~97-99% have exactly 1, BUT a real tail has 2+ — phenotype 13,975
  annotations, disease 51. So a "condition_relations is always length-1" shortcut would MISS validating the
  conditions in the 2nd+ relation for ~14k phenotype annotations. Outer list must fan out too.
So the engine needs nested fan-out: fan `condition_relations[i]`, and within each fan `conditions[j]`, producing
per-condition targets `condition_relations[i].conditions[j]`. The index-capable write-back already handles
`a[i].b[j].c` paths; the gap is the match FAN-OUT recursing into a nested multivalued field. This becomes the
first build step (engine extension), gated + reviewed like the one-level engine was.

## Build plan (phased, gated like multivalued/R3/R4)
1. **Survey** the nesting depth (DB) + confirm the per-field ontology/validator mapping end-to-end (does
   `ontology_term_validation` accept a ZECO/anatomy/taxon ontology param? confirm `chemical_validation`'s
   I/O). Resolve D-D/D-E + the nested-multivalued decision.
2. **Engine** (only if nested fan-out is needed): extend match fan-out to recurse into nested multivalued
   fields; unit + the existing multivalued tests stay green. Gated, independent review.
3. **Disease first** (it has the scaffolding): re-declare conditions natively multivalued, wire the per-field
   bindings (relation_type CV already active; add class/id/chemical/GO/anatomy/taxon), activate, builder
   staging of the nested structure, materialization, extractor prompt. Sandbox e2e proving a paper's
   conditions validate per-field per-element + 0 structural. Independent Opus review.
4. **Gene-expression + phenotype**: replicate the (now-proven) condition block + bindings + builder staging +
   prompt. Each gated (broad suite + e2e + review).
5. Leverage **batching** so each condition's per-field lookups (and multiple conditions) batch per binding.

## Open questions for Chris
- D-D: per-field validators (proposed) vs the composite `experimental_condition` agent?
- Negation: do `not_ameliorated_by` etc. map to the CV directly, or a base relation + a negation flag? (Check
  the LinkML ConditionRelation shape.)
- condition_anatomy is organism-specific (ZFA/XAO/…) — scope the ontology by the host's organism (ties to the
  future MOD/group layer noted in the subset doc); for now validate against the appropriate anatomy ontology by
  taxon context.
- Extraction realism: conditions are subtle to pull from text — strong "only when explicitly stated" prompting
  (same anti-hallucination rule as R4), especially for the sparse anatomy/GO/taxon fields.

## Status log
- 2026-06-01: design written (go-big / all-host-types / validate-everything per Chris).
- 2026-06-01: NESTED FAN-OUT ENGINE landed (f2c609a0) — generic N-level fan-out (condition_relations[i].conditions[j]).
- 2026-06-01: DISEASE CONDITIONS LANDED (0fa7afda). Composite per-condition validation (the kept
  experimental_condition agent, one fan-out per ExperimentalCondition, batched) + full nested extraction +
  staging (grounded CURIEs, evidence via evidence_record_ids) + materialization. Engine got a 2nd refinement
  (ValidatorBindingMatch.resolve_input_path — sibling input_field index resolution across relation/condition
  depths). LIVE-PROVEN on a concrete GeneDiseaseAnnotation (the gate that caught the bugs). Defects found via
  the live proof + my review, all FIXED + re-proven: (1) both condition bindings now scope to all 4 object_types
  + condition fields declared on the 3 concrete subtypes (materializer emits concrete subtypes; abstract-only
  scoping fired on 0 real objects — a silent-no-validation bug the tests + static review missed); (2) relation
  is context_only in the composite (was guessing a non-existent 'Condition Relation' vocab; the dedicated
  disease_condition_relation_lookup owns it, vocab 'Condition Relation Type'); (3) chemical validated via
  agr_curation_query ontology lookup (ChEBI is in the curation DB; was a 404 ChEBI REST URL); (4)
  expected_result_fields = condition_class_curie (always present) NOT condition_id (optional, absent in ~74%).
  587 broad suite, 0 regressions; my review + independent Opus review clean; live proof: 2 conditions resolve
  incl. ChEBI:9168->sirolimus, no condition_id required.
- 2026-06-01: GE + PHENOTYPE CONDITIONS LANDED (e6c39bc2). Replicated the disease pattern (reusing the composite
  agent + nested engine unchanged); each scoped to its single curatable type (GeneExpressionAnnotation /
  PhenotypeAnnotation — no subtype split). 595 broad suite, 0 regressions; my review + independent Opus review
  clean; LIVE-PROVEN on BOTH real object types (each: composite fires, 2 conditions resolve incl CHEBI:9168, relation
  resolved). Tidy-later: 2 GE materialization tests in an SDK-coupled unit file (pass in CI; covered SDK-free too).

## OUTCOME: COMPLETE (2026-06-01)
Cross-type experimental conditions is DONE. All three host annotation types (disease, gene-expression,
phenotype) extract experimental conditions and validate EVERY validate-able condition field PER CONDITION
(nested fan-out), composing per-field ontology/CV lookups into a condition-level decision, batched, evidence-
grounded (span_ids/evidence_record_ids; no LLM quotes). Built on the nested multivalued engine (f2c609a0) +
the composite experimental_condition agent. Commits: engine f2c609a0; disease 0fa7afda; GE+phenotype e6c39bc2.
Remaining/optional: negation handling (not_ameliorated_by etc.); anatomy/GO/taxon are validate-able via the
same agr_curation_query ontology path if a condition stages them; the 2 GE tests' placement tidy.
