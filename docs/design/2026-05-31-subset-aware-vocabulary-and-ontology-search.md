# Subset-aware controlled-vocabulary (and ontology) searching

Date: 2026-05-31. Origin: R2 (disease per-subtype relation enforcement) generalized — Chris asked to make
the shared lookup tool subset-aware for ALL data types, designing now for a future group/MOD-login layer.

## Motivation

Extraction + validation pick controlled-vocabulary (and ontology) terms via shared Alliance lookup tools.
Today they search/validate against the FULL vocabulary (or full ontology). The LinkML model + the curation
DB already define narrower, context-appropriate **subsets**, and restricting the search to the right subset:
- prevents valid-but-wrong-context picks (e.g. `is_model_of` on a `GeneDiseaseAnnotation` — an AGM-only
  relation), the disease R2 case; and
- improves search quality everywhere (fewer, more relevant candidates -> better LLM selection + fewer
  validator_unresolved).

## The data is there (curation DB, 2026-05-31 survey)

15 of 45 vocabularies carry subsets, modeled as `vocabularytermset` rows with membership in
`vocabularytermset_vocabularyterm`. Relevant to our types:

| Vocabulary | Subsets (data-type axis) | Our type / field |
|---|---|---|
| Disease Relation | Gene / Allele / AGM / Via-Orthology Disease Relation | disease relation (per concrete subtype) |
| Condition Relation Type | Expression Condition Relation | gene-expression / disease condition relations |
| Spatial Expression Qualifier | Anatomical Structure / SubStructure / Cellular Component Qualifier | gene-expression qualifiers |
| Allele Relation | Allele-Gene / Allele-Construct / Variant / … Association Relation | allele associations |
| Expression Relation | (gene-expression relation) | gene-expression |

Ontology **slims** are a PARALLEL axis (different tool): `Anatomical Structure Slim`, `Cellular Components GO
Slim`, `Stage Uberon Slim` ("Public Site") restrict Uberon/GO/stage ontology searches to a curated slim.

## Design

### Mechanism (the shared tool gains an optional, config-driven subset)
- The controlled-vocabulary lookup tool (`packages/alliance/python/.../tools/agr_curation.py`,
  `_vocabulary_term_query` / CV helpers) and the `controlled_vocabulary_validation` agent gain an OPTIONAL
  `subset` (vocabularytermset name/id) parameter. When present, the lookup restricts to that subset's members
  (`vocabularytermset_vocabularyterm`); when absent, behavior is UNCHANGED (full vocabulary). Both tool + agent
  live in the Alliance package (NOT `backend/src`), so this is Alliance-package + domain-pack-config work.
- The subset to apply is supplied by **per-field domain-pack binding config** — generic, not hardcoded in the
  tool. Disease's currently-dead `relation_subsets` becomes the live source.
- Because both the EXTRACTOR's term-resolution and the VALIDATOR call the same tool, enforcement happens on
  both sides at the source: the extractor only ever surfaces valid-subset terms; the validator backstops.

### The subset can depend on a sibling field
For disease, the correct relation subset depends on the staged `subject_type` (Gene/Allele/AGM). So the
binding selects `relation_subsets[subject_type]`. The binding metadata must express a value-dependent subset;
the subject is staged first (D1/D2 already establish this).

### FUTURE — group/MOD-login subset layer (design for it now; do NOT build yet)
Subsets should COMPOSE along two axes:
1. **data-type axis** (this pass): the LinkML/subtype subset for the field (e.g. AGM Disease Relation).
2. **group/MOD axis** (future): the logged-in curator's group restricts further (e.g. a ZFIN curator searching
   disease terms -> only ZFIN-relevant DOID terms / ZFIN-used vocabulary terms).
The effective search set is the INTERSECTION (data-type subset ∩ group subset). The tool's `subset` parameter
should therefore be designed to accept/compose MULTIPLE subset constraints (or a resolved member set), and a
group/MOD subset will be sourced from the curator's login group at request time. ADD A TOOL COMMENT marking
this composition point so the future group layer slots in without reshaping the API.

### FUTURE — ontology-term slims (parallel, different tool)
The ontology lookup/validator (`ontology_term_validation`) gets the same treatment using the `*Slim Terms`
vocabularies (e.g. restrict Uberon anatomy search to the Anatomical Structure Slim). Same data-type + group
composition. Bigger scope; documented here, not in this first pass.

