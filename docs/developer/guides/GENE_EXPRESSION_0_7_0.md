# Gene Expression 0.7.0 Domain Pack

This guide documents the shipped Gene Expression extraction-to-workspace-to-export
flow for the 0.7.0 domain-envelope release.

## Contract

The Gene Expression contract is package-owned by the Alliance domain pack:

- Metadata: `packages/alliance/domain_packs/gene_expression/domain_pack.yaml`
- Package helpers: `packages/alliance/python/src/agr_ai_curation_alliance/domain_packs/gene_expression/`
- Curator fixtures: `packages/alliance/domain_packs/gene_expression/fixtures/`
- Extractor-output fixture: `backend/tests/fixtures/domain_packs/gene_expression/tmem67_gene_expression_output.yaml`
- Contract tests: `backend/tests/contract/alliance/domain_packs/test_gene_expression_domain_pack.py`
- Export tests: `backend/tests/unit/lib/curation_workspace/test_gene_expression_export_submission.py`

The LinkML source for this release is pinned to
`agr_curation_schema@1b11d0888f19eba4ca72022200bb7d96b30d4a52`,
`model/schema/expression.yaml`. Runtime code must not browse GitHub or pull a
new schema dynamically. Future schema changes are intentional release work:
update the domain-pack `schema_refs`, model and field provider refs, generated
fixtures, export expectations, docs, and contract tests together.

## Flow

The Gene Expression extractor produces a `DomainEnvelopeExtractionResult` with
one `GeneExpressionAnnotation` curatable object per retained expression
statement. The converter then builds a pending `DomainEnvelope`; the envelope is
the semantic source of truth for workspace rows, validation findings, export
payloads, and submission handoff.

The release fixtures cover:

- `tmem67` extraction-style evidence with metanephros anatomy, stage, figure-like
  evidence records, rescue-language exclusion, ambiguity metadata, reagent text,
  and DB-shaped annotation/reference/experiment projection.
- `tmem67` multi-annotation handling where one paper yields multiple review rows
  for one gene and reference.
- `flcn` ZFIN curator guidance with multiple embryonic sites/stages, anatomy-only
  site routing, GO cellular-component-only routing, mixed anatomy plus cellular
  component routing, and negated expression.

Required LinkML selectors such as relation, data provider, subject gene,
reference, assay, stage label, and expression site must be present or produce
field-addressed diagnostics. Optional LinkML context such as detection reagents,
specimen genomic model, specimen alleles, and condition relations is
non-export-blocking in 0.7.0, but non-export-blocking does not mean optional to
preserve. If extraction identifies that context, it must remain in the payload
when a safe placeholder is available or remain field-addressed in metadata with
an explicit unresolved reason.

## Validation

Validation is split deliberately:

- Extractors preserve paper-backed labels, helper selections, placeholders,
  evidence IDs, exclusions, and ambiguity metadata.
- Active validator bindings resolve or reject authoritative controlled fields
  such as relation, data provider, subject gene, source reference, assay, stage,
  anatomy, UBERON slim terms, and GO cellular-component terms.
- Materializers copy validator-owned resolved values into envelope fields and
  record unresolved findings when a field cannot be resolved.
- The export adapter blocks missing required export fields and emits audit-only
  warnings for optional experiment context whose 0.7.0 curation DB write mapping
  is not approved yet.

Controlled vocabulary and ontology values used by extractors must come from
field-scoped helper tools such as `get_domain_field_term_options`, or remain
unresolved. The release fixtures record helper provenance in
`metadata.extraction_metadata.provenance.helper_selections[]`; they do not treat
prompt-memory constants as authoritative IDs.

PMID, DOI, title, citation, and source-document text are lookup inputs and
evidence/provenance context. Durable reference output is the resolved
`single_reference.reference_id`, `single_reference.curie`, and supported
title/citation fields from `reference_validation` or
`agr_literature_reference_lookup`. The annotation reference and expression
experiment reference must project to the same resolved reference.

## Export

`GeneExpressionExportAdapter` maps ready domain-envelope candidates to a
read-only, curation DB-shaped JSON handoff. It includes:

- LinkML commit and source file metadata.
- Source envelope object and payload.
- Target rows for `geneexpressionannotation`, `geneexpressionexperiment`,
  `expressionpattern`, `temporalcontext`, and `anatomicalsite`.
- Reference, relation, subject gene, assay, stage, anatomy, and GO lookup
  projections.
- Field-addressed blockers for missing required fields.
- Audit-only warnings for reagent, specimen, allele, and condition context whose
  export joins remain under development.

Live DB writes remain out of scope for 0.7.0.

## Non-Alliance Packs

The runtime model is project-agnostic. A different organization can ship a
domain pack outside `agr.alliance` by putting a package under
`~/.agr_ai_curation/runtime/packages/<package-id>/` with:

- `package.yaml` declaring agent, tool, and curation-adapter exports.
- `agents/<agent>/agent.yaml`, `prompt.yaml`, optional `schema.py`, and
  organization group rules.
- `tools/bindings.yaml` for package-owned tool callables.
- `domain_packs/<pack>/domain_pack.yaml` with schema refs, object definitions,
  fields, validators, workspace display groups, and fixture refs.
- `domain_packs/<pack>/fixtures/*.yaml` with representative envelopes.
- A curation adapter export defining `register_curation_adapters(registry)` that
  registers a candidate normalizer, domain pack, optional deterministic
  validator, `DomainPackMetadataReviewRowMaterializer`, export adapter, and any
  submission transport.

The minimal tested example lives at
`backend/tests/unit/lib/packages/fixtures/org_custom_runtime/`. The walkthrough
test `test_org_custom_domain_pack_walkthrough_registers_runtime_surfaces` proves
that this non-Alliance package loads without the Alliance package and registers
its agent, tool, domain pack, validator hook, workspace layout/materializer, and
export adapter.

## Known Limitations

- Direct live curation DB writes are not enabled; export is a read-only handoff.
- Reagent, specimen, allele, and condition context is preserved but not fully
  materialized into approved DB joins in 0.7.0.
- Some optional normalization surfaces remain under development and must be
  represented as unresolved context rather than silently defaulted.
- Schema updates are release-pinned. A new LinkML commit requires coordinated
  metadata, fixture, export, docs, and test updates.
