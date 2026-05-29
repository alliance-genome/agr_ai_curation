# Gene Expression LinkML Extraction Failure Notes

Date: 2026-05-29

## Context

Local testing exposed a gene-expression extraction that appeared successful in the
chat stream but produced a curation candidate that was far too sparse for the
Gene Expression LinkML/domain-pack contract.

Observed trace:

- Trace ID: `e88c6ef4899bc8534878526692e49fb8`
- Chat session: `57caaffe-8eb4-4552-b69d-f4d3c315ecd9`
- Document:
  `PMID39671436_pgen.1011496_integrin-adhesome-axis-rpm-1-growth-cone-axon-development.pdf`
- Persisted envelope:
  `extraction-result:chat-runtime:17e60e91-d0ef-4e05-a726-e2d33697abc3`
- Affected curation candidate:
  `447d964f-ca09-4fc7-846d-c7d8bfe101db`

The downstream curation load also hit an envelope revision mismatch:

```text
Curation candidate 447d964f-ca09-4fc7-846d-c7d8bfe101db envelope revision 2 does not match domain envelope revision 3
```

That revision problem is separate from the extraction quality issue, but it made
the bad persisted candidate visible during review.

## What The Model Produced

The model found a real expression statement:

- PAT-3, UNC-112, and TLN-1 expression in C. elegans mechanosensory neurons.

It also recorded two useful evidence records from the paper, including a Results
chunk describing PAT-3::GFP, UNC-112::GFP, and GFP::TLN-1 CRISPR localization in
ALM/PLM mechanosensory neurons.

However, the final object payload was mostly empty:

```json
{
  "data_provider": {"abbreviation": "WB"},
  "expression_annotation_subject": {
    "primary_external_id": null,
    "gene_symbol": null
  },
  "relation": {"name": "is_expressed_in"},
  "single_reference": {"reference_id": null},
  "expression_experiment": {
    "unique_id": null,
    "single_reference": {"reference_id": null},
    "entity_assayed": {
      "primary_external_id": null,
      "gene_symbol": null
    },
    "expression_assay_used": {"curie": null}
  },
  "when_expressed_stage_name": null,
  "where_expressed_statement": "PAT-3, UNC-112, TLN-1 expression in mechanosensory neurons",
  "expression_pattern": {
    "where_expressed": {
      "anatomical_structure": null,
      "cellular_component": null
    }
  }
}
```

The only materially populated LinkML/domain-pack values were:

- `relation.name = is_expressed_in`
- `data_provider.abbreviation = WB`
- `where_expressed_statement = PAT-3, UNC-112, TLN-1 expression in mechanosensory neurons`

## Missing LinkML Fields

The extracted candidate did not populate the fields curators need for a useful
Gene Expression LinkML review:

- Subject gene fields were null.
- Reference fields were null.
- Assay/method was null.
- Stage was null.
- Anatomy/cellular-component site was null.
- Helper-selection provenance was absent.

The persisted metadata contained an ambiguity for `mechanosensory neurons`:

```text
No matching anatomical or cellular component term found for 'mechanosensory neurons'.
```

That suggests the model at least attempted or reasoned about the expression site.
The runtime log showed only two `get_domain_field_term_options` calls, which is
not enough for the prompt-required relation, assay/method, stage, and site
selector checks. There is no evidence that assay or stage were seriously staged
or preserved as unresolved field-level metadata.

## Validator Behavior

The package-specific `GeneExpressionEnvelope` validation correctly rejected the
model output. The logged validation error reported:

- Missing required payload fields, including:
  - `date_created`
  - `internal`
  - `single_reference.reference_id`
  - `expression_annotation_subject.gene_symbol`
  - `expression_annotation_subject.primary_external_id`
  - `expression_experiment.unique_id`
  - `expression_experiment.single_reference.reference_id`
  - `expression_experiment.entity_assayed.gene_symbol`
  - `expression_experiment.entity_assayed.primary_external_id`
  - `when_expressed_stage_name`
- `relation.name` lacked required
  `metadata.provenance.helper_selections[]` evidence from
  `get_domain_field_term_options`.
- `expression_pattern.where_expressed` lacked both `anatomical_structure` and
  `cellular_component`.

This means the domain-pack contract did its job at the validation boundary.

## Runtime Failure Mode Observed

After the package-specific validator rejected the output, the streaming runtime
recovered the generated text through a generic domain-envelope salvage path.

The salvage path:

- Parsed the generated text as JSON after the stream validation error.
- Repaired missing object refs by synthesizing a `pending_ref_id` such as
  `salvaged_geneexpressionannotation_1`.
- Validated the repaired payload only against the generic
  `DomainEnvelopeExtractionResult`.
- Allowed the sparse object to be persisted and converted into curation
  candidates.

