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
- **D-D Per-field validators, NOT a single composite.** PROPOSED: validate each condition field with its
  specialist validator (the table above) rather than one opaque `experimental_condition_validation` composite.
  Rationale: reuses the proven per-ontology validators (DOID/ECO/CV/gene all already work this way), each field
  gets the right specialist + ontology scoping, it composes naturally with the multivalued engine + batching,
  and it avoids inventing the undefined composite "validation contract." The kept `experimental_condition`
  composite agent is then retired/unused (note it; don't delete envelope-legacy per Phase-6 scope). ALTERNATIVE
  if you prefer: one composite call per condition (fewer calls, but a complex new agent contract). **Need your
  call.**
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
2. **Validation (the binding layer).** The per-field validator bindings (table above) re-validate every staged
   condition field against its ontology/CV, deterministically, exactly like DOID/ECO/relation/qualifiers are
   validated today — using the multivalued engine so EVERY condition (and every field) is checked, batched per
   binding. This is the "validate later just like the other tools" layer.

Net: same shared lookup tool on BOTH sides (extractor grounds with it; validator re-checks with it) — which is
exactly the symmetry Chris described.

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

RESOLUTION: survey the nesting depth; if >1 condition_relation per annotation is real, extend the engine to
nested fan-out (option 1) as part of this work. Either way, every CONDITION gets validated; the question is
whether the outer `condition_relations` list also needs fan-out.

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
- 2026-06-01: design written (go-big / all-host-types / validate-everything per Chris). NEXT: survey nesting
  depth + validator I/O, resolve D-D/D-E + nested-multivalued, then build disease-first gated, then GE +
  phenotype.