## Scope for NOW
- Data-type-axis subset on the shared **controlled-vocabulary** tool + validator + per-field binding config,
  applied across the types/fields above that have CV subsets. Group/MOD axis = designed-for + commented, NOT
  built. Ontology slims = documented future.
- Per-field mapping to wire (CV subsets): disease relation (per concrete subtype, makes `relation_subsets`
  live), condition relation type (Expression Condition Relation), gene-expression Expression Relation + spatial
  qualifiers, allele association relations (where the allele builder stages them).
- Gate: a wrong-subtype/CV term is flagged where before it passed; existing full-vocabulary lookups unchanged;
  unit/contract + broad suite green; Opus review. Implement as a gated workflow.

## Open scope question for Chris
- First implementation breadth: (a) disease relation only (closes R2), (b) all CV-subset fields across types,
  or (c) (b) + the ontology slims now. (b) is the natural "all data types, data-type axis, CV" target.

---

## Outcome (landed 2026-05-31/06-01)

### Part A — controlled-vocabulary subsets: DONE + WIRED (commit 07207f1f)
Generic optional `subset` (vocabularytermset) param on the shared Alliance CV lookup tool + a new generic
`payload_keyed_literal` input-selector to supply it from per-field binding config (no domain logic in
backend/src; full-vocabulary behavior unchanged when absent). **Disease per-subtype relation subsets are now
ENFORCED** (closes R2): the `disease_relation_cv_lookup` binding selects `relation_subsets[subject_type]`
(Gene/Allele/AGM, gene = Gene + Via-Orthology union), making the previously-dead `relation_subsets` live.
Proven via direct tool API (AGM subset -> only the 3 model-of relations; `is_model_of` rejected under a gene
subset) and a disease e2e trace (live CV call carried `subset:"AGM Disease Relation"`, 0 structural). Other CV
fields: condition_relation_type wired but inert (conditions deferred, D6); Expression Relation has no subset;
allele is mention-only (no association-relation field); spatial qualifiers are the ontology axis (Part B).

### Part B — ontology slims: MECHANISM BUILT + PROVEN, NO FIELD WIRED (commit 21c56017)
Generic optional `slim` param on the shared Alliance ontology lookup tool (search_ontology/go/anatomy/life_stage),
restricting the search AT THE QUERY LEVEL to a slim vocabularytermset's member CURIEs; fail-OPEN on DB error
(a transient failure never wrecks extraction); config-driven (binding `slim` input + field `term_source.slim`);
zero backend/src changes. Proven via direct tool API (GO CC "nucleus" 8->1, "cytoplasm" 20->5, all slim members).
**No gene_expression field is wired in the final state**, by evidence:
- The only curated GO-CC vocabularytermset slim is the coarse ~17-term **"Public Site" DISPLAY slim**; wiring it
  REGRESSED CC extraction to 0 on the proven worm doc (which curates fine-grained ciliary CC terms cilium
  GO:0005929 / axoneme GO:0005930 / periciliary membrane GO:1990075, none in the display slim). Removed per the
  no-regression rule. Final e2e: 0 structural, worm CC/anatomy/stage still resolve, slim inert (baseline).
- Uberon anatomy/stage slims are ORGANISM-DEPENDENT (the proven doc is C. elegans = WBbt/WBls, not Uberon) ->
  documented as the natural first consumer of the future MOD/group layer.
- Disease (DOID) / phenotype (HP/MP) have NO curated vocabularytermset slim (only raw OBO ontologyterm_subsets
  tags). N/A.

### Open questions for Chris (post-subset-work)
- S1 — **Curation vs display slims.** The vocabularytermset "Public Site" slims are DISPLAY slims (coarse), not
  extraction-appropriate. To actually USE ontology-slim search we need field-appropriate EXTRACTION slims
  (finer, curation-relevant term sets) — a data-curation task, not code. The generic mechanism is in place and
  proven; it just needs the right slim data.
- S2 — **Group/MOD layer is the natural next step for anatomy/stage.** A ZFIN/WB curator's login group should
  supply the organism-appropriate ontology + slim at request time, composed (intersection) with the data-type
  slim. The tools carry a comment marking this composition point. Designed-for; not built.
- S3 — **Exact-CURIE consistency.** Part A applies the CV subset to BOTH search and the exact get_vocabulary_term
  (so a wrong-subtype CURIE is rejected at validation). Part B restricts only the ontology SEARCH helpers, not
  exact get_ontology_term (a curator-picked out-of-slim CURIE is not rejected). Decide whether the ontology slim
  should also gate exact-CURIE validation for parity.