That turned a correct fail-fast validator rejection into a bad persisted
candidate.

As of the immediate follow-up patch, this salvage path should be removed. The
intended runtime behavior is now fail-fast: a model either produces valid
structured output for the package-specific contract, or no domain envelope is
persisted.

## Why This Matters

The curation screen can render Gene Expression domain-envelope fields, but it
can only review what the extraction persisted. In this run, the UI would be
mostly empty because the model did not provide enough LinkML-shaped content and
the runtime salvage path let the sparse object through.

The immediate quality problem is not that the UI lacks every Gene Expression
field. The immediate problem is that invalid or weak domain-envelope output can
escape the package-specific extraction contract and become review data.

## Term Resolution Is The Hard Part

Detailed local reference inventory and reusable resolver-design notes live in
[`2026-05-29-ontology-resolution-reference-research.md`](./2026-05-29-ontology-resolution-reference-research.md).
This document keeps the gene-expression failure narrative focused on the
specific extraction incident.

The next problem is not just prompt wording. Mapping paper language to controlled
vocabulary terms is intrinsically difficult, especially for anatomy, stage, and
assay fields.

We should explicitly move away from treating lexical matching as the expected
path from paper phrase to CV term. Lexical matches, synonyms, and IDs are useful
signals, but real paper text often names:

- specific cells or substructures rather than the exact ontology label;
- experimental methods rather than the exact curation assay term;
- figure/contextual evidence without a clean stage string;
- organism-specific names that need species/provider-specific anatomy or stage
  vocabularies;
- broader biological phrases that imply several possible slots.

The external literature/tools support this caution:

- BioPortal-style annotation uses names, synonyms, and IDs as a direct matching
  baseline. That is useful for candidate retrieval, but it reinforces that
  lexical matching is only one layer.
- scispaCy-style entity linking uses approximate nearest-neighbor retrieval over
  concepts and aliases. This is closer to a scalable candidate generator than an
  authority.
- Graph-based biomedical entity linking work such as PPR-SSM uses ontology
  relations and semantic similarity to prefer globally coherent candidates.
- Uberon is the most important warning for anatomy: lexical matches are treated
  as suggestions, then curated/reasoned over. The paper explicitly describes
  combining lexical matching, expert curation, and computational reasoning rather
  than relying on text matching alone.

For this project, that means a Weaviate or vector index may help retrieve
candidates, but it must not be the source of truth. The authoritative boundary
should remain the Alliance curation DB, ontology APIs, LinkML/domain-pack field
metadata, and package validators.

## Live Curation DB Reconnaissance

Read-only probes against the local Incus backend's configured curation DB showed
that the existing Alliance curation DB is already a strong ontology candidate
source. The useful data is there; the current problem is mostly that our helper
path is too literal and throws away too much explanatory context.

The live schema has:

- `ontologyterm` with CURIE, name, definition, namespace, ontology term type,
  obsolete state, child count, and descendant count;
- `synonym` plus `ontologyterm_synonym`;
- `ontologytermclosure` with ancestor/descendant closure, distance, and closure
  relationship types;
- `ontologyterm_secondaryidentifiers`, `ontologyterm_definitionurls`, and
  `ontologyterm_subsets`;
- `vocabulary`, `vocabularyterm`, and `vocabularyterm_synonyms`;
- `pg_trgm` installed, with GIN trigram indexes on `UPPER(ontologyterm.name)`
  and `UPPER(synonym.name)`.

Observed ontology coverage in the active DB is broad enough to make the curation
DB the first place to improve candidate generation:

- `WBBTTerm`: thousands of active C. elegans anatomy terms, with definitions and
  synonym links.
- `WBLSTerm`: hundreds of active WormBase life-stage terms, almost all with
  definitions and many synonyms.
- `MMOTerm`: hundreds of active measurement-method/assay terms, with definitions
  and synonyms.
- `GOTerm`, `UBERONTerm`, `CLTerm`, `ZFATerm`, `FBDVTerm`, and many other
  ontology families are present.

Concrete probe results:

- `mechanosensory neuron` resolves to `WBbt:0008431`.
- `mechanosensory neurons` currently returns zero through the package helper,
  but a direct trigram query over the same DB ranks `WBbt:0008431`
  (`mechanosensory neuron`) first with a very high similarity score. This is a
  normalization/fuzzy-search failure, not a missing-data failure.
- `touch receptor neuron` resolves to `WBbt:0005237`, and the closure table shows
  `touch receptor neuron -> mechanosensory neuron -> sensory neuron -> neuron`.
  This is exactly the sort of ontology graph context the resolver should expose.
