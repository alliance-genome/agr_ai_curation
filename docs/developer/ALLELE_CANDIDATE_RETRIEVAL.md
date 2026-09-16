# Bounded allele candidate retrieval

`agr-curation-api-client==0.15.0` owns the rich database search and detail queries.
The Alliance tool uses the same retrieval path for single and bulk allele searches,
and the rich detail API for `get_allele_by_id`. Existing exact-symbol lookup remains
available. A search candidate is not a confirmed paper identity, even when unique.

Keep `allele_symbol` literal. Pass an explicit paper-supported gene separately as
`gene_id` or `gene_symbol`, supplier/source text as `allele_attribution`, and a
structured impact such as `conditional_ready` as `allele_functional_impact`.
Gene relationships and taxon constrain discovery; attribution and impact only rank
the discovered set. No source-specific identifier is special-cased. Missing facts
remain unknown. If literal discovery returns nothing without a gene scope, the
existing bounded trigram search remains available; failures are not empty results.

The default discovery budget is 200, while the default display is 20. Both can be
tuned separately (see `AGR_ALLELE_*` settings in `.env.example`). Candidates carry
names, synonyms, verified gene associations, functional impacts, mutation types,
ranking reasons and annotation-cap flags. Rich facts appear once in tool `data`;
canonical tracking projections retain only compact identity/match metadata.

`coverage` describes discovered and displayed counts, their respective caps, and
missing detail rows. A capped count is never an exact database total. Bulk response
caps update each affected input's coverage. Validators are instructed to copy the
provider coverage into their saved lookup attempts and explain meaningful limits;
materialization preserves this metadata for review. This records the validator's
reported evidence, not a guarantee that an LLM inspected every candidate. No new
model calls or compulsory full pagination are introduced.

Regression coverage includes literal FlyBase notation, single/bulk parity, explicit
scope and separate clues, candidate uncertainty, compact projections, source outage
versus no-match, bulk limits, and durable coverage. Package SQL tests plus separate
read-only live database checks cover discovery beyond the old display cutoff.