- `ALM neuron` and `PLM neuron` do not work well as literal helper searches, but
  the DB has `ALM`, `ALML`, `ALMR`, `PLM`, `PLML`, and `PLMR` terms with
  definitions indicating touch-receptor / sensory-neuron context. A useful
  resolver needs acronym/token expansion and definition search, not only
  full-phrase label search.
- `L4` returns an ambiguous WB life-stage list. `L4 larval stage` improves the
  search but still needs ranking and provider/species-sensitive scoring.
- `CRISPR engineered GFP proteins` does not resolve as an MMO label/synonym, but
  searching MMO definitions for `GFP`, `green fluorescent`, `reporter`, and
  `transgenic` surfaces likely candidates such as `in situ reporter assay`,
  `knock-in in situ reporter assay`, and `transgenic in situ reporter assay`.
  This suggests the paper phrase describes construction/detection evidence, not
  a direct assay label.

The current `agr-curation-api-client==0.10.1` search implementation already
returns `definition` and `synonyms` from the direct DB methods, but our package
wrapper projects anatomy/life-stage/GO helper results down to `curie`, `name`,
and type/namespace. That loses the evidence needed to explain or rank candidate
choices.

## Curation Repo / Index Status

I refreshed the local `temp_agr_curation` checkout against `origin/alpha`
(`e88ba6c79`, merge of
`ai-curation-reference-fuzzy-indexes`). Current `agr_curation` already contains
the relevant Postgres trigram migrations:

- `v0.43.0.4__enable_pg_trgm_extension.sql` enables `pg_trgm`;
- `v0.43.0.6__add_ontology_fuzzy_search_indexes.sql` adds
  `ontologyterm_upper_name_index` and `ontologyterm_name_trgm_idx`;
- `v0.43.0.7__add_synonym_fuzzy_search_indexes.sql` adds
  `synonym_upper_name_index` and `synonym_name_trgm_idx`;
- later reference fuzzy indexes also exist under `v0.47.0.5`.

So a new `agr_curation` PR just to add ontology/synonym trigram indexes is
probably not needed unless a target environment is missing those migrations.
The more likely follow-up is in `agr_curation_api_client` and this repo:

- update the client/docs/tests to recognize that trigram indexes exist;
- add a scored fuzzy-search path using `similarity` / `word_similarity` or a
  dedicated trigram candidate query;
- return match metadata such as `matched_field`, `matched_text`, `match_type`,
  and score;
- optionally add ancestor/parent context to ontology search result projections;
- update our `agr_curation_query` / `get_domain_field_term_options` wrappers to
  preserve definitions, synonyms, matched text, scores, and graph context for
  anatomy, stage, GO, and assay candidates.

This still preserves the authority boundary: candidate generation can be richer
and fuzzy, but final accepted values must round-trip through the curation DB /
domain-pack validator.

## Resolver Shape To Consider

The likely resolver architecture should be field-specific, organism-aware, and
validator-backed:

1. Extract evidence-backed source phrases from the paper.
2. Expand the source phrases using local evidence context.
3. Generate candidates using several retrieval strategies.
4. Score candidates with slot, organism, ontology, synonym, definition, parent,
   xref, and evidence-context features.
5. Return a shortlist with provenance and instructions.
6. Require the model or builder loop to select a candidate or mark the field
   unresolved.
7. Validate the selected value through the package/domain-pack authority before
   persistence.

Candidate generation may include:

- exact label/synonym/ID lookup;
- normalized lexical search over ontology labels and synonyms;
- trigram/fuzzy search over labels and synonyms using the existing curation DB
  `pg_trgm` indexes;
- targeted definition search when labels/synonyms fail, especially for assay and
  anatomy terms;
- abbreviation and variant expansion;
- field-scoped vector search over labels, synonyms, definitions, parent labels,
  and xrefs;
- ontology graph context, such as parents, children, taxon constraints, and
  related terms;
- organism/provider filtering, especially `WB` / `NCBITaxon:6239` for
  C. elegans.

But final selection must remain:

- field-path scoped;
- organism/provider scoped;
- provenance-bearing;
- validated by the authoritative resolver/validator;
- allowed to return `unresolved` rather than guessing.

## Example: Mechanosensory Neurons

The phrase `mechanosensory neurons` is a good example of why lexical matching is
too weak. The evidence also mentions ALM and PLM neurons and cellular locations
such as axon and soma. The resolver should not search only the literal phrase.
For a WB/C. elegans context, it should consider source-phrase expansion such as:

- `mechanosensory neuron`
- `ALM neuron`
- `PLM neuron`
- `ALML`, `ALMR`, `PLML`, `PLMR`
- `touch receptor neuron`
- `axon`
- `soma`

Those candidates may belong to different slots:

- anatomy/cell terms for the expressed-in site;
- GO cellular-component terms for true subcellular locations;
- evidence-context notes when the paper states a cell class but the exact
  curator-preferred term remains ambiguous.

The helper should return that distinction explicitly. It should not ask the model
to infer from a flat list.

## Example: Assay/Method

The phrase `CRISPR engineered GFP proteins` is also not likely to map cleanly by
label. A useful helper may need to separate:

- reporter/localization evidence;
- endogenous tagging or CRISPR knock-in context;
- fluorescent protein observation;
- whether the domain-pack expected field is a formal assay term, a method term,
  or a narrower Alliance-controlled vocabulary value.

The model should receive a small field-specific shortlist and clear instructions,
for example:

```text
Field: expression_experiment.expression_assay_used
Source phrase: "PAT-3::GFP, UNC-112::GFP and GFP::TLN-1 CRISPR"
Status: ambiguous
Candidate options:
- option A ...
- option B ...
Instruction: choose one only if the paper evidence supports the assay value;
otherwise set the field unresolved and attach this ambiguity to the object.
```

## Builder Loop To Consider

The current flow asks the model to synthesize an entire final envelope. A more
robust design may use builder tools:

1. `create_domain_object`
   - The model declares a candidate object type and attaches initial evidence.
   - The runtime creates a draft object with a stable pending ref.
2. Runtime returns a field checklist.
   - Required fields.
   - Missing fields.
   - Which helper to call for each field.
   - Allowed unresolved states.
3. `resolve_domain_field_term`
   - Field-specific candidate generation and instructions.
   - Returns candidates, slot hints, provenance, and ambiguity status.
4. `set_domain_object_field`
   - The model sets one field at a time from accepted candidates or marks it
     unresolved with evidence-backed rationale.
5. `validate_domain_object`
   - The runtime returns actionable next steps rather than only a generic error.
6. `finalize_domain_object`
   - Only succeeds if the domain-pack contract passes or the object is in an
     explicitly allowed unresolved state.

This would let the runtime say:

```text
You created GeneExpressionAnnotation pending_ref_id=expr-1.
Before this object can be finalized:
- set expression_annotation_subject using a gene resolver;
- set expression_experiment.expression_assay_used using the assay resolver;
- set expression_pattern.where_expressed using the anatomy/site resolver;
- set when_expressed_stage_name or add an unresolved-stage finding;
- preserve helper selections in metadata.provenance.helper_selections[].
```

The model would still reason over evidence, but the runtime would own the object
shape, field checklist, resolver instructions, provenance requirements, and
final validation gate.

## Open Questions For Tomorrow

- Should the gene-expression extractor emit one object per gene expression
  assertion instead of combining PAT-3, UNC-112, and TLN-1 into one statement?
- Should missing assay/stage/anatomy become hard extraction failures or explicit
  unresolved field-level findings with required metadata?
- How should helper-call arguments and outputs be retained so we can audit
  whether the model attempted relation, assay, stage, and anatomy resolution?
- Should extraction use builder tools for Gene Expression instead of asking the
  model to synthesize the final domain envelope directly?
- What counts as an allowed unresolved state for assay, stage, anatomy, and
  cellular component?
- Should we build a local ontology candidate index, and if so, which fields from
  ontology terms should it index?
- Should `agr_curation_api_client` grow a first-class scored ontology candidate
  search, or should this repo issue its own SQL through the curation DB resolver
  for resolver-only experiments?
- Should we open a small upstream client PR to expose match metadata and
  ancestors, even before changing ranking?
- How should candidate rankers balance lexical/synonym hits, embedding
  similarity, ontology graph context, organism/provider constraints, and paper
  evidence context?
- How do we measure resolver quality? We likely need a small gold set of
  evidence phrases mapped to accepted terms or curated unresolved states.

## References Consulted

- [BioPortal Help / Annotator](https://www.bioontology.org/wiki/BioPortal_Help)
  for the common exact name/synonym/ID matching baseline.
- [Uberon, an integrative multi-species anatomy ontology](https://pmc.ncbi.nlm.nih.gov/articles/PMC3334586/)
  for the warning that lexical anatomy matches are suggestions requiring
  curation/reasoning, not final truth.
- [PPR-SSM biomedical ontology entity linking](https://pmc.ncbi.nlm.nih.gov/articles/PMC6819326/)
  for graph/semantic-coherence approaches to ontology candidate disambiguation.
- [scispaCy paper](https://aclanthology.org/W19-5034.pdf)
  for approximate nearest-neighbor entity linking over biomedical concepts and
  aliases.
- [OLS4 repository](https://github.com/EBISPOT/ols4)
  for ontology search/graph-query infrastructure patterns.
- [WormBase resource paper](https://pmc.ncbi.nlm.nih.gov/articles/PMC6424801/)
  for C. elegans anatomy and life-stage ontology context.
